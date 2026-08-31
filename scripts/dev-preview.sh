#!/usr/bin/env bash
# dev-preview：种 demo 数据 → 起 server → 开浏览器（BUILD-CONTRACT §2.3）。
# 用法：scripts/dev-preview.sh [scene]   scene ∈ captured|initial|approved|running|review|done
# 一律使用临时 AIASSISTANT_HOME —— 绝不触碰生产 state/（ground rule 3）。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCENE="${1:-initial}"
DEMO_HOME="${ZAI_DEMO_HOME:-/tmp/zai-demo}"
PORT="${ZAI_PORT:-47820}"

# demo_seed.py 以本仓 scripts/ 为准
SEEDER="$ROOT/scripts/demo_seed.py"
if [ ! -f "$SEEDER" ]; then
  echo "dev-preview: demo_seed.py not found" >&2
  exit 1
fi

python3 "$SEEDER" "$DEMO_HOME" --scene "$SCENE"

AIASSISTANT_HOME="$DEMO_HOME" ZAI_PORT="$PORT" PYTHONPATH="$ROOT" \
  python3 -m server &
SERVER_PID=$!
trap 'kill "$SERVER_PID" 2>/dev/null || true' EXIT INT TERM

# 等 server 起来（最多 5s）
for _ in $(seq 1 50); do
  if curl -fsS "http://127.0.0.1:$PORT/api/board" >/dev/null 2>&1; then
    break
  fi
  sleep 0.1
done

URL="http://127.0.0.1:$PORT"
echo "dev-preview: $URL  (scene=$SCENE, home=$DEMO_HOME)"
if [ "$(uname)" = "Darwin" ]; then
  open "$URL"
fi

wait "$SERVER_PID"
