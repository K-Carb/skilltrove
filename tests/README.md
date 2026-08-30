# 测试套件约定（F.I.R.S.T 姿态）

对照《代码整洁之道》Ch9 维护。当前姿态：**203 项 / 约 2 秒**，任何一台机器结果一致。

| 原则 | 本套件的落实 |
|---|---|
| Fast | 全套 ~2s；异步边界用有界轮询（≤5s 封顶）而非盲等 |
| Independent | 测试间零共享可变状态；写操作一律进 `tempfile.TemporaryDirectory()` |
| Repeatable | 不碰网络、不用随机、不依赖当前时间与执行顺序（unittest 按字典序跑） |
| Self-validating | 全部布尔断言；唯一的准异常是 `demo.py --quick` 的 DoD 报告（也有 pass/fail 汇总） |
| Timely | 新增行为先写断言再实现；边界（LLM/外部解析）必须有学习测试（见 test_llm_boundaries.py） |

## 新增测试的规则

1. 写入 `tests/`，文件名 `test_<模块>.py`；顶部 `sys.path.insert` 与现有文件保持一致。
2. 所有文件读写走 `tempfile`，**禁止**写仓库工作目录（`data/`、`archive/`、`registry/` 是演示数据，也是别人的测试环境）。
3. 需要隔离的 Web 写端点：`unittest.mock.patch("web.app.issues_path" / "web.app.registry_path")`（参考 test_web_api.py）。
4. 异步/子进程边界：轮询必须带次数上限与超时（参考 test_pipeline.py 的 `_wait_terminal`）。
5. 第三方解析函数：先写学习测试钉住真实行为，发现"假设≠现实"时以实测为准并在注释记录怪癖。
