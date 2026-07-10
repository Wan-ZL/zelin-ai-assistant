#!/bin/bash
# Clean uninstall for Zelin's AI Assistant — the exact inverse of install.sh.
#
# Why this file matters: without it, "drag the app to Trash" leaves the actd
# daemon dispatching claude sessions (real API spend) and the cron chain
# recording/ingesting the screen — invisible, indefinitely. Uninstall must be
# one command, loud about every action, and conservative about user data.
#
# What it does (in order):
#   1. unload + delete every com.zelin.aiassistant.* launchd agent
#   2. remove OUR crontab lines only — matched by the marker tokens below,
#      the same distinctive tokens install.sh writes; all other lines kept
#   3. quit the menu-bar app and stop the screenpipe recording engine
#   4. remove the app bundle(s) from /Applications and ~/Applications
#   5. remove the root-owned pipeline master copy (or print the sudo command)
#
# What it KEEPS by default (printed with the exact removal command for each):
#   - this repo working copy, including state/ (your task history),
#     config.yaml and config/secrets/ (your keys)
#   - your Obsidian vault (never touched, not even with --purge)
#   - ~/.screenpipe recordings
#
# Flags:
#   --dry-run   print the full plan, change nothing, exit 0
#   --yes       skip the confirmation prompt (for scripted use)
#   --purge     ALSO delete user data: state/, config.yaml, config/secrets/,
#               config/runtime.json, redaction terms, the app-support pointer
#               dir and ~/.screenpipe recordings. The vault is still never
#               touched. The repo directory itself is left for a final manual
#               `rm -rf` (this script runs from inside it).
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$SCRIPT_DIR"
LA_DIR="$HOME/Library/LaunchAgents"
APP_NAME="Zelin's AI Assistant"
APP_BUNDLE_ID="com.zelin.ai-engineer"   # CONTRACT §12 — deliberately unchanged
PIPELINE_MASTER="/Library/Application Support/ZelinAIAssistant"
POINTER_DIR="$HOME/Library/Application Support/ZelinAIAssistant"

# cron marker tokens (CONTRACT §18) — a crontab line is OURS iff it matches
# one of these. install.sh only ever writes lines containing them; everything
# else in the user's crontab is preserved verbatim.
CRON_MARKERS='screenpipe-export\.sh|act\.radar|act\.digest|act\.analytics_sync|zelin-ai-assistant'

DRY=0; YES=0; PURGE=0
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY=1 ;;
        --yes)     YES=1 ;;
        --purge)   PURGE=1 ;;
        -h|--help)
            sed -n '2,33p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
            exit 0 ;;
        *) echo "unknown flag: $arg (see --help)" >&2; exit 2 ;;
    esac
done

ok()   { printf "  [ ok ] %s\n" "$1"; }
warn() { printf "  [warn] %s\n" "$1"; }
info() { printf "  [info] %s\n" "$1"; }
plan() { printf "  [dry-run] would %s\n" "$1"; }

# remove a path; on permission failure print the exact sudo command instead
# of failing silently. In dry-run mode only announces.
remove_path() {
    p="$1"
    [ -e "$p" ] || { info "not present (nothing to do): $p"; return 0; }
    if [ "$DRY" -eq 1 ]; then plan "remove: $p"; return 0; fi
    if rm -rf "$p" 2>/dev/null; then
        ok "removed: $p"
    else
        warn "could not remove (owned by root?): $p"
        info "run: sudo rm -rf \"$p\""
    fi
}

echo "=============================================="
echo " Zelin's AI Assistant — uninstall 卸载"
echo " repo: $REPO_ROOT"
[ "$DRY" -eq 1 ]   && echo " MODE: dry-run — printing the plan, changing nothing 只预览，不改动"
[ "$PURGE" -eq 1 ] && echo " MODE: purge — user data (state/, config, secrets, recordings) will be deleted too"
echo "=============================================="
echo ""
echo "This will stop all background services (AI dispatch, screen recording,"
echo "radars, cron ingest), remove the launchd agents, our crontab lines, the"
echo "app in /Applications and the root-owned pipeline master copy."
if [ "$PURGE" -eq 1 ]; then
    echo "--purge: ALSO deletes state/, config.yaml, config/secrets/, runtime"
    echo "pointer files and ~/.screenpipe recordings. Your Obsidian vault is"
    echo "never touched."
else
    echo "Kept (by default): this repo working copy incl. state/ and your keys,"
    echo "your Obsidian vault, ~/.screenpipe recordings — removal commands are"
    echo "printed at the end. 默认保留你的数据；每一项都会给出删除命令。"
fi
echo ""

if [ "$DRY" -eq 0 ] && [ "$YES" -eq 0 ]; then
    if [ ! -t 0 ]; then
        echo "refusing to uninstall without confirmation (no terminal attached)." >&2
        echo "re-run interactively, or use --yes; preview first with --dry-run." >&2
        exit 2
    fi
    printf "Proceed? 确认卸载？ [y/N] "
    read -r reply
    case "$reply" in
        y|Y|yes|YES) ;;
        *) echo "aborted — nothing changed. 已取消，未做任何改动。"; exit 0 ;;
    esac
    echo ""
fi

# --------------------------------------------------------------------------
echo "==> 1. launchd agents"
if command -v launchctl >/dev/null 2>&1; then
    UID_NUM="$(id -u)"
    found_any=0
    for plist in "$LA_DIR"/com.zelin.aiassistant.*.plist; do
        [ -e "$plist" ] || continue
        found_any=1
        label="$(basename "$plist" .plist)"
        if [ "$DRY" -eq 1 ]; then
            plan "unload + delete agent: $label"
            continue
        fi
        launchctl bootout "gui/$UID_NUM/$label" >/dev/null 2>&1 \
            || launchctl unload "$plist" >/dev/null 2>&1 || true
        rm -f "$plist"
        ok "unloaded + deleted agent: $label"
    done
    [ "$found_any" -eq 0 ] && info "no com.zelin.aiassistant.* agents installed"
else
    info "launchctl not available — skipped (not macOS?)"
fi

# --------------------------------------------------------------------------
echo ""
echo "==> 2. crontab lines (only ours — marker tokens: screenpipe-export.sh /"
echo "       act.radar / act.digest / act.analytics_sync / zelin-ai-assistant)"
if command -v crontab >/dev/null 2>&1; then
    CURRENT_CRON="$(crontab -l 2>/dev/null || true)"
    OURS="$(printf '%s\n' "$CURRENT_CRON" | grep -E "$CRON_MARKERS" || true)"
    if [ -z "$OURS" ]; then
        info "no matching cron lines found"
    else
        printf '%s\n' "$OURS" | while IFS= read -r line; do
            [ -n "$line" ] || continue
            if [ "$DRY" -eq 1 ]; then plan "remove cron line: $line"
            else info "removing cron line: $line"; fi
        done
        if [ "$DRY" -eq 0 ]; then
            KEPT="$(printf '%s\n' "$CURRENT_CRON" | grep -Ev "$CRON_MARKERS" || true)"
            if [ -z "$(printf '%s' "$KEPT" | tr -d '[:space:]')" ]; then
                crontab -r 2>/dev/null || true
                ok "crontab now empty — removed"
            elif printf '%s\n' "$KEPT" | crontab -; then
                ok "crontab rewritten (all other lines preserved)"
            else
                warn "crontab rewrite failed — remove the lines above manually: crontab -e"
            fi
        fi
    fi
else
    info "crontab not available — skipped"
fi

# --------------------------------------------------------------------------
echo ""
echo "==> 3. running processes"
if [ "$DRY" -eq 1 ]; then
    plan "quit the menu-bar app ($APP_NAME)"
    plan "stop the screenpipe recording engine (if running)"
else
    if command -v osascript >/dev/null 2>&1; then
        osascript -e "tell application id \"$APP_BUNDLE_ID\" to quit" >/dev/null 2>&1 || true
    fi
    pkill -x ZelinAIEngineer 2>/dev/null || true
    ok "menu-bar app asked to quit"
    # char-class avoids pgrep/pkill matching itself (HANDOFF §3)
    if pkill -f "screenpipe.*[r]ecord" 2>/dev/null; then
        ok "screenpipe recording engine stopped"
    else
        info "screenpipe recording engine not running"
    fi
fi

# --------------------------------------------------------------------------
echo ""
echo "==> 4. app bundle(s)"
remove_path "/Applications/$APP_NAME.app"
remove_path "$HOME/Applications/$APP_NAME.app"

# --------------------------------------------------------------------------
echo ""
echo "==> 5. pipeline master copy (root-owned, installed by the .pkg)"
remove_path "$PIPELINE_MASTER"
if [ -e "$PIPELINE_MASTER" ] || [ "$DRY" -eq 1 ]; then
    info "pkg receipts (optional): sudo pkgutil --forget com.zelin.aiassistant.app; sudo pkgutil --forget com.zelin.aiassistant.pipeline"
fi

# --------------------------------------------------------------------------
if [ "$PURGE" -eq 1 ]; then
    echo ""
    echo "==> 6. purge user data (--purge)"
    if [ -d "$HOME/.screenpipe" ]; then
        SIZE="$(du -sh "$HOME/.screenpipe" 2>/dev/null | cut -f1)"
        info "screen recordings in $HOME/.screenpipe: ${SIZE:-?}"
    fi
    remove_path "$REPO_ROOT/state"
    remove_path "$REPO_ROOT/config.yaml"
    remove_path "$REPO_ROOT/config/secrets"
    remove_path "$REPO_ROOT/config/runtime.json"
    remove_path "$REPO_ROOT/config/redaction_terms.txt"
    remove_path "$POINTER_DIR"
    remove_path "$HOME/.screenpipe"
fi

# --------------------------------------------------------------------------
echo ""
echo "=============================================="
if [ "$DRY" -eq 1 ]; then
    echo " Dry run complete — nothing was changed. 预览结束，未做任何改动。"
    echo " Run again without --dry-run to uninstall."
elif [ "$PURGE" -eq 1 ]; then
    echo " Uninstall + purge complete. 卸载完成（含用户数据）。"
    echo " Kept: your Obsidian vault (never touched)."
    echo " Last step (this script cannot delete the directory it runs from):"
    echo "   rm -rf \"$REPO_ROOT\""
else
    echo " Uninstall complete. 卸载完成。Background services are gone;"
    echo " nothing records and nothing spends API credit anymore."
    echo ""
    echo " Kept — yours to keep or delete 以下数据已保留（附删除命令）:"
    echo "   - task history + settings:  rm -rf \"$REPO_ROOT/state\""
    echo "   - API keys / credentials:   rm -rf \"$REPO_ROOT/config/secrets\""
    echo "   - screen recordings:        rm -rf \"$HOME/.screenpipe\""
    echo "   - this repo working copy:   rm -rf \"$REPO_ROOT\""
    echo "   - your Obsidian vault:      untouched, always"
    echo " (or re-run with --purge to remove all of the above except the vault)"
fi
echo "=============================================="
exit 0
