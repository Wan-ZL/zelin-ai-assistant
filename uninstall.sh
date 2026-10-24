#!/bin/bash
# Clean uninstall for Zelin's AI Assistant lite 2.0 — the inverse of install.sh
# (CONTRACT §78: every resident piece of the closed footprint goes, item by item).
#
# What it removes by default (in order, each line printed):
#   1. LaunchAgents: com.zelin.aiassistant.* (the server; any 1.x agent left
#      over) and com.zelin.screenpipe-* (the retired 1.x ingest / prune agents)
#   2. our crontab lines only — matched by the marker tokens below; every other
#      line is kept verbatim
#   3. the shell app is asked to quit. The screenpipe recording engine keeps
#      recording unless you pass --stop-engine (the engineHandoff preference is
#      written first so the quitting app leaves it alone)
#   4. the app bundle(s) in /Applications and ~/Applications, matched by bundle id
#   5. ~/Library/Logs/zelin-ai-assistant, ~/Library/Caches/zelin-ai-assistant,
#      Application Support's bin/ (the stable claude copy), claude-config/ (the
#      private Claude Code config dir of the headless runs) and deploy_state.json,
#      the ingest lock files and /tmp/screenpipe-auto.log
#   6. prints what only you can do: tccutil reset for com.zelin.ai-board, the
#      login item check, the 1.x headless transcript cleanup command
#
# What it KEEPS by default (printed with the exact removal command):
#   this repo working copy incl. state/ (recaps), config.yaml, config/secrets/;
#   your Obsidian vault (never touched, not even with --purge); ~/.screenpipe.
#
# Flags:
#   --dry-run      print the full plan, change nothing, exit 0
#   --yes          skip the confirmation prompt (scripted use)
#   --stop-engine  also stop the screenpipe recording engine
#   --purge        ALSO delete user data: state/, config.yaml, config/secrets/,
#                  config/runtime.json, redaction terms, the Application Support
#                  dir and ~/.screenpipe. The vault is still never touched; the
#                  repo directory is left for a final manual `rm -rf`.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$SCRIPT_DIR"
LA_DIR="$HOME/Library/LaunchAgents"
SHELL_APP_NAME="Zelin's AI Assistant"   # CONTRACT §54 — the Dock shell (= the product)
SHELL_BUNDLE_ID="com.zelin.ai-board"
SHELL_EXEC_NAME="ZelinAIBoard"
ENGINE_HANDOFF_KEY="engineHandoff"      # = shell EngineOwnership.handoffKey (§61.8)
LEGACY_APP_NAME="Zelin's AI Assistant (old)"   # the 1.x menu-bar app after the §54 swap
LEGACY_BUNDLE_ID="com.zelin.ai-engineer"
PREVIOUS_SHELL_APP_NAME="Zelin AI Board"       # the shell's folder before the swap
APP_SUPPORT_DIR="$HOME/Library/Application Support/ZelinAIAssistant"
CLAUDE_CONFIG_DIR_PATH="${AIASSISTANT_CLAUDE_CONFIG_DIR:-$APP_SUPPORT_DIR/claude-config}"
ENGINE_PATTERN='screenpipe.*[r]ecord'   # char class: pgrep/pkill never match themselves

# cron marker tokens (CONTRACT §18) — a crontab line is OURS iff it matches one.
CRON_MARKERS='screenpipe-export\.sh|act\.radar|act\.digest|act\.weekly_digest|act\.analytics_sync|zelin-ai-assistant'

DRY=0; YES=0; PURGE=0; STOP_ENGINE=0
for arg in "$@"; do
    case "$arg" in
        --dry-run)     DRY=1 ;;
        --yes)         YES=1 ;;
        --purge)       PURGE=1 ;;
        --stop-engine) STOP_ENGINE=1 ;;
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

# remove a path; on permission failure print the exact sudo command instead.
remove_path() {
    p="$1"
    [ -e "$p" ] || [ -L "$p" ] || { info "not present (nothing to do): $p"; return 0; }
    if [ "$DRY" -eq 1 ]; then plan "remove: $p"; return 0; fi
    if rm -rf "$p" 2>/dev/null; then
        ok "removed: $p"
    else
        warn "could not remove (owned by root?): $p"
        info "run: sudo rm -rf \"$p\""
    fi
}

# An ingest PID lock goes only when its process is gone (a run in flight keeps it).
remove_lock() {
    _pid="$(tr -cd '0-9' < "$1" 2>/dev/null)"
    if [ -n "$_pid" ] && kill -0 "$_pid" 2>/dev/null; then
        info "in use by pid $_pid, left: $1"
        return 0
    fi
    remove_path "$1"
}

echo "=============================================="
echo " Zelin's AI Assistant — uninstall 卸载"
echo " repo: $REPO_ROOT"
[ "$DRY" -eq 1 ]   && echo " MODE: dry-run — printing the plan, changing nothing 只预览，不改动"
[ "$PURGE" -eq 1 ] && echo " MODE: purge — user data (state/, config, secrets, recordings) will be deleted too"
echo "=============================================="
echo ""
echo "This removes the board server agent, our crontab line, the app, its logs,"
echo "caches and the private claude config dir."
if [ "$STOP_ENGINE" -eq 1 ]; then
    echo "--stop-engine: the screenpipe recording engine is stopped too."
else
    echo "The screenpipe recording engine keeps running (pass --stop-engine to stop it)."
fi
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
echo "==> 1. launchd agents (com.zelin.aiassistant.*, com.zelin.screenpipe-*)"
HAVE_LAUNCHCTL=0
command -v launchctl >/dev/null 2>&1 && HAVE_LAUNCHCTL=1
UID_NUM="$(id -u)"
found_any=0
for plist in "$LA_DIR"/com.zelin.aiassistant.*.plist "$LA_DIR"/com.zelin.screenpipe-*.plist; do
    [ -e "$plist" ] || continue
    found_any=1
    label="$(basename "$plist" .plist)"
    if [ "$DRY" -eq 1 ]; then
        plan "unload + delete agent: $label"
        continue
    fi
    if [ "$HAVE_LAUNCHCTL" -eq 1 ]; then
        launchctl bootout "gui/$UID_NUM/$label" >/dev/null 2>&1 \
            || launchctl unload "$plist" >/dev/null 2>&1 || true
    fi
    rm -f "$plist"
    ok "unloaded + deleted agent: $label"
done
[ "$found_any" -eq 0 ] && info "no agents of ours installed"

# --------------------------------------------------------------------------
echo ""
echo "==> 2. crontab lines (only ours — marker tokens: screenpipe-export.sh /"
echo "       act.radar / act.digest / act.weekly_digest / act.analytics_sync / zelin-ai-assistant)"
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
    plan "ask the shell app ($SHELL_APP_NAME) to quit"
    if [ "$STOP_ENGINE" -eq 1 ]; then
        plan "stop the screenpipe recording engine (if running)"
    else
        plan "leave the screenpipe recording engine running (no --stop-engine)"
    fi
else
    if [ "$STOP_ENGINE" -eq 0 ]; then
        # §61.8: with the handoff key set the quitting shell leaves the engine it spawned
        defaults write "$SHELL_BUNDLE_ID" "$ENGINE_HANDOFF_KEY" -string "$(date -u +%FT%TZ)" >/dev/null 2>&1 || true
    fi
    pkill -TERM -x "$SHELL_EXEC_NAME" 2>/dev/null || true
    ok "shell app asked to quit"
    if pgrep -x ZelinAIEngineer >/dev/null 2>&1; then
        warn "the 1.x menu-bar app (ZelinAIEngineer) is still running — quit it from its menu"
    fi
    if [ "$STOP_ENGINE" -eq 1 ]; then
        if pkill -f "$ENGINE_PATTERN" 2>/dev/null; then
            ok "screenpipe recording engine stopped"
        else
            info "screenpipe recording engine not running"
        fi
    elif pgrep -f "$ENGINE_PATTERN" >/dev/null 2>&1; then
        info "screenpipe recording engine left running; stop it with: pkill -f '$ENGINE_PATTERN'"
    fi
fi

# --------------------------------------------------------------------------
echo ""
echo "==> 4. app bundle(s)"
# remove_app <path> <accepted bundle id>...: the id decides, never the folder name.
remove_app() {
    p="$1"; shift
    [ -e "$p" ] || { info "not present (nothing to do): $p"; return 0; }
    have="$(plutil -extract CFBundleIdentifier raw "$p/Contents/Info.plist" 2>/dev/null || true)"
    for want in "$@"; do
        if [ "$have" = "$want" ]; then remove_path "$p"; return 0; fi
    done
    info "left alone (bundle id ${have:-unreadable} is not ours): $p"
}
# AIASSISTANT_UI_APPS_DIR: the same test seam install.sh honours (default /Applications).
for _dir in "${AIASSISTANT_UI_APPS_DIR:-/Applications}" "$HOME/Applications"; do
    remove_app "$_dir/$SHELL_APP_NAME.app" "$SHELL_BUNDLE_ID" "$LEGACY_BUNDLE_ID"
    remove_app "$_dir/$LEGACY_APP_NAME.app" "$LEGACY_BUNDLE_ID"
    remove_app "$_dir/$PREVIOUS_SHELL_APP_NAME.app" "$SHELL_BUNDLE_ID"
    for p in "$_dir/$LEGACY_APP_NAME "*.app; do
        [ -e "$p" ] && remove_app "$p" "$LEGACY_BUNDLE_ID"
    done
done

# --------------------------------------------------------------------------
echo ""
echo "==> 5. logs, caches, the private claude config dir, lock files"
remove_path "$HOME/Library/Logs/zelin-ai-assistant"
remove_path "$HOME/Library/Caches/zelin-ai-assistant"
remove_path "$APP_SUPPORT_DIR/bin"
remove_path "$CLAUDE_CONFIG_DIR_PATH"
remove_path "$APP_SUPPORT_DIR/deploy_state.json"
remove_lock "${PROCESS_SCREENPIPE_LOCK:-/tmp/process-screenpipe.lock}"
remove_lock "${ZAI_SCREENPIPE_EXPORT_LOCK:-/tmp/screenpipe-export.lock}"
remove_path "${PROCESS_SCREENPIPE_LOG:-/tmp/screenpipe-auto.log}"

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
    remove_path "$APP_SUPPORT_DIR"
    remove_path "$HOME/.screenpipe"
fi

# --------------------------------------------------------------------------
slug() { printf '%s' "$1" | sed 's/[^A-Za-z0-9]/-/g'; }
echo ""
echo "==> what only you can do (printed, never run)"
info "privacy grants:   tccutil reset All $SHELL_BUNDLE_ID"
info "login item:       System Settings > General > Login Items — remove \"$SHELL_APP_NAME\" if it is still listed"
info "1.x transcripts:  rm -rf ~/.claude/projects/$(slug "$REPO_ROOT/state")*   (archive them first if you want them)"

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
    echo " Uninstall complete. 卸载完成。Nothing of ours runs in the background now"
    echo " (the recording engine too, if you passed --stop-engine)."
    echo ""
    echo " Kept — yours to keep or delete 以下数据已保留（附删除命令）:"
    echo "   - recaps + settings:        rm -rf \"$REPO_ROOT/state\""
    echo "   - API key:                  rm -rf \"$REPO_ROOT/config/secrets\""
    echo "   - screen recordings:        rm -rf \"$HOME/.screenpipe\""
    echo "   - this repo working copy:   rm -rf \"$REPO_ROOT\""
    echo "   - your Obsidian vault:      untouched, always"
    echo " (or re-run with --purge to remove all of the above except the vault)"
fi
echo "=============================================="
exit 0
