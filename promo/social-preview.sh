#!/usr/bin/env bash
# Render docs/assets/social-preview.png (1280×640, issue #19) from
# promo/social-preview.html with headless Chrome. Re-run whenever the UI
# changes materially, then upload the PNG by hand under GitHub → Settings →
# General → Social preview (there is no API for it — release checklist item,
# docs/assets/README.md).
#
#   bash promo/social-preview.sh            # → docs/assets/social-preview.png
#   PROMO_CHROME=/path/to/chrome bash promo/social-preview.sh
#
# The page + its two inputs are STAGED INTO A TEMP DIR first: Chrome reading a
# file:// page straight off an external volume hung indefinitely here (macOS
# TCC per-binary volume access, CONTRACT §55 family) — $TMPDIR is never gated.
# We wait for the PNG (not for Chrome to exit — it lingers on its updater);
# no PNG within 60 s = loud failure.
set -euo pipefail
cd "$(dirname "$0")/.."

OUT="docs/assets/social-preview.png"

find_chrome() {
  if [ -n "${PROMO_CHROME:-}" ]; then printf '%s\n' "$PROMO_CHROME"; return 0; fi
  local c
  for c in "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
           "/Applications/Chromium.app/Contents/MacOS/Chromium" \
           "$(command -v google-chrome 2>/dev/null || true)" \
           "$(command -v chromium 2>/dev/null || true)"; do
    if [ -n "$c" ] && [ -x "$c" ]; then printf '%s\n' "$c"; return 0; fi
  done
  echo "no Chrome/Chromium found — set PROMO_CHROME=/path/to/chrome" >&2
  return 1
}

CHROME="$(find_chrome)"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
mkdir -p "$STAGE/promo/build" "$STAGE/docs/assets" "$STAGE/profile"
cp promo/social-preview.html "$STAGE/promo/"
cp docs/assets/kanban.png "$STAGE/docs/assets/"
# app icon (same extraction as promo/prepare.sh)
sips -s format png -Z 512 mac/AppIcon.icns --out "$STAGE/promo/build/icon.png" >/dev/null

# cwd = the stage too (Chrome's helpers resolve cwd; an external-volume cwd is
# another TCC trip wire). --timeout caps the page-load wait so a stuck
# resource can't keep the screenshot from firing.
( cd "$STAGE" && exec "$CHROME" --headless=new --disable-gpu --hide-scrollbars --no-first-run \
  --no-default-browser-check --user-data-dir="$STAGE/profile" --window-size=1280,640 \
  --force-device-scale-factor=1 --timeout=15000 --virtual-time-budget=5000 \
  --screenshot="$STAGE/out.png" "file://$STAGE/promo/social-preview.html" ) >"$STAGE/chrome.log" 2>&1 &
CHROME_PID=$!
# Wait for the screenshot, not for Chrome: the PNG lands in ~3 s, but this
# Chrome build then lingers on its updater / GCM machinery for a minute or
# more. Once the file is present and its size is stable, the render is done.
LAST=0
DONE=0
for _ in $(seq 1 60); do
  if [ -s "$STAGE/out.png" ]; then
    SIZE="$(wc -c < "$STAGE/out.png" | tr -d ' ')"
    if [ "$SIZE" = "$LAST" ]; then DONE=1; break; fi
    LAST="$SIZE"
  elif ! kill -0 "$CHROME_PID" 2>/dev/null; then
    break
  fi
  sleep 1
done
kill "$CHROME_PID" 2>/dev/null || true
wait "$CHROME_PID" 2>/dev/null || true
if [ "$DONE" -ne 1 ]; then
  echo "chrome produced no screenshot within 60 s (log tail below)" >&2
  grep -v -i -e updater -e crashpad "$STAGE/chrome.log" | tail -n 20 >&2 || true
  exit 1
fi

# the file must be exactly 1280×640 (PNG IHDR: width/height big-endian at bytes 16..23)
python3 - "$STAGE/out.png" <<'EOF'
import struct, sys
data = open(sys.argv[1], "rb").read(24)
w, h = struct.unpack(">II", data[16:24])
assert data[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
assert (w, h) == (1280, 640), f"social preview is {w}x{h}, expected 1280x640"
EOF
cp "$STAGE/out.png" "$OUT"
echo "wrote $OUT (1280x640)"
