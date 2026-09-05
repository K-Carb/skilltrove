"""F4 审核辅助 + F5 发布入库（Spec §4.5，registry.py）。

职责：
- registry.json 读写（不存在时初始化为空索引，Spec §2.2 / §3.3）
- 条目结构校验 validate_entry（字段与 skill.meta.yaml 同名同义）
- 审核状态生命周期：draft -> in_review -> published -> deprecated（合法迁移表写死）
- 入库前查重 find_duplicate（F4.1）：name 精确匹配 + 描述 bigram 余弦相似度（仅对比已发布条目）
- publish（F5）：校验 skill 文件夹完整性 -> 查重 -> 更新/新建条目 -> 写回 registry.json
- review-helper（F4.2）：生成审核单 skills/<name>/references/review-notes.md

默认路径 <仓库根>/registry/registry.json（仓库根 = cli/ 上级目录；CWD 在仓库根时
相对路径 registry/registry.json 等价），可用 --registry 覆盖。
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import sys
import tempfile
import time
from collections import Counter

# 统一 UTF-8 输出（Windows 控制台/管道默认 GBK，Git Bash 按 UTF-8 解析）
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

SCHEMA = "skilltrove-registry-v1"

# 审核状态合法迁移表（写死，Spec §4.5：draft -> in_review -> published -> deprecated）
STATUS_FLOW: dict[str, list[str]] = {
    "draft": ["in_review"],
    "in_review": ["published"],
    "published": ["deprecated"],
    "deprecated": [],
}

# 证据引用行识别（Spec §4.4：每条 Step 引用真实 trace 位置 = issue key + 时间戳 + 段落）
_EVIDENCE_RE = re.compile(r"证据|引用|trace|WIKI-\d+", re.IGNORECASE)
_AGEN_RE = re.compile(r"WIKI-\d+")

# skill 名白名单（路径安全：仅小写字母/数字开头，可含短横线；禁止任何路径字符）
SKILL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def validate_skill_name(name: str) -> list[str]:
    """校验 skill 名是否为安全 slug（路径白名单，防路径穿越）。

    skill 名会被直接拼进 skills/<name>/ 等路径，必须是
    `^[a-z0-9][a-z0-9-]*$` 形式（如 implementation-research），
    拒绝空名、大写、空格、`..`、`/`、`\\`、冒号等任何路径字符。
    返回错误列表（空 = 合法）。
    """
    if not name:
        return ["skill 名不能为空"]
    if not SKILL_NAME_RE.match(name):
        return [
            f"skill 名非法: {name!r}（仅允许小写字母/数字开头、可含短横线的小写 slug，"
            "禁止路径分隔符与 '..'）"
        ]
    return []


# ---------------------------------------------------------------------------
# 路径约定
# ---------------------------------------------------------------------------

def default_registry_path() -> str:
    """默认 registry.json：<仓库根>/registry/registry.json（仓库根 = cli/ 的上级）。"""
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(repo_root, "registry", "registry.json")


def skills_root_for(registry_path: str) -> str:
    """从 registry 位置推断共享库根：<root>/registry/registry.json -> <root>/skills/。"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(registry_path)))
    return os.path.join(root, "skills")


# ---------------------------------------------------------------------------
# registry.json 读写
# ---------------------------------------------------------------------------

def load_registry(path: str) -> dict:
    """读取 registry.json；不存在时初始化为空索引（不写盘，由 save 落盘）。"""
    if not os.path.isfile(path):
        return {"schema": SCHEMA, "skills": []}
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"registry 格式错误（应为 JSON 对象）: {path}")
    if not isinstance(data.get("skills"), list):
        data["skills"] = []
    if not data.get("schema"):
        data["schema"] = SCHEMA
    return data


def save_registry(registry: dict, path: str) -> None:
    """原子写：先落临时文件再 os.replace，写一半中断不会损坏唯一事实源。

    替换前把旧内容留一份 .bak（runbook 第 4 条的恢复依据）。"""
    import os
    import shutil

    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(registry, f, ensure_ascii=False, indent=2)
    if os.path.exists(path):
        shutil.copyfile(path, path + ".bak")
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# 条目结构（Spec §3.3）与校验
# ---------------------------------------------------------------------------

def find_entry(registry: dict, name: str) -> dict | None:
    for e in registry.get("skills", []):
        if e.get("name") == name:
            return e
    return None


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def validate_entry(entry: dict) -> list[str]:
    """校验条目必填字段与类型（Spec §3.3），返回错误列表（空 = 合法）。"""
    errors: list[str] = []
    required = ["name", "version", "owner", "layer", "action_level", "author",
                "source_runs", "contributors", "review_status", "usage", "path"]
    for k in required:
        if k not in entry:
            errors.append(f"缺必填字段: {k}")

    if "name" in entry and not (isinstance(entry["name"], str) and entry["name"].strip()):
        errors.append("name 必须是非空字符串")
    for k in ["version", "owner", "layer", "action_level", "author", "path"]:
        if k in entry and not isinstance(entry[k], str):
            errors.append(f"{k} 必须是字符串")
    if "review_status" in entry and entry["review_status"] not in STATUS_FLOW:
        errors.append(f"review_status 非法: {entry.get('review_status')!r}（合法: {'/'.join(STATUS_FLOW)}）")

    src = entry.get("source_runs")
    if src is not None and (not isinstance(src, list) or not all(isinstance(s, str) for s in src)):
        errors.append("source_runs 必须是字符串数组")

    contrib = entry.get("contributors")
    if contrib is not None:
        if not isinstance(contrib, dict):
            errors.append("contributors 必须是对象 {distinct_agents, agents}")
        else:
            if not _is_int(contrib.get("distinct_agents")) or contrib["distinct_agents"] < 0:
                errors.append("contributors.distinct_agents 必须是非负整数")
            agents = contrib.get("agents")
            if not isinstance(agents, list) or not all(isinstance(a, str) for a in agents):
                errors.append("contributors.agents 必须是字符串数组")

    usage = entry.get("usage")
    if usage is not None:
        if not isinstance(usage, dict):
            errors.append("usage 必须是对象 {applied_count, last_applied_at, distinct_appliers}")
        else:
            if not _is_int(usage.get("applied_count")) or usage["applied_count"] < 0:
                errors.append("usage.applied_count 必须是非负整数")
            if not _is_int(usage.get("distinct_appliers")) or usage["distinct_appliers"] < 0:
                errors.append("usage.distinct_appliers 必须是非负整数")
            la = usage.get("last_applied_at")
            if la is not None and not isinstance(la, str):
                errors.append("usage.last_applied_at 必须是字符串或 null")
    return errors


# ---------------------------------------------------------------------------
# 审核状态生命周期
# ---------------------------------------------------------------------------

def set_status(registry: dict, name: str, new_status: str) -> None:
    """按合法迁移表迁移审核状态；非法迁移抛 ValueError（不修改 registry）。

    同状态调用为幂等 no-op（重新发布同一状态不报错）。
    """
    if new_status not in STATUS_FLOW:
        raise ValueError(f"未知审核状态: {new_status!r}（合法: {'/'.join(STATUS_FLOW)}）")
    entry = find_entry(registry, name)
    if entry is None:
        raise ValueError(f"registry 中无条目: {name!r}")
    cur = entry["review_status"]
    if new_status == cur:
        return
    if new_status not in STATUS_FLOW[cur]:
        raise ValueError(
            f"非法状态迁移: {cur} -> {new_status}（合法路径: draft -> in_review -> published -> deprecated）")
    entry["review_status"] = new_status


# ---------------------------------------------------------------------------
# 入库前查重（F4.1）：name 精确匹配 + 描述 bigram 余弦
# ---------------------------------------------------------------------------

def _bigrams(text: str) -> list[str]:
    """字符 bigram 切分（去空白，小写）；短于 2 字符退化为单字符。"""
    chars = [c.lower() for c in text if not c.isspace()]
    if len(chars) < 2:
        return list(chars)
    return [chars[i] + chars[i + 1] for i in range(len(chars) - 1)]


def bigram_similarity(a: str, b: str) -> float:
    """字符 bigram 频率向量余弦相似度（纯标准库手写，Spec §5.2 简化版）。"""
    if not a.strip() or not b.strip():
        return 0.0
    va, vb = Counter(_bigrams(a)), Counter(_bigrams(b))
    dot = sum(va[g] * vb[g] for g in va if g in vb)
    na = math.sqrt(sum(v * v for v in va.values()))
    nb = math.sqrt(sum(v * v for v in vb.values()))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _entry_description(entry: dict, skills_root: str) -> str:
    """读条目对应 SKILL.md 的 description（frontmatter），读不到返回空串。

    skills_root 已是 skills/ 目录，故按条目名拼接；path 字段（skills/<name>，
    相对仓库根）仅作兜底解析。
    """
    name = entry.get("name")
    if not skills_root or not name:
        return ""
    md = os.path.join(skills_root, name, "SKILL.md")
    if os.path.isfile(md):
        return read_frontmatter(md).get("description", "") or ""
    rel = entry.get("path")
    if rel and rel.startswith("skills/"):
        md = os.path.join(skills_root, rel[len("skills/"):], "SKILL.md")
        if os.path.isfile(md):
            return read_frontmatter(md).get("description", "") or ""
    return ""


def find_duplicate(registry: dict, name: str, description: str,
                   skills_root: str | None = None, threshold: float = 0.5) -> list[dict]:
    """入库前查重（F4.1）：name 精确匹配（任意状态）+ 描述 bigram 相似度 >= threshold（仅已发布条目）。

    skills_root 用于读取已发布条目的 SKILL.md 描述（描述存于 frontmatter 而非 registry 条目）；
    为 None 时只做 name 精确匹配。返回疑似重复条目列表。
    """
    dups: list[dict] = []
    seen: set[int] = set()
    for e in registry.get("skills", []):
        if e.get("name") == name:
            dups.append(e)
            seen.add(id(e))
    if skills_root and description:
        for e in registry.get("skills", []):
            if e.get("review_status") != "published" or id(e) in seen:
                continue
            d = _entry_description(e, skills_root)
            if d and bigram_similarity(description, d) >= threshold:
                dups.append(e)
                seen.add(id(e))
    return dups


# ---------------------------------------------------------------------------
# SKILL.md frontmatter / 文件夹完整性 / 条目推断
# ---------------------------------------------------------------------------

def parse_frontmatter(text: str, limit: int = 20) -> dict:
    """解析 SKILL.md 顶部 frontmatter（--- 包围的单行 key: value），仅扫前 limit 行。"""
    lines = text.splitlines()[:limit]
    if not lines or lines[0].strip() != "---":
        return {}
    fm: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        m = re.match(r"^([A-Za-z_][\w-]*)\s*:\s*(.*)$", line)
        if m:
            fm[m.group(1).strip()] = m.group(2).strip()
    return fm


def read_frontmatter(skill_md_path: str, limit: int = 20) -> dict:
    if not os.path.isfile(skill_md_path):
        return {}
    with open(skill_md_path, encoding="utf-8") as f:
        return parse_frontmatter(f.read(), limit)


def check_skill_folder(skills_root: str, name: str) -> list[str]:
    """F5.1 校验 skill 文件夹完整性：SKILL.md 存在 + evals/cases/ 存在（至少 1 个 case 目录）。

    入口先过名字白名单（防路径穿越），非法名直接返回错误。
    """
    name_errors = validate_skill_name(name)
    if name_errors:
        return name_errors
    skill_dir = os.path.join(skills_root, name)
    if not os.path.isdir(skill_dir):
        return [f"skill 文件夹不存在: {skill_dir}"]
    errors: list[str] = []
    md = os.path.join(skill_dir, "SKILL.md")
    if not os.path.isfile(md):
        errors.append(f"缺少 SKILL.md: {md}")
    cases_dir = os.path.join(skill_dir, "evals", "cases")
    if not os.path.isdir(cases_dir):
        errors.append(f"缺少 evals/cases/ 目录: {cases_dir}")
    else:
        cases = [d for d in os.listdir(cases_dir) if os.path.isdir(os.path.join(cases_dir, d))]
        if not cases:
            errors.append(f"evals/cases/ 下至少需要 1 个 case 目录: {cases_dir}")
    return errors


def infer_entry(name: str, skills_root: str, status: str) -> dict:
    """从文件夹推断新条目：name/version/path + SKILL.md 证据引用中的 source_runs（WIKI-key）。"""
    md_path = os.path.join(skills_root, name, "SKILL.md")
    fm = read_frontmatter(md_path)
    source_runs: list[str] = []
    if os.path.isfile(md_path):
        with open(md_path, encoding="utf-8") as f:
            source_runs = sorted(set(_AGEN_RE.findall(f.read())))
    return {
        "name": name,
        "version": fm.get("version") or "1.0.0",
        "owner": "team",
        "layer": "shared",
        "action_level": "advice",
        "author": "SkillTrove",
        "source_runs": source_runs,
        "contributors": {"distinct_agents": 0, "agents": []},
        "review_status": status,
        "usage": {"applied_count": 0, "last_applied_at": None, "distinct_appliers": 0},
        "path": f"skills/{name}",
    }


def extract_evidence_lines(skill_md_path: str, cap: int = 10) -> list[str]:
    """从 SKILL.md 提取「证据引用」行（含 证据/引用/trace/WIKI-key 的行），无则返回空列表。"""
    if not os.path.isfile(skill_md_path):
        return []
    out: list[str] = []
    with open(skill_md_path, encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if s and _EVIDENCE_RE.search(s) and s not in out:
                out.append(s)
                if len(out) >= cap:
                    break
    return out


# ---------------------------------------------------------------------------
# 审核单生成（F4.2，五要素 + 证据态 + 贡献者）
# ---------------------------------------------------------------------------

def build_review_notes(name: str, entry: dict | None, fm: dict, evidence: list[str], skills_root: str) -> str:
    """拼装审核单 markdown：五要素（重要性/证据/期望产出/范围/验收）+ 证据态 + 贡献者信息。"""
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    status = (entry or {}).get("review_status") or "未入库"
    version = (entry or {}).get("version") or fm.get("version") or "待定"
    owner = (entry or {}).get("owner") or "team"
    layer = (entry or {}).get("layer") or "shared"
    action_level = (entry or {}).get("action_level") or "advice"
    author = (entry or {}).get("author") or "待补充"
    source_runs = (entry or {}).get("source_runs") or []
    contrib = (entry or {}).get("contributors") or {}
    distinct_agents = contrib.get("distinct_agents")
    agents = contrib.get("agents") or []
    usage = (entry or {}).get("usage") or {}
    desc = fm.get("description") or "【待补充】"
    when_to_use = fm.get("when_to_use") or "【待补充】"

    lines_out: list[str] = []
    ap = lines_out.append
    ap(f"# 审核单：{name}")
    ap("")
    ap(f"- 生成时间：{now}（registry.py review-helper，F4.2）")
    ap(f"- skill 位置：`{skills_root}/{name}/`")
    ap(f"- 条目：review_status={status} / version={version} / owner={owner} / layer={layer}")
    ap(f"- 条目：action_level={action_level} / author={author}")
    ap("")

    ap("## 1. 重要性")
    ap("【人工填写】该经验对团队的复用价值与影响面。")
    ap(f"- 提示（when_to_use）：{when_to_use}")
    ap("")

    ap("## 2. 证据")
    ap(f"- source_runs（来源 issue）：{', '.join(source_runs) if source_runs else '【待补充】'}")
    if evidence:
        ap(f"- 证据态置信：已引用（trace 位置可查证，{len(evidence)} 行）")
        ap("- SKILL.md 证据引用行：")
        for ln in evidence:
            ap(f"  - `{ln}`")
    else:
        ap("- 证据态置信：待人工补充（SKILL.md 无证据引用行，须对照 trace 补齐，Spec §4.4 硬约束）")
    ap("- 审核要点：逐条核对证据引用可回溯到 issue key + 评论时间戳 + 段落。")
    ap("")

    ap("## 3. 期望产出")
    ap("【人工填写】该 skill 被调用后应产出的交付物与验收形态。")
    ap(f"- 提示（description）：{desc}")
    ap("")

    ap("## 4. 范围")
    ap("【人工填写】适用 / 不适用边界（对照 when_to_use 与示例；防火墙：目标 / 产物 / 输入形态 / 验收边界不同不算同类）。")
    ap(f"- 边界提示（when_to_use）：{when_to_use}")
    ap("")

    ap("## 5. 验收（人工勾选）")
    ap("- [ ] SKILL.md 结构完整：What it does / When to use / Steps / Examples / Going deeper")
    ap("- [ ] 每条 Step 均有可回溯的证据引用")
    ap("- [ ] `evals/cases/` 下至少 1 个 case（config.yaml + judge.py + fixture）")
    ap("- [ ] 入库前查重通过（publish 自动校验，冲突留人审）")
    ap(f"- [ ] 审核通过后发布：`python cli/registry.py publish --skill {name} --status published`")
    ap("")

    ap("## 6. 贡献者")
    ap(f"- distinct agents：{distinct_agents if distinct_agents is not None else '待补充'}（未入库时由 registry 条目回填）")
    ap(f"- agents：{', '.join(agents) if agents else '待补充'}")
    ap(f"- usage：applied_count={usage.get('applied_count', '-')} / distinct_appliers={usage.get('distinct_appliers', '-')}")
    ap("")

    ap("## 7. 审核结论")
    ap("- [ ] 通过（发布入库）    - [ ] 打回（补充证据 / 修改后复审）")
    return "\n".join(lines_out) + "\n"


# ---------------------------------------------------------------------------
# 命令
# ---------------------------------------------------------------------------

def publish(args) -> int:
    """F5 发布：校验文件夹 -> 查重 -> 更新/新建条目 -> 写回 registry.json。

    生命周期：新条目直接以 --status 入库；已有条目的状态变更走 set_status 校验。
    """
    name = getattr(args, "skill", "").strip()
    status = (getattr(args, "status", None) or "published").strip()
    registry_path = getattr(args, "registry", None) or default_registry_path()
    skills_root = skills_root_for(registry_path)

    if not name:
        print("错误: --skill 不能为空")
        return 1
    name_errors = validate_skill_name(name)
    if name_errors:
        for e in name_errors:
            print(f"错误: {e}")
        return 1
    if status not in STATUS_FLOW:
        print(f"错误: --status 非法: {status!r}（合法: {'/'.join(STATUS_FLOW)}）")
        return 1

    # 1) F5.1 校验 skill 文件夹完整性
    errors = check_skill_folder(skills_root, name)
    if errors:
        for e in errors:
            print(f"错误: {e}")
        return 1

    registry = load_registry(registry_path)
    existing = find_entry(registry, name)
    fm = read_frontmatter(os.path.join(skills_root, name, "SKILL.md"))
    desc = fm.get("description", "")

    # 2) F4.1 入库前查重（冲突留人审）
    dups = find_duplicate(registry, name, desc, skills_root)
    conflicts = [d for d in dups if d is not existing]
    if conflicts:
        print(f"查重拒绝发布: {name} 疑似与以下已存在条目重复，请人工审核后再入（F4）:")
        for d in conflicts:
            print(f"  - {d.get('name')}（review_status={d.get('review_status')}，path={d.get('path')}）")
        return 1

    # 3) 状态生命周期 + 更新/新建
    if existing is not None:
        try:
            set_status(registry, name, status)
        except ValueError as e:
            print(f"错误: {e}")
            return 1
        existing["version"] = fm.get("version") or existing.get("version", "1.0.0")
        existing["path"] = f"skills/{name}"
        entry = existing
        action = "更新"
    else:
        entry = infer_entry(name, skills_root, status)
        errs = validate_entry(entry)
        if errs:
            for e in errs:
                print(f"错误: {e}")
            return 1
        registry["skills"].append(entry)
        action = "新建"

    # 从 references/evidence-index.json 补充运营字段（贡献者 + 来源 run，draft 已物化）
    ref_path = os.path.join(skills_root, name, "references", "evidence-index.json")
    if os.path.isfile(ref_path):
        try:
            with open(ref_path, encoding="utf-8") as f:
                ref = json.load(f)
            if ref.get("contributors"):
                entry["contributors"] = ref["contributors"]
            if ref.get("episodes"):
                entry["source_runs"] = sorted(
                    {s.replace("ep-", "") for s in ref["episodes"] if s.startswith("ep-")}
                )
        except Exception as e:
            print(f"警告: evidence-index 读取失败: {e}")

    save_registry(registry, registry_path)
    print(f"[publish] {action}条目: {name} v{entry['version']} review_status={entry['review_status']} path={entry['path']}")
    print(f"[publish] registry -> {registry_path}")
    return 0


def review_helper(args) -> int:
    """F4.2 生成审核单：读 SKILL.md 前 20 行（frontmatter）+ registry 条目，写 references/review-notes.md。"""
    name = getattr(args, "skill", "").strip()
    registry_path = getattr(args, "registry", None) or default_registry_path()
    skills_root = skills_root_for(registry_path)

    if not name:
        print("错误: --skill 不能为空")
        return 1
    name_errors = validate_skill_name(name)
    if name_errors:
        for e in name_errors:
            print(f"错误: {e}")
        return 1
    skill_dir = os.path.join(skills_root, name)
    md_path = os.path.join(skill_dir, "SKILL.md")
    if not os.path.isdir(skill_dir) or not os.path.isfile(md_path):
        print(f"错误: skill 文件夹不完整（缺 SKILL.md）: {skill_dir}")
        return 1

    entry = find_entry(load_registry(registry_path), name)
    fm = read_frontmatter(md_path)
    evidence = extract_evidence_lines(md_path)
    notes = build_review_notes(name, entry, fm, evidence, skills_root)

    ref_dir = os.path.join(skill_dir, "references")
    os.makedirs(ref_dir, exist_ok=True)
    out = os.path.join(ref_dir, "review-notes.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write(notes)

    print(f"[review-helper] 审核单 -> {out}")
    print(f"[review-helper] 条目: {name}（registry={'有' if entry else '无，未入库'}）"
          f"，证据态: {'已引用 ' + str(len(evidence)) + ' 行' if evidence else '待人工补充'}")
    return 0


def run(args) -> int:
    """子命令分发（main.py 同款入口约定）。"""
    step = getattr(args, "step", None)
    if step == "publish":
        return publish(args)
    if step == "review-helper":
        return review_helper(args)
    print(f"未知步骤: {step}")
    return 1


# ---------------------------------------------------------------------------
# 自测（python cli/registry.py 无参数执行）
# ---------------------------------------------------------------------------

_SKILL_MD_TEST = """---
name: test-skill
description: 编写 S2 阶段产品需求文档 PRD，定义 MVP 范围与验收标准并收敛开放问题
when_to_use: 需要产出可评审的产品需求文档时
version: 1.1.0
---

# test-skill

## What it does
基于 issue 上下文编写并收敛 PRD 草稿。

## When to use
需要产出可评审 PRD 且证据充分时。

## Steps
1. 梳理目标与验收。证据引用: WIKI-4 @2026-08-28T09:17:56Z 段落2
2. 撰写草稿并收敛开放问题。证据引用: WIKI-4 @2026-08-28T09:41:16Z 段落1

## Examples
见 evals/cases/test-skill-001/。
"""

_SKILL_MD_B = """---
name: test-skill-b
description: 编写 S3 阶段产品需求文档 PRD，定义 MVP 范围与验收标准并收敛开放问题
when_to_use: 需要产出可评审的产品需求文档时
---

# test-skill-b

## What it does
基于 issue 上下文编写并收敛 PRD 草稿。
"""

_SKILL_MD_NC = """---
name: test-skill-nc
description: 咖啡烘焙曲线调试与温控参数整定
when_to_use: 烘焙曲线异常时
---

# test-skill-nc

## What it does
根据杯测结果调整烘焙曲线。
"""


def selftest() -> int:
    """自测：临时目录（仓库根内）建假 skill，跑 publish / review-helper / 生命周期 / 查重 / 校验。"""
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    tmp = tempfile.mkdtemp(prefix="registry-selftest-", dir=repo_root)
    print(f"自测临时目录: {tmp}（结束后清理）")
    try:
        registry_path = os.path.join(tmp, "registry", "registry.json")
        skills_root = os.path.join(tmp, "skills")
        os.makedirs(os.path.dirname(registry_path), exist_ok=True)

        # 假 skill：test-skill（含证据引用 + 版本 1.1.0）
        skill_dir = os.path.join(skills_root, "test-skill")
        cases_dir = os.path.join(skill_dir, "evals", "cases", "test-skill-001")
        os.makedirs(cases_dir)
        with open(os.path.join(skill_dir, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write(_SKILL_MD_TEST)

        # ---- 1) publish：新建条目（F5）----
        print("\n== 1) publish（新建，默认 published）==")
        rc = publish(argparse.Namespace(skill="test-skill", status="published", registry=registry_path))
        assert rc == 0, "publish 新建应成功"
        reg = load_registry(registry_path)
        entry = find_entry(reg, "test-skill")
        assert entry and entry["review_status"] == "published", "条目应已发布"
        assert entry["version"] == "1.1.0", "version 应从 frontmatter 推断"
        assert entry["source_runs"] == ["WIKI-4"], "source_runs 应从 SKILL.md 证据引用推断"
        assert validate_entry(entry) == [], f"条目应通过校验: {validate_entry(entry)}"
        # 模拟 F2/F3 回填贡献者后写回（review-helper 读盘）
        entry["contributors"] = {"distinct_agents": 2, "agents": ["dave", "carol"]}
        save_registry(reg, registry_path)
        print(f"  条目: {entry['name']} v{entry['version']} status={entry['review_status']} "
              f"source_runs={entry['source_runs']} contributors={entry['contributors']}")

        # ---- 2) review-helper：生成审核单（F4.2）----
        print("\n== 2) review-helper（生成审核单）==")
        rc = review_helper(argparse.Namespace(skill="test-skill", registry=registry_path))
        assert rc == 0, "review-helper 应成功"
        notes_path = os.path.join(skill_dir, "references", "review-notes.md")
        assert os.path.isfile(notes_path), "应生成 review-notes.md"
        with open(notes_path, encoding="utf-8") as f:
            notes = f.read()
        for k in ["## 1. 重要性", "## 2. 证据", "## 3. 期望产出", "## 4. 范围", "## 5. 验收",
                  "已引用（trace 位置可查证", "distinct agents：2", "dave"]:
            assert k in notes, f"审核单应包含: {k}"
        print("  审核单内容预览（前 14 行）:")
        for line in notes.splitlines()[:14]:
            print(f"    {line}")

        # review-helper 无证据分支（未入库 + 无证据引用 -> 待人工补充）
        nc_dir = os.path.join(skills_root, "test-skill-nc")
        os.makedirs(nc_dir)
        with open(os.path.join(nc_dir, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write(_SKILL_MD_NC)
        rc = review_helper(argparse.Namespace(skill="test-skill-nc", registry=registry_path))
        assert rc == 0, "review-helper（无证据）应成功"
        with open(os.path.join(nc_dir, "references", "review-notes.md"), encoding="utf-8") as f:
            nc_notes = f.read()
        assert "待人工补充" in nc_notes and "未入库" in nc_notes, "无证据/未入库应标注"
        print("  无证据分支: 证据态=待人工补充, 条目=未入库（OK）")

        # ---- 3) 入库前查重 find_duplicate（F4.1）----
        print("\n== 3) 入库前查重 find_duplicate ==")
        dups_name = find_duplicate(reg, "test-skill", "完全无关的描述", skills_root)
        assert len(dups_name) == 1 and dups_name[0]["name"] == "test-skill", "name 精确匹配应命中"
        print(f"  name 精确匹配: {[d['name'] for d in dups_name]}")
        dups_sem = find_duplicate(reg, "other-skill", "编写 S2 阶段产品需求文档 PRD，定义 MVP 范围与验收标准并收敛开放问题", skills_root)
        assert any(d["name"] == "test-skill" for d in dups_sem), "相似描述应语义命中已发布条目"
        print(f"  语义相似（>=0.5，仅已发布）: {[d['name'] for d in dups_sem]}")
        dups_none = find_duplicate(reg, "other-skill", "咖啡烘焙曲线调试与温控参数整定", skills_root)
        assert dups_none == [], "不相关描述不应命中"
        print(f"  不相关描述: {len(dups_none)} 条命中")
        same = bigram_similarity("编写 S2 阶段产品需求文档 PRD，定义 MVP 范围与验收标准并收敛开放问题",
                                 "编写 S2 阶段产品需求文档 PRD，定义 MVP 范围与验收标准并收敛开放问题")
        assert same >= 0.5, f"相同文本相似度应 >=0.5，got {same}"
        print(f"  相同文本 bigram 相似度: {same:.3f}")

        # ---- 4) publish 查重拒绝（冲突留人审）----
        print("\n== 4) publish 查重拒绝（相似描述）==")
        b_dir = os.path.join(skills_root, "test-skill-b")
        os.makedirs(os.path.join(b_dir, "evals", "cases", "test-skill-b-001"))
        with open(os.path.join(b_dir, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write(_SKILL_MD_B)
        rc = publish(argparse.Namespace(skill="test-skill-b", status="published", registry=registry_path))
        assert rc == 1, "相似描述应拒绝发布"
        assert find_entry(load_registry(registry_path), "test-skill-b") is None, "拒绝后不应写入条目"
        print("  已拒绝且未写 registry（OK）")

        # ---- 5) 审核状态生命周期 set_status ----
        print("\n== 5) 审核状态生命周期 set_status ==")
        mem = {"schema": SCHEMA, "skills": [{
            "name": "lifecycle-skill", "version": "1.0.0", "owner": "team", "layer": "shared",
            "action_level": "advice", "author": "SkillTrove", "source_runs": [],
            "contributors": {"distinct_agents": 0, "agents": []}, "review_status": "draft",
            "usage": {"applied_count": 0, "last_applied_at": None, "distinct_appliers": 0},
            "path": "skills/lifecycle-skill",
        }]}
        for s in ["in_review", "published", "deprecated"]:
            set_status(mem, "lifecycle-skill", s)
            print(f"  draft -> ... -> {s}（OK）")
        try:
            set_status(mem, "lifecycle-skill", "draft")
            raise AssertionError("deprecated -> draft 应非法")
        except ValueError:
            print("  deprecated -> draft 非法（OK）")
        mem["skills"][0]["review_status"] = "draft"
        try:
            set_status(mem, "lifecycle-skill", "published")
            raise AssertionError("draft -> published 应非法")
        except ValueError:
            print("  draft -> published 非法（OK）")
        set_status(mem, "lifecycle-skill", "in_review")
        set_status(mem, "lifecycle-skill", "published")
        set_status(mem, "lifecycle-skill", "published")  # 同状态幂等 no-op
        print("  同状态幂等 no-op（OK）")
        try:
            set_status(mem, "lifecycle-skill", "unknown")
            raise AssertionError("未知状态应非法")
        except ValueError:
            print("  未知状态非法（OK）")

        # ---- 6) publish 更新已有条目走生命周期 ----
        print("\n== 6) publish 更新走生命周期 ==")
        rc = publish(argparse.Namespace(skill="test-skill", status="deprecated", registry=registry_path))
        assert rc == 0, "published -> deprecated 应成功"
        assert find_entry(load_registry(registry_path), "test-skill")["review_status"] == "deprecated"
        print("  published -> deprecated（OK）")
        rc = publish(argparse.Namespace(skill="test-skill", status="draft", registry=registry_path))
        assert rc == 1, "deprecated -> draft 应被生命周期拒绝"
        print("  deprecated -> draft 拒绝（OK）")

        # ---- 7) validate_entry ----
        print("\n== 7) validate_entry ==")
        ok = {"name": "s", "version": "1.0.0", "owner": "team", "layer": "shared", "action_level": "advice",
              "author": "SkillTrove", "source_runs": ["WIKI-4"],
              "contributors": {"distinct_agents": 2, "agents": ["dave", "carol"]},
              "review_status": "draft", "usage": {"applied_count": 0, "last_applied_at": None, "distinct_appliers": 0},
              "path": "skills/s"}
        assert validate_entry(ok) == [], "合法条目应零错误"
        bad = dict(ok)
        del bad["name"]
        bad["usage"] = {"applied_count": "x"}
        bad["review_status"] = "foo"
        errs = validate_entry(bad)
        assert len(errs) >= 3, f"非法条目应报错: {errs}"
        print(f"  合法条目: 0 错误；非法条目报 {len(errs)} 条: {errs[:3]}")

        print("\n=== registry selftest OK（publish / review-helper / 生命周期 / 查重 / 校验 全部通过）===")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        print(f"已清理临时目录: {tmp}")


if __name__ == "__main__":
    p = argparse.ArgumentParser(
        prog="registry.py", description="SkillTrove F4 审核辅助 + F5 发布入库（Spec §4.5）")
    sub = p.add_subparsers(dest="step", title="子命令")
    pp = sub.add_parser("publish", help="F5 发布：校验 skill 文件夹 + 查重 + 写 registry.json")
    pp.add_argument("--skill", required=True, help="skill 名（= skills/<name>/ 目录名）")
    pp.add_argument("--status", default="published", help="入库状态（默认 published，须符合生命周期）")
    pp.add_argument("--registry", default=None, help="registry.json 路径（默认 <仓库>/registry/registry.json）")
    pr = sub.add_parser("review-helper", help="F4.2 生成审核单（skills/<name>/references/review-notes.md）")
    pr.add_argument("--skill", required=True, help="skill 名")
    pr.add_argument("--registry", default=None, help="registry.json 路径")
    args = p.parse_args()
    if args.step is None:
        # 无子命令 -> 模块自测
        raise SystemExit(selftest())
    raise SystemExit(run(args))
