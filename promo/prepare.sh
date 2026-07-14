#!/usr/bin/env bash
# Generate render inputs: demo_seed scene JSONs -> build/scenes.js + app icon.
set -euo pipefail
cd "$(dirname "$0")/.."

mkdir -p promo/build/scenes
# 'done' is a demo SCENE NAME here, not the loop keyword — quoted so the
# linter (SC1010) and any reader doing a double take both relax.
for s in captured initial approved running review 'done'; do
  python3 scripts/demo_seed.py promo/build/seed-tmp --scene "$s" >/dev/null
  mv promo/build/seed-tmp/state/dashboard.json "promo/build/scenes/$s.json"
done
rm -rf promo/build/seed-tmp

python3 - <<'EOF'
import json, pathlib
scenes = {p.stem: json.loads(p.read_text(encoding="utf-8"))
          for p in pathlib.Path("promo/build/scenes").glob("*.json")}
out = "window.SCENES = " + json.dumps(scenes, ensure_ascii=False) + ";"
pathlib.Path("promo/build/scenes.js").write_text(out, encoding="utf-8")
print("wrote promo/build/scenes.js")
EOF

sips -s format png -Z 512 mac/AppIcon.icns --out promo/build/icon.png >/dev/null
echo "prepare: done"
