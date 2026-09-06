"""团队记录库（Model 1）：成员侧推送 + 脱敏 + 质量闸门 + 发现侧拉取。

数据流（ADR-0004）：
  成员本机 --export+脱敏+质量闸门--> records/episodes-<成员>.jsonl（自己的分片，全量重写幂等）
                                  --git push--> 共享仓库
  发现机器 --pull--> 读全部分片拼成 episode 流（GitRecordsAdapter）

设计要点：
- 分片按人：每人只写自己的文件，git 永不冲突
- 幂等：分片由成员本机全量重写，重复推送不产生重复记录
- 隐私门在出口：脱敏发生在数据离开本机之前，推送前有确认报告
"""

from __future__ import annotations

import json
import os
import re
import subprocess

# ---------------------------------------------------------------------------
# 脱敏
# ---------------------------------------------------------------------------

# 高置信敏感模式：命中即替换
_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"ghp_[A-Za-z0-9]{30,}"),
    re.compile(r"gho_[A-Za-z0-9]{30,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"(?i)(api[_-]?key|secret|token|password|passwd)\s*[:=]\s*\S+"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._-]{10,}"),
]

REDACTED = "[已移除疑似敏感内容]"


def sanitize_text(text: str, stoplist: list[str] | None = None) -> tuple[str, int]:
    """清洗一段文本，返回 (清洗后文本, 命中次数)。"""
    hits = 0
    out = text or ""
    for pat in _SECRET_PATTERNS:
        out, n = re.subn(pat, REDACTED, out)
        hits += n
    for word in stoplist or []:
        if word and word.lower() in out.lower():
            out = re.sub(re.escape(word), "某客户", out, flags=re.IGNORECASE)
            hits += out.count("某客户")
    return out, hits


def sanitize_episode(ep: dict, stoplist: list[str] | None = None) -> tuple[dict, int]:
    """逐字段清洗一条 episode；附件只留文件名（正文可能含敏感内容）。"""
    total = 0
    clean = {}
    for k, v in ep.items():
        if k == "attachments":
            clean[k] = [(a if isinstance(a, str) else a.get("name", "")) for a in (v or [])]
            continue
        if isinstance(v, str):
            clean[k], n = sanitize_text(v, stoplist)
            total += n
        elif isinstance(v, list):
            new_list = []
            for item in v:
                if isinstance(item, dict):
                    item = dict(item)
                    if "text" in item:
                        item["text"], n = sanitize_text(item["text"], stoplist)
                        total += n
                elif isinstance(item, str):
                    item, n = sanitize_text(item, stoplist)
                    total += n
                new_list.append(item)
            clean[k] = new_list
        else:
            clean[k] = v
    return clean, total


# ---------------------------------------------------------------------------
# 质量闸门（"足以做成 episode"的机械定义）
# ---------------------------------------------------------------------------

_DONE_CLASS = {"done", "in_review"}


def quality_gate(ep: dict, min_goal_chars: int = 15, min_title_chars: int = 4) -> tuple[bool, list[str]]:
    """四要素合格线：标题/目标文本、完成态、归属者、证据。返回 (是否合格, 不合格原因列表)。"""
    reasons = []
    title = (ep.get("title") or "").strip()
    goal = ((ep.get("goal") or "") + " " + (ep.get("acceptance") or "")).strip()
    if len(title) < min_title_chars:
        reasons.append(f"title 不足 {min_title_chars} 字")
    if len(goal) < min_goal_chars:
        reasons.append(f"goal/acceptance 合计不足 {min_goal_chars} 字")
    st = (ep.get("status") or "").strip().lower()
    if st not in _DONE_CLASS:
        reasons.append(f"状态非完成态: {st or '空'}")
    if not (ep.get("main_agent") or (ep.get("agent_ids") or [None])[0]):
        reasons.append("缺少归属者")
    if not ((ep.get("comments") or []) or (ep.get("acceptance") or "").strip()):
        reasons.append("无评论且无验收文本")
    return (not reasons, reasons)


# ---------------------------------------------------------------------------
# 分片推送 / 拉取
# ---------------------------------------------------------------------------

def _git(args: list[str], cwd: str | None = None, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True, check=check)


def push_shard(remote: str, member: str, episodes: list[dict], cache_root: str,
               member_id_sha: str = "") -> dict:
    """把成员的 episodes 全量重写进自己的分片并推送到共享仓库。

    幂等：重复推送同一批内容时 commit 为空，视为成功（no-op）。
    返回 {"ok": bool, "error": str|None, "shard": str, "count": int}。
    """
    if not member or not re.fullmatch(r"[\w.-]+", member):
        return {"ok": False, "error": f"成员名不合法: {member!r}（仅允许字母数字._-）", "count": 0}
    cache = os.path.join(cache_root, "records-repo")
    try:
        if os.path.isdir(os.path.join(cache, ".git")):
            _git(["pull", "--ff-only"], cwd=cache, check=False)  # 没有远端提交时拉取失败无害
        else:
            os.makedirs(cache_root, exist_ok=True)
            _git(["clone", remote, cache])
        shard_rel = os.path.join("records", f"episodes-{member}.jsonl")
        shard = os.path.join(cache, shard_rel)
        os.makedirs(os.path.dirname(shard), exist_ok=True)
        with open(shard, "w", encoding="utf-8") as f:
            for ep in episodes:
                f.write(json.dumps(ep, ensure_ascii=False) + "\n")
        _git(["add", "records"], cwd=cache)
        msg = f"records: {member} sync {len(episodes)} episodes"
        c = _git(["commit", "-m", msg], cwd=cache, check=False)
        if c.returncode != 0 and "nothing to commit" not in (c.stdout + c.stderr):
            raise RuntimeError(c.stderr.strip()[:300])
        if c.returncode == 0:
            _git(["push"], cwd=cache)
        return {"ok": True, "error": None, "shard": shard_rel, "count": len(episodes)}
    except Exception as e:  # noqa: BLE001 —— 边界层统一把异常转成结构化返回
        return {"ok": False, "error": str(e), "count": len(episodes)}


def pull_records(remote: str, cache_root: str) -> str:
    """克隆或更新共享记录仓库到本地缓存，返回缓存目录。"""
    cache = os.path.join(cache_root, "records-repo")
    if os.path.isdir(os.path.join(cache, ".git")):
        _git(["pull", "--ff-only"], cwd=cache, check=False)
    else:
        os.makedirs(cache_root, exist_ok=True)
        _git(["clone", remote, cache])
    return cache


def load_shards(records_dir: str) -> list[dict]:
    """读取 records/ 下所有成员分片，拼接为 episode 流。"""
    rd = os.path.join(records_dir, "records")
    if not os.path.isdir(rd):
        return []
    eps = []
    for n in sorted(os.listdir(rd)):
        if not (n.startswith("episodes-") and n.endswith(".jsonl")):
            continue
        with open(os.path.join(rd, n), encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    eps.append(json.loads(line))
    return eps


# ---------------------------------------------------------------------------
# CLI 命令处理器（cli/main.py 派发）
# ---------------------------------------------------------------------------

def cmd_scan(args) -> int:
    """探测报告：本机哪里有记录来源、四要素覆盖率如何。"""
    from cli.scan import scan_sources

    reports = scan_sources(extra_dirs=list(args.dir))
    if not reports:
        print("未扫描到任何可识别的记录来源。")
        print("支持：AI 助手会话目录（.jsonl）/ 任务文件夹 / 数据表 / 导出包 / 文档目录。")
        return 1
    print(f"扫描到 {len(reports)} 个候选来源：\n")
    for r in reports:
        print(f"  [{r['shape']}] {r['source']}")
        if not r.get("usable"):
            print(f"    ✗ 不可用：{r.get('error', '未产出 episode')}")
            continue
        cov = r.get("coverage") or {}
        print(f"    ✓ 可用（样本 {cov.get('样本数', 0)} 条）")
        print(f"      goal 文本 {cov.get('goal 文本', 0)} · 完成态已知 {cov.get('完成态已知', 0)}"
              f" · 完成类占比 {cov.get('完成类占比', 0)} · 归属者 {cov.get('归属者', 0)}"
              f" · 证据 {cov.get('证据（评论/验收）', 0)}")
    print("\n说明：完成态“已知”不等于“已完成”——会话类来源在 export-push 时需逐条确认。")
    return 0


def cmd_export_push(args) -> int:
    """M1 成员侧：导出 → 脱敏 → 质量闸门 → 确认 → 分片推送。"""
    import argparse
    import tempfile

    from cli import export as export_mod
    from cli.scan import load_profile, save_profile

    profile = load_profile(args.profile)
    stoplist = profile.get("stoplist") or []

    # 1) 导出到临时目录（复用现有 export 编排）
    with tempfile.TemporaryDirectory() as tmp:
        export_args = argparse.Namespace(
            source=args.source, out=tmp, exclude_agents="", adapter="auto")
        try:
            export_mod.run(export_args)
        except Exception as e:  # noqa: BLE001
            print(f"导出失败: {e}")
            return 1
        episodes_path = os.path.join(tmp, "episodes.jsonl")
        if not os.path.isfile(episodes_path):
            print("导出未产出 episodes.jsonl（数据源可能为空）。")
            return 1
        with open(episodes_path, encoding="utf-8") as f:
            episodes = [json.loads(line) for line in f if line.strip()]

    if not episodes:
        print("来源里没有可用记录。")
        return 1

    # 2) 脱敏（出口隐私门）
    redactions = 0
    clean_eps = []
    for ep in episodes:
        clean, n = sanitize_episode(ep, stoplist)
        clean_eps.append(clean)
        redactions += n

    # 3) 质量闸门
    passed, rejected = [], []
    for ep in clean_eps:
        ok, reasons = quality_gate(ep)
        (passed if ok else rejected).append((ep, reasons))

    # 4) 推送报告
    print(f"\n=== 推送报告（来源 {args.source}） ===")
    print(f"扫描 {len(clean_eps)} 条：达标 {len(passed)} 条，不合格 {len(rejected)} 条，脱敏 {redactions} 处\n")
    for ep, reasons in rejected[:10]:
        print(f"  ✗ {ep.get('issue_key') or ep.get('title', '?')}: {'；'.join(reasons)}")
    if len(rejected) > 10:
        print(f"  … 其余 {len(rejected) - 10} 条不合格从略")
    for ep, _ in passed:
        print(f"  ✓ {ep.get('issue_key')}  {(ep.get('title') or '')[:40]}")
    if not passed:
        print("\n没有达标的记录可推送。常见原因：任务未完成态、正文太短。")
        return 1
    if len(passed) > args.limit:
        print(f"\n超过单次上限 {args.limit} 条，本轮只推前 {args.limit} 条（其余下轮再推）。")
        passed = passed[:args.limit]

    if not args.yes:
        answer = input(f"\n确认推送 {len(passed)} 条到 {args.remote}？[Y/n] ").strip().lower()
        if answer not in ("", "y", "yes"):
            print("已取消。")
            return 0

    # 5) 分片推送（幂等：全量重写自己的分片）
    result = push_shard(args.remote, args.member, [ep for ep, _ in passed],
                        cache_root=os.path.join("data", "records-cache"))
    if not result["ok"]:
        print(f"推送失败: {result['error']}")
        return 1
    print(f"\n已推送 {result['count']} 条到 {result['shard']}（分片幂等，重复推送无副作用）。")

    # 6) 来源档案：登记本次来源，下次免配置
    profile.setdefault("member", args.member)
    if args.source not in profile.get("sources", []):
        profile.setdefault("sources", []).append(args.source)
    save_profile(args.profile, profile)
    print(f"来源已登记到 {args.profile}（下次 export-push 直接使用）。")
    return 0


def cmd_sync_records(args) -> int:
    """M2 发现侧：拉取团队记录库到本地缓存（之后作为数据源被 git-records 适配器读取）。"""
    try:
        cache = pull_records(args.remote, args.out)
    except Exception as e:  # noqa: BLE001
        print(f"拉取失败: {e}")
        return 1
    eps = load_shards(cache)
    print(f"已同步团队记录库 -> {cache}")
    print(f"记录：{len(eps)} 条（分片 {len(os.listdir(os.path.join(cache, 'records'))) if os.path.isdir(os.path.join(cache, 'records')) else 0} 个）")
    print("下一步：把这个缓存目录作为 --source 交给 export / 一键发现。")
    return 0
