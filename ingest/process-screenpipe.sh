#!/bin/bash
# Auto-process files in 1 - unprocessed/ via the /unprocessed-ingest skill.
# Triggered by user crontab cron-chain (every :00 and :30):
#   screenpipe-export.sh && screenpipe-cleanup.sh && this script
# (Was previously launchd file-watcher; switched to cron because TCC blocks
# launchd-spawned bash from reading ~/Documents on macOS Sonoma+.)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VAULT="$HOME/Documents/Obsidian Vault"
UNPROCESSED="$VAULT/1 - unprocessed"
LOCKFILE="/tmp/process-screenpipe.lock"
LOGFILE="/tmp/screenpipe-auto.log"

# Prevent concurrent runs — PID lock.
# (Was an mtime lock with a 30-min staleness cutoff, but real runs take
# 26-33 min: a slow run's lock could be declared stale and a second run
# started on top of it — 2026-07-08 double-run incident. A PID lock asks
# "is the holder actually alive" instead of guessing from age.)
if [ -f "$LOCKFILE" ]; then
    LOCK_PID="$(tr -cd '0-9' < "$LOCKFILE" 2>/dev/null)"
    # Holder counts as alive if the PID is this script OR its headless-claude
    # child (an orphaned claude keeps ingesting after its parent script dies).
    if [ -n "$LOCK_PID" ] && ps -p "$LOCK_PID" -o command= 2>/dev/null \
            | grep -qE 'process-screenpipe|unprocessed-ingest'; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] Skipped — already running (pid $LOCK_PID)" >> "$LOGFILE"
        # exit 3 = "another run holds the lock" so callers (main-window button)
        # can say "already running" instead of a misleading "done"
        exit 3
    fi
    # Holder gone (crash/reboot) or legacy PID-less lock — take over.
    rm -f "$LOCKFILE"
fi
trap 'rm -f "$LOCKFILE"' EXIT
echo $$ > "$LOCKFILE"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Triggered (cron-chain) — checking for files..." >> "$LOGFILE"

# Defensive padding: with cron-chain (export && cleanup && process) the export
# is already complete by the time we run, so this is belt-and-suspenders.
# Kept at 90s as a guard against partial writes from any other source.
# Manual triggers (Screenpipe-Export.command / 主窗口"立即导出"/"立即 Ingest")
# set SCREENPIPE_NO_WAIT=1 to skip it — no export is racing us there.
if [ "${SCREENPIPE_NO_WAIT:-0}" != "1" ]; then
    sleep 90
fi

# Find any non-dotfile in the inbox (skip .DS_Store and other hidden files).
# The /unprocessed-ingest skill handles all file types via its dispatch table.
# DIAG 2026-05-20: capture stderr to log to debug "No ingestable files" while
# files clearly exist (TCC / FDA suspected). Remove this diag once root cause
# is confirmed.
FIND_STDERR=$(mktemp)
FILES=$(find "$UNPROCESSED" -maxdepth 1 -type f ! -name '.*' 2>"$FIND_STDERR")
FIND_EXIT=$?
DIR_LS_COUNT=$(ls -1 "$UNPROCESSED" 2>>"$LOGFILE" | wc -l | tr -d ' ')
FILES_COUNT=$(printf '%s\n' "$FILES" | grep -c .)
echo "[$(date '+%Y-%m-%d %H:%M:%S')] DIAG find_exit=$FIND_EXIT find_count=$FILES_COUNT ls_count=$DIR_LS_COUNT stderr=$(tr '\n' '|' < "$FIND_STDERR")" >> "$LOGFILE"
rm -f "$FIND_STDERR"

if [ -z "$FILES" ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] No ingestable files found — exiting" >> "$LOGFILE"
    echo "---" >> "$LOGFILE"
    exit 0
fi

FILE_COUNT=$(echo "$FILES" | wc -l | tr -d ' ')
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Processing triggered — $FILE_COUNT file(s) found" >> "$LOGFILE"

cd "$VAULT" || exit 1

# Cron auth strategy: use ANTHROPIC_API_KEY (not OAuth via Keychain).
#
# Background — there are TWO macOS sandboxes blocking cron:
#   1. macOS Keychain — only readable from a process in the user's Aqua GUI
#      session. Cron runs in a daemon session, so OAuth tokens are inaccessible.
#   2. Audit session — `launchctl asuser <UID>` can in principle bridge into
#      the user's GUI session, but the caller itself must already be in the
#      user's audit session. From cron's daemon audit session, the asuser call
#      fails with "Could not switch to audit session: Operation not permitted".
#
# Both block OAuth-based auth from cron. The ONLY auth path that bypasses
# both is ANTHROPIC_API_KEY env var — claude CLI uses it directly, no keychain
# or session bridge needed. Trade-off: usage bills against the API key
# (a metered API key), not against the Pro subscription.
#
# IMPORTANT: key file MUST live outside TCC-protected folders (~/Desktop, ~/Documents,
# ~/Downloads, etc). cron's grandfathered FDA via /usr/sbin/cron covers ~/Documents
# but NOT ~/Desktop — silently empty grep → ANTHROPIC_API_KEY="" → abort.
#
# Resolution order (CONTRACT §19, mirrors act/lib/secrets.resolve_credential):
#   1. $AIASSISTANT_HOME/config/secrets/anthropic-api-key.txt (App 设置窗口保存;
#      defaults to ~/Projects/zelin-ai-assistant when AIASSISTANT_HOME unset)
#   2. legacy ~/.config/anthropic-key.txt (single line, sk-ant-... only). Refresh:
#      grep -E '^sk-ant-' ~/Desktop/Keys/anthropic_key.txt > ~/.config/anthropic-key.txt
SECRETS_KEY_FILE="${AIASSISTANT_HOME:-$HOME/Projects/zelin-ai-assistant}/config/secrets/anthropic-api-key.txt"
if [ -s "$SECRETS_KEY_FILE" ]; then
    export ANTHROPIC_API_KEY=$(cat "$SECRETS_KEY_FILE" 2>>"$LOGFILE")
else
    export ANTHROPIC_API_KEY=$(cat "$HOME/.config/anthropic-key.txt" 2>>"$LOGFILE")
fi
if [ -z "$ANTHROPIC_API_KEY" ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ❌ ANTHROPIC_API_KEY empty (neither $SECRETS_KEY_FILE nor ~/.config/anthropic-key.txt readable?) — aborting" >> "$LOGFILE"
    echo "---" >> "$LOGFILE"
    exit 1
fi

# Resolve the claude binary — cron's PATH is minimal, so check the usual homes
# instead of hardcoding one machine's install path (repo-portable, CONTRACT §18).
CLAUDE_BIN="$(command -v claude 2>/dev/null || true)"
if [ -z "$CLAUDE_BIN" ]; then
    for c in "$HOME/.local/bin/claude" /opt/homebrew/bin/claude /usr/local/bin/claude; do
        if [ -x "$c" ]; then CLAUDE_BIN="$c"; break; fi
    done
fi
if [ -z "$CLAUDE_BIN" ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ❌ claude CLI not found — aborting" >> "$LOGFILE"
    echo "---" >> "$LOGFILE"
    exit 1
fi

# Skill lives in the vault (original setup); fall back to the repo's copy so a
# fresh machine works before the vault skill is provisioned (self-contained).
SKILL_MD="$VAULT/.claude/skills/unprocessed-ingest/SKILL.md"
[ -f "$SKILL_MD" ] || SKILL_MD="$SCRIPT_DIR/skills/unprocessed-ingest/SKILL.md"

# Run claude in headless print mode and load the /unprocessed-ingest skill directly.
"$CLAUDE_BIN" -p "Read \"$SKILL_MD\" and execute it on all files in 1 - unprocessed/." --allowedTools "Read,Write,Edit,Bash,Glob,Grep" >> "$LOGFILE" 2>&1
EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ✅ Processing complete (exit $EXIT_CODE)" >> "$LOGFILE"
else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ❌ Processing failed (exit $EXIT_CODE)" >> "$LOGFILE"
fi

echo "---" >> "$LOGFILE"
