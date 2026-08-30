"""F2 聚类：高分 episode → 本地向量粗筛 → LLM 四维契约复核（Spec §4.3）。

流程：
  1. 取 scored.jsonl 中 high 分、未 excluded 的 episode
  2. 语料 = title + description（任务定义域，实测避免正文通用术语噪声）
  3. embed.pairs_above 相似配对（默认阈值 0.5，宁多勿漏）
  4. 逐对 LLM 四维契约复核（目标/产物/输入形态/验收边界 + 贡献者集合 + 防火墙 5 条）
  5. 通过复核的配对按连通分量合并为候选簇，输出 candidates.json
LLM 不可用时降级规则复核（review_method=rule-fallback），并显式记录。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cli import embed  # noqa: E402
from cli import llm    # noqa: E402

FIREWALL_RULES = [
    "目标不同（任务类型不同）→ 不算同类",
    "产物不同（交付物形态不同）→ 不算同类",
    "输入形态不同（数据来源/工具集不同）→ 不算同类",
    "验收边界不同 → 不算同类",
    "疑似一次性事件 / fork 分支 → defer，不产候选",
]

REVIEW_SYSTEM = (
    "你是 SkillTrove 的候选复核器：判断两个 agent 执行的任务是否构成「同类重复经验」。"
    "严格按四维契约判断，输出纯 JSON，不要额外文字。"
)

KIND_HINT = {
    "procedure": "程序级重复（同一做法可复现执行）",
    "goal": "目标级重复（同类目标，做法可参考）",
}


DEFAULT_THRESHOLD = 0.5
# 按数据源形态校准的默认粗筛阈值：文本薄、异构度高的来源下调（宁多勿漏，LLM 复核兜底）。
# git-repo：commit 级语料只有 subject+body，实测真实重复对 sim=0.4841 < 0.5，
# 且 0.40 下无新增假对（2026-08-26 本仓库 22 commit 验证）。
SHAPE_DEFAULT_THRESHOLD = {"git-repo": 0.40}


def resolve_threshold(arg_threshold: float | None, shape: str) -> float:
    """粗筛阈值：显式指定优先；否则按数据源形态取默认（未知形态用 0.5）。"""
    if arg_threshold is not None:
        return arg_threshold
    return SHAPE_DEFAULT_THRESHOLD.get(shape or "", DEFAULT_THRESHOLD)


def build_corpus(ep: dict) -> str:
    """聚类语料 = 任务定义域（title/description），避免评论正文噪声。"""
    goal = ep.get("goal") or ep.get("title") or ""
    acceptance = (ep.get("acceptance") or "")[:800]
    return goal + "\n" + acceptance


def load_high_episodes(path: str) -> list[dict]:
    eps = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            ep = json.loads(line)
            if ep.get("excluded"):
                continue
            score = ep.get("score") or {}
            if score.get("label") == "high":
                eps.append(ep)
    return eps


def _episode_summary(ep: dict) -> str:
    return (
        f"[{ep.get('episode_id')}] 任务: {ep.get('goal') or ep.get('title')}\n"
        f"  验收/描述: {(ep.get('acceptance') or '')[:400]}\n"
        f"  主执行: {ep.get('main_agent')} | 状态: {ep.get('status')} | 评论数: {ep.get('turn_count')}"
    )


def review_prompt(ep_a: dict, ep_b: dict) -> str:
    agents = set(ep_a.get("agent_ids") or []) | set(ep_b.get("agent_ids") or [])
    return (
        "判断以下两个任务是否同类重复。\n\n"
        "判断维度（四维契约）：\n"
        "1. 目标（task goal）是否同类\n"
        "2. 产物（deliverable）是否同类\n"
        "3. 输入形态（输入数据 / 工具集）是否同类\n"
        "4. 验收边界是否同类\n\n"
        "防火墙（满足任一条即判 different）：\n" + "\n".join(f"- {r}" for r in FIREWALL_RULES) + "\n\n"
        "任务 A：\n" + _episode_summary(ep_a) + "\n\n"
        "任务 B：\n" + _episode_summary(ep_b) + "\n\n"
        f"贡献者信息：任务 A 执行 agent={ep_a.get('agent_ids')}；任务 B 执行 agent={ep_b.get('agent_ids')}；"
        f"合并 distinct agent 数={len(agents)}。"
        "（提示：2+ 个不同 agent 分别完成同类任务 = 强团队信号，倾向于 same）\n\n"
        '只输出 JSON：{"judgement": "same"|"different", '
        '"target": "同类|不同类", "product": "同类|不同类", "input_form": "同类|不同类", '
        '"acceptance": "同类|不同类", "firewall_hit": "无"|"规则N", "kind": "procedure"|"goal", "note": "一句话"}'
    )


def parse_review(text: str) -> dict:
    """从 LLM 输出提取 JSON（容忍 ```json 围栏与前后杂讯）。"""
    if not text:
        return {}
    text = text.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        text = text[start:end + 1]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {}


def rule_fallback(ep_a: dict, ep_b: dict) -> dict:
    """LLM 不可用时的降级复核：主题词粗判。"""
    a = (ep_a.get("goal") or "") + (ep_a.get("title") or "")
    b = (ep_b.get("goal") or "") + (ep_b.get("title") or "")
    s = embed.similarity(a, b)
    # 主题关键词交集（调研/PRD/评审/范围/分析）
    for kw in ("调研", "PRD", "评审", "范围", "分析"):
        if kw in a and kw in b:
            return {"judgement": "same", "kind": "goal", "note": f"规则降级：同含关键词「{kw}」, sim={s:.2f}",
                    "firewall_hit": "无(规则降级)"}
    return {"judgement": "different", "kind": "goal", "note": f"规则降级：无共同主题词, sim={s:.2f}",
            "firewall_hit": "规则降级"}


def evidence_for(ep: dict) -> list[dict]:
    """证据位置：取每条评论的时间戳（真实 trace 位置）。"""
    out = []
    for c in (ep.get("comments") or [])[:3]:
        out.append({"episode": ep.get("episode_id"), "loc": f"comment@{c.get('ts')}",
                    "agent": c.get("agent"), "preview": (c.get("text") or "")[:120]})
    if not out:
        out.append({"episode": ep.get("episode_id"), "loc": "description.md"})
    return out


def union_find_clusters(edges: list[tuple[int, int]]) -> list[list[int]]:
    """通过复核的配对 → 连通分量。"""
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for a, b in edges:
        union(a, b)
    groups: dict[int, list[int]] = {}
    for i in list(parent):
        groups.setdefault(find(i), []).append(i)
    return sorted((sorted(v) for v in groups.values()), key=lambda x: x[0])


def run(args) -> int:
    eps = load_high_episodes(args.episodes)
    print(f"高分 episode: {len(eps)} 条 -> {[e['episode_id'] for e in eps]}")

    shape = (eps[0].get("shape") or "") if eps else ""
    threshold = resolve_threshold(args.threshold, shape)
    corpus = [build_corpus(e) for e in eps]
    pairs = embed.pairs_above(corpus, threshold)
    print(f"向量粗筛配对（阈值 {threshold}）: {len(pairs)} 对")
    # LLM 复核成本 O(对数)：大窗口下截断到相似度最高的 K 对（pairs 已按 sim 降序）
    max_pairs = int(os.environ.get("SKILLTROVE_MAX_REVIEW_PAIRS", "60") or 60)
    if len(pairs) > max_pairs:
        print(f"粗筛 {len(pairs)} 对超过复核上限 {max_pairs}，取相似度最高 {max_pairs} 对复核"
              f"（SKILLTROVE_MAX_REVIEW_PAIRS 可调）")
        pairs = pairs[:max_pairs]
    for i, j, s in pairs:
        print(f"  {eps[i]['episode_id']} - {eps[j]['episode_id']} sim={s}")

    if not pairs:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
        out = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "high_episodes": len(eps), "candidates": [],
               "no_candidate": {"status": "ran_and_found_none",
                                "note": "跑了，向量粗筛无 ≥ 阈值配对（区别于「没跑」）"}}
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print(f"无候选 -> {args.out}")
        return 0

    # 逐对复核（method 随对存，候选 review_method 取代表对的方式，避免循环残留）
    accepted: list[tuple[int, int, dict, float, str]] = []
    for i, j, s in pairs:
        r = llm.call(review_prompt(eps[i], eps[j]), system=REVIEW_SYSTEM, backend=args.llm_backend)
        if r["ok"]:
            review = parse_review(r["text"])
            method = f"llm:{r['backend']}"
            if not review:
                review = rule_fallback(eps[i], eps[j])
                review["llm_empty"] = True
                method += "+rule-fallback(LLM输出不可解析)"
        else:
            review = rule_fallback(eps[i], eps[j])
            method = f"rule-fallback({r['error'][:60]})"
        judgement = review.get("judgement", "different")
        print(f"  复核 {eps[i]['episode_id']}-{eps[j]['episode_id']}: {judgement} [{method}] note={review.get('note','')[:60]}")
        if judgement == "same":
            accepted.append((i, j, review, s, method))

    # 合并候选簇
    clusters = union_find_clusters([(i, j) for i, j, _, _, _ in accepted])
    candidates = []
    for cluster in clusters:
        members = [eps[k] for k in cluster]
        agents = []
        for m in members:
            for a in (m.get("agent_ids") or []):
                if a not in agents:
                    agents.append(a)
        # 取簇内首对复核结论作为代表（review 与 review_method 同源，均为 first_pair）
        first_pair = next(((i, j, r, s, m) for i, j, r, s, m in accepted if i in cluster and j in cluster), None)
        candidates.append({
            "candidate_id": f"cand-{len(candidates) + 1:03d}",
            "kind": (first_pair[2].get("kind") if first_pair else "goal"),
            "episode_ids": [m["episode_id"] for m in members],
            "episodes": [{k: m.get(k) for k in ("episode_id", "issue_key", "goal", "main_agent", "status")} for m in members],
            "contributors": {"distinct_agents": len(agents), "agents": agents},
            "similarity": (first_pair[3] if first_pair else None),
            "review": (first_pair[2] if first_pair else {}),
            "evidence": [ev for m in members for ev in evidence_for(m)],
            "review_method": (first_pair[4] if first_pair else ""),
        })

    out = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "high_episodes": len(eps),
        "pairs_above_threshold": len(pairs),
        "candidates": candidates,
        "no_candidate": {"status": "found" if candidates else "none_accepted",
                         "note": f"{len(pairs)} 对粗筛, {len(accepted)} 对通过复核"} if not candidates else None,
    }
    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\ncandidates.json -> {args.out} ({len(candidates)} 个候选簇)")
    for c in candidates:
        print(f"  {c['candidate_id']} [{c['kind']}] {c['episode_ids']} contributors={c['contributors']}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="F2 聚类")
    p.add_argument("--episodes", required=True, help="scored.jsonl 路径")
    p.add_argument("--out", default="data/candidates.json")
    p.add_argument("--threshold", type=float, default=0.5, help="向量粗筛相似度阈值")
    p.add_argument("--llm-backend", default=None, help="LLM 后端（默认 claude）")
    raise SystemExit(run(p.parse_args()))
