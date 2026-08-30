"""cli/draft.py 单元测试：frontmatter 校验拦截（结构性错误阻断写盘）+ 名称白名单。

覆盖：
- validate_frontmatter 各分支（合法 / 缺 frontmatter / 缺必填键 / 白名单外字段提示 / 非法名）
- run() 在缺 frontmatter / 缺必填键 / 非法 name 时拒绝写盘（exit 1、不落盘）
- run() 合法草稿正常落盘；--name 覆盖生效
llm.call 用 monkeypatch 模拟（决策门 pass + 草稿文本），零真实 LLM 调用。

运行：python -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cli import draft  # noqa: E402

VALID_SKILL_MD = """---
name: test-draft-skill
description: 撰写产品需求文档 PRD
when_to_use: 需要产出可评审 PRD 时
---

# test-draft-skill

## What it does
x
## When to use
y
## Steps
1. 第一步。证据引用: WIKI-4 @2026-08-28T09:17:56Z
## Examples
e
## Going deeper
g
"""


def make_args(tmp: str, name: str | None = None):
    """构造 draft.run 的 args：候选在 tmp 下，out=tmp，registry 指向不存在的文件。"""
    class A:
        pass
    a = A()
    cand_path = os.path.join(tmp, "candidates.json")
    with open(cand_path, "w", encoding="utf-8") as f:
        json.dump({"candidates": [{
            "candidate_id": "cand-001", "kind": "procedure", "episode_ids": ["ep-A", "ep-B"],
            "episodes": [{"episode_id": "ep-A", "goal": "g", "main_agent": "a", "status": "done"},
                         {"episode_id": "ep-B", "goal": "g2", "main_agent": "b", "status": "done"}],
            "contributors": {"distinct_agents": 2, "agents": ["a", "b"]},
            "evidence": [{"episode": "ep-A", "loc": "x", "preview": "y"}],
            "similarity": 0.8, "review": {"judgement": "same"},
        }]}, f, ensure_ascii=False)
    a.candidates = cand_path
    a.candidate = None
    a.out = tmp
    a.registry = os.path.join(tmp, "registry.json")  # 不存在 -> registry_names=[]
    a.name = name
    a.llm_backend = None
    return a


class _PatchedLLM:
    """上下文管理器：把 draft.llm.call 替换为 决策门 pass + 给定草稿文本。"""

    def __init__(self, draft_text: str):
        self.draft_text = draft_text
        self.orig = draft.llm.call
        self.n = 0

    def __enter__(self):
        def fake(prompt, system=None, backend=None):
            self.n += 1
            if self.n == 1:
                return {"ok": True, "text": '{"pass": true, "needs_more_evidence": false, "gates": [], "reason": "ok"}'}
            return {"ok": True, "text": self.draft_text}
        draft.llm.call = fake
        return self

    def __exit__(self, *exc):
        draft.llm.call = self.orig


class TestValidateFrontmatter(unittest.TestCase):
    def test_valid(self):
        fm, errors = draft.validate_frontmatter(VALID_SKILL_MD)
        self.assertEqual(errors, [])
        self.assertEqual(fm["name"], "test-draft-skill")

    def test_missing_frontmatter(self):
        _, errors = draft.validate_frontmatter("没有 frontmatter 的正文")
        self.assertTrue(any("缺少 frontmatter" in e for e in errors))

    def test_missing_required_keys(self):
        _, errors = draft.validate_frontmatter("---\nname: ok-slug\n---\n正文")
        self.assertTrue(any("缺 description" in e for e in errors))
        self.assertTrue(any("缺 when_to_use" in e for e in errors))

    def test_extra_key_is_warning_only(self):
        _, errors = draft.validate_frontmatter(
            "---\nname: ok-slug\ndescription: d\nwhen_to_use: w\nversion: 1.0.0\n---\n")
        self.assertTrue(any("白名单外字段" in e for e in errors))
        self.assertFalse(any("缺 description" in e for e in errors))

    def test_invalid_name_reported(self):
        _, errors = draft.validate_frontmatter(
            "---\nname: ../evil\ndescription: d\nwhen_to_use: w\n---\n")
        self.assertTrue(any("name 缺失或格式非法" in e for e in errors))

    def test_bullet_prefix_tolerated(self):
        # 回归：kimi 输出以项目符号开头（"• ---"）时应能解析（LLM 输出方差）
        fm, errors = draft.validate_frontmatter(
            "• ---\n  name: fix-gh-ssh-key-delete\n  description: d\n  when_to_use: w\n  ---\n正文")
        self.assertEqual(errors, [])
        self.assertEqual(fm["name"], "fix-gh-ssh-key-delete")

    def test_parse_skill_md_strips_leading_bullet(self):
        self.assertEqual(draft.parse_skill_md("• ---\nname: x\n---\n正文"), "---\nname: x\n---\n正文")


class TestRunBlocksOnStructuralErrors(unittest.TestCase):
    def test_missing_frontmatter_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            with _PatchedLLM("```markdown\n正文无 frontmatter\n```"):
                rc = draft.run(make_args(tmp))
            self.assertEqual(rc, 1, "缺 frontmatter 应拒绝写盘")
            self.assertEqual(sorted(os.listdir(tmp)), ["candidates.json"], "不应创建 skill 目录")

    def test_missing_keys_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            with _PatchedLLM("---\nname: ok-slug\n---\n正文"):
                rc = draft.run(make_args(tmp))
            self.assertEqual(rc, 1, "缺 description/when_to_use 应拒绝")
            self.assertEqual(sorted(os.listdir(tmp)), ["candidates.json"])

    def test_invalid_llm_name_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            with _PatchedLLM("---\nname: ../evil\ndescription: d\nwhen_to_use: w\n---\n正文"):
                rc = draft.run(make_args(tmp))
            self.assertEqual(rc, 1, "非法 name 应拒绝写盘")
            self.assertEqual(sorted(os.listdir(tmp)), ["candidates.json"])

    def test_valid_draft_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            with _PatchedLLM(VALID_SKILL_MD):
                rc = draft.run(make_args(tmp))
            self.assertEqual(rc, 0, "合法草稿应成功")
            skill_dir = os.path.join(tmp, "test-draft-skill")
            self.assertTrue(os.path.isfile(os.path.join(skill_dir, "SKILL.md")))
            self.assertTrue(os.path.isdir(os.path.join(skill_dir, "references")))
            self.assertTrue(os.path.isdir(os.path.join(skill_dir, "evals", "cases")))

    def test_name_override_used(self):
        with tempfile.TemporaryDirectory() as tmp:
            with _PatchedLLM(VALID_SKILL_MD):
                rc = draft.run(make_args(tmp, name="forced-name"))
            self.assertEqual(rc, 0)
            self.assertTrue(os.path.isdir(os.path.join(tmp, "forced-name")))
            self.assertFalse(os.path.isdir(os.path.join(tmp, "test-draft-skill")))
            # frontmatter name 应同步改写为强制名，目录名与 SKILL.md 一致
            with open(os.path.join(tmp, "forced-name", "SKILL.md"), encoding="utf-8") as f:
                self.assertIn("name: forced-name", f.read())


if __name__ == "__main__":
    unittest.main()
