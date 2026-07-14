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
