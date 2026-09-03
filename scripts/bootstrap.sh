#!/bin/bash
# Zelin's AI Assistant — one-command fresh-machine bootstrap (CONTRACT §69).
#
#   curl -fsSL https://raw.githubusercontent.com/Wan-ZL/zelin-ai-assistant/main/scripts/bootstrap.sh | bash
#   curl -fsSL …/scripts/bootstrap.sh | bash -s -- ~/Code/zelin-ai-assistant   # custom checkout dir
#   bash scripts/bootstrap.sh [--dir PATH] [--ref BRANCH] [--no-open] [--no-launchd] [PATH]
#
# Owner's acceptance criterion (vnext2-plan, 2026-09-02): "on another computer
# I can start from a blank environment — or update this software on another
# computer — and use it directly." This script is that command, and it is the
# SAME command for both cases: a re-run on a machine that already has the
# checkout fast-forwards it and re-runs the installer (idempotent = update).
#
# What it does, in order (each step prints ok / warn / ERR):
#   1. preflight — macOS only (Linux: install-linux.sh, Windows: install.ps1);
#      Xcode Command Line Tools (git, swiftc, /usr/bin/python3 come with them —
#      checked with `xcode-select -p`, which never pops the install dialog);
#      git; python3.
#   2. checkout dir — default ~/Projects/zelin-ai-assistant, or the PATH arg /
#      --dir / $ZAI_BOOTSTRAP_DIR. A dir outside $HOME (external volume, network
#      share) gets a loud TCC warning (§55: macOS grants file access PER BINARY;
#      launchd-spawned daemons and claude will need Full Disk Access there).
#   3. clone (first run) or fetch + fast-forward (re-run). An existing non-git
#      dir, a checkout of some other repo, or local edits are refused / left
#      alone — this script never destroys anything it did not create.
#   4. config.yaml from config.example.yaml when absent (never overwritten).
#   5. bash install.sh --non-interactive [--no-launchd] — never prompts; the
#      exit code is the number of failed steps (CONTRACT §23/§56).
#   6. python3 -m act.doctor --fresh-install — the buckets: what the installer
#      wired, what still needs YOU (Full Disk Access for the daemon interpreter
#      and claude — with paths — the API key, opening the board), what is broken.
#   7. open the Board app (skipped with --no-open / $ZAI_BOOTSTRAP_NO_OPEN=1, or
#      when no bundle was built — the board is then at http://127.0.0.1:<port>/).
#
# Exit code: 0 = installed, nothing broken (TCC grants / credentials may still
# be pending — those are listed, they are the human's); otherwise the first
# non-zero of: preflight (2–5), install.sh failed steps, doctor broken rows.
#
# Test seams (tests/integration/test_bootstrap_script.py): every external tool
# is resolved through PATH (a fake git/xcode-select/uname/open can front it),
# ZAI_BOOTSTRAP_REPO_URL points the clone anywhere (a local bare repo in CI),
# stdin is /dev/null for every child (the script itself may be arriving on
# stdin through the curl pipe — a child reading stdin would eat the script).
set -uo pipefail

REPO_URL="${ZAI_BOOTSTRAP_REPO_URL:-https://github.com/Wan-ZL/zelin-ai-assistant.git}"
DEFAULT_DIR="$HOME/Projects/zelin-ai-assistant"
BOARD_BUNDLE="Zelin's AI Assistant.app"   # §54 bundle folder (install.sh UI_APP_NAME; id com.zelin.ai-board)
BOARD_BUNDLE_ID="com.zelin.ai-board"

ok()   { printf '  [ ok ] %s\n' "$1"; }
warn() { printf '  [warn] %s\n' "$1"; }
info() { printf '  [info] %s\n' "$1"; }
err()  { printf '  [ERR ] %s\n' "$1" >&2; }

usage() {
    cat <<'EOF'
usage: bootstrap.sh [--dir PATH] [--ref BRANCH] [--no-open] [--no-launchd] [PATH]
  --dir PATH      checkout directory (default ~/Projects/zelin-ai-assistant; env ZAI_BOOTSTRAP_DIR)
  --ref BRANCH    branch or tag to install (default main; env ZAI_BOOTSTRAP_REF)
  --no-open       do not open the Board app at the end (env ZAI_BOOTSTRAP_NO_OPEN=1)
  --no-launchd    install.sh --no-launchd: wire no scheduler, run one actd pass (CI / dry run)
EOF
}

# Physical path of an existing directory (install.sh physical_path twin, §55).
physical_path() {
    ( cd "$1" 2>/dev/null && pwd -P ) || printf '%s' "$1"
}

# Is $1 (physical) outside the physical $HOME? 0 = outside (TCC-sensitive).
outside_home() {
    _h="$(physical_path "$HOME")"
    case "$1" in "$_h"|"$_h"/*) return 1 ;; *) return 0 ;; esac
}

# ---------------------------------------------------------------- 1. preflight
preflight() {
    echo "==> 1. preflight"
    _os="$(uname -s 2>/dev/null || echo unknown)"
    if [ "$_os" != "Darwin" ]; then
        err "this bootstrap is for macOS (found: $_os). Linux: bash install-linux.sh · Windows: install.ps1 (docs/LINUX.md, docs/WINDOWS.md)"
        return 2
    fi
    _ver="$(sw_vers -productVersion 2>/dev/null || echo unknown)"
    case "$_ver" in
        1[0-3].*) warn "macOS $_ver — the product targets macOS 14+ (launchd/TCC behavior verified there)" ;;
        *) ok "macOS $_ver" ;;
    esac
    if ! xcode-select -p >/dev/null 2>&1; then
        err "Xcode Command Line Tools are not installed — they bring git, swiftc and /usr/bin/python3."
        err "run:  xcode-select --install   (accept the dialog, wait for it to finish), then re-run this command"
        return 3
    fi
    ok "Xcode Command Line Tools: $(xcode-select -p 2>/dev/null)"
    if ! command -v git >/dev/null 2>&1; then
        err "git not found — xcode-select --install provides it"
        return 4
    fi
    ok "git: $(command -v git)"
    if ! command -v python3 >/dev/null 2>&1; then
        err "python3 not found — xcode-select --install provides /usr/bin/python3"
        return 5
    fi
    ok "python3: $(command -v python3)"
    if command -v claude >/dev/null 2>&1; then
        ok "claude: $(command -v claude)"
    else
        warn "claude CLI not found — install.sh continues; the daemons idle until Claude Code is installed (the summary at the end lists it)"
    fi
    return 0
}

# ------------------------------------------------------------ 2. checkout dir
announce_dir() {
    echo ""
    echo "==> 2. checkout dir: $TARGET_DIR"
    _parent="$(dirname "$TARGET_DIR")"
    mkdir -p "$_parent" 2>/dev/null || { err "cannot create $_parent"; return 6; }
    _phys="$(physical_path "$_parent")/$(basename "$TARGET_DIR")"
    [ -d "$TARGET_DIR" ] && _phys="$(physical_path "$TARGET_DIR")"
    if outside_home "$_phys"; then
        warn "$_phys is OUTSIDE your home folder (external volume / network share / protected location)."
        warn "macOS grants file access PER BINARY (TCC, CONTRACT §55): the daemon interpreter and claude"
        warn "will each need Full Disk Access before anything launchd starts can read this checkout."
        warn "A path under \$HOME (default ~/Projects/zelin-ai-assistant) needs none of that."
    else
        ok "inside \$HOME — no Full Disk Access needed for the checkout itself"
    fi
    return 0
}

# ------------------------------------------------------- 3. clone or update
origin_matches() { # is $1 a checkout of our repo (or of $REPO_URL)?
    _url="$(git -C "$1" remote get-url origin 2>/dev/null </dev/null || true)"
    [ -n "$_url" ] || return 1
    case "$_url" in
        "$REPO_URL"|"${REPO_URL%.git}"|"$REPO_URL.git"|*zelin-ai-assistant*) return 0 ;;
        *) return 1 ;;
    esac
}

update_checkout() {
    if ! origin_matches "$TARGET_DIR"; then
        err "$TARGET_DIR is a git checkout of something else (origin: $(git -C "$TARGET_DIR" remote get-url origin 2>/dev/null </dev/null || echo none)) — pick another --dir"
        return 7
    fi
    if [ -n "$(git -C "$TARGET_DIR" status --porcelain --untracked-files=no 2>/dev/null </dev/null)" ]; then
        warn "local edits in $TARGET_DIR — not updating the code (installing what is there); commit or stash them to update"
        UPDATED="local-edits"
        return 0
    fi
    if ! git -C "$TARGET_DIR" fetch --tags --force origin </dev/null; then
        warn "git fetch failed (offline?) — installing the checkout as it is"
        UPDATED="offline"
        return 0
    fi
    if ! git -C "$TARGET_DIR" checkout -q "$REF" </dev/null; then
        err "git checkout $REF failed in $TARGET_DIR"
        return 8
    fi
    # a branch fast-forwards to origin; a tag / detached ref is already exact
    if git -C "$TARGET_DIR" show-ref --verify -q "refs/remotes/origin/$REF" </dev/null; then
        if ! git -C "$TARGET_DIR" merge -q --ff-only "origin/$REF" </dev/null; then
            err "$TARGET_DIR has diverged from origin/$REF — resolve by hand (git -C '$TARGET_DIR' status)"
            return 9
        fi
    fi
    UPDATED="updated"
    ok "updated to $REF @ $(git -C "$TARGET_DIR" rev-parse --short HEAD 2>/dev/null </dev/null || echo '?')"
    return 0
}

clone_checkout() {
    if [ -e "$TARGET_DIR" ] && [ -n "$(ls -A "$TARGET_DIR" 2>/dev/null)" ]; then
        err "$TARGET_DIR exists and is not a git checkout — move it away or pick another --dir"
        return 7
    fi
    if ! git clone --branch "$REF" "$REPO_URL" "$TARGET_DIR" </dev/null; then
        err "git clone failed ($REPO_URL, ref $REF) — check the network / the ref name"
        return 8
    fi
    UPDATED="cloned"
    ok "cloned $REPO_URL ($REF) @ $(git -C "$TARGET_DIR" rev-parse --short HEAD 2>/dev/null </dev/null || echo '?')"
    return 0
}

fetch_code() {
    echo ""
    echo "==> 3. clone or update ($REPO_URL, ref $REF)"
    if [ -d "$TARGET_DIR/.git" ] || [ -f "$TARGET_DIR/.git" ]; then
        update_checkout
    else
        clone_checkout
    fi
}

# ------------------------------------------------------------- 4. config.yaml
ensure_config() {
    echo ""
    echo "==> 4. config.yaml"
    if [ -f "$TARGET_DIR/config.yaml" ]; then
        ok "config.yaml exists — left untouched"
    elif [ -f "$TARGET_DIR/config.example.yaml" ]; then
        cp "$TARGET_DIR/config.example.yaml" "$TARGET_DIR/config.yaml"
        ok "config.yaml created from config.example.yaml (defaults; edit later or use the board's Settings)"
    else
        warn "no config.example.yaml in the checkout — install.sh will report it"
    fi
}

# ---------------------------------------------------------------- 5. install
run_install() {
    echo ""
    echo "==> 5. bash install.sh --non-interactive${NO_LAUNCHD:+ --no-launchd}"
    if [ ! -f "$TARGET_DIR/install.sh" ]; then
        err "$TARGET_DIR/install.sh missing — not a usable checkout"
        return 10
    fi
    # shellcheck disable=SC2086 # NO_LAUNCHD is either empty or the literal flag
    bash "$TARGET_DIR/install.sh" --non-interactive $NO_LAUNCHD </dev/null
    INSTALL_RC=$?
    if [ "$INSTALL_RC" -eq 0 ]; then
        ok "install.sh: every step ok"
    else
        warn "install.sh reported $INSTALL_RC failed step(s) — see above and state/install_report.json"
    fi
    return 0
}

# --------------------------------------------------------- 6. what is left
runtime_python() { # the §19 pin install.sh just wrote, else PATH python3
    _p="$(sed -n 's/.*"python"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' \
        "$TARGET_DIR/config/runtime.json" 2>/dev/null)"
    if [ -n "$_p" ] && [ -x "$_p" ]; then printf '%s' "$_p"; else command -v python3; fi
}

fresh_summary() {
    echo ""
    echo "==> 6. what is left (python3 -m act.doctor --fresh-install)"
    _py="$(runtime_python)"
    (cd "$TARGET_DIR" && AIASSISTANT_HOME="$TARGET_DIR" "$_py" -m act.doctor --fresh-install </dev/null)
    DOCTOR_RC=$?
    [ "$DOCTOR_RC" -eq 0 ] || warn "doctor found $DOCTOR_RC broken row(s) — fix those first"
    return 0
}

# ------------------------------------------------------------ 7. open board
# Is this bundle the board shell? install.sh tells bundles apart by
# CFBundleIdentifier, never by folder name (§54 name swap: the frozen legacy
# app used to own this folder name). Unreadable plist = cannot tell = accept.
bundle_is_shell() {
    _pl="$1/Contents/Info.plist"
    [ -f "$_pl" ] || return 0                 # nothing to judge by — accept
    _id="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$_pl" 2>/dev/null)" || return 0
    [ "$_id" = "$BOARD_BUNDLE_ID" ]
}

board_bundle() {
    # same seam install.sh's ui step honors (AIASSISTANT_UI_APPS_DIR), so a
    # redirected install is found where it went; ~/Applications is its fallback
    for _b in "${AIASSISTANT_UI_APPS_DIR:-/Applications}/$BOARD_BUNDLE" "$HOME/Applications/$BOARD_BUNDLE"; do
        [ -d "$_b" ] && bundle_is_shell "$_b" && { printf '%s' "$_b"; return 0; }
    done
    return 1
}

open_board() {
    echo ""
    echo "==> 7. open the board"
    if [ "$NO_OPEN" -eq 1 ]; then
        info "not opening the board (--no-open)"
        return 0
    fi
    _b="$(board_bundle || true)"
    if [ -n "$_b" ]; then
        if open "$_b" </dev/null >/dev/null 2>&1; then
            ok "opened \"$_b\" — its first-run wizard (?page=setup) takes over until config and credentials are in place"
        else
            warn "could not open \"$_b\" — open it from /Applications"
        fi
    else
        warn "no board app bundle was built (swiftc missing? or the product folder still holds the legacy app) — open http://127.0.0.1:47820/ in a browser once the board server runs; xcode-select --install + bash install.sh builds the Dock app"
    fi
}

main() {
    TARGET_DIR="${ZAI_BOOTSTRAP_DIR:-}"
    REF="${ZAI_BOOTSTRAP_REF:-main}"
    NO_OPEN=0; [ "${ZAI_BOOTSTRAP_NO_OPEN:-0}" = "1" ] && NO_OPEN=1
    NO_LAUNCHD=""
    UPDATED=""; INSTALL_RC=0; DOCTOR_RC=0
    while [ $# -gt 0 ]; do
        case "$1" in
            --dir) shift; TARGET_DIR="${1:-}" ;;
            --dir=*) TARGET_DIR="${1#--dir=}" ;;
            --ref) shift; REF="${1:-}" ;;
            --ref=*) REF="${1#--ref=}" ;;
            --no-open) NO_OPEN=1 ;;
            --no-launchd) NO_LAUNCHD="--no-launchd" ;;
            -h|--help) usage; return 0 ;;
            -*) err "unknown flag: $1"; usage >&2; return 2 ;;
            *) TARGET_DIR="$1" ;;
        esac
        shift
    done
    [ -n "$TARGET_DIR" ] || TARGET_DIR="$DEFAULT_DIR"
    # a literal leading tilde (quoted through `bash -s -- '~/x'`) means $HOME
    # shellcheck disable=SC2088 # that literal is exactly what we are matching
    case "$TARGET_DIR" in "~"|"~/"*) TARGET_DIR="$HOME${TARGET_DIR#\~}" ;; esac
    [ -n "$REF" ] || { err "--ref needs a value"; return 2; }

    echo "=============================================="
    echo " Zelin's AI Assistant — bootstrap"
    echo "=============================================="
    preflight || return $?
    announce_dir || return $?
    fetch_code || return $?
    ensure_config
    run_install || return $?
    fresh_summary
    open_board
    echo ""
    echo "=============================================="
    echo " bootstrap done ($UPDATED): $TARGET_DIR"
    echo " re-run this same command any time to update; bash $TARGET_DIR/install.sh --check for diagnostics"
    echo "=============================================="
    [ "$INSTALL_RC" -ne 0 ] && return "$INSTALL_RC"
    return "$DOCTOR_RC"
}

main "$@"
