# 迭代日志（夜间自主迭代用）

> 协议：每轮开始先读本文件与 docs/study/reading-plan.md；结束前必须跑完全部验证门禁
> （ruff / 单测 / demo --quick 5/5 / web/verify-frontend.sh PASS）并提交，然后在此追加一节。

## 2026-08-31 基线（人工）

- 完成大厂工程实践对齐升级：ruff lint 门禁（0 违规）、/api/health 端点、
  Web API 测试层（tests/test_web_api.py，14 项，含状态机非法迁移契约）、
  GitHub Actions CI（py3.10/3.12 + ruff + 单测）、Dependabot（pip/actions 每周）、
  pydantic 直接依赖声明、.gitattributes 换行策略、CONTRIBUTING/CHANGELOG、
  ADR-0001~0003（无构建前端 / git 事实源 / 可插拔 LLM）、README 安全声明与徽章占位
- 验证：195 项单测 OK、ruff 0 违规、DoD 5/5、前端验证 PASS
- 待办：夜间读书计划见 docs/study/reading-plan.md；CI 徽章 URL 中 USER 占位待新账号名


## 2026-08-31 00:40 轮 1（读书对照：Clean Code Ch2/3/4 命名/函数/注释）

- 审计：AST 扫描 17 个超 60 行函数；发现 esc() 名不副实；llm.call() 97 行四后端 if/elif
- 改动：llm.call() 拆为 _call_{claude,codex,kimi,openai_compatible} + _BACKENDS 查找表调度（行为不变）；
  删除 app.js 的 esc()，调用点改为直白的 `line ?? ""`
- 验证：ruff 0 违规 / 195 单测 OK / DoD 5/5 / 前端 PASS
- 下一步：轮 2 取条目 2（Clean Code Ch7/8 错误处理与边界）


## 2026-08-31 01:45 轮 2（读书对照：Clean Code Ch7 错误处理 / Ch8 边界）

- 审计：llm.py 的 _call_* 已是标准适配器层；call() 返回 ok 字典属跨进程 API 边界合理取舍（记录不改）；
  抓到缺口——_extract_codex_text 与 parse_comments_md 两个解析边界无学习测试
- 改动：新增 tests/test_llm_boundaries.py（8 项），钉住 codex JSONL 事件流提取、旧版 result/output
  兼容、function_call 兜底怪癖、comments.md 切段契约、状态归一词表
- 学习测试的即时价值：首轮断言有 3 处与边界真实行为不符（reasoning 会并入输出、
  legacy 需 result 键存在、function_call 独项走原样兜底），全部按实测修正为契约钉子，
  其中 function_call 兜底怪癖已注释记录
- 验证：ruff 0 违规 / 203 单测 OK / DoD 5/5 / 前端 PASS
- 下一步：轮 3 取条目 3（Clean Code Ch9 单元测试 F.I.R.S.T 原则）


## 2026-08-31 02:45 轮 3（读书对照：Clean Code Ch9 单元测试 F.I.R.S.T）

- 审计：203 项 1.9 秒（Fast✓）、无共享状态/随机/网络/顺序依赖（Independent/Repeatable✓）、
  全布尔断言（Self-validating✓）；抓到 test_pipeline 两段相同的轮询等待循环（测试代码 DRY 违规）
- 改动：提取 _wait_terminal() 有界轮询助手（5s 封顶、0.05s 步进，由断言判失败而非盲等）；
  新增 tests/README.md 把 F.I.R.S.T 姿态与"新增测试五条规则"文档化（含禁止写工作目录、
  mock 隔离写端点、边界学习测试强制）
- 验证：ruff 0 违规 / 203 单测 OK / DoD 5/5 / 前端 PASS
- 下一步：轮 4 取条目 4（Fowler 重构坏味道目录，候选：registry.selftest 150 行等长函数）


## 2026-08-31 03:50 轮 4（读书对照：Fowler 重构 坏味道目录 Extract Function）

- 审计：对象为 AST 扫描出的长函数 Top（verify.run 95 行 / create_app 144 行 / selftest 150 行）
- 改动：cli/verify.py run() 拆为 _check_dod1~5 五个检查函数 + run 纯编排（95→约 30 行）。
  重构过程当场抓到并修 2 个真问题：①切分时丢失"汇总/报告路径"两行打印（门禁 grep 报警发现）；
  ②verify.py 输出接管道时 GBK 编码可能崩（补 stdout reconfigure utf-8，与 demo.py 同法）
- 验证：ruff 0 违规 / 203 单测 OK / DoD 5/5 / 前端 PASS（门禁以落文件+grep 方式规避管道编码 flake）
- 经验记录：bash heredoc 中 `
` 经多层转义会变成真实换行——跨语言生成代码一律用 Edit 工具或行号手术
- 下一步：轮 5 取条目 5（OWASP Top 10 逐项对照 Web 看板）
