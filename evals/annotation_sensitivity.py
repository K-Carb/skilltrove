"""标注敏感性分析：主结论对人工真值单点裁断的依赖验证（可复现）。

背景：算法四件套评估（evals/evaluate_pipeline.py）的主结论「现状管线
ARI=AMI=1.000」基于人工真值 evals/gold-truth.json。该真值由单一标注者
（dave）按四维契约标注，其中存在可争议的单点裁断——例如 WIKI-6（汇总
最终报告）是否属于「调研轮次」簇、WIKI-1（项目整体分析）是否并入。
本脚本对每个可疑边界做反事实重算，把「ARI=1.0」扩展为真实可信度区间，
供对外表述时如实引用。

方法：
  1. 读 gold-truth.json 的 cluster_truth 作为基准标注（base）；
  2. 构造反事实标注（WIKI-6 并入调研簇 / WIKI-1 并入调研簇 / 两者都并入）；
  3. 各管线变体的簇划分只依赖向量 + LLM 判定记录，与标注无关，
     故先一次性预计算全部 11 个变体的簇，再对每种标注重算指标
     （ARI / AMI / 配对 P/R/F1 / 簇数）；
  4. 输出全表 JSON 到 evals/results/annotation-sensitivity-2026-08-29.json。

用法：
  python evals/annotation_sensitivity.py
      [--episodes archive/scored.jsonl] [--gold evals/gold-truth.json]
      [--out evals/results/annotation-sensitivity-2026-08-29.json]

纯标准库、零 LLM、零第三方依赖；全部数字由脚本独立计算，可复现。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # evals/（导入 evaluate_pipeline）

from cli import cluster  # noqa: E402
import evaluate_pipeline as ev  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

def derive_pairs(labels: dict) -> set:
    """从簇标签推导同类对（同一标签内两两组合）。"""
    by_label: dict = {}
    for eid, lab in labels.items():
        by_label.setdefault(lab, []).append(eid)
    pairs = set()
    for members in by_label.values():
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                pairs.add((members[i], members[j]))
    return pairs


def build_labelings(base: dict, episode_ids: list) -> tuple[dict, dict]:
    """基准 + 反事实标注集合，返回 (标注集合, 说明集合)。

    反事实一律用「把某个边界 episode 从所在簇剥离成独类」构造，不硬编码编号。
    旧实现硬编码了 WIKI-1 / WIKI-6 两个目标和一个簇标签名 'research-round'，
    与 gold-truth 的实际标签（如 implementation-research）不一致、目标 episode
    也常常不在参与聚类的范围内——结果是「并入」退化成「新建一个不相干的簇」，
    敏感性分析静默失效，却仍输出一个看起来稳健的区间。已改为数据驱动。

    每个标注都覆盖全部 episode_ids（缺失的补自身 id 作独类），避免下游
    `truth_labels[eid]` 抛 KeyError。
    """
    canonical = {eid: base.get(eid, eid) for eid in episode_ids}
    labelings: dict[str, dict] = {"base": dict(canonical)}
    notes: dict[str, str] = {"base": "基准标注（gold-truth 原文）"}

    members_by_label: dict[str, list] = {}
    for eid, lab in canonical.items():
        members_by_label.setdefault(lab, []).append(eid)

    for eid in episode_ids:
        lab = canonical[eid]
        partners = [x for x in members_by_label.get(lab, []) if x != eid]
        if not partners:
            continue  # 独类 episode 再剥离没有意义
        revised = dict(canonical)
        revised[eid] = f"detached:{eid}"
        name = f"detach_{eid}"
        labelings[name] = revised
        notes[name] = (f"{eid} 从「{lab}」簇剥离成独类："
                       f"原簇另有 {len(partners)} 个成员（{'、'.join(partners)}）")
    return labelings, notes


def build_variant_clusters(eps: list[dict], episode_ids: list, verdicts: dict) -> dict:
    """预计算 11 个变体的簇划分（与标注无关，只算一次）。"""
    edges = ev.vector_edges(eps, 0.5, 0.6, 0.4)
    same = [(a, b) for a, b, _ in edges if verdicts.get(tuple(sorted((a, b)))) == "same"]
    rule_same = [(a, b) for a, b, _ in edges
                 if cluster.rule_fallback(eps[episode_ids.index(a)], eps[episode_ids.index(b)])["judgement"] == "same"]
    kw_edges = ev.keyword_edges(eps, episode_ids)

    def _c(same_edges: list) -> list:
        return ev.clusters_from_edges(episode_ids, [(a, b) for a, b in same_edges])

    return {
        "full_pipeline_llm": _c(same),
        "rule_fallback_pipeline": _c(rule_same),
        "vector_only_050_mixed": _c([(a, b) for a, b, _ in edges]),
        "vector_only_040_mixed": _c([(a, b) for a, b, _ in ev.vector_edges(eps, 0.4, 0.6, 0.4)]),
        "vector_only_060_mixed": _c([(a, b) for a, b, _ in ev.vector_edges(eps, 0.6, 0.6, 0.4)]),
        "vector_only_050_bigram_only": _c([(a, b) for a, b, _ in ev.vector_edges(eps, 0.5, 1.0, 0.0)]),
        "vector_only_050_trigram_only": _c([(a, b) for a, b, _ in ev.vector_edges(eps, 0.5, 0.0, 1.0)]),
        "vector_only_050_equal": _c([(a, b) for a, b, _ in ev.vector_edges(eps, 0.5, 0.5, 0.5)]),
        "baseline_keyword_only": _c(kw_edges),
        "baseline_all_diff": [[e] for e in episode_ids],
        "baseline_all_same": [list(episode_ids)],
    }


VARIANT_ORDER = (
    "full_pipeline_llm", "rule_fallback_pipeline",
    "vector_only_050_mixed", "vector_only_040_mixed", "vector_only_060_mixed",
    "vector_only_050_bigram_only", "vector_only_050_trigram_only", "vector_only_050_equal",
    "baseline_keyword_only", "baseline_all_diff", "baseline_all_same",
)


def _row_compact(row: dict) -> dict:
    """run_variant 行 → 紧凑指标（含簇划分，保留可复现性）。"""
    return {
        "n_clusters": row["n_clusters"],
        "ari": row["ari"],
        "ami": row["ami"],
        "pair_prf": row["pair_prf"],
        "false_positive_pairs": row["false_positive_pairs"],
        "false_negative_pairs": row["false_negative_pairs"],
        "clusters": row["clusters"],
    }


def main() -> int:
    p = argparse.ArgumentParser(description="标注敏感性分析（零 LLM，纯标准库）")
    p.add_argument("--episodes", default=os.path.join(ROOT, "archive", "scored.jsonl"))
    p.add_argument("--gold", default=os.path.join(ROOT, "evals", "gold-truth.json"))
    p.add_argument("--candidates", default=ev.DEFAULT_CANDIDATES,
                   help="聚类候选产物（LLM 复核判定的事实源）")
    p.add_argument("--out", default=os.path.join(ROOT, "evals", "results",
                                                 "annotation-sensitivity-2026-08-29.json"))
    args = p.parse_args()

    gold = json.load(open(args.gold, encoding="utf-8"))
    base = dict(gold["cluster_truth"])
    eps = cluster.load_high_episodes(args.episodes)
    episode_ids = [e["episode_id"] for e in eps]
    print(f"参与聚类的 episode（{len(eps)}）: {episode_ids}")

    verdicts = ev.verdicts_from_candidates(args.candidates)
    if not verdicts:
        print(f"[警告] 无复核判定来源（{args.candidates} 缺失或无候选），"
              f"full_pipeline_llm 将退化为「无任何 same 边」，ARI 不代表真实管线表现")

    labelings, notes = build_labelings(base, episode_ids)
    variant_clusters = build_variant_clusters(eps, episode_ids, verdicts)

    # 每种标注下重算全表
    per_labeling = {}
    for lname, labels in labelings.items():
        truth_pairs = derive_pairs(labels)
        rows = {}
        for vname in VARIANT_ORDER:
            rows[vname] = _row_compact(ev.run_variant(vname, variant_clusters[vname],
                                                      labels, truth_pairs, episode_ids))
        per_labeling[lname] = {
            "n_truth_clusters": len(set(labels.values())),
            "n_truth_same_pairs": len(truth_pairs),
            "truth_same_pairs": sorted(truth_pairs),
            "variants": rows,
        }

    # 主结论区间（full 管线 vs 关键词 baseline 的 ARI）
    full_ari = {lname: v["variants"]["full_pipeline_llm"]["ari"] for lname, v in per_labeling.items()}
    full_f1 = {lname: v["variants"]["full_pipeline_llm"]["pair_prf"]["f1"] for lname, v in per_labeling.items()}
    kw_ari = {lname: v["variants"]["baseline_keyword_only"]["ari"] for lname, v in per_labeling.items()}

    base_ari = full_ari["base"]
    # 影响最大的反事实（按 full 管线 ARI 相对基准的偏移幅度取绝对值最大者）
    deltas = {k: full_ari[k] - base_ari for k in full_ari if k != "base"}
    worst = max(deltas, key=lambda k: abs(deltas[k])) if deltas else None

    if worst is None:
        takeaway = ("参与聚类的 episode 均为独类，无可做的边界反事实；"
                    "主结论区间等于单点，不代表已验证稳健。")
    elif deltas[worst] == 0:
        takeaway = (f"全部 {len(deltas)} 个边界反事实下 full 管线 ARI 均无变化（恒为 "
                    f"{base_ari}），本数据集上主结论对单点裁断不敏感。")
    else:
        takeaway = (f"主结论对单点标注敏感：{worst}（{notes[worst]}）使 full 管线 "
                    f"ARI 由 {base_ari} 变为 {full_ari[worst]}"
                    f"（Δ{deltas[worst]:+.4f}），F1 {full_f1['base']} → {full_f1[worst]}。"
                    f"全区间 ARI [{min(full_ari.values())}, {max(full_ari.values())}]、"
                    f"F1 [{min(full_f1.values())}, {max(full_f1.values())}]，"
                    "对外表述须引用区间而非单点。")

    summary = {
        "full_pipeline_ari_range": [min(full_ari.values()), max(full_ari.values())],
        "full_pipeline_f1_range": [min(full_f1.values()), max(full_f1.values())],
        "keyword_baseline_ari_range": [min(kw_ari.values()), max(kw_ari.values())],
        "full_pipeline_ari": full_ari,
        "full_pipeline_f1": full_f1,
        "keyword_baseline_ari": kw_ari,
        "most_sensitive_counterfactual": worst,
        "takeaway": takeaway,
    }

    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "gold": {"labeler": gold["labeler"], "revision_note": gold.get("revision", "")},
        "counterfactuals": notes,
        "labelings": labelings,
        "per_labeling": per_labeling,
        "summary": summary,
    }
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    # stdout 摘要
    print("\n=== 标注敏感性：full 管线 vs 关键词 baseline（ARI / F1）===")
    header = f"{'标注':<32}{'same 对':>6}{'full ARI':>9}{'full F1':>8}{'kw ARI':>8}{'kw F1':>8}"
    print(header)
    for lname, v in per_labeling.items():
        r = v["variants"]
        print(f"{lname:<32}{v['n_truth_same_pairs']:>6}"
              f"{r['full_pipeline_llm']['ari']:>9}{r['full_pipeline_llm']['pair_prf']['f1']:>8}"
              f"{r['baseline_keyword_only']['ari']:>8}{r['baseline_keyword_only']['pair_prf']['f1']:>8}")
    print(f"\nfull 管线 ARI 区间: {summary['full_pipeline_ari_range']}")
    print(f"full 管线 F1  区间: {summary['full_pipeline_f1_range']}")
    print(f"关键词 baseline ARI 区间: {summary['keyword_baseline_ari_range']}")
    print(f"\n结论: {summary['takeaway']}")
    print(f"\n全表 JSON -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
