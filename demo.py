"""M4 端到端演示：一键串行执行六步闭环 + DoD 验收（Deliverable Plan M4）。

用法：
  python demo.py --quick   仅验收已有产物（不重跑，默认）
  python demo.py --full    全链重跑（export→score→cluster→draft→publish→recall→verify）
                           （cluster/draft 会再次调用 LLM，消耗配额）

说明：--full 会覆盖 archive/、data/candidates.json、skills/<name>/ 并新增 recall run。
注意：同一份导出数据重复全链时，F3 决策门会以「语义重复」拦截已沉淀的 skill（防重复机制），
不会生成新 skill / 新 recall run——--full 只有在新数据带来新聚类簇时才产生新沉淀。
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
MAIN = os.path.join(ROOT, "cli", "main.py")
# 数据源导出包：默认取仓库同级的 skilltrove-export-*/export-*/，可用环境变量覆盖（12-Factor）
SOURCE = os.environ.get("SKILLTROVE_EXPORT_SOURCE", os.path.join(ROOT, "examples", "sources"))
MANIFEST = os.path.join(SOURCE, "MANIFEST.json")


def run_step(name: str, args: list[str]) -> bool:
    print(f"\n===== {name} =====")
    r = subprocess.run([PY, MAIN] + args, cwd=ROOT)
    return r.returncode == 0


def full_chain() -> int:
    steps = [
        ("F1 导出", ["export", "--source", SOURCE, "--out", "archive/"]),
        ("F1.6 打分", ["score", "--episodes", "archive/episodes.jsonl"] + (["--manifest", MANIFEST] if os.path.isfile(MANIFEST) else [])),
        ("F2 聚类", ["cluster", "--episodes", "archive/scored.jsonl", "--out", "data/candidates.json"]),
        ("F3 草稿", ["draft", "--candidates", "data/candidates.json"]),
        ("F4/F5 发布", ["publish", "--skill", "implementation-research", "--status", "published"]),
        ("F6 调用回采", ["recall", "--skill", "implementation-research"]),
        ("DoD 验收", ["dod-verify"]),
    ]
    for name, args in steps:
        if not run_step(name, args):
            print(f"\n[FAIL] {name} 失败")
            return 1
    print("\n===== 全链演示完成 =====")
    return 0


def quick() -> int:
    print("快速验收（只核对已有产物）：")
    return 0 if run_step("DoD 验收", ["dod-verify"]) else 1


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--full", action="store_true", help="全链重跑（含 LLM 调用）")
    p.add_argument("--quick", action="store_true", help="快速验收已有产物（默认）")
    args = p.parse_args()
    return full_chain() if args.full else quick()


if __name__ == "__main__":
    raise SystemExit(main())
