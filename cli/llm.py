"""可插拔 LLM 后端：本机 CLI agent 无头调用（claude / codex / kimi），失败降级。

Spec §5.1：
- 默认后端 kimi（`kimi -p` 无头模式）
- 环境变量 LLM_BACKEND 切换
- 每次调用落 data/llm-log.jsonl（可审计）
- 后端不可用时返回 ok=False，由调用方决定降级路径
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import urllib.request
import urllib.error
import uuid

_LOG_PATH = os.environ.get("SKILLTROVE_LLM_LOG", os.path.join("data", "llm-log.jsonl"))


def _find(cmd: str) -> str | None:
    return shutil.which(cmd)


def _log(entry: dict) -> None:
    try:
        os.makedirs(os.path.dirname(_LOG_PATH), exist_ok=True)
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass  # 日志失败不阻塞调用


def _call_claude(prompt: str, system: str, timeout: int) -> str:
    """claude CLI：prompt 走 stdin（Windows 下 claude.cmd shim 会截断长 argv 参数）。"""
    cli = _find("claude")
    if not cli:
        raise RuntimeError("claude CLI 不在 PATH")
    cmd = [cli, "-p", "--output-format", "text"]
    # LLM_MODEL 选择模型（本机 claude 的别名/模型名，如 haiku/sonnet/opus）
    model = os.environ.get("LLM_MODEL", "")
    if model:
        cmd += ["--model", model]
    if system:
        cmd += ["--system-prompt", system]
    r = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                       timeout=timeout, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"claude 退出码 {r.returncode}: {r.stderr[:300]}")
    return r.stdout.strip()


def _call_codex(prompt: str, system: str, timeout: int) -> str:
    """codex CLI：exec "-" 从 stdin 读 prompt（规避 argv 截断）。

    codex 0.147 无 --system-prompt 参数（实测报 unexpected argument），
    system 文本并入 prompt 头部（功能等价，仅丢失 system 角色标记）。
    """
    cli = _find("codex")
    if not cli:
        raise RuntimeError("codex CLI 不在 PATH")
    cmd = [cli, "exec", "-", "--json"]
    payload = (system + "\n\n" + prompt) if system else prompt
    r = subprocess.run(cmd, input=payload, capture_output=True, text=True,
                       timeout=timeout, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"codex 退出码 {r.returncode}: {r.stderr[:300]}")
    return _extract_codex_text(r.stdout)


def _call_kimi(prompt: str, system: str, timeout: int) -> str:
    """kimi CLI：-p 以参数传 prompt（非 stdin，实测 'argument missing'）；无 system 参数。"""
    cli = _find("kimi")
    if not cli:
        raise RuntimeError("kimi CLI 不在 PATH")
    payload = (system + "\n\n" + prompt) if system else prompt
    cmd = [cli, "-p", payload]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       timeout=timeout, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"kimi 退出码 {r.returncode}: {r.stderr[:300]}")
    return r.stdout.strip()


def _call_openai_compatible(prompt: str, system: str, timeout: int) -> str:
    """BYO 网关/key（客户场景最通用）；标准库 urllib，无第三方依赖。"""
    base_url = os.environ.get("LLM_BASE_URL", "http://localhost:11434/v1").rstrip("/")
    api_key = os.environ.get("LLM_API_KEY", "")
    model = os.environ.get("LLM_MODEL", "")
    if not model:
        raise RuntimeError("openai-compatible 后端需设 LLM_MODEL（如 qwen2.5:7b）")
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system or "You are a helpful assistant."},
            {"role": "user", "content": prompt},
        ],
    }
    req = urllib.request.Request(
        base_url + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 **({"Authorization": f"Bearer {api_key}"} if api_key else {})},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"openai-compatible HTTP {e.code}: {e.read()[:200]}")
    return (body.get("choices") or [{}])[0].get("message", {}).get("content", "").strip()


_BACKENDS = {
    "claude": _call_claude,
    "codex": _call_codex,
    "kimi": _call_kimi,
    "openai-compatible": _call_openai_compatible,
}


def call(prompt: str, system: str = "", backend: str | None = None,
         timeout: int = 600) -> dict:
    """调用 LLM，返回 {"ok": bool, "text": str, "backend": str, "error": str|None}。"""
    backend = backend or os.environ.get("LLM_BACKEND", "kimi")
    call_id = uuid.uuid4().hex[:8]
    t0 = time.time()
    entry = {
        "call_id": call_id, "backend": backend, "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "prompt_preview": prompt[:300], "ok": False, "error": None,
        "text_preview": None, "duration_s": None,
    }
    try:
        fn = _BACKENDS.get(backend)
        if fn is None:
            raise RuntimeError(f"未知后端: {backend}")
        text = fn(prompt, system, timeout)
        if not text:
            raise RuntimeError("LLM 返回空输出")
        entry["ok"] = True
        entry["text"] = text
        entry["text_preview"] = text[:300]
        return {"ok": True, "text": text, "backend": backend, "error": None}
    except Exception as e:
        entry["error"] = str(e)
        return {"ok": False, "text": "", "backend": backend, "error": str(e)}
    finally:
        entry["duration_s"] = round(time.time() - t0, 2)
        _log(entry)


def _extract_codex_text(stdout: str) -> str:
    """codex exec --json 输出是 JSONL 事件流，提取最终 assistant 文本。

    事件类型：thread.started / turn.started / response_item（含
    payload.type=message|reasoning|...，content[].text） / turn.completed 等。
    兼容旧版单 JSON 对象输出（{"result": ...}）。
    """
    stdout = stdout.strip()
    if not stdout:
        return ""
    texts: list[str] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get("type") == "response_item":
            payload = obj.get("payload") or {}
            if payload.get("type") in ("message", "reasoning"):
                for block in payload.get("content") or []:
                    if isinstance(block, dict) and block.get("type") == "output_text":
                        texts.append(block.get("text", ""))
            elif payload.get("type") == "function_call":
                continue  # 工具调用非最终文本
        elif isinstance(obj, dict) and "result" in obj:  # 旧版单对象
            return str(obj.get("result") or obj.get("output") or "")
    out = "\n".join(t for t in texts if t).strip()
    if out:
        return out
    return stdout  # 兜底：解析不到就原样返回


def probe() -> dict:
    """探测各后端 CLI 可用性（不实际调用）。"""
    result = {}
    for name in ("claude", "codex", "kimi"):
        path = _find(name)
        result[name] = bool(path)
    return result


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "probe":
        print(json.dumps(probe(), ensure_ascii=False))
    else:
        r = call("请只回复两个字：正常")
        print(json.dumps({k: r[k] for k in ("ok", "backend", "error")}, ensure_ascii=False))
        print("text:", r.get("text", "")[:100])
