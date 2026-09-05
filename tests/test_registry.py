"""cli/registry.py 单元测试：条目查找 / 审核状态机 / 校验 / 查重 / frontmatter。

运行：python -m unittest discover -s tests -v
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cli import registry  # noqa: E402


def make_entry(name: str = "demo-skill", status: str = "draft") -> dict:
    return {
        "name": name,
        "version": "1.0.0",
        "owner": "team",
        "layer": "shared",
        "action_level": "advice",
        "author": "SkillTrove",
        "source_runs": ["WIKI-1"],
        "contributors": {"distinct_agents": 2, "agents": ["a", "b"]},
        "review_status": status,
        "usage": {"applied_count": 0, "distinct_appliers": 0},
        "path": f"skills/{name}",
    }


def make_registry(*entries) -> dict:
    return {"schema": registry.SCHEMA, "skills": list(entries)}


class TestFindEntry(unittest.TestCase):
    def test_found(self):
        e = make_entry()
        r = make_registry(e)
        self.assertIs(registry.find_entry(r, "demo-skill"), e)

    def test_not_found(self):
        self.assertIsNone(registry.find_entry(make_registry(), "nope"))


class TestSetStatus(unittest.TestCase):
    def test_forward_migration(self):
        r = make_registry(make_entry(status="draft"))
        registry.set_status(r, "demo-skill", "in_review")
        self.assertEqual(r["skills"][0]["review_status"], "in_review")

    def test_full_chain(self):
        r = make_registry(make_entry(status="draft"))
        for s in ("in_review", "published", "deprecated"):
            registry.set_status(r, "demo-skill", s)
        self.assertEqual(r["skills"][0]["review_status"], "deprecated")

    def test_idempotent_same_status(self):
        r = make_registry(make_entry(status="published"))
        registry.set_status(r, "demo-skill", "published")  # 不抛错
        self.assertEqual(r["skills"][0]["review_status"], "published")

    def test_reversible_transitions(self):
        # 撤销/反悔是产品行为：状态图全连通（含回退边）
        r = make_registry(make_entry(status="published"))
        registry.set_status(r, "demo-skill", "draft")  # 发布后撤销 → 回草稿
        self.assertEqual(r["skills"][0]["review_status"], "draft")
        registry.set_status(r, "demo-skill", "published")  # 草稿可直接发布
        self.assertEqual(r["skills"][0]["review_status"], "published")
        registry.set_status(r, "demo-skill", "deprecated")  # 打回
        registry.set_status(r, "demo-skill", "published")  # 撤销打回 → 恢复发布
        self.assertEqual(r["skills"][0]["review_status"], "published")

    def test_unknown_status(self):
        r = make_registry(make_entry())
        with self.assertRaises(ValueError):
            registry.set_status(r, "demo-skill", "weird")

    def test_missing_entry(self):
        with self.assertRaises(ValueError):
            registry.set_status(make_registry(), "nope", "published")

    def test_unknown_status_does_not_mutate(self):
        # 失败迁移不产生副作用：非法状态被拒后，registry 保持原样
        r = make_registry(make_entry(status="draft"))
        with self.assertRaises(ValueError):
            registry.set_status(r, "demo-skill", "weird")
        self.assertEqual(r["skills"][0]["review_status"], "draft")


class TestValidateEntry(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(registry.validate_entry(make_entry()), [])

    def test_missing_required(self):
        errors = registry.validate_entry({"name": "x"})
        self.assertIn("缺必填字段: version", errors)

    def test_bad_name(self):
        errors = registry.validate_entry(make_entry(name="  "))
        self.assertTrue(any("name" in e for e in errors))

    def test_bad_review_status(self):
        errors = registry.validate_entry(make_entry(status="weird"))
        self.assertTrue(any("review_status" in e for e in errors))

    def test_bad_contributors(self):
        errors = registry.validate_entry(make_entry() | {"contributors": {"distinct_agents": -1}})
        self.assertTrue(any("distinct_agents" in e for e in errors))


class TestBigramSimilarity(unittest.TestCase):
    def test_identical(self):
        self.assertAlmostEqual(registry.bigram_similarity("调研报告", "调研报告"), 1.0)

    def test_empty(self):
        self.assertEqual(registry.bigram_similarity("", "调研"), 0.0)


class TestFindDuplicate(unittest.TestCase):
    def test_same_name_any_status(self):
        e1 = make_entry("same", status="draft")
        e2 = make_entry("same", status="in_review")
        r = make_registry(e1, e2)
        dups = registry.find_duplicate(r, "same", "desc")
        self.assertEqual(len(dups), 2)

    def test_different_name_no_desc_no_dup(self):
        r = make_registry(make_entry())
        self.assertEqual(registry.find_duplicate(r, "other", ""), [])

    def test_similar_description_published(self):
        # 构造已发布条目 + 同描述 → bigram 命中
        published = make_entry("existing", status="published")
        r = make_registry(published)
        with tempfile.TemporaryDirectory() as tmp:
            # 无 skills_root 时不走描述匹配
            dups = registry.find_duplicate(r, "new", "一模一样的描述内容", tmp)
            # published 条目描述取自 SKILL.md，缺失则为空 → 不命中
            self.assertEqual(dups, [])

    def test_similar_description_with_skill_md(self):
        published = make_entry("existing", status="published")
        r = make_registry(published)
        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = os.path.join(tmp, "existing")
            os.makedirs(skill_dir, exist_ok=True)
            with open(os.path.join(skill_dir, "SKILL.md"), "w", encoding="utf-8") as f:
                f.write("---\nname: existing\ndescription: 全面调研参考对象机制拆解产出报告\n---\n")
            dups = registry.find_duplicate(r, "new", "全面调研参考对象机制拆解产出报告", tmp)
            self.assertEqual(len(dups), 1)
            self.assertEqual(dups[0]["name"], "existing")


class TestParseFrontmatter(unittest.TestCase):
    def test_basic(self):
        md = "---\nname: demo\ndescription: 描述\n---\n正文"
        fm = registry.parse_frontmatter(md)
        self.assertEqual(fm["name"], "demo")
        self.assertEqual(fm["description"], "描述")

    def test_no_frontmatter(self):
        self.assertEqual(registry.parse_frontmatter("plain text"), {})


class TestLoadSaveRoundtrip(unittest.TestCase):
    def test_roundtrip(self):
        r = make_registry(make_entry(status="published"))
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "registry.json")
            registry.save_registry(r, path)
            loaded = registry.load_registry(path)
            self.assertEqual(loaded["schema"], registry.SCHEMA)
            self.assertEqual(loaded["skills"][0]["name"], "demo-skill")
            self.assertEqual(loaded["skills"][0]["review_status"], "published")

    def test_load_missing_returns_empty(self):
        loaded = registry.load_registry("nonexistent.json")
        self.assertEqual(loaded["schema"], registry.SCHEMA)
        self.assertEqual(loaded["skills"], [])


class TestValidateSkillName(unittest.TestCase):
    """skill 名路径白名单（防路径穿越）。"""

    def test_valid_slugs(self):
        for name in ("implementation-research", "a", "a1", "a-b-c", "test-skill-001"):
            self.assertEqual(registry.validate_skill_name(name), [], f"{name} 应合法")

    def test_path_traversal_rejected(self):
        for name in ("../../pwned", "../evil", "..", "skills/../x", "a/b", "a\\b", "a:b",
                     "a b", "A-B", "-abc", ".hidden", "a..b"):
            self.assertTrue(registry.validate_skill_name(name), f"{name} 应被拒绝")

    def test_empty_rejected(self):
        self.assertTrue(registry.validate_skill_name(""))
        self.assertTrue(registry.validate_skill_name(None))


class TestCheckSkillFolderNameGuard(unittest.TestCase):
    def test_traversal_name_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            errors = registry.check_skill_folder(tmp, "../../evil")
            self.assertTrue(any("非法" in e or "路径" in e for e in errors))

    def test_valid_name_missing_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            errors = registry.check_skill_folder(tmp, "not-exist")
            self.assertTrue(any("不存在" in e for e in errors))


class TestPublishReviewHelperNameGuard(unittest.TestCase):
    def test_publish_rejects_traversal(self):
        rc = registry.publish(argparse.Namespace(skill="../../evil", status="published", registry=None))
        self.assertEqual(rc, 1)

    def test_review_helper_rejects_traversal(self):
        rc = registry.review_helper(argparse.Namespace(skill="../evil", registry=None))
        self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()
