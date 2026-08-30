"""cli/verify.py 单元测试：DoD 验收的路径语义（--root 与 CWD 无关）+ DoD4 实证检查。

覆盖：
- runs_dir / _latest_recall 基于 root 定位（回归：旧实现查 CWD 导致 DoD3 假失败）
- 从外部 CWD 跑 run()：报告应落 root/docs/ 而非 CWD/docs/
- DoD4 真实校验：六步子命令注册 + 模块可导入；缺子命令时应判失败

运行：python -m unittest discover -s tests -v
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from cli import verify  # noqa: E402


def make_fake_root(tmp: str) -> str:
    """构造最小可验收产物树（candidates/registry/scored/recall-run/skill）。"""
    root = os.path.join(tmp, "fake-skilltrove")
    os.makedirs(os.path.join(root, "data", "recall-runs", "recall-run-20260819-160818"), exist_ok=True)
    os.makedirs(os.path.join(root, "archive"), exist_ok=True)
    os.makedirs(os.path.join(root, "registry"), exist_ok=True)
    os.makedirs(os.path.join(root, "skills", "demo-skill", "evals", "cases", "demo-skill-001"), exist_ok=True)

    with open(os.path.join(root, "data", "candidates.json"), "w", encoding="utf-8") as f:
        json.dump({"candidates": [{"candidate_id": "cand-001",
                                   "episode_ids": ["ep-A", "ep-B", "ep-C"],
                                   "contributors": {"distinct_agents": 2, "agents": ["a", "b"]},
                                   "evidence": [{"episode": "ep-A", "loc": "x", "preview": "y"}]}]}, f)
    with open(os.path.join(root, "registry", "registry.json"), "w", encoding="utf-8") as f:
        json.dump({"schema": "skilltrove-registry-v1", "skills": [{
            "name": "demo-skill", "version": "1.0.0", "review_status": "published",
            "source_runs": ["WIKI-1"], "contributors": {"distinct_agents": 2, "agents": ["a", "b"]},
            "path": "skills/demo-skill"}]}, f)
    with open(os.path.join(root, "archive", "scored.jsonl"), "w", encoding="utf-8") as f:
        f.write('{"episode_id": "ep-A", "score": {"label": "high"}}\n')
    with open(os.path.join(root, "data", "recall-runs", "recall-run-20260819-160818", "result.json"),
              "w", encoding="utf-8") as f:
        json.dump({"run_id": "recall-run-20260819-160818", "skill_id": "demo-skill",
                   "agent_id": "claude", "applied": True}, f)
    return root


class TestRunsDir(unittest.TestCase):
    def test_runs_dir_joins_root(self):
        self.assertEqual(verify.runs_dir("/some/root"),
                         os.path.join("/some/root", "data", "recall-runs"))

    def test_latest_recall_under_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_fake_root(tmp)
            self.assertEqual(verify._latest_recall(root), "recall-run-20260819-160818")

    def test_latest_recall_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(verify._latest_recall(tmp))


class TestRunFromExternalCwd(unittest.TestCase):
    """回归：外部 CWD + 绝对 --root 时，DoD3 应 PASS 且报告落 root/docs/。"""

    def test_report_lands_in_root_docs_from_foreign_cwd(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_fake_root(tmp)
            foreign = os.path.join(tmp, "foreign-cwd")
            os.makedirs(foreign, exist_ok=True)
            old = os.getcwd()
            os.chdir(foreign)
            try:
                class A:
                    pass
                a = A()
                a.root = root
                rc = verify.run(a)
            finally:
                os.chdir(old)

            self.assertEqual(rc, 0, "外部 CWD 下 DoD 五条应全部 PASS")
            report = os.path.join(root, "docs", "dod-report.json")
            self.assertTrue(os.path.isfile(report), "报告应落在 root/docs/")
            self.assertFalse(os.path.isfile(os.path.join(foreign, "docs", "dod-report.json")),
                             "报告不应落在 CWD 的 docs/")
            with open(report, encoding="utf-8") as f:
                data = json.load(f)
            items = {i["item"]: i for i in data["items"]}
            self.assertTrue(items["DoD3 跨 agent 调用（调用者≠沉淀者）"]["pass"],
                            "DoD3 在外部 CWD 下应 PASS（旧实现会因查 CWD 假失败）")
            self.assertEqual(data["pass"], 5)


class TestCheckCliSteps(unittest.TestCase):
    def test_all_steps_available(self):
        ok, ok_steps, missing = verify.check_cli_steps()
        self.assertTrue(ok, f"六步子命令应全部可用，缺失: {missing}")
        self.assertEqual(len(ok_steps), 6)

    def test_missing_step_detected(self):
        # 注册表少一个子命令时 DoD4 应判失败（monkeypatch build_parser）
        import cli.main as main_mod
        orig = main_mod.build_parser

        def broken():
            p = orig()
            subs = next(a for a in p._actions
                        if isinstance(a, argparse._SubParsersAction))
            del subs.choices["recall"]
            return p

        main_mod.build_parser = broken
        try:
            ok, ok_steps, missing = verify.check_cli_steps()
            self.assertFalse(ok)
            self.assertIn("recall", missing)
            self.assertEqual(len(ok_steps), 5)
        finally:
            main_mod.build_parser = orig

    def test_module_import_failure_detected(self):
        # 模块无法导入时该步骤也应判缺失（monkeypatch importlib.import_module）
        import cli.main as main_mod
        import importlib
        orig = main_mod.build_parser
        orig_import = importlib.import_module

        def broken():
            return orig()

        def broken_import(name, *a, **k):
            if name == "cli.sync":  # recall 步骤的实现模块
                raise ImportError("模拟 recall 实现模块缺失")
            return orig_import(name, *a, **k)

        main_mod.build_parser = broken
        importlib.import_module = broken_import
        try:
            ok, ok_steps, missing = verify.check_cli_steps()
            self.assertFalse(ok)
            self.assertIn("recall", missing)
        finally:
            main_mod.build_parser = orig
            importlib.import_module = orig_import


if __name__ == "__main__":
    unittest.main()
