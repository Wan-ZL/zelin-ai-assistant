#!/usr/bin/env bash
# Assemble captured frames + music into the final mp4(s) in ~/Downloads.
# Music: "Voxel Revolution" by Kevin MacLeod (incompetech.com), CC BY 4.0 —
# keep the attribution line in the X post / video description.
set -euo pipefail
cd "$(dirname "$0")/.."

FPS=30
MUSIC=promo/build/music.mp3
MUSIC_URL="https://incompetech.com/music/royalty-free/mp3-royaltyfree/Voxel%20Revolution.mp3"
OUT_H="$HOME/Downloads/zelin-ai-assistant-promo.mp4"
OUT_V="$HOME/Downloads/zelin-ai-assistant-promo-vertical.mp4"

[ -s "$MUSIC" ] || curl -fsSL -o "$MUSIC" "$MUSIC_URL"

DUR=$(grep -m1 'duration:' promo/stage/timeline.js | sed 's/[^0-9.]//g')
FADE_ST=$(awk "BEGIN{print $DUR-3}")

compose() { # $1 frames dir, $2 output
  ffmpeg -y -v warning -framerate "$FPS" -i "$1/f%05d.png" -i "$MUSIC" \
    -filter_complex "[1:a]atrim=0:${DUR},afade=t=in:st=0:d=0.3,afade=t=out:st=${FADE_ST}:d=3[a]" \
    -map 0:v -map "[a]" -c:v libx264 -pix_fmt yuv420p -crf 18 -preset slow \
    -c:a aac -b:a 192k -movflags +faststart -t "$DUR" "$2"
  echo "wrote $2"
}

[ -d promo/build/frames ] && compose promo/build/frames "$OUT_H"
[ -d promo/build/frames-v ] && compose promo/build/frames-v "$OUT_V"
echo 'attribution: "Voxel Revolution" Kevin MacLeod (incompetech.com), CC BY 4.0'
