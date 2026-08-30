"""F1.6 L2 业务结果打分（Spec §4.2，A2/A3 本地化）。

一类规则校验：business_join_key 指向本地可查证记录（issue_status_history）。
一条完成态偏差规则：issue 到达 done/in_review 且存在产出（附件或产出评论）→ 高分。
"""

from __future__ import annotations

import json
import os


def _load_episodes(path: str) -> list[dict]:
    eps = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                eps.append(json.loads(line))
    return eps


def _load_manifest_keys(manifest: str | None) -> set[str]:
    if not manifest or not os.path.isfile(manifest):
        return set()
    with open(manifest, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        data = data.get("issues", data)
    return {i.get("issue", "") for i in data if i.get("issue")}


def _has_output(ep: dict) -> bool:
    """产出判据：输出信号 / 附件非空，或评论含附件引用，或存在产出性长评论（≥500 字符）。

    output_signal 由数据源适配器显式给出（如 issue 已关闭、git commit 带文件、
    会话有产出）——通用来源（table/git/session）的产出判据由此补齐。
    """
    if ep.get("output_signal"):
        return True
    if ep.get("attachments"):
        return True
    for c in ep.get("comments", []):
        text = c.get("text", "")
        if "附件:" in text or "attachment" in text.lower():
            return True
        if len(text) >= 500:  # 产出性内容（评论正文呈现的交付物）
            return True
    return False


def score_episode(ep: dict, manifest_keys: set[str]) -> dict:
    """返回该 episode 的 score 字典（不改写 ep 本体）。

    join 查证：business_join_key 指向任意来源表（table 非空即可），
    id 须在 manifest（归一化任务清单）中存在且有 measured_at。
    """
    jk = ep.get("business_join_key") or {}
    join_verified = bool(
        jk.get("table")
        and jk.get("id")
        and jk.get("id") in manifest_keys
        and jk.get("measured_at")
    )
    status = (ep.get("status") or "").lower()
    done = status in {"done", "in_review"}
    output = _has_output(ep)
    rules = [
        {"rule": "join_verifiable", "pass": join_verified,
         "detail": f"id={jk.get('id')} 可查证" if join_verified else f"id={jk.get('id')} 不可查证"},
        {"rule": "completion_signal", "pass": done and output,
         "detail": f"status={status}, output={'Y' if output else 'N'}"},
    ]
    high = join_verified and done and output
    return {
        "label": "high" if high else "low",
        "join_verified": join_verified,
        "rules": rules,
        "reason": "完成态 + 产出" if high else "未满足完成态/产出/join 查证",
    }


def run(args) -> int:
    eps = _load_episodes(args.episodes)
    manifest_keys = _load_manifest_keys(args.manifest)

    out_path = args.out or os.path.join(os.path.dirname(os.path.abspath(args.episodes)), "scored.jsonl")
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    high_count = 0
    scored_total = 0
    with open(out_path, "w", encoding="utf-8") as f:
        for ep in eps:
            if ep.get("excluded"):
                ep["score"] = {"label": "excluded", "join_verified": False, "rules": [], "reason": "excludeAgents 排除"}
            else:
                ep["score"] = score_episode(ep, manifest_keys)
                scored_total += 1
                if ep["score"]["label"] == "high":
                    high_count += 1
            f.write(json.dumps(ep, ensure_ascii=False) + "\n")

    print(f"scored.jsonl -> {out_path}")
    print(f"共 {len(eps)} 条（excluded {len(eps) - scored_total} 条）, 参与评分 {scored_total} 条, 高分 {high_count} 条, 低分 {scored_total - high_count} 条")
    for ep in eps:
        if ep["score"]["label"] == "high":
            print(f"  HIGH {ep['episode_id']} [{ep['status']}] 规则={[r['rule'] for r in ep['score']['rules']]}")
    return 0


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--episodes", required=True)
    p.add_argument("--manifest", default=None)
    p.add_argument("--out", default=None)
    raise SystemExit(run(p.parse_args()))
