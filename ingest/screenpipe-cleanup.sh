#!/bin/bash
# Delete screenpipe raw media older than the retention window (CONTRACT §71).
# Keeps only OCR text + audio transcriptions in db.sqlite — this script never
# touches the database, so transcripts survive any retention setting.
#
# Retention window = `recording.media_retention_minutes` through the same
# config layer the daemon reads (settings_overrides.json → config.yaml → 60),
# so the web Settings knob takes effect on the next cron round with no
# restart. Any failure to resolve it falls back to 60: this runs from cron
# and must never break because a dependency is missing.
#
# Runs in the 30-minute cron chain BETWEEN export and ingest
# (screenpipe-export.sh && screenpipe-cleanup.sh && process-screenpipe.sh),
# so it always exits 0 — a prune problem must not swallow the ingest round.
# The truth about whether it worked goes into the receipt instead
# (state/screenpipe_prune.json, read by GET /api/settings/storage): a prune
# that stopped running and a prune that ran and found nothing look identical
# from outside, and with a retention window the first one costs data
# permanently.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
export AIASSISTANT_HOME="${AIASSISTANT_HOME:-$REPO_ROOT}"

# 两个 env 缝只为判例（tests/integration/test_screenpipe_cleanup.py）：真实
# 运行下就是引擎的数据目录与本 checkout 的 state/。
DATA_DIR="${ZAI_SCREENPIPE_DATA_DIR:-$HOME/.screenpipe/data}"
RECEIPT="${ZAI_SCREENPIPE_PRUNE_RECEIPT:-$AIASSISTANT_HOME/state/screenpipe_prune.json}"
DEFAULT_RETENTION=60

resolve_retention() {
    local py resolved
    py="$(sed -n 's/.*"python"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$REPO_ROOT/config/runtime.json" 2>/dev/null)"
    [ -x "$py" ] || py="$(command -v python3 2>/dev/null)"
    resolved=""
    if [ -n "$py" ]; then
        resolved="$(cd "$REPO_ROOT" 2>/dev/null && "$py" -m act.lib.config \
            --print-value recording_media_retention_minutes 2>/dev/null)"
    fi
    case "$resolved" in
        ''|*[!0-9]*) printf '%s\n' "$DEFAULT_RETENTION" ;;
        *)           printf '%s\n' "$resolved" ;;
    esac
}

file_size() {  # macOS stat 没有 -c；文件在这一瞬消失就算 0
    stat -f%z "$1" 2>/dev/null || stat -c%s "$1" 2>/dev/null || printf '0\n'
}

write_receipt() {  # $1 = state, $2 = files, $3 = bytes
    local dir tmp escaped
    dir="$(dirname "$RECEIPT")"
    mkdir -p "$dir" 2>/dev/null || return 0
    tmp="$RECEIPT.tmp.$$"
    escaped="$(printf '%s' "$DATA_DIR" | sed 's/\\/\\\\/g; s/"/\\"/g')"
    if printf '{"ts":"%s","state":"%s","retention_minutes":%s,"deleted_files":%s,"deleted_bytes":%s,"data_dir":"%s"}\n' \
        "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1" "$RETENTION" "$2" "$3" "$escaped" > "$tmp" 2>/dev/null; then
        mv -f "$tmp" "$RECEIPT" 2>/dev/null
    fi
    rm -f "$tmp" 2>/dev/null
    return 0
}

RETENTION="$(resolve_retention)"

# 目录不在 = 引擎还没录过（全新机器 / 录制关着）——如实记 no_data_dir，不当失败
if [ ! -d "$DATA_DIR" ]; then
    write_receipt no_data_dir 0 0
    exit 0
fi
# 目录在但进不去（TCC / 权限）= 真失败：从外面看与「没东西可删」一模一样，
# 而这一种会让盘一直涨。记 unreadable，设置页据此报警。
if [ ! -r "$DATA_DIR" ] || [ ! -x "$DATA_DIR" ]; then
    write_receipt unreadable 0 0
    exit 0
fi

files=0
bytes=0
while IFS= read -r -d '' victim; do
    size="$(file_size "$victim")"
    if rm -f "$victim" 2>/dev/null; then
        files=$((files + 1))
        bytes=$((bytes + size))
    fi
done < <(find "$DATA_DIR" -type f \( -name '*.jpg' -o -name '*.mp4' \) -mmin +"$RETENTION" -print0 2>/dev/null)

write_receipt ok "$files" "$bytes"
exit 0
