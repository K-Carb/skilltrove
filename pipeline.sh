#!/usr/bin/env bash
# SkillTrove 提交流水线（对照《持续交付》Ch5：一条命令、阶段最快优先、fail-fast）
# 用法：bash pipeline.sh    —— 全绿才算过；任何阶段失败立即中止
# ASCII 标记 + 日志落 .scratch/pipeline/（中文输出经管道有编码 flake，判定一律读产物文件）
set -u
cd "$(dirname "$0")"
SCRATCH="$PWD/.scratch/pipeline"; mkdir -p "$SCRATCH"
PY=".venv/Scripts/python"; [ -x "$PY" ] || PY="python"

declare -a NAMES=() RESULTS=()
stage() {  # stage <名称> <命令...>
  local name="$1"; shift
  echo "── 阶段：$name"
  local t0=$SECONDS
  if "$@" > "$SCRATCH/$name.log" 2>&1; then
    local dt=$((SECONDS - t0))
    NAMES+=("$name"); RESULTS+=("PASS ${dt}s")
    echo "   PASS (${dt}s)"
  else
    local dt=$((SECONDS - t0))
    NAMES+=("$name"); RESULTS+=("FAIL ${dt}s")
    echo "   FAIL (${dt}s) —— 日志: $SCRATCH/$name.log"; tail -20 "$SCRATCH/$name.log"
    print_summary; exit 1
  fi
}

print_summary() {
  echo; echo "══ 流水线汇总 ══"
  local i
  for i in "${!NAMES[@]}"; do echo "  ${RESULTS[$i]}  ${NAMES[$i]}"; done
}

# 阶段 1（commit stage：秒级，语法与风格门禁）
stage lint      "$PY" -m ruff check .
stage css-guard "$PY" -m unittest tests.test_css_integrity
stage js-syntax node --check web/static/app.js

# 阶段 2（单元测试）
stage unittest  "$PY" -m unittest discover -s tests

# 阶段 3（验收：端到端 DoD；判定读报告 JSON 而非 grep 输出）
stage dod       "$PY" demo.py --quick
"$PY" - <<'PYEOF'
import json, sys
r = json.load(open("docs/dod-report.json", encoding="utf-8"))
sys.exit(0 if r.get("pass") == r.get("total") and r["total"] > 0 else 1)
PYEOF
[ $? -eq 0 ] && echo "   ✓ DoD 报告 5/5（docs/dod-report.json）" || { echo "   ✗ DoD 报告未全过"; print_summary; exit 1; }
NAMES+=("dod-report"); RESULTS+=("PASS 0s")

# 阶段 4（验收：前端全路由截图 + DOM 断言）
stage frontend  bash web/verify-frontend.sh

print_summary
echo "══ 流水线全绿 ══"
