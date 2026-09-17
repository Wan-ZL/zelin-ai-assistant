#!/usr/bin/env bash
# 剪辑：M/raw/*.webm + M/audio/*.mp3 → M/demo.mp4（CONTRACT §77 拟；QA 面 §58）。
# 每段按 shots.json 的计划时长裁齐 → 字幕 / 配音 / 画面共用同一把尺子（srt.py 不做二次测量）。
# 音轨两条：zh 是默认轨（第一条），en 第二条；画面 ≤ 1080p（源 1440×900，不放大）。
# 用法：bash scripts/media/assemble.sh <M>
set -euo pipefail

M="${1:?usage: assemble.sh <artefact dir M>}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FFMPEG="${FFMPEG:-$HOME/.local/bin/ffmpeg}"   # -nostdin 必带：ffmpeg 会吃掉 while read 的 stdin（镜头清单只剩半截）
FFPROBE="${FFPROBE:-ffprobe}"
PY="${PYTHON:-python3}"
WORK="$(mktemp -d /tmp/zaa-assemble-XXXX)"
trap 'rm -rf "$WORK"' EXIT

# bash 3.2（macOS 自带）没有 mapfile：镜头清单落一个临时文件，while read 逐行走
"$PY" -c "
import json
d=json.load(open('$HERE/shots.json',encoding='utf-8'))
for s in d['shots']: print(s['id'], s['seconds'])
" > "$WORK/shots.txt"

dur() { "$FFPROBE" -v error -show_entries format=duration -of csv=p=0 "$1"; }

# atempo 单级只吃 0.5–2.0；需要更快时串两级（本片的旁白最多 1.5 倍左右）
tempo_filter() {
  "$PY" - "$1" <<'PYEOF'
import sys
t = float(sys.argv[1])
t = max(0.5, min(t, 4.0))
parts = []
while t > 2.0:
    parts.append("atempo=2.0"); t /= 2.0
while t < 0.5:
    parts.append("atempo=0.5"); t /= 0.5
parts.append(f"atempo={t:.4f}")
print(",".join(parts))
PYEOF
}

: > "$WORK/video.txt"
for lang in zh en; do : > "$WORK/audio-$lang.txt"; done

while read -r id secs; do
  src="$M/raw/$id.webm"
  [ -f "$src" ] || { echo "missing shot video: $src" >&2; exit 1; }
  seg="$WORK/v-$id.mp4"
  "$FFMPEG" -nostdin -v error -y -i "$src" -t "$secs" -r 30 -vf "scale='min(1920,iw)':'min(1080,ih)':force_original_aspect_ratio=decrease,format=yuv420p" \
    -c:v libx264 -preset medium -crf 20 -an "$seg"
  echo "file '$seg'" >> "$WORK/video.txt"
  for lang in zh en; do
    voice="$M/audio/$lang-$id.mp3"
    [ -f "$voice" ] || { echo "missing narration audio: $voice" >&2; exit 1; }
    have="$(dur "$voice")"
    filter="$(tempo_filter "$("$PY" -c "print(max(1.0, $have / $secs))")")"
    aseg="$WORK/a-$lang-$id.m4a"
    "$FFMPEG" -nostdin -v error -y -i "$voice" -filter:a "$filter,apad" -t "$secs" -ar 48000 -ac 2 -c:a aac -b:a 128k "$aseg"
    echo "file '$aseg'" >> "$WORK/audio-$lang.txt"
  done
done < "$WORK/shots.txt"

"$FFMPEG" -nostdin -v error -y -f concat -safe 0 -i "$WORK/video.txt" -c copy "$WORK/video.mp4"
for lang in zh en; do
  "$FFMPEG" -nostdin -v error -y -f concat -safe 0 -i "$WORK/audio-$lang.txt" -c copy "$WORK/audio-$lang.m4a"
done

"$FFMPEG" -nostdin -v error -y -i "$WORK/video.mp4" -i "$WORK/audio-zh.m4a" -i "$WORK/audio-en.m4a" \
  -map 0:v:0 -map 1:a:0 -map 2:a:0 -c:v copy -c:a copy \
  -metadata:s:a:0 language=zho -metadata:s:a:0 title="旁白（中文）" \
  -metadata:s:a:1 language=eng -metadata:s:a:1 title="Narration (English)" \
  -disposition:a:0 default -disposition:a:1 0 -movflags +faststart "$M/demo.mp4"

echo "ASSEMBLE shots=$(wc -l < "$WORK/shots.txt" | tr -d " ") duration=$(dur "$M/demo.mp4") out=$M/demo.mp4"
