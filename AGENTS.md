# SkillTrove 前端约定（给后续 agent 的接手说明）

> 依据：Anthropic frontend-design / webapp-testing skills + 真实站点对照（GitHub Primer 实值）。
> 核心纪律：**设计计划先行防 AI slop；真实浏览器截图验证；人做最后复核；改动必须出示证据，不口头声称成功。**

## 技术栈与硬约束

- FastAPI + 原生 JS（`web/app.py` + `web/static/`），纯 CSS 变量，**零外部依赖**（无 CDN 字体/图标/框架）
- 中文界面；**`.bat` 必须全 ASCII**（GBK 控制台会误解析 UTF-8 中文导致命令断裂，踩过坑）
- 这是**数据工具看板**（IA 自 v17：导航只露 4 个主视图「首页 overview / 技能库 skills / 收件箱 inbox / 运行任务 pipeline」，带内联 SVG 图标；candidates / metrics / recalls 为旧深链保留路由但不在导航，指标与使用记录从首页 hero 进入；收件箱 = 待审核技能 + 待确认重复任务 + 技能问题反馈，`candidateCard()` 为共用组件），功能优先、克制视觉，不是营销页
- **大众化层（v18-v19）**：技能正文一律走 `renderMarkdown()`（零依赖 DOM API 渲染，禁 innerHTML）；审核 = 渲染要点 + 大按钮，动作后 toast 带「撤销」（禁 window.confirm）；发布成功给 `.celebrate` 横幅；技能库头部有 `.search-box`（命中 name/description/when_to_use）；技能详情已发布态有「报告问题」折叠表单（POST `/api/skills/<n>/issue` 写 `data/skill-issues.json`，收件箱展示 open 项）；首页 = hero 价值数字 + 待办卡 + 完成后收起的向导折叠（`details.wizard-fold`）

## 设计令牌（`style.css :root`，改样式先改令牌）

- **主题**：暖纸白 + 墨色文字 + 单一深墨绿强调（配色调研选型：Tailwind stone+emerald 系，hex 已核验）
- **表面**：页面 `#fafaf9` + 卡片白 + header 暖纸玻璃（`rgba(250,250,249,.88)` + blur）；hairline `#e7e5e4`
- **signature（唯一强调，全纯色无渐变）**：深墨绿 `#065f46`，用于 brand 点 / 导航 active 下划线 / 主按钮 / 向导当前步 / 指标条 / 日志胶囊
- **圆角：全部 0（方角风格，用户指定）**；状态色（深字浅底）：`ok #15803d/#f0fdf4`、`err #991b1b/#fdf0f0`、`run #065f46/#eef6f3`、`warn #92400e/#fdf6ec`、`mut #57534e/#f3f2ef`
- **字体**：`--font`（Segoe UI + 中文回退）、`--mono`（日志/代码）；**数字一律 `tabular-nums`**
- **间距**：4px 基准（`--s1..s7`）

## 组件约定

- **同构行 → hairline 列表**（`.list/.list-row`，skill 库/回采/最近运行）；**异构内容 → 卡片**（`.card`，候选/指标）
- **状态徽章**：方形 `.tag` + 前置状态点 + 文字，**绝不只靠颜色**；deprecated/取消态加删除线
- **复杂度封装（产品化铁律，v13）**：默认视图只出现产品语言；技术参数（适配器/阈值/LLM 后端）收进 `details.advanced`「高级设置」默认折叠；原始 JSON/日志/内部标识符（run_id、candidate_id、episode_ids、evidence）收进 `details.tech`「技术详情」默认折叠；指标不用内部代号（M1..M4）与"口径"行话；步骤/状态全中文（`STEP_CN/STATUS_CN` 映射表）
- **术语表（v16 定稿，改动文案前先查）**：导航与全站用"技能库/重复任务/使用记录/运行任务"（不用 Skill 库/聚类候选/回采记录/流水线）；AI 助手（不用 agent 裸称）；AI 引擎（界面层，不用 LLM 后端；"LLM 后端"仅限高级设置内）；技能的人类可读说明来自 SKILL.md frontmatter 的 `description`（后端 `/api/registry` 注入 `description` 字段，展示于列表 `.row-desc` 与详情页）；候选卡结论用产品语言重述（"N 个人或 AI 助手各自做过…可合并成一份技能"），技术性复核原文收进技术详情
- **文案忌口表（v21，去 AI 味，借鉴 anti-vibe-writing）**：界面文案禁用"沉淀/赋能/打通/抓手/自进化/让 X 流动起来/正常运转"等黑话与比喻腔；少用"即可"（改"就行"）；`「」`直角引号界面不用（代码注释随意）；不写滴水不漏的完美长句，一句说一件事；产品自称"团队 AI 经验库"（不再叫"经验沉淀看板"）。改文案前先读这条
- 导航 active = **下划线式**（accent 下划线 + 浅发光）；主按钮 `.action` = 实心墨绿，次按钮 `.action.ghost`
- **空状态必须给"下一步"引导按钮**（如"去流水线发现经验 →"）；流水线跑完给跳转（`.pipeline-jumps`：查看候选/查看 Skill 库）；回采页空状态必须给"接入 agent + 回传命令"两步指引（第 5 步的完成路径在 UI 之外）
- **首次使用三提示（v15）**：示例数据标注（`.demo-note`，按预置示例技能名探测）、数据源卡内容量（`detect_sources` 返回 `count`/`size_kb`）、LLM 后端状态行（`/api/llm_status`，流水线运行卡内）——避免新用户误以为示例进展是自己产生的、不知道选哪个源、跑到一半才发现 LLM 没配
- 首页 = 价值仪表（hero 数字 + 待办卡 + 指标/使用记录入口），5 步向导（`.wizard-*`）当前步高亮（accent 段）+ 单一主按钮，回答"我该干嘛"；五步全部完成后整块收起为 `details.wizard-fold`

## 交互

- 过渡 `180ms ease`；动画只用 `transform/opacity`；`prefers-reduced-motion` 降级已内置
- **折叠面板**：`details.advanced` / `details.tech` 一律默认收起（封装复杂度），summary 为可点击标签；展开态用 `details[open]` 控制
- 日志流：用户上滚时显示"▼ 新日志 N 条"胶囊（`.log-new`），点击回底，不打断阅读

## 验证工作流（每次改前端必做，出示证据）

```bash
bash web/verify-frontend.sh    # 一键：起服务 → 全路由（7 条）无头截图+DOM 断言 → node check → 单测
```

1. `node --check web/static/app.js`
2. `python -m unittest discover -s tests`（当前 181）
3. `bash web/verify-frontend.sh`：Edge 无头截图全部路由（7 条）+ DOM 断言（0 错误）
4. 对照截图人工看一遍；**改动必须附截图/DOM 证据**

## 已知坑

- Edge/Chrome 无头访问 localhost 需 `--no-proxy-server`（系统代理会拦 127.0.0.1）
- 静态资源已 `Cache-Control: no-cache`：改 static 刷新即生效；改 `web/app.py` 才需重启
- `start.bat` 幂等重启（先杀 8000 旧实例再起，服务窗口 + 2s 后开浏览器）；`.bat` 保持全 ASCII
- 中文文案：中英文间加空格（"skill 库"）；不用 em-dash（`—`），用 `-` 或 `[ ]`
