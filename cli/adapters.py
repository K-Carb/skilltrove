"""通用数据源适配器（Deliverable Plan D2）：多来源 → 标准 episode + facts。

把"发现经验"的输入从 log-export 导出包扩展到真实世界的常见数据形态。核心思想：
接口统一（detect / discover / to_episode），切块与字段映射按形态分派，
字段可用度分层并在 facts.depth 显式标注，下游 score/cluster 零改接口。

支持形态（`--adapter auto` 自动判定，判定顺序见 pick_adapter）：
  log-export  log-export 导出包（MANIFEST.json + issue 目录）——原 export.py 逻辑迁移
  task-dirs       任务文件夹（子目录含 description.md/task.md/README.md 等，可选 meta.json）
  table           CSV / JSON 数组 / JSONL（每行一任务，列名别名映射；gh issue 导出即此形态）
  session-logs    agent 会话日志目录（Codex / Claude Code / 通用 jsonl，一会话一任务）
  git-repo        Git 仓库（git log，一次 commit = 一个任务）
  docs            文档目录（.md/.txt）——仅经验池，无归因/完成态，显式降级不参与发现

统一出口：run_export 写 out/manifest.json + out/facts.json + out/episodes.jsonl。
manifest.json 为归一化任务清单（score 的 join 查证用，多来源统一）。
"""

from __future__ import annotations

import csv
import json
import os
import re
import subprocess
import time

# ---------------------------------------------------------------------------
# 共享工具（从 export.py 迁移，供 log-export / task-dirs 复用）
# ---------------------------------------------------------------------------

COMMENT_HEADER_RE = re.compile(r"^##\s+(\S+)\s*\|\s*(\S+)\s*$")


def parse_comments_md(md_text: str) -> list[dict]:
    """解析 comments.md：按 '## <ts> | <agent>' 切段（正文内 ## 小节标题天然免疫）。"""
    lines = md_text.splitlines()
    comments: list[dict] = []
    cur: dict | None = None
    buf: list[str] = []

    def flush() -> None:
        nonlocal cur, buf
        if cur is not None:
            cur["text"] = "\n".join(buf).strip()
            comments.append(cur)
        cur, buf = None, []

    for line in lines:
        m = COMMENT_HEADER_RE.match(line.strip())
        if m:
            flush()
            cur = {"ts": m.group(1), "agent": m.group(2), "text": ""}
        elif cur is not None:
            buf.append(line)
    flush()
    return comments


def parse_description_md(md_text: str) -> str:
    """描述文件去 # 标题行后的正文（作为 goal/acceptance 语料）。"""
    body = "\n".join(l for l in md_text.strip().splitlines() if not l.strip().startswith("#"))
    return body.strip()


def markdown_title(text: str) -> str:
    """取 markdown 首个 # 标题（无则空串）。"""
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("#"):
            return s.lstrip("#").strip()
    return ""


def status_normalize(status: str) -> str:
    """把真实世界的状态值归一到内部词汇（score 的完成态只认 done/in_review）。

    完成类 → done；其余保小写原值（open/in_progress/cancelled/backlog/...）。
    """
    s = (status or "").strip().lower()
    if s in {"done", "in_review", "merged", "closed", "completed", "resolved",
             "完成", "已完成", "已关闭", "已解决", "已合并"}:
        return "done"
    return s


def build_join_key(table: str, task_id: str, measured_at) -> dict:
    return {"table": table, "id": task_id, "measured_at": measured_at}


def normalize_episode(ep: dict, shape: str) -> dict:
    """补齐标准 episode schema 的默认字段（下游 score/cluster 依赖）。"""
    key = ep.get("issue_key") or ep.get("episode_id") or "unknown"
    ep.setdefault("episode_id", f"ep-{key}")
    ep.setdefault("issue_key", key)
    ep.setdefault("issue_id", key)
    ep.setdefault("title", "")
    ep.setdefault("status", None)
    ep.setdefault("goal", ep.get("title") or "")
    ep.setdefault("acceptance", "")
    ep.setdefault("agent_ids", [])
    ep.setdefault("main_agent", (ep.get("agent_ids") or [None])[0])
    ep.setdefault("turn_count", len(ep.get("comments") or []))
    ep.setdefault("comments", [])
    ep.setdefault("attachments", [])
    ep.setdefault("output_signal", False)
    ep.setdefault("score", None)
    ep.setdefault("excluded", False)
    if not ep.get("business_join_key"):
        ep["business_join_key"] = build_join_key(f"adapter:{shape}", key, None)
    return ep


def _first(aliases: tuple[str, ...], row: dict):
    """按别名列表取行字段（忽略大小写/下划线差异），返回 (原始值, 规范名)。

    null/空值视为未命中（跳过继续找下一个别名）——否则 gh 的 `assignee`(null)
    会遮蔽 `assignees`(列表)、`createdAt` 等键名差异也会误命中空值。
    """
    norm = {str(k).strip().lower().replace("_", "").replace(" ", ""): (k, v)
            for k, v in row.items()}
    for a in aliases:
        key = a.lower().replace("_", "").replace(" ", "")
        if key in norm:
            k, v = norm[key]
            if v is None or v == "":
                continue
            return v, k
    return None, None


def _as_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return [value]
    return [str(value)]


# ---------------------------------------------------------------------------
# SourceAdapter 协议
# ---------------------------------------------------------------------------

class SourceAdapter:
    """输入源适配器：detect（认识吗）→ discover（切块）→ to_episode（翻译）。

    facts 的 provider/channels/depth 由实现类提供，run_export 统一组装。
    """

    provider: str = "generic"
    channels: list[str] = []
    shape: str = "generic"

    def detect(self, source: str) -> bool:
        raise NotImplementedError

    def discover(self, source: str) -> list[dict]:
        """返回 [TaskRef]：{id, loc, raw}（raw 为延迟读取所需的最小定位信息）。"""
        raise NotImplementedError

    def to_episode(self, source: str, task: dict) -> dict:
        raise NotImplementedError

    def depth(self, source: str) -> str:
        """facts.depth：字段可用度描述（如 'table:key+title+status+assignee'）。"""
        return self.shape


# ---------------------------------------------------------------------------
# 1) log-export：原 export.py 逻辑（MANIFEST.json + issue 目录）
# ---------------------------------------------------------------------------

class LogExportAdapter(SourceAdapter):
    provider = "log-export"
    channels = ["log-export"]
    shape = "log-export"

    def detect(self, source: str) -> bool:
        manifest_path = os.path.join(source, "MANIFEST.json")
        if not os.path.isfile(manifest_path):
            return False
        try:
            with open(manifest_path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return False
        if isinstance(data, dict):
            data = data.get("issues", data)
        return isinstance(data, list)

    def discover(self, source: str) -> list[dict]:
        with open(os.path.join(source, "MANIFEST.json"), encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            data = data.get("issues", data)
        tasks = []
        for issue in data:
            key = issue.get("issue", "")
            if not key:
                continue
            tasks.append({"id": key, "loc": f"MANIFEST.json:{key}", "raw": issue})
        return tasks

    def to_episode(self, source: str, task: dict) -> dict:
        issue = task["raw"]
        key = issue.get("issue", "")
        issue_dir = _find_issue_dir(source, key)
        ep = {
            "episode_id": f"ep-{key}",
            "issue_key": key,
            "issue_id": key,
            "title": issue.get("title", ""),
            "status": issue.get("status", ""),
            "goal": issue.get("title", ""),
            "acceptance": "",
            "agent_ids": [],
            "main_agent": None,
            "turn_count": 0,
            "comments": [],
            "attachments": [],
            "business_join_key": None,
            "score": None,
            "excluded": False,
            "shape": self.shape,
        }
        if issue_dir is None:
            return normalize_episode(ep, self.shape)
        desc_path = os.path.join(issue_dir, "description.md")
        if os.path.isfile(desc_path):
            with open(desc_path, encoding="utf-8") as f:
                ep["acceptance"] = parse_description_md(f.read())
        comments_path = os.path.join(issue_dir, "comments.md")
        if os.path.isfile(comments_path):
            with open(comments_path, encoding="utf-8") as f:
                ep["comments"] = parse_comments_md(f.read())
            ep["turn_count"] = len(ep["comments"])
            agents = [c["agent"] for c in ep["comments"] if c.get("agent")]
            ep["agent_ids"] = list(dict.fromkeys(agents))
            ep["main_agent"] = ep["agent_ids"][0] if ep["agent_ids"] else None
        att_dir = os.path.join(issue_dir, "attachments")
        if os.path.isdir(att_dir):
            ep["attachments"] = sorted(os.listdir(att_dir))
        ep["business_join_key"] = build_join_key(
            "issue_status_history", key,
            ep["comments"][-1]["ts"] if ep["comments"] else None)
        ep["shape"] = self.shape
        return normalize_episode(ep, self.shape)

    def depth(self, source: str) -> str:
        return "issue+comments+attachments"


def _find_issue_dir(src: str, issue_key: str) -> str | None:
    prefix = issue_key + "-"
    for name in os.listdir(src):
        full = os.path.join(src, name)
        if os.path.isdir(full) and name.startswith(prefix):
            return full
    return None


# ---------------------------------------------------------------------------
# 2) task-dirs：任务文件夹（通用版 log-export 形态，别名 + meta.json 逃生舱）
# ---------------------------------------------------------------------------

_DESC_NAMES = ("description.md", "task.md", "issue.md", "README.md", "readme.md")
_COMMENT_NAMES = ("comments.md", "log.md", "thread.md", "conversation.md")
_ATTACH_DIRS = ("attachments", "artifacts", "outputs", "products")
_DIR_KEY_RE = re.compile(r"^([A-Za-z]+-\d+)")


def _dir_key(dirname: str) -> str:
    """目录名 → 任务 key：优先取 '字母-数字' 前缀（WIKI-4-S2-... → WIKI-4、
    TASK-1-调研 → TASK-1）；否则首个 '-' 前段；无 '-' 取全名。"""
    m = _DIR_KEY_RE.match(dirname)
    if m:
        return m.group(1)
    return dirname.split("-", 1)[0] if "-" in dirname else dirname


class TaskDirsAdapter(SourceAdapter):
    provider = "task-dirs"
    channels = ["task-dirs"]
    shape = "task-dirs"

    def detect(self, source: str) -> bool:
        if not os.path.isdir(source):
            return False
        for name in os.listdir(source):
            full = os.path.join(source, name)
            if os.path.isdir(full):
                if any(os.path.isfile(os.path.join(full, d)) for d in _DESC_NAMES):
                    return True
                if os.path.isfile(os.path.join(full, "meta.json")):
                    return True
        return False

    def discover(self, source: str) -> list[dict]:
        # 可选显式清单：skilltrove-manifest.json（[{id/key, title, status, ...}]）
        manifest_path = os.path.join(source, "skilltrove-manifest.json")
        explicit: dict[str, dict] = {}
        if os.path.isfile(manifest_path):
            with open(manifest_path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                data = data.get("issues", data)
            for item in data:
                kid = item.get("id") or item.get("key") or item.get("issue")
                if kid:
                    explicit[str(kid)] = item
        tasks = []
        for name in sorted(os.listdir(source)):
            full = os.path.join(source, name)
            if not os.path.isdir(full):
                continue
            if not any(os.path.isfile(os.path.join(full, d)) for d in _DESC_NAMES) \
               and not os.path.isfile(os.path.join(full, "meta.json")):
                continue
            key = _dir_key(name)
            tasks.append({"id": key, "loc": full, "raw": {"dir": name, "key": key}})
        return tasks

    def to_episode(self, source: str, task: dict) -> dict:
        import json as _json
        task_dir = os.path.join(source, task["raw"]["dir"])
        key = task["raw"]["key"]
        meta: dict = {}
        meta_path = os.path.join(task_dir, "meta.json")
        if os.path.isfile(meta_path):
            with open(meta_path, encoding="utf-8") as f:
                meta = _json.load(f)
        desc = ""
        for dname in _DESC_NAMES:
            p = os.path.join(task_dir, dname)
            if os.path.isfile(p):
                with open(p, encoding="utf-8") as f:
                    desc = f.read()
                break
        title = meta.get("title") or markdown_title(desc) or task["raw"]["dir"]
        comments: list[dict] = []
        for cname in _COMMENT_NAMES:
            p = os.path.join(task_dir, cname)
            if os.path.isfile(p):
                with open(p, encoding="utf-8") as f:
                    comments = parse_comments_md(f.read())
                break
        attachments: list[str] = []
        for aname in _ATTACH_DIRS:
            p = os.path.join(task_dir, aname)
            if os.path.isdir(p):
                attachments = sorted(os.listdir(p))
                break
        agents = list(dict.fromkeys(meta.get("agents") or [c.get("agent") for c in comments if c.get("agent")]))
        status = meta.get("status")
        if status is None and os.path.isfile(os.path.join(source, "skilltrove-manifest.json")):
            # 显式清单里的 status 兜底
            with open(os.path.join(source, "skilltrove-manifest.json"), encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                data = data.get("issues", data)
            for item in data:
                if (item.get("id") or item.get("key") or item.get("issue")) == key:
                    status = item.get("status")
                    break
        ep = {
            "episode_id": f"ep-{key}",
            "issue_key": key,
            "issue_id": key,
            "title": title,
            "status": status_normalize(status or ""),
            "goal": title,
            "acceptance": parse_description_md(desc),
            "agent_ids": agents,
            "main_agent": agents[0] if agents else None,
            "turn_count": len(comments),
            "comments": comments,
            "attachments": attachments,
            "business_join_key": build_join_key(
                "issue_status_history", key,
                comments[-1]["ts"] if comments else meta.get("timestamps", {}).get("end")),
            "score": None,
            "excluded": False,
            "output_signal": bool(attachments) or bool(meta.get("output")),
            "shape": self.shape,
        }
        return normalize_episode(ep, self.shape)

    def depth(self, source: str) -> str:
        return "task-dirs: description(+comments/attachments)+meta.json(可选)"


# ---------------------------------------------------------------------------
# 3) table：CSV / JSON 数组 / JSONL（每行一任务，列名别名映射）
# ---------------------------------------------------------------------------

_KEY_ALIASES = ("key", "id", "issue", "issue_key", "identifier", "number", "work item id")
_TITLE_ALIASES = ("title", "summary", "name", "subject")
_STATUS_ALIASES = ("status", "state", "阶段", "状态")
_AGENT_ALIASES = ("assignee", "assignees", "owner", "creator", "author", "user", "reporter", "负责人")
_DESC_ALIASES = ("description", "body", "content", "desc", "描述")
_CREATED_ALIASES = ("created", "created_at", "created", "open", "opened")
_UPDATED_ALIASES = ("updated", "updated_at", "updated", "closed", "closedat")
_COMMENT_COL_ALIASES = ("comments", "comment", "评论")


def _collect_agents(row: dict) -> list:
    """合并所有"参与人"别名的值（去重保序）——gh 数据里 author(user) 与
    assignees 可能同时存在，只取第一个会漏掉作者或指派人。"""
    agents: list = []
    for a in _AGENT_ALIASES:
        v, _ = _first((a,), row)
        for x in _as_list(v):
            if isinstance(x, dict):
                x = x.get("login") or x.get("name") or ""
            x = str(x).strip()
            if x and x not in agents:
                agents.append(x)
    return agents


class TableAdapter(SourceAdapter):
    provider = "table"
    channels = ["table"]
    shape = "table"

    def _load_rows(self, source: str) -> list[dict]:
        ext = os.path.splitext(source)[1].lower()
        if ext == ".csv":
            with open(source, encoding="utf-8-sig", newline="") as f:
                return list(csv.DictReader(f))
        if ext == ".json":
            with open(source, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                data = data.get("issues", data.get("data", []))
            return list(data)
        if ext == ".jsonl":
            rows = []
            with open(source, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        rows.append(json.loads(line))
            return rows
        # 无扩展名：嗅探——先试 JSON（数组/对象包装），再试 JSONL，最后 CSV
        try:
            return _json_rows(source)
        except Exception:
            pass
        try:
            rows = []
            with open(source, encoding="utf-8", newline="") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        rows.append(json.loads(line))
            if rows:
                return rows
        except Exception:
            pass
        return _csv_rows(source)

    def detect(self, source: str) -> bool:
        if not os.path.isfile(source):
            return False
        ext = os.path.splitext(source)[1].lower()
        if ext in (".csv", ".json", ".jsonl"):
            return True
        try:
            rows = self._load_rows(source)
            return len(rows) > 0
        except Exception:
            return False

    def discover(self, source: str) -> list[dict]:
        rows = self._load_rows(source)
        tasks = []
        for idx, row in enumerate(rows):
            key, _ = _first(_KEY_ALIASES, row)
            kid = str(key) if key is not None else f"row-{idx + 1}"
            tasks.append({"id": kid, "loc": f"{source}#{idx + 2}", "raw": {"row": row, "idx": idx}})
        return tasks

    def to_episode(self, source: str, task: dict) -> dict:
        row = task["raw"]["row"]
        key, _ = _first(_KEY_ALIASES, row)
        kid = str(key) if key is not None else task["id"]
        title, _ = _first(_TITLE_ALIASES, row)
        status_raw, _ = _first(_STATUS_ALIASES, row)
        agents = _collect_agents(row)
        desc, _ = _first(_DESC_ALIASES, row)
        created, _ = _first(_CREATED_ALIASES, row)
        updated, _ = _first(_UPDATED_ALIASES, row)
        comments_raw, _ = _first(_COMMENT_COL_ALIASES, row)

        comments: list[dict] = []
        if comments_raw is not None:
            text = comments_raw if isinstance(comments_raw, str) else json.dumps(comments_raw, ensure_ascii=False)
            comments = [{"ts": None, "agent": None, "text": t.strip()}
                        for t in text.splitlines() if t.strip()]

        status = status_normalize(str(status_raw or ""))
        ep = {
            "episode_id": f"ep-{kid}",
            "issue_key": kid,
            "issue_id": kid,
            "title": str(title or kid),
            "status": status,
            "goal": str(title or kid),
            "acceptance": str(desc or ""),
            "agent_ids": agents,
            "main_agent": agents[0] if agents else None,
            "turn_count": len(comments),
            "comments": comments,
            "attachments": [],
            "business_join_key": build_join_key(
                "source_table", kid, str(updated or created or "") or None),
            "score": None,
            "excluded": False,
            "output_signal": status == "done",
            "shape": self.shape,
        }
        return normalize_episode(ep, self.shape)

    def depth(self, source: str) -> str:
        return "table: key+title+status+assignee(+comments)"


def _json_rows(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        text = f.read()
    data = json.loads(text)  # JSON 数组 / {"issues": [...]} / {"data": [...]}
    if isinstance(data, dict):
        data = data.get("issues", data.get("data", []))
    return list(data)


def _csv_rows(path: str) -> list[dict]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


# ---------------------------------------------------------------------------
# 4) session-logs：agent 会话日志目录（Codex / Claude Code / 通用 jsonl）
# ---------------------------------------------------------------------------

_SESSION_META_KEYS = ("sessionId", "session_id", "id", "uuid", "cwd", "gitBranch",
                      "branch", "originator", "entrypoint", "agent", "title",
                      "custom-title", "instructions", "cli_version", "version")


def _session_line_fields(obj: dict) -> dict:
    """把会话 jsonl 的任意一行压平为 {ts, role, text, tool, meta}（容忍 schema 漂移）。"""
    out = {"ts": None, "role": None, "text": None, "tool": None, "meta": {}}
    payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
    message = obj.get("message") if isinstance(obj.get("message"), dict) else {}
    content = message.get("content") or payload.get("content") or []
    if isinstance(content, str):
        content = [{"type": "text", "text": content}]

    # 时间戳：timestamp 键在 obj / payload 任一层
    ts = obj.get("timestamp") or payload.get("timestamp") or message.get("timestamp") \
        or obj.get("created_at") or obj.get("createdAt")
    out["ts"] = str(ts) if ts else None

    # 角色：message.role / payload.role / 顶层 type
    role = message.get("role") or payload.get("role") or obj.get("type") or obj.get("role")
    out["role"] = str(role) if role else None

    # payload / 顶层级类型（function_call / tool_use / exec_command_begin）
    ptype = payload.get("type") or obj.get("type") or ""
    if ptype == "function_call":
        out["tool"] = payload.get("name")
    elif ptype == "tool_use":
        out["tool"] = payload.get("name")
    elif ptype == "exec_command_begin":
        out["tool"] = "shell"

    # 文本：content 块逐段收集
    texts = []
    tool = out["tool"]
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type", "")
        if btype in ("tool_use", "function_call"):
            tool = tool or block.get("name")
        elif btype in ("text", "output_text", "input_text"):
            texts.append(str(block.get("text", "")))
        elif btype == "tool_result":
            c = block.get("content")
            if isinstance(c, str):
                texts.append(c)
            elif isinstance(c, list):
                texts.extend(str(x.get("text", "")) for x in c if isinstance(x, dict))
        elif btype == "agent_message":
            texts.append(str(block.get("message", "")))
    if isinstance(payload.get("message"), str):
        texts.append(payload["message"])
    if isinstance(obj.get("message"), str):
        texts.append(obj["message"])
    out["text"] = "\n".join(t for t in texts if t) or None
    out["tool"] = tool
    # 元数据：session_meta / 顶层常见键
    if ptype == "session_meta" or obj.get("type") == "session_meta":
        out["meta"] = payload or obj
    for k in _SESSION_META_KEYS:
        if obj.get(k) is not None:
            out["meta"].setdefault(k, obj[k])
    return out


def _detect_session_like(source: str) -> bool:
    """目录内 .jsonl 行是否含会话特征键（type/timestamp/payload/message/role）。"""
    hits = 0
    for name in os.listdir(source):
        if not name.endswith(".jsonl"):
            continue
        path = os.path.join(source, name)
        try:
            with open(path, encoding="utf-8") as f:
                for _ in range(40):
                    line = f.readline()
                    if not line:
                        break
                    line = line.strip()
                    if not line:
                        continue
                    obj = json.loads(line)
                    keys = set(obj.keys())
                    if keys & {"timestamp", "payload", "message", "role", "sessionId"}:
                        hits += 1
                    break  # 只看首个有效行
        except Exception:
            continue
        if hits:
            return True
    return False


class SessionLogsAdapter(SourceAdapter):
    provider = "session-logs"
    channels = ["session-logs"]
    shape = "session-logs"

    def detect(self, source: str) -> bool:
        if os.path.isfile(source) and source.endswith(".jsonl"):
            return True
        return os.path.isdir(source) and _detect_session_like(source)

    def discover(self, source: str) -> list[dict]:
        if os.path.isfile(source):
            files = [source]
        else:
            files = sorted(os.path.join(source, n) for n in os.listdir(source)
                           if n.endswith(".jsonl"))
        tasks = []
        for path in files:
            stem = os.path.splitext(os.path.basename(path))[0]
            tasks.append({"id": stem, "loc": path, "raw": {"path": path, "stem": stem}})
        return tasks

    def to_episode(self, source: str, task: dict) -> dict:
        path = task["raw"]["path"]
        turns: list[dict] = []
        meta: dict = {}
        last_ts = None
        has_assistant_text = False
        has_tool = False
        agent_names: list[str] = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                fl = _session_line_fields(obj)
                if fl["meta"]:
                    meta.update(fl["meta"])
                if fl["ts"]:
                    last_ts = fl["ts"]
                if fl["tool"]:
                    has_tool = True
                role = (fl["role"] or "").lower()
                # Claude Code 的 agent-name / custom-title 行是元信息，不构成 turn
                if obj.get("type") == "agent-name" and fl["text"]:
                    agent_names.append(fl["text"])
                if obj.get("type") == "custom-title" and fl["text"] and not meta.get("title"):
                    meta["title"] = fl["text"]
                if role in ("user", "assistant") and fl["text"]:
                    turns.append({"ts": fl["ts"], "agent": role, "text": fl["text"]})
                    if role == "assistant":
                        has_assistant_text = True

        agent = (meta.get("originator") or meta.get("agent")
                 or (meta.get("entrypoint") if meta.get("entrypoint") not in ("cli", "code") else None)
                 or (agent_names[-1] if agent_names else None)
                 or "agent")
        agents = list(dict.fromkeys([str(agent)] + [a for a in agent_names if a]))
        title = (str(meta.get("title") or meta.get("instructions") or "")
                 or (turns[0]["text"][:60] if turns else task["raw"]["stem"]))
        # 完成信号（弱）：会话有 assistant 文本产出 或 有工具调用
        output_signal = has_assistant_text or has_tool
        status = "done" if output_signal else "open"

        ep = {
            "episode_id": f"ep-{task['raw']['stem']}",
            "issue_key": task["raw"]["stem"],
            "issue_id": task["raw"]["stem"],
            "title": title,
            "status": status,
            "goal": title,
            "acceptance": turns[-1]["text"][:800] if turns else "",
            "agent_ids": agents,
            "main_agent": agents[0] if agents else None,
            "turn_count": len(turns),
            "comments": turns,
            "attachments": [],
            "business_join_key": build_join_key("session_logs", task["raw"]["stem"], last_ts),
            "score": None,
            "excluded": False,
            "output_signal": output_signal,
            "shape": self.shape,
            "session_meta": {k: meta.get(k) for k in
                             ("cwd", "gitBranch", "cli_version", "version", "model_provider")
                             if meta.get(k) is not None},
        }
        return normalize_episode(ep, self.shape)

    def depth(self, source: str) -> str:
        return "session-logs: turns+ts+agent（弱完成信号，单 agent 会话团队信号受限）"


# ---------------------------------------------------------------------------
# 5) git-repo：Git 仓库（git log，一次 commit = 一个任务）
# ---------------------------------------------------------------------------

_GIT_FMT = "@@CG@@%x1f%H%x1f%an%x1f%aI%x1f%s%x1f%b"
_GIT_EXCLUDE_PATTERNS = ("merge branch", "merge pull request", "revert", "bump version")


class GitRepoAdapter(SourceAdapter):
    provider = "git-repo"
    channels = ["git-repo"]
    shape = "git-repo"

    def detect(self, source: str) -> bool:
        return os.path.isdir(os.path.join(source, ".git"))

    def discover(self, source: str) -> list[dict]:
        cmd = ["git", "-C", source, "log", "--no-merges", "--name-only",
               f"--format={_GIT_FMT}"]
        # 历史窗口上限：大仓库（万级 commit）对 O(n²) 向量层不可行，默认取最近 500 条
        # （可用 SKILLTROVE_GIT_MAX_COMMITS 覆盖；0/空 = 不限）
        v = os.environ.get("SKILLTROVE_GIT_MAX_COMMITS", "500")
        if v.isdigit() and int(v) > 0:
            cmd.append(f"--max-count={v}")
        try:
            out = subprocess.run(cmd,
                                 capture_output=True, text=True, encoding="utf-8",
                                 errors="replace", timeout=180, check=True).stdout
        except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
            return []
        tasks = []
        cur = None
        for line in out.splitlines():
            if line.startswith("@@CG@@"):
                if cur is not None and _keep_commit(cur):
                    tasks.append({"id": cur["hash"][:7], "loc": f"git:{cur['hash'][:7]}",
                                  "raw": cur})
                parts = line.split("\x1f")
                cur = {"hash": parts[1] if len(parts) > 1 else "",
                       "author": parts[2] if len(parts) > 2 else "",
                       "date": parts[3] if len(parts) > 3 else "",
                       "subject": parts[4] if len(parts) > 4 else "",
                       "body": parts[5] if len(parts) > 5 else "",
                       "files": []}
            elif cur is not None and line.strip():
                cur["files"].append(line.strip())
        if cur is not None and _keep_commit(cur):
            tasks.append({"id": cur["hash"][:7], "loc": f"git:{cur['hash'][:7]}", "raw": cur})
        return tasks

    def to_episode(self, source: str, task: dict) -> dict:
        c = task["raw"]
        subject = c.get("subject", "")
        ep = {
            "episode_id": f"ep-{c['hash'][:7]}",
            "issue_key": c["hash"][:7],
            "issue_id": c["hash"],
            "title": subject,
            "status": "done",
            "goal": subject,
            "acceptance": c.get("body", ""),
            "agent_ids": [c.get("author", "")] if c.get("author") else [],
            "main_agent": c.get("author") or None,
            "turn_count": 0,
            "comments": [],
            "attachments": c.get("files", []),
            "business_join_key": build_join_key("git_commit", c["hash"][:7], c.get("date")),
            "score": None,
            "excluded": False,
            "output_signal": bool(c.get("files")),
            "shape": self.shape,
        }
        return normalize_episode(ep, self.shape)

    def depth(self, source: str) -> str:
        return "git-repo: commit(subject/body/author/date/files)（commit 级语义）"


def _keep_commit(c: dict) -> bool:
    subject = (c.get("subject") or "").lower()
    if any(p in subject for p in _GIT_EXCLUDE_PATTERNS):
        return False
    return len(c.get("subject") or "") >= 4


# ---------------------------------------------------------------------------
# 6) docs：文档目录（仅经验池，显式降级不参与发现）
# ---------------------------------------------------------------------------

_MD_EXTS = (".md", ".markdown", ".txt")


class DocsAdapter(SourceAdapter):
    provider = "docs"
    channels = ["docs"]
    shape = "docs"

    def detect(self, source: str) -> bool:
        if not os.path.isdir(source):
            return False
        return any(os.path.isfile(os.path.join(source, n)) and n.endswith(_MD_EXTS)
                   for n in os.listdir(source))

    def discover(self, source: str) -> list[dict]:
        tasks = []
        for name in sorted(os.listdir(source)):
            if name.endswith(_MD_EXTS) and os.path.isfile(os.path.join(source, name)):
                stem = os.path.splitext(name)[0]
                tasks.append({"id": stem, "loc": f"docs:{name}", "raw": {"file": name}})
        return tasks

    def to_episode(self, source: str, task: dict) -> dict:
        name = task["raw"]["file"]
        path = os.path.join(source, name)
        with open(path, encoding="utf-8") as f:
            text = f.read()
        mtime = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(os.path.getmtime(path)))
        title = markdown_title(text) or os.path.splitext(name)[0]
        ep = {
            "episode_id": f"ep-{task['id']}",
            "issue_key": task["id"],
            "issue_id": task["id"],
            "title": title,
            "status": None,
            "goal": title,
            "acceptance": parse_description_md(text),
            "agent_ids": [],
            "main_agent": None,
            "turn_count": 0,
            "comments": [],
            "attachments": [],
            "business_join_key": build_join_key("doc_file", task["id"], mtime),
            "score": None,
            "excluded": False,
            "output_signal": False,
            "shape": self.shape,
        }
        return normalize_episode(ep, self.shape)

    def depth(self, source: str) -> str:
        return "docs: 仅经验池（无归因/完成态，不参与聚类发现）"


# ---------------------------------------------------------------------------
# 注册表 + 自动判定 + 统一出口
# ---------------------------------------------------------------------------

ADAPTERS: list[SourceAdapter] = [
    LogExportAdapter(),       # MANIFEST.json 特征明确，优先
    TaskDirsAdapter(),
    SessionLogsAdapter(),   # 须先于 Table：jsonl 会话文件不能当表格行
    GitRepoAdapter(),
    TableAdapter(),
    DocsAdapter(),
]


def pick_adapter(source: str, name: str = "auto") -> SourceAdapter:
    """按名字或自动判定选择适配器（auto 按 ADAPTERS 顺序第一个 detect 命中）。"""
    if name and name != "auto":
        for a in ADAPTERS:
            if a.shape == name or a.provider == name:
                return a
        raise ValueError(f"未知适配器: {name!r}（可选: auto/{'/'.join(a.shape for a in ADAPTERS)}）")
    for a in ADAPTERS:
        if a.detect(source):
            return a
    supported = "、".join(a.shape for a in ADAPTERS)
    raise ValueError(f"无法识别的数据源: {source!r}（支持形态: {supported}；"
                     f"或显式指定 --adapter）")


def run_export(source: str, out_dir: str, adapter: SourceAdapter,
               exclude_agents: list[str] | None = None) -> tuple[list[dict], dict]:
    """统一出口：discover → to_episode → 过滤 → facts + episodes + manifest。

    返回 (episodes, facts)。写 out_dir/manifest.json（归一化任务清单，
    score 的 join 查证统一指向它）、facts.json、episodes.jsonl。
    """
    exclude = exclude_agents or []
    episodes = [adapter.to_episode(source, t) for t in adapter.discover(source)]
    for ep in episodes:
        if ep["main_agent"] in exclude:
            ep["excluded"] = True
        ep["shape"] = ep.get("shape") or adapter.shape

    os.makedirs(out_dir, exist_ok=True)

    # 归一化 manifest（多来源统一，score join 查证用）
    manifest = {"issues": [{"issue": ep["issue_key"], "status": ep["status"]}
                           for ep in episodes if not ep["excluded"]]}
    with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    facts = {
        "facts_id": f"facts-{time.strftime('%Y%m%d')}",
        "frozen_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "provider": adapter.provider,
        "shape": adapter.shape,
        "source": os.path.abspath(source),
        "time_window": {"from": None, "to": None},
        "depth": adapter.depth(source),
        "permissions": "readonly-archive",
        "contributors": [],
        "excluded_agents": exclude,
        "channels": adapter.channels,
        "signature_note": "facts 随包冻结，下游只读分析",
    }
    seen: dict[str, list[str]] = {}
    for ep in episodes:
        for a in ep["agent_ids"]:
            seen.setdefault(a, []).append(ep["issue_key"])
    facts["contributors"] = [{"agent": a, "issues": keys} for a, keys in sorted(seen.items())]

    with open(os.path.join(out_dir, "facts.json"), "w", encoding="utf-8") as f:
        json.dump(facts, f, ensure_ascii=False, indent=2)
    with open(os.path.join(out_dir, "episodes.jsonl"), "w", encoding="utf-8") as f:
        for ep in episodes:
            f.write(json.dumps(ep, ensure_ascii=False) + "\n")
    return episodes, facts
