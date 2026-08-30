"""SkillTrove 六步闭环 CLI 入口。

用法：
  python cli/main.py <step> [args]
  python cli/main.py --help
"""

from __future__ import annotations

import argparse
import os
import sys

# Windows 控制台 UTF-8 输出（Git Bash 下避免 GBK 乱码）
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

# 保证 cli 包内相对导入可用（把 skilltrove/ 加入 path，使 `import cli` 可解析）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cli import cluster as cluster_mod # noqa: E402
from cli import draft as draft_mod     # noqa: E402
from cli import export as export_mod   # noqa: E402
from cli import registry as registry_mod # noqa: E402
from cli import score as score_mod     # noqa: E402
from cli import sync as sync_mod       # noqa: E402
from cli import verify as verify_mod   # noqa: E402


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--out", default=None, help="输出路径（默认各步约定）")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="skilltrove", description="SkillTrove 六步闭环 CLI（本地 MVP）")
    sub = p.add_subparsers(dest="step", required=True, title="步骤")

    pe = sub.add_parser("export", help="F1 导出：多来源 → facts + episodes（适配器识别）")
    pe.add_argument("--source", required=True, help="数据源（log-export 导出包 / 任务目录 / 表格 / 会话日志 / git 仓库 / 文档目录）")
    pe.add_argument("--out", default="archive", help="输出目录（默认 archive/）")
    pe.add_argument("--exclude-agents", default="", help="排除 agent 列表（逗号分隔；默认空。Q8：alice 在本批数据为真实执行者，不默认排除）")
    pe.add_argument("--adapter", default="auto",
                    help="数据源形态（auto/log-export/task-dirs/table/session-logs/git-repo/docs）")

    ps = sub.add_parser("score", help="F1.6 L2 打分：join 可查证 + 完成态偏差规则")
    ps.add_argument("--episodes", required=True, help="episodes.jsonl 路径")
    ps.add_argument("--manifest", default=None, help="导出包 MANIFEST.json（join 查证用）")
    ps.add_argument("--out", default=None, help="输出 scored.jsonl（默认同输入目录）")

    pc = sub.add_parser("cluster", help="F2 聚类：向量粗筛 + LLM 四维复核")
    pc.add_argument("--episodes", required=True, help="scored.jsonl 路径")
    pc.add_argument("--out", default="data/candidates.json")
    pc.add_argument("--threshold", type=float, default=None,
                    help="向量粗筛相似度阈值（默认按数据源形态：log-export 0.5 / git-repo 0.40）")
    pc.add_argument("--llm-backend", default=None, help="LLM 后端（默认 kimi）")
    pd = sub.add_parser("draft", help="F3 草稿生成：决策门 + SKILL.md + CogEval Case")
    pd.add_argument("--candidates", required=True, help="candidates.json 路径")
    pd.add_argument("--candidate", default=None, help="候选 ID（默认取第一个）")
    pd.add_argument("--out", default="skills", help="输出根目录（默认 skills/）")
    pd.add_argument("--registry", default="registry/registry.json")
    pd.add_argument("--name", default=None, help="强制 skill 名（覆盖 LLM 生成）")
    pd.add_argument("--llm-backend", default=None, help="LLM 后端（默认 kimi）")

    pr = sub.add_parser("publish", help="F5 发布：校验 skill 完整性 + 更新 registry 审核状态")
    pr.add_argument("--skill", required=True, help="skill 名（对应 skills/<name>/ 目录）")
    pr.add_argument("--status", default="published", help="审核状态（draft/in_review/published/deprecated）")
    pr.add_argument("--registry", default="registry/registry.json")

    prh = sub.add_parser("review-helper", help="F4.2 审核单生成（五要素 + 证据态 + 贡献者）")
    prh.add_argument("--skill", required=True)
    prh.add_argument("--registry", default="registry/registry.json")

    prc = sub.add_parser("recall", help="F6 调用回采：另一 agent 调用共享库 skill + eval")
    prc.add_argument("--skill", required=True)
    prc.add_argument("--prompt", default=None, help="调用任务（默认最小定位任务）")
    prc.add_argument("--agent", default="kimi", help="调用 agent（默认 kimi）")
    prc.add_argument("--registry", default="registry/registry.json")
    prc.add_argument("--timeout", type=int, default=600)
    prc.add_argument("--permission-mode", default=None, help="传给 claude 的 --permission-mode（如 acceptEdits）")
    pv = sub.add_parser("dod-verify", help="IMP-6 DoD 端到端验收（核对六步产物）")
    pv.add_argument("--root", default=".", help="skilltrove/ 根目录")
    return p


def main() -> int:
    p = build_parser()
    args = p.parse_args()
    step = args.step

    if step == "export":
        return export_mod.run(args)
    if step == "score":
        return score_mod.run(args)
    if step == "cluster":
        return cluster_mod.run(args)
    if step == "draft":
        return draft_mod.run(args)
    if step == "publish":
        return registry_mod.publish(args)
    if step == "review-helper":
        return registry_mod.review_helper(args)
    if step == "recall":
        return sync_mod.run(args)
    if step == "dod-verify":
        return verify_mod.run(args)

    print(f"[{step}] 尚未实现（见 spec/implementation-issues.md）")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
