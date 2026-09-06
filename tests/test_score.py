"""cli/score.py 单元测试：L2 业务结果评分规则（join 查证 + 完成态偏差）。

运行：python -m unittest discover -s tests -v
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cli import score  # noqa: E402

MANIFEST_KEYS = {"WIKI-1", "WIKI-2"}


def make_ep(overrides=None) -> dict:
    ep = {
        "episode_id": "ep-WIKI-1",
        "issue_key": "WIKI-1",
        "status": "done",
        "attachments": ["attachment.md"],
        "comments": [],
        "business_join_key": {
            "table": "issue_status_history",
            "id": "WIKI-1",
            "measured_at": "2026-08-28T00:00:00Z",
        },
    }
    if overrides:
        ep.update(overrides)
    return ep


class TestScoreEpisode(unittest.TestCase):
    def test_high_full_signal(self):
        s = score.score_episode(make_ep(), MANIFEST_KEYS)
        self.assertEqual(s["label"], "high")
        self.assertTrue(s["join_verified"])
        self.assertTrue(all(r["pass"] for r in s["rules"]))

    def test_low_missing_join(self):
        s = score.score_episode(make_ep({"business_join_key": {}}), MANIFEST_KEYS)
        self.assertEqual(s["label"], "low")

    def test_low_join_id_not_in_manifest(self):
        ep = make_ep({"business_join_key": {"table": "issue_status_history",
                                            "id": "WIKI-99", "measured_at": "x"}})
        self.assertEqual(score.score_episode(ep, MANIFEST_KEYS)["label"], "low")

    def test_join_generic_table(self):
        # D2 泛化：非 issue_status_history 的来源表（git_commit/source_table 等）
        # 只要 id 在 manifest 且 measured_at 存在，join 即可查证
        ep = make_ep({"business_join_key": {"table": "git_commit", "id": "WIKI-1", "measured_at": "x"}})
        self.assertEqual(score.score_episode(ep, MANIFEST_KEYS)["label"], "high")

    def test_low_join_missing_measured_at(self):
        ep = make_ep({"business_join_key": {"table": "git_commit", "id": "WIKI-1", "measured_at": None}})
        self.assertEqual(score.score_episode(ep, MANIFEST_KEYS)["label"], "low")

    def test_low_join_empty_table(self):
        ep = make_ep({"business_join_key": {"id": "WIKI-1", "measured_at": "x"}})
        self.assertEqual(score.score_episode(ep, MANIFEST_KEYS)["label"], "low")

    def test_low_not_done(self):
        ep = make_ep({"status": "in_progress"})
        self.assertEqual(score.score_episode(ep, MANIFEST_KEYS)["label"], "low")

    def test_low_no_output(self):
        ep = make_ep({"attachments": [], "comments": []})
        self.assertEqual(score.score_episode(ep, MANIFEST_KEYS)["label"], "low")

    def test_high_output_via_long_comment(self):
        ep = make_ep({"attachments": [], "comments": [{"text": "产出性长文" * 200}]})
        self.assertEqual(score.score_episode(ep, MANIFEST_KEYS)["label"], "high")

    def test_high_output_via_attachment_ref(self):
        ep = make_ep({"attachments": [], "comments": [{"text": "结论见附件: report.md"}]})
        self.assertEqual(score.score_episode(ep, MANIFEST_KEYS)["label"], "high")

    def test_high_output_via_signal(self):
        # D2 泛化：通用来源由适配器给出 output_signal（如 issue 关闭 / commit 带文件）
        ep = make_ep({"attachments": [], "comments": [], "output_signal": True})
        self.assertEqual(score.score_episode(ep, MANIFEST_KEYS)["label"], "high")

    def test_rules_recorded(self):
        s = score.score_episode(make_ep(), MANIFEST_KEYS)
        self.assertEqual([r["rule"] for r in s["rules"]],
                         ["join_verifiable", "completion_signal"])
        self.assertTrue(all(r["pass"] for r in s["rules"]))

    def test_does_not_mutate_episode(self):
        ep = make_ep()
        score.score_episode(ep, MANIFEST_KEYS)
        self.assertNotIn("score", ep)


if __name__ == "__main__":
    unittest.main()


class TestScoreOutDir(unittest.TestCase):
    """回归：--out 指向不存在的目录时应自动创建（审查 P1-1，曾 FileNotFoundError 崩溃）。"""

    def test_out_dir_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            ep_path = os.path.join(tmp, "episodes.jsonl")
            with open(ep_path, "w", encoding="utf-8") as f:
                f.write(json.dumps({"episode_id": "ep-1", "status": "done",
                                    "attachments": ["a"], "comments": [],
                                    "business_join_key": {"table": "t", "id": "1",
                                                          "measured_at": "x"}}) + "\n")
            out = os.path.join(tmp, "deep", "scored.jsonl")
            rc = score.run(argparse.Namespace(episodes=ep_path, manifest=None, out=out))
            self.assertEqual(rc, 0)
            self.assertTrue(os.path.isfile(out))


class VerifiedAtSourceDispatchTests(unittest.TestCase):
    """查证器分派：来源侧已验证（verified_at_source）无需 manifest 即可 join 通过。"""

    def test_verified_at_source_passes_without_manifest(self):
        from cli import score
        ep = {
            "episode_id": "gh-acme-wiki-42", "issue_key": "acme/wiki#42",
            "title": "调研分页方案", "status": "done",
            "goal": "对比 offset 与 cursor",
            "output_signal": True,
            "business_join_key": {"table": "github_issues", "id": "acme/wiki#42",
                                  "measured_at": "2026-08-29T12:00:00Z",
                                  "verified_at_source": True},
        }
        result = score.score_episode(ep, manifest_keys=set())  # 空 manifest：无表可查
        self.assertTrue(result["rules"][0]["pass"])  # join_verifiable

    def test_without_source_verification_manifest_still_required(self):
        from cli import score
        ep = {
            "episode_id": "ep-1", "issue_key": "WIKI-1", "title": "t", "status": "done",
            "output_signal": True,
            "business_join_key": {"table": "issue_status_history", "id": "WIKI-1",
                                  "measured_at": "2026-08-29T12:00:00Z"},
        }
        result = score.score_episode(ep, manifest_keys=set())
        self.assertFalse(result["rules"][0]["pass"])
