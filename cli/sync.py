"""F6 调用回采：另一 agent（claude/codex）无头 run 调用共享库 skill（Spec §4.6，A7/A8）。

流程：
  1. 读 skills/<name>/SKILL.md（frontmatter + Steps 摘要）
  2. 以另一 agent 身份构造调用任务（读 SKILL.md → 按 Steps 执行最小任务 → 产出）
  3. claude -p 无头执行（cwd = skilltrove/，可访问共享库）
  4. applied 判定（skill 名/步骤被引述）→ 留痕四元组 result.json
  5. 执行 evals/cases/<name>-001/judge.py → 结果落 evals/results/
  6. 回填 registry usage（applied_count / distinct_appliers / last_applied_at）
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cli import registry  # noqa: E402

def default_task(fm: dict) -> str:
    """按技能自身说明派生一个与之匹配的调用任务。

    旧的默认值硬编码了「某开源工作流框架的对象定位判断 / 换框校准步骤」，
    那是为另一类技能写的。用在调研选型类技能上时 agent 无从遵循其 Steps，
    applied 判定必然为假，却看不出是任务错配还是技能没被遵循。
    """
    hint = (fm.get("when_to_use") or fm.get("description") or "").strip()
    if hint:
        return (f"请严格按该 skill 的 Steps，完成一次符合其适用场景的小任务：{hint}。"
                f"把完整产物以 markdown 直接输出。")
    return ("请严格按该 skill 的 Steps 完成一个小任务（主题自定，能体现各步骤即可），"
            "把完整产物以 markdown 直接输出。")


def _read_frontmatter(md_text: str) -> dict:
    fm: dict = {}
    m = re.match(r"^---\s*\n(.*?)\n---", md_text, re.S)
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                fm[k.strip()] = v.strip()
    return fm


def _extract_steps(md_text: str) -> list[str]:
    """提取 ## Steps 段的编号步骤（1. / 2. ...）。"""
    m = re.search(r"## Steps\s*\n(.*?)(?=\n## |\Z)", md_text, re.S)
    if not m:
        return []
    steps = []
    for line in m.group(1).splitlines():
        s = line.strip()
        if re.match(r"^\d+\.", s):
            steps.append(s)
    return steps


def _core_phrase(step: str) -> str:
    """提取步骤核心短语：去编号/加粗，取冒号前标题（如『确认输入与验收口径』）。"""
    s = re.sub(r"^\d+[\.、]\s*", "", step)
    s = re.sub(r"[*#`]", "", s)
    s = re.split(r"[：:]", s)[0]
    return s.strip()


def run(args) -> int:
    name = args.skill
    # 路径白名单：skill 名会被拼进 skills/<name>/ 路径，非法名（含路径穿越）拒绝执行
    name_errors = registry.validate_skill_name(name)
    if name_errors:
        for e in name_errors:
            print(f"错误: {e}")
        return 1
    skill_dir = os.path.join("skills", name)
    md_path = os.path.join(skill_dir, "SKILL.md")
    if not os.path.isfile(md_path):
        print(f"错误: 共享库无 {md_path}")
        return 1

    with open(md_path, encoding="utf-8") as f:
        skill_md = f.read()
    fm = _read_frontmatter(skill_md)
    version = fm.get("version") or "1.0.0"
    steps = _extract_steps(skill_md)
    print(f"skill: {name} v{version}（Steps {len(steps)} 条）")

    run_id = f"recall-run-{time.strftime('%Y%m%d-%H%M%S')}"
    run_dir = os.path.join("data", "recall-runs", run_id)
    prod_dir = os.path.join(run_dir, "products")
    os.makedirs(prod_dir, exist_ok=True)

    # 1. 构造调用任务（agent = 另一 agent，非沉淀者）
    task = args.prompt or default_task(fm)
    agent_prompt = (
        f"你在当前工作目录（本项目仓库根）下工作。\n"
        f"任务要求：\n"
        f"1. 先读取团队共享 skill 库中的 skills/{name}/SKILL.md（这是已验证经验的沉淀，优先按其 Steps 执行）；\n"
        f"2. 按该 skill 的 Steps 完成这个小任务：{task}\n"
        f"3. 把你的完整产物以 markdown 直接输出在回复中（不要写文件，不要用任何工具）；\n"
        f"4. 回复开头列出你遵循了该 skill 的哪些 Steps（逐条引用）。"
    )
    agent_id = args.agent or "kimi"

    print(f"recall run: {run_id} agent={agent_id}")
    cli = shutil.which(agent_id)
    if not cli:
        print(f"错误: agent CLI 不存在: {agent_id}")
        return 1

    # 各 agent CLI 的无头调用形态不同：claude/codex 走 stdin，kimi -p 走 argv
    run_input = agent_prompt
    if agent_id == "claude":
        cmd = [cli, "-p", "--output-format", "text"]
        if args.permission_mode:
            cmd += ["--permission-mode", args.permission_mode]
    elif agent_id == "codex":
        cmd = [cli, "exec", "-", "--json"]
    elif agent_id == "kimi":
        # kimi -p <prompt> 以参数传 prompt（非 stdin，实测 'argument missing'）
        cmd = [cli, "-p", agent_prompt, "--output-format", "text"]
        run_input = None
    else:
        cmd = [cli, "-p", "--output-format", "text"]
    r = subprocess.run(cmd, input=run_input, capture_output=True, text=True,
                       timeout=args.timeout, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print(f"警告: {agent_id} 退出码 {r.returncode}: {r.stderr[:200]}")

    output = r.stdout.strip()
    with open(os.path.join(run_dir, "agent-output.md"), "w", encoding="utf-8") as f:
        f.write(agent_prompt + "\n\n--- AGENT OUTPUT ---\n\n" + output)
    if output:
        with open(os.path.join(prod_dir, "result.md"), "w", encoding="utf-8") as f:
            f.write(output)

    # 2. applied 判定（核心短语匹配 + 结构信号；MVP 期人工核对兜底，输出全量留痕）
    steps_followed = []
    for st in steps:
        phrase = _core_phrase(st)
        if len(phrase) >= 4 and phrase in output:
            steps_followed.append(st)
    structure_signal = ("Steps" in output) or ("遵循" in output and "步骤" in output)
    step_mentioned = len(steps_followed) >= 1 or structure_signal
    skill_named = name in output or fm.get("name", "") in output
    applied = bool(step_mentioned and skill_named and output)

    products = []
    if os.path.isdir(prod_dir):
        products = sorted(os.listdir(prod_dir))

    # 3. 留痕四元组
    result = {
        "run_id": run_id,
        "skill_id": name,
        "version": version,
        "agent_id": agent_id,
        "applied": applied,
        "steps_followed": steps_followed,
        # 统一正斜杠：os.path.join 在 Windows 上会写进反斜杠，留档跨平台不可读
        "products": ["products/" + p for p in products],
        "output_path": "agent-output.md",
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    result_path = os.path.join(run_dir, "result.json")
    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"留痕四元组 -> {result_path}")
    print(f"  applied={applied} | steps_followed={len(steps_followed)} | products={len(products)}")

    # 4. judge.py
    case_dir = os.path.join(skill_dir, "evals", "cases", f"{name}-001")
    judge_path = os.path.join(case_dir, "judge.py")
    if os.path.isfile(judge_path):
        res_dir = os.path.join("evals", "results")
        os.makedirs(res_dir, exist_ok=True)
        res_out = os.path.join(res_dir, f"{run_id}.json")
        jr = subprocess.run([sys.executable, judge_path, result_path], capture_output=True,
                            text=True, encoding="utf-8", errors="replace", timeout=60)
        with open(res_out, "w", encoding="utf-8") as f:
            f.write(f"# eval result for {run_id}\n# judge exit: {jr.returncode}\n\n")
            f.write(jr.stdout or "")
            if jr.stderr:
                f.write("\n# stderr\n" + jr.stderr)
        print(f"eval 结果 -> {res_out}（judge 退出码 {jr.returncode}）")
    else:
        print(f"警告: 无 judge.py（{judge_path}），跳过 eval")

    # 5. registry usage 回填（仅 applied 计一次）
    if applied:
        try:
            reg_path = args.registry
            reg = registry.load_registry(reg_path)
            entry = registry.find_entry(reg, name)
            if entry:
                usage = entry.setdefault("usage", {})
                usage["applied_count"] = (usage.get("applied_count") or 0) + 1
                usage["last_applied_at"] = result["ts"]
                appliers = set((usage.get("applier_ids") or []))
                appliers.add(agent_id)
                usage["applier_ids"] = sorted(appliers)
                usage["distinct_appliers"] = len(appliers)
                registry.save_registry(reg, reg_path)
                print(f"registry usage 更新: applied_count={usage['applied_count']} distinct_appliers={usage['distinct_appliers']}")
        except Exception as e:
            print(f"警告: registry usage 回填失败: {e}")
    else:
        print("applied=False，不计入 usage")

    return 0 if applied else 1


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="F6 调用回采")
    p.add_argument("--skill", required=True)
    p.add_argument("--prompt", default=None, help="调用任务（默认最小定位任务）")
    p.add_argument("--agent", default="kimi", help="调用 agent（默认 kimi，与沉淀者不同）")
    p.add_argument("--registry", default="registry/registry.json")
    p.add_argument("--timeout", type=int, default=600)
    p.add_argument("--permission-mode", default=None, help="传给 claude 的 --permission-mode（如 acceptEdits）")
    raise SystemExit(run(p.parse_args()))
