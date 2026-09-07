#!/bin/bash
# screenpipe 磁盘清理（CONTRACT §18 cron 链第二步；§71.2）。
# 1) 原始媒体：删一小时前的 jpg / mp4——OCR 文本 + 音频转写留在 db.sqlite（历来如此）。
# 2) 保留期：按 recording.retention_days（设置页「录制数据与磁盘」区，0 = 永久保留 = 出厂默认）
#    删掉 db.sqlite 里「已导出进 vault 且早于 N 天」的 frames / OCR / 音频转写行
#    （act/lib/screenpipe_retention.py，回执 state/screenpipe_retention.json）。
#    这一步永不让链断掉：模块自己把错误写进回执并退出 0，这里再 || true 兜一层。
find "$HOME/.screenpipe/data" -name "*.jpg" -mmin +60 -delete 2>/dev/null
find "$HOME/.screenpipe/data" -name "*.mp4" -mmin +60 -delete 2>/dev/null

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
export AIASSISTANT_HOME="${AIASSISTANT_HOME:-$REPO_ROOT}"

# 守护进程的解释器（config/runtime.json，与 screenpipe-export.sh 同款），否则 PATH 上的 python3。
RETENTION_PY="$(sed -n 's/.*"python"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$REPO_ROOT/config/runtime.json" 2>/dev/null)"
[ -x "$RETENTION_PY" ] || RETENTION_PY="$(command -v python3 2>/dev/null)"
if [ -n "$RETENTION_PY" ]; then
    (cd "$REPO_ROOT" && "$RETENTION_PY" -m act.lib.screenpipe_retention >/dev/null 2>&1) || true
fi
