#!/bin/bash
# Auto-process files in 1 - unprocessed/ via the /unprocessed-ingest skill.
# Triggered by user crontab cron-chain (every :00 and :30):
#   screenpipe-export.sh && screenpipe-cleanup.sh && this script
# (Was previously launchd file-watcher; switched to cron because TCC blocks
# launchd-spawned bash from reading ~/Documents on macOS Sonoma+.)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
# Same home the daemon uses (CONTRACT §19); default to the checkout this
# script lives in so a clone outside ~/Projects still reads its own config.
export AIASSISTANT_HOME="${AIASSISTANT_HOME:-$REPO_ROOT}"

# Resolve vault paths through the config layer (sources.obsidian_*, P1-6):
# prefer the daemon's interpreter from config/runtime.json, else PATH python3.
# Any failure (no python, no act package, broken config) falls back to the
# legacy hardcoded path — this script runs from cron and must never break
# because a dependency is missing.
resolve_config_path() {  # $1 = config key, $2 = fallback path
    local py resolved
    py="$(sed -n 's/.*"python"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$REPO_ROOT/config/runtime.json" 2>/dev/null)"
    [ -x "$py" ] || py="$(command -v python3 2>/dev/null)"
    resolved=""
    if [ -n "$py" ]; then
        resolved="$(cd "$REPO_ROOT" 2>/dev/null && "$py" -m act.lib.config --print-path "$1" 2>/dev/null)"
    fi
    if [ -n "$resolved" ]; then
        printf '%s\n' "$resolved"
    else
        printf '%s\n' "$2"
    fi
}

UNPROCESSED="$(resolve_config_path obsidian_unprocessed "$HOME/Documents/Obsidian Vault/1 - unprocessed")"
# Vault root = obsidian_raw's parent — the same derivation rule the config
# layer itself uses for the pipeline dirs (v0.10.3 契约二).
VAULT="$(dirname "$(resolve_config_path obsidian_raw "$HOME/Documents/Obsidian Vault/2 - raw")")"
LOCKFILE="/tmp/process-screenpipe.lock"
LOGFILE="/tmp/screenpipe-auto.log"

# VAULT MIRROR mode (claude TCC isolation — see ingest/vault-sync.sh):
# screenpipe-export.sh, at the top of this same chain run, pulled the vault
# into the repo-local mirror and recorded the mode. In mirror mode claude
# works entirely inside the repo — it never sees ~/Documents, so a claude
# CLI update can no longer re-prompt for permissions or die with EPERM
# (the 07-09→07-13 incident: 38 straight cron failures from the CLI's
# per-version TCC identity). Results are pushed back by the app-bundle
# courier after a successful run.
# shellcheck source=/dev/null
. "$SCRIPT_DIR/vault-sync.sh"
VAULT_ROOT_REAL="$VAULT"
VAULT_SYNC_MODE="$(cat "$VAULT_SYNC_MODE_FILE" 2>/dev/null || echo direct)"
if [ "$VAULT_SYNC_MODE" = "mirror" ] && [ -d "$VAULT_MIRROR/1 - unprocessed" ]; then
    VAULT="$VAULT_MIRROR"
    UNPROCESSED="$VAULT_MIRROR/1 - unprocessed"
else
    VAULT_SYNC_MODE="direct"
fi

# Prevent concurrent runs — PID lock.
# (Was an mtime lock with a 30-min staleness cutoff, but real runs take
# 26-33 min: a slow run's lock could be declared stale and a second run
# started on top of it — 2026-07-08 double-run incident. A PID lock asks
# "is the holder actually alive" instead of guessing from age.)
if [ -f "$LOCKFILE" ]; then
    # Holder counts as alive if ANY recorded PID is this script OR its
    # headless-claude child (an orphaned claude keeps ingesting after its
    # parent script dies). The lock holds one PID per line — $$ at startup,
    # the claude child appended at spawn; vault_sync_processing_live
    # (vault-sync.sh, sourced above) checks every line.
    if vault_sync_processing_live; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] Skipped — already running (pids: $(tr '\n' ' ' < "$LOCKFILE" 2>/dev/null))" >> "$LOGFILE"
        # exit 3 = "another run holds the lock" so callers (main-window button)
        # can say "already running" instead of a misleading "done"
        exit 3
    fi
    # Every holder gone (crash/reboot) or legacy PID-less lock — take over.
    rm -f "$LOCKFILE"
fi
# On exit KEEP the lock while the headless-claude child is still alive: a
# killed parent (Ctrl-C in the .command Terminal, SIGTERM) orphans the
# backgrounded claude, which keeps writing the mirror for up to
# CLAUDE_MAX_SECONDS — dropping the lock then would let the next round start
# a second claude over the same inbox AND let its pull rsync --delete the
# live mirror workspace (the 2026-07-14 13:30 incident class). The next run
# takes over the stale lock once every recorded PID is gone.
# shellcheck disable=SC2329  # invoked via the EXIT trap below, not directly
cleanup_lock() {
    if [ -n "${CLAUDE_PID:-}" ] && ps -p "$CLAUDE_PID" -o command= 2>/dev/null \
            | grep -qE 'process-screenpipe|unprocessed-ingest'; then
        return 0
    fi
    rm -f "$LOCKFILE"
}
trap cleanup_lock EXIT
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
# shellcheck disable=SC2012  # deliberate second opinion: the diag compares `ls` vs `find` counts to expose TCC/FDA visibility gaps, so it must NOT use find
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
# but NOT ~/Desktop — a key file there reads as silently empty from cron.
#
# Resolution order (CONTRACT §19, mirrors act/lib/secrets.resolve_credential):
#   1. $AIASSISTANT_HOME/config/secrets/anthropic-api-key.txt (App 设置窗口保存;
#      AIASSISTANT_HOME resolved at the top of this script, defaulting to the
#      checkout this script lives in)
#   2. legacy ~/.config/anthropic-key.txt (single line, sk-ant-... only)
#   3. neither file → fall back to the claude CLI's own stored credentials.
#      The keychain caveats above are real on some machines, but not all: on an
#      always-logged-in Mac (e.g. a Mac mini) cron can often use the CLI's own
#      auth just fine. If claude then fails, its error lands in $LOGFILE below.
SECRETS_KEY_FILE="$AIASSISTANT_HOME/config/secrets/anthropic-api-key.txt"
if [ -s "$SECRETS_KEY_FILE" ]; then
    ANTHROPIC_API_KEY=$(cat "$SECRETS_KEY_FILE" 2>>"$LOGFILE")
    export ANTHROPIC_API_KEY
elif [ -s "$HOME/.config/anthropic-key.txt" ]; then
    ANTHROPIC_API_KEY=$(cat "$HOME/.config/anthropic-key.txt" 2>>"$LOGFILE")
    export ANTHROPIC_API_KEY
fi
if [ -z "${ANTHROPIC_API_KEY:-}" ]; then
    unset ANTHROPIC_API_KEY
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] no API key file — falling back to the claude CLI's own credentials" >> "$LOGFILE"
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

# Skill lives in the vault (original setup — the mirror carries the vault's
# copy); fall back to the repo's copy so a fresh machine works before the
# vault skill is provisioned (self-contained).
SKILL_MD="$VAULT/.claude/skills/unprocessed-ingest/SKILL.md"
[ -f "$SKILL_MD" ] || SKILL_MD="$SCRIPT_DIR/skills/unprocessed-ingest/SKILL.md"

# Run claude in headless print mode and load the /unprocessed-ingest skill
# directly — under a watchdog: a wedged run once held the chain's lock for
# 41 hours (2026-07-11, EINTR). 2h covers the observed worst honest case
# (26-33 min normal, longer on backlog) with wide margin.
CLAUDE_MAX_SECONDS="${CLAUDE_MAX_SECONDS:-7200}"
"$CLAUDE_BIN" -p "Read \"$SKILL_MD\" and execute it on all files in \"$UNPROCESSED\"." --allowedTools "Read,Write,Edit,Bash,Glob,Grep" >> "$LOGFILE" 2>&1 &
CLAUDE_PID=$!
# Record the child in the PID lock (second line): the liveness checks here
# and in vault-sync.sh promise to cover "this script OR its headless-claude
# child" — without this line the child was never actually in the lock, so
# killing the parent defeated both the double-run lock and the in-flight
# pull guard. (The child's command line carries the skill path, which the
# 'unprocessed-ingest' grep matches.)
echo "$CLAUDE_PID" >> "$LOCKFILE"
( sleep "$CLAUDE_MAX_SECONDS" && kill "$CLAUDE_PID" 2>/dev/null \
  && echo "[$(date '+%Y-%m-%d %H:%M:%S')] ⏱ watchdog killed claude after ${CLAUDE_MAX_SECONDS}s" >> "$LOGFILE" ) &
WATCHDOG_PID=$!
wait "$CLAUDE_PID"
EXIT_CODE=$?
kill "$WATCHDOG_PID" 2>/dev/null

if [ $EXIT_CODE -eq 0 ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ✅ Processing complete (exit $EXIT_CODE)" >> "$LOGFILE"
    if [ "$VAULT_SYNC_MODE" = "mirror" ]; then
        # publish the mirror's results (new raw/wiki/change-summary + consumed
        # inbox files) back to the real vault via the app-bundle courier.
        if vault_sync_push "$VAULT_ROOT_REAL" >> "$LOGFILE" 2>&1; then
            echo "[$(date '+%Y-%m-%d %H:%M:%S')] ✅ vault-sync push ok" >> "$LOGFILE"
        else
            # results stay safe in the mirror; the next chain run retries the
            # push BEFORE pulling (vault-sync.sh pending marker) so nothing
            # is lost. Fail the chain so the health banner surfaces it.
            echo "[$(date '+%Y-%m-%d %H:%M:%S')] ❌ vault-sync push failed — results held in mirror, will retry next run" >> "$LOGFILE"
            EXIT_CODE=1
        fi
    fi
else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ❌ Processing failed (exit $EXIT_CODE)" >> "$LOGFILE"
    if [ "$VAULT_SYNC_MODE" = "mirror" ]; then
        # A failed run can still hold mirror-ONLY state that the next round's
        # pull (rsync --delete) would destroy: this round's export dump (its
        # immediate push was skipped while our claude looked live) plus any
        # partial raw/wiki written before claude died — and the export
        # markers are long advanced, so losing the dump means that capture
        # window is never re-exported. The helper's push is additive
        # (--update + manifest-based inbox deletion only), so pushing after
        # a dead claude is safe; if the push fails too, vault_sync_push
        # leaves VAULT_PUSH_PENDING so the next pull retries the push
        # BEFORE any --delete.
        if vault_sync_push "$VAULT_ROOT_REAL" >> "$LOGFILE" 2>&1; then
            echo "[$(date '+%Y-%m-%d %H:%M:%S')] ✅ vault-sync push ok (mirror state salvaged after failed run)" >> "$LOGFILE"
        else
            echo "[$(date '+%Y-%m-%d %H:%M:%S')] ❌ vault-sync push failed — mirror state held, will retry next run" >> "$LOGFILE"
        fi
    fi
fi

echo "---" >> "$LOGFILE"

# Propagate the real outcome: the 227-line comment above promises "fail the
# chain" on a held-in-mirror push failure, but without this exit the script
# always returned 0 and the health banner never saw it.
exit "$EXIT_CODE"
