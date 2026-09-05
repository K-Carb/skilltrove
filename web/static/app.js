/* SkillTrove 看板逻辑 v13：复杂度封装（默认视图只展示产品语言）
   - 技术参数（适配器/阈值/LLM 后端）收进「高级设置」折叠
   - 原始 JSON / 日志收进「技术详情」折叠
   - 内部标识符（run_id / candidate_id / episode_ids）默认不展示
   - 指标去掉内部代号（M1..M4）与行话，步骤/状态全部中文化 */
"use strict";

const $view = document.getElementById("view");
/* 路由表：导航只露出 4 个主视图（overview/skills/inbox/pipeline）；
   candidates/metrics/recalls 为旧深链保留（首页有入口），不再出现在导航里 */
const views = ["overview", "skills", "inbox", "pipeline", "candidates", "metrics", "recalls"];
let current = "skills";

/* 中文化映射（产品语言；key 为后端原值） */
const STEP_CN = {
  export: "读取记录", score: "评估任务", cluster: "发现重复",
  draft: "起草技能", publish: "发布", recall: "记录使用",
};
const STATUS_CN = {
  published: "已发布", deprecated: "已废弃", draft: "草稿", in_review: "审核中",
};
const RUN_STATUS_CN = {
  queued: "排队中", running: "运行中", success: "成功", failed: "失败", cancelled: "已取消",
};
const METRIC_CN = {
  "M1_复用次数": "累计使用次数",
  "M3_调用率口径": "实际使用率",
  "M4_审核通过率": "审核通过率",
  "M2_重复探索口径": "发现的重复任务",
};
const METRIC_NOTE = {
  "M1_复用次数": "所有技能被团队实际使用的累计次数",
  "M3_调用率口径": "使用任务里真正遵循了技能步骤的比例",
  "M4_审核通过率": "已通过审核发布的技能占已审结技能的比例",
  "M2_重复探索口径": "系统发现的重复任务数量（过程相似度达标 / 全部任务）",
};
const ADAPTER_CN = {
  "table": "数据表", "git-repo": "Git 仓库", "log-export": "日志导出包",
  "task-dirs": "任务文件夹", "session-logs": "会话记录", "docs": "文档", "auto": "自动识别",
};

function cn(map, key, fallback) { return (map[key] || fallback || key); }
function fmtTs(iso) {
  if (!iso) return "未知时间";
  try {
    return new Date(iso).toLocaleString("zh-CN",
      { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false });
  } catch (e) { return "未知时间"; }
}

async function api(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok) throw new Error((await r.text()) || r.statusText);
  return r.json();
}

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}

/* 状态提示条：可带一个操作按钮（如"撤销"），代替打断式 confirm 弹窗 */
function showToast(msg, kind, actionLabel, actionFn) {
  const box = document.getElementById("toast");
  if (!box) return;
  const t = el("div", "toast" + (kind ? " " + kind : "") + (actionLabel ? " has-action" : ""));
  t.appendChild(el("span", null, msg));
  let dismissed = false;
  const dismiss = () => {
    if (dismissed) return;
    dismissed = true;
    t.classList.remove("show");
    setTimeout(() => t.remove(), 250);
  };
  if (actionLabel) {
    const b = el("button", "toast-action", actionLabel);
    b.onclick = () => { dismiss(); if (actionFn) actionFn(); };
    t.appendChild(b);
  }
  box.appendChild(t);
  requestAnimationFrame(() => t.classList.add("show"));
  setTimeout(dismiss, actionLabel ? 6000 : 2600);
}

/* 技术详情折叠块（默认收起）：原始 JSON / 日志收在这里 */
function techDetails(summaryText, blocks) {
  const d = el("details", "tech");
  d.appendChild(el("summary", null, summaryText));
  const body = el("div", "detail-body");
  for (const [label, text] of blocks) {
    if (text == null || text === "" || (Array.isArray(text) && !text.length)) continue;
    body.appendChild(el("div", "tech-label", label));
    body.appendChild(el("pre", "code", text));
  }
  d.appendChild(body);
  return d;
}

/* ---------- 轻量 markdown 渲染（零依赖；全部 DOM API 构建，无 innerHTML） ---------- */
function mdInline(s) {
  const frag = document.createDocumentFragment();
  const re = /(\*\*[^*]+\*\*|`[^`]+`)/g;
  let last = 0, m;
  while ((m = re.exec(s))) {
    if (m.index > last) frag.appendChild(document.createTextNode(s.slice(last, m.index)));
    const tok = m[0];
    if (tok.startsWith("**")) frag.appendChild(el("strong", null, tok.slice(2, -2)));
    else frag.appendChild(el("code", "md-code", tok.slice(1, -1)));
    last = m.index + tok.length;
  }
  if (last < s.length) frag.appendChild(document.createTextNode(s.slice(last)));
  return frag;
}

function renderMarkdown(src) {
  const frag = document.createDocumentFragment();
  let text = String(src || "").replace(/\r\n/g, "\n");
  // 跳过开头 frontmatter（--- ... ---），那是给机器读的
  const fm = text.match(/^---\n[\s\S]*?\n---\n/);
  if (fm) text = text.slice(fm[0].length);
  const lines = text.split("\n");
  const frag2 = document.createDocumentFragment();
  let para = [], list = null, inFence = false, fence = [];
  const flushPara = () => { if (para.length) { const p = el("p", "md-p"); p.appendChild(mdInline(para.join(" "))); frag2.appendChild(p); para = []; } };
  const flushList = () => { if (list) { frag2.appendChild(list); list = null; } };
  for (const line of lines) {
    if (inFence) {
      if (/^```/.test(line)) { frag2.appendChild(el("pre", "code", fence.join("\n"))); fence = []; inFence = false; }
      else fence.push(line);
      continue;
    }
    if (/^```/.test(line)) { flushPara(); flushList(); inFence = true; continue; }
    const h = line.match(/^(#{1,4})\s+(.*)/);
    if (h) { flushPara(); flushList(); const hd = el("div", "md-h md-h" + h[1].length); hd.appendChild(mdInline(h[2])); frag2.appendChild(hd); continue; }
    const ul = line.match(/^\s*[-*]\s+(.*)/);
    const ol = line.match(/^\s*\d+[.)]\s+(.*)/);
    if (ul || ol) {
      flushPara();
      const want = ul ? "ul" : "ol";
      if (!list || list._tag !== want) { flushList(); list = document.createElement(want); list.className = "md-list"; list._tag = want; }
      const li = document.createElement("li");
      li.appendChild(mdInline((ul || ol)[1]));
      list.appendChild(li);
      continue;
    }
    if (/^\s*(-{3,}|\*{3,})\s*$/.test(line)) { flushPara(); flushList(); frag2.appendChild(el("hr", "md-hr")); continue; }
    if (!line.trim()) { flushPara(); flushList(); continue; }
    para.push(line.trim());
  }
  flushPara(); flushList();
  frag.appendChild(frag2);
  return frag;
}

/* ---------- 视图：开始（5 步任务向导，人类第一问：我该干嘛） ---------- */
async function renderOverview() {
  $view.innerHTML = "";
  const [reg, cand, rec, runs] = await Promise.all([
    api("/api/registry"), api("/api/candidates"), api("/api/recalls"), api("/api/pipeline/runs"),
  ]);
  const skills = reg.skills || [];
  const pending = skills.filter(s => ["draft", "in_review"].includes(s.review_status));
  const published = skills.filter(s => s.review_status === "published");
  const candCount = (cand.candidates || []).length;
  const recCount = rec.length;
  const runsList = runs.runs || [];
  const everRanExport = runsList.some(r => r.status === "success" && (r.steps || []).includes("export"));

  $view.appendChild(el("p", "home-intro",
    "你的团队平时让 AI 助手（agent）干活，它们每次干活的完整过程都会被记录下来。SkillTrove 从这些记录里找出反复出现的同类任务，整理成团队可以重复用的技能。按下面 5 步做就行。"));

  // 项目自带示例数据标注：新用户第一次打开不至于误以为"这些进展是我做出来的"
  const hasDemoData = skills.some(s => s.name === "implementation-research");
  if (hasDemoData) {
    $view.appendChild(el("p", "demo-note",
      "页面里的技能和记录是项目自带的示例数据，用来演示完整流程，不是你产生的。正式使用时换成你自己的工作记录就行。"));
  }

  // 5 步任务流（当前步高亮 + 单一主按钮）
  const steps = [
    { name: "接入数据", desc: "把团队的工作记录交给系统：日志导出包、GitHub 数据、Git 仓库或任务文件夹都行。",
      done: everRanExport || candCount > 0, act: "选择数据源", go: "pipeline", need: null },
    { name: "发现经验", desc: "系统自动找出团队里反复出现的同类任务（重复调研、重复修复、重复踩坑）。",
      done: candCount > 0, act: "开始发现", go: "discover", need: "先在上一步选择数据源" },
    { name: "起草技能", desc: "把发现的重复任务写成标准技能文档：AI 起草，你来审核确认。",
      done: pending.length > 0 || published.length > 0, act: "起草技能", go: "inbox", need: candCount ? null : "先完成上一步，等系统发现重复任务" },
    { name: "审核发布", desc: "人工确认后技能入库，整个团队都能复用。",
      done: published.length > 0, act: "去审核", go: "inbox", need: pending.length ? null : "先有草稿（draft/in_review）才能审核" },
    { name: "使用", desc: "让 AI 助手在真实任务里用上团队的技能，留下使用记录。",
      done: recCount > 0, act: "去使用", go: "recalls", need: published.length ? null : "先发布至少一个技能" },
  ];
  const allDone = steps.every(s => s.done);
  const currentIdx = steps.findIndex(s => !s.done);
  const stateOf = i => (allDone || i < currentIdx) ? "done" : i === currentIdx ? "current" : "todo";

  // 价值仪表：回答"这个系统帮我产出了什么"（大众首页的第一视觉）
  const hero = el("div", "card hero");
  const heroMain = el("div", "hero-main");
  heroMain.appendChild(el("div", "num", String(recCount)));
  heroMain.appendChild(el("div", "hero-label", "技能被团队使用的次数"));
  const heroSide = el("div", "hero-side");
  heroSide.appendChild(el("div", "hero-line", `已有技能 ${published.length} 个 · 发现重复任务 ${candCount} 组 · 待审核 ${pending.length} 个`));
  const heroLinks = el("div", "home-summary-links");
  const toMetrics = el("button", "action ghost", "全部指标 →");
  toMetrics.onclick = () => navigate("metrics");
  const toRecalls = el("button", "action ghost", "使用记录 →");
  toRecalls.onclick = () => navigate("recalls");
  heroLinks.append(toMetrics, toRecalls);
  heroSide.appendChild(heroLinks);
  hero.append(heroMain, heroSide);
  $view.appendChild(hero);

  // 待办卡：有需要决定的事时出现，活来找用户
  const todoCount = pending.length + (candCount ? 1 : 0);
  if (todoCount > 0) {
    const todo = el("div", "card todo-card");
    const parts = [];
    if (pending.length) parts.push(`${pending.length} 个技能待审核`);
    if (candCount) parts.push(`${candCount} 组重复任务待确认`);
    todo.appendChild(el("span", "todo-text", "有 " + todoCount + " 件事需要你处理：" + parts.join("、") + "。"));
    const goInbox = el("button", "action", "去收件箱 →");
    goInbox.onclick = () => navigate("inbox");
    todo.appendChild(goInbox);
    $view.appendChild(todo);
  }

  // 进度条（①→⑤，当前步高亮，完成打勾）
  const bar = el("div", "wizard-bar");
  steps.forEach((s, i) => {
    const seg = el("div", "wizard-seg " + stateOf(i));
    seg.appendChild(el("span", "wizard-idx", stateOf(i) === "done" ? "✓" : "0" + (i + 1)));
    seg.appendChild(el("span", "wizard-name", s.name));
    bar.appendChild(seg);
  });

  // 五步全部完成：给明确的完成反馈，而不是把所有步骤灰掉
  const wizardBody = el("div", null);
  wizardBody.appendChild(bar);
  if (allDone) {
    const banner = el("div", "wizard-done-banner",
      "✓ 五步已经全部跑通。想继续的话，去运行页发现新任务，或者再多积累几个技能。");
    wizardBody.appendChild(banner);
  }

  // 步骤卡
  const list = el("div", "wizard-steps");
  steps.forEach((s, i) => {
    const st = stateOf(i);
    const card = el("div", "card wizard-step " + st);
    const head = el("div", "wizard-step-head");
    head.appendChild(el("span", "wizard-num", st === "done" ? "✓ 已完成" : "0" + (i + 1) + (st === "current" ? " · 当前" : "")));
    if (st === "current") head.appendChild(el("span", "tag run-bg", "下一步做这个"));
    card.appendChild(head);
    card.appendChild(el("div", "wizard-step-title", s.name));
    card.appendChild(el("p", "wizard-step-desc", s.desc));
    const acts = el("div", "card-actions");
    if (st === "done") {
      const redo = el("button", "action ghost", "再看一下 →");
      redo.onclick = () => navigate(s.go === "discover" ? "pipeline" : s.go);
      acts.appendChild(redo);
    } else if (st === "current") {
      const btn = el("button", "action", s.act + " →");
      btn.onclick = () => {
        if (s.go === "pipeline") { navigate("pipeline"); }
        else if (s.go === "discover") {
          if (pipelineState.source) { pipelineState.autoStart = ["export", "score", "cluster"]; navigate("pipeline"); }
          else showToast(s.need || "请先选择数据源", "err");
        }
        else { navigate(s.go); }
      };
      acts.appendChild(btn);
      if (s.need) acts.appendChild(el("span", "wizard-hint", s.need));
    } else {
      acts.appendChild(el("span", "wizard-hint", s.need || "前面的步骤还没完成"));
    }
    card.appendChild(acts);
    list.appendChild(card);
  });
  wizardBody.appendChild(list);

  // 向导完成后整块收起：首页让位给价值仪表与待办，向导只在需要时被展开
  if (allDone) {
    const fold = el("details", "wizard-fold");
    fold.appendChild(el("summary", null, "任务向导（5 步已全部完成，点开查看）"));
    const foldBody = el("div", "detail-body");
    foldBody.appendChild(wizardBody);
    fold.appendChild(foldBody);
    $view.appendChild(fold);
  } else {
    $view.appendChild(wizardBody);
  }

  // 最近运行
  const runsCard = el("div", "card");
  runsCard.appendChild(el("h3", null, "最近运行"));
  if (runsList.length) {
    const l = el("div", "list");
    for (const r of runsList.slice(0, 5)) {
      const row = el("div", "list-row");
      const main = el("div", "row-main");
      main.appendChild(el("div", "row-title",
        (r.steps || []).map(s => cn(STEP_CN, s)).join(" → ")));
      main.appendChild(el("div", "row-meta",
        (r.duration != null ? "耗时 " + r.duration + " 秒" : "刚提交")));
      row.appendChild(main);
      row.appendChild(el("span", "tag " + r.status, cn(RUN_STATUS_CN, r.status)));
      const btn = el("button", "action ghost", "日志");
      btn.onclick = () => { pipelineState.pendingLoadRun = r.id; navigate("pipeline"); };
      row.appendChild(btn);
      l.appendChild(row);
    }
    runsCard.appendChild(l);
  } else {
    runsCard.appendChild(el("div", "empty", "还没运行过。选好数据源后点“一键发现重复任务”就行。"));
  }
  $view.appendChild(runsCard);
}

/* ---------- 视图：技能库（消费侧：搜索 + hairline 列表） ---------- */
async function renderSkills() {
  const reg = await api("/api/registry");
  const skills = reg.skills || [];
  $view.innerHTML = "";
  $view.appendChild(el("h2", "section-title", `共享技能库（${skills.length} 个）`));
  if (!skills.length) {
    const box = el("div", "empty");
    box.appendChild(el("p", null, "还没有沉淀任何技能。"));
    const go = el("button", "action", "去运行任务发现经验 →");
    go.onclick = () => navigate("pipeline");
    box.appendChild(go);
    $view.appendChild(box);
    return;
  }

  // ≥1100px：搜索结果列表 + 右侧技能页双栏；窄屏：点行进整页详情（原有行为）
  const wide = window.matchMedia("(min-width: 1100px)").matches;
  const search = el("input", "search-box");
  search.type = "text";
  search.placeholder = "搜索技能：名字、用途、适用场景…";
  search.setAttribute("aria-label", "搜索技能");
  const listBox = el("div");

  let detailPane = null;
  if (wide) {
    detailPane = el("div", "lib-detail-pane");
    detailPane.appendChild(el("div", "empty", "从左侧选择一个技能查看。"));
  }

  const draw = q => {
    listBox.innerHTML = "";
    const f = !q ? skills : skills.filter(s =>
      [s.name, s.description, s.when_to_use].some(t => (t || "").toLowerCase().includes(q)));
    if (!f.length) {
      listBox.appendChild(el("div", "empty", "没有匹配的技能。换个词试试，或去运行新的一次发现。"));
      return;
    }
    const list = el("div", "list");
    for (const s of f) {
      const row = el("div", "list-row");
      row.style.cursor = "pointer";
      const main = el("div", "row-main");
      main.appendChild(el("div", "row-title", s.name));
      if (s.description) main.appendChild(el("div", "row-desc", s.description));
      const contrib = s.contributors || {};
      const usage = s.usage || {};
      main.appendChild(el("div", "row-meta",
        `v${s.version} · 贡献者 ${contrib.distinct_agents || 0} 人 · 使用 ${usage.applied_count || 0} 次`));
      row.appendChild(main);
      row.appendChild(el("span", "tag " + s.review_status, cn(STATUS_CN, s.review_status)));
      const open = () => {
        if (wide) {
          listBox.querySelectorAll(".list-row").forEach(x => x.classList.remove("selected"));
          row.classList.add("selected");
          renderSkillDetail(s.name, { pane: detailPane, onBack: () => draw(search.value.trim().toLowerCase()) });
        } else {
          renderSkillDetail(s.name);
        }
      };
      row.onclick = open;
      if (!wide) {
        const btn = el("button", "action", "详情");
        btn.onclick = open;
        row.appendChild(btn);
      }
      list.appendChild(row);
    }
    listBox.appendChild(list);
  };

  search.oninput = () => draw(search.value.trim().toLowerCase());

  if (wide) {
    const layout = el("div", "lib-layout");
    const listPane = el("div", "lib-list-pane");
    listPane.append(search, listBox);
    layout.append(listPane, detailPane);
    $view.appendChild(layout);
  } else {
    $view.append(search, listBox);
  }
  draw("");
  if (wide && skills.length) {
    // 主从双栏惯例：初始自动选中第一条，右侧立即可读
    const firstRow = listBox.querySelector(".list-row");
    if (firstRow) firstRow.click();
  }
}

async function renderSkillDetail(name, opts = {}) {
  const pane = opts.pane || $view;
  const d = await api("/api/skills/" + name);
  // 详情页同步深链：任何入口（搜索/收件箱/列表）打开都可分享、可刷新
  history.replaceState(null, "", "#skill/" + encodeURIComponent(name));
  pane.innerHTML = "";
  const head = el("div", "detail-header");
  head.appendChild(el("span", "tag " + d.review_status, cn(STATUS_CN, d.review_status)));
  head.appendChild(el("h2", null, d.name + " v" + d.version));
  const back = el("button", "action ghost", "← 返回列表");
  back.onclick = () => {
    history.replaceState(null, "", "#skills");
    if (opts.onBack) opts.onBack(); else renderSkills();
  };
  head.appendChild(back);
  const toRecalls = el("button", "action ghost", "使用记录 →");
  toRecalls.onclick = () => navigate("recalls");
  head.appendChild(toRecalls);
  pane.appendChild(head);

  // 人类可读要点：一句话说明 + 使用时机 + 影响面
  if (d.description) pane.appendChild(el("p", "note", d.description));
  if (d.when_to_use) pane.appendChild(el("p", "note", "什么时候用：" + d.when_to_use));
  const contrib = d.contributors || {};
  const usage = d.usage || {};
  pane.appendChild(el("p", "note",
    `由 ${contrib.distinct_agents || 0} 位贡献者的相似经验合并而来` +
    `（${(contrib.agents || []).join("、")}），发布后全团队可用，已被使用 ${usage.applied_count || 0} 次。`));

  const needsReview = d.review_status !== "published" && d.review_status !== "deprecated";
  if (needsReview) {
    // 审核卡：渲染后的要点在前、决策按钮大而明确，10 秒内做出负责任的决定
    const box = el("div", "card review-card");
    box.appendChild(el("h3", null, "审核这份技能"));
    box.appendChild(el("p", null, "下面是整理后的技能内容。确认做法正确、步骤可复现，就通过；有问题就打回，不会进入技能库。"));
    // 快捷决策行在顶部：长文档时不用滚到底也能先拍板（动作可撤销）
    const quick = el("div", "review-actions");
    const quickPass = el("button", "action big", "✓ 通过，发布到技能库");
    quickPass.onclick = () => doReview(name, "published", quickPass, d.review_status);
    const quickReject = el("button", "action big ghost danger", "✗ 打回，标记废弃");
    quickReject.onclick = () => doReview(name, "deprecated", quickReject, d.review_status);
    quick.append(quickPass, quickReject);
    box.appendChild(quick);
    const body = el("div", "md-body");
    body.appendChild(renderMarkdown(d.skill_md));
    box.appendChild(body);
    const actions = el("div", "review-actions");
    const pass = el("button", "action big", "✓ 通过，发布到技能库");
    pass.onclick = () => doReview(name, "published", pass, d.review_status);
    const reject = el("button", "action big ghost danger", "✗ 打回，标记废弃");
    reject.onclick = () => doReview(name, "deprecated", reject, d.review_status);
    actions.append(pass, reject);
    box.appendChild(actions);
    pane.appendChild(box);
  } else {
    pane.appendChild(el("div", "section-title", "技能内容"));
    const body = el("div", "md-body");
    body.appendChild(renderMarkdown(d.skill_md));
    pane.appendChild(body);
  }

  // 技术详情：审核单 / 证据索引 / eval cases / 使用评估（原始数据默认收起）
  const techBlocks = [];
  if (d.review_notes) {
    techBlocks.push(["审核单（原文）", d.review_notes]);
  }
  if (d.evidence_index && Object.keys(d.evidence_index).length) {
    techBlocks.push(["证据索引（溯源）", JSON.stringify(d.evidence_index, null, 2)]);
  }
  if ((d.cases || []).length) {
    for (const c of d.cases) {
      techBlocks.push(["Eval Case " + c.id, c.config]);
    }
  }
  if ((d.eval_results || []).length) {
    for (const e of d.eval_results) {
      techBlocks.push(["使用评估 " + e.file, e.content]);
    }
  }
  if (techBlocks.length) {
    pane.appendChild(techDetails("技术详情（审核单、证据与评估数据）", techBlocks));
  }

  // 审核动作提示（M1 审核工作台）
  if (needsReview) {
    pane.appendChild(el("p", "note",
      "审核动作会直接生效：通过即发布入库；打回即标记废弃。发布后请提交 git 同步给团队。"));
  }

  // 消费侧反馈闭环：技能用着不对劲，随时报告，汇总到收件箱
  if (d.review_status === "published") {
    const rep = el("details", "issue");
    rep.appendChild(el("summary", null, "这个技能有问题？（步骤过时、说法错误、跑不通…）"));
    const body = el("div", "detail-body");
    const ta = el("textarea", "issue-text");
    ta.placeholder = "描述你遇到的问题，比如：第 3 步里提到的工具已经改名了。";
    const submit = el("button", "action", "提交反馈");
    submit.onclick = async () => {
      const text = ta.value.trim();
      if (!text) return showToast("先写两句问题描述", "err");
      submit.disabled = true;
      try {
        await api("/api/skills/" + name + "/issue", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text }),
        });
        ta.value = "";
        showToast("已提交，反馈会出现在收件箱里", "ok");
      } catch (e) {
        showToast("提交失败: " + e.message, "err");
      }
      submit.disabled = false;
    };
    body.append(ta, submit);
    rep.appendChild(body);
    pane.appendChild(rep);
  }
}

async function postReview(name, status) {
  return api("/api/review/" + name, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ status }),
  });
}

async function doReview(name, status, btn, prevStatus) {
  btn.disabled = true;
  try {
    await postReview(name, status);
    if (status === "published") {
      // 庆祝时刻：第一次发布是团队的里程碑，给明确确认而不是一闪而过的 toast
      await renderSkillDetail(name);
      const banner = el("div", "celebrate",
        "✓ 技能已发布到技能库，全团队现在都能用了。下一步可以把它接到更多任务里用起来。");
      $view.insertBefore(banner, $view.firstChild);
      showToast("已发布", "ok", "撤销", async () => {
        await postReview(name, prevStatus || "draft");
        showToast("已撤销，恢复为 " + cn(STATUS_CN, prevStatus || "draft"), "ok");
        renderSkillDetail(name);
      });
    } else {
      // 打回也可撤销（6 秒内），不再用打断式 confirm
      showToast("已打回，标记为废弃", "err", "撤销", async () => {
        await postReview(name, prevStatus || "draft");
        showToast("已撤销，恢复为 " + cn(STATUS_CN, prevStatus || "draft"), "ok");
        renderSkillDetail(name);
      });
      renderSkillDetail(name);
    }
  } catch (e) {
    showToast("审核失败: " + e.message, "err");
    btn.disabled = false;
  }
}

/* ---------- 视图：聚类候选（卡 + "起草"跨视图跳转） ---------- */
const KIND_CN = { procedure: "流程类", other: "其他类" };

/* 聚类候选卡（产品化：任务组序号 + 主题；收件箱与旧 #candidates 路由共用） */
function candidateCard(c, i) {
  const card = el("div", "card");
  const episodes = c.episodes || [];
  const topic = (episodes[0] && episodes[0].goal) || "";
  const head = el("div", "cand-head");
  head.appendChild(el("span", "cand-no", "任务组 " + (i + 1)));
  head.appendChild(el("span", "tag", cn(KIND_CN, c.kind, "任务")));
  card.appendChild(head);
  if (topic) card.appendChild(el("div", "cand-topic", topic));
  const contrib = c.contributors || {};
  const sim = Math.round((c.similarity || 0) * 100);
  card.appendChild(el("p", null,
    `${episodes.length || (c.episode_ids || []).length} 个人或 AI 助手各自做过一次这类工作` +
    `（${(contrib.agents || []).join("、")}），过程相似度 ${sim}%，可以合并成一份技能。`));
  // 人类决策用的结论用产品语言重述；技术性复核原文收进技术详情
  const rv = c.review || {};
  const blocks = [];
  if (rv.note || rv.judgement) {
    blocks.push(["系统复核备注（技术原文）", rv.note || rv.judgement]);
  }
  const actions = el("div", "card-actions");
  const draftBtn = el("button", "action", "合并起草成技能 →");
  draftBtn.onclick = () => {
    pipelineState.draftCandidate = c.candidate_id;
    pipelineState.autoStart = ["draft"];
    navigate("pipeline");
  };
  actions.appendChild(draftBtn);
  card.appendChild(actions);
  // 技术详情：证据 / 内部标识
  if ((c.evidence || []).length) {
    blocks.push(["证据（episode 明细）", JSON.stringify(c.evidence, null, 2)]);
  }
  const ids = (c.episode_ids || []).join(", ");
  if (ids) blocks.push(["关联任务 ID", ids]);
  if (c.review_method) blocks.push(["复核方式", c.review_method]);
  if (c.candidate_id) blocks.push(["候选 ID", c.candidate_id]);
  if (blocks.length) card.appendChild(techDetails("技术详情", blocks));
  return card;
}

/* ---------- 视图：收件箱（需要用户决定的事：待审技能 + 重复任务 + 技能问题反馈） ---------- */
async function renderInbox() {
  const [reg, cand, iss] = await Promise.all([
    api("/api/registry"), api("/api/candidates"), api("/api/skill-issues"),
  ]);
  const pending = (reg.skills || []).filter(x => ["draft", "in_review"].includes(x.review_status));
  const cands = cand.candidates || [];
  const issues = (iss.issues || []).filter(x => x.status === "open");

  $view.innerHTML = "";
  $view.appendChild(el("h2", "section-title", "收件箱"));
  $view.appendChild(el("p", "note", "需要你决定的事都集中在这里：选中左侧一条，右侧直接处理，处理完自动到下一条。"));

  const queue = [
    ...pending.map(x => ({ type: "skill", id: "skill:" + x.name, skill: x })),
    ...cands.map((x, i) => ({ type: "candidate", id: "cand:" + (x.candidate_id || i), cand: x, idx: i })),
    ...issues.map(x => ({ type: "issue", id: "issue:" + x.skill + ":" + x.ts, issue: x })),
  ];

  if (!queue.length) {
    const box = el("div", "empty");
    box.appendChild(el("p", null, "没有需要你处理的事。系统发现新技能或重复任务时，会出现在这里。"));
    const go = el("button", "action", "去运行任务发现经验 →");
    go.onclick = () => navigate("pipeline");
    box.appendChild(go);
    $view.appendChild(box);
    return;
  }

  const state = { handled: new Set(), current: null };

  const layout = el("div", "inbox-layout");
  const listPane = el("div", "inbox-list");
  const detailPane = el("div", "inbox-detail");
  layout.append(listPane, detailPane);
  $view.appendChild(layout);

  const qTypeLabel = { skill: "技能审核", candidate: "重复任务", issue: "问题反馈" };

  function rowFor(item) {
    const row = el("button", "queue-row" + (state.current === item.id ? " selected" : ""));
    row.type = "button";
    const title = el("div", "q-title");
    title.appendChild(el("span", "q-type", qTypeLabel[item.type]));
    row.appendChild(title);
    if (item.type === "skill") {
      title.appendChild(el("span", null, item.skill.name));
      if (item.skill.description) row.appendChild(el("div", "q-sub", item.skill.description));
    } else if (item.type === "candidate") {
      const topic = (item.cand.episodes || [])[0];
      title.appendChild(el("span", null, (topic && topic.goal) || "重复任务组 " + (item.idx + 1)));
      row.appendChild(el("div", "q-sub",
        ((item.cand.episodes || []).length) + " 个人或 AI 助手做过高度相似的工作"));
    } else {
      title.appendChild(el("span", null, item.issue.skill));
      row.appendChild(el("div", "q-sub", item.issue.text));
    }
    row.onclick = () => { state.current = item.id; renderQueue(); renderDetail(item); };
    return row;
  }

  function renderQueue() {
    listPane.innerHTML = "";
    const rest = queue.filter(x => !state.handled.has(x.id));
    const head = el("div", "inbox-list-head", rest.length ? "待处理（" + rest.length + "）" : "已全部处理");
    listPane.appendChild(head);
    for (const item of rest) listPane.appendChild(rowFor(item));
  }

  function renderDetail(item) {
    detailPane.innerHTML = "";
    const head = el("div", "q-detail-head");
    head.appendChild(el("span", "tag " + (item.type === "skill" ? item.skill.review_status : "mut"),
      item.type === "skill" ? cn(STATUS_CN, item.skill.review_status) : qTypeLabel[item.type]));
    detailPane.appendChild(head);

    if (item.type === "skill") {
      const sk = item.skill;
      detailPane.appendChild(el("h3", "q-detail-title", sk.name + " v" + sk.version));
      if (sk.description) detailPane.appendChild(el("p", "q-detail-note", sk.description));
      const box = el("div", "card review-card");
      box.appendChild(el("p", null, "由 " + ((sk.contributors || {}).distinct_agents || 0) +
        " 位贡献者的相似经验合并而来。确认做法正确、步骤可复现，就通过；有问题就打回。"));
      const body = el("div", "md-body");
      box.appendChild(body);
      const actions = el("div", "review-actions");
      const pass = el("button", "action big", "✓ 通过，发布到共享库");
      const reject = el("button", "action big ghost danger", "✗ 打回，标记废弃");
      const busy = b => { pass.disabled = b; reject.disabled = b; };
      pass.onclick = () => inboxReview(sk.name, "published", sk.review_status, busy);
      reject.onclick = () => inboxReview(sk.name, "deprecated", sk.review_status, busy);
      actions.append(pass, reject);
      box.appendChild(actions);
      detailPane.appendChild(box);
      api("/api/skills/" + encodeURIComponent(sk.name))
        .then(d => { body.appendChild(renderMarkdown(d.skill_md || "")); })
        .catch(() => { body.appendChild(el("p", "md-p", "技能内容加载失败，可前往技能库查看。")); });
    } else if (item.type === "candidate") {
      detailPane.appendChild(candidateCard(item.cand, item.idx));
    } else {
      const box = el("div", "card");
      box.appendChild(el("h3", "q-detail-title", item.issue.skill));
      box.appendChild(el("p", "md-p", item.issue.text));
      box.appendChild(el("p", "q-detail-note", "反馈时间：" + fmtTs(item.issue.ts)));
      const go = el("button", "action", "打开技能页处理 →");
      go.onclick = () => renderSkillDetail(item.issue.skill);
      box.appendChild(go);
      detailPane.appendChild(box);
    }
  }

  async function inboxReview(name, status, prevStatus, busy) {
    busy(true);
    try {
      await postReview(name, status);
      showToast(status === "published" ? "已发布" : "已打回", status === "published" ? "ok" : "err",
        "撤销", async () => {
          await postReview(name, prevStatus || "draft");
          state.handled.delete("skill:" + name);
          renderQueue();
          showToast("已撤销，恢复为 " + cn(STATUS_CN, prevStatus || "draft"), "ok");
        });
      state.handled.add("skill:" + name);
      advance();
    } catch (e) {
      busy(false);
      showToast("审核失败: " + e.message, "err");
    }
  }

  function advance() {
    renderQueue();
    const rest = queue.filter(x => !state.handled.has(x.id));
    if (!rest.length) {
      detailPane.innerHTML = "";
      detailPane.appendChild(el("div", "celebrate", "✓ 全部处理完毕。系统有新发现时会再回到这里。"));
      return;
    }
    state.current = rest[0].id;
    renderQueue();
    renderDetail(rest[0]);
  }

  renderQueue();
  advance();
}

async function renderCandidates() {
  const d = await api("/api/candidates");
  $view.innerHTML = "";
  $view.appendChild(el("h2", "section-title", `发现的重复任务（${(d.candidates || []).length} 个）`));
  $view.appendChild(el("p", "note",
    "以下是系统在团队工作记录里发现的重复任务：多个人或 AI 助手各自做过高度相似的工作。确认后可以合并成一份团队技能。"));
  const list = d.candidates || [];
  if (!list.length) {
    const box = el("div", "empty");
    box.appendChild(el("p", null, "暂时没有发现重复任务。没有重复也属正常，换一批工作记录后再试。"));
    const go = el("button", "action", "先去运行发现任务 →");
    go.onclick = () => navigate("pipeline");
    box.appendChild(go);
    $view.appendChild(box);
    return;
  }
  for (let i = 0; i < list.length; i++) {
    $view.appendChild(candidateCard(list[i], i));
  }
  if (d.no_candidate) {
    $view.appendChild(el("p", "note", "本轮另有未形成重复组的任务记录（详情见接口数据）。"));
  }
}

/* ---------- 视图：指标 ---------- */
async function renderMetrics() {
  const m = await api("/api/metrics");
  $view.innerHTML = "";
  $view.appendChild(el("h2", "section-title", "指标"));
  $view.appendChild(el("p", "note", "这些数字反映团队积累经验的效果：使用次数代表有多少人在用，实际做得好不好由每次使用的评估判定。"));
  const grid = el("div", "metric");
  for (const [k, v] of Object.entries(m)) {
    if (typeof v === "object" && "value" in v) {
      const c = el("div", "card");
      // 后端拼好的口径字符串里的打分行话（"高分"）在展示层转成白话
      const numText = String(v.value).replace("高分", "达标").replace("总 ", "共 ");
      c.appendChild(el("div", "num", numText));
      c.appendChild(el("div", "label", cn(METRIC_CN, k, k)));
      c.appendChild(el("div", "note", METRIC_NOTE[k] || v.note || ""));
      grid.appendChild(c);
    }
  }
  $view.appendChild(grid);
  const st = m["skill 状态"];
  if (st) {
    const c = el("div", "card");
    c.appendChild(el("p", null, "已发布 " + st.published + " · 已废弃 " + st.deprecated +
      " · 草稿/审核中 " + st["draft/in_review"] + " · 参与贡献的 AI 助手 " + st.distinct_appliers + " 个"));
    $view.appendChild(c);
  }
}

/* ---------- 视图：回采记录（hairline 列表 + 可展开输出） ---------- */
async function renderRecalls() {
  const runs = await api("/api/recalls");
  $view.innerHTML = "";
  $view.appendChild(el("h2", "section-title", `使用记录（${runs.length} 次）`));
  $view.appendChild(el("p", "note",
    "这里的记录来自 AI 助手在真实任务中使用技能后的回传：“已应用”代表它实际遵循了技能步骤，“未应用”代表调用了但没有遵循。"));
  if (!runs.length) {
    // 第 5 步的完成路径在 UI 之外：空状态必须告诉用户怎么让 AI 助手用起来
    const box = el("div", "empty");
    box.appendChild(el("p", null, "还没有使用记录。技能发布后，让团队的 AI 助手在真实任务里使用它，就会留下记录。做法分两步："));
    const how = el("div", "recall-how");
    how.appendChild(el("div", null, "① 把技能接入你的 AI 助手：让助手读取技能库里的技能文档（skills/<技能名>/SKILL.md），按文档步骤执行真实任务。"));
    how.appendChild(el("div", null, "② 任务完成后，在命令行记录本次使用（在项目目录下执行）："));
    const cmd = el("pre", "code", "python cli/main.py recall --skill <技能名>");
    how.appendChild(cmd);
    box.appendChild(how);
    const go = el("button", "action", "去技能库选一个技能 →");
    go.onclick = () => navigate("skills");
    box.appendChild(go);
    $view.appendChild(box);
    return;
  }
  const list = el("div", "list");
  for (const r of runs) {
    const res = r.result || {};
    const item = el("div", "list-item");
    const row = el("div", "list-row");
    const main = el("div", "row-main");
    main.appendChild(el("div", "row-title", (res.skill_id || "-") + " v" + (res.version || "-")));
    const followed = (res.steps_followed || []).length;
    main.appendChild(el("div", "row-meta",
      `${fmtTs(res.ts)} · AI 助手 ${res.agent_id || "-"} · ` +
      (followed ? `遵循 ${followed} 个步骤 · ` : "") +
      `产出 ${(res.products || []).length} 件`));
    row.appendChild(main);
    row.appendChild(el("span", "tag " + (res.applied ? "true" : "false"),
      res.applied ? "已应用" : "未应用"));
    const btn = el("button", "action ghost", "查看产出");
    const pre = el("pre", "code");
    pre.textContent = (r.output || "").slice(0, 800);
    pre.style.display = "none";
    btn.onclick = () => {
      const open = pre.style.display !== "none";
      pre.style.display = open ? "none" : "";
      btn.textContent = open ? "查看产出" : "收起";
    };
    row.appendChild(btn);
    item.appendChild(row);
    item.appendChild(pre);
    list.appendChild(item);
  }
  $view.appendChild(list);
}

/* ---------- 视图：运行任务（原流水线；封装内部参数） ---------- */
const pipelineState = {
  source: null, adapter: "auto", threshold: "", exclude: "", backend: "", skill: "",
  runId: null, cursor: 0, timer: null, logLines: 0, logBox: null, stepBar: null,
  userUp: false, pendingLogs: 0, draftCandidate: "", autoStart: null, pendingLoadRun: null,
};

async function renderPipeline() {
  $view.innerHTML = "";
  $view.appendChild(el("h2", "section-title", "运行任务"));
  $view.appendChild(el("p", "note", "选择一份团队工作记录作为数据源，点“一键发现重复任务”，系统会自动完成读取、评估、发现与起草，不需要其他操作。"));

  // ① 数据源选择
  // 工作台分栏（≥960px）：左=控制（数据源/高级设置/启动），右=监控（日志/历史）
  const pipelineCols = { left: el("div", "run-col-left"), right: el("div", "run-col-right") };
  const runLayout = el("div", "run-layout");
  runLayout.append(pipelineCols.left, pipelineCols.right);
  $view.appendChild(runLayout);

  const srcCard = el("div", "card");
  srcCard.appendChild(el("h3", null, "① 数据源"));
  const srcBox = el("div", "pipeline-sources");
  srcBox.appendChild(el("div", "empty", "加载中…"));
  srcCard.appendChild(srcBox);
  pipelineCols.left.append(srcCard);
  try {
    const d = await api("/api/sources");
    srcBox.innerHTML = "";
    for (const s of d.sources) {
      const c = el("button", "pipeline-source" + (s.adapter ? "" : " unusable"));
      c.appendChild(el("span", "pipeline-source-name", s.name));
      // 内容量提示：优先记录数，其次文件大小（帮新用户判断"哪个源有东西"）
      const meta = s.count != null ? `${s.count} 条记录`
        : s.size_kb != null ? `约 ${s.size_kb} KB` : "";
      if (meta) c.appendChild(el("span", "pipeline-source-meta", meta));
      c.appendChild(el("span", "tag " + (s.adapter ? "mut" : "unknown"),
        s.adapter ? cn(ADAPTER_CN, s.adapter) : "无法识别"));
      c.onclick = () => {
        pipelineState.source = s.path;
        srcBox.querySelectorAll(".pipeline-source").forEach(x => x.classList.remove("selected"));
        c.classList.add("selected");
        showToast("已选择: " + s.name, "ok");
      };
      srcBox.appendChild(c);
    }
  } catch (e) { srcBox.innerHTML = ""; srcBox.appendChild(el("div", "empty", "数据源加载失败: " + e.message)); }

  // ② 高级设置（默认收起：绝大多数场景不用改）
  const adv = el("details", "advanced");
  adv.appendChild(el("summary", null, "高级设置（保持默认即可）"));
  const advBody = el("div", "detail-body");
  const cfgRow = el("div", "pipeline-config");
  const mkField = (label, id, def) => {
    const w = el("label", "pipeline-field");
    w.appendChild(el("span", null, label));
    const inp = el("input", null);
    inp.id = id; inp.value = def;
    w.appendChild(inp);
    return w;
  };
  const adapterSel = mkField("适配器", "pl-adapter", "auto");
  adapterSel.querySelector("input").remove();
  const sel = el("select"); sel.id = "pl-adapter";
  for (const a of ["auto", "log-export", "task-dirs", "table", "session-logs", "git-repo", "docs"]) {
    const o = el("option", null, a); o.value = a; sel.appendChild(o);
  }
  adapterSel.appendChild(sel);
  cfgRow.append(
    adapterSel,
    mkField("粗筛阈值", "pl-threshold", "（按形态默认）"),
    mkField("排除 agents（逗号分隔）", "pl-exclude", ""),
    mkField("LLM 后端", "pl-backend", "（默认 kimi）"),
  );
  advBody.appendChild(cfgRow);
  adv.appendChild(advBody);
  pipelineCols.left.append(adv);

  // ③ 运行
  const runCard = el("div", "card");
  runCard.appendChild(el("h3", null, "② 运行"));
  const actions = el("div", "card-actions");
  const btnDiscover = el("button", "action", "一键发现重复任务");
  btnDiscover.onclick = () => startRun(["export", "score", "cluster", "draft"]);
  // 技能选择下拉（发布 / 回采不需要手敲名字）
  const skillWrap = el("label", "pipeline-field");
  skillWrap.appendChild(el("span", null, "选择技能（发布 / 记录使用）"));
  const skillSel = el("select"); skillSel.id = "pl-skill";
  const seedSkill = el("option", null, "加载中…"); seedSkill.value = ""; skillSel.appendChild(seedSkill);
  try {
    const reg = await api("/api/registry");
    const ss = reg.skills || [];
    skillSel.innerHTML = "";
    if (ss.length) {
      for (const s of ss) {
        const o = el("option", null, s.name + "（" + cn(STATUS_CN, s.review_status) + "）");
        o.value = s.name; skillSel.appendChild(o);
      }
    } else {
      const o = el("option", null, "暂无技能，先一键发现重复任务"); o.value = ""; skillSel.appendChild(o);
    }
  } catch (e) {
    skillSel.innerHTML = "";
    const o = el("option", null, "技能列表加载失败"); o.value = ""; skillSel.appendChild(o);
  }
  skillWrap.appendChild(skillSel);
  const btnPublish = el("button", "action ghost", "发布");
  btnPublish.onclick = () => startRun(["publish"]);
  const btnRecall = el("button", "action ghost", "记录使用");
  btnRecall.onclick = () => startRun(["recall"]);
  const btnCancel = el("button", "action ghost danger", "取消当前运行");
  btnCancel.onclick = async () => {
    if (!pipelineState.runId) return showToast("当前无运行", "err");
    await api("/api/pipeline/runs/" + pipelineState.runId + "/cancel", { method: "POST" });
    showToast("已请求取消", "ok");
  };
  actions.append(btnDiscover, skillWrap, btnPublish, btnRecall, btnCancel);
  runCard.appendChild(actions);
  // AI 引擎状态：发现/起草依赖它，未配置时提前告知而不是跑到一半失败
  const llmLine = el("p", "llm-status", "AI 引擎检查中…");
  runCard.appendChild(llmLine);
  api("/api/llm_status").then(st => {
    llmLine.innerHTML = "";
    llmLine.appendChild(el("span", null, "AI 引擎（自动评估与起草用）：" + st.backend + " "));
    const tag = el("span", "tag " + (st.available === true ? "true" : st.available === false ? "false" : "mut"));
    tag.textContent = st.available === true ? "已就绪"
      : st.available === false ? "未检测到，发现/起草会失败"
      : "无法自动检测，请确保网关可用";
    llmLine.appendChild(tag);
    if (st.available === false) {
      llmLine.appendChild(el("span", "llm-hint",
        " 安装对应命令行工具（如 kimi）到 PATH，或在上方高级设置里更换。"));
    }
  }).catch(() => { llmLine.textContent = ""; });
  pipelineCols.left.append(runCard);

  // ④ 当前运行：步骤条 + 日志 + 完成后跳转
  const runCard2 = el("div", "card");
  pipelineState.stepBar = el("div", "pipeline-steps");
  runCard2.appendChild(el("h3", null, "③ 当前运行"));
  runCard2.appendChild(pipelineState.stepBar);
  runCard2.appendChild(el("div", "pipeline-log-label", "运行日志"));
  pipelineState.logBox = el("div", "pipeline-log");
  pipelineState.logBox.onscroll = () => {
    const near = pipelineState.logBox.scrollHeight - pipelineState.logBox.scrollTop - pipelineState.logBox.clientHeight < 60;
    pipelineState.userUp = !near;
  };
  runCard2.appendChild(pipelineState.logBox);
  pipelineState.jumps = el("div", "pipeline-jumps");
  runCard2.appendChild(pipelineState.jumps);
  pipelineCols.right.append(runCard2);

  // ⑤ 运行历史
  const histCard = el("div", "card");
  histCard.appendChild(el("h3", null, "④ 运行历史"));
  const histBox = el("div", null);
  histCard.appendChild(histBox);
  pipelineCols.right.append(histCard);
  const renderHist = async () => {
    try {
      const d = await api("/api/pipeline/runs");
      histBox.innerHTML = "";
      if (!d.runs.length) { histBox.appendChild(el("div", "empty", "暂无运行记录。")); return; }
      for (const r of d.runs) {
        const row = el("div", "pipeline-history-row");
        row.appendChild(el("span", "tag " + r.status, cn(RUN_STATUS_CN, r.status)));
        row.appendChild(el("span", null,
          (r.steps || []).map(s => cn(STEP_CN, s)).join(" → ") +
          (r.duration != null ? " · 耗时 " + r.duration + " 秒" : "")));
        const btn = el("button", "action ghost", "查看日志");
        btn.onclick = () => loadRun(r.id);
        row.appendChild(btn);
        histBox.appendChild(row);
      }
    } catch (e) { histBox.innerHTML = ""; histBox.appendChild(el("div", "empty", "历史加载失败: " + e.message)); }
  };
  renderHist();
  setInterval(renderHist, 8000);

  // 从候选视图跳转而来：自动起草指定候选（人类工作流：发现→起草）
  if (pipelineState.autoStart) {
    const steps = pipelineState.autoStart;
    pipelineState.autoStart = null;
    setTimeout(() => startRun(steps), 250);
  }
  // 从总览跳转而来：直接加载指定运行日志
  if (pipelineState.pendingLoadRun) {
    const id = pipelineState.pendingLoadRun;
    pipelineState.pendingLoadRun = null;
    setTimeout(() => loadRun(id), 250);
  }
}

function collectConfig() {
  return {
    adapter: document.getElementById("pl-adapter")?.value || "auto",
    threshold: document.getElementById("pl-threshold")?.value || "",
    exclude_agents: document.getElementById("pl-exclude")?.value || "",
    llm_backend: document.getElementById("pl-backend")?.value || "",
    skill: document.getElementById("pl-skill")?.value || "",
    candidate: pipelineState.draftCandidate,
  };
}

async function startRun(steps) {
  const cfg = collectConfig();
  if (steps.includes("export") && !pipelineState.source) return showToast("请先选择数据源", "err");
  if ((steps.includes("publish") || steps.includes("recall")) && !cfg.skill) return showToast("请先选择技能", "err");
  try {
    const r = await api("/api/pipeline/run", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ steps, source: pipelineState.source || "", ...cfg }),
    });
    loadRun(r.run_id);
    showToast("已提交: " + steps.map(s => cn(STEP_CN, s)).join(" → "), "ok");
  } catch (e) { showToast("提交失败: " + e.message, "err"); }
}

async function loadRun(runId) {
  pipelineState.runId = runId;
  pipelineState.cursor = 0;
  pipelineState.logLines = 0;
  pipelineState.userUp = false;
  pipelineState.pendingLogs = 0;
  if (pipelineState.logBox) pipelineState.logBox.innerHTML = "";
  if (pipelineState.timer) clearInterval(pipelineState.timer);
  pipelineState.timer = setInterval(() => pollRun(runId), 1500);
  pollRun(runId);
}

async function pollRun(runId) {
  try {
    const [detail, logs] = await Promise.all([
      api("/api/pipeline/runs/" + runId),
      api("/api/pipeline/runs/" + runId + "/logs?after=" + pipelineState.cursor),
    ]);
    // 步骤条
    if (pipelineState.stepBar) {
      pipelineState.stepBar.innerHTML = "";
      for (const st of detail.step_states) {
        const b = el("span", "pipeline-step " + st.status,
          cn(STEP_CN, st.name) + (st.duration != null ? " " + st.duration + "s" : ""));
        pipelineState.stepBar.appendChild(b);
      }
    }
    // 日志增量追加：钉底跟随；用户上滚时显示"新日志 N 条"胶囊（不打断）
    if (pipelineState.logBox && logs.lines.length) {
      for (const line of logs.lines) {
        pipelineState.logBox.appendChild(el("div", null, line ?? ""));
      }
      pipelineState.cursor = logs.next;
      if (!pipelineState.userUp) {
        pipelineState.logBox.scrollTop = pipelineState.logBox.scrollHeight;
        pipelineState.pendingLogs = 0;
        const old = pipelineState.logBox.querySelector(".log-new");
        if (old) old.remove();
      } else {
        pipelineState.pendingLogs += logs.lines.length;
        let badge = pipelineState.logBox.querySelector(".log-new");
        if (!badge) {
          badge = el("button", "log-new");
          badge.onclick = () => {
            pipelineState.logBox.scrollTop = pipelineState.logBox.scrollHeight;
            pipelineState.userUp = false;
            pipelineState.pendingLogs = 0;
            badge.remove();
          };
          pipelineState.logBox.appendChild(badge);
        }
        badge.textContent = "▼ 新日志 " + pipelineState.pendingLogs + " 条";
      }
    }
    if (logs.done) {
      clearInterval(pipelineState.timer);
      pipelineState.timer = null;
      if (detail.status === "success") {
        showToast("运行完成（" + (detail.duration || 0) + " 秒）", "ok");
        // 人类工作流跳转：跑完发现链 → 去看候选；起草完成 → 去看 Skill 库
        if (pipelineState.jumps) {
          pipelineState.jumps.innerHTML = "";
          const hint = el("span", "pipeline-jump-hint", "下一步：");
          const toCand = el("button", "action ghost", "去收件箱处理 →");
          toCand.onclick = () => navigate("inbox");
          const toSkills = el("button", "action ghost", "查看技能库 →");
          toSkills.onclick = () => navigate("skills");
          pipelineState.jumps.append(hint, toCand, toSkills);
        }
      } else if (detail.status === "failed") {
        showToast("运行失败: " + (detail.error || ""), "err");
      }
    }
  } catch (e) {
    clearInterval(pipelineState.timer);
    pipelineState.timer = null;
  }
}

/* ---------- 导航 ---------- */
async function navigate(view) {
  current = view;
  document.querySelectorAll("nav button").forEach(b => b.classList.toggle("active", b.dataset.view === view));
  // 加载反馈：骨架占位（Nielsen #1 状态可见性；不改变视图逻辑）
  $view.innerHTML = "";
  const sk = el("div", null);
  sk.appendChild(el("div", "skeleton line"));
  sk.appendChild(el("div", "skeleton line"));
  sk.appendChild(el("div", "skeleton block"));
  $view.appendChild(sk);
  try {
    if (view === "overview") await renderOverview();
    else if (view === "skills") await renderSkills();
    else if (view === "inbox") await renderInbox();
    else if (view === "candidates") await renderCandidates();
    else if (view === "metrics") await renderMetrics();
    else if (view === "recalls") await renderRecalls();
    else if (view === "pipeline") await renderPipeline();
  } catch (e) {
    $view.innerHTML = "";
    const box = el("div", "empty");
    box.appendChild(el("p", null, "加载失败: " + e.message));
    const retry = el("button", "action", "重试");
    retry.onclick = () => navigate(view);
    box.appendChild(retry);
    $view.appendChild(box);
  }
}

document.querySelectorAll("nav button").forEach(b => b.onclick = () => {
  navigate(b.dataset.view);
  history.replaceState(null, "", "#" + b.dataset.view);
});
// 深链支持：http://127.0.0.1:8000/#pipeline 直达对应视图；#skill/<名字> 直达技能详情；默认着陆首页
const initial = (location.hash || "#overview").slice(1);
if (initial.startsWith("skill/")) {
  document.querySelectorAll("nav button").forEach(b => b.classList.toggle("active", b.dataset.view === "skills"));
  const sk = el("div", null);
  sk.appendChild(el("div", "skeleton line"));
  sk.appendChild(el("div", "skeleton block"));
  $view.innerHTML = "";
  $view.appendChild(sk);
  renderSkillDetail(decodeURIComponent(initial.slice(6))).catch(e => {
    $view.innerHTML = "";
    const box = el("div", "empty");
    box.appendChild(el("p", null, "技能加载失败: " + e.message));
    const retry = el("button", "action", "去技能库");
    retry.onclick = renderSkills;
    box.appendChild(retry);
    $view.appendChild(box);
  });
} else {
  navigate(views.includes(initial) ? initial : "overview");
}
