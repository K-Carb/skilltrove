"""成员侧记录来源扫描（探测报告）：本机哪里有可用的记录、四要素覆盖率如何。

探测 = 对每个候选来源：适配器识别 → 抽样翻译前 3 条 → 报告四要素覆盖率。
让成员在投入任何时间之前就知道"这个来源能不能用、缺什么"。
"""

from __future__ import annotations

import os

from cli import adapters
from cli.adapters import pick_adapter, status_normalize

# 已知会话记录位置（按助手类型枚举；目标用户机器上这些位置天然存在）
KNOWN_SESSION_LOCATIONS = [
    os.path.expanduser("~/.claude/projects"),          # Claude Code
    os.path.expanduser("~/.codex/sessions"),           # Codex CLI
]


def _coverage(episodes: list[dict]) -> dict:
    """对样本 episode 报告四要素覆盖率（goal 文本 / 完成态 / 归属者 / 证据）。"""
    n = len(episodes) or 1
    def frac(pred) -> float:
        return round(sum(1 for e in episodes if pred(e)) / n, 2)
    return {
        "样本数": len(episodes),
        "goal 文本": frac(lambda e: len((e.get("goal") or "").strip()) >= 10),
        "完成态已知": frac(lambda e: bool((e.get("status") or "").strip())),
        "完成类占比": frac(lambda e: status_normalize(e.get("status") or "") == "done"),
        "归属者": frac(lambda e: bool(e.get("main_agent"))),
        "证据（评论/验收）": frac(lambda e: bool((e.get("comments") or []) or (e.get("acceptance") or "").strip())),
    }


def _probe(source: str, name: str) -> dict | None:
    """探测单个来源：识别适配器、抽样翻译、报告覆盖率。不可识别返回 None。"""
    try:
        adapter = pick_adapter(source, name)
    except ValueError:
        return None
    try:
        tasks = adapter.discover(source)[:3]
        eps = [adapters.normalize_episode(adapter.to_episode(source, t), adapter.shape) for t in tasks]
    except Exception as e:  # noqa: BLE001 —— 探测阶段任何失败都降级为"不可用"报告
        return {"source": source, "shape": adapter.shape, "usable": False, "error": str(e)[:120]}
    return {
        "source": source,
        "shape": adapter.shape,
        "usable": bool(eps),
        "coverage": _coverage(eps) if eps else None,
    }


def scan_sources(extra_dirs: list[str] | None = None) -> list[dict]:
    """扫描本机已知位置 + 显式传入的目录，返回探测报告列表。

    目录型适配器（会话目录/团队记录库）探测目录本身；
    文件型适配器（数据表/会话文件）逐个探测目录内的文件。
    """
    candidates: list[tuple[str, str]] = []  # (source, adapter_name or "auto")

    def add_dir(d: str) -> None:
        if not os.path.isdir(d):
            return
        candidates.append((d, "auto"))
        for n in sorted(os.listdir(d)):
            fp = os.path.join(d, n)
            if os.path.isfile(fp) and not n.endswith((".bin", ".log")):
                candidates.append((fp, "auto"))

    # 1) 已知会话记录位置（按助手类型）
    for base in KNOWN_SESSION_LOCATIONS:
        if not os.path.isdir(base):
            continue
        for proj in sorted(os.listdir(base)):
            add_dir(os.path.join(base, proj))

    # 2) 显式目录（SKILLTROVE_SOURCES_DIR / profile 登记的来源）
    for d in extra_dirs or []:
        add_dir(d)

    reports = []
    seen = set()
    for source, name in candidates:
        key = os.path.abspath(source).lower()
        if key in seen:
            continue
        seen.add(key)
        r = _probe(source, name)
        if r:
            reports.append(r)
    return reports


def load_profile(path: str) -> dict:
    import json

    if os.path.isfile(path):
        return json.load(open(path, encoding="utf-8"))
    return {"member": "", "sources": [], "stoplist": []}


def save_profile(path: str, profile: dict) -> None:
    import json

    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    json.dump(profile, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
