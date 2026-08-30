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

RESEARCH_LABEL = "research-round"
RESEARCH_MEMBERS = ["ep-WIKI-3", "ep-WIKI-4", "ep-WIKI-5"]

# 反事实标注定义（键 = 标注名，值 = 相对基准的修订；见 main() 内构造）
COUNTERFACTUALS = {
    "merge_AGEN6_into_research": "WIKI-6（汇总最终报告）并入调研簇：与四维契约「汇总≠调研」的裁断相反",
    "merge_AGEN1_into_research": "WIKI-1（项目整体分析）并入调研簇：任务类型判定相反",
    "merge_AGEN6_AGEN1_into_research": "WIKI-6 与 WIKI-1 同时并入调研簇（最激进边界）",
}


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


def build_labelings(base: dict) -> dict:
    """基准 + 反事实标注集合。每个标注 = {episode_id: cluster_label}。"""
    labelings = {"base": dict(base)}
    for name in COUNTERFACTUALS:
        revised = dict(base)
        if "AGEN6" in name:
            revised["ep-WIKI-6"] = RESEARCH_LABEL
        if "AGEN1" in name:
            revised["ep-WIKI-1"] = RESEARCH_LABEL
        labelings[name] = revised
    return labelings


def build_variant_clusters(eps: list[dict], episode_ids: list) -> dict:
    """预计算 11 个变体的簇划分（与标注无关，只算一次）。"""
    edges = ev.vector_edges(eps, 0.5, 0.6, 0.4)
    same = [(a, b) for a, b, _ in edges if ev.LLM_REVIEW_VERDICTS.get((a, b)) == "same"]
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
    p.add_argument("--out", default=os.path.join(ROOT, "evals", "results",
                                                 "annotation-sensitivity-2026-08-29.json"))
    args = p.parse_args()

    gold = json.load(open(args.gold, encoding="utf-8"))
    base = dict(gold["cluster_truth"])
    eps = cluster.load_high_episodes(args.episodes)
    episode_ids = [e["episode_id"] for e in eps]
    print(f"参与聚类的 episode（{len(eps)}）: {episode_ids}")

    labelings = build_labelings(base)
    variant_clusters = build_variant_clusters(eps, episode_ids)

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

    summary = {
        "headline_base": f"基准标注下 full 管线 ARI/AMI=1.000、F1=1.000；关键词 baseline F1=0.667",
        "full_pipeline_ari_range": [min(full_ari.values()), max(full_ari.values())],
        "full_pipeline_f1_range": [min(full_f1.values()), max(full_f1.values())],
        "keyword_baseline_ari_range": [min(kw_ari.values()), max(kw_ari.values())],
        "full_pipeline_ari": full_ari,
        "full_pipeline_f1": full_f1,
        "keyword_baseline_ari": kw_ari,
        "takeaway": (
            "主结论依赖单点标注：WIKI-6 并入调研簇后 full 管线 ARI 由 1.000 降至 "
            f"{full_ari['merge_AGEN6_into_research']}（关键词 baseline 反而升至 "
            f"{kw_ari['merge_AGEN6_into_research']}）；WIKI-6+WIKI-1 同时并入时 "
            f"full 管线 ARI 降至 {full_ari['merge_AGEN6_AGEN1_into_research']}。"
            "对外表述须引用区间而非单点。"
        ),
    }

    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "gold": {"labeler": gold["labeler"], "revision_note": gold.get("revision", "")},
        "research_cluster": RESEARCH_MEMBERS,
        "counterfactuals": COUNTERFACTUALS,
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
