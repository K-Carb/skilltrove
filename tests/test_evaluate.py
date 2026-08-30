"""evals/evaluate_pipeline.py 单元测试：ARI / AMI / 配对 P-R-F1 / 参数化相似度 / 关键词边。

运行：python -m unittest discover -s tests -v
"""

from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "evals"))

import evaluate_pipeline as ev  # noqa: E402


class TestARI(unittest.TestCase):
    def test_perfect_agreement(self):
        self.assertEqual(ev.ari([0, 0, 1, 1], [0, 0, 1, 1]), 1.0)

    def test_all_same_vs_all_same(self):
        self.assertEqual(ev.ari(["a"] * 4, ["x"] * 4), 1.0)

    def test_known_small_case(self):
        # 手算：true=[0,0,1], pred=[0,1,1] → sum_ij=0, sum_i=sum_j=1, total=3
        # ARI = (0 - 1/3) / (1 - 1/3) = -0.5
        self.assertAlmostEqual(ev.ari([0, 0, 1], [0, 1, 1]), -0.5, places=9)

    def test_symmetry(self):
        t = [0, 0, 1, 1, 2]
        p = [1, 2, 2, 0, 0]
        self.assertAlmostEqual(ev.ari(t, p), ev.ari(p, t), places=9)

    def test_empty(self):
        self.assertEqual(ev.ari([], []), 0.0)

    def test_permutation_invariant(self):
        # 真值簇标签换名不影响指标（标签无关性）
        self.assertAlmostEqual(ev.ari([0, 0, 1, 1], [0, 0, 1, 1]),
                               ev.ari([0, 0, 1, 1], [1, 1, 0, 0]), places=9)


class TestAMI(unittest.TestCase):
    def test_perfect_agreement(self):
        self.assertEqual(ev.ami([0, 0, 1, 1], [0, 0, 1, 1]), 1.0)

    def test_symmetry(self):
        t = [0, 0, 1, 1, 2, 2]
        p = [2, 1, 0, 1, 2, 0]
        self.assertAlmostEqual(ev.ami(t, p), ev.ami(p, t), places=9)

    def test_empty(self):
        self.assertEqual(ev.ami([], []), 0.0)

    def test_independent_clusters_low(self):
        # 每个簇各含所有类 → 与随机持平，AMI 应接近 0
        t = [0, 0, 1, 1, 2, 2]
        p = [0, 1, 2, 0, 1, 2]
        self.assertLess(ev.ami(t, p), 0.05)

    def test_permutation_invariant(self):
        a = ev.ami([0, 0, 1, 1], [0, 1, 1, 0])
        b = ev.ami([0, 0, 1, 1], [1, 0, 0, 1])
        self.assertAlmostEqual(a, b, places=9)

    def test_rejects_random(self):
        # 随机性很强的划分 AMI 不应为高值（校正随机机会）
        t = [0, 0, 0, 1, 1, 1]
        p = [0, 1, 0, 1, 0, 1]  # 每个簇各含两类一半 → 接近随机
        self.assertLess(ev.ami(t, p), 0.2)


class TestPairPRF(unittest.TestCase):
    def test_perfect(self):
        truth = {("a", "b"), ("b", "c")}
        r = ev.pair_prf(truth, set(truth))
        self.assertEqual(r, {"tp": 2, "fp": 0, "fn": 0,
                             "precision": 1.0, "recall": 1.0, "f1": 1.0})

    def test_empty_prediction(self):
        r = ev.pair_prf({("a", "b")}, set())
        self.assertEqual(r["precision"], 0.0)
        self.assertEqual(r["recall"], 0.0)
        self.assertEqual(r["f1"], 0.0)

    def test_false_positive_lowers_precision(self):
        truth = {("a", "b")}
        pred = {("a", "b"), ("a", "c")}  # (a,c) 是 FP
        r = ev.pair_prf(truth, pred)
        self.assertEqual(r["tp"], 1)
        self.assertEqual(r["fp"], 1)
        self.assertEqual(r["fn"], 0)
        self.assertAlmostEqual(r["precision"], 0.5, places=9)
        self.assertEqual(r["recall"], 1.0)

    def test_false_negative_lowers_recall(self):
        truth = {("a", "b"), ("b", "c")}
        pred = {("a", "b")}
        r = ev.pair_prf(truth, pred)
        self.assertEqual(r["fn"], 1)
        self.assertAlmostEqual(r["recall"], 0.5, places=9)


class TestSimWeighted(unittest.TestCase):
    def test_matches_embed_default(self):
        # 权重 0.6/0.4 时应与生产模块 embed.similarity 完全一致
        from cli import embed  # noqa: E402
        a = "全面调研参考对象的经验机制拆解产出调研报告"
        b = "全面调研参考对象的证据核实产出调研报告"
        self.assertAlmostEqual(ev.sim_weighted(a, b, 0.6, 0.4), embed.similarity(a, b), places=9)

    def test_symmetry(self):
        a, b = "甲乙文本内容", "乙甲文本内容"
        self.assertAlmostEqual(ev.sim_weighted(a, b, 0.5, 0.5),
                               ev.sim_weighted(b, a, 0.5, 0.5), places=9)

    def test_self_similar(self):
        self.assertAlmostEqual(ev.sim_weighted("测试文本", "测试文本", 1.0, 0.0), 1.0, places=9)

    def test_weight_shift_changes_score(self):
        a = "调研调研调研"
        b = "调研调研报告"
        # 短文本上 bigram 与 trigram 的取值不同，权重移动应改变混合分
        s_b = ev.sim_weighted(a, b, 1.0, 0.0)
        s_t = ev.sim_weighted(a, b, 0.0, 1.0)
        self.assertNotAlmostEqual(s_b, s_t, places=9)


class TestClusterHelpers(unittest.TestCase):
    def test_clusters_from_edges_connectivity(self):
        ids = ["a", "b", "c", "d"]
        clusters = ev.clusters_from_edges(ids, [("a", "b"), ("b", "c")])
        self.assertEqual(clusters, [["a", "b", "c"]])

    def test_label_vectors_fills_singletons(self):
        clusters = [["a", "b"]]
        self.assertEqual(ev.label_vectors(clusters, ["a", "b", "c"]), ["a", "a", "c"])

    def test_run_variant_completes_partition(self):
        # 连通分量缺孤立节点时，run_variant 应补全单例簇（簇数与指标在完整划分上算）
        row = ev.run_variant("t", [["a", "b"]], {"a": "x", "b": "x", "c": "y"},
                             {("a", "b")}, ["a", "b", "c"])
        self.assertEqual(row["n_clusters"], 2)  # {a,b} + {c}
        self.assertEqual(row["ari"], 1.0)
        self.assertEqual(row["pair_prf"]["f1"], 1.0)

    def test_keyword_edges_hit_same_keyword(self):
        eps = [{"episode_id": "e1", "goal": "竞品调研任务", "title": ""},
               {"episode_id": "e2", "goal": "参考对象调研", "title": ""},
               {"episode_id": "e3", "goal": "写单元测试", "title": ""}]
        edges = ev.keyword_edges(eps, ["e1", "e2", "e3"])
        self.assertEqual(edges, [("e1", "e2")])

    def test_keyword_edges_cross_type(self):
        # PRD 写 vs PRD 审：主题词同含「PRD」→ 关键词规则误判为同类（hard negative 案例）
        eps = [{"episode_id": "w", "goal": "撰写 PRD 文档", "title": ""},
               {"episode_id": "r", "goal": "评审 PRD 草稿", "title": ""}]
        self.assertEqual(ev.keyword_edges(eps, ["w", "r"]), [("w", "r")])


class TestAnnotationSensitivity(unittest.TestCase):
    """evals/annotation_sensitivity.py 的逻辑单元：同类对推导 / 反事实标注构造。"""

    def test_derive_pairs_pair(self):
        from annotation_sensitivity import derive_pairs
        self.assertEqual(derive_pairs({"a": "r", "b": "r", "c": "s", "d": "s"}),
                         {("a", "b"), ("c", "d")})

    def test_derive_pairs_triple(self):
        from annotation_sensitivity import derive_pairs
        self.assertEqual(derive_pairs({"a": "r", "b": "r", "c": "r"}),
                         {("a", "b"), ("a", "c"), ("b", "c")})

    def test_derive_pairs_singletons_empty(self):
        from annotation_sensitivity import derive_pairs
        self.assertEqual(derive_pairs({"a": "s1", "b": "s2"}), set())

    def test_build_labelings_shapes(self):
        from annotation_sensitivity import build_labelings, RESEARCH_LABEL
        base = {"ep-WIKI-1": "single-project-analysis", "ep-WIKI-2": "single-mvp-scope",
                "ep-WIKI-3": RESEARCH_LABEL, "ep-WIKI-4": RESEARCH_LABEL,
                "ep-WIKI-5": RESEARCH_LABEL, "ep-WIKI-6": "single-research-synthesis",
                "ep-WIKI-8": "single-prd-review"}
        ls = build_labelings(base)
        self.assertEqual(set(ls), {"base", "merge_AGEN6_into_research",
                                   "merge_AGEN1_into_research",
                                   "merge_AGEN6_AGEN1_into_research"})
        self.assertEqual(ls["base"], base)
        self.assertEqual(ls["merge_AGEN6_into_research"]["ep-WIKI-6"], RESEARCH_LABEL)
        self.assertEqual(ls["merge_AGEN1_into_research"]["ep-WIKI-1"], RESEARCH_LABEL)
        self.assertEqual(ls["merge_AGEN6_AGEN1_into_research"]["ep-WIKI-6"], RESEARCH_LABEL)
        self.assertEqual(ls["merge_AGEN6_AGEN1_into_research"]["ep-WIKI-1"], RESEARCH_LABEL)

    def test_counterfactual_only_touches_targets(self):
        from annotation_sensitivity import build_labelings
        base = {"ep-WIKI-1": "a", "ep-WIKI-2": "b", "ep-WIKI-3": "r", "ep-WIKI-4": "r",
                "ep-WIKI-5": "r", "ep-WIKI-6": "c", "ep-WIKI-8": "d"}
        for lname, labels in build_labelings(base).items():
            if lname == "base":
                continue
            changed = {k for k in labels if labels[k] != base[k]}
            self.assertLessEqual(len(changed), 2, f"{lname} 改动应 <=2 个标签")


if __name__ == "__main__":
    unittest.main()
