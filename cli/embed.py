"""纯 Python 文本向量：字符 bigram+trigram 混合 TF-IDF + 余弦相似度。零外部依赖。

用途：F2 聚类粗筛（Spec §5.2）。
语料选择（cluster.py 决定）：title + description（任务定义域），避免正文通用术语噪声。
算法实测结论（AGEN 语料）：mixed = 0.6*bigram + 0.4*trigram，默认阈值 0.55，
真同类（WIKI-4/5 全面调研）0.67 居首，假阳性对由 LLM 四维复核拆分。
"""

from __future__ import annotations

import math
from collections import Counter

_BIGRAM_W = 0.6
_TRIGRAM_W = 0.4


def ngrams(text: str, n: int) -> list[str]:
    """字符 n-gram 切分（去空白，小写）。短于 n 时退化。"""
    chars = [c.lower() for c in text if not c.isspace()]
    if len(chars) < n:
        return [chars[0]] if chars else []
    return ["".join(chars[i:i + n]) for i in range(len(chars) - n + 1)]


def bigrams(text: str) -> list[str]:
    return ngrams(text, 2)


def trigrams(text: str) -> list[str]:
    return ngrams(text, 3)


def cosine(a: Counter, b: Counter) -> float:
    if not a or not b:
        return 0.0
    dot = sum(a[g] * b[g] for g in a if g in b)
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def similarity(a_text: str, b_text: str) -> float:
    """两段文本的混合相似度（bigram+trigram 余弦）。"""
    s2 = cosine(Counter(bigrams(a_text)), Counter(bigrams(b_text)))
    s3 = cosine(Counter(trigrams(a_text)), Counter(trigrams(b_text)))
    return _BIGRAM_W * s2 + _TRIGRAM_W * s3


def pairs_above(docs: list[str], threshold: float = 0.5) -> list[tuple[int, int, float]]:
    """返回 (i, j, score) 且 score >= threshold 的相似配对，按 score 降序。"""
    n = len(docs)
    pairs = []
    for i in range(n):
        for j in range(i + 1, n):
            s = similarity(docs[i], docs[j])
            if s >= threshold:
                pairs.append((i, j, round(s, 4)))
    pairs.sort(key=lambda x: x[2], reverse=True)
    return pairs


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")

    # 冒烟自测：语料形态 = cluster.py 实际输入（title + description 任务定义域）
    same_a = ("WIKI-2 分页方案调研（bob）：offset 与 cursor 分页的取舍对比。"
              "目标：全面调研三个参考对象的经验机制，产出调研报告，供后续 PRD 使用。")
    same_b = ("WIKI-3 搜索选型调研（carol）：数据库检索与倒排索引的路线对比。"
              "目标：全面调研参考对象并核实证据，产出调研报告，供后续 PRD 使用。")
    diff = ("WIKI-4 S2：本地 MVP PRD（dave 主笔）。"
            "目标：编写本地 MVP 产品需求文档，定义功能点与验收标准。")

    s_same = similarity(same_a, same_b)
    s_diff = similarity(same_a, diff)
    print(f"同类调研 sim={s_same:.3f} (应>0.5)")
    print(f"调研 vs PRD sim={s_diff:.3f} (应<0.5)")
    assert s_same > 0.5, "同类任务应高于阈值"
    assert s_diff < 0.5, "不同任务应低于阈值"
    assert similarity(same_a, same_a) == 1.0

    docs = [same_a, same_b, diff]
    pairs = pairs_above(docs, 0.5)
    print("pairs:", pairs)
    assert pairs and pairs[0][:2] == (0, 1), "应只有同类对配对"
    print("embed smoke OK")
