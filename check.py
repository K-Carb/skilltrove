"""SkillTrove 环境自检（Deliverable Plan D4）：检查运行环境与数据产物。

用法：python check.py [--web]    加 --web 额外检查 Web 依赖（fastapi/uvicorn）。
退出码 0 = 就绪；非 0 = 有问题。
"""

from __future__ import annotations

import argparse
import json
import importlib.util
import os
import socket
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
checks = []

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def check(name: str, ok: bool, detail: str = "") -> None:
    checks.append((name, ok, detail))
    print(f"  [{'OK ' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--web", action="store_true", help="额外检查 Web 依赖")
    args = p.parse_args()

    print("=== SkillTrove 环境自检 ===")
    check("Python 版本 >= 3.10", sys.version_info >= (3, 10), sys.version.split()[0])

    # 数据产物
    # archive/manifest.json 必须存在：它是 score 的 join 查证依据。缺失会让所有
    # episode 静默判为低分、下游 cluster 无输入，而故障要穿三层才被看见——
    # README 与看板「一键发现」都按此路径调用，故纳入自检。
    for name in ("registry/registry.json", "archive/facts.json", "archive/episodes.jsonl",
                 "archive/manifest.json", "archive/scored.jsonl", "data/candidates.json"):
        check(f"产物 {name}", os.path.isfile(os.path.join(ROOT, name)))

    # JSON 可解析（损坏早发现；runbook 第 4 条的前置排查也依赖这里）
    for name in ("registry/registry.json", "data/candidates.json",
                 "data/skill-issues.json", "archive/facts.json"):
        p = os.path.join(ROOT, name)
        if os.path.isfile(p):
            try:
                json.load(open(p, encoding="utf-8"))
                check(f"JSON 可解析 {name}", True, "")
            except ValueError as e:
                check(f"JSON 可解析 {name}", False, str(e)[:100])

    # 共享库
    skills_root = os.path.join(ROOT, "skills")
    if os.path.isdir(skills_root):
        names = [n for n in os.listdir(skills_root) if os.path.isdir(os.path.join(skills_root, n))]
        for n in names:
            md = os.path.isfile(os.path.join(skills_root, n, "SKILL.md"))
            check(f"skill {n}/SKILL.md", md)
    else:
        check("skills/ 目录", False)

    # Web 依赖
    if args.web:
        check("fastapi 可导入", importlib.util.find_spec("fastapi") is not None)
        check("uvicorn 可导入", importlib.util.find_spec("uvicorn") is not None)
        try:
            s = socket.socket()
            s.bind(("127.0.0.1", int(os.environ.get("SKILLTROVE_WEB_PORT", "8000"))))
            s.close()
            check("端口 8000 可用", True)
        except OSError:
            check("端口 8000 可用", False, "被占用")

    n_fail = sum(1 for _, ok, _ in checks if not ok)
    print(f"\n=== 自检完成: {len(checks) - n_fail}/{len(checks)} 通过 ===")
    return 1 if n_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
