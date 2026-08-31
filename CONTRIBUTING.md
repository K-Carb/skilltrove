# 贡献指南

## 开发环境

```bat
install.bat      rem 或手动：python -m venv .venv && .venv\Scripts\pip install -r requirements-web.txt
start.bat        rem 启动看板 http://127.0.0.1:8000
```

## 改动前后必须跑的验证

```bash
bash pipeline.sh    # 一条命令跑完整流水线：lint -> JS 语法 -> 单测 -> DoD -> 前端（fail-fast，含各阶段耗时）
```

细分命令（调试单阶段时用）：lint `ruff check .`；单测 `python -m unittest discover -s tests`；
DoD `python demo.py --quick`；前端 `bash web/verify-frontend.sh`。
推送前确认 CI（GitHub Actions）为绿。

## 约定

- 界面文案与组件约定见 [AGENTS.md](AGENTS.md)（术语表、文案忌口表、设计令牌都在里面，改文案前先读）
- 界面只用产品语言；技术参数收进"高级设置/技术详情"折叠
- 提交信息用中文、一行说清改动；版本号遵循语义化版本，见 [CHANGELOG.md](CHANGELOG.md)
- 仓库内所有演示数据为虚构（TeamWiki 团队），请勿提交任何真实工作记录、密钥或个人信息

## 发布流程（版本化纪律）

1. 改 `web/app.py` 的 `APP_VERSION`（唯一版本事实源，测试会校验它与 CHANGELOG 一致）
2. 在 `CHANGELOG.md` 新增对应版本节（日期用发布日）
3. 全量验证四件套全绿后提交
4. `git tag -a vX.Y.Z -m "vX.Y.Z"` —— tag 必须打在当前历史的提交上；未推送前可删了重打
5. 推送时带 `--tags`
