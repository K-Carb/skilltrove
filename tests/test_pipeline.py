"""web/pipeline.py 单元测试：步骤→命令构造 / job 快照 / 日志游标。

运行：python -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest

sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))

from web import pipeline  # noqa: E402


class TestBuildCommand(unittest.TestCase):
    def test_export_default_adapter(self):
        cmd = pipeline.build_command("export", {"source": "/data/x"})
        self.assertEqual(cmd[0], sys.executable)
        self.assertIn("export", cmd)
        self.assertNotIn("--adapter", cmd)  # auto 不传

    def test_export_explicit_adapter_and_exclude(self):
        cmd = pipeline.build_command("export", {"source": "/d", "adapter": "table", "exclude_agents": "bot"})
        self.assertIn("--adapter", cmd)
        self.assertEqual(cmd[cmd.index("--adapter") + 1], "table")
        self.assertEqual(cmd[cmd.index("--exclude-agents") + 1], "bot")

    def test_score_fixed_paths(self):
        cmd = pipeline.build_command("score", {})
        self.assertEqual(cmd[cmd.index("score") + 1:cmd.index("score") + 8],
                         ["--episodes", "archive/episodes.jsonl", "--manifest",
                          "archive/manifest.json", "--out", "archive/scored.jsonl"])

    def test_cluster_threshold_and_backend(self):
        cmd = pipeline.build_command("cluster", {"threshold": 0.4, "llm_backend": "kimi"})
        self.assertEqual(cmd[cmd.index("--threshold") + 1], "0.4")
        self.assertEqual(cmd[cmd.index("--llm-backend") + 1], "kimi")

    def test_cluster_no_threshold(self):
        cmd = pipeline.build_command("cluster", {})
        self.assertNotIn("--threshold", cmd)

    def test_draft_backend(self):
        cmd = pipeline.build_command("draft", {"llm_backend": "kimi"})
        self.assertEqual(cmd[cmd.index("--llm-backend") + 1], "kimi")

    def test_draft_candidate(self):
        # 跨视图跳转：从候选视图"起草此候选"→ draft --candidate <id>
        cmd = pipeline.build_command("draft", {"candidate": "cand-002", "llm_backend": "kimi"})
        self.assertEqual(cmd[cmd.index("--candidate") + 1], "cand-002")
        cmd2 = pipeline.build_command("draft", {})
        self.assertNotIn("--candidate", cmd2)

    def test_publish_skill_and_status(self):
        cmd = pipeline.build_command("publish", {"skill": "s1", "status": "deprecated"})
        self.assertEqual(cmd[cmd.index("--skill") + 1], "s1")
        self.assertEqual(cmd[cmd.index("--status") + 1], "deprecated")

    def test_recall_skill_backend_agent(self):
        cmd = pipeline.build_command("recall", {"skill": "s1", "llm_backend": "kimi", "agent": "kimi"})
        self.assertEqual(cmd[cmd.index("--skill") + 1], "s1")
        self.assertEqual(cmd[cmd.index("--agent") + 1], "kimi")

    def test_unknown_step_raises(self):
        with self.assertRaises(ValueError):
            pipeline.build_command("nope", {})

    def test_job_timeout_env(self):
        old = __import__("os").environ.get("SKILLTROVE_PIPELINE_TIMEOUT")
        __import__("os").environ["SKILLTROVE_PIPELINE_TIMEOUT"] = "60"
        try:
            self.assertEqual(pipeline._job_timeout(), 60)
        finally:
            if old is None:
                __import__("os").environ.pop("SKILLTROVE_PIPELINE_TIMEOUT", None)
            else:
                __import__("os").environ["SKILLTROVE_PIPELINE_TIMEOUT"] = old
        self.assertGreaterEqual(pipeline._job_timeout(), 10)  # 默认 1800
        self.assertEqual(pipeline._job_timeout(), pipeline._job_timeout())


def _wait_terminal(run_id: str, timeout_s: float = 5.0):
    """有界轮询直到 run 进入终态（success/failed/cancelled），返回最终快照。

    F.I.R.S.T 的 Fast/Repeatable 落实：不盲等，超时返回最后快照由断言判失败。
    """
    import time
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        snap = pipeline.get_job(run_id)
        if snap["status"] in ("success", "failed", "cancelled"):
            return snap
        time.sleep(0.05)
    return pipeline.get_job(run_id)


class TestJobStore(unittest.TestCase):
    def test_start_get_snapshot_shape(self):
        run_id = pipeline.start_run(["export", "score"], {"source": "/tmp/x"})
        snap = pipeline.get_job(run_id)
        self.assertEqual(snap["id"], run_id)
        self.assertEqual(snap["steps"], ["export", "score"])
        self.assertEqual(len(snap["step_states"]), 2)
        self.assertIn(snap["status"], ("queued", "running", "success", "failed", "cancelled"))
        # 等它跑完（export 对不存在路径会失败——正好测 failed 路径；不依赖真实数据）
        final = _wait_terminal(run_id)
        self.assertIn(final["status"], ("success", "failed"))
        self.assertIsNotNone(final["finished"])
        self.assertIsNotNone(final["duration"])

    def test_logs_since_cursor(self):
        run_id = pipeline.start_run(["export"], {"source": "/definitely-not-exist-xyz"})
        _wait_terminal(run_id)
        d0 = pipeline.logs_since(run_id, 0)
        self.assertIsNotNone(d0)
        self.assertTrue(d0["done"])
        n = d0["next"]
        self.assertGreater(n, 0)
        d1 = pipeline.logs_since(run_id, n)  # 游标到尾部 → 无增量
        self.assertEqual(d1["lines"], [])
        self.assertEqual(d1["next"], n)
        d2 = pipeline.logs_since(run_id, n + 5)  # 越界游标 → 空且 next 不变
        self.assertEqual(d2["lines"], [])
        self.assertEqual(d2["next"], n)

    def test_cancel_unknown_returns_false(self):
        self.assertFalse(pipeline.cancel_run("nope"))

    def test_list_jobs_sorted(self):
        pipeline.start_run(["export"], {"source": "/a"})
        runs = pipeline.list_jobs()
        self.assertGreaterEqual(len(runs), 1)
        times = [r["started"] or 0 for r in runs]
        self.assertEqual(times, sorted(times, reverse=True))


if __name__ == "__main__":
    unittest.main()
