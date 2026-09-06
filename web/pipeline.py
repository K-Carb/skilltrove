"""可视化流水线运行器：把六步 CLI 变成 Web 可交互任务。

设计（对照调研结论，零第三方依赖）：
- 每步 = 真实 CLI 子进程（`sys.executable cli/main.py <step> ...`，与用户手敲一致），
  子进程输出逐行读入环形缓冲（deque），避免 redirect_stdout 的线程安全问题
- 单 worker 串行执行（ThreadPoolExecutor(max_workers=1)），本地单用户天然串行
- 取消 = threading.Event + Popen.terminate()
- 轮询 API：run 状态 + 游标式日志增量拉取（docker logs --tail 语义）
"""

from __future__ import annotations

import collections
import json
import os
import shlex
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAX_LOG_LINES = 500

# 可被 UI 选择的数据源目录（分号分隔；默认实验数据 + log-export 导出包）
DEFAULT_SOURCES_DIRS = [os.path.join(ROOT, "examples", "sources")]
SOURCES_DIRS = [p for p in os.environ.get("SKILLTROVE_SOURCES_DIR", "").split(";") if p] or DEFAULT_SOURCES_DIRS

ADAPTER_CHOICES = ("auto", "log-export", "task-dirs", "table", "session-logs", "git-repo", "docs", "git-records", "github")


def _job_timeout() -> int:
    """任务级超时（秒）：默认 1800（30 分钟），SKILLTROVE_PIPELINE_TIMEOUT 可调。"""
    v = os.environ.get("SKILLTROVE_PIPELINE_TIMEOUT", "1800")
    try:
        return max(int(v), 10)
    except ValueError:
        return 1800


class Job:
    """一次流水线运行：步骤序列 + 状态 + 日志缓冲。"""

    def __init__(self, run_id: str, steps: list[str], params: dict):
        self.id = run_id
        self.steps = list(steps)
        self.params = dict(params)
        self.status = "queued"          # queued / running / success / failed / cancelled
        self.current = -1
        self.step_states = [
            {"name": s, "status": "pending", "started": None, "finished": None,
             "duration": None, "exit_code": None}
            for s in self.steps
        ]
        self.started = None
        self.finished = None
        self.duration = None
        self.error = None
        self.log = collections.deque(maxlen=MAX_LOG_LINES)
        self._cancel = threading.Event()
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None

    def emit(self, line: str) -> None:
        with self._lock:
            self.log.append(line)

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "id": self.id, "steps": self.steps, "params": self.params,
                "status": self.status, "current": self.current,
                "step_states": list(self.step_states),
                "started": self.started, "finished": self.finished,
                "duration": self.duration, "error": self.error,
            }


def build_command(step: str, params: dict) -> list[str]:
    """步骤 → 真实 CLI 命令（与用户手敲等价；子进程隔离输出）。"""
    py = [sys.executable, os.path.join("cli", "main.py")]
    if step == "export":
        cmd = py + ["export", "--source", params["source"], "--out", "archive/"]
        adapter = params.get("adapter") or "auto"
        if adapter != "auto":
            cmd += ["--adapter", adapter]
        if params.get("exclude_agents"):
            cmd += ["--exclude-agents", params["exclude_agents"]]
        return cmd
    if step == "score":
        return py + ["score", "--episodes", "archive/episodes.jsonl",
                     "--manifest", "archive/manifest.json", "--out", "archive/scored.jsonl"]
    if step == "cluster":
        cmd = py + ["cluster", "--episodes", "archive/scored.jsonl",
                    "--out", "data/candidates.json"]
        if params.get("threshold") not in (None, ""):
            cmd += ["--threshold", str(params["threshold"])]
        if params.get("llm_backend"):
            cmd += ["--llm-backend", params["llm_backend"]]
        return cmd
    if step == "draft":
        cmd = py + ["draft", "--candidates", "data/candidates.json"]
        if params.get("candidate"):
            cmd += ["--candidate", params["candidate"]]
        if params.get("llm_backend"):
            cmd += ["--llm-backend", params["llm_backend"]]
        return cmd
    if step == "publish":
        return py + ["publish", "--skill", params["skill"],
                     "--status", params.get("status", "published")]
    if step == "recall":
        cmd = py + ["recall", "--skill", params["skill"]]
        if params.get("llm_backend"):
            cmd += ["--llm-backend", params["llm_backend"]]
        if params.get("agent"):
            cmd += ["--agent", params["agent"]]
        return cmd
    raise ValueError(f"未知步骤: {step}")


_JOBS: dict[str, Job] = {}
_JOBS_LOCK = threading.Lock()
_RUNNER = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pipeline")


def start_run(steps: list[str], params: dict) -> str:
    """提交一次运行（串行队列），返回 run_id。"""
    run_id = uuid.uuid4().hex[:8]
    job = Job(run_id, steps, params)
    with _JOBS_LOCK:
        _JOBS[run_id] = job
    _RUNNER.submit(_execute, job)
    return run_id


def _execute(job: Job) -> None:
    job.started = time.time()
    job.status = "running"
    job.emit(f"[pipeline] 开始运行：{' → '.join(job.steps)}")
    for idx, step in enumerate(job.steps):
        if job._cancel.is_set():
            job.status = "cancelled"
            job.emit("[pipeline] 已取消")
            break
        st = job.step_states[idx]
        st["status"] = "running"
        st["started"] = time.time()
        job.current = idx
        job.emit(f"\n=== 步骤 {idx + 1}/{len(job.steps)}: {step} ===")
        try:
            cmd = build_command(step, job.params)
        except ValueError as e:
            st["status"] = "failed"
            job.status = "failed"
            job.error = str(e)
            job.finished = time.time()
            job.duration = round(job.finished - job.started, 1)
            job.emit(f"[pipeline] {e}")
            return
        job.emit("$ " + " ".join(shlex.quote(str(c)) for c in cmd))
        try:
            proc = subprocess.Popen(
                cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, encoding="utf-8", errors="replace")
        except OSError as e:
            st["status"] = "failed"
            job.status = "failed"
            job.error = f"无法启动进程: {e}"
            job.finished = time.time()
            job.duration = round(job.finished - job.started, 1)
            job.emit(f"[pipeline] {job.error}")
            return
        job._proc = proc
        # 任务级超时：LLM 步骤可能很慢（cluster/draft/recall 数十次调用），但必须
        # 有上限，防止进程挂死。默认 30 分钟，SKILLTROVE_PIPELINE_TIMEOUT 可调。
        step_timeout = _job_timeout()
        step_deadline = time.time() + step_timeout
        for line in proc.stdout:  # type: ignore[union-attr]
            job.emit(line.rstrip("\n"))
            if time.time() > step_deadline:
                proc.terminate()
                break
        proc.wait()
        job._proc = None
        st["finished"] = time.time()
        st["duration"] = round(st["finished"] - (st["started"] or st["finished"]), 1)
        st["exit_code"] = proc.returncode
        if time.time() > step_deadline:
            st["status"] = "failed"
            job.status = "failed"
            job.error = f"步骤 {step} 超时（>{step_timeout}s），已终止"
            job.finished = time.time()
            job.duration = round(job.finished - job.started, 1)
            job.emit(f"[pipeline] {job.error}")
            return
        if proc.returncode != 0:
            st["status"] = "failed"
            job.status = "failed"
            job.error = f"步骤 {step} 失败（exit {proc.returncode}）"
            job.finished = time.time()
            job.duration = round(job.finished - job.started, 1)
            job.emit(f"[pipeline] {job.error}，中止")
            return
        st["status"] = "success"
        job.emit(f"[pipeline] 步骤 {step} 完成（{st['duration']}s）")
    if job.status == "running":
        job.status = "success"
    job.finished = time.time()
    job.duration = round(job.finished - job.started, 1)
    job.emit(f"[pipeline] 运行结束：{job.status}，耗时 {job.duration}s")


def cancel_run(run_id: str) -> bool:
    with _JOBS_LOCK:
        job = _JOBS.get(run_id)
    if not job:
        return False
    job._cancel.set()
    proc = job._proc
    if proc is not None:
        try:
            proc.terminate()
        except Exception:
            pass
    return True


def get_job(run_id: str) -> dict | None:
    with _JOBS_LOCK:
        job = _JOBS.get(run_id)
    return job.snapshot() if job else None


def list_jobs() -> list[dict]:
    with _JOBS_LOCK:
        jobs = list(_JOBS.values())
    jobs.sort(key=lambda j: j.started or 0, reverse=True)
    return [j.snapshot() for j in jobs]


def logs_since(run_id: str, after: int = 0) -> dict | None:
    """游标式日志拉取：{lines, next, done}（等价 docker logs --tail 语义）。

    next 钳制到日志总长（越界游标返回空增量且不增长）。
    """
    with _JOBS_LOCK:
        job = _JOBS.get(run_id)
    if not job:
        return None
    with job._lock:
        lines = list(job.log)
    total = len(lines)
    start = min(max(after, 0), total)
    done = job.status in ("success", "failed", "cancelled")
    return {"lines": lines[start:], "next": total, "done": done}


def _source_count(path: str, adapter: str | None) -> int | None:
    """轻量记录数统计（仅文件型数据源；目录源如 git 仓库不统计）。

    只做尽力而为：解析失败或结构未知返回 None，前端降级显示文件大小。
    """
    if not adapter or not os.path.isfile(path):
        return None
    try:
        if path.endswith(".jsonl"):
            with open(path, encoding="utf-8") as f:
                return sum(1 for line in f if line.strip())
        if path.endswith(".json"):
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return len(data)
            if isinstance(data, dict):
                for key in ("rows", "records", "episodes", "issues", "items", "data"):
                    if isinstance(data.get(key), list):
                        return len(data[key])
    except (OSError, ValueError):
        return None
    return None


def detect_sources() -> list[dict]:
    """扫描数据源目录，对每个候选做适配器自动识别（供 UI 选择）。"""
    from cli import adapters  # noqa: E402
    found = []
    for base in SOURCES_DIRS:
        if not os.path.isdir(base):
            continue
        for name in sorted(os.listdir(base)):
            path = os.path.join(base, name)
            if not (os.path.isfile(path) or os.path.isdir(path)):
                continue
            try:
                adapter = adapters.pick_adapter(path).shape
            except ValueError:
                adapter = None
            count = _source_count(path, adapter)
            size_kb = round(os.path.getsize(path) / 1024) if os.path.isfile(path) else None
            found.append({"path": path, "name": name, "adapter": adapter,
                          "kind": "file" if os.path.isfile(path) else "dir",
                          "count": count, "size_kb": size_kb})
    return found
