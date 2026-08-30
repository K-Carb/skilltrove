"""cli/embed.py 单元测试：n-gram 切分 / 余弦 / 相似度 / 配对。

运行：python -m unittest discover -s tests -v
"""

from __future__ import annotations

import os
import sys
import unittest
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cli import embed  # noqa: E402


class TestNgrams(unittest.TestCase):
    def test_bigrams(self):
        self.assertEqual(embed.bigrams("abc"), ["ab", "bc"])

    def test_trigrams(self):
        self.assertEqual(embed.trigrams("abcd"), ["abc", "bcd"])

    def test_ngrams_short_degrade(self):
        # 长度不足退化为单字符
        self.assertEqual(embed.ngrams("a", 2), ["a"])

    def test_ngrams_empty(self):
        self.assertEqual(embed.ngrams("", 2), [])

    def test_ngrams_lowercase_and_strip_space(self):
        self.assertEqual(embed.ngrams("A B", 2), ["ab"])


class TestCosine(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(embed.cosine(Counter(), Counter()), 0.0)

    def test_identical(self):
        c = Counter(["ab", "bc"])
        self.assertAlmostEqual(embed.cosine(c, c), 1.0, places=9)

    def test_disjoint(self):
        self.assertEqual(embed.cosine(Counter(["ab"]), Counter(["cd"])), 0.0)

    def test_partial_overlap(self):
        a = Counter(["ab", "bc"])
        b = Counter(["ab", "cd"])
        self.assertGreater(embed.cosine(a, b), 0.0)
        self.assertLess(embed.cosine(a, b), 1.0)


class TestSimilarity(unittest.TestCase):
    def test_self_similar(self):
        self.assertEqual(embed.similarity("测试文本", "测试文本"), 1.0)

    def test_similar_greater_than_dissimilar(self):
        a = "全面调研参考对象的经验机制拆解产出调研报告"
        b = "全面调研参考对象的证据核实产出调研报告"
        c = "编写产品需求文档定义功能点与验收标准"
        self.assertGreater(embed.similarity(a, b), embed.similarity(a, c))

    def test_symmetry(self):
        a, b = "甲文本内容", "乙文本内容"
        self.assertAlmostEqual(embed.similarity(a, b), embed.similarity(b, a))


class TestPairsAbove(unittest.TestCase):
    def test_pairs_filter_and_sort(self):
        docs = ["同类任务调研报告", "同类任务调研报告", "完全不同的需求文档"]
        pairs = embed.pairs_above(docs, 0.3)
        self.assertTrue(pairs)
        self.assertEqual(pairs[0][:2], (0, 1))  # 最相似的同类对排第一
        scores = [p[2] for p in pairs]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_threshold_no_pairs(self):
        self.assertEqual(embed.pairs_above(["abc", "xyz"], 0.99), [])

    def test_empty_docs(self):
        self.assertEqual(embed.pairs_above([], 0.5), [])


if __name__ == "__main__":
    unittest.main()
