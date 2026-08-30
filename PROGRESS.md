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
