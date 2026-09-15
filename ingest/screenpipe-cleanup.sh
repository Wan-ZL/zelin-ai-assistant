#!/bin/bash
# screenpipe 磁盘清理（CONTRACT §18 cron 链第二步；§72.2 + §72.4）。
# 1) 原始媒体：删早于 recording.media_retention_minutes 分钟的 jpg / mp4（出厂 60 = 历来的写死值），
#    每轮留一条回执 state/screenpipe_prune.json——「清理停了」与「没东西可删」从外面看一模一样，
#    而前一种会悄悄把盘吃满（§72.4）。回执里记的不只是这一轮：`last_ok_ts` 把**上次真正跑完**
#    的时刻一路带下去，否则一次 unreadable / partial 就把成功那一刻擦掉，「已经多久没删过东西」
#    再也算不出来。枚举本身失败（子目录读不到 / 文件在扫的过程中变动）单列 partial——
#    丢掉 find 的退出码就等于把「没扫完」报成「干净的一轮」。
#    OCR 文本 + 音频转写在 db.sqlite 里，这一步不碰。
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

# 回执里某个字串键的值（只解析本脚本自己写的紧凑形；`[{,]"key":"` 里那个前导
# 字符让 "ts" 不会匹配到 "last_ok_ts" 上）。读不到 = 空。
receipt_string() {  # $1 = key, $2 = 回执正文
    printf '%s' "$2" | sed -n "s/.*[{,]\"$1\":\"\([^\"]*\)\".*/\1/p"
}

# 上一轮回执里的「上次真正跑完」时刻，一路带下去（§72.4 追记）：只有 ok 那一轮才刷新它，
# 所以一次 unreadable / partial 不会把它擦掉——设置页正是靠它说出「已经多久没有一轮干净跑完」。
# 升级前写的回执没有这一键：它自己是 ok 的话，它的 ts 就是一次成功。
# 形状只收本脚本写出来的那种 ISO 秒（回执被人手改坏了也拼不进新 JSON 里）。
read_last_ok_ts() {
    local doc ts
    doc="$(cat "$RECEIPT" 2>/dev/null)" || doc=""
    ts="$(receipt_string last_ok_ts "$doc")"
    if [ -z "$ts" ] && [ "$(receipt_string state "$doc")" = "ok" ]; then
        ts="$(receipt_string ts "$doc")"
    fi
    case "$ts" in
        [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z) ;;
        *) ts="" ;;
    esac
    printf '%s\n' "$ts"
}

write_receipt() {  # $1 = state, $2 = files, $3 = bytes
    local dir tmp escaped now last_ok
    dir="$(dirname "$RECEIPT")"
    now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    last_ok="$PREV_OK_TS"
    [ "$1" = "ok" ] && last_ok="$now"
    if [ -n "$last_ok" ]; then
        last_ok="\"$last_ok\""
    else
        last_ok="null"          # 一轮都没干净跑完过：JSON null，不是空串（投影据此说「没有记录」）
    fi
    mkdir -p "$dir" 2>/dev/null || return 0
    tmp="$RECEIPT.tmp.$$"
    escaped="$(printf '%s' "$DATA_DIR" | sed 's/\\/\\\\/g; s/"/\\"/g')"
    if printf '{"ts":"%s","state":"%s","retention_minutes":%s,"deleted_files":%s,"deleted_bytes":%s,"data_dir":"%s","last_ok_ts":%s}\n' \
        "$now" "$1" "$RETENTION" "$2" "$3" "$escaped" "$last_ok" > "$tmp" 2>/dev/null; then
        mv -f "$tmp" "$RECEIPT" 2>/dev/null
    fi
    rm -f "$tmp" 2>/dev/null
    return 0
}

DELETED_FILES=0
DELETED_BYTES=0

delete_listed() {  # $1 = NUL 分隔的清单文件；结果落 DELETED_FILES / DELETED_BYTES
    local victim size
    DELETED_FILES=0
    DELETED_BYTES=0
    while IFS= read -r -d '' victim; do
        size="$(file_size "$victim")"
        if rm -f "$victim" 2>/dev/null; then
            DELETED_FILES=$((DELETED_FILES + 1))
            DELETED_BYTES=$((DELETED_BYTES + size))
        fi
    done < "$1"
}

prune_media() {
    local list status
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
    # 清单落临时文件（而不是 `< <(find …)`）：进程替换会把 find 自己的退出码丢掉，
    # 于是「子目录读不到」这种没扫完的一轮会被报成 ok + 删了 0 个——那个子树里的
    # 旧文件永远留着，而回执说一切正常（§72.4 追记）。macOS 的 bash 3.2 没有
    # `mapfile -d ''`，所以走文件而不是数组。
    list="$(mktemp "${TMPDIR:-/tmp}/zai-prune.XXXXXX" 2>/dev/null)" || list=""
    if [ -z "$list" ]; then
        write_receipt partial 0 0       # 连清单都建不出来：这一轮压根没扫，不许报 ok
        return 0
    fi
    find "$DATA_DIR" -type f \( -name '*.jpg' -o -name '*.mp4' \) -mmin +"$RETENTION" -print0 \
        > "$list" 2>/dev/null
    status=$?
    delete_listed "$list"
    rm -f "$list" 2>/dev/null
    # find 非零 = 有东西没枚举到（读不到的子目录、或文件在扫的过程中消失）：删掉的那些是真的，
    # 但这一轮不是干净的一轮——partial 不刷新 last_ok_ts，连着几小时都 partial 就会被报出来。
    if [ "$status" -eq 0 ]; then
        write_receipt ok "$DELETED_FILES" "$DELETED_BYTES"
    else
        write_receipt partial "$DELETED_FILES" "$DELETED_BYTES"
    fi
}

PY="$(resolve_python)"
RETENTION="$(resolve_retention)"
PREV_OK_TS="$(read_last_ok_ts)"

prune_media

# 第二步：db.sqlite 的文本保留期（§72.2）。--db 跟着上面的数据目录走，判例里指到临时库。
if [ -n "$PY" ]; then
    (cd "$REPO_ROOT" && "$PY" -m act.lib.screenpipe_retention \
        --db "$(dirname "$DATA_DIR")/db.sqlite" >/dev/null 2>&1) || true
fi

exit 0
