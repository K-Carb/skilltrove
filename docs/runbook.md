# 运维手册（常见故障处置）

看板打不开、跑不出候选、数字不对时，按这里的条目排查。每条都是「症状 → 原因 → 处置」三步。

## 先查这两个地方

```bash
python check.py --web          # 环境自检：产物是否齐全、依赖与端口是否可用
curl -s http://127.0.0.1:8000/api/health   # 运行状态：uptime / 请求数 / 5xx / 流水线失败数
```

`/api/health` 里的 `pipeline_runs.failed` 大于 0，说明流水线有跑失败的任务，去「运行任务」页看对应日志。

---

## 1. 所有任务都被判成低分，聚类没有候选

症状：`scored.jsonl` 里高分 0 条，cluster 报「向量粗筛无配对」，DoD1 失败。

原因：`archive/manifest.json` 不存在或路径不对。score 的 join 查证依赖它，读不到时
所有 episode 的 `join_verifiable` 都是 false，于是全判低分。

处置：

```bash
ls archive/manifest.json          # 确认是否存在
python cli/main.py export --source <数据源> --out archive/   # 重新导出，会一并生成 manifest
python cli/main.py score --episodes archive/episodes.jsonl --manifest archive/manifest.json
```

`check.py` 已把 manifest 纳入自检，缺失会直接报 FAIL。

## 2. 看板显示「AI 引擎已就绪」，但发现/起草其实走了规则降级

症状：日志里出现 `rule-fallback(...)` 或 `复核 ... [rule-fallback]`，候选的
`review_method` 不是 `llm:<后端>`。

原因：`/api/llm_status` 只探测命令行工具是否在 PATH（`llm.probe()`），不做真实调用。
工具在 PATH 但调用失败（如 kimi 报 `storage` 相关错误）时，界面仍显示可用。

处置：换个后端实测一次，确认哪个真能调通：

```bash
python -c "import sys; sys.path.insert(0,'.'); from cli import llm; \
print(llm.call('请只回复两个字：正常', backend='claude', timeout=90)['ok'])"
```

把可用的后端写进 `LLM_BACKEND`，或在看板「高级设置」里指定。规则降级不影响闭环跑完，
但复核质量会下降：降级后候选的 `kind` 固定为 `goal`，`review.note` 只有关键词匹配的结论。

## 3. 流水线卡住不结束

症状：「运行任务」页某一步一直转圈，日志停在某行不动。

原因：步骤在等 LLM 返回，或子进程自己挂住了。流水线默认 30 分钟超时。

处置：

1. 点「取消当前运行」（发 terminate 给子进程）。
2. 日志停在 `$ python cli/main.py cluster ...` 这类命令行上，说明是子进程慢，按第 2 条换后端。
3. 调整超时：`SKILLTROVE_PIPELINE_TIMEOUT=600`（秒）。
4. 复核对数过多也会拖慢：`SKILLTROVE_MAX_REVIEW_PAIRS=20` 限制只复核相似度最高的若干对。

## 4. registry.json 打不开或看板报 JSON 错误

症状：看板 500，`python cli/main.py publish ...` 报 JSON 解析错误。

原因：`registry/registry.json` 被写坏了（写一半中断）或手工编辑出错。

处置：

```bash
python -c "import json; json.load(open('registry/registry.json', encoding='utf-8'))"  # 确认是否损坏
ls registry/                      # 找 registry.json.corrupt-<时间戳> 隔离文件与 .bak 备份
git diff registry/registry.json   # 看改动了什么
git checkout -- registry/registry.json   # 确认无保留价值时回滚（会丢弃未提交的审核动作）
```

恢复后走一次 `python cli/main.py publish --skill <名字> --status published` 重建条目。
预防：`publish` 走原子写（临时文件 + 替换）并留 `.bak`，正常操作不会写坏。

## 5. 前端改了但页面没变

症状：改完 `web/static/app.js` 或 `style.css`，刷新后还是旧的。

原因与处置：

- 静态资源已设 `no-cache`，正常刷新即生效；若仍不变，硬刷新（Ctrl+F5）。
- 改的是 `web/app.py` 必须重启服务，改静态文件不需要。
- 用 `start.bat` 启动时会先杀掉 8000 端口的旧实例，重复启动是安全的。

## 6. 端口 8000 起不来

症状：`OSError: [Errno 10048]` 或 `check.py --web` 报「端口 8000 被占用」。

处置：

```bash
netstat -ano | findstr :8000        # 找占用进程
```

杀掉它，或换端口：`set SKILLTROVE_WEB_PORT=8001` 再启动。

## 7. 数据源选不中，卡片显示「无法识别」

症状：「运行任务」页数据源卡片标签是「无法识别」，点不动。

原因：`adapters.pick_adapter()` 按形状自动判定，六种形态都不匹配就认不出来。

处置：在「高级设置」里显式指定适配器。`--adapter` 可选值：
`auto / log-export / task-dirs / table / session-logs / git-repo / docs`。
判定顺序是 log-export → task-dirs → session-logs → git-repo → table → docs，
jsonl 会话日志会被 session-logs 优先接走，不会误当表格。

## 8. 无头截图验证失败（verify-frontend.sh）

症状：`VERIFY-frontend: FAIL`，截图 0 字节或 DOM 断言报 `ERR_`。

原因：系统代理会拦 127.0.0.1 的请求；脚本已加 `--no-proxy-server`。
另外 Edge 路径写死在脚本里（`/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe`），
装在别处就跑不了。

处置：改脚本里的 `EDGE` 变量指向你的 Edge；确认没有其他服务占了 12761 端口。

## 9. 改了 DoD 相关代码后验收不过

DoD 五条各查一处，按报错定位：

| 条目 | 查什么 |
|---|---|
| DoD1 聚类识别真实重复 | `data/candidates.json` 有没有候选 |
| DoD2 草稿审核入共享库 | registry 里有没有 `published` 条目，且 `skills/<名>/evals/cases/` 存在 |
| DoD3 跨 agent 调用 | `data/recall-runs/` 最新 run 的 `applied` 是否为真、调用者是否不在贡献者里 |
| DoD4 命令可重复执行 | 六个子命令是否都注册且模块可导入 |
| DoD5 业务 join 可验证 | `archive/scored.jsonl` 里有没有 high 分 episode |

## 10. .bat 脚本报乱码或命令断裂

原因：GBK 控制台会误解析 UTF-8 中文。仓库里所有 `.bat` 都是纯 ASCII，注释用英文。

处置：新增 `.bat` 时保持全 ASCII，中文提示另想办法或干脆不写。

---

## 附：运行时产物清单

| 路径 | 谁写的 | 说明 |
|---|---|---|
| `archive/manifest.json` | export | 归一化任务清单，score 的 join 查证依据（缺了会全判低分） |
| `archive/facts.json` | export | 数据源元信息，冻结快照 |
| `archive/episodes.jsonl` | export | 标准 episode 序列 |
| `archive/scored.jsonl` | score | 带 label（high/low/excluded） |
| `data/candidates.json` | cluster | 候选簇；评估脚本从这里反推 LLM 复核判定 |
| `data/skill-issues.json` | 看板反馈表单 | 技能问题反馈 |
| `data/recall-runs/<run>/result.json` | recall | 使用留痕四元组 |
| `evals/results/*.json` | 评估脚本 / judge | 算法评估与使用评估留档 |
| `data/llm-log.jsonl` | llm | 每次调用的提示词预览（含敏感内容，已 gitignore） |
| `web/server.log` | uvicorn | 服务日志（已 gitignore） |
