#!/bin/bash
# screenpipe 磁盘清理（CONTRACT §18 cron 链第二步；§72.2 + §72.4）。
# 1) 原始媒体：删早于 recording.media_retention_minutes 分钟的 jpg / mp4（出厂 60 = 历来的写死值），
#    每轮留一条回执 state/screenpipe_prune.json——「清理停了」与「没东西可删」从外面看一模一样，
#    而前一种会悄悄把盘吃满（§72.4）。OCR 文本 + 音频转写在 db.sqlite 里，这一步不碰。
# 2) 保留期：按 recording.retention_days（设置页「录制数据与磁盘」区，0 = 永久保留 = 出厂默认）
#    删掉 db.sqlite 里「已导出进 vault 且早于 N 天」的 frames / OCR / 音频转写行
#    （act/lib/screenpipe_retention.py，回执 state/screenpipe_retention.json）。
#    这一步永不让链断掉：模块自己把错误写进回执并退出 0，这里再 || true 兜一层。
# 本脚本**永远 exit 0**：cron 链是 && 串的，清理的毛病不许吞掉这一轮 ingest；真相走回执。

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
export AIASSISTANT_HOME="${AIASSISTANT_HOME:-$REPO_ROOT}"

# 两个 env 缝只为判例（tests/integration/test_screenpipe_cleanup.py）：真实运行下就是引擎的
# 数据目录与本 checkout 的 state/——判例绝不许碰这台机器真实的 ~/.screenpipe。
DATA_DIR="${ZAI_SCREENPIPE_DATA_DIR:-$HOME/.screenpipe/data}"
RECEIPT="${ZAI_SCREENPIPE_PRUNE_RECEIPT:-$AIASSISTANT_HOME/state/screenpipe_prune.json}"
DEFAULT_RETENTION=60

# 守护进程的解释器（config/runtime.json，与 screenpipe-export.sh 同款），否则 PATH 上的 python3。
resolve_python() {
    local py
    py="$(sed -n 's/.*"python"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$REPO_ROOT/config/runtime.json" 2>/dev/null)"
    [ -x "$py" ] || py="$(command -v python3 2>/dev/null)"
    printf '%s\n' "$py"
}

# 保留分钟数 = overrides → config.yaml → 60，与守护进程同一层（设置页改完下一轮生效）。
# 打不出数就用出厂值：cron 消费方必须拿到能直接喂给 find 的值。
resolve_retention() {
    local resolved=""
    if [ -n "$PY" ]; then
        resolved="$(cd "$REPO_ROOT" 2>/dev/null && "$PY" -m act.lib.config \
            --print-value screenpipe_media_retention_minutes 2>/dev/null)"
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

prune_media() {
    local files=0 bytes=0 victim size
    # 目录不在 = 引擎还没录过（全新机器 / 录制关着）——如实记 no_data_dir，不当失败
    if [ ! -d "$DATA_DIR" ]; then
        write_receipt no_data_dir 0 0
        return 0
    fi
    # 目录在但进不去（TCC / 权限）= 真失败：从外面看与「删了 0 个」一模一样，
    # 而这一种会让盘一直涨。记 unreadable，设置页据此报警。
    if [ ! -r "$DATA_DIR" ] || [ ! -x "$DATA_DIR" ]; then
        write_receipt unreadable 0 0
        return 0
    fi
    while IFS= read -r -d '' victim; do
        size="$(file_size "$victim")"
        if rm -f "$victim" 2>/dev/null; then
            files=$((files + 1))
            bytes=$((bytes + size))
        fi
    done < <(find "$DATA_DIR" -type f \( -name '*.jpg' -o -name '*.mp4' \) -mmin +"$RETENTION" -print0 2>/dev/null)
    write_receipt ok "$files" "$bytes"
}

PY="$(resolve_python)"
RETENTION="$(resolve_retention)"

prune_media

# 第二步：db.sqlite 的文本保留期（§72.2）。--db 跟着上面的数据目录走，判例里指到临时库。
if [ -n "$PY" ]; then
    (cd "$REPO_ROOT" && "$PY" -m act.lib.screenpipe_retention \
        --db "$(dirname "$DATA_DIR")/db.sqlite" >/dev/null 2>&1) || true
fi

exit 0
