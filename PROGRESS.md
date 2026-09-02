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


## 2026-08-31 04:45 轮 5（读书对照：OWASP Top 10:2021 逐项）

- 审计：A03 注入（子进程列表参数/路径穿越写前校验/DOM 全 textContent）、A05 配置（默认回环绑定）、
  A09 日志治理大体达标；抓到 A01/A05 真缺口——无 Host 校验，与 Vite GHSA-vg6x-rcgg-rjx6、
  webpack-dev-server #887 同类：DNS rebinding 可让恶意网站直达 127.0.0.1 的无鉴权写端点
- 改动：新增 Host 允许列表中间件（默认 127.0.0.1/localhost/::1，SKILLTROVE_ALLOWED_HOSTS 可扩展，
  非法 Host 403）+ nosniff 响应头；README 安全节补充；HostGuardTests 4 项契约测试
- 验证：ruff 0 违规 / 207 单测 OK / DoD 5/5 / 前端 PASS
- 下一步：轮 6 取条目 6（OWASP ASVS L1 子集落地为自动化检查项）


## 2026-08-31 05:45 轮 6（读书对照：OWASP ASVS L1 子集 V4/V5/V7）

- 审计：V4 访问控制（轮 5 已测）、V5 输入校验（已有契约）达标；V7 两个可自动化项缺口——
  日志注入抵抗与错误信息泄露无测试；缺 GitHub 社区标配 SECURITY.md
- 改动：新增日志注入抵抗测试（换行/引号/反斜杠/制表/ANSI 转义进 llm-log 后文件仍为合法
  JSONL 且内容原样保留）；404 错误 detail 无文件系统路径断言；新增 SECURITY.md
  （版本支持表、私有漏洞报告指引、安全模型声明）
- 过程教训（复现轮 4 记录）：heredoc 多层转义连续三次把 
/引号/ESC 解码成真实字符——
  最终用 chr() 运行时构造 + 行号截断重建解决；此教训已两次验证，后续轮次直接用 Edit/chr()
- 验证：ruff 0 违规 / 209 单测 OK / DoD 5/5 / 前端 PASS
- 下一步：轮 7 取条目 7（Google SRE Ch6 可观测性：health 之外的日志规范与运行指标输出）


## 2026-08-31 06:45 轮 7（读书对照：Google SRE Ch6 监控/四黄金信号）

- 审计：health 是裸探针，无流量/错误/存活信号，流水线失败数不可从单端点感知
- 改动：/api/health 升级为仪表端点——uptime_s + requests_total + responses_5xx
  （中间件计数）+ rejected_hosts（DNS rebinding 拒绝数，安全信号）+ pipeline_runs
  （总数/失败数）；403 归入安全信号而非 5xx（修正了首版把 403 误计 5xx 的分类错误）
- 验证：ruff 0 违规 / 211 单测 OK / DoD 5/5 / 前端 PASS
- 下一步：轮 8 取条目 8（SRE 发布工程：APP_VERSION/CHANGELOG/tag 流程文档化）


## 2026-08-31 07:45 轮 8（读书对照：Google SRE 发布工程）

- 审计：抓到 tag v0.1.0 悬空（历史重建后指向孤儿提交）；CHANGELOG 日期滞后；版本漂移无守卫；发布流程未文档化
- 改动：新增 test_version_single_source_of_truth（APP_VERSION ↔ CHANGELOG 最新节一致性守卫）；
  CHANGELOG 日期对齐；CONTRIBUTING 增"发布流程"五步纪律；删除悬空 tag 并重打到当前 HEAD
- 验证：ruff 0 违规 / 212 单测 OK / DoD 5/5 / 前端 PASS；git tag --points-at HEAD 确认 v0.1.0 已就位
- 下一步：轮 9 取条目 9（《持续交付》部署流水线：verify 脚本串联成一条提交即验证流水线）


## 2026-08-31 08:50 轮 9（读书对照：《持续交付》Ch5 部署流水线）

- 审计：四道门禁此前靠手工按序敲，无一条命令入口、无耗时可见性、无 fail-fast
- 改动：新增 pipeline.sh——lint(0s) -> js 语法(0s) -> 单测(3s) -> DoD(0s) -> 前端(586s)，
  阶段最快优先、fail-fast 中止并指出日志位置、耗时汇总；DoD 判定改为读 docs/dod-report.json
  （规避 grep 中文输出的编码 flake——轮 4 记录的坑在脚本层根治）；CONTRIBUTING 验证入口收敛为
  一条命令 bash pipeline.sh
- 验证：流水线自身全绿（如上耗时）；tag v0.1.0 保持在 HEAD 附近未动
- 下一步：轮 10 取条目 10（《Accelerate》交付效能度量：从 git 历史统计交付周期是否可自动化）


## 2026-08-31 09:40 轮 10a（前端专项 1：对比度 + 触控目标 —— 队列盲点修正）

- 审计（WCAG 公式实算）：--ink-3 #a8a29e 在白卡上 2.52:1（AA 要求 4.5），而 row-meta/
  向导提示/流水线标签/页脚等小字全在用它（CSS 17 处）；移动端按钮/输入无触控尺寸下限
- 改动：--ink-3 → stone-500 #78716c（白卡 4.8:1，单一令牌点修复全部 17 处）；
  移动端 nav/按钮/输入 min-height 40px（WCAG 2.5.8 / Refactoring UI 触控下限）
- 反思：前 9 轮零前端迭代是队列设计盲点——已把 Refactoring UI/WCAG 两个前端专项插入队列，
  且本轮人工补位执行；后续 cron 轮次将优先消化 10b（字号刻度/间距审计）
- 验证：ruff 0 违规 / 212 单测 OK / DoD 5/5 / 前端 PASS / 收件箱截图人工复核对比度改善


## 2026-08-31 11:10 轮 11 收编（中断轮：评估管线加固 + 字号刻度归并）

- 背景：本轮在"停止循环任务"时被中断，事后核验改动完整且全绿，予以收编提交
- 内容 A（评估管线加固）：check.py 增加 archive/manifest.json 自检（缺失会让全部 episode
  静默判低分）；facts.json/candidates 链路补 shape 字段；evaluate_pipeline 与 test_evaluate
  扩充（+8 项测试）
- 内容 B（前端 10b 前半）：字号刻度从 15 种含分数值收敛为 9 个整数值
  （11/11.5/12.5→12，13.5→13，15.5→16，0.85rem→13）
- 验证：ruff 0 违规 / 220 单测 OK / DoD 5/5 / 前端 PASS
