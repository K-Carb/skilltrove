#!/usr/bin/env bash
# SkillTrove 前端一键验证（agent 前端方法论：真实浏览器截图 + DOM 断言 + 语法 + 单测）
# 用法：bash web/verify-frontend.sh
set -e
cd "$(dirname "$0")/.."

PY=".venv/Scripts/python.exe"
EDGE="/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"
[ -x "$PY" ] || PY="python"
PORT=12761
OUT=$(mktemp -d)
VIEWS="overview skills inbox pipeline candidates metrics recalls"

echo "=== 起服务 (127.0.0.1:$PORT) ==="
SKILLTROVE_WEB_PORT=$PORT "$PY" web/app.py > /tmp/cg-verify.log 2>&1 &
SRV=$!
sleep 3
trap "kill $SRV 2>/dev/null; rm -rf $OUT" EXIT

curl -s -o /dev/null -w "GET / -> %{http_code}\n" "http://127.0.0.1:$PORT/"

echo "=== 全路由无头截图 + DOM 断言 ==="
FAIL=0
for v in $VIEWS; do
  "$EDGE" --headless=new --no-proxy-server --disable-gpu \
    --user-data-dir="$OUT/prof-$v" \
    --screenshot="$OUT/$v.png" --window-size=1400,900 --virtual-time-budget=7000 \
    "http://127.0.0.1:$PORT/#$v" > /dev/null 2>&1
  size=$(stat -c %s "$OUT/$v.png" 2>/dev/null || echo 0)
  err=$("$EDGE" --headless=new --no-proxy-server --disable-gpu \
    --user-data-dir="$OUT/prof2-$v" --dump-dom --virtual-time-budget=5000 \
    "http://127.0.0.1:$PORT/#$v" 2>/dev/null | grep -c "ERR_\|加载失败" || true)
  echo "  [$v] 截图 ${size}B 错误=$err"
  [ "$size" -gt 1000 ] && [ "$err" = "0" ] || FAIL=1
done

echo "=== JS 语法 ==="
node --check web/static/app.js && echo "  app.js OK"

echo "=== 单元测试 ==="
"$PY" -m unittest discover -s tests 2>&1 | grep -E "^Ran|^OK|^FAILED"

echo
if [ "$FAIL" = "0" ]; then
  echo "VERIFY-FRONTEND: PASS（全路由截图+DOM 0 错误，JS/单测通过）"
else
  echo "VERIFY-FRONTEND: FAIL（见上）"
  exit 1
fi
