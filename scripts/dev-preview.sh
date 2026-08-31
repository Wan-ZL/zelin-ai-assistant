#!/usr/bin/env bash
# dev-preview：（缺 web/dist 则先 build）→ 种 demo 数据 → 起 server → 开浏览器（BUILD-CONTRACT §2.3）。
# 用法：
#   scripts/dev-preview.sh [scene]   # demo 模式；scene ∈ captured|initial|approved|running|review|done
#   scripts/dev-preview.sh --real    # 真实数据：读 §19 home 指针（或 AIASSISTANT_HOME），不种 demo
# demo 模式一律使用临时 AIASSISTANT_HOME —— 绝不触碰生产 state/（ground rule 3）。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${ZAI_PORT:-47820}"

# --- 参数解析：--real 或一个 scene 名，二者互斥（与参数顺序无关：先记
# scene-seen 旗标，循环结束后统一校验——`initial --real` 也必须被拒）---
REAL_MODE=0
SCENE="initial"
SCENE_SEEN=0
for arg in "$@"; do
  case "$arg" in
    --real) REAL_MODE=1 ;;
    -h|--help)
      sed -n '2,6p' "${BASH_SOURCE[0]}"
      exit 0
      ;;
    *)
      SCENE="$arg"
      SCENE_SEEN=1
      ;;
  esac
done
if [ "$REAL_MODE" = 1 ] && [ "$SCENE_SEEN" = 1 ]; then
  echo "dev-preview: scene 参数仅用于 demo 模式，不能与 --real 同用" >&2
  exit 1
fi

# --- web/dist 自愈：缺席则现场 build（首次 clone 后无需手动步骤）---
DIST="$ROOT/web/dist/index.html"
if [ ! -f "$DIST" ]; then
  echo "dev-preview: web/dist 尚未构建，现在构建（首次约 1-2 分钟）…"
  if ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1; then
    echo "dev-preview: 需要 Node.js（LTS）来构建 web 看板 —— 未找到 node/npm。" >&2
    echo "  安装: https://nodejs.org 或 brew install node，然后重跑本脚本。" >&2
    exit 1
  fi
  (cd "$ROOT/web" && npm install && npm run build)
  if [ ! -f "$DIST" ]; then
    echo "dev-preview: web build 结束但 web/dist/index.html 仍缺席，请检查上方 npm 输出" >&2
    exit 1
  fi
  echo "dev-preview: web/dist 构建完成"
fi

if [ "$REAL_MODE" = 1 ]; then
  # --- 真实模式：按 §19 解析 home（env AIASSISTANT_HOME → home 指针文件）---
  POINTER="$HOME/Library/Application Support/ZelinAIAssistant/home.txt"
  RUN_HOME="${AIASSISTANT_HOME:-}"
  if [ -z "$RUN_HOME" ] && [ -f "$POINTER" ]; then
    RUN_HOME="$(head -n 1 "$POINTER")"
  fi
  if [ -z "$RUN_HOME" ] || [ ! -d "$RUN_HOME" ]; then
    echo "dev-preview: 找不到真实数据目录。" >&2
    echo "  --real 需要 §19 home 指针（${POINTER}，install.sh 会写入）" >&2
    echo "  或环境变量 AIASSISTANT_HOME 指向你的 repo 根。" >&2
    echo "  还没安装过？先跑 bash install.sh，或先用 demo 模式：bash scripts/dev-preview.sh" >&2
    exit 1
  fi
  if [ ! -f "$RUN_HOME/state/dashboard.json" ]; then
    echo "dev-preview: 注意 —— $RUN_HOME/state/dashboard.json 不存在（actd 还没跑过？），看板会是空的" >&2
  fi
  MODE_DESC="real, home=$RUN_HOME"
else
  # --- demo 模式：种虚构数据到临时 home ---
  RUN_HOME="${ZAI_DEMO_HOME:-/tmp/zai-demo}"
  SEEDER="$ROOT/scripts/demo_seed.py"
  if [ ! -f "$SEEDER" ]; then
    echo "dev-preview: demo_seed.py not found" >&2
    exit 1
  fi
  python3 "$SEEDER" "$RUN_HOME" --scene "$SCENE"
  MODE_DESC="scene=$SCENE, home=$RUN_HOME"
fi

AIASSISTANT_HOME="$RUN_HOME" ZAI_PORT="$PORT" PYTHONPATH="$ROOT" \
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
echo "dev-preview: $URL  ($MODE_DESC)"
if [ "$(uname)" = "Darwin" ]; then
  open "$URL"
fi

wait "$SERVER_PID"
