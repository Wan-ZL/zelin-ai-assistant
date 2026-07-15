#!/bin/bash
# vault-sync.sh — shared helper-location + pull/push wrappers for the ingest
# chain's VAULT MIRROR mode (claude TCC isolation, 2026-07-14).
#
# Sourced by screenpipe-export.sh and process-screenpipe.sh. Not executable
# on its own.
#
# WHY: the claude CLI installs per-version binaries and macOS TCC keys grants
# to the real binary path — every CLI update is a new TCC identity, so cron
# runs died with EPERM (38 consecutive ingest failures 07-09→07-13) and GUI
# runs re-prompt for Documents on every update. In mirror mode NOTHING in the
# chain touches ~/Documents except the vault-sync-helper that ships inside
# the app bundle (stable bundle id + stable signing cert = one Documents
# grant, made once via a normal GUI prompt, valid forever).
#
# Mode contract (state/vault_sync_mode, written by screenpipe-export.sh at
# the top of each chain run and read by process-screenpipe.sh in the SAME
# run): "mirror" = work against $VAULT_MIRROR; "direct" = legacy behavior
# (helper missing / grant missing / non-mac) — the chain always works, mirror
# mode is an upgrade, never a requirement.

VAULT_MIRROR="$AIASSISTANT_HOME/state/vault-mirror"
# shellcheck disable=SC2034  # consumed by the sourcing scripts, not here
VAULT_SYNC_MODE_FILE="$AIASSISTANT_HOME/state/vault_sync_mode"
# push failed last round → its products only exist in the mirror; a pull
# (rsync --delete) before a successful retry would DESTROY them.
VAULT_PUSH_PENDING="$AIASSISTANT_HOME/state/vault-sync-push-pending"
# the processing chain's PID lock (must match process-screenpipe.sh) — the
# in-flight guard below keys off it. Holds one PID per line: the script's own
# $$ at startup, plus the headless-claude child's PID once spawned (so a
# killed parent's orphaned claude still holds the lock).
VAULT_SYNC_PROCESS_LOCK="/tmp/process-screenpipe.lock"

# vault_sync_processing_live — true iff the previous round's processing is
# STILL RUNNING in the mirror (same liveness rule as process-screenpipe.sh's
# own lock takeover: ANY pid in the lock file belongs to the processing
# script or its headless-claude child).
vault_sync_processing_live() {
    local line pid
    [ -f "$VAULT_SYNC_PROCESS_LOCK" ] || return 1
    while IFS= read -r line || [ -n "$line" ]; do
        pid="$(printf '%s' "$line" | tr -cd '0-9')"
        [ -n "$pid" ] || continue
        if ps -p "$pid" -o command= 2>/dev/null \
                | grep -qE 'process-screenpipe|unprocessed-ingest'; then
            return 0
        fi
    done < "$VAULT_SYNC_PROCESS_LOCK"
    return 1
}

find_vault_sync_helper() {
    local c
    for c in "/Applications/Zelin's AI Assistant.app/Contents/MacOS/vault-sync-helper" \
             "$HOME/Applications/Zelin's AI Assistant.app/Contents/MacOS/vault-sync-helper"; do
        if [ -x "$c" ]; then printf '%s\n' "$c"; return 0; fi
    done
    return 1
}

# vault_sync_pull <vault_root> — refresh the mirror from the vault.
# Retries a pending push FIRST (see VAULT_PUSH_PENDING above). Returns 0 and
# echoes nothing on success; non-zero = caller stays in direct mode.
vault_sync_pull() {
    local vault_root="$1" helper
    helper="$(find_vault_sync_helper)" || return 1
    # IN-FLIGHT GUARD (2026-07-14 13:30 incident): the previous round's claude
    # was still writing raw/wiki in the mirror when the next round's export
    # ran this pull — rsync --delete wiped every un-pushed product, and since
    # a mirror-mode dump exists ONLY in the mirror until push, the source
    # dump died with it (the export marker had advanced: no re-export).
    # Processing alive → the mirror is a workspace, not a stale copy: skip
    # the pull, keep mirror mode (the caller still writes its export into the
    # mirror inbox; the round's eventual push carries everything home, and
    # the NEXT idle round pulls fresh vault edits).
    if vault_sync_processing_live; then
        echo "vault-sync: processing in flight — pull skipped (mirror is a live workspace)"
        return 0
    fi
    if [ -f "$VAULT_PUSH_PENDING" ]; then
        if "$helper" push --vault "$vault_root" --mirror "$VAULT_MIRROR"; then
            rm -f "$VAULT_PUSH_PENDING"
        else
            # cannot publish last round's results — do NOT pull over them.
            # Direct mode this round re-processes the still-present inbox
            # files: duplicated work beats destroyed work.
            return 1
        fi
    fi
    "$helper" pull --vault "$vault_root" --mirror "$VAULT_MIRROR"
}

# vault_sync_push <vault_root> — publish mirror results back to the vault.
vault_sync_push() {
    local vault_root="$1" helper
    helper="$(find_vault_sync_helper)" || return 1
    if "$helper" push --vault "$vault_root" --mirror "$VAULT_MIRROR"; then
        rm -f "$VAULT_PUSH_PENDING"
        return 0
    fi
    touch "$VAULT_PUSH_PENDING"
    return 1
}
