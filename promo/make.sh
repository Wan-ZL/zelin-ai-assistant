#!/usr/bin/env bash
# One-shot rebuild of the promo video: seed scenes -> render frames -> compose.
# See promo/README.md. Re-run any single step directly when iterating.
set -euo pipefail
cd "$(dirname "$0")/.."

bash promo/prepare.sh
[ -d promo/node_modules/playwright-core ] || (cd promo && npm install --no-save --no-audit --no-fund playwright-core)
node promo/render.mjs
node promo/render.mjs --vertical
bash promo/compose.sh
