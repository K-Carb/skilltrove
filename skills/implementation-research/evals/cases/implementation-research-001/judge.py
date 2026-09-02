"""implementation-research-001 的本地判定（Spec §4.4 / A6）。

用法: python judge.py <result.json>
  result.json = 调用 run 的留痕（F6.2 四元组 + applied 标记 + 产物清单），
                位于 data/recall-runs/<run_id>/result.json

判定分两层：
  阻断项（blocking）——调用留痕的结构完整性，任一项不通过即 fail；
  内容项（content）——对照 config.yaml 的 description，检查真实产出里是否
    含「候选清单 / 对比 / 推荐结论」三要素。全部通过才 pass，缺一项即 fail，
    但每项结果都如实写进 measured_delta，供人工判断是技能问题还是措辞差异。

退出码 0 = pass；1 = fail。stdout 输出 JSON 行的 measured_delta。
"""

import json
import os
import re
import sys

# 内容三要素的判据（启发式：只看产出是否具备该形态，不评判质量）
CANDIDATE_HINTS = ("方案", "候选", "路线", "选项", "做法", "实现")
COMPARE_HINTS = ("对比", "比较", "取舍", "优劣", "维度", "vs", "VS")
CONCLUSION_HINTS = ("推荐", "建议", "结论", "选择", "选型")
REASON_HINTS = ("因为", "由于", "理由", "原因", "权衡", "考虑", "综合")


def _read_products(run_dir: str, products: list) -> str:
    """拼接所有产物文件的文本（读不到就当空串，交由内容项自然判 fail）。"""
    parts = []
    for rel in products:
        path = os.path.join(run_dir, str(rel).replace("/", os.sep))
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8", errors="replace") as f:
            parts.append(f.read())
    return "\n".join(parts)


def _has_any(text: str, hints: tuple) -> bool:
    return any(h in text for h in hints)


def _count_candidates(text: str) -> int:
    """候选方案计数：编号列表项 + 候选类关键词的出现次数（取较可信的一个）。"""
    numbered = len(re.findall(r"(?m)^\s*(?:\d+[.)、]|[-*])\s+\S", text))
    keyword = sum(text.count(h) for h in CANDIDATE_HINTS)
    return max(numbered, keyword)


def main() -> int:
    result_path = sys.argv[1] if len(sys.argv) > 1 else "result.json"
    with open(result_path, encoding="utf-8") as f:
        r = json.load(f)

    run_dir = os.path.dirname(os.path.abspath(result_path))
    skill_id = r.get("skill_id", "")
    applied = bool(r.get("applied"))
    agent_id = r.get("agent_id", "")
    products = r.get("products") or []

    blocking = {
        "skill_id 非空": bool(skill_id),
        "applied 为真": applied,
        "agent_id 非空": bool(agent_id),
        "至少一件产物": len(products) >= 1,
    }

    text = _read_products(run_dir, products)
    has_table = len(re.findall(r"(?m)^\s*\|.*\|\s*$", text)) >= 2
    content = {
        "产出含候选清单": _count_candidates(text) >= 2,
        "产出含对比": has_table or _has_any(text, COMPARE_HINTS),
        "产出含带理由的推荐结论": (_has_any(text, CONCLUSION_HINTS)
                                    and _has_any(text, REASON_HINTS)),
    }

    blocking_ok = all(blocking.values())
    content_ok = all(content.values())
    passed = blocking_ok and content_ok

    delta = {
        "case_id": "implementation-research-001",
        "run_id": r.get("run_id"),
        "skill_id": skill_id,
        "agent_id": agent_id,
        "applied": applied,
        "products_count": len(products),
        "steps_followed_count": len(r.get("steps_followed") or []),
        "blocking": blocking,
        "content": content,
        "blocking_pass": blocking_ok,
        "content_pass": content_ok,
        "content_pass_count": f"{sum(1 for v in content.values() if v)}/{len(content)}",
        "judgement": "pass" if passed else "fail",
    }
    print(json.dumps({"measured_delta": delta}, ensure_ascii=False))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
