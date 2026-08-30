"""F3 草稿生成：候选簇 → 决策门 10 条 → SKILL.md + CogEval Case（Spec §4.4）。

产物：skills/<name>/
  ├── SKILL.md          # frontmatter 白名单(name/description/when_to_use) + 正文五段
  ├── references/       # 证据索引 + 审核单占位
  └── evals/cases/<name>-001/  # config.yaml + judge.py + fixture（本地可执行）
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cli import llm, registry  # noqa: E402

DECISION_GATES = [
    "复用价值：该做法是否可复现执行、有明确复用价值（非一次性事件）？",
    "团队信号：参与 agent 数（distinct）是否 >= 2？",
    "证据充分：是否 >= 2 个真实 episode 且可下钻到 trace 位置？",
    "安全性：是否不含敏感信息 / prompt 注入 / 危险副作用？",
    "命名冲突：name 是否与 registry 现有 skill 冲突（需传入 registry 名列表）？",
    "目标清晰：经验目标是否清晰单一、不混杂多个任务？",
    "可复现性：procedure 级可复现执行，或 goal 级可作为参考模板？",
    "语义重复：是否与库内已有 skill 重复（语义层面）？",
    "出口选择：转规则 / 转文档 / 丢弃是否比 skill 更合适？",
    "可验收性：期望产出与验收边界是否明确、审核人可核对？",
]

GATE_SYSTEM = (
    "你是 SkillTrove 的 skill 价值决策门。对候选经验逐条回答 10 个问题，"
    "输出纯 JSON，不要额外文字。"
)

SKILL_SYSTEM = (
    "你是 SkillTrove 的 skill 起草者。基于真实 agent 执行经验撰写 SKILL.md。"
    "严格遵守格式要求，输出完整 SKILL.md（含 frontmatter），不要额外文字。"
)


def load_candidates(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def select_candidate(data: dict, candidate_id: str | None) -> dict | None:
    cands = data.get("candidates", [])
    if not cands:
        return None
    if candidate_id:
        for c in cands:
            if c["candidate_id"] == candidate_id:
                return c
        raise SystemExit(f"候选不存在: {candidate_id}")
    return cands[0]


def _candidate_context(cand: dict) -> str:
    parts = []
    for ep in cand.get("episodes", []):
        parts.append(
            f"- [{ep.get('episode_id')}] {ep.get('goal')} (agent={ep.get('main_agent')}, 状态={ep.get('status')})"
        )
    ev = cand.get("evidence", [])
    ev_lines = "\n".join(f"  - {e.get('episode')} @ {e.get('loc')}: {e.get('preview', '')[:100]}" for e in ev[:6])
    return (
        f"候选编号: {cand.get('candidate_id')}\n"
        f"类型: {cand.get('kind')}\n"
        f"贡献者: distinct={cand.get('contributors', {}).get('distinct_agents')} "
        f"agents={cand.get('contributors', {}).get('agents')}\n"
        f"向量相似度: {cand.get('similarity')}\n"
        f"复核结论: {json.dumps(cand.get('review', {}), ensure_ascii=False)}\n"
        f"构成 episode:\n" + "\n".join(parts) + "\n"
        f"证据位置:\n{ev_lines}"
    )


def gate_prompt(cand: dict, registry_names: list[str]) -> str:
    return (
        "以下是待评估的经验候选：\n\n" + _candidate_context(cand) + "\n\n"
        f"registry 现有 skill 名: {registry_names or '(空)'}\n\n"
        "请逐条判断以下 10 个问题（每个给出 pass: true/false 与一句 note）：\n"
        + "\n".join(f"{i}. {g}" for i, g in enumerate(DECISION_GATES, 1)) + "\n\n"
        '输出 JSON: {"pass": bool(总体是否进入草稿), "needs_more_evidence": bool, '
        '"gates": [{"n": 1, "pass": true, "note": "..."}], "reason": "一句话总评"}'
    )


def gate_fallback(cand: dict) -> dict:
    """LLM 不可用降级：distinct>=2 且 evidence>=2 即过。"""
    distinct = cand.get("contributors", {}).get("distinct_agents", 0)
    n_ev = len(cand.get("evidence", []))
    ok = distinct >= 2 and n_ev >= 2
    return {"pass": ok, "needs_more_evidence": not ok,
            "gates": [{"n": 1, "pass": ok, "note": "规则降级"},
                      {"n": 2, "pass": distinct >= 2, "note": f"distinct={distinct}"},
                      {"n": 3, "pass": n_ev >= 2, "note": f"evidence={n_ev}"}],
            "reason": "规则降级判定"}


def parse_json(text: str) -> dict:
    text = text or ""
    s, e = text.find("{"), text.rfind("}")
    if s >= 0 and e > s:
        text = text[s:e + 1]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {}


def draft_prompt(cand: dict) -> str:
    ep_texts = []
    for ep in cand.get("episodes", []):
        ep_texts.append(f"[{ep.get('episode_id')}] {ep.get('goal')} — {ep.get('main_agent')}")
    return (
        "基于以下真实经验候选撰写 SKILL.md（中文为主）：\n\n"
        + _candidate_context(cand) + "\n\n"
        "格式硬性要求：\n"
        "1. frontmatter（YAML，键白名单仅三个）：name / description / when_to_use\n"
        "   - name: 英文小写短横线 slug（如 conduct-research）\n"
        "   - description: 一句话说明\n"
        "   - when_to_use: 何时使用（snake_case 描述）\n"
        "2. 正文五段：## What it does / ## When to use / ## Steps / ## Examples / ## Going deeper\n"
        "3. Steps 必须引用证据位置（如 [WIKI-4 comment@2026-08-28T09:17:56Z]）\n"
        "4. Examples 用候选中的真实做法；Going deeper 给延伸\n\n"
        "直接输出 SKILL.md 全文。"
    )


def _strip_leading_bullet(text: str) -> str:
    """去掉首行的列表符号前缀（kimi 输出 "• ---" 的项目符号行）。"""
    lines = text.strip().splitlines()
    if lines:
        first = lines[0].lstrip()
        if first.startswith(("•", "-", "*")) and not first.startswith("---"):
            lines[0] = first[1:].lstrip()
    return "\n".join(lines).strip()


def parse_skill_md(text: str) -> str:
    """提取 markdown 围栏内容；容忍未闭合的围栏（LLM 偶发输出只开不闭）。

    两种路径都去掉前导列表符号（LLM 输出方差，如 kimi 的 "• ---"）。
    """
    m = re.search(r"```(?:markdown|md)?\s*\n(.*)", text, re.S)
    if m:
        content = m.group(1)
        idx = content.rfind("```")  # 若有闭合围栏，截断到它
        if idx >= 0:
            content = content[:idx]
        return _strip_leading_bullet(content)
    return _strip_leading_bullet(text)


def validate_frontmatter(skill_md: str) -> tuple[dict, list[str]]:
    """解析 frontmatter 并校验白名单。返回 (frontmatter, 错误列表)。

    行基容错解析：容忍 LLM 输出的前导噪声（如 "• ---" 项目符号、缩进键）。
    """
    errors = []
    fm: dict = {}
    lines = skill_md.splitlines()
    start = None
    for idx, line in enumerate(lines[:20]):
        s = line.strip()
        # 容忍前导列表符号（kimi 输出 "• ---"）——s[:1] 是符号时剥掉再比
        if s == "---" or (s[:1] in "•-*" and s[1:].strip() == "---"):
            start = idx
            break
    if start is None:
        return {}, ["缺少 frontmatter（--- 包裹的 YAML）"]
    for line in lines[start + 1:]:
        s = line.strip()
        if s == "---":
            break
        if ":" in s:
            k, v = s.split(":", 1)
            fm[k.strip()] = v.strip()
    allowed = {"name", "description", "when_to_use"}
    for k in fm:
        if k not in allowed:
            errors.append(f"frontmatter 白名单外字段: {k}（将不生效，贡献者/版本请放 registry）")
    if "name" not in fm or not re.match(r"^[a-z0-9][a-z0-9-]*$", fm.get("name", "")):
        errors.append("name 缺失或格式非法（小写 slug）")
    for k in ("description", "when_to_use"):
        if k not in fm:
            errors.append(f"frontmatter 缺 {k}")
    return fm, errors


def write_cogeval_case(skill_dir: str, skill_name: str, cand: dict) -> None:
    case_dir = os.path.join(skill_dir, "evals", "cases", f"{skill_name}-001")
    os.makedirs(os.path.join(case_dir, "fixture"), exist_ok=True)

    with open(os.path.join(case_dir, "config.yaml"), "w", encoding="utf-8") as f:
        f.write(f"case_id: {skill_name}-001\n")
        f.write(f"skill: {skill_name}\n")
        f.write("judge: judge.py\n")
        f.write(f"description: 验证 {skill_name} 在调用 run 中被引述并产生对应产物\n")
        f.write("fixture: fixture/\n")

    with open(os.path.join(case_dir, "judge.py"), "w", encoding="utf-8") as f:
        f.write(JUDGE_TEMPLATE)

    fixture = {
        "source_candidates": cand.get("candidate_id"),
        "source_episodes": cand.get("episode_ids"),
        "evidence": cand.get("evidence", []),
        "note": "fixture = 真实 trace 证据摘录；judge.py 检查调用 run 的 result.json",
    }
    with open(os.path.join(case_dir, "fixture", "evidence.json"), "w", encoding="utf-8") as f:
        json.dump(fixture, f, ensure_ascii=False, indent=2)


JUDGE_TEMPLATE = '''"""本地可执行判定（Spec §4.4 / A6）。

用法: python judge.py <result.json>
result.json = 调用 run 的留痕（F6.2 四元组 + applied 标记 + 产物清单）
退出码 0 = pass；1 = fail。stdout 输出 measured_delta。
"""
import json
import sys


def main() -> int:
    result_path = sys.argv[1] if len(sys.argv) > 1 else "result.json"
    with open(result_path, encoding="utf-8") as f:
        r = json.load(f)

    skill_id = r.get("skill_id", "")
    applied = r.get("applied", False)
    agent_id = r.get("agent_id", "")
    products = r.get("products", [])

    step_ref = r.get("steps_followed", [])
    passed = bool(skill_id and applied and agent_id) and len(products) >= 1

    delta = {
        "skill_id": skill_id,
        "applied": applied,
        "products_count": len(products),
        "steps_followed_count": len(step_ref),
        "judgement": "pass" if passed else "fail",
    }
    print(json.dumps({"measured_delta": delta}, ensure_ascii=False))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
'''


def run(args) -> int:
    data = load_candidates(args.candidates)
    cand = select_candidate(data, args.candidate)
    if cand is None:
        print("无候选（candidates.json 为空或没有候选簇），跳过草稿")
        return 0

    # registry 现有 skill 名（命名冲突门）
    registry_names: list[str] = []
    if os.path.isfile(args.registry):
        with open(args.registry, encoding="utf-8") as f:
            reg = json.load(f)
        registry_names = [s.get("name") for s in reg.get("skills", [])]

    print(f"候选: {cand['candidate_id']} [{cand['kind']}] {cand['episode_ids']}")

    # 决策门
    r = llm.call(gate_prompt(cand, registry_names), system=GATE_SYSTEM, backend=args.llm_backend)
    gate = parse_json(r["text"]) if r["ok"] else {}
    if not gate:
        gate = gate_fallback(cand)
        gate["llm_fallback"] = not r["ok"]
    print(f"决策门: pass={gate.get('pass')} needs_more_evidence={gate.get('needs_more_evidence')} "
          f"reason={str(gate.get('reason'))[:60]}")

    if gate.get("needs_more_evidence") and not gate.get("pass"):
        print("决策门未过（needs-more-evidence），不产草稿")
        return 1
    if not gate.get("pass"):
        print(f"决策门打回: {gate.get('reason')}")
        return 1

    # 草稿生成
    r2 = llm.call(draft_prompt(cand), system=SKILL_SYSTEM, backend=args.llm_backend)
    if not r2["ok"]:
        print(f"LLM 草稿生成失败: {r2['error'][:100]}")
        return 1
    skill_md = parse_skill_md(r2["text"])
    fm, errors = validate_frontmatter(skill_md)
    # 结构性错误（缺 frontmatter / name 缺失或非法 / 缺 description / 缺 when_to_use）
    # 阻断写盘；「白名单外字段」仅为提示（多余键会被忽略，不阻断）
    blocking = [e for e in errors if not e.startswith("frontmatter 白名单外字段")]
    print(f"frontmatter 校验: {len(errors)} 个问题" + ("（阻断）" if blocking else ""))
    for e in errors:
        print(f"  ! {e}")
    if blocking:
        print("草稿中止：frontmatter 不完整，拒绝写盘（name/description/when_to_use 三键齐全才可入草稿）")
        return 1

    skill_name = args.name or fm.get("name") or "unnamed-skill"
    # --name 强制覆盖时同步重写 frontmatter 的 name，保持目录名与 SKILL.md 一致
    if args.name and args.name != fm.get("name"):
        skill_md = re.sub(r"(?m)^name:\s*.*$", f"name: {args.name}", skill_md, count=1)
    # 路径白名单：skill 名会被拼进 skills/<name>/ 路径，非法名（含路径穿越）拒绝写盘
    name_errors = registry.validate_skill_name(skill_name)
    if name_errors:
        for e in name_errors:
            print(f"错误: {e}")
        print("草稿中止：skill 名不合法，拒绝写盘（请修正 LLM 生成的 frontmatter name 或 --name）")
        return 1
    skill_dir = os.path.join(args.out, skill_name)
    os.makedirs(os.path.join(skill_dir, "references"), exist_ok=True)

    with open(os.path.join(skill_dir, "SKILL.md"), "w", encoding="utf-8") as f:
        f.write(skill_md + "\n")

    write_cogeval_case(skill_dir, skill_name, cand)

    # references：证据索引 + 审核单占位
    ref = {
        "candidate": cand.get("candidate_id"),
        "episodes": cand.get("episode_ids"),
        "contributors": cand.get("contributors"),
        "evidence": cand.get("evidence"),
        "gate": gate,
        "review_notes": "（人工审核后填写：五要素 + 证据态 + 通过/打回理由）",
    }
    with open(os.path.join(skill_dir, "references", "evidence-index.json"), "w", encoding="utf-8") as f:
        json.dump(ref, f, ensure_ascii=False, indent=2)
    with open(os.path.join(skill_dir, "references", "review-notes.md"), "w", encoding="utf-8") as f:
        f.write(f"# {skill_name} 审核单\n\n"
                f"- 候选: {cand['candidate_id']} | episodes: {cand['episode_ids']}\n"
                f"- 贡献者: distinct={cand['contributors']['distinct_agents']} {cand['contributors']['agents']}\n"
                f"- 五要素: 重要性/证据/期望产出/范围/验收 → 待填写\n"
                f"- 证据态: 待人工确认\n")

    print(f"skill 草稿 -> {skill_dir}")
    print(f"  SKILL.md ({len(skill_md)} 字符), evals/cases/{skill_name}-001/, references/")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="F3 草稿生成")
    p.add_argument("--candidates", required=True, help="candidates.json 路径")
    p.add_argument("--candidate", default=None, help="候选 ID（默认取第一个）")
    p.add_argument("--out", default="skills", help="输出根目录（默认 skills/）")
    p.add_argument("--registry", default="registry/registry.json")
    p.add_argument("--name", default=None, help="强制 skill 名（覆盖 LLM 生成）")
    p.add_argument("--llm-backend", default=None)
    raise SystemExit(run(p.parse_args()))
