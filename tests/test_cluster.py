"""cli/cluster.py 单元测试：语料构建 / LLM 复核解析 / 规则降级 / 连通分量 / 证据。

运行：python -m unittest discover -s tests -v
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cli import cluster  # noqa: E402


class TestBuildCorpus(unittest.TestCase):
    def test_goal_and_acceptance(self):
        ep = {"goal": "全面调研参考对象", "acceptance": "报告完整且带来源标注"}
        c = cluster.build_corpus(ep)
        self.assertIn("全面调研参考对象", c)
        self.assertIn("报告完整且带来源标注", c)

    def test_fallback_title(self):
        ep = {"title": "只有标题", "acceptance": "x"}
        self.assertIn("只有标题", cluster.build_corpus(ep))


class TestParseReview(unittest.TestCase):
    def test_plain_json(self):
        r = cluster.parse_review('{"judgement": "same", "kind": "procedure"}')
        self.assertEqual(r.get("judgement"), "same")
        self.assertEqual(r.get("kind"), "procedure")

    def test_fenced_json(self):
        r = cluster.parse_review('```json\n{"judgement": "different"}\n```')
        self.assertEqual(r.get("judgement"), "different")

    def test_empty(self):
        self.assertEqual(cluster.parse_review(""), {})

    def test_garbage(self):
        self.assertEqual(cluster.parse_review("这不是 JSON 内容"), {})

    def test_noise_around_json(self):
        r = cluster.parse_review('前置说明 {"judgement": "same"} 后置')
        self.assertEqual(r.get("judgement"), "same")


class TestRuleFallback(unittest.TestCase):
    def test_same_keyword(self):
        a = {"goal": "竞品调研任务", "title": ""}
        b = {"goal": "参考对象调研任务", "title": ""}
        r = cluster.rule_fallback(a, b)
        self.assertEqual(r["judgement"], "same")
        self.assertIn("规则降级", r["note"])

    def test_different(self):
        a = {"goal": "调研", "title": ""}
        b = {"goal": "写单元测试", "title": ""}
        self.assertEqual(cluster.rule_fallback(a, b)["judgement"], "different")

    def test_prd_keyword(self):
        a = {"goal": "编写 PRD 文档", "title": ""}
        b = {"goal": "评审 PRD v2", "title": ""}
        self.assertEqual(cluster.rule_fallback(a, b)["judgement"], "same")


class TestUnionFindClusters(unittest.TestCase):
    def test_chain_merge(self):
        groups = cluster.union_find_clusters([(0, 1), (1, 2)])
        self.assertEqual(groups, [[0, 1, 2]])

    def test_disjoint(self):
        groups = cluster.union_find_clusters([(0, 1), (2, 3)])
        self.assertEqual(groups, [[0, 1], [2, 3]])

    def test_empty(self):
        self.assertEqual(cluster.union_find_clusters([]), [])


class TestEvidenceFor(unittest.TestCase):
    def test_no_comments_fallback(self):
        ev = cluster.evidence_for({"episode_id": "ep-1"})
        self.assertEqual(ev[0]["loc"], "description.md")

    def test_comment_evidence(self):
        ep = {"episode_id": "ep-1",
              "comments": [{"ts": "2026-08-28T05:00:00Z", "agent": "alice", "text": "结论"}]}
        ev = cluster.evidence_for(ep)
        self.assertEqual(ev[0]["loc"], "comment@2026-08-28T05:00:00Z")
        self.assertEqual(ev[0]["agent"], "alice")

    def test_evidence_capped_at_three(self):
        comments = [{"ts": f"t{i}", "agent": "a", "text": "x"} for i in range(5)]
        ev = cluster.evidence_for({"episode_id": "ep-1", "comments": comments})
        self.assertLessEqual(len(ev), 3)


class TestResolveThreshold(unittest.TestCase):
    def test_explicit_wins(self):
        self.assertEqual(cluster.resolve_threshold(0.7, "git-repo"), 0.7)
        self.assertEqual(cluster.resolve_threshold(0.4, "log-export"), 0.4)

    def test_default_050_for_unknown_and_log_export(self):
        self.assertEqual(cluster.resolve_threshold(None, ""), 0.5)
        self.assertEqual(cluster.resolve_threshold(None, "log-export"), 0.5)
        self.assertEqual(cluster.resolve_threshold(None, None), 0.5)

    def test_git_repo_default_040(self):
        self.assertEqual(cluster.resolve_threshold(None, "git-repo"), 0.40)


class TestFirewallRules(unittest.TestCase):
    def test_five_rules(self):
        self.assertEqual(len(cluster.FIREWALL_RULES), 5)
        self.assertIn("疑似一次性事件", cluster.FIREWALL_RULES[-1])


class TestReviewMethodBinding(unittest.TestCase):
    """回归：候选 review_method 应取代表对（first_pair）的复核方式，
    而非循环结束后最后一个 method（旧实现会把最后一对的降级方式错标给候选）。"""

    def setUp(self):
        self._orig_call = cluster.llm.call
        self._orig_pairs = cluster.embed.pairs_above

    def tearDown(self):
        cluster.llm.call = self._orig_call
        cluster.embed.pairs_above = self._orig_pairs

    def _episodes_file(self, path: str) -> None:
        import json
        with open(path, "w", encoding="utf-8") as f:
            for eid, goal in [("ep-1", "全面调研对象 A 的机制拆解"),
                              ("ep-2", "全面调研对象 A 的机制拆解"),
                              ("ep-3", "全面调研对象 C 的机制拆解")]:
                f.write(json.dumps({"episode_id": eid, "goal": goal, "acceptance": "报告带来源",
                                    "title": "", "main_agent": "a", "status": "done",
                                    "comments": [], "agent_ids": ["a"],
                                    "score": {"label": "high"}}, ensure_ascii=False) + "\n")

    def test_method_binds_to_first_pair(self):
        import json
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            ep_path = os.path.join(tmp, "scored.jsonl")
            self._episodes_file(ep_path)
            # 固定粗筛顺序：首对 (0,1) sim 更高；第二对 (0,2) LLM 失败走规则降级
            cluster.embed.pairs_above = lambda corpus, threshold: [(0, 1, 0.9), (0, 2, 0.8)]
            calls = {"n": 0}

            def fake_llm(prompt, system=None, backend=None):
                calls["n"] += 1
                if calls["n"] == 1:
                    return {"ok": True, "text": '{"judgement": "same", "kind": "procedure"}',
                            "backend": "fake"}
                return {"ok": False, "error": "模拟 LLM 失败"}

            cluster.llm.call = fake_llm
            out_path = os.path.join(tmp, "candidates.json")

            class A:
                pass
            a = A()
            a.episodes = ep_path
            a.out = out_path
            a.threshold = 0.5
            a.llm_backend = None
            rc = cluster.run(a)

            self.assertEqual(rc, 0)
            with open(out_path, encoding="utf-8") as f:
                data = json.load(f)
            cand = data["candidates"][0]
            # 首对（ep-1×ep-2）走 llm 复核，末对走降级——review_method 必须取首对
            self.assertEqual(cand["review_method"], "llm:fake",
                             "候选 review_method 应取首对（llm）而非最后一对（rule-fallback）")
            self.assertNotIn("rule-fallback", cand["review_method"])
            self.assertEqual(cand["episode_ids"], ["ep-1", "ep-2", "ep-3"])
            self.assertEqual(calls["n"], 2, "应恰好复核两对")

    def test_git_repo_shape_uses_default_040(self):
        # 回归：git-repo 源未显式指定阈值时，粗筛用形态默认 0.40（commit 文本薄）
        import json
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            ep_path = os.path.join(tmp, "scored.jsonl")
            with open(ep_path, "w", encoding="utf-8") as f:
                for eid, goal in [("ep-1", "更新 DoD 验收报告（回归验证 5/5）"),
                                  ("ep-2", "更新 DoD 验收报告（2026-08-24 回归验证 5/5）")]:
                    f.write(json.dumps({"episode_id": eid, "goal": goal, "title": goal,
                                        "acceptance": "", "status": "done", "main_agent": "a",
                                        "agent_ids": ["a"], "comments": [], "shape": "git-repo",
                                        "score": {"label": "high"}}, ensure_ascii=False) + "\n")
            captured = {}

            def fake_pairs(corpus, threshold):
                captured["threshold"] = threshold
                return [(0, 1, 0.4841)]

            cluster.embed.pairs_above = fake_pairs
            cluster.llm.call = lambda *a, **k: {"ok": False, "error": "mock"}
            try:
                class A:
                    pass
                a = A()
                a.episodes = ep_path
                a.out = os.path.join(tmp, "c.json")
                a.threshold = None
                a.llm_backend = None
                rc = cluster.run(a)
            finally:
                cluster.embed.pairs_above = self._orig_pairs
                cluster.llm.call = self._orig_call
            self.assertEqual(rc, 0)
            self.assertEqual(captured["threshold"], 0.40,
                             "git-repo 源默认阈值应为 0.40")


class TestOutDirCreated(unittest.TestCase):
    """回归：--out 指向不存在的目录时应自动创建（审查 P1-1，曾 FileNotFoundError 崩溃）。"""

    def setUp(self):
        self._orig_pairs = cluster.embed.pairs_above
        self._orig_call = cluster.llm.call

    def tearDown(self):
        cluster.embed.pairs_above = self._orig_pairs
        cluster.llm.call = self._orig_call

    def test_cluster_out_dir_created(self):
        import json
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            ep_path = os.path.join(tmp, "scored.jsonl")
            with open(ep_path, "w", encoding="utf-8") as f:
                f.write(json.dumps({"episode_id": "ep-1", "goal": "g", "title": "g",
                                    "acceptance": "", "status": "done", "main_agent": "a",
                                    "agent_ids": ["a"], "comments": [],
                                    "score": {"label": "high"}}, ensure_ascii=False) + "\n")
            cluster.embed.pairs_above = lambda c, t: []
            try:
                class A:
                    pass
                a = A()
                a.episodes = ep_path
                a.out = os.path.join(tmp, "deep", "nested", "c.json")
                a.threshold = 0.5
                a.llm_backend = None
                rc = cluster.run(a)
            finally:
                cluster.embed.pairs_above = self._orig_pairs
            self.assertEqual(rc, 0)
            self.assertTrue(os.path.isfile(os.path.join(tmp, "deep", "nested", "c.json")))


    def test_review_pair_cap(self):
        # 大窗口下复核成本 O(对数)：SKILLTROVE_MAX_REVIEW_PAIRS 截断（取相似度最高 K 对）
        import json
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            ep_path = os.path.join(tmp, "scored.jsonl")
            with open(ep_path, "w", encoding="utf-8") as f:
                for eid in ("ep-1", "ep-2", "ep-3"):
                    f.write(json.dumps({"episode_id": eid, "goal": f"任务 {eid}", "title": f"任务 {eid}",
                                        "acceptance": "", "status": "done", "main_agent": "a",
                                        "agent_ids": ["a"], "comments": [],
                                        "score": {"label": "high"}}, ensure_ascii=False) + "\n")
            cluster.embed.pairs_above = lambda c, t: [(0, 1, 0.9), (0, 2, 0.8), (1, 2, 0.7)]
            calls = {"n": 0}
            cluster.llm.call = lambda *a, **k: (calls.__setitem__("n", calls["n"] + 1)
                                                or {"ok": False, "error": "mock"})
            old = os.environ.get("SKILLTROVE_MAX_REVIEW_PAIRS")
            os.environ["SKILLTROVE_MAX_REVIEW_PAIRS"] = "2"
            try:
                class A:
                    pass
                a = A()
                a.episodes = ep_path
                a.out = os.path.join(tmp, "c.json")
                a.threshold = 0.5
                a.llm_backend = None
                rc = cluster.run(a)
            finally:
                cluster.embed.pairs_above = self._orig_pairs
                cluster.llm.call = self._orig_call
                if old is None:
                    os.environ.pop("SKILLTROVE_MAX_REVIEW_PAIRS", None)
                else:
                    os.environ["SKILLTROVE_MAX_REVIEW_PAIRS"] = old
            self.assertEqual(rc, 0)
            self.assertEqual(calls["n"], 2, "只应复核相似度最高的 2 对")


if __name__ == "__main__":
    unittest.main()
