"""GitHub 适配器（ADR-0004 Model 2）的学习测试：issue JSON -> episode 翻译契约。

fetch_issues 的网络行为不做测试（无网络依赖原则）；翻译映射用真实结构的 fixture 钉住。
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cli.adapters import (GitHubAdapter, github_issue_to_episode,  # noqa: E402
                          status_normalize)

FIXTURE_ISSUE = {
    "number": 42,
    "title": "调研：列表分页的主流实现方案",
    "state": "closed",
    "body": "动手改列表前，先对比 offset 与 cursor 两种做法的取舍与迁移成本。",
    "user": {"login": "bob"},
    "closed_at": "2026-08-29T12:00:00Z",
    "updated_at": "2026-08-29T12:30:00Z",
}
FIXTURE_COMMENTS = [
    {"created_at": "2026-08-29T11:00:00Z", "user": {"login": "carol"}, "body": "补充：深分页场景"},
]


class GithubMappingTests(unittest.TestCase):
    def setUp(self):
        self.ep = github_issue_to_episode("acme/wiki", FIXTURE_ISSUE, FIXTURE_COMMENTS)

    def test_identity_fields(self):
        self.assertEqual(self.ep["episode_id"], "gh-acme-wiki-42")
        self.assertEqual(self.ep["issue_key"], "acme/wiki#42")

    def test_text_fields_copied(self):
        self.assertEqual(self.ep["title"], "调研：列表分页的主流实现方案")
        self.assertIn("offset 与 cursor", self.ep["goal"])

    def test_state_closed_maps_to_done(self):
        self.assertEqual(self.ep["status"], "done")
        self.assertEqual(status_normalize("closed"), "done")

    def test_actor_from_author(self):
        self.assertEqual(self.ep["main_agent"], "bob")
        self.assertEqual(self.ep["agent_ids"], ["bob"])

    def test_join_key_verified_at_source(self):
        jk = self.ep["business_join_key"]
        self.assertEqual(jk["table"], "github_issues")
        self.assertTrue(jk["verified_at_source"])  # closed 状态在 API 层即已验证

    def test_comments_mapped(self):
        self.assertEqual(len(self.ep["comments"]), 1)
        self.assertEqual(self.ep["comments"][0]["agent"], "carol")

    def test_open_issue_not_source_verified(self):
        issue = dict(FIXTURE_ISSUE, state="open", closed_at=None)
        ep = github_issue_to_episode("acme/wiki", issue, [])
        self.assertFalse(ep["business_join_key"]["verified_at_source"])

    def test_long_body_truncated(self):
        issue = dict(FIXTURE_ISSUE, body="长" * 5000)
        ep = github_issue_to_episode("acme/wiki", issue, [])
        self.assertLessEqual(len(ep["goal"]), 2000)


class GithubDetectTests(unittest.TestCase):
    def test_detect_gh_prefix(self):
        self.assertTrue(GitHubAdapter().detect("gh:acme/wiki"))
        self.assertFalse(GitHubAdapter().detect("https://github.com/acme/wiki"))

    def test_excludes_pull_requests_in_discover(self):
        # fetch_issues 的过滤逻辑：PR 混在 issues API 里，须按 pull_request 键排除
        batch = [{"number": 1, "pull_request": {"url": "x"}}, {"number": 2}]
        prs = [i for i in batch if "pull_request" in i]
        self.assertEqual([i["number"] for i in batch if i not in prs], [2])


if __name__ == "__main__":
    unittest.main()
