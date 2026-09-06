"""团队记录库（ADR-0004 Model 1）的学习测试：脱敏 / 质量闸门 / 分片幂等 / git 推拉。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cli import records  # noqa: E402
from cli.records import quality_gate, sanitize_episode, sanitize_text  # noqa: E402


class SanitizeTests(unittest.TestCase):
    def test_secret_patterns_redacted(self):
        text = "配置 sk-live-abc123xyzdef1234 和 ghp_" + "a" * 36 + "，别提交"
        out, hits = sanitize_text(text)
        self.assertNotIn("sk-live", out)
        self.assertNotIn("ghp_", out)
        self.assertGreaterEqual(hits, 2)

    def test_key_value_assignment_redacted(self):
        out, hits = sanitize_text("api_key = abc123def456secret")
        self.assertNotIn("abc123def456secret", out)
        self.assertGreaterEqual(hits, 1)

    def test_stoplist_replaces_customer_name(self):
        out, _ = sanitize_text("修复 ShopVidi 网关 502", stoplist=["ShopVidi"])
        self.assertNotIn("ShopVidi", out)
        self.assertIn("某客户", out)

    def test_clean_text_untouched(self):
        original = "调研分页方案：对比 offset 与 cursor 的取舍"
        out, hits = sanitize_text(original)
        self.assertEqual(out, original)
        self.assertEqual(hits, 0)

    def test_sanitize_episode_covers_fields_and_attachments(self):
        ep = {
            "title": "修复 ShopVidi 网关",
            "goal": "密钥 sk-abcdefghijklmnopqrst 在配置里",
            "comments": [{"agent": "alice", "text": "token=zzz111222333"}, {"agent": "bob", "text": "干净"}],
            "attachments": ["report.md", {"name": "data.csv"}],
        }
        clean, _ = sanitize_episode(ep, stoplist=["ShopVidi"])
        self.assertIn("某客户", clean["title"])
        self.assertNotIn("sk-abcdefghijklmnopqrst", clean["goal"])
        self.assertNotIn("zzz111222333", clean["comments"][0]["text"])
        self.assertIn("干净", clean["comments"][1]["text"])
        self.assertEqual(clean["attachments"], ["report.md", "data.csv"])


class QualityGateTests(unittest.TestCase):
    def _good(self, **over):
        ep = {
            "episode_id": "ep-1", "issue_key": "WIKI-2",
            "title": "调研分页方案", "status": "done",
            "goal": "对比 offset 与 cursor 两种分页做法的取舍与迁移成本",
            "acceptance": "给出选型结论",
            "main_agent": "bob", "agent_ids": ["bob"],
            "comments": [{"agent": "bob", "text": "完成了调研"}],
        }
        ep.update(over)
        return ep

    def test_good_episode_passes(self):
        ok, reasons = quality_gate(self._good())
        self.assertTrue(ok, reasons)

    def test_thin_goal_rejected_with_reason(self):
        ok, reasons = quality_gate(self._good(goal="调研"))
        self.assertFalse(ok)
        self.assertTrue(any("不足" in r for r in reasons))

    def test_unfinished_status_rejected(self):
        ok, reasons = quality_gate(self._good(status="in_progress"))
        self.assertFalse(ok)
        self.assertTrue(any("完成态" in r for r in reasons))

    def test_missing_actor_rejected(self):
        ep = self._good(main_agent="", agent_ids=[])
        ok, reasons = quality_gate(ep)
        self.assertFalse(ok)
        self.assertTrue(any("归属者" in r for r in reasons))

    def test_no_evidence_rejected(self):
        ep = self._good(comments=[], acceptance="")
        ok, reasons = quality_gate(ep)
        self.assertFalse(ok)
        self.assertTrue(any("证据" in r or "评论" in r for r in reasons))


class ShardPushPullTests(unittest.TestCase):
    """用本地裸仓库当远端（git 原生支持路径远端），验证分片推送/拉取/幂等。"""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.remote = os.path.join(self.td.name, "team-records.git")
        subprocess.run(["git", "init", "--bare", "-b", "main", self.remote],
                       check=True, capture_output=True)
        self.cache_root = os.path.join(self.td.name, "cache")

    def tearDown(self):
        self.td.cleanup()

    def _eps(self, n):
        return [{"episode_id": f"alice-ep-{i}", "issue_key": f"WIKI-{i}",
                 "title": f"任务 {i}", "status": "done", "main_agent": "alice"} for i in range(1, n + 1)]

    def test_push_creates_shard_and_roundtrips(self):
        r1 = records.push_shard(self.remote, "alice", self._eps(3), self.cache_root)
        self.assertTrue(r1["ok"], r1["error"])
        cache = records.pull_records(self.remote, self.cache_root + "-pull")
        eps = records.load_shards(cache)
        self.assertEqual(len(eps), 3)
        self.assertEqual({e["main_agent"] for e in eps}, {"alice"})

    def test_push_is_idempotent_full_rewrite(self):
        records.push_shard(self.remote, "alice", self._eps(3), self.cache_root)
        # 第二次推送只有 2 条（全量重写自己的分片）→ 分片变为 2 条，无重复
        records.push_shard(self.remote, "alice", self._eps(2), self.cache_root)
        cache = records.pull_records(self.remote, self.cache_root + "-pull2")
        eps = records.load_shards(cache)
        self.assertEqual(len(eps), 2)

    def test_two_members_no_conflict(self):
        records.push_shard(self.remote, "alice", self._eps(2), self.cache_root)
        records.push_shard(self.remote, "bob", [
            {"episode_id": "bob-ep-1", "issue_key": "WIKI-9", "title": "搜索选型",
             "status": "done", "main_agent": "bob"}], self.cache_root)
        cache = records.pull_records(self.remote, self.cache_root + "-pull3")
        eps = records.load_shards(cache)
        self.assertEqual(len(eps), 3)
        self.assertEqual({e["main_agent"] for e in eps}, {"alice", "bob"})

    def test_invalid_member_rejected(self):
        r = records.push_shard(self.remote, "../evil", self._eps(1), self.cache_root)
        self.assertFalse(r["ok"])

    def test_records_jsonl_valid(self):
        records.push_shard(self.remote, "alice", self._eps(3), self.cache_root)
        cache = records.pull_records(self.remote, self.cache_root + "-pull4")
        with open(os.path.join(cache, "records", "episodes-alice.jsonl"), encoding="utf-8") as f:
            rows = [json.loads(line) for line in f if line.strip()]
        self.assertEqual(len(rows), 3)


if __name__ == "__main__":
    unittest.main()
