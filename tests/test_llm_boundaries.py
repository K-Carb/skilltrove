"""第三方解析边界的学习测试（Clean Code Ch8）：钉住外部数据格式的真实解析行为。

这些纯函数位于系统与第三方数据的接缝上（codex CLI 事件流 / 外部 markdown 导出），
行为被本文件钉住：若上游格式变化，最先在这里报警，而不是在业务深处神秘失败。
"""

from __future__ import annotations

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cli.adapters import parse_comments_md, parse_description_md, status_normalize  # noqa: E402
from cli import llm as cli_llm  # noqa: E402
from cli.llm import _extract_codex_text  # noqa: E402


class CodexTextExtractionTests(unittest.TestCase):
    """钉住 _extract_codex_text 对 codex exec --json 事件流的解析契约。"""

    def test_reasoning_and_message_both_collected(self):
        stdout = "\n".join([
            '{"type":"response_item","payload":{"type":"reasoning","content":[{"type":"output_text","text":"思路"}]}}',
            '{"type":"response_item","payload":{"type":"message","content":[{"type":"output_text","text":"结论"}]}}',
        ])
        self.assertEqual(_extract_codex_text(stdout), "思路\n结论")

    def test_legacy_single_object_output(self):
        # 实测契约：旧版兼容要求 "result" 键存在；"output" 仅作同对象内的空值兜底
        self.assertEqual(_extract_codex_text('{"result": "旧版输出"}'), "旧版输出")
        self.assertEqual(_extract_codex_text('{"result": null, "output": "兜底字段"}'), "兜底字段")

    def test_empty_and_garbage_fall_back_to_raw(self):
        self.assertEqual(_extract_codex_text(""), "")
        self.assertEqual(_extract_codex_text("not json at all"), "not json at all")

    def test_function_call_only_output_falls_back_to_raw(self):
        # 实测契约（怪癖记录）：仅有 function_call 项时提取不到文本，走"解析失败原样返回"兜底。
        # 若未来想改为返回空串，应先改 cli/llm.py 再更新本测试。
        stdout = '{"type":"response_item","payload":{"type":"function_call","name":"read"}}'
        self.assertEqual(_extract_codex_text(stdout), stdout)


class MarkdownBoundaryTests(unittest.TestCase):
    """钉住外部 markdown 导出文件的解析契约。"""

    def test_parse_comments_md_splits_by_header(self):
        md = (
            "## 2026-08-28T03:38:38Z | alice\n第一段结论\n第二行细节\n"
            "## 2026-08-28T05:00:00Z | bob\n只一行\n"
        )
        comments = parse_comments_md(md)
        self.assertEqual(len(comments), 2)
        self.assertEqual(comments[0]["ts"], "2026-08-28T03:38:38Z")
        self.assertEqual(comments[0]["agent"], "alice")
        self.assertEqual(comments[0]["text"], "第一段结论\n第二行细节")
        self.assertEqual(comments[1]["agent"], "bob")

    def test_parse_comments_md_ignores_leading_orphan_text(self):
        # 头部之前的散行不属于任何评论，被丢弃（正文内 ## 小节标题天然免疫）
        md = "前言散行\n## 2026-08-28T03:38:38Z | alice\n正文\n"
        comments = parse_comments_md(md)
        self.assertEqual(len(comments), 1)
        self.assertEqual(comments[0]["text"], "正文")

    def test_parse_description_md_strips_headings(self):
        body = parse_description_md("# 标题\n\n正文第一段\n## 小节\n正文第二段\n")
        self.assertEqual(body, "正文第一段\n正文第二段")

    def test_status_normalize_folds_synonyms_to_done(self):
        for raw in ("done", "DONE", "已完成", "closed", "merged"):
            self.assertEqual(status_normalize(raw), "done")
        self.assertEqual(status_normalize("in_progress"), "in_progress")
        self.assertEqual(status_normalize(None), "")


if __name__ == "__main__":
    unittest.main()


class LogInjectionResistanceTests(unittest.TestCase):
    """ASVS V7.1：含换行/引号/控制字符的内容进日志后，文件必须仍是合法 JSONL。"""

    def setUp(self):
        import tempfile
        self.td = tempfile.TemporaryDirectory()
        self.fake = os.path.join(self.td.name, "llm-log.jsonl")
        self._orig = cli_llm._LOG_PATH
        cli_llm._LOG_PATH = self.fake

    def tearDown(self):
        cli_llm._LOG_PATH = self._orig
        self.td.cleanup()

    def test_hostile_prompt_stays_valid_jsonl(self):
        # ASVS V7.1：换行/引号/反斜杠/制表/ANSI 转义全部原样入库（json 转义而非剥离），
        # 且文件保持逐行合法 JSONL（日志注入抵抗）
        from cli import llm
        nl, esc, tab = chr(10), chr(27), chr(9)
        bs = chr(92)
        hostile = {
            "call_id": "x1",
            "prompt_preview": "第一行" + nl + "第二行 " + chr(34) + "引号" + chr(34) + " " + bs + " 斜杠" + tab + "制表",
            "error": "退出码 1: " + esc + "[31mANSI 转义" + esc + "[0m",
        }
        llm._log(hostile)
        llm._log({**hostile, "call_id": "x2"})
        with open(self.fake, encoding="utf-8") as f:
            rows = [json.loads(line) for line in f if line.strip()]
        self.assertEqual([r["call_id"] for r in rows], ["x1", "x2"])
        self.assertIn(nl, rows[0]["prompt_preview"])   # 内容原样保留
        self.assertIn(esc, rows[0]["error"])
        self.assertIn(bs, rows[0]["prompt_preview"])


if __name__ == "__main__":
    unittest.main()