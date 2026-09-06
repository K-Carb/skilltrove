"""成员侧来源扫描（探测报告）的学习测试：覆盖率计算与未知来源降级。"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cli.scan import _coverage, scan_sources  # noqa: E402


def _write_session(path: str, rows: list[dict]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


class CoverageTests(unittest.TestCase):
    def test_coverage_fractions(self):
        eps = [
            {"goal": "完整的调研目标文本足够长", "status": "done", "main_agent": "bob", "comments": [1]},
            {"goal": "", "status": "", "main_agent": "", "comments": []},
        ]
        cov = _coverage(eps)
        self.assertEqual(cov["样本数"], 2)
        self.assertEqual(cov["goal 文本"], 0.5)
        self.assertEqual(cov["完成态已知"], 0.5)
        self.assertEqual(cov["归属者"], 0.5)

    def test_empty_sample_safe(self):
        cov = _coverage([])
        self.assertEqual(cov["样本数"], 0)
        self.assertEqual(cov["goal 文本"], 0)


class ScanSourcesTests(unittest.TestCase):
    def test_table_dir_detected_with_coverage(self):
        with tempfile.TemporaryDirectory() as td:
            table = [{"id": f"WIKI-{i}", "title": f"任务 {i} 的标题文本", "status": "done",
                      "assignee": "bob", "comments": [{"agent": "bob", "text": "完成了"}]}
                     for i in range(1, 4)]
            with open(os.path.join(td, "tasks.json"), "w", encoding="utf-8") as f:
                json.dump(table, f, ensure_ascii=False)
            reports = scan_sources(extra_dirs=[td])
            hit = [r for r in reports if r["shape"] == "table" and r.get("usable")]
            self.assertTrue(hit, reports)
            cov = hit[0]["coverage"]
            self.assertEqual(cov["样本数"], 3)
            self.assertEqual(cov["完成态已知"], 1.0)

    def test_unknown_dir_degrades_not_raises(self):
        with tempfile.TemporaryDirectory() as td:
            open(os.path.join(td, "随机文件.bin"), "wb").write(b"\x00\x01")
            reports = scan_sources(extra_dirs=[td])
            # 随机二进制要么不可识别（不在报告里），要么报告为不可用——都不应抛异常
            for r in reports:
                self.assertIn("usable", r)

    def test_gh_source_bypasses_scan(self):
        # gh: 前缀是用户显式配置的来源（token 已就位），不进入目录扫描；
        # 扫描器对 gh: 的职责只是确认适配器认识它
        from cli.adapters import GitHubAdapter
        self.assertTrue(GitHubAdapter().detect("gh:acme/wiki"))


if __name__ == "__main__":
    unittest.main()
