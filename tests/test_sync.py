"""cli/sync.py 单元测试：SKILL.md frontmatter / Steps 提取 / 核心短语（recall 判定基础）。

运行：python -m unittest discover -s tests -v
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cli import sync  # noqa: E402

SAMPLE_MD = """---
name: implementation-research
description: 全面调研
version: 1.0.0
---

## What it does

目标对齐。

## Steps

1. **确认输入与验收口径**：以背景文档为唯一输入
2. 校准定位与 framing：动笔前明确对象定位
3. 传播事实更正

## Examples

- 示例一
"""


class TestReadFrontmatter(unittest.TestCase):
    def test_parse_fields(self):
        fm = sync._read_frontmatter(SAMPLE_MD)
        self.assertEqual(fm["name"], "implementation-research")
        self.assertEqual(fm["version"], "1.0.0")

    def test_no_frontmatter(self):
        self.assertEqual(sync._read_frontmatter("plain"), {})


class TestExtractSteps(unittest.TestCase):
    def test_extract_numbered_steps(self):
        steps = sync._extract_steps(SAMPLE_MD)
        self.assertEqual(len(steps), 3)
        self.assertTrue(all(s.startswith(("1.", "2.", "3.")) for s in steps))

    def test_stop_at_next_section(self):
        steps = sync._extract_steps(SAMPLE_MD)
        # Examples 段不应被当作 Steps
        self.assertFalse(any("示例" in s for s in steps))


class TestCorePhrase(unittest.TestCase):
    def test_strip_number_and_markdown(self):
        self.assertEqual(sync._core_phrase("1. **确认输入与验收口径**：详细说明"),
                         "确认输入与验收口径")

    def test_plain_step(self):
        self.assertEqual(sync._core_phrase("3. 传播事实更正"), "传播事实更正")

    def test_short_phrase_ignored_in_applied(self):
        # 短语过短（<4 字符）不参与 applied 判定
        phrase = sync._core_phrase("5. 交付")
        self.assertEqual(phrase, "交付")
        self.assertLess(len(phrase), 4)


class TestRecurringHelpers(unittest.TestCase):
    def test_frontmatter_roundtrip_consistency(self):
        # _extract_steps 与 _read_frontmatter 在同一文本上协同
        fm = sync._read_frontmatter(SAMPLE_MD)
        steps = sync._extract_steps(SAMPLE_MD)
        self.assertIn("name", fm)
        self.assertGreaterEqual(len(steps), 1)


if __name__ == "__main__":
    unittest.main()
