# Changelog

格式参考 Keep a Changelog；版本遵循语义化版本。

## [0.1.0] - 2026-08-30

首个公开发布版本。

### 新增
- 本地 Web 看板（FastAPI + 原生 JS，零外部依赖）：5 步任务向导、技能库（搜索）、
  收件箱（待审技能 / 重复任务 / 问题反馈）、运行任务（日志流、AI 引擎状态检查）
- 技能审核流：markdown 渲染审阅、通过/打回（可撤销）、发布庆祝态
- 消费侧闭环：技能问题反馈表单 -> 收件箱、`#skill/<名字>` 深链
- 指标页：累计使用次数、实际使用率、审核通过率、发现的重复任务
- CLI 六步闭环：export -> score -> cluster -> draft -> publish -> recall + dod-verify
- 内置虚构演示数据（TeamWiki 团队）与示例数据源
- 单元测试 181 项；一键前端验证脚本（截图 + DOM 断言）；GitHub Actions CI
