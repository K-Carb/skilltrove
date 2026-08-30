"""F1 导出：多来源 → facts.json + episodes.jsonl + manifest.json（Deliverable Plan D2）。

数据源由适配器识别（`--adapter auto` 自动判定；可显式指定）：
  log-export / task-dirs / table / session-logs / git-repo / docs

实现见 cli/adapters.py（SourceAdapter 协议 + run_export 统一出口）；
本模块只做参数装配、适配器选择与输出汇总。

用法：
  python cli/main.py export --source <输入> [--adapter auto|...] [--out archive/] [--exclude-agents ...]
"""

from __future__ import annotations

import argparse
import os

from cli import adapters


def run(args) -> int:
    exclude = [a.strip() for a in (args.exclude_agents or "").split(",") if a.strip()]
    adapter = adapters.pick_adapter(args.source, args.adapter)
    print(f"数据源形态: {adapter.shape}（provider={adapter.provider}）")
    episodes, facts = adapters.run_export(args.source, args.out, adapter, exclude)

    included = [e for e in episodes if not e["excluded"]]
    print(f"MANIFEST: {len(included)} issues, 排除 agents: {exclude}")
    print(f"facts.json -> {os.path.join(args.out, 'facts.json')}")
    print(f"episodes.jsonl -> {os.path.join(args.out, 'episodes.jsonl')} "
          f"({len(episodes)} 条, 排除 {len(episodes) - len(included)} 条)")
    print(f"manifest.json -> {os.path.join(args.out, 'manifest.json')}（归一化任务清单，score join 查证用）")
    contributors = ", ".join(c["agent"] for c in facts["contributors"])
    print(f"贡献者: {contributors}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--source", required=True)
    p.add_argument("--out", default="archive")
    p.add_argument("--exclude-agents", default="", help="排除 agent 列表（逗号分隔）")
    p.add_argument("--adapter", default="auto",
                   help="数据源形态（auto/log-export/task-dirs/table/session-logs/git-repo/docs）")
    raise SystemExit(run(p.parse_args()))
