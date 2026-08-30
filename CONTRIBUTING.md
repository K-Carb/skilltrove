# 贡献指南

## 开发环境

```bat
install.bat      rem 或手动：python -m venv .venv && .venv\Scripts\pip install -r requirements-web.txt
start.bat        rem 启动看板 http://127.0.0.1:8000
```

## 改动前后必须跑的验证

```bash
.venv/Scripts/python -m unittest discover -s tests   # 单元测试（当前 181 项）
bash web/verify-frontend.sh                          # 前端：全路由截图 + DOM 断言 + JS 语法 + 单测
.venv/Scripts/python demo.py --quick                 # 端到端 DoD 验收（应 5/5）
```

推送前确认 CI（GitHub Actions）为绿。

## 约定

- 界面文案与组件约定见 [AGENTS.md](AGENTS.md)（术语表、文案忌口表、设计令牌都在里面，改文案前先读）
- 界面只用产品语言；技术参数收进"高级设置/技术详情"折叠
- 提交信息用中文、一行说清改动；版本号遵循语义化版本，见 [CHANGELOG.md](CHANGELOG.md)
- 仓库内所有演示数据为虚构（TeamWiki 团队），请勿提交任何真实工作记录、密钥或个人信息
