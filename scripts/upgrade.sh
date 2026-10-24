#!/bin/bash
# scripts/upgrade.sh — the lite upgrader (CONTRACT §79). Started detached by
# POST /api/update/install (the About page's "Upgrade now", or the §26 check when
# updates.auto_install is on); runnable by hand: bash scripts/upgrade.sh
#
#   1. git fetch --tags origin
#   2. origin/main must be exactly a v* release tag (else: refused)
#   3. the working tree must be clean, on main, and an ancestor of origin/main;
#      otherwise state/upgrade_state.json says status=diverged with the manual
#      commands — this script never resets, stashes or rewrites anything
#   4. git merge --ff-only origin/main
#   5. bash install.sh --non-interactive --from-upgrade
#   6. when the shell app is running: write the engineHandoff preference, quit
#      it, open -b com.zelin.ai-board (the new shell adopts the still-recording
#      engine, §61.8). The recording engine itself is never signalled.
#
# Receipt: state/upgrade_state.json (overwritten atomically each run) —
#   status   running | up_to_date | ok | refused | diverged | failed
#   reason, from (sha before), target (tag), install_rc, relaunched,
#   commands[] (what to run by hand), started_at, updated_at.
# A failure is a receipt + a notification (§28 relay); there is no automatic
# rollback. Exit: 0 ok / up to date, 1 failed, 2 refused, 3 another upgrade is
# running (state/upgrade.lock; a lock older than two hours is a killed run and is
# cleared), 4 diverged / dirty.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
STATE_DIR="${AIASSISTANT_HOME:-$REPO_ROOT}/state"
STATE_FILE="$STATE_DIR/upgrade_state.json"
LOCK_DIR="$STATE_DIR/upgrade.lock"
LOCK_STALE_MIN=120                   # = server/about.py UPGRADE_LOCK_STALE_S / 60
REMOTE="origin"
BRANCH="main"
SHELL_BUNDLE_ID="com.zelin.ai-board"
SHELL_EXEC_NAME="ZelinAIBoard"
ENGINE_HANDOFF_KEY="engineHandoff"   # = shell EngineOwnership.handoffKey (§61.8)
STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

say() { printf '%s upgrade: %s\n' "$(date -u +%H:%M:%SZ)" "$1"; }

# The interpreter install.sh pinned (config/runtime.json), else python3.
pick_py() {
    _p="$(sed -n 's/.*"python"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' \
        "$REPO_ROOT/config/runtime.json" 2>/dev/null)"
    if [ -n "$_p" ] && [ -x "$_p" ]; then printf '%s' "$_p"; else command -v python3 || echo /usr/bin/python3; fi
}
PY="$(pick_py)"

# write_state <status> [key=value ...] — `commands=<cmd>` may repeat. Atomic.
write_state() {
    mkdir -p "$STATE_DIR"
    "$PY" - "$STATE_FILE" "$STARTED_AT" "$@" <<'PYEOF'
import datetime, json, os, sys, tempfile
path, started, status = sys.argv[1], sys.argv[2], sys.argv[3]
doc = {"status": status, "started_at": started,
       "updated_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
for kv in sys.argv[4:]:
    key, _, value = kv.partition("=")
    if key == "commands":
        doc.setdefault("commands", []).append(value)
    elif value != "":
        doc[key] = value
fd, tmp = tempfile.mkstemp(prefix=".upgrade_state.", dir=os.path.dirname(path))
with os.fdopen(fd, "w", encoding="utf-8") as fh:
    fh.write(json.dumps(doc, ensure_ascii=False, indent=2) + "\n")
os.replace(tmp, path)
PYEOF
}

# Best-effort native notification through the shell's relay queue (§28).
notify_failure() { # $1 = one-line reason
    (cd "$REPO_ROOT" && AIASSISTANT_HOME="${AIASSISTANT_HOME:-$REPO_ROOT}" "$PY" -c '
import sys
from act.lib import notify
notify.notify("Upgrade did not finish", sys.argv[1])' "$1" >/dev/null 2>&1) || true
}

git_() { git -C "$REPO_ROOT" "$@" </dev/null; }

refuse_diverged() { # $1 = reason, $2 = sentence, rest = the manual commands
    _reason="$1"; _why="$2"; shift 2
    say "not upgrading: $_why"
    _cmds=()
    for _c in "$@"; do
        say "  by hand: $_c"
        _cmds+=("commands=$_c")
    done
    write_state diverged "reason=$_reason" "from=$FROM" "target=$TARGET" "${_cmds[@]}"
    notify_failure "$_why — the commands to run by hand are in state/upgrade_state.json"
    exit 4
}

relaunch_shell() {
    RELAUNCHED=no
    pgrep -x "$SHELL_EXEC_NAME" >/dev/null 2>&1 || { say "shell app not running — nothing to relaunch"; return 0; }
    defaults write "$SHELL_BUNDLE_ID" "$ENGINE_HANDOFF_KEY" -string "$(date -u +%FT%TZ)" >/dev/null 2>&1 || true
    pkill -TERM -x "$SHELL_EXEC_NAME" 2>/dev/null || true
    for _ in 1 2 3 4 5 6 7 8 9 10; do
        pgrep -x "$SHELL_EXEC_NAME" >/dev/null 2>&1 || break
        sleep 0.5
    done
    if pgrep -x "$SHELL_EXEC_NAME" >/dev/null 2>&1; then
        pkill -KILL -x "$SHELL_EXEC_NAME" 2>/dev/null || true
        sleep 0.5
    fi
    if open -b "$SHELL_BUNDLE_ID" >/dev/null 2>&1; then
        RELAUNCHED=yes
        say "relaunched the shell app (engineHandoff written first; it adopts the running engine)"
    else
        say "could not reopen the app — open \"Zelin's AI Assistant\" yourself"
    fi
}

main() {
    mkdir -p "$STATE_DIR"
    # A lock older than LOCK_STALE_MIN belongs to a run that was killed: clear it.
    if [ -d "$LOCK_DIR" ] && [ -n "$(find "$LOCK_DIR" -maxdepth 0 -mmin +"$LOCK_STALE_MIN" 2>/dev/null)" ]; then
        say "clearing a stale lock ($LOCK_DIR, older than $LOCK_STALE_MIN min)"
        rmdir "$LOCK_DIR" 2>/dev/null || true
    fi
    if ! mkdir "$LOCK_DIR" 2>/dev/null; then
        say "another upgrade is running ($LOCK_DIR exists) — nothing done"
        return 3
    fi
    trap 'rmdir "$LOCK_DIR" 2>/dev/null' EXIT
    FROM="$(git_ rev-parse --short HEAD 2>/dev/null || true)"
    TARGET=""
    write_state running "from=$FROM"
    say "fetching $REMOTE (repo $REPO_ROOT, at ${FROM:-?})"
    if ! git_ fetch --tags "$REMOTE"; then
        write_state failed "reason=fetch" "from=$FROM"
        notify_failure "git fetch failed (offline?)"
        return 1
    fi
    TARGET="$(git_ describe --tags --exact-match --match 'v[0-9]*' "$REMOTE/$BRANCH" 2>/dev/null || true)"
    if [ -z "$TARGET" ]; then
        say "refused: $REMOTE/$BRANCH is not exactly a v* release tag"
        write_state refused "reason=untagged" "from=$FROM"
        return 2
    fi
    if [ "$(git_ rev-parse HEAD)" = "$(git_ rev-parse "$REMOTE/$BRANCH^{commit}")" ]; then
        say "already at $TARGET"
        write_state up_to_date "from=$FROM" "target=$TARGET"
        return 0
    fi
    if [ -n "$(git_ status --porcelain --untracked-files=no)" ]; then
        refuse_diverged dirty "the checkout has local edits" \
            "git -C $REPO_ROOT status" \
            "git -C $REPO_ROOT stash   # or commit them on a branch of your own" \
            "bash $REPO_ROOT/scripts/upgrade.sh"
    fi
    if [ "$(git_ symbolic-ref -q --short HEAD 2>/dev/null || true)" != "$BRANCH" ]; then
        refuse_diverged not_on_main "the checkout is not on $BRANCH" \
            "git -C $REPO_ROOT checkout $BRANCH" \
            "bash $REPO_ROOT/scripts/upgrade.sh"
    fi
    if ! git_ merge-base --is-ancestor HEAD "$REMOTE/$BRANCH"; then
        refuse_diverged diverged "local $BRANCH has commits that are not on $REMOTE/$BRANCH" \
            "git -C $REPO_ROOT branch keep/local-$(date +%Y%m%d) HEAD" \
            "git -C $REPO_ROOT reset --hard $TARGET" \
            "bash $REPO_ROOT/install.sh"
    fi
    if ! git_ merge --ff-only -q "$REMOTE/$BRANCH"; then
        refuse_diverged ff_failed "git merge --ff-only $REMOTE/$BRANCH failed" \
            "git -C $REPO_ROOT status" \
            "git -C $REPO_ROOT merge --ff-only $REMOTE/$BRANCH && bash $REPO_ROOT/install.sh"
    fi
    say "fast-forwarded ${FROM:-?} -> $TARGET; running install.sh --non-interactive --from-upgrade"
    write_state running "from=$FROM" "target=$TARGET" "reason=installing"
    bash "$REPO_ROOT/install.sh" --non-interactive --from-upgrade </dev/null
    _rc=$?
    if [ "$_rc" -ne 0 ]; then
        say "install.sh reported $_rc failed step(s) — see state/install_report.json (no rollback)"
        write_state failed "reason=install" "from=$FROM" "target=$TARGET" "install_rc=$_rc" \
            "commands=bash $REPO_ROOT/install.sh --check"
        notify_failure "install.sh reported $_rc failed step(s) on $TARGET"
        return 1
    fi
    relaunch_shell
    write_state ok "from=$FROM" "target=$TARGET" "install_rc=0" "relaunched=$RELAUNCHED"
    say "done: $TARGET"
    return 0
}

main "$@"; exit $?
