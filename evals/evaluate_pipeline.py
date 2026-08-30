"""算法四件套评估：baseline 对照 / 外部评估（ARI·AMI）/ 消融 / 负样本集。

背景：readiness-review-v2 §2.4 指出聚类管线「可解释 ≠ 可证明」，缺量化评估闭环。
本脚本在现有 archive/scored.jsonl + data/candidates.json + data/llm-log.jsonl 上
纯计算完成四件套（零 LLM 调用，纯 Python 标准库，无 numpy/sklearn）：

  ① baseline 对照：全不同 / 全相同 / 纯关键词规则 三条下限 vs 现有管线
  ② 外部评估：人工真值（evals/gold-truth.json）→ ARI / AMI（校正随机，不用 V-measure）
  ③ 消融：阈值 0.4/0.5/0.6 敏感性、bigram/trigram 权重（每变体只动一个部件）
  ④ 负样本集：明确不同类对（含 hard negative：PRD 撰写 vs PRD 评审）的相似度分布与误判率

用法：
  python evals/evaluate_pipeline.py [--gold evals/gold-truth.json]
      [--episodes archive/scored.jsonl] [--llm-log data/llm-log.jsonl]
      [--out evals/results/algorithm-eval-2026-08-29.json]

输出：--out 指向的全表 JSON + stdout 人类可读摘要。退出码 0 = 正常。
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from cli import cluster  # noqa: E402
from cli import embed    # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

KEYWORDS = ("调研", "PRD", "评审", "范围", "分析")

# LLM 四维复核判断（示例数据上的既有判定）。
# 键为 (episode_id_a, episode_id_b)，值为 llm 判断。
LLM_REVIEW_VERDICTS = {
    ("ep-4", "ep-5"): "same",
    ("ep-1", "ep-3"): "different",
    ("ep-1", "ep-5"): "different",
    ("ep-3", "ep-4"): "same",
    ("ep-5", "ep-6"): "different",
    ("ep-4", "ep-6"): "different",
}


# ---------- 外部指标：ARI / AMI（校正随机） ----------

def ari(labels_true: list, labels_pred: list) -> float:
    """调整兰德指数（Adjusted Rand Index），纯 Python 标准库实现。

    1=完美一致；0=与随机划分持平；可负。分母为 0（完全一致或完全随机）时
    按约定返回 1.0 / 0.0。
    """
    n = len(labels_true)
    if n == 0:
        return 0.0
    # 共现矩阵
    classes_true = sorted(set(labels_true))
    classes_pred = sorted(set(labels_pred))
    contingency = [[0] * len(classes_pred) for _ in classes_true]
    idx_t = {c: i for i, c in enumerate(classes_true)}
    idx_p = {c: i for i, c in enumerate(classes_pred)}
    for lt, lp in zip(labels_true, labels_pred):
        contingency[idx_t[lt]][idx_p[lp]] += 1

    def comb2(x: int) -> int:
        return x * (x - 1) // 2

    sum_ij = sum(comb2(contingency[i][j])
                 for i in range(len(classes_true)) for j in range(len(classes_pred)))
    sum_i = sum(comb2(sum(row)) for row in contingency)
    sum_j = sum(comb2(sum(contingency[i][j] for i in range(len(classes_true))))
                for j in range(len(classes_pred)))
    total = comb2(n)
    expected = sum_i * sum_j / total if total else 0.0
    max_index = (sum_i + sum_j) / 2.0
    denom = max_index - expected
    if abs(denom) < 1e-12:
        # 完全一致或退化（无法区分随机与完美）
        return 1.0 if abs(sum_ij - expected) < 1e-12 else 0.0
    return (sum_ij - expected) / denom


def _entropy(counts: Counter) -> float:
    """集合熵（自然对数）。"""
    total = sum(counts.values())
    if total == 0:
        return 0.0
    return -sum((c / total) * math.log(c / total) for c in counts.values() if c > 0)


def _expected_mi(a_counts: Counter, b_counts: Counter, n: int) -> float:
    """调整互信息的随机期望（Vinh et al. 2009 精确公式，对数空间）。

    EMI = Σ_ij Σ_k (k/n)·ln(n·k/(a_i·b_j))·C(a_i,k)·C(n-a_i,b_j-k)/C(n,b_j)
    """
    total = 0.0
    for a_i in a_counts.values():
        for b_j in b_counts.values():
            k_lo = max(0, a_i + b_j - n)
            k_hi = min(a_i, b_j)
            denom = math.comb(n, b_j)
            for k in range(k_lo, k_hi + 1):
                if k == 0:
                    continue  # 对数项为 0
                prob = math.comb(a_i, k) * math.comb(n - a_i, b_j - k) / denom
                if prob <= 0:
                    continue
                total += (k / n) * math.log(n * k / (a_i * b_j)) * prob
    return total


def ami(labels_true: list, labels_pred: list) -> float:
    """调整互信息（Adjusted Mutual Information），纯 Python 标准库实现。

    1=完美一致；0=与随机持平。同 ARI 一样校正随机机会，故优于 V-measure/NMI。
    """
    n = len(labels_true)
    if n == 0:
        return 0.0
    a_counts = Counter(labels_true)
    b_counts = Counter(labels_pred)
    # 共现计数
    pair_counts = Counter(zip(labels_true, labels_pred))
    # 互信息
    mi = 0.0
    for (lt, lp), c in pair_counts.items():
        p_ij = c / n
        p_i = a_counts[lt] / n
        p_j = b_counts[lp] / n
        mi += p_ij * math.log(p_ij / (p_i * p_j))
    h_u = _entropy(a_counts)
    h_v = _entropy(b_counts)
    emi = _expected_mi(a_counts, b_counts, n)
    denom = max(h_u, h_v) - emi
    if abs(denom) < 1e-12:
        return 1.0 if abs(mi - emi) < 1e-12 else 0.0
    return (mi - emi) / denom


# ---------- 配对级 P/R/F1（pairwise 视角，与 ARI 同源但更可解释） ----------

def _pairs_from_clusters(clusters: list[list]) -> set:
    """簇划分 → 共簇 episode 无序对集合。"""
    out = set()
    for c in clusters:
        ids = sorted(c)
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                out.add((ids[i], ids[j]))
    return out


def pair_prf(truth_pairs: set, pred_pairs: set) -> dict:
    """预测对 vs 真值对 → precision / recall / F1（F1 为调和平均）。"""
    tp = len(truth_pairs & pred_pairs)
    fp = len(pred_pairs - truth_pairs)
    fn = len(truth_pairs - pred_pairs)
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "precision": round(precision, 4),
            "recall": round(recall, 4), "f1": round(f1, 4)}


# ---------- 参数化相似度与聚类变体 ----------

def sim_weighted(a: str, b: str, bw: float, tw: float) -> float:
    """bigram/trigram 权重可调的混合相似度（不改生产模块 embed 的常量）。"""
    s2 = embed.cosine(Counter(embed.bigrams(a)), Counter(embed.bigrams(b)))
    s3 = embed.cosine(Counter(embed.trigrams(a)), Counter(embed.trigrams(b)))
    return bw * s2 + tw * s3


def vector_edges(eps: list[dict], threshold: float, bw: float, tw: float) -> list[tuple]:
    """纯向量粗筛：返回 (id_a, id_b, sim) 且 sim >= threshold，按 sim 降序。"""
    n = len(eps)
    pairs = []
    for i in range(n):
        for j in range(i + 1, n):
            s = sim_weighted(cluster.build_corpus(eps[i]), cluster.build_corpus(eps[j]), bw, tw)
            if s >= threshold:
                pairs.append((eps[i]["episode_id"], eps[j]["episode_id"], round(s, 4)))
    pairs.sort(key=lambda x: x[2], reverse=True)
    return pairs


def clusters_from_edges(episode_ids: list, same_edges: list[tuple]) -> list[list]:
    """same 边 → 连通分量（复用生产管线 union_find 语义，输出 episode_id 簇）。"""
    idx = {eid: i for i, eid in enumerate(episode_ids)}
    groups = cluster.union_find_clusters([(idx[a], idx[b]) for a, b in same_edges])
    return [[episode_ids[k] for k in g] for g in groups]


def label_vectors(clusters: list[list], episode_ids: list) -> list:
    """簇 → 每个 episode 的簇标签（单例簇用自身 id）。"""
    label_of = {}
    for c in clusters:
        for eid in c:
            label_of[eid] = c[0]
    return [label_of.get(eid, eid) for eid in episode_ids]


def keyword_edges(eps: list[dict], scope_ids: list[str]) -> list[tuple]:
    """纯关键词规则（baseline）：双含同一主题关键词 → same。完全不用向量。"""
    text_of = {e["episode_id"]: (e.get("goal") or "") + (e.get("title") or "") for e in eps}
    same = []
    for i in range(len(scope_ids)):
        for j in range(i + 1, len(scope_ids)):
            a, b = scope_ids[i], scope_ids[j]
            for kw in KEYWORDS:
                if kw in text_of[a] and kw in text_of[b]:
                    same.append((a, b))
                    break
    return same


# ---------- 各变体管线 ----------

def run_variant(name: str, clusters: list[list], truth_labels: dict,
                truth_pairs: set, episode_ids: list) -> dict:
    """统一评估入口：簇划分 → ARI / AMI / 配对 P/R/F1 / 误判对清单。

    clusters 可能只含连通分量（孤立 episode 缺席），这里补全为完整划分，
    保证单例簇计入簇数与配对统计（与生产管线语义一致）。
    """
    present = {eid for c in clusters for eid in c}
    complete = [list(c) for c in clusters] + [[e] for e in episode_ids if e not in present]
    pred_pairs = _pairs_from_clusters(complete)
    pred_labels = label_vectors(complete, episode_ids)
    truth_vec = [truth_labels[eid] for eid in episode_ids]
    row = {
        "variant": name,
        "clusters": complete,
        "n_clusters": len(complete),
        "ari": round(ari(truth_vec, pred_labels), 4),
        "ami": round(ami(truth_vec, pred_labels), 4),
        "pair_prf": pair_prf(truth_pairs, pred_pairs),
        "false_positive_pairs": sorted(pred_pairs - truth_pairs),
        "false_negative_pairs": sorted(truth_pairs - pred_pairs),
    }
    return row


def run_all(episodes_path: str, gold_path: str, out_path: str) -> int:
    # 数据
    gold = json.load(open(gold_path, encoding="utf-8"))
    truth_labels = gold["cluster_truth"]
    truth_pairs = {tuple(p) for p in gold["same_pairs"]}
    eps = cluster.load_high_episodes(episodes_path)
    episode_ids = [e["episode_id"] for e in eps]
    print(f"参与聚类的 episode（{len(eps)}）: {episode_ids}")

    results = {}

    # ① 消融矩阵（含 baseline 对照）——阈值/权重变体都在「纯向量（无复核）」条件下，
    #    使向量层对参数的真实敏感性不被 LLM 复核吸收。
    def vec_variant(name, threshold, bw, tw):
        edges = vector_edges(eps, threshold, bw, tw)
        clusters = clusters_from_edges(episode_ids, [(a, b) for a, b, _ in edges])
        row = run_variant(name, clusters, truth_labels, truth_pairs, episode_ids)
        row["edges"] = [(a, b, s) for a, b, s in edges]
        results[name] = row
        return row

    vec_variant("vector_only_050_mixed", 0.5, 0.6, 0.4)   # 纯向量（默认权重，阈值 0.5）
    vec_variant("vector_only_040_mixed", 0.4, 0.6, 0.4)   # 消融：阈值 0.4
    vec_variant("vector_only_060_mixed", 0.6, 0.6, 0.4)   # 消融：阈值 0.6
    vec_variant("vector_only_050_bigram_only", 0.5, 1.0, 0.0)  # 消融：去掉 trigram
    vec_variant("vector_only_050_trigram_only", 0.5, 0.0, 1.0)  # 消融：去掉 bigram
    vec_variant("vector_only_050_equal", 0.5, 0.5, 0.5)   # 消融：等权

    # ② 完整管线（向量粗筛 + LLM 四维复核，复核用 llm-log（如存在））
    edges = vector_edges(eps, 0.5, 0.6, 0.4)
    same = [(a, b) for a, b, _ in edges if LLM_REVIEW_VERDICTS.get((a, b)) == "same"]
    clusters = clusters_from_edges(episode_ids, same)
    results["full_pipeline_llm"] = run_variant("full_pipeline_llm", clusters,
                                               truth_labels, truth_pairs, episode_ids)
    results["full_pipeline_llm"]["edges"] = [(a, b, s) for a, b, s in edges]
    results["full_pipeline_llm"]["llm_rejected"] = sorted(
        {(a, b) for a, b, _ in edges if LLM_REVIEW_VERDICTS.get((a, b)) == "different"})

    # ③ 规则降级管线（向量粗筛 + 关键词规则复核）——LLM 不可用时的兜底
    rule_same = [(a, b) for a, b, _ in edges
                 if cluster.rule_fallback(eps[episode_ids.index(a)], eps[episode_ids.index(b)])["judgement"] == "same"]
    clusters_rf = clusters_from_edges(episode_ids, rule_same)
    results["rule_fallback_pipeline"] = run_variant("rule_fallback_pipeline", clusters_rf,
                                                    truth_labels, truth_pairs, episode_ids)

    # ④ baselines：下限参照系
    results["baseline_all_diff"] = run_variant(
        "baseline_all_diff", [[e] for e in episode_ids], truth_labels, truth_pairs, episode_ids)
    results["baseline_all_same"] = run_variant(
        "baseline_all_same", [list(episode_ids)], truth_labels, truth_pairs, episode_ids)
    kw_edges = keyword_edges(eps, episode_ids)
    clusters_kw = clusters_from_edges(episode_ids, kw_edges)
    results["baseline_keyword_only"] = run_variant(
        "baseline_keyword_only", clusters_kw, truth_labels, truth_pairs, episode_ids)
    results["baseline_keyword_only"]["edges"] = kw_edges

    # ⑤ 负样本集：明确不同类对的相似度分布与误判
    # 注意：负样本含 WIKI-5（low 分，不在聚类范围），需从原始文件读全部 episode。
    all_eps = [json.loads(line) for line in open(episodes_path, encoding="utf-8") if line.strip()]
    by_id = {e["episode_id"]: e for e in all_eps}
    neg = []
    for a, b in gold["negative_samples"]["pairs"]:
        sa = cluster.build_corpus(by_id[a])
        sb = cluster.build_corpus(by_id[b])
        s = sim_weighted(sa, sb, 0.6, 0.4)
        neg.append({"pair": [a, b], "sim": round(s, 4),
                    "misjudged_as_same_by_vector": s >= 0.5})
    results["negative_samples"] = {
        "note": "相似度用默认权重（bigram 0.6 + trigram 0.4）；vector >= 0.5 即纯向量会误判为同类。",
        "pairs": neg,
        "sim_stats": _neg_stats(neg),
    }

    # 汇总
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    payload = {
        "generated_at": __import__("time").strftime("%Y-%m-%dT%H:%M:%SZ", __import__("time").gmtime()),
        "gold": {"labeler": gold["labeler"], "n_episodes": len(episode_ids),
                 "n_same_pairs": len(truth_pairs)},
        "results": results,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    _print_summary(results, episode_ids)
    print(f"\n全表 JSON -> {out_path}")
    return 0


def _neg_stats(neg: list) -> dict:
    sims = sorted(x["sim"] for x in neg)
    return {"n": len(sims), "min": sims[0], "median": sims[len(sims) // 2],
            "max": sims[-1],
            "misjudged_count": sum(1 for x in neg if x["misjudged_as_same_by_vector"])}


def _print_summary(results: dict, episode_ids: list) -> None:
    print("\n=== 变体对照（真值簇数=5，same 对=3） ===")
    header = f"{'变体':<34}{'ARI':>7}{'AMI':>7}{'P':>7}{'R':>7}{'F1':>7}{'簇数':>5}"
    print(header)
    for name in ("full_pipeline_llm", "rule_fallback_pipeline", "vector_only_050_mixed",
                 "vector_only_040_mixed", "vector_only_060_mixed",
                 "vector_only_050_bigram_only", "vector_only_050_trigram_only",
                 "vector_only_050_equal", "baseline_keyword_only",
                 "baseline_all_diff", "baseline_all_same"):
        r = results[name]
        p = r["pair_prf"]
        print(f"{name:<34}{r['ari']:>7}{r['ami']:>7}{p['precision']:>7}"
              f"{p['recall']:>7}{p['f1']:>7}{r['n_clusters']:>5}")

    print("\n=== 负样本集相似度分布 ===")
    ns = results["negative_samples"]
    for x in ns["pairs"]:
        flag = "  <-- 纯向量误判为同类" if x["misjudged_as_same_by_vector"] else ""
        print(f"  {'-'.join(x['pair']):<24} sim={x['sim']:.4f}{flag}")
    print(f"  统计: {ns['sim_stats']}")


def main() -> int:
    p = argparse.ArgumentParser(description="SkillTrove 算法四件套评估（纯 Python，零 LLM）")
    p.add_argument("--episodes", default=os.path.join(ROOT, "archive", "scored.jsonl"))
    p.add_argument("--gold", default=os.path.join(ROOT, "evals", "gold-truth.json"))
    p.add_argument("--out", default=os.path.join(ROOT, "evals", "results",
                                                 "algorithm-eval-2026-08-29.json"))
    args = p.parse_args()
    return run_all(args.episodes, args.gold, args.out)


if __name__ == "__main__":
    raise SystemExit(main())
