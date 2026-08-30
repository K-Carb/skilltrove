# 权威教材逐章对照计划（夜间迭代队列）

> 规则：每轮取一个未完成条目，先搜索该章/该主题的权威要点（只读），
> 提炼 3~5 条可核查标准，对照本项目审计，实现最有价值的改进，验证后勾选。
> 顺序即优先级；单项太大就拆成多轮，在条目下加子项。

- [x] 1. 《代码整洁之道》Ch2 命名 / Ch3 函数 / Ch4 注释
  - 提炼的可核查标准（2026-08-31 首轮）：
    a) 名字显意：名字能回答"是什么/为什么存在"，不名不副实（如 esc() 实际只做字符串化）
    b) 函数短小且单一抽象层级：超过 ~80 行的函数需拆分
    c) 消除 if/elif 多路分发：按类型分支改为查找表/多态
    d) 注释只解释"为什么"和坑位事实，不复述代码
    e) 无死代码、无注释掉的代码块
  - 本轮审计：AST 扫描出 17 个超 60 行函数（Top：registry.selftest 150 行、sync.run 142 行、
    create_app 144 行、app.js renderPipeline ~150 行）；修 2 处——
    llm.call() 97 行 if/elif 拆为 _call_{claude,codex,kimi,openai_compatible} + 查找表调度；
    删除 app.js 名不副实的 esc()。其余长函数列入条目 4 重构队列
- [x] 2. 《代码整洁之道》Ch7 错误处理 / Ch8 边界
  - 提炼的可核查标准（2026-08-31 轮 2）：
    a) 错误处理不遮蔽业务逻辑：异常优于返回码，happy path 与错误路径分离
    b) 异常带上下文：消息含"哪一步/失败类型/关键数据"
    c) 不返回 None/null 强迫调用方检查（返回特例或抛异常）；确实返回 None 的点要有明确取舍记录
    d) 第三方边界用适配器包裹，第三方类型不外泄
    e) 每个第三方解析边界要有"学习测试"钉住真实行为
  - 本轮审计与改进：llm.py 的 _call_* 已是标准适配器层（满足 d）；call() 返回 ok 字典
    属跨进程 API 边界的合理取舍（记录不改）；抓到缺口——_extract_codex_text（codex JSONL
    事件流解析）与 parse_comments_md（外部 markdown 解析）两个纯函数边界无学习测试，
    新增 tests/test_llm_boundaries.py 钉住其真实行为
- [x] 3. 《代码整洁之道》Ch9 单元测试 —— 测试 F.I.R.S.T 原则、测试可读性（对照 tests/）
  - 提炼的可核查标准（2026-08-31 轮 3）：
    a) Fast：整套测试应秒级（慢了就没人肯跑）
    b) Independent：测试间无共享可变状态、不依赖执行顺序
    c) Repeatable：不依赖网络/环境/随机，任何机器结果一致
    d) Self-validating：布尔输出，无需人工判读
    e) 测试代码也是代码：无重复样板，约定文档化
  - 本轮审计：203 项 1.9 秒（a✓）；无共享状态/随机/网络（b/c✓）；全部布尔断言（d✓）；
    抓到 test_pipeline 两段相同的轮询等待循环（e✗）——提取 _wait_terminal 助手；
    新增 tests/README.md 把 F.I.R.S.T 姿态与新增测试约定文档化
- [x] 4. Fowler《重构（第2版）》坏味道目录（节选）—— 神秘命名/重复代码/过长函数/发散变化，
       挑 3 处最值得的重构并实施（保持测试绿）
  - 提炼的可核查标准（2026-08-31 轮 4，依据 refactoring.com 目录）：
    a) Long Function：编排型函数只做编排，检查逻辑下沉为具名单元
    b) Duplicated Code：相同逻辑块提取复用
    c) 重构纪律：小步、测试全程绿、行为不变
  - 本轮实施 2 处 Extract Function：verify.run() 95 行拆为 _check_dod1~5 五个检查函数
    （run 只做编排与报告）；顺带修 2 个真 bug——重构暴露的汇总打印丢失、
    verify.py stdout 接管道时 GBK 编码崩溃（补 reconfigure utf-8）
       挑 3 处最值得的重构并实施（保持测试绿）
- [x] 5. OWASP Top 10:2021 逐项 —— 对照 Web 看板（注入/A03 注入面/CORS/错误信息泄露/日志注入）
  - 提炼的可核查标准（2026-08-31 轮 5，参考 owasp.org/Top10/2021 与 Vite GHSA-vg6x-rcgg-rjx6）：
    a) A01 访问控制：写端点无鉴权属设计，但必须有边界防线（绑定回环 + Host 校验防 DNS rebinding）
    b) A03 注入：子进程列表参数、路径穿越写前校验、DOM 全 textContent
    c) A05 配置：默认绑定 127.0.0.1、debug 关、安全响应头（nosniff）
    d) A09 日志：日志含用户输入时不可被控制字符污染；敏感日志已 gitignore
  - 本轮审计：b/c/d 大体达标；抓到 a 的真缺口——无 Host 校验（Vite/webpack 同款漏洞类），
    新增 Host 允许列表中间件（DNS rebinding 防护，SKILLTROVE_ALLOWED_HOSTS 可扩展）
    + nosniff 响应头 + 对应契约测试
- [x] 6. OWASP ASVS L1 子集落地为自动化检查项
  - 提炼的可核查标准（2026-08-31 轮 6，依据 ASVS 4.0 V4/V5/V7）：
    a) V4 访问控制：Host 允许列表（轮 5 已测）
    b) V5 输入校验：请求体 pydantic 校验 + 写路径守卫（已有契约测试）
    c) V7.1 日志防注入：含换行/引号/控制字符的输入进日志后，文件必须仍是合法 JSONL
    d) V7.2 错误不泄露内部路径：错误响应 detail 不得含文件系统路径或堆栈
    e) 社区标配：SECURITY.md 漏洞报告指引 + 安全模型声明
  - 本轮实施：c/d 以测试落地（log 注入抵抗 + 404 detail 无路径断言）；新增 SECURITY.md
- [x] 7. Google SRE Ch6 监控告警 —— 看板自身可观测性：health 已有，补日志规范与运行指标输出
  - 提炼的可核查标准（2026-08-31 轮 7，依据 sre.google/sre-book/ch06 四黄金信号）：
    a) 白盒插桩：健康端点应带仪表数据而非裸 ok
    b) 流量/错误信号：请求总数与 5xx 计数可查
    c) 饱和/错误可见性：流水线运行总数与失败数可查
    d) 告警可行动：本地工具无告警系统，以"一个端点看全运行状态"替代
  - 本轮实施：/api/health 升级——uptime_s + requests_total + responses_5xx（中间件计数）
    + pipeline_runs（总数/失败数），并补契约测试
- [x] 8. Google SRE 发布工程 —— 版本化一致性（APP_VERSION/CHANGELOG/tag 流程文档化）
  - 提炼的可核查标准（2026-08-31 轮 8）：
    a) 单一版本事实源：APP_VERSION 为准，CHANGELOG 最新节必须包含它（自动化守卫）
    b) tag 必须指向当前历史的提交，不得悬空指向孤儿提交
    c) 发布流程文档化：改版本 -> 记 CHANGELOG -> 提交 -> 打 tag
  - 本轮审计：抓到 tag v0.1.0 悬空指向历史重建前的孤儿提交；CHANGELOG 日期滞后；
    版本漂移无守卫；发布流程未文档化——四项全修（一致性测试 + 流程入 CONTRIBUTING + 重打 tag）
- [ ] 9. 《持续交付》Ch5 部署流水线 —— 把 verify 脚本串联成一条"提交即验证"的流水线脚本
- [ ] 10. 《Accelerate》交付效能 —— 度量现状：从 git 历史统计交付周期/变更失败率是否可自动化
- [ ] 11. Well-Architected 可靠性支柱 —— 数据备份/恢复演练：registry 损坏时的降级与恢复路径
- [ ] 12. Well-Architected 运维卓越支柱 —— 常见故障 playbook 写入 docs/runbook.md
