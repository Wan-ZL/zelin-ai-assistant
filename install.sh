#!/bin/bash
# Installer for Zelin's AI Assistant lite 2.0 (recording -> ingest -> meeting recaps).
#
# Steps (each records one or more CONTRACT §23 report lines):
#   1. dependency checks — claude, swift toolchain, python3 + PyYAML, node/npx
#   2. config.yaml from config.example.yaml (never overwritten) + redaction terms;
#      the version stamp (act/_version.py from the git tag, §56.1); the daemon
#      interpreter pin (config/runtime.json, §19/§55); the home pointer; the
#      stable claude copy (§55 第五幕); the §78 isolation dirs
#      (claude-config/ + claude-config/cwd/); the Anthropic API key file (§19)
#   3. state/ directories
#   4. migrate_from_1x (§78): boot out + delete the 1.x LaunchAgents, unlink
#      ~/.claude/skills symlinks that point into this repo, remove the 1.x
#      deploy_state.json, print (never run) the command that clears the 1.x
#      headless transcripts. Retired cron lines go in step 7.
#   5. the board UI: web/dist + the Dock shell app (§56.5 `ui` step)
#   6. the ONE LaunchAgent: com.zelin.aiassistant.server (§78 closed footprint)
#   7. crontab: the */30 ingest chain is the only line of ours (§18); every
#      other user line is kept verbatim
#   8. post-install doctor (interactive runs only)
# The run ends by writing state/install_report.json (§23). The screenpipe
# engine is never signalled: a running engine is left recording and the shell
# adopts it (§61.8).
#
# Usage:
#   bash install.sh                       interactive install / re-install
#   bash install.sh --non-interactive     never prompts; exit code = failed steps
#   bash install.sh --no-launchd          wire no scheduler (no agent, no crontab;
#                                         CI acceptance and bootstrap dry runs)
#   bash install.sh --from-upgrade        what scripts/upgrade.sh runs (§79): implies
#                                         --non-interactive; the shell relaunch is
#                                         left to the upgrader
#   bash install.sh --check [doctor flags]  run python -m act.doctor, change nothing
# Flags combine; an unknown flag exits 2 before any side effect.
#
# Test seams: AIASSISTANT_UI_APPS_DIR, AIASSISTANT_UI_BUILD_DIR,
# AIASSISTANT_UI_BUDGET, AIASSISTANT_STABLE_CLAUDE, AIASSISTANT_CLAUDE_CONFIG_DIR,
# AIASSISTANT_LAUNCHD_PROBE=0 (skip the launchd viability probe). The script can
# be sourced (functions only; main runs when executed).
set -uo pipefail

# Physical path of a directory, every symlink resolved (CONTRACT §55).
physical_path() {
    ( cd "$1" 2>/dev/null && pwd -P ) || printf '%s' "$1"
}

# `pwd -P`: a convenience symlink in the invocation path (~/Projects ->
# /Volumes/…) must never be baked into PYTHONPATH / AIASSISTANT_HOME (§55).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
SCRIPT_DIR_LOGICAL="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$SCRIPT_DIR"
LA_DIR="$HOME/Library/LaunchAgents"
UID_NUM="$(id -u)"
APP_SUPPORT_DIR="$HOME/Library/Application Support/ZelinAIAssistant"
LOG_DIR="$HOME/Library/Logs/zelin-ai-assistant"
SERVER_LABEL="com.zelin.aiassistant.server"
# §78 constants — same literals as act/lib/config.py and ingest/claude-env.sh.
CLAUDE_CONFIG_DIR_PATH="${AIASSISTANT_CLAUDE_CONFIG_DIR:-$APP_SUPPORT_DIR/claude-config}"
STABLE_CLAUDE_BIN="${AIASSISTANT_STABLE_CLAUDE:-$HOME/Library/Application Support/ZelinAIAssistant/bin/claude}"
# The 1.x LaunchAgents migrate_from_1x retires (§78).
RETIRED_LABELS="com.zelin.aiassistant.actd com.zelin.aiassistant.autodeploy com.zelin.aiassistant.gmailradar com.zelin.aiassistant.slackradar com.zelin.aiassistant.syncd com.zelin.aiassistant.weeklydigest com.zelin.aiassistant.imessageradar com.zelin.aiassistant.radar com.zelin.screenpipe-process com.zelin.screenpipe-prune"
# Uncommented crontab lines of ours: the ingest chain (keyed on its first script)
# and the 1.x module lines. Everything else in the user's crontab is kept.
CRON_OURS_RE='^[^#]*(screenpipe-export\.sh|act\.radar|act\.digest|act\.weekly_digest|act\.analytics_sync)'

# Mode flags (main sets them; defaults keep the functions usable when sourced).
NON_INTERACTIVE=0
NO_LAUNCHD=0
FROM_UPGRADE=0

ok()   { printf "  [ ok ] %s\n" "$1"; }
warn() { printf "  [warn] %s\n" "$1"; }
info() { printf "  [info] %s\n" "$1"; }

# §23 report lines, newline-separated "name=status[:detail]" (a plain string:
# macOS bash 3.2 + set -u make empty arrays hazardous).
REPORT_STEPS=""
LOADED_LABELS=""
report_step() { # $1=name $2=status [$3=detail]
    REPORT_STEPS="${REPORT_STEPS}$1=$2${3:+:$3}
"
}

# --non-interactive verdict: the report lines whose status is fail, one per line;
# the exit code is their count. skipped / skipped_tcc / warn never count (§56.5:
# environment problems are not something a rollback or a retry fixes).
failed_deploy_steps() {
    printf '%s' "$REPORT_STEPS" | grep -E '^[^=]+=fail' || true
}

# ------------------------------------------------------------------ interpreters
# §19/§55: a daemon interpreter is an ABSOLUTE path that can `import yaml`.
py_imports_yaml() {
    case "${1:-}" in /*) ;; *) return 1 ;; esac
    [ -x "$1" ] || return 1
    "$1" -c "import yaml" >/dev/null 2>&1
}

pick_python() {
    for _cand in "$@"; do
        if py_imports_yaml "$_cand"; then printf '%s' "$_cand"; return 0; fi
    done
    return 1
}

_xml_escape() { printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g'; }
_sed_escape() { printf '%s' "$1" | sed -e 's/[\\&|]/\\&/g'; }

# §55 launchd viability: TCC grants file access per binary and a launchd job is
# its own responsible process, so the only honest probe asks launchd to run
# `import act` with the candidate. 0 viable, 1 not viable, 2 inconclusive.
LAUNCHD_PROBE_DETAIL=""
py_launchd_can_import_act() { # $1=interpreter $2=physical repo root
    _py="$1"; _repo="$2"
    LAUNCHD_PROBE_DETAIL=""
    case "$_py" in /*) ;; *) return 1 ;; esac
    [ -x "$_py" ] || return 1
    [ "${AIASSISTANT_LAUNCHD_PROBE:-1}" = "0" ] && return 2
    command -v launchctl >/dev/null 2>&1 || return 2
    _probe_dir="$(mktemp -d 2>/dev/null)" || return 2
    _probe_label="com.zelin.aiassistant.viability.$$"
    _probe_pl="$_probe_dir/probe.plist"
    _probe_verdict="$_probe_dir/verdict"
    cat > "$_probe_pl" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$_probe_label</string>
  <key>ProgramArguments</key><array>
    <string>$(_xml_escape "$_py")</string><string>-c</string>
    <string>import sys
try:
    sys.path.insert(0, sys.argv[1]); import act; v = "ok"
except BaseException as e:
    v = "fail:%s: %s" % (type(e).__name__, e)
open(sys.argv[2], "w").write(v)</string>
    <string>$(_xml_escape "$_repo")</string>
    <string>$(_xml_escape "$_probe_verdict")</string>
  </array>
  <key>EnvironmentVariables</key><dict>
    <key>PYTHONPATH</key><string>$(_xml_escape "$_repo")</string>
    <key>AIASSISTANT_HOME</key><string>$(_xml_escape "$_repo")</string>
  </dict>
  <key>WorkingDirectory</key><string>$(_xml_escape "$HOME")</string>
  <key>RunAtLoad</key><true/>
  <key>AbandonProcessGroup</key><true/>
</dict></plist>
EOF
    launchctl bootout "gui/$UID_NUM/$_probe_label" >/dev/null 2>&1
    if ! { launchctl bootstrap "gui/$UID_NUM" "$_probe_pl" >/dev/null 2>&1 \
           || launchctl load "$_probe_pl" >/dev/null 2>&1; }; then
        rm -rf "$_probe_dir"
        return 2
    fi
    _probe_n=0
    while [ ! -s "$_probe_verdict" ] && [ "$_probe_n" -lt 60 ]; do
        sleep 0.25; _probe_n=$((_probe_n + 1))
    done
    LAUNCHD_PROBE_DETAIL="$(cat "$_probe_verdict" 2>/dev/null || true)"
    launchctl bootout "gui/$UID_NUM/$_probe_label" >/dev/null 2>&1 \
        || launchctl unload "$_probe_pl" >/dev/null 2>&1 || true
    rm -rf "$_probe_dir"
    [ "$LAUNCHD_PROBE_DETAIL" = "ok" ] && return 0
    [ -z "$LAUNCHD_PROBE_DETAIL" ] && LAUNCHD_PROBE_DETAIL="fail: produced no verdict under launchd"
    return 1
}

# Is the repo outside $HOME (physical paths)? That is the TCC-sensitive shape.
repo_outside_home() {
    _rh="$(physical_path "$HOME")"
    _rr="$(physical_path "$REPO_ROOT")"
    case "$_rr" in "$_rh"|"$_rh"/*) return 1 ;; *) return 0 ;; esac
}

# Candidates, most preferred first (§55): the system python ranks above
# Homebrew / conda when the repo is outside $HOME (it already holds the user's
# file-access grants); $AIASSISTANT_PYTHON always first.
daemon_python_candidates() {
    if repo_outside_home; then
        printf '%s\n' "${AIASSISTANT_PYTHON:-}" /usr/bin/python3 \
            "$(pinned_python)" "$HOME/miniconda3/bin/python3" "${PY:-}"
    else
        printf '%s\n' "${AIASSISTANT_PYTHON:-}" "$(pinned_python)" \
            "$HOME/miniconda3/bin/python3" "${PY:-}" /usr/bin/python3
    fi
}

# First candidate that imports yaml AND imports act under launchd. Sets
# DAEMON_PY / DAEMON_PY_NOTE (globals: the probe verdict must survive).
DAEMON_PY=""
DAEMON_PY_NOTE=""
pick_daemon_python() {
    DAEMON_PY=""; DAEMON_PY_NOTE=""
    _first_yaml=""
    _repo_phys="$(physical_path "$REPO_ROOT")"
    for _cand in "$@"; do
        py_imports_yaml "$_cand" || continue
        [ -n "$_first_yaml" ] || _first_yaml="$_cand"
        py_launchd_can_import_act "$_cand" "$_repo_phys"
        case "$?" in
            0) DAEMON_PY="$_cand"; DAEMON_PY_NOTE=""; return 0 ;;
            2) DAEMON_PY="$_cand"
               DAEMON_PY_NOTE="launchd viability unverifiable here — used the PyYAML gate only"
               return 0 ;;
            *) DAEMON_PY_NOTE="$_cand imports yaml but cannot import act under launchd ($LAUNCHD_PROBE_DETAIL)" ;;
        esac
    done
    if [ -n "$_first_yaml" ]; then
        DAEMON_PY="$_first_yaml"
        DAEMON_PY_NOTE="no candidate passed the launchd probe; fell back to $_first_yaml"
        return 0
    fi
    return 1
}

# The interpreter pinned by a previous run, "" when unpinned.
pinned_python() {
    [ -f "$REPO_ROOT/config/runtime.json" ] || return 0
    sed -n 's/.*"python"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' \
        "$REPO_ROOT/config/runtime.json"
}

# Version stamp (§56.1): the tag is the truth; act/_version.py is written before
# anything imports act. The first candidate interpreter that can run the stamper
# wins (§55 第三幕: a Homebrew python may be TCC-blind to an external volume).
STAMPED_VERSION=""
STAMP_PY=""
stamp_python_candidates() {
    daemon_python_candidates
    printf '%s\n' "$(command -v python3 2>/dev/null || true)"
}
stamp_version() {
    STAMPED_VERSION=""; STAMP_PY=""
    _stamp_err="$(mktemp 2>/dev/null || printf '/tmp/zai-stamp.%s' "$$")"
    _tried=""; _why=""
    _saved_ifs="$IFS"
    IFS='
'
    # shellcheck disable=SC2046 # newline word-splitting the candidate list is the point
    for _spy in $(stamp_python_candidates); do
        IFS="$_saved_ifs"
        case "$_spy" in /*) ;; *) continue ;; esac
        [ -x "$_spy" ] || continue
        case " $_tried " in *" $_spy "*) continue ;; esac
        _tried="$_tried $_spy"
        STAMPED_VERSION="$(cd "$REPO_ROOT" && "$_spy" scripts/version_stamp.py --write 2>"$_stamp_err")"
        _rc=$?
        if [ "$_rc" -eq 0 ] && [ -n "$STAMPED_VERSION" ]; then
            STAMP_PY="$_spy"
            break
        fi
        STAMPED_VERSION=""
        _last="$(tail -n 1 "$_stamp_err" 2>/dev/null | tr -d '\r')"
        _why="${_why:+$_why; }$_spy: ${_last:-exit $_rc, no stderr}"
    done
    IFS="$_saved_ifs"
    rm -f "$_stamp_err"
    if [ -n "$STAMPED_VERSION" ]; then
        ok "act/_version.py -> v$STAMPED_VERSION (git tag truth, §56.1; $STAMP_PY)"
        [ -z "$_why" ] || info "  version: skipped interpreter(s) that could not run the stamper — $_why"
        report_step "version" "ok" "$STAMPED_VERSION"
    elif [ -z "$_tried" ]; then
        warn "no python3 — act/_version.py not written (the baked fallback version is reported)"
        report_step "version" "warn" "no python3 to stamp"
    else
        warn "scripts/version_stamp.py failed with every interpreter — act/_version.py not written: $_why"
        report_step "version" "warn" "stamp failed — $_why"
    fi
}

# claude as the user's LOGIN shell resolves it (2026-07-08: a second, outdated
# install ranked first on a minimal PATH). Sets CLAUDE_LOGIN_BIN ("" = none).
CLAUDE_LOGIN_BIN=""
resolve_claude_login_bin() {
    CLAUDE_LOGIN_BIN=""
    _c="$("${SHELL:-/bin/zsh}" -lc 'command -v claude' 2>/dev/null </dev/null | tail -n 1 || true)"
    case "$_c" in
        /*) [ -x "$_c" ] && CLAUDE_LOGIN_BIN="$_c" ;;
    esac
    [ -n "$CLAUDE_LOGIN_BIN" ] || CLAUDE_LOGIN_BIN="$(command -v claude 2>/dev/null || true)"
    if [ -z "$CLAUDE_LOGIN_BIN" ]; then
        for _c in "$HOME/.local/bin/claude" /opt/homebrew/bin/claude /usr/local/bin/claude; do
            [ -x "$_c" ] && { CLAUDE_LOGIN_BIN="$_c"; break; }
        done
    fi
}

# §55 第五幕 — the stable copy of claude at ONE path under $HOME, refreshed in
# place, so a Full Disk Access grant survives claude updates. Refuses a source
# without a valid Apple-anchored signature; every refusal keeps the previous
# copy and is a warn, never a fail. §23 step `stable_claude`.
refresh_stable_claude() { # $1 = the claude to copy
    _src="${1:-}"
    if [ -z "$_src" ] || [ ! -x "$_src" ]; then
        warn "no claude to copy — the stable copy $STABLE_CLAUDE_BIN was not created"
        report_step "stable_claude" "skipped" "no claude to copy"
        return 0
    fi
    _real="$(readlink -f "$_src" 2>/dev/null || printf '%s' "$_src")"
    _ver="$( (cd "$HOME" && DISABLE_AUTOUPDATER=1 "$_real" --version 2>/dev/null | head -n 1) || true)"
    if [ -x "$STABLE_CLAUDE_BIN" ] && cmp -s "$_real" "$STABLE_CLAUDE_BIN"; then
        ok "stable claude: $STABLE_CLAUDE_BIN unchanged (${_ver:-$_real})"
        report_step "stable_claude" "ok" "unchanged: $STABLE_CLAUDE_BIN (${_ver:-$_real})"
        return 0
    fi
    _kept=""
    [ -x "$STABLE_CLAUDE_BIN" ] && _kept="; the previous copy stays in place"
    if ! codesign --verify --strict -R='anchor apple generic' "$_real" >/dev/null 2>&1; then
        warn "claude at $_real carries no valid Apple-anchored signature — not copied$_kept"
        report_step "stable_claude" "warn" "refused: $_real is not a validly signed binary$_kept"
        return 0
    fi
    _dir="$(dirname "$STABLE_CLAUDE_BIN")"
    _tmp="$_dir/.claude.tmp.$$"
    if ! mkdir -p "$_dir"; then
        warn "cannot create $_dir — stable claude not written$_kept"
        report_step "stable_claude" "warn" "mkdir $_dir failed$_kept"
        return 0
    fi
    rm -f "$_tmp"
    if ! { cp -cp "$_real" "$_tmp" 2>/dev/null || cp -p "$_real" "$_tmp"; }; then
        rm -f "$_tmp"
        warn "copying $_real to $_tmp failed — stable claude not refreshed$_kept"
        report_step "stable_claude" "warn" "copy failed$_kept"
        return 0
    fi
    if ! codesign --verify --strict -R='anchor apple generic' "$_tmp" >/dev/null 2>&1; then
        rm -f "$_tmp"
        warn "the copy at $_tmp failed signature verification — discarded$_kept"
        report_step "stable_claude" "warn" "copy failed signature verification$_kept"
        return 0
    fi
    if ! (cd "$HOME" && DISABLE_AUTOUPDATER=1 "$_tmp" --version >/dev/null 2>&1); then
        rm -f "$_tmp"
        warn "the copy at $_tmp does not run \`--version\` — discarded$_kept"
        report_step "stable_claude" "warn" "copy does not run --version$_kept"
        return 0
    fi
    _what="created"
    [ -n "$_kept" ] && _what="refreshed"
    if ! mv -f "$_tmp" "$STABLE_CLAUDE_BIN"; then
        rm -f "$_tmp"
        warn "mv $_tmp -> $STABLE_CLAUDE_BIN failed$_kept"
        report_step "stable_claude" "warn" "mv into place failed$_kept"
        return 0
    fi
    ok "stable claude: $_what $STABLE_CLAUDE_BIN (${_ver:-$_real})"
    report_step "stable_claude" "ok" "$_what: $STABLE_CLAUDE_BIN (${_ver:-$_real})"
    if [ "$_what" = "created" ]; then
        info "one-time: System Settings > Privacy & Security > Full Disk Access > + > $STABLE_CLAUDE_BIN"
    fi
}

# §78: the private Claude Code config dir every headless claude runs on, and its
# empty cwd outside the repo tree. Both 0700, never written into by this app.
ensure_claude_config_dirs() {
    if mkdir -p "$CLAUDE_CONFIG_DIR_PATH/cwd" && chmod 700 "$CLAUDE_CONFIG_DIR_PATH" "$CLAUDE_CONFIG_DIR_PATH/cwd"; then
        ok "isolation dirs: $CLAUDE_CONFIG_DIR_PATH (+ cwd/) — headless claude never reads ~/.claude (§78)"
        report_step "claude_config" "ok" "$CLAUDE_CONFIG_DIR_PATH"
    else
        warn "could not create $CLAUDE_CONFIG_DIR_PATH/cwd — act.lib.config creates it on first use"
        report_step "claude_config" "warn" "mkdir $CLAUDE_CONFIG_DIR_PATH/cwd failed"
    fi
}

# First non-empty line, surrounding whitespace trimmed (§19: one-line token files).
first_token_line() {
    awk 'NF { gsub(/^[ \t\r]+|[ \t\r]+$/, ""); print; exit }'
}

# §19: the key lives in config/secrets/anthropic-api-key.txt (dir 0700, file
# 0600). When that file is missing and $ANTHROPIC_API_KEY or
# ~/.config/anthropic-key.txt holds one, copy it — asked in an interactive run
# with a terminal, done directly otherwise. The value is never printed.
install_api_key() {
    _dst_dir="$REPO_ROOT/config/secrets"
    _dst="$_dst_dir/anthropic-api-key.txt"
    _legacy="$HOME/.config/anthropic-key.txt"
    if [ -s "$_dst" ]; then
        ok "Anthropic API key present (config/secrets/anthropic-api-key.txt)"
        report_step "anthropic_key" "ok" "config/secrets/anthropic-api-key.txt present"
        return 0
    fi
    _from=""
    if [ -n "${ANTHROPIC_API_KEY:-}" ]; then
        _from="\$ANTHROPIC_API_KEY"
    elif [ -s "$_legacy" ]; then
        _from="$_legacy"
    fi
    if [ -z "$_from" ]; then
        warn "no Anthropic API key — paste one in the app (Settings > Credentials); headless claude runs only on an API key (§78)"
        report_step "anthropic_key" "skipped" "no key found; paste one in Settings > Credentials"
        return 0
    fi
    if [ "$NON_INTERACTIVE" -eq 0 ] && [ -t 0 ]; then
        printf "  Copy the Anthropic API key from %s into config/secrets/anthropic-api-key.txt? [Y/n] " "$_from"
        _reply=""
        read -r _reply || _reply=""
        case "$_reply" in
            n|N|no|NO)
                info "key not copied — paste one later in Settings > Credentials"
                report_step "anthropic_key" "skipped" "copy from $_from declined"
                return 0 ;;
        esac
    fi
    if [ "$_from" = "\$ANTHROPIC_API_KEY" ]; then
        _val="$(printf '%s\n' "$ANTHROPIC_API_KEY" | first_token_line)"
    else
        _val="$(first_token_line < "$_legacy")"
    fi
    if [ -z "$_val" ]; then
        warn "$_from holds no key line — nothing copied"
        report_step "anthropic_key" "warn" "$_from is empty"
        return 0
    fi
    if ( umask 077 && mkdir -p "$_dst_dir" && chmod 700 "$_dst_dir" \
            && printf '%s\n' "$_val" > "$_dst.tmp.$$" && chmod 600 "$_dst.tmp.$$" \
            && mv -f "$_dst.tmp.$$" "$_dst" ); then
        ok "Anthropic API key copied from $_from into config/secrets/anthropic-api-key.txt (0600)"
        report_step "anthropic_key" "ok" "copied from $_from"
    else
        rm -f "$_dst.tmp.$$"
        warn "could not write $_dst — paste the key in Settings > Credentials"
        report_step "anthropic_key" "warn" "write into config/secrets failed"
    fi
}

# ------------------------------------------------------------------ launchd
launchd_unload() { # $1=plist path, $2=label
    launchctl bootout "gui/$UID_NUM/$2" >/dev/null 2>&1 \
        || launchctl unload "$1" >/dev/null 2>&1 || true
}
launchd_load() { # $1=plist path
    launchctl bootstrap "gui/$UID_NUM" "$1" >/dev/null 2>&1 \
        || launchctl load "$1" >/dev/null 2>&1
}
launchd_label_loaded() { # $1=label
    launchctl list 2>/dev/null | awk -v l="$1" '$3 == l' | grep -q .
}

# Retire a label for good: unload + delete its plist + PROVE it is gone (§55:
# a swallowed bootout once left a removed agent running for 51 days).
RETIRED_STILL_LOADED=""
RETIRED_DONE=""
launchd_retire() { # $1=label
    _was=0
    launchd_label_loaded "$1" && _was=1
    [ -e "$LA_DIR/$1.plist" ] && _was=1
    launchd_unload "$LA_DIR/$1.plist" "$1"
    rm -f "$LA_DIR/$1.plist"
    if launchd_label_loaded "$1"; then
        echo "  [ERR ] retired agent $1 is STILL loaded after bootout" >&2
        info "  fix: launchctl bootout gui/$UID_NUM/$1   # then re-run install.sh"
        RETIRED_STILL_LOADED="${RETIRED_STILL_LOADED:-} $1"
    elif [ "$_was" -eq 1 ]; then
        ok "unloaded retired agent $1 (1.x; plist removed)"
        RETIRED_DONE="${RETIRED_DONE:-} $1"
    fi
}

# Labels of ours (both §78 prefixes) loaded or left in ~/Library/LaunchAgents
# with no template under act/launchd/ — reported, never unloaded.
launchd_orphans() {
    {
        launchctl list 2>/dev/null | awk '$3 ~ /^com\.zelin\.(aiassistant\.|screenpipe-)/ {print $3}'
        for _p in "$LA_DIR"/com.zelin.aiassistant.*.plist "$LA_DIR"/com.zelin.screenpipe-*.plist; do
            [ -e "$_p" ] || continue
            _b="$(basename "$_p")"; printf '%s\n' "${_b%.plist}"
        done
    } | sort -u | while IFS= read -r _label; do
        [ -e "$REPO_ROOT/act/launchd/$_label.plist" ] && continue
        printf '%s\n' "$_label"
    done
}

# Render a template: the /Users/YOURUSERNAME placeholders become the daemon
# interpreter, the PHYSICAL repo root, $HOME and the login shell's claude dir
# (first on PATH); ZAI_PORT comes from config.yaml server.port (§54).
render_launchd_plist() {
    src="$1"; dest="$2"
    py="${RUNTIME_PY:-}"
    if ! py_imports_yaml "$py"; then
        _saved_ifs="$IFS"
        IFS='
'
        # shellcheck disable=SC2046 # newline word-splitting the candidate list is the point
        py="$(pick_python $(daemon_python_candidates) || echo /usr/bin/python3)"
        IFS="$_saved_ifs"
    fi
    pydir="$(dirname "$py")"
    repo="$(physical_path "$REPO_ROOT")"
    claudedir="$HOME/.local/bin"
    [ -n "${CLAUDE_LOGIN_BIN:-}" ] && claudedir="$(dirname "$CLAUDE_LOGIN_BIN")"
    # launchd opens StandardOut/ErrorPath before exec: the directory must exist.
    mkdir -p "$HOME/Library/Logs/zelin-ai-assistant"
    port="${SERVER_PORT:-47820}"
    sed -e "s|/Users/YOURUSERNAME/\.claude-bin|$(_sed_escape "$claudedir")|g" \
        -e "s|/Users/YOURUSERNAME/miniconda3/bin/python3|$(_sed_escape "$py")|g" \
        -e "s|/Users/YOURUSERNAME/Projects/zelin-ai-assistant|$(_sed_escape "$repo")|g" \
        -e "s|/Users/YOURUSERNAME/miniconda3/bin|$(_sed_escape "$pydir")|g" \
        -e "s|/Users/YOURUSERNAME|$(_sed_escape "$HOME")|g" \
        -e "s|<key>ZAI_PORT</key><string>[0-9]*</string>|<key>ZAI_PORT</key><string>$(_sed_escape "$port")</string>|g" \
        "$src" > "$dest"
}

# config.yaml server.port, fail-open to 47820.
server_port() {
    _p="$( (cd "$REPO_ROOT" && AIASSISTANT_HOME="$REPO_ROOT" "${RUNTIME_PY:-python3}" -c '
from act.lib import config
try:
    print(int(config.load_config().server_port))
except Exception:
    print(config.DEFAULT_SERVER_PORT)') 2>/dev/null )"
    case "$_p" in ''|*[!0-9]*) _p=47820 ;; esac
    printf '%s' "$_p"
}

# Does anything answer GET /api/health on the board port? (§54: two servers must
# never fight for the port; a stranger is reported, never killed.)
board_server_answering() { # $1=port
    (cd "$REPO_ROOT" && "${RUNTIME_PY:-python3}" -c '
import sys, urllib.request
try:
    with urllib.request.urlopen("http://127.0.0.1:%d/api/health" % int(sys.argv[1]), timeout=2) as r:
        raise SystemExit(0 if 200 <= r.status < 300 else 1)
except SystemExit:
    raise
except Exception:
    raise SystemExit(1)' "$1" >/dev/null 2>&1)
}

# Keep the newest half of an agent's launchd log past 1 MB, in the unload→load
# window when launchd holds no fd on it (防腐 #4; §55).
cap_launchd_log() { # $1=label
    _lf="$HOME/Library/Logs/zelin-ai-assistant/${1##*.}.launchd.log"
    [ -f "$_lf" ] || return 0
    _lsz="$(wc -c < "$_lf" | tr -d ' ')"
    [ "$_lsz" -gt 1048576 ] || return 0
    if tail -c 524288 "$_lf" > "$_lf.tmp" && mv "$_lf.tmp" "$_lf"; then
        info "capped ${1##*.}.launchd.log (was $_lsz bytes; newest half kept)"
    fi
}

# `launchctl load` succeeds even when the job dies on spawn — read `launchctl list`.
verify_launchd_agent() { # $1=label
    _line="$(launchctl list 2>/dev/null | awk -v l="$1" '$3 == l')"
    if [ -z "$_line" ]; then
        echo "  [ERR ] $1 not registered with launchd — try: launchctl bootstrap gui/$UID_NUM $LA_DIR/$1.plist" >&2
        return 1
    fi
    _pid="$(printf '%s' "$_line" | awk '{print $1}')"
    _st="$(printf '%s' "$_line" | awk '{print $2}')"
    if [ "$_pid" != "-" ]; then
        ok "$1 running (pid $_pid)"
    elif [ "$_st" = "0" ]; then
        ok "$1 loaded (last run exited 0)"
    else
        echo "  [ERR ] $1 loaded but its process exits with status $_st" >&2
        info "fix: read ~/Library/Logs/zelin-ai-assistant/${1##*.}.launchd.log — \"No module named 'act'\" = the interpreter cannot see the repo (Full Disk Access, §55); \"No module named 'yaml'\" = ${RUNTIME_PY:-python3} -m pip install --user pyyaml"
        return 1
    fi
}

# ------------------------------------------------------------------ 1.x migration
# The physical form of a path that may not exist (a dangling link into a removed
# repo directory): resolve the deepest existing ancestor, keep the rest.
physical_target() { # $1 = absolute path
    _cur="$1"; _rest=""
    while [ -n "$_cur" ] && [ "$_cur" != "/" ] && [ ! -d "$_cur" ]; do
        _rest="/$(basename "$_cur")$_rest"
        _cur="$(dirname "$_cur")"
    done
    _base="$(physical_path "${_cur:-/}")"
    [ "$_base" = "/" ] && _base=""
    printf '%s%s' "$_base" "$_rest"
}

# §78: unlink the entries of ~/.claude/skills that are symlinks resolving into
# this repo (1.x scripts/skills_sync.sh). Real directories (the owner's own
# skills) and links elsewhere are never touched. Sets MIGRATED_LINKS.
MIGRATED_LINKS=""
unlink_repo_skill_links() {
    _sd="$HOME/.claude/skills"
    [ -d "$_sd" ] || return 0
    _root="$(physical_path "$REPO_ROOT")"
    for _l in "$_sd"/* "$_sd"/.[!.]*; do
        [ -L "$_l" ] || continue
        _t="$(readlink "$_l" 2>/dev/null)" || continue
        case "$_t" in /*) ;; *) _t="$_sd/$_t" ;; esac
        case "$(physical_target "$_t")" in
            "$_root"|"$_root"/*)
                if rm -f "$_l"; then
                    ok "unlinked ~/.claude/skills/$(basename "$_l") (a 1.x link into this repo)"
                    MIGRATED_LINKS="$MIGRATED_LINKS $(basename "$_l")"
                fi ;;
        esac
    done
}

# Claude Code's project-dir name for a cwd (every non-alphanumeric -> '-').
claude_project_slug() { printf '%s' "$1" | sed 's/[^A-Za-z0-9]/-/g'; }

# The cleanup command for the 1.x headless transcripts (their cwd was state/).
# Printed, never run: those files live in the owner's Claude Code home (art.13).
print_transcript_cleanup() {
    _slugs="$(claude_project_slug "$SCRIPT_DIR_LOGICAL/state")"
    _phys="$(claude_project_slug "$REPO_ROOT/state")"
    [ "$_phys" = "$_slugs" ] || _slugs="$_slugs $_phys"
    info "1.x headless runs left transcripts in Claude Code's projects dir. Archive or delete them yourself"
    info "(this installer never touches ~/.claude/projects):"
    for _s in $_slugs; do
        info "  rm -rf ~/.claude/projects/$_s*"
    done
}

# Step 4 (§78 install.sh 1.x migration duty). Under --no-launchd the LaunchAgent
# half is skipped (that mode touches no scheduler). §23 step `migrate`.
migrate_from_1x() {
    echo "==> 4. migrate a 1.x install (§78)"
    RETIRED_STILL_LOADED=""; RETIRED_DONE=""; MIGRATED_LINKS=""
    _removed=""
    if [ "$NO_LAUNCHD" -eq 1 ]; then
        info "--no-launchd: 1.x LaunchAgents not inspected"
    else
        for _label in $RETIRED_LABELS; do
            launchd_retire "$_label"
        done
    fi
    unlink_repo_skill_links
    for _f in "$APP_SUPPORT_DIR/deploy_state.json" "$REPO_ROOT/state/deploy_state.json"; do
        if [ -e "$_f" ] && rm -f "$_f"; then
            ok "removed the 1.x auto-deploy state $_f"
            _removed="$_removed $(basename "$(dirname "$_f")")/deploy_state.json"
        fi
    done
    if [ -n "$RETIRED_DONE$MIGRATED_LINKS$_removed" ]; then
        print_transcript_cleanup
    fi
    _detail="agents:${RETIRED_DONE:- none}; skills links:${MIGRATED_LINKS:- none}; files:${_removed:- none}"
    if [ -n "$RETIRED_STILL_LOADED" ]; then
        report_step "migrate" "fail" "still loaded:$RETIRED_STILL_LOADED; $_detail"
    elif [ -n "$RETIRED_DONE$MIGRATED_LINKS$_removed" ]; then
        report_step "migrate" "ok" "$_detail"
    else
        ok "nothing of 1.x left"
        report_step "migrate" "ok" "nothing to migrate"
    fi
}

# ------------------------------------------------------------------ board UI
# Step 5 (§56.5 `ui`): web/dist (built in a mirror under $HOME — node is
# TCC-judged on its own and may not read an external-volume repo) and the Dock
# shell installed as "Zelin's AI Assistant.app" (id com.zelin.ai-board, §54).
# Each half ok | skipped | fail (web also skipped_tcc); a missing toolchain is
# skipped + warn, never a failure. Each command runs under a wall clock.
UI_APP_NAME="Zelin's AI Assistant"      # bundle folder — id com.zelin.ai-board (§54)
UI_BUNDLE_ID="com.zelin.ai-board"
UI_EXEC_NAME="ZelinAIBoard"             # CFBundleExecutable → pgrep/pkill -x
UI_ENGINE_HANDOFF_KEY="engineHandoff"   # = shell EngineOwnership.handoffKey (§61.8)
UI_LEGACY_APP_NAME="Zelin's AI Assistant (old)"   # the retired 1.x menu-bar app
UI_LEGACY_BUNDLE_ID="com.zelin.ai-engineer"       # CONTRACT §12
UI_PREVIOUS_APP_NAME="Zelin AI Board"   # the shell's folder name before the §54 swap
UI_APPS_DIR="${AIASSISTANT_UI_APPS_DIR:-/Applications}"
UI_BUDGET_S="${AIASSISTANT_UI_BUDGET:-600}"
UI_LOG="$HOME/Library/Logs/zelin-ai-assistant/ui-build.log"
UI_LOG_CAP_BYTES=1048576                # 防腐 #4
UI_SHELL_INSTALLED=0
UI_LEGACY_MOVED=0
UI_APP_PATH=""
UI_WEB_STATUS=""; UI_WEB_DETAIL=""
UI_SHELL_STATUS=""; UI_SHELL_DETAIL=""

# A command under a wall-clock limit; 124 on timeout (children reaped too).
ui_run_with_timeout() { # $1=seconds, rest=command
    _limit="$1"; shift
    "$@" &
    _pid=$!
    _ticks=0
    while kill -0 "$_pid" 2>/dev/null; do
        if [ $((_ticks / 4)) -ge "$_limit" ]; then
            echo "  [ERR ] ui: timeout after ${_limit}s — killing: $*" >&2
            pkill -TERM -P "$_pid" 2>/dev/null
            kill -TERM "$_pid" 2>/dev/null
            sleep 2
            pkill -KILL -P "$_pid" 2>/dev/null
            kill -KILL "$_pid" 2>/dev/null
            wait "$_pid" 2>/dev/null
            return 124
        fi
        sleep 0.25
        _ticks=$((_ticks + 1))
    done
    wait "$_pid"
}

ui_log_begin() {
    mkdir -p "$(dirname "$UI_LOG")"
    if [ -f "$UI_LOG" ]; then
        _sz="$(wc -c < "$UI_LOG" | tr -d ' ')"
        if [ "$_sz" -gt "$UI_LOG_CAP_BYTES" ]; then
            tail -c "$((UI_LOG_CAP_BYTES / 2))" "$UI_LOG" > "$UI_LOG.tmp" && mv "$UI_LOG.tmp" "$UI_LOG"
        fi
    fi
    printf '\n==== install.sh ui step %s ====\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$UI_LOG"
}

ui_log_tail() {
    info "last lines of $UI_LOG:"
    tail -n 15 "$UI_LOG" 2>/dev/null | sed 's/^/         /'
}

ui_now() { date +%s; }

ui_web_build_dir() {
    if [ -n "${AIASSISTANT_UI_BUILD_DIR:-}" ]; then printf '%s' "$AIASSISTANT_UI_BUILD_DIR"; return; fi
    printf '%s' "$HOME/Library/Caches/zelin-ai-assistant/web-build"
}

# Mirror web/ (minus node_modules + dist) into the build dir; --checksum so the
# `npm ci` gate below sees same-size edits inside rsync's mtime window.
ui_sync_web_sources() { # $1=src web dir $2=build dir
    mkdir -p "$2"
    if command -v rsync >/dev/null 2>&1; then
        rsync -a --checksum --delete --exclude node_modules --exclude dist --exclude '.zai-*' "$1/" "$2/" >> "$UI_LOG" 2>&1
    else
        _keep="$2/node_modules"; _tmpkeep=""
        if [ -d "$_keep" ]; then _tmpkeep="$2.node_modules.keep"; rm -rf "$_tmpkeep"; mv "$_keep" "$_tmpkeep"; fi
        rm -rf "$2" && mkdir -p "$2" && cp -R "$1/." "$2/" && rm -rf "$2/node_modules" "$2/dist" \
            && { [ -z "$_tmpkeep" ] || mv "$_tmpkeep" "$_keep"; }
    fi
}

ui_log_says_tcc() {
    tail -n 40 "$UI_LOG" 2>/dev/null | grep -Eiq 'EPERM|operation not permitted'
}

install_web_ui() {
    UI_WEB_STATUS=skipped; UI_WEB_DETAIL=""
    if ! command -v npm >/dev/null 2>&1 || ! command -v node >/dev/null 2>&1; then
        warn "ui: node/npm not found — web board not built (brew install node, then bash install.sh)"
        UI_WEB_DETAIL="web skipped (no node/npm)"
        return 0
    fi
    if [ ! -f "$REPO_ROOT/web/package.json" ]; then
        warn "ui: web/package.json missing — web board not built"
        UI_WEB_DETAIL="web skipped (no web/package.json)"
        return 0
    fi
    _web="$REPO_ROOT/web"
    _bld="$(ui_web_build_dir)"
    if ! ui_sync_web_sources "$_web" "$_bld"; then
        warn "ui: could not mirror web/ into $_bld — see $UI_LOG"
        UI_WEB_STATUS=fail; UI_WEB_DETAIL="web fail (source sync into $_bld failed)"
        return 0
    fi
    _stamp="$_bld/node_modules/.zai-package-lock.cksum"
    _lock_sum=""
    [ -f "$_bld/package-lock.json" ] && _lock_sum="$(cksum < "$_bld/package-lock.json" | cut -d' ' -f1)"
    _ci_s=0
    if [ ! -d "$_bld/node_modules" ] || [ "$(cat "$_stamp" 2>/dev/null)" != "$_lock_sum" ]; then
        _t0="$(ui_now)"
        (cd "$_bld" && CI=1 ui_run_with_timeout "$UI_BUDGET_S" npm ci --no-audit --no-fund >> "$UI_LOG" 2>&1)
        _rc=$?
        if [ "$_rc" -ne 0 ]; then
            ui_web_failed "npm ci" "$_rc"
            return 0
        fi
        mkdir -p "$_bld/node_modules" && printf '%s' "$_lock_sum" > "$_stamp"
        _ci_s=$(( $(ui_now) - _t0 ))
    fi
    _t0="$(ui_now)"
    (cd "$_bld" && CI=1 ui_run_with_timeout "$UI_BUDGET_S" npm run build >> "$UI_LOG" 2>&1)
    _rc=$?
    if [ "$_rc" -ne 0 ]; then
        ui_web_failed "npm run build" "$_rc"
        return 0
    fi
    _build_s=$(( $(ui_now) - _t0 ))
    if [ ! -f "$_bld/dist/index.html" ]; then
        warn "ui: npm run build exited 0 but $_bld/dist/index.html is missing"
        UI_WEB_STATUS=fail; UI_WEB_DETAIL="web fail (no dist/index.html after build)"
        return 0
    fi
    rm -rf "$_web/dist.tmp"
    if ! cp -R "$_bld/dist" "$_web/dist.tmp"; then
        rm -rf "$_web/dist.tmp"
        warn "ui: could not copy the built dist into $_web"
        UI_WEB_STATUS=fail; UI_WEB_DETAIL="web fail (publish into web/dist failed)"
        return 0
    fi
    rm -rf "$_web/dist" && mv "$_web/dist.tmp" "$_web/dist"
    UI_WEB_STATUS=ok
    UI_WEB_DETAIL="web ok (npm ci ${_ci_s}s, build ${_build_s}s)"
    ok "ui: web/dist built (npm ci ${_ci_s}s, build ${_build_s}s; built in $_bld)"
}

ui_web_failed() { # $1=step name $2=rc
    if ui_log_says_tcc; then
        warn "ui: $1 hit EPERM / operation not permitted — node lacks Full Disk Access in this session; web board not rebuilt"
        info "  fix: run bash $REPO_ROOT/install.sh from a terminal, or grant $(command -v node) Full Disk Access"
        UI_WEB_STATUS=skipped_tcc; UI_WEB_DETAIL="web skipped_tcc ($1 exit $2: EPERM; grant node Full Disk Access or bash install.sh from a terminal)"
        return 0
    fi
    warn "ui: $1 failed (exit $2) — see $UI_LOG"
    ui_log_tail
    UI_WEB_STATUS=fail; UI_WEB_DETAIL="web fail ($1 exit $2)"
}

ui_bundle_id() { # $1=.app path
    plutil -extract CFBundleIdentifier raw "$1/Contents/Info.plist" 2>/dev/null || true
}

# §12: a residual 1.x menu-bar bundle (com.zelin.ai-engineer) on the product
# path is renamed aside to "(old)" — same-directory rename, never edited (its
# Info.plist is inside the signature seal) — so the shell can take the path.
ui_retire_legacy_app() { # $1=apps dir
    _product="$1/$UI_APP_NAME.app"
    _old="$1/$UI_LEGACY_APP_NAME.app"
    [ -d "$_product" ] || return 0
    [ "$(ui_bundle_id "$_product")" = "$UI_LEGACY_BUNDLE_ID" ] || return 0
    if [ ! -d "$_old" ]; then
        if mv "$_product" "$_old" 2>>"$UI_LOG"; then
            ok "ui: legacy app moved aside: $_old (the 1.x menu-bar app; delete it when you no longer need it)"
            UI_LEGACY_MOVED=1
            return 0
        fi
        warn "ui: could not move the legacy app to \"$_old\" — see $UI_LOG"
        info "  fix: mv \"$_product\" \"$_old\"   # then bash install.sh"
        return 1
    fi
    _parked="$1/$UI_LEGACY_APP_NAME $(date +%Y%m%d-%H%M%S).app"
    if ! mv "$_product" "$_parked" 2>>"$UI_LOG"; then
        warn "ui: a legacy app sits on the product path and \"$_old\" already exists — could not move it aside"
        info "  fix: sudo rm -rf \"$_product\""
        return 1
    fi
    if rm -rf "$_parked" 2>>"$UI_LOG" && [ ! -e "$_parked" ]; then
        ok "ui: removed a second legacy bundle from the product path (\"$_old\" kept)"
    else
        warn "ui: a second legacy bundle was parked as \"$_parked\" (rm refused)"
        info "  fix: sudo rm -rf \"$_parked\""
    fi
    return 0
}

# The shell's pre-swap bundle "Zelin AI Board.app" goes once the new one is in
# place — only when its id really is com.zelin.ai-board.
ui_remove_previous_shell() { # $1=apps dir
    _prev="$1/$UI_PREVIOUS_APP_NAME.app"
    [ -d "$_prev" ] || return 0
    if [ "$(ui_bundle_id "$_prev")" != "$UI_BUNDLE_ID" ]; then
        info "ui: $_prev is not the board shell (bundle id differs) — left alone"
        return 0
    fi
    if rm -rf "$_prev" 2>>"$UI_LOG" && [ ! -e "$_prev" ]; then
        ok "ui: removed the pre-rename shell bundle $_prev"
    else
        warn "ui: could not remove the pre-rename shell bundle $_prev — see $UI_LOG"
    fi
}

install_shell_app() {
    UI_SHELL_STATUS=skipped; UI_SHELL_DETAIL=""
    if [ "$(uname -s)" != "Darwin" ]; then
        UI_SHELL_DETAIL="shell skipped (not macOS)"
        return 0
    fi
    if ! command -v swiftc >/dev/null 2>&1 || ! bash "$REPO_ROOT/shell/build.sh" --check-toolchain >/dev/null 2>&1; then
        warn "ui: swift toolchain missing/too old — shell app not built (xcode-select --install, then bash install.sh)"
        UI_SHELL_DETAIL="shell skipped (no swift toolchain)"
        return 0
    fi
    _t0="$(ui_now)"
    ZAI_PORT="${SERVER_PORT:-}" ui_run_with_timeout "$UI_BUDGET_S" bash "$REPO_ROOT/shell/build.sh" >> "$UI_LOG" 2>&1
    _rc=$?
    if [ "$_rc" -ne 0 ]; then
        warn "ui: shell/build.sh failed (exit $_rc) — see $UI_LOG"
        ui_log_tail
        UI_SHELL_STATUS=fail; UI_SHELL_DETAIL="shell fail (shell/build.sh exit $_rc)"
        return 0
    fi
    _src="$REPO_ROOT/shell/build/$UI_APP_NAME.app"
    if [ ! -d "$_src" ]; then
        warn "ui: shell/build.sh exited 0 but $_src is missing"
        UI_SHELL_STATUS=fail; UI_SHELL_DETAIL="shell fail (no bundle after build)"
        return 0
    fi
    _dest_dir="$UI_APPS_DIR"
    [ -w "$_dest_dir" ] && ui_retire_legacy_app "$_dest_dir"
    if [ ! -w "$_dest_dir" ] || { [ -e "$_dest_dir/$UI_APP_NAME.app" ] && [ ! -w "$_dest_dir/$UI_APP_NAME.app" ]; }; then
        warn "ui: $_dest_dir not writable — installing the shell app to ~/Applications"
        _dest_dir="$HOME/Applications"
        mkdir -p "$_dest_dir"
        ui_retire_legacy_app "$_dest_dir"
    fi
    if [ -d "$_dest_dir/$UI_APP_NAME.app" ] && [ "$(ui_bundle_id "$_dest_dir/$UI_APP_NAME.app")" = "$UI_LEGACY_BUNDLE_ID" ]; then
        warn "ui: the legacy app still occupies $_dest_dir/$UI_APP_NAME.app — shell not installed"
        UI_SHELL_STATUS=fail; UI_SHELL_DETAIL="shell fail (legacy app still on the product path in $_dest_dir)"
        return 0
    fi
    # stage-then-swap: a failed copy never leaves a half-bundle; ditto keeps the signature
    _staged="$_dest_dir/.$UI_APP_NAME.app.staged"
    rm -rf "$_staged"
    if ! ditto "$_src" "$_staged" >> "$UI_LOG" 2>&1; then
        rm -rf "$_staged"
        warn "ui: could not copy the shell bundle into $_dest_dir — installed app left untouched"
        UI_SHELL_STATUS=fail; UI_SHELL_DETAIL="shell fail (ditto into $_dest_dir failed)"
        return 0
    fi
    rm -rf "$_dest_dir/$UI_APP_NAME.app"
    mv "$_staged" "$_dest_dir/$UI_APP_NAME.app"
    UI_APP_PATH="$_dest_dir/$UI_APP_NAME.app"
    UI_SHELL_INSTALLED=1
    ui_remove_previous_shell "$_dest_dir"
    [ "$_dest_dir" = "$UI_APPS_DIR" ] || ui_remove_previous_shell "$UI_APPS_DIR"
    _shell_s=$(( $(ui_now) - _t0 ))
    UI_SHELL_STATUS=ok
    UI_SHELL_DETAIL="shell ok (${_shell_s}s → $UI_APP_PATH)"
    [ "$UI_LEGACY_MOVED" -eq 1 ] && UI_SHELL_DETAIL="$UI_SHELL_DETAIL; legacy app moved to \"$UI_LEGACY_APP_NAME.app\""
    ok "ui: shell app built + installed to $UI_APP_PATH (${_shell_s}s)"
    if pgrep -x "$UI_EXEC_NAME" >/dev/null 2>&1 && [ "$NON_INTERACTIVE" -eq 0 ]; then
        info "ui: the shell app is running — quit + reopen it to pick up this build: open \"$UI_APP_PATH\""
    fi
}

install_ui() {
    echo "==> 5. board UI (web/dist + shell app)"
    ui_log_begin
    _ui_t0="$(ui_now)"
    install_web_ui
    install_shell_app
    _ui_s=$(( $(ui_now) - _ui_t0 ))
    _status=skipped
    if [ "$UI_WEB_STATUS" = fail ] || [ "$UI_SHELL_STATUS" = fail ]; then
        _status=fail
    elif [ "$UI_WEB_STATUS" = ok ] || [ "$UI_SHELL_STATUS" = ok ]; then
        _status=ok
    elif [ "$UI_WEB_STATUS" = skipped_tcc ]; then
        _status=skipped_tcc
    fi
    info "ui step: $_status in ${_ui_s}s"
    report_step "ui" "$_status" "$UI_WEB_DETAIL; $UI_SHELL_DETAIL; ${_ui_s}s total"
}

# §56.5 relaunch: --non-interactive only, only when this run installed a new
# bundle and the shell is running, after the server agent came back. The
# engineHandoff preference is written BEFORE the SIGTERM (§61.8): the quitting
# shell then leaves its engine recording and the relaunched shell adopts it.
# `open -g … --args --background` relaunches without stealing focus (D38).
# --from-upgrade leaves the relaunch to scripts/upgrade.sh (§79).
relaunch_shell_app() {
    [ "$NON_INTERACTIVE" -eq 1 ] || return 0
    if [ "${FROM_UPGRADE:-0}" -eq 1 ]; then
        info "ui: shell relaunch left to scripts/upgrade.sh (§79)"
        return 0
    fi
    [ "$UI_SHELL_INSTALLED" -eq 1 ] || return 0
    [ -n "$UI_APP_PATH" ] || return 0
    pgrep -x "$UI_EXEC_NAME" >/dev/null 2>&1 || return 0
    defaults write "$UI_BUNDLE_ID" "$UI_ENGINE_HANDOFF_KEY" -string "$(date -u +%FT%TZ)" 2>/dev/null || true
    pkill -TERM -x "$UI_EXEC_NAME" 2>/dev/null || true
    for _ in 1 2 3 4 5 6 7 8 9 10; do
        pgrep -x "$UI_EXEC_NAME" >/dev/null 2>&1 || break
        sleep 0.5
    done
    if pgrep -x "$UI_EXEC_NAME" >/dev/null 2>&1; then
        pkill -KILL -x "$UI_EXEC_NAME" 2>/dev/null || true
        sleep 0.5
    fi
    if open -g "$UI_APP_PATH" --args --background 2>/dev/null; then
        ok "ui: relaunched the shell app on the new build (server agent reloaded first)"
    else
        warn "ui: relaunch failed — start it manually: open \"$UI_APP_PATH\""
    fi
}

# The installer never signals the screenpipe engine (§61.8): say what it found.
note_engine() {
    _pids="$(pgrep -f 'screenpipe.*[r]ecord' 2>/dev/null | tr '\n' ' ' | sed 's/ *$//')"
    if [ -n "$_pids" ]; then
        info "engine running (pid $_pids): left untouched, the app adopts it"
    else
        info "engine not running: the app starts it when recording is on"
    fi
}

# ------------------------------------------------------------------ server agent
# Step 6: the one LaunchAgent (§78). --no-launchd records the literal marker
# act/lib/fresh_install.py reads (`--no-launchd` at the start of the detail).
install_server_agent() {
    if [ "$NO_LAUNCHD" -eq 1 ]; then
        echo "==> 6. board server agent — skipped (--no-launchd)"
        report_step "launchd" "skipped" "--no-launchd: no agent loaded"
        return 0
    fi
    echo "==> 6. board server agent ($SERVER_LABEL)"
    mkdir -p "$LA_DIR"
    _src="$REPO_ROOT/act/launchd/$SERVER_LABEL.plist"
    _dest="$LA_DIR/$SERVER_LABEL.plist"
    if [ ! -f "$_src" ]; then
        warn "template $_src missing"
        report_step "launchd" "fail" "no template for $SERVER_LABEL"
        return 0
    fi
    if ! launchd_label_loaded "$SERVER_LABEL" && board_server_answering "$SERVER_PORT"; then
        warn "a board server not managed by launchd answers on 127.0.0.1:$SERVER_PORT — quit it so $SERVER_LABEL can take the port"
        report_step "board_server_port" "warn" "127.0.0.1:$SERVER_PORT answered before $SERVER_LABEL loaded"
    fi
    launchd_unload "$_dest" "$SERVER_LABEL"
    cap_launchd_log "$SERVER_LABEL"
    render_launchd_plist "$_src" "$_dest"
    if launchd_load "$_dest"; then
        ok "loaded $SERVER_LABEL (python=${RUNTIME_PY:-python3} port=$SERVER_PORT)"
        LOADED_LABELS="$SERVER_LABEL"
        sleep 2
        verify_launchd_agent "$SERVER_LABEL"
        report_step "launchd" "ok" "1 agent loaded ($SERVER_LABEL)"
    else
        warn "failed to load $SERVER_LABEL (Full Disk Access for the daemon python? see $LOG_DIR/server.launchd.log)"
        report_step "launchd" "fail" "$SERVER_LABEL failed to load"
    fi
    _orphans="$(launchd_orphans | tr '\n' ' ' | sed 's/ *$//')"
    if [ -n "$_orphans" ]; then
        warn "launchd agent(s) of ours outside the closed footprint (§78): $_orphans"
        info "  launchctl bootout gui/$UID_NUM/<label>; rm ~/Library/LaunchAgents/<label>.plist"
        report_step "launchd_orphans" "warn" "$_orphans"
    fi
    relaunch_shell_app
}

# ------------------------------------------------------------------ crontab
# Step 7 (§18): ours = the ingest chain + the 1.x module lines; the new crontab
# keeps every other line verbatim and ends with the one chain line.
compose_crontab() { # stdin = current crontab, stdout = the new one
    { grep -Ev "$CRON_OURS_RE" || true; } | grep -v '^[[:space:]]*$' || true
    printf '%s\n' "$INGEST_CHAIN"
}

# The crontab write and its verdict (§23 / §56.5): "Operation not permitted" =
# TCC refused the rewrite in this session → cron=skipped_tcc (not a failed step,
# doctor `cron write access` makes it visible); any other failure = cron=fail.
apply_crontab() {
    if [ "$NEW_CRON" = "$CURRENT_CRON" ]; then
        report_step "cron" "ok" "already installed"
        return 0
    fi
    if _cron_err="$(printf '%s\n' "$NEW_CRON" | grep -v '^[[:space:]]*$' | crontab - 2>&1)"; then
        ok "crontab rewritten (other lines preserved)"
        report_step "cron" "ok" "ingest chain installed${CRON_STRIPPED:+; removed $CRON_STRIPPED retired line(s)}"
        return 0
    fi
    [ -n "$_cron_err" ] && printf '%s\n' "$_cron_err" >&2
    warn "crontab update failed — add this line manually with 'crontab -e':"
    info "$INGEST_CHAIN"
    case "$_cron_err" in
        *"Operation not permitted"*)
            warn "crontab is TCC-blocked in this session — grant Full Disk Access to $CRON_PY, then rerun bash install.sh"
            report_step "cron" "skipped_tcc" "crontab rewrite refused (Operation not permitted — TCC); grant Full Disk Access to $CRON_PY"
            ;;
        *)
            report_step "cron" "fail" "crontab rewrite failed"
            ;;
    esac
    return 0
}

install_cron_lines() {
    if [ "$NO_LAUNCHD" -eq 1 ]; then
        echo "==> 7. crontab — skipped (--no-launchd)"
        report_step "cron" "skipped" "--no-launchd: crontab not touched"
        return 0
    fi
    echo "==> 7. crontab — the ingest chain (§18)"
    chmod +x "$REPO_ROOT"/ingest/*.sh "$REPO_ROOT"/ingest/*.command 2>/dev/null || true
    CURRENT_CRON="$(crontab -l 2>/dev/null || true)"
    CRON_STRIPPED="$(printf '%s\n' "$CURRENT_CRON" | grep -E "$CRON_OURS_RE" | grep -cvF "$INGEST_CHAIN" || true)"
    [ "$CRON_STRIPPED" = "0" ] && CRON_STRIPPED=""
    NEW_CRON="$(printf '%s\n' "$CURRENT_CRON" | compose_crontab)"
    [ -n "$CRON_STRIPPED" ] && info "removing $CRON_STRIPPED retired / outdated line(s) of ours (radar, digest, telemetry, old chain)"
    apply_crontab
}

# ------------------------------------------------------------------ report
write_install_report() {
    _rpy="${RUNTIME_PY:-${PY:-}}"
    { [ -n "$_rpy" ] && [ -x "$_rpy" ]; } || _rpy="$(command -v python3 || true)"
    if [ -z "$_rpy" ]; then
        warn "no python3 — skipped state/install_report.json"
        return 0
    fi
    _mode=interactive
    [ "$NON_INTERACTIVE" -eq 1 ] && _mode=non-interactive
    [ "$FROM_UPGRADE" -eq 1 ] && _mode=upgrade
    if printf '%s' "$REPORT_STEPS" | (cd "$REPO_ROOT" && AIASSISTANT_HOME="$REPO_ROOT" \
        "$_rpy" -m act.lib.install_report --mode "$_mode" --steps-stdin \
        --agents "$LOADED_LABELS" >/dev/null 2>&1); then
        ok "state/install_report.json written"
    else
        warn "could not write state/install_report.json (non-fatal)"
    fi
}

# ------------------------------------------------------------------ steps 1–3
check_dependencies() {
    echo "==> 1. dependency checks"
    if command -v claude >/dev/null 2>&1; then
        ok "claude found: $(command -v claude)"
    elif [ "$NON_INTERACTIVE" -eq 1 ]; then
        warn "claude CLI not found — ingest and recaps stay idle until Claude Code is installed"
    else
        echo "  [ERR ] claude CLI not found (REQUIRED). Install Claude Code first, then re-run." >&2
        exit 1
    fi
    if [ "$NON_INTERACTIVE" -eq 1 ]; then
        info "swift toolchain not required here — the shell app (step 5) is skipped when swiftc is absent"
    elif bash "$REPO_ROOT/shell/build.sh" --check-toolchain; then
        ok "swift toolchain: $(swiftc --version 2>/dev/null | head -n1)"
    else
        echo "  [ERR ] Swift toolchain check failed (see message above), then re-run this script." >&2
        exit 1
    fi
    PY="$(command -v python3 || true)"
    if [ -z "$PY" ]; then
        echo "  [ERR ] python3 not found (xcode-select --install provides /usr/bin/python3)." >&2
        exit 1
    fi
    ok "python3 found: $PY"
    if "$PY" -c "import yaml" >/dev/null 2>&1; then
        ok "PyYAML available"
    elif "$PY" -m pip install --user pyyaml >/dev/null 2>&1 \
        || "$PY" -m pip install --user --break-system-packages pyyaml >/dev/null 2>&1; then
        ok "PyYAML installed"
    else
        echo "  [ERR ] PyYAML install failed (REQUIRED)." >&2
        info "fix: $PY -m pip install --user --break-system-packages pyyaml"
        exit 1
    fi
    if command -v npx >/dev/null 2>&1; then
        ok "node/npx found: $(command -v npx) — the recording engine runs via npx"
    else
        warn "node/npx not found — nothing records without it (brew install node)"
    fi
}

setup_config_and_runtime() {
    echo "==> 2. config, interpreter, claude, isolation dirs, API key"
    if [ -f "$REPO_ROOT/config.yaml" ]; then
        ok "config.yaml already exists (left untouched)"
        report_step "config" "ok" "existing config.yaml kept"
    else
        cp "$REPO_ROOT/config.example.yaml" "$REPO_ROOT/config.yaml"
        ok "created config.yaml from config.example.yaml"
        report_step "config" "ok" "created from config.example.yaml"
    fi
    if [ ! -f "$REPO_ROOT/config/redaction_terms.txt" ] && [ -f "$REPO_ROOT/config/redaction_terms.example.txt" ]; then
        cp "$REPO_ROOT/config/redaction_terms.example.txt" "$REPO_ROOT/config/redaction_terms.txt"
        ok "created config/redaction_terms.txt from template (gitignored)"
    fi
    stamp_version
    mkdir -p "$REPO_ROOT/config"
    _saved_ifs="$IFS"
    IFS='
'
    # shellcheck disable=SC2046 # newline word-splitting the candidate list is the point
    pick_daemon_python $(daemon_python_candidates) || true
    IFS="$_saved_ifs"
    RUNTIME_PY="$DAEMON_PY"
    if [ -n "${AIASSISTANT_PYTHON:-}" ] && [ "$RUNTIME_PY" != "$AIASSISTANT_PYTHON" ]; then
        warn "AIASSISTANT_PYTHON=$AIASSISTANT_PYTHON did not pass the daemon interpreter gates — ignored"
    fi
    if [ -n "$RUNTIME_PY" ]; then
        printf '{"python": "%s"}\n' "$RUNTIME_PY" > "$REPO_ROOT/config/runtime.json"
        ok "config/runtime.json -> $RUNTIME_PY"
        if [ -n "$DAEMON_PY_NOTE" ]; then
            warn "$DAEMON_PY_NOTE"
            report_step "runtime_python" "ok" "$RUNTIME_PY ($DAEMON_PY_NOTE)"
        else
            report_step "runtime_python" "ok" "$RUNTIME_PY"
        fi
    else
        echo "  [ERR ] no python3 with PyYAML found — the server agent and the cron chain need one" >&2
        info "fix: $(command -v python3 || echo python3) -m pip install --user --break-system-packages pyyaml"
        report_step "runtime_python" "fail" "no candidate python3 can import yaml"
    fi
    resolve_claude_login_bin
    if [ -n "$CLAUDE_LOGIN_BIN" ]; then
        ok "claude: $CLAUDE_LOGIN_BIN (login-shell resolution)"
        report_step "claude_bin" "ok" "$CLAUDE_LOGIN_BIN"
    else
        warn "claude not resolvable from the login shell — the cron PATH falls back to ~/.local/bin"
        report_step "claude_bin" "missing"
    fi
    if [ "$(uname -s 2>/dev/null)" = "Darwin" ]; then
        refresh_stable_claude "$CLAUDE_LOGIN_BIN"
    else
        report_step "stable_claude" "skipped" "not macOS"
    fi
    if mkdir -p "$APP_SUPPORT_DIR" && printf '%s\n' "$REPO_ROOT" > "$APP_SUPPORT_DIR/home.txt"; then
        ok "home pointer -> $APP_SUPPORT_DIR/home.txt"
    else
        warn "could not write $APP_SUPPORT_DIR/home.txt — the app assumes ~/Projects/zelin-ai-assistant"
    fi
    ensure_claude_config_dirs
    install_api_key
}

make_state_dirs() {
    echo "==> 3. state directories"
    if mkdir -p "$REPO_ROOT/state/recap"; then
        ok "state/ ready"
        report_step "state_dirs" "ok"
    else
        report_step "state_dirs" "fail" "mkdir state/ failed"
    fi
}

run_doctor() {
    if [ "$NON_INTERACTIVE" -eq 1 ]; then
        echo "==> 8. diagnostics — skipped (non-interactive; run: bash install.sh --check)"
    elif [ -n "${RUNTIME_PY:-}" ] && [ -x "${RUNTIME_PY:-}" ]; then
        echo "==> 8. post-install diagnostics (python -m act.doctor)"
        if ! (cd "$REPO_ROOT" && AIASSISTANT_HOME="$REPO_ROOT" "$RUNTIME_PY" -m act.doctor); then
            warn "doctor reported problems above — fix them, then re-check: bash install.sh --check"
        fi
    else
        echo "==> 8. diagnostics — skipped (no usable python3); run later: bash install.sh --check"
    fi
}

closing_banner() {
    cat <<EOF

==============================================
 Install complete. What is left is yours:
   bash install.sh --check --fresh-install   # the live, path-exact version of this list
==============================================
 1. Open "Zelin's AI Assistant" from /Applications (or ~/Applications), or
    http://127.0.0.1:${SERVER_PORT:-47820}/ in a browser. Turn on recording and, if you want
    it after a restart, "Launch at login" in the app.
 2. Anthropic API key — the only credential (headless claude never reads your Claude Code
    login, CONTRACT §78): Settings > Credentials, or config/secrets/anthropic-api-key.txt (0600).
 3. Full Disk Access (System Settings > Privacy & Security > Full Disk Access), per binary:
      - the daemon interpreter in config/runtime.json — the board server
      - ${STABLE_CLAUDE_BIN}
      - /usr/sbin/cron — the ingest chain reads the Obsidian vault
    Needed when the repo or the vault sits under ~/Documents, ~/Desktop or an external volume.
 4. Screen Recording + Microphone for com.zelin.ai-board: the app's Permissions page.
 5. Upgrades: the About page's "Upgrade now" (scripts/upgrade.sh, CONTRACT §79).
==============================================
EOF
}

usage() {
    echo "usage: bash install.sh [--non-interactive] [--no-launchd] [--from-upgrade] | --check [doctor flags]" >&2
}

main() {
    # --check [doctor flags…]: delegate to act.doctor (exit = number of FAILs).
    if [ "${1:-}" = "--check" ]; then
        shift
        _dpy="$(pick_python "$(pinned_python)" "$(command -v python3 || echo /usr/bin/python3)" /usr/bin/python3 || true)"
        [ -n "$_dpy" ] || _dpy="$(command -v python3 || echo /usr/bin/python3)"
        cd "$REPO_ROOT" || exit 1
        AIASSISTANT_HOME="$REPO_ROOT" exec "$_dpy" -m act.doctor "$@"
    fi
    NON_INTERACTIVE=0; NO_LAUNCHD=0; FROM_UPGRADE=0
    for _arg in "$@"; do
        case "$_arg" in
            --non-interactive) NON_INTERACTIVE=1 ;;
            --no-launchd) NO_LAUNCHD=1 ;;
            --from-upgrade) FROM_UPGRADE=1; NON_INTERACTIVE=1 ;;
            --pkg-postinstall)
                # CONTRACT §74 v2.0.0: no .pkg ships, so its postinstall mode always
                # refuses before any side effect (exit 3, no §23 report).
                echo "install.sh --pkg-postinstall: no .pkg ships since v2.0.0 — nothing changed (CONTRACT §74)." >&2
                exit 3 ;;
            --reinstall-agent)
                echo "install.sh: --reinstall-agent was retired in v2.0.0 (full text at git tag full)" >&2
                usage; exit 2 ;;
            *)
                echo "install.sh: unknown flag '$_arg'" >&2
                usage; exit 2 ;;
        esac
    done

    echo "=============================================="
    echo " Zelin's AI Assistant — installer"
    echo " repo: $REPO_ROOT"
    echo "=============================================="
    echo ""; check_dependencies
    echo ""; setup_config_and_runtime
    echo ""; make_state_dirs
    echo ""; migrate_from_1x
    SERVER_PORT="$(server_port)"
    echo ""; install_ui
    echo ""; install_server_agent
    note_engine
    # cron runs outside the login shell: the same validated interpreter (§55) and
    # the login shell's claude directory first on PATH.
    _saved_ifs="$IFS"
    IFS='
'
    # shellcheck disable=SC2046 # newline word-splitting the candidate list is the point
    CRON_PY="$(pick_python "${RUNTIME_PY:-}" $(daemon_python_candidates) || echo "${PY:-python3}")"
    IFS="$_saved_ifs"
    CRON_CLAUDE_DIR="$HOME/.local/bin"
    [ -n "$CLAUDE_LOGIN_BIN" ] && CRON_CLAUDE_DIR="$(dirname "$CLAUDE_LOGIN_BIN")"
    # process-screenpipe.sh exits 3 when another run holds the lock (a skip, not a
    # failure). AIASSISTANT_CRON=1 arms the FDA probe in screenpipe-export.sh (§25).
    INGEST_CHAIN="*/30 * * * * cd $REPO_ROOT && export PATH=$CRON_CLAUDE_DIR:\$PATH AIASSISTANT_CRON=1 && { ./ingest/screenpipe-export.sh && ./ingest/screenpipe-cleanup.sh && { ./ingest/process-screenpipe.sh || [ \$? -eq 3 ]; }; } >> $REPO_ROOT/state/ingest.cron.log 2>&1"
    echo ""; install_cron_lines
    echo ""; run_doctor
    echo ""; write_install_report

    if [ "$NON_INTERACTIVE" -eq 1 ]; then
        _failed="$(failed_deploy_steps)"
        _n="$(printf '%s' "$_failed" | grep -c . || true)"
        if [ "$_n" -gt 0 ]; then
            echo "install.sh --non-interactive: $_n failed step(s): $(printf '%s' "$_failed" | cut -d= -f1 | tr '\n' ' ')"
        else
            echo "install.sh --non-interactive: ok (v${STAMPED_VERSION:-?})"
        fi
        exit "$_n"
    fi
    closing_banner
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
    main "$@"
fi
