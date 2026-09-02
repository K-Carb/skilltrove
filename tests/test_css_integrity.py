"""样式表完整性守卫：所有 var() 引用的 CSS 变量必须有定义。

起因：一次自动化改动删掉了 :root 的 --s1..--s7 间距刻度，var(--s5) 全部失效归零，
页面贴边——DOM 断言测不出布局，直到人工截图才暴露。此测试让它永远在 CI 报警。
"""

from __future__ import annotations

import os
import re
import unittest


class CssIntegrityTests(unittest.TestCase):
    def test_all_var_references_are_defined(self):
        css = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "web", "static", "style.css"), encoding="utf-8").read()
        defined = set(re.findall(r"(--[\w-]+)\s*:", css))
        used = set(re.findall(r"var\((--[\w-]+)", css))
        self.assertEqual(sorted(used - defined), [],
                         "CSS 中存在引用了但未定义的变量（会导致样式静默失效）")

    def test_no_fractional_font_sizes(self):
        # 字号刻度纪律：只允许整数值 px（12/13/14/16/20/28/36...），禁止 11.5/12.5 一类半档
        css = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "web", "static", "style.css"), encoding="utf-8").read()
        fractional = re.findall(r"font-size:\s*\d+\.\d+px", css)
        self.assertEqual(fractional, [], f"出现半档字号: {fractional}")


if __name__ == "__main__":
    unittest.main()
