# SkillTrove

[![CI](https://github.com/K-Carb/skilltrove/actions/workflows/ci.yml/badge.svg)](../../actions)

把团队 AI 助手干活的记录，变成全团队可复用的技能。

SkillTrove 自动从多 agent 的工作记录里发现反复出现的同类任务，合并整理成标准技能文档（SKILL.md），人工审核后进入共享技能库，供所有人的 AI 助手在真实任务里使用——并留下使用记录与效果评估，形成闭环。

## 它解决什么问题

团队里每个人（和每个人的 AI 助手）都在各自踩同样的坑、各自调研同样的问题。这些"重复探索"散落在各自的工作记录里，没人看见。SkillTrove 把它们挖出来：发现重复 → 合并成技能 → 人工审核 → 全队复用。

## 快速开始

```bat
skilltrove.bat   rem 一键：建 venv + 装依赖 + 自检 + 启动看板 + 开浏览器
```

或分步：

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements-web.txt
.venv/Scripts/python check.py --web
.venv/Scripts/python web/app.py     # 打开 http://127.0.0.1:8000
```

看板首次打开自带一套演示数据（虚构团队 TeamWiki 的 5 条工作记录），跟着首页 5 步向导走一遍即可理解全流程。

## 六步闭环命令（可重复执行，全本地）

```bash
python cli/main.py export  --source examples/sources/wiki-tasks.json --out archive/
python cli/main.py score   --episodes archive/episodes.jsonl --manifest archive/manifest.json
python cli/main.py cluster --episodes archive/scored.jsonl --out data/candidates.json
python cli/main.py draft   --candidates data/candidates.json
python cli/main.py publish --skill <name> --status published
python cli/main.py recall  --skill <name>
python cli/main.py dod-verify          # 端到端验收
```

说明：`--out archive/` 会覆盖自带的演示数据，想保留演示效果就先备份，或把 `--out` 指到别的目录。示例表只有 6 条记录，量太小，聚类大概率给不出候选——看板预置的演示数据展示的是完整闭环的效果，接入真实数据（几十条以上）后更容易出候选。

## 工作方式

1. **接入数据**：工作记录（任务表、导出包、Git 仓库、会话日志等，适配器自动识别）。
2. **发现经验**：本地向量粗筛 + LLM 四维复核，找出"多个人各自做过的同类任务"。
3. **起草技能**：AI 把重复任务合并写成标准 SKILL.md 草稿。
4. **人工审核**：看板内渲染审阅，通过/打回（均可撤销）。
5. **使用**：团队 AI 助手在真实任务里按技能执行，回传使用记录。
6. **度量**：使用次数、实际使用率、审核通过率、重复任务量。

## LLM 配置（可插拔后端）

| 变量 | 说明 |
|---|---|
| `LLM_BACKEND` | `kimi`（默认）/ `claude` / `codex` / `openai-compatible` |
| `LLM_BASE_URL` | openai-compatible 的 base（默认 `http://localhost:11434/v1`，如 Ollama） |
| `LLM_API_KEY` | 网关 key（如无需鉴权可不设） |
| `LLM_MODEL` | 模型名（openai-compatible 必填，如 `qwen2.5:7b`） |

纯浏览看板不需要 LLM；"发现/起草"等步骤需要。

## 其他环境变量

| 变量 | 说明 |
|---|---|
| `SKILLTROVE_WEB_HOST` / `SKILLTROVE_WEB_PORT` | 看板监听地址（默认 127.0.0.1:8000） |
| `SKILLTROVE_SOURCES_DIR` | 数据源扫描目录（默认 `examples/sources`，分号分隔多个） |
| `SKILLTROVE_PIPELINE_TIMEOUT` | 单次流水线超时秒数（默认 1800） |

## 隐私与安全

- 全本地运行，工作记录不出本机；共享技能库以 git 为事实源，团队成员各自同步。
- 看板无鉴权，**默认只绑定 127.0.0.1**——请勿把端口暴露到公网；跨站表单请求会被 JSON 请求体校验挡下。
- 内置 DNS rebinding 防护（Host 允许列表，对照 Vite GHSA-vg6x-rcgg-rjx6 同类漏洞）：仅接受
  127.0.0.1 / localhost / ::1；确需远程访问时用 `SKILLTROVE_ALLOWED_HOSTS`（分号分隔）显式加白，并自行补反代鉴权。
- LLM 调用日志（`data/llm-log.jsonl`，已 gitignore）包含提示词内容，属于本机敏感数据，勿入库、勿外传。
- 仓库内所有数据（`archive/`、`data/`、`skills/`、`examples/`）均为虚构的演示数据（TeamWiki 团队），可随时清空后接入自己的记录。

## 开发

```bash
python -m unittest discover -s tests   # 单元测试
bash web/verify-frontend.sh            # 前端一键验证（截图 + DOM 断言 + 测试）
python demo.py --quick                 # 端到端 DoD 验收（应 5/5）
```

提交约定与验证清单见 [CONTRIBUTING.md](CONTRIBUTING.md)；内部约定（术语表、文案规范、设计令牌）见 [AGENTS.md](AGENTS.md)。

## License

MIT
