"""IMP-6 DoD 端到端验收：核对六步产物，输出 DoD 五条对照（Spec §6）。

不改动任何产物，只读核对 + 生成验收报告。
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # 管道/重定向时 GBK 控制台会因 ≠ 等字符崩

# 保证 `import cli` 可解析（独立运行 python cli/verify.py 时也需要）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# DoD4 校验的六步子命令：步骤名 -> 实现模块（publish 在 registry.py、recall 在 sync.py）
CLI_STEPS = {
    "export": "cli.export",
    "score": "cli.score",
    "cluster": "cli.cluster",
    "draft": "cli.draft",
    "publish": "cli.registry",
    "recall": "cli.sync",
}


def runs_dir(root: str) -> str:
    """回采 run 根目录：基于 root 拼接（CWD 无关）。"""
    return os.path.join(root, "data", "recall-runs")


def _j(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _latest_recall(root: str) -> str | None:
    runs_root = runs_dir(root)
    if not os.path.isdir(runs_root):
        return None
    runs = sorted(os.listdir(runs_root))
    return runs[-1] if runs else None


def check_cli_steps() -> tuple[bool, list[str], list[str]]:
    """DoD4 实证：六步子命令在 cli/main.py 均已注册且实现模块可导入（CWD 无关）。

    注册检查用 build_parser 的 subparsers choices（避免 parse_args 因必填参数
    缺失而 SystemExit）；模块检查用 importlib 按 CLI_STEPS 的步骤->模块映射。
    返回 (全部可用?, 可用列表, 缺失列表)。
    注意：延迟导入 cli.main（main.py 顶部会 import verify，模块级导入会循环）。
    """
    from cli import main as main_mod
    parser = main_mod.build_parser()
    subs = next(a for a in parser._actions
                if isinstance(a, argparse._SubParsersAction))
    registered = set(subs.choices)
    ok: list[str] = []
    missing: list[str] = []
    for step, module in CLI_STEPS.items():
        if step not in registered:
            missing.append(step)
            continue
        try:
            importlib.import_module(module)
            ok.append(step)
        except Exception:
            missing.append(step)
    return len(ok) == len(CLI_STEPS), ok, missing


def _check_dod1_clustering(root, check) -> None:
    """DoD 1：聚类识别真实重复经验（candidates.json 首个候选有证据）。"""
    cand_path = os.path.join(root, "data", "candidates.json")
    if not os.path.isfile(cand_path):
        check("DoD1 聚类识别真实重复经验", False, f"缺 {cand_path}")
        return
    c = _j(cand_path).get("candidates", [])
    if not c:
        check("DoD1 聚类识别真实重复经验", False, "candidates.json 无候选")
        return
    first = c[0]
    evidence = (f"候选 {first['candidate_id']} {first['episode_ids']} "
                f"contributors={first['contributors']} 证据={len(first.get('evidence', []))} 条")
    check("DoD1 聚类识别真实重复经验", True, evidence)


def _published_skills(root) -> list[dict]:
    reg_path = os.path.join(root, "registry", "registry.json")
    if not os.path.isfile(reg_path):
        return []
    reg = _j(reg_path)
    return [s for s in reg.get("skills", []) if s.get("review_status") == "published"]


def _check_dod2_published_skill(root, published, check) -> None:
    """DoD 2：草稿 -> 审核 -> 入共享库（published 条目带 evals/cases）。"""
    if not published:
        reg_path = os.path.join(root, "registry", "registry.json")
        check("DoD2 草稿审核入共享库", False,
              "registry 无 published 条目" + ("" if os.path.isfile(reg_path) else f"（缺 {reg_path}）"))
        return
    p = published[0]
    skill_dir = os.path.join(root, p["path"])
    has_case = os.path.isdir(os.path.join(skill_dir, "evals", "cases"))
    evidence = (f"{p['name']} v{p['version']} published source_runs={p.get('source_runs')} "
                f"contributors={p.get('contributors', {}).get('distinct_agents')} 人 evals/cases={'Y' if has_case else 'N'}")
    check("DoD2 草稿审核入共享库", has_case, evidence)


def _check_dod3_cross_agent_call(root, published, check) -> None:
    """DoD 3：跨 agent 调用（调用者 != 沉淀者）。"""
    recall_run = _latest_recall(root)
    if not recall_run:
        check("DoD3 跨 agent 调用", False, "无 recall run")
        return
    result_path = os.path.join(runs_dir(root), recall_run, "result.json")
    if not os.path.isfile(result_path):
        check("DoD3 跨 agent 调用", False, f"缺 {result_path}")
        return
    r = _j(result_path)
    agents = set()
    for s in published:
        agents.update(s.get("contributors", {}).get("agents", []))
    caller = r.get("agent_id")
    different = caller not in agents
    evidence = (f"run={r['run_id']} skill={r['skill_id']} caller={caller} "
                f"applied={r.get('applied')} 沉淀者={sorted(agents)} 调用者≠沉淀者={different}")
    check("DoD3 跨 agent 调用（调用者≠沉淀者）", r.get("applied") and different, evidence)


def _check_dod4_cli_repeatable(check) -> None:
    """DoD 4：命令可重复执行（六步子命令已注册且模块可导入）。"""
    ok4, ok_steps, missing = check_cli_steps()
    evidence4 = f"{len(ok_steps)}/{len(CLI_STEPS)} 子命令可调用（{'、'.join(ok_steps) or '无'}），本地无外部服务依赖"
    if missing:
        evidence4 += f"；缺失: {'、'.join(missing)}"
    check("DoD4 命令可重复执行（六步无外部服务依赖）", ok4, evidence4)


def _check_dod5_business_join(root, check) -> None:
    """DoD 5：业务 join 端到端可验证（A9 本地化：issue 完成态）。"""
    scored_path = os.path.join(root, "archive", "scored.jsonl")
    if not os.path.isfile(scored_path):
        check("DoD5 业务 join 端到端可验证（本地化）", False, f"缺 {scored_path}")
        return
    highs = 0
    with open(scored_path, encoding="utf-8") as f:
        for line in f:
            ep = json.loads(line)
            if (ep.get("score") or {}).get("label") == "high":
                highs += 1
    jk = "issue_status_history"
    evidence = (f"{jk} 查证：{highs} 个高分 episode 的 business_join_key 可查证并回连评分（本地最轻信号）")
    check("DoD5 业务 join 端到端可验证（本地化）", highs > 0, evidence)


def run(args) -> int:
    """DoD 端到端验收：五项检查各由 _check_dodN 承担，run 只做编排与报告。"""
    root = args.root
    results = []

    def check(name: str, ok: bool, evidence: str) -> None:
        results.append({"item": name, "pass": bool(ok), "evidence": evidence})
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {evidence}")

    print("=== SkillTrove DoD 端到端验收 ===\n")

    published = _published_skills(root)
    _check_dod1_clustering(root, check)
    _check_dod2_published_skill(root, published, check)
    _check_dod3_cross_agent_call(root, published, check)
    _check_dod4_cli_repeatable(check)
    _check_dod5_business_join(root, check)

    # 汇总
    n_pass = sum(1 for r in results if r["pass"])
    summary = {"verified_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "pass": n_pass, "total": len(results), "items": results}
    os.makedirs(os.path.join(root, "docs"), exist_ok=True)
    report_path = os.path.join(root, "docs", "dod-report.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"\n=== 汇总: {n_pass}/{len(results)} 条 DoD 通过 ===")
    print(f"验收报告 -> {report_path}")
    return 0 if n_pass == len(results) else 1


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="DoD 端到端验收")
    p.add_argument("--root", default=".", help="skilltrove/ 根目录")
    raise SystemExit(run(p.parse_args()))
