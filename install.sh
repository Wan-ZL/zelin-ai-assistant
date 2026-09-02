#!/bin/bash
# One-click installer for Zelin's AI Assistant (Act pipeline + board UI).
#
# What it does:
#   1. dependency checks (claude/swift required; python3/PyYAML; node+npx for
#      the recording engine; obsidian/gh optional)
#   2. config.example.yaml -> config.yaml (if absent), the version stamp
#      (act/_version.py from the git tag — CONTRACT §56.1; written before any
#      `import act`), runtime/home pointers
#   3. create state/ and state/inbox/
#   4. build + install the legacy Mac app (mac/build.sh --install; frozen, D3)
#   4b. `ui` step (CONTRACT §56.5): build the web board (web/ -> web/dist via
#      npm ci + npm run build) and the board shell app (shell/build.sh) and
#      install the bundle to /Applications — each half skipped with a warn
#      when its toolchain (node+npm / swiftc) is absent, never a failure.
#      Never touches the legacy "Zelin's AI Assistant.app".
#   5. install launchd agents (actd + board server resident, radars periodic):
#      render the plist templates (replace /Users/YOURUSERNAME placeholders
#      with the real python/repo/home paths + the login shell's claude
#      directory, which goes FIRST on the daemon PATH; ZAI_PORT from
#      config.yaml server.port), load them, then verify they actually spawn.
#      In --non-interactive mode a running shell app is relaunched here, AFTER
#      the server agent came back (§56.5 relaunch rule).
#   6. unify the user crontab (CONTRACT §18): screenpipe ingest chain now runs
#      the repo's ingest/ scripts + `python -m act.radar --once`, and a daily
#      09:07 `python -m act.digest` (self-gated by digest.frequency, §17)
#   7. run the post-install diagnostics (python -m act.doctor)
#
# Run from anywhere; it locates the repo root via its own path.
#
# --pkg-postinstall: non-interactive mode used by the .pkg installer's
#   postinstall (mac/package.sh). Skips dependency checks (nothing to ask a
#   user for) and the Mac app build/install (the pkg already installed it),
#   but does everything else: config files, state dirs, the launchd agents
#   (actd + radars per config — without them the product is inert) and the
#   ingest cron chain. Every run (both modes) ends by writing
#   state/install_report.json (CONTRACT §23) with what actually happened.
#
# --check: run the post-install doctor (python -m act.doctor) and exit with
#   the number of failing checks. Installs/changes nothing.
#
# --non-interactive: the mode scripts/auto-deploy.sh runs (CONTRACT §56). Same
#   steps as the interactive run, but it can never stop to ask: a missing
#   claude only warns, the doctor step is left to the caller (it gates the
#   deploy and decides on rollback), the closing "next steps" banner is
#   replaced by a one-line summary, and the EXIT CODE is the number of failed
#   steps. It NEVER builds or installs the legacy Mac app (step 4 is skipped,
#   §56.5): D3 froze it, and `mac/build.sh --install` quits + relaunches the
#   running instance — screenpipe is its direct child (RunningBoard reaps
#   orphans) and live captions live inside it, so an unattended rebuild would
#   kill a recording or a meeting's captions at whatever hour a merge lands.
#   Only a hand-run `bash install.sh` (the owner picking the moment) rebuilds
#   that app. The board UI (step 4b) IS built and installed in this mode —
#   merge = deploy includes the UI (2026-09-02: the UI had never been deployed
#   because nothing built it) — the shell spawns nothing (the server is a
#   launchd agent), so relaunching it costs nobody a recording. The §23 report
#   records mode "non-interactive".
set -uo pipefail

# Physical path of a directory — every symlink resolved (CONTRACT §55).
# `cd … && pwd -P` is the portable realpath: macOS only grew /usr/bin/realpath
# recently and the pkg postinstall runs with a minimal PATH.
physical_path() {
    ( cd "$1" 2>/dev/null && pwd -P ) || printf '%s' "$1"
}

# `pwd -P`, NOT `pwd`: a convenience symlink in the invocation path (the
# 2026-08-31 incident: ~/Projects -> /Volumes/Storage/Server/Projects) would
# otherwise be baked into PYTHONPATH / AIASSISTANT_HOME / the home pointer,
# and launchd-spawned processes are TCC-denied on the external volume through
# that path shape — every agent died on "No module named 'act'".
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$SCRIPT_DIR"
LA_DIR="$HOME/Library/LaunchAgents"

# CONTRACT §19/§55 — an interpreter is only usable for the daemons if it is an
# ABSOLUTE path that can really `import yaml`. 2026-08-31: install.sh pinned
# /opt/homebrew/bin/python3 (3.14, no PyYAML), so even a correctly rendered
# PYTHONPATH left every launchd agent exiting 1 in a KeepAlive loop.
py_imports_yaml() {
    case "${1:-}" in /*) ;; *) return 1 ;; esac
    [ -x "$1" ] || return 1
    "$1" -c "import yaml" >/dev/null 2>&1
}

# First candidate that passes py_imports_yaml wins; prints nothing and returns
# 1 when none do (callers report that loudly rather than pinning a dud).
pick_python() {
    for _cand in "$@"; do
        if py_imports_yaml "$_cand"; then printf '%s' "$_cand"; return 0; fi
    done
    return 1
}

# CONTRACT §55 — LAUNCHD VIABILITY. `import yaml` is necessary but NOT
# sufficient: TCC grants file access PER BINARY, and a launchd-spawned process
# is its own responsible process (an interactive shell lends its own grant to
# everything it starts, which is why every candidate reads the repo fine from
# a terminal). 2026-08-31 live deployment: the repo sits on /Volumes/…,
# /usr/bin/python3 could read it under launchd and /opt/homebrew/bin/python3
# could not — both imported yaml, so the yaml-only gate pinned the blind one
# and every agent died on "No module named 'act'" with a perfectly rendered
# PYTHONPATH. Measured: the denied interpreter raises PermissionError(EPERM)
# while scanning sys.path, which import machinery reports as the missing module.
#
# The only honest probe is to ask launchd itself, so this loads a throwaway
# agent that does exactly what the daemons do — insert the repo on sys.path and
# `import act` — and reads its verdict from a sentinel file (portable across
# macOS versions; no `launchctl print` output parsing). Sub-second in practice.
#   0 = viable, 1 = NOT viable, 2 = inconclusive (no launchd / probe disabled)
# Callers must treat 2 as "unknown", never as a rejection.
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
    # The payload takes repo + sentinel as argv so no path is ever interpolated
    # into python source; only the plist XML needs escaping.
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
        return 2  # launchd refused the job itself — tells us nothing about $_py
    fi
    _probe_n=0
    while [ ! -s "$_probe_verdict" ] && [ "$_probe_n" -lt 60 ]; do
        sleep 0.25; _probe_n=$((_probe_n + 1))
    done
    LAUNCHD_PROBE_DETAIL="$(cat "$_probe_verdict" 2>/dev/null || true)"
    launchctl bootout "gui/$UID_NUM/$_probe_label" >/dev/null 2>&1 \
        || launchctl unload "$_probe_pl" >/dev/null 2>&1 || true
    rm -rf "$_probe_dir"
    # No sentinel at all = the interpreter never got far enough to write one
    # (also a real failure: launchd could not run it).
    [ "$LAUNCHD_PROBE_DETAIL" = "ok" ] && return 0
    [ -z "$LAUNCHD_PROBE_DETAIL" ] && LAUNCHD_PROBE_DETAIL="fail: produced no verdict under launchd"
    return 1
}

# Daemon interpreter candidates, most-preferred first (CONTRACT §55).
#
# ORDERING RATIONALE: /usr/bin/python3 is the Apple-shipped system interpreter,
# and on a normally-used Mac it is the one binary that already holds the user's
# file-access grants — Homebrew/miniconda pythons are separate binaries that
# each need their OWN TCC grant before a launchd job spawned from them can read
# anything outside $HOME. So when the repo lives outside $HOME (external volume,
# network share — precisely where per-binary TCC bites), the system python is
# ranked ABOVE them; inside $HOME the historical order is kept, because there is
# no TCC boundary to cross and a user's richer python is the better default.
# $AIASSISTANT_PYTHON stays first in both: an explicit override outranks a guess.
daemon_python_candidates() {
    if repo_outside_home; then
        printf '%s\n' "${AIASSISTANT_PYTHON:-}" /usr/bin/python3 \
            "$(pinned_python)" "$HOME/miniconda3/bin/python3" "${PY:-}"
    else
        printf '%s\n' "${AIASSISTANT_PYTHON:-}" "$(pinned_python)" \
            "$HOME/miniconda3/bin/python3" "${PY:-}" /usr/bin/python3
    fi
}

# Is the repo outside $HOME? That is the TCC-sensitive shape (see the ordering
# rationale above). Compares PHYSICAL paths — a symlink into /Volumes must not
# read as "inside $HOME".
repo_outside_home() {
    _rh="$(physical_path "$HOME")"
    _rr="$(physical_path "$REPO_ROOT")"
    case "$_rr" in "$_rh"|"$_rh"/*) return 1 ;; *) return 0 ;; esac
}

# The daemon interpreter: first candidate that passes BOTH gates (§55) —
# `import yaml`, then "can actually import act when launchd spawns it".
# Sets DAEMON_PY (the winner) and DAEMON_PY_NOTE (empty when both gates passed
# cleanly); returns 1 when no candidate is usable at all. Globals rather than
# stdout because the probe's verdict has to survive back to the caller, and a
# command substitution would run the whole thing in a subshell.
# When the launchd gate rejects every yaml-capable candidate (or cannot run at
# all) we fall back to the first yaml-capable one and say why, rather than
# pinning nothing: yaml-capable is still strictly better than a PATH guess.
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

# The interpreter pinned by a previous run (CONTRACT §19), "" when unpinned.
pinned_python() {
    [ -f "$REPO_ROOT/config/runtime.json" ] || return 0
    sed -n 's/.*"python"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' \
        "$REPO_ROOT/config/runtime.json"
}

# --check: delegate to act/doctor.py — re-validates every runtime assumption
# (deps, key resolution, launchd agents alive, cron lines, dashboard freshness)
# symptom-first, one fix line per finding. Exit code = number of FAILs.
if [ "${1:-}" = "--check" ]; then
    # prefer the pinned daemon interpreter — it is what launchd/cron run
    DOCTOR_PY="$(command -v python3 || echo /usr/bin/python3)"
    RJ_PY="$(pick_python "$(pinned_python)" "$DOCTOR_PY" /usr/bin/python3 || true)"
    [ -n "$RJ_PY" ] && DOCTOR_PY="$RJ_PY"
    cd "$REPO_ROOT" || exit 1
    AIASSISTANT_HOME="$REPO_ROOT" exec "$DOCTOR_PY" -m act.doctor
fi

PKG_POSTINSTALL=0
[ "${1:-}" = "--pkg-postinstall" ] && PKG_POSTINSTALL=1
# CONTRACT §56 — never-prompting mode for scripts/auto-deploy.sh (see header).
NON_INTERACTIVE=0
[ "${1:-}" = "--non-interactive" ] && NON_INTERACTIVE=1

ok()   { printf "  [ ok ] %s\n" "$1"; }
warn() { printf "  [warn] %s\n" "$1"; }
info() { printf "  [info] %s\n" "$1"; }

# install report (CONTRACT §23) — newline-separated "name=status[:detail]"
# lines accumulated as a plain string (macOS bash 3.2 + set -u make empty
# arrays hazardous), written by write_install_report at the end of the run.
REPORT_STEPS=""
LOADED_LABELS=""
report_step() { # $1=name $2=status [$3=detail]
    REPORT_STEPS="${REPORT_STEPS}$1=$2${3:+:$3}
"
}

# --non-interactive verdict (§56): the report lines whose status is fail,
# minus `app` — the frozen legacy Mac app (D3): its build failing leaves the
# installed app untouched, and rolling the deploy back would not fix it.
# (Since §56.5 that mode never builds the app at all — install_mac_app records
# `app=skipped` — so the exclusion is a belt for a stray fail line, kept
# because the exit code is the deploy verdict and must never hinge on it.)
# `cron=skipped_tcc`（apply_crontab：launchd 会话被 TCC 拒写 crontab）同理不算
# fail——环境问题回滚治不了，还会毒掉 sha（2026-09-02 v0.48.12 实战）。
# The `ui` step (§56.5) is NOT excluded: `ui=skipped` (toolchain absent) is
# not a fail line, `ui=fail` (web/shell build broke) is — a merge that breaks
# the UI build rolls back like any other. Printed one per line; the exit code
# is their count.
failed_deploy_steps() {
    printf '%s' "$REPORT_STEPS" | grep -E '^[^=]+=fail' | grep -v '^app=' || true
}

# Step 4 — build + install the Mac app (CONTRACT §23 step `app`; §56.5).
# A function so tests can run it against a fake mac/build.sh. Skipped by the
# .pkg postinstall (the pkg installed the app) and by --non-interactive:
# auto-deploy must NEVER rebuild the frozen legacy app (D3) — build.sh quits
# and relaunches the running instance, taking screenpipe (its direct child)
# and live captions down with it, and a `swift build` + `codesign` under
# launchd can hang on a keychain prompt nobody is there to click.
install_mac_app() {
    if [ "$PKG_POSTINSTALL" -eq 1 ]; then
        echo "==> 4. build + install Mac app — skipped (the .pkg already installed it)"
        report_step "app" "skipped" "installed by the .pkg"
    elif [ "$NON_INTERACTIVE" -eq 1 ]; then
        echo "==> 4. build + install Mac app — skipped (--non-interactive never rebuilds the frozen legacy app; run bash install.sh by hand)"
        report_step "app" "skipped" "non-interactive never rebuilds the app (D3); bash install.sh to rebuild"
    else
        echo "==> 4. build + install Mac app"
        if bash "$REPO_ROOT/mac/build.sh" --install; then
            ok "Mac app built + installed"
            report_step "app" "ok" "built and installed"
        else
            warn "Mac app build failed — see output above"
            report_step "app" "fail" "mac/build.sh --install failed"
        fi
    fi
}

# --------------------------------------------------------------------------
# Step 4b — the board UI (CONTRACT §56.5 `ui` step; §54 shell).
#
# Two halves, each independently `ok | skipped | fail` (web also `skipped_tcc`):
#   web:   node+npm present → mirror web/ into a build dir under $HOME
#          (ui_web_build_dir: TCC — see below) → `npm ci` (only when that
#          dir's node_modules is missing or package-lock.json changed since
#          the last ci — cksum stamp) → `npm run build` → copy dist/ back to
#          web/dist (served by the board server). EPERM in the npm log =
#          `skipped_tcc` (node lacks Full Disk Access in a launchd session;
#          not something a rollback can fix).
#   shell: macOS + swiftc present → `bash shell/build.sh` (ZAI_PORT stamped
#          from config.yaml server.port) → stage-then-swap the bundle into
#          $UI_APPS_DIR (default /Applications; ~/Applications when not
#          writable). Bundle folder/id stay "Zelin AI Board.app" /
#          com.zelin.ai-board (§54 — TCC keys on the id); the display name is
#          the product's.
# Combined step status: any fail → fail; else any ok → ok; else skipped_tcc
# if the web half was TCC-refused; else skipped.
# Missing toolchain is `skipped` + a warn, NEVER a deploy failure (mirror of
# the `app` precedent, §56.5). Never prompts: ad-hoc codesign needs no
# keychain, npm runs with CI=1 --no-audit --no-fund. Each half runs under a
# wall-clock budget (AIASSISTANT_UI_BUDGET, default 600 s per command) so a
# hung npm cannot eat the auto-deploy watchdog (1800 s); durations are logged
# and land in the report detail. Output goes to ui-build.log (capped), the
# tail is echoed on failure.
#
# The legacy "Zelin's AI Assistant.app" is NEVER touched here (D3):
# tests/test_install_ui_step.py plants one next to the shell bundle and
# asserts it is byte-identical afterwards.
UI_APP_NAME="Zelin AI Board"            # bundle folder — id com.zelin.ai-board (§54)
UI_EXEC_NAME="ZelinAIBoard"             # CFBundleExecutable → pgrep/pkill -x
UI_APPS_DIR="${AIASSISTANT_UI_APPS_DIR:-/Applications}"   # test seam
UI_BUDGET_S="${AIASSISTANT_UI_BUDGET:-600}"
UI_LOG="$HOME/Library/Logs/zelin-ai-assistant/ui-build.log"
UI_LOG_CAP_BYTES=1048576                # 防腐 #4：日志必有帽
UI_SHELL_INSTALLED=0
UI_APP_PATH=""
UI_WEB_STATUS=""; UI_WEB_DETAIL=""
UI_SHELL_STATUS=""; UI_SHELL_DETAIL=""

# Run a command with a wall-clock limit; 124 on timeout (children reaped too).
# Same shape as scripts/auto-deploy.sh run_with_timeout (macOS has no `timeout`);
# polls 4×/s so short builds do not pay a whole second of slack.
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

ui_log_begin() { # cap the build log, open a timestamped section
    mkdir -p "$(dirname "$UI_LOG")"
    if [ -f "$UI_LOG" ]; then
        _sz="$(wc -c < "$UI_LOG" | tr -d ' ')"
        if [ "$_sz" -gt "$UI_LOG_CAP_BYTES" ]; then
            tail -c "$((UI_LOG_CAP_BYTES / 2))" "$UI_LOG" > "$UI_LOG.tmp" && mv "$UI_LOG.tmp" "$UI_LOG"
        fi
    fi
    printf '\n==== install.sh ui step %s ====\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$UI_LOG"
}

ui_log_tail() { # echo the last lines of the build log after a failure
    info "last lines of $UI_LOG:"
    tail -n 15 "$UI_LOG" 2>/dev/null | sed 's/^/         /'
}

ui_now() { date +%s; }

# Where the web build actually runs (§56.5): NOT inside the repo. Verified
# 2026-09-02 with a throwaway launchd job: homebrew `node` is TCC-denied on a
# repo living on an external volume (EPERM on scandir / uv_cwd) even as a
# child of the FDA-granted daemon python — TCC judges each non-platform binary
# on its own (§55 第三幕) — while bash/cp/rsync/swiftc (Apple platform
# binaries) read it fine. So the sources are rsync'ed under $HOME, node/npm
# only ever touch $HOME paths, and the finished dist/ is copied back into the
# repo by cp (platform binary). Side benefit: `npm ci` never writes
# node_modules into the checkout.
ui_web_build_dir() {
    if [ -n "${AIASSISTANT_UI_BUILD_DIR:-}" ]; then printf '%s' "$AIASSISTANT_UI_BUILD_DIR"; return; fi
    if [ "$(uname -s)" = "Darwin" ]; then
        printf '%s' "$HOME/Library/Caches/zelin-ai-assistant/web-build"
    else
        printf '%s' "${XDG_CACHE_HOME:-$HOME/.cache}/zelin-ai-assistant/web-build"
    fi
}

# Mirror web/ (minus node_modules + dist) into the build dir. rsync --delete is
# the sanctioned directory sync (防腐 #8); cp -R after rm -rf is the fallback.
# --checksum: the mirror follows CONTENT, not rsync's size+whole-second-mtime
# quick check — the `npm ci` gate below hashes the mirrored package-lock.json,
# so a same-size edit inside the mtime window must still land (the tree is
# ~100 small files; hashing it costs nothing).
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

# Was that build failure TCC (EPERM / operation not permitted in the log tail)?
# Code cannot fix a missing Full Disk Access grant and a rollback will hit the
# same wall (the 2026-09-02 `cron=skipped_tcc` lesson, §23/§56.5) — such a half
# is `skipped_tcc`, not `fail`.
ui_log_says_tcc() {
    tail -n 40 "$UI_LOG" 2>/dev/null | grep -Eiq 'EPERM|operation not permitted'
}

# web half → UI_WEB_STATUS / UI_WEB_DETAIL
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
    # publish: stage next to the served dir, then swap (the server reads files
    # per request — the missing-dist window is milliseconds)
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

# a failed npm step → fail, or skipped_tcc when the log says TCC (see ui_log_says_tcc)
ui_web_failed() { # $1=step name $2=rc
    if ui_log_says_tcc; then
        warn "ui: $1 hit EPERM / operation not permitted — node lacks Full Disk Access in this (launchd) session; web board not rebuilt"
        info "  fix: System Settings > Privacy & Security > Full Disk Access: add $(command -v node) (resolve the symlink), or run: bash $REPO_ROOT/install.sh   # from a terminal"
        UI_WEB_STATUS=skipped_tcc; UI_WEB_DETAIL="web skipped_tcc ($1 exit $2: EPERM under launchd; grant node Full Disk Access or bash install.sh from a terminal)"
        return 0
    fi
    warn "ui: $1 failed (exit $2) — see $UI_LOG"
    ui_log_tail
    UI_WEB_STATUS=fail; UI_WEB_DETAIL="web fail ($1 exit $2)"
}

# shell half → UI_SHELL_STATUS / UI_SHELL_DETAIL (+ UI_APP_PATH, UI_SHELL_INSTALLED)
install_shell_app() {
    UI_SHELL_STATUS=skipped; UI_SHELL_DETAIL=""
    if [ "$(uname -s)" != "Darwin" ]; then
        UI_SHELL_DETAIL="shell skipped (not macOS)"
        return 0
    fi
    if ! command -v swiftc >/dev/null 2>&1 || ! bash "$REPO_ROOT/shell/build.sh" --check-toolchain >/dev/null 2>&1; then
        warn "ui: swift toolchain missing/too old — board shell app not built (xcode-select --install, then bash install.sh)"
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
    if [ ! -w "$_dest_dir" ] || { [ -e "$_dest_dir/$UI_APP_NAME.app" ] && [ ! -w "$_dest_dir/$UI_APP_NAME.app" ]; }; then
        warn "ui: $_dest_dir not writable — installing the shell app to ~/Applications"
        _dest_dir="$HOME/Applications"
        mkdir -p "$_dest_dir"
    fi
    # Stage-then-swap (mac/build.sh precedent): a failed copy must never leave
    # a half-bundle in place; the rm+mv window is near-instant. ditto keeps
    # the ad-hoc signature intact (cp -R can perturb it).
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
    _shell_s=$(( $(ui_now) - _t0 ))
    UI_SHELL_STATUS=ok
    UI_SHELL_DETAIL="shell ok (${_shell_s}s → $UI_APP_PATH)"
    ok "ui: board shell built + installed to $UI_APP_PATH (${_shell_s}s)"
    if pgrep -x "$UI_EXEC_NAME" >/dev/null 2>&1; then
        if [ "$NON_INTERACTIVE" -eq 1 ]; then
            info "ui: the shell app is running — relaunched after the server agent reloads (step 5)"
        else
            info "ui: the shell app is running — quit + reopen it to pick up this build: open \"$UI_APP_PATH\""
        fi
    fi
}

install_ui() {
    if [ "$PKG_POSTINSTALL" -eq 1 ]; then
        echo "==> 4b. board UI (web/dist + shell app) — skipped (.pkg mode)"
        report_step "ui" "skipped" "pkg-postinstall never builds the UI"
        return 0
    fi
    echo "==> 4b. board UI (web/dist + shell app)"
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

# §56.5 relaunch rule: only the auto-deploy path (--non-interactive), only when
# this run installed a new shell bundle AND the app is running, and only AFTER
# step 5 reloaded the server agent. SIGTERM → the shell's DispatchSource turns
# it into a regular NSApp.terminate (it spawned nothing to clean up: the server
# is launchd's). `open -g` relaunches without stealing focus. Interactive runs
# leave a running app alone (the owner picks the moment).
relaunch_shell_app() {
    [ "$NON_INTERACTIVE" -eq 1 ] || return 0
    [ "$UI_SHELL_INSTALLED" -eq 1 ] || return 0
    [ -n "$UI_APP_PATH" ] || return 0
    pgrep -x "$UI_EXEC_NAME" >/dev/null 2>&1 || return 0
    pkill -TERM -x "$UI_EXEC_NAME" 2>/dev/null || true
    for _ in 1 2 3 4 5 6 7 8 9 10; do
        pgrep -x "$UI_EXEC_NAME" >/dev/null 2>&1 || break
        sleep 0.5
    done
    pkill -KILL -x "$UI_EXEC_NAME" 2>/dev/null || true
    if open -g "$UI_APP_PATH" 2>/dev/null; then
        ok "ui: relaunched the shell app on the new build (server agent reloaded first)"
    else
        warn "ui: relaunch failed — start it manually: open \"$UI_APP_PATH\""
    fi
}

# Version stamp (CONTRACT §56.1) — the version's truth is the git tag; nothing
# committed carries it. Write the git-ignored act/_version.py BEFORE anything
# here imports act (the launchd viability probe, the dashboard seed, the
# report writer, the daemons themselves): under launchd `git` is a different
# binary whose TCC grant may not cover an external-volume checkout, so the
# daemons must read the stamp and never derive the version themselves. The
# stamper keeps an existing stamp when git cannot answer (.pkg copy without
# .git — the payload already carries the tag's stamp). Never fatal: without
# a stamp act.__version__ falls back to the baked constant, and doctor's
# `version` row says so.
#
# WHICH python runs the stamper matters (§55 第三幕): TCC judges every
# non-platform binary on its own, even as a child of the launchd job's
# FDA-granted interpreter. 2026-09-02 (v0.48.21 first contact): auto-deploy's
# PATH puts Homebrew first, so `command -v python3` handed the stamper to
# /opt/homebrew/bin/python3, which could not even open scripts/version_stamp.py
# on the external-volume checkout (`[Errno 1] Operation not permitted`) — and
# 2>/dev/null hid it. So the candidates are the §55 daemon order
# ($AIASSISTANT_PYTHON = the launchd job's own interpreter first, the system
# python above the PATH python when the repo is outside $HOME), the first one
# that stamps wins, and every failure's last stderr line is logged AND lands
# in the §23 report (`version=warn:<interpreter>: <stderr>`).
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
        warn "no python3 — act/_version.py not written (daemons report the baked fallback version)"
        report_step "version" "warn" "no python3 to stamp"
    else
        warn "scripts/version_stamp.py failed with every interpreter — act/_version.py not written (daemons derive the version themselves or report the baked fallback): $_why"
        report_step "version" "warn" "stamp failed — $_why"
    fi
}

write_install_report() {
    RPY="${RUNTIME_PY:-${PY:-}}"
    { [ -n "$RPY" ] && [ -x "$RPY" ]; } || RPY="$(command -v python3 || true)"
    if [ -z "$RPY" ]; then
        warn "no python3 — skipped state/install_report.json"
        return 0
    fi
    MODE=interactive
    [ "$PKG_POSTINSTALL" -eq 1 ] && MODE=pkg-postinstall
    [ "$NON_INTERACTIVE" -eq 1 ] && MODE=non-interactive
    if printf '%s' "$REPORT_STEPS" | (cd "$REPO_ROOT" && AIASSISTANT_HOME="$REPO_ROOT" \
        "$RPY" -m act.lib.install_report --mode "$MODE" --steps-stdin \
        --agents "$LOADED_LABELS" >/dev/null 2>&1); then
        ok "state/install_report.json written"
    else
        warn "could not write state/install_report.json (non-fatal)"
    fi
}

# launchd load/unload — modern bootstrap/bootout against the user's gui
# domain first (the only form that works from the pkg postinstall context,
# where mac/package.sh wraps us in `launchctl asuser`), legacy load/unload
# as fallback for older macOS.
UID_NUM="$(id -u)"
launchd_unload() { # $1=plist path, $2=label
    launchctl bootout "gui/$UID_NUM/$2" >/dev/null 2>&1 \
        || launchctl unload "$1" >/dev/null 2>&1 || true
}
launchd_load() { # $1=plist path
    launchctl bootstrap "gui/$UID_NUM" "$1" >/dev/null 2>&1 \
        || launchctl load "$1" >/dev/null 2>&1
}
# Is this label registered with launchd right now? (`launchctl list` columns:
# PID Status Label). The only proof an unload actually took.
launchd_label_loaded() { # $1=label
    launchctl list 2>/dev/null | awk -v l="$1" '$3 == l' | grep -q .
}
# Retire a label for good: unload + delete its plist + PROVE it is gone
# (CONTRACT §55). launchd_unload swallows failures by design (idempotent
# upgrades), which is exactly how the v0.21-removed imessageradar agent kept
# running for 51 days — 23,613 tracebacks — while every install.sh run printed
# nothing (2026-08-31 audit L3). A label that survives bootout is reported
# loudly and lands in the install report as launchd_retired=fail.
RETIRED_STILL_LOADED=""
launchd_retire() { # $1=label
    _was_loaded=0
    launchd_label_loaded "$1" && _was_loaded=1
    launchd_unload "$LA_DIR/$1.plist" "$1"
    rm -f "$LA_DIR/$1.plist"
    if launchd_label_loaded "$1"; then
        echo "  [ERR ] retired agent $1 is STILL loaded after bootout" >&2
        info "  fix: launchctl bootout gui/$UID_NUM/$1   # then re-run install.sh"
        RETIRED_STILL_LOADED="$RETIRED_STILL_LOADED $1"
    elif [ "$_was_loaded" -eq 1 ]; then
        ok "unloaded retired agent $1"
    fi
}
# Orphans = our label prefix, loaded (or left in ~/Library/LaunchAgents), but
# no template in act/launchd/ any more and not in the explicit RETIRED list.
# Reported, never auto-unloaded (a label we do not know is not ours to kill);
# doctor's "launchd orphans" row carries the same finding with the fix.
launchd_orphans() { # prints one label per line
    {
        launchctl list 2>/dev/null | awk '$3 ~ /^com\.zelin\.aiassistant\./ {print $3}'
        for _p in "$LA_DIR"/com.zelin.aiassistant.*.plist; do
            [ -e "$_p" ] || continue
            _b="$(basename "$_p")"; printf '%s\n' "${_b%.plist}"
        done
    } | sort -u | while IFS= read -r _label; do
        [ -e "$REPO_ROOT/act/launchd/$_label.plist" ] && continue
        printf '%s\n' "$_label"
    done
}

# escape a value for use on the replacement side of sed s|…|…| (delimiter |)
_sed_escape() { printf '%s' "$1" | sed -e 's/[\\&|]/\\&/g'; }

# escape a value for a plist <string> body (the viability probe writes XML)
_xml_escape() { printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g'; }

# Render a launchd plist template into place. The repo plists carry
# /Users/YOURUSERNAME placeholders (plists don't expand ~) — substitute the
# detected python interpreter, this repo root, $HOME, and the login shell's
# claude directory (/Users/YOURUSERNAME/.claude-bin, kept FIRST on PATH —
# see the CLAUDE_LOGIN_BIN resolution below) before installing.
# Kept as a function so both the interactive path and --pkg-postinstall (which
# currently skips launchd) render identically if they ever load agents.
render_launchd_plist() {
    src="$1"; dest="$2"
    # Validated interpreter, never a bare PATH guess: an interpreter without
    # PyYAML makes every agent exit 1 on `import yaml` before it can log.
    # RUNTIME_PY already cleared both §55 gates; this is only the safety net for
    # a caller that never ran the selection, so it reuses the same candidate
    # ORDER (system python first when the repo is outside $HOME) with the cheap
    # yaml gate — re-probing launchd once per plist would buy nothing.
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
    # PHYSICAL repo path (CONTRACT §55): the rendered PYTHONPATH /
    # AIASSISTANT_HOME must not carry a symlink the launchd session cannot
    # traverse under TCC. Idempotent when REPO_ROOT is already physical.
    repo="$(physical_path "$REPO_ROOT")"
    claudedir="$HOME/.local/bin"
    [ -n "${CLAUDE_LOGIN_BIN:-}" ] && claudedir="$(dirname "$CLAUDE_LOGIN_BIN")"
    # launchd opens StandardOut/ErrorPath BEFORE exec — the templates point
    # them at ~/Library/Logs/zelin-ai-assistant/ (never under the repo: an
    # external-volume repo makes the spawn fail with EX_CONFIG 78), and the
    # directory must exist or the spawn fails the same way.
    mkdir -p "$HOME/Library/Logs/zelin-ai-assistant"
    # §54 board server port: the server template carries
    # `<key>ZAI_PORT</key><string>47820</string>` on ONE line; SERVER_PORT is
    # config.yaml server.port (computed once by server_port), default 47820.
    port="${SERVER_PORT:-47820}"
    sed -e "s|/Users/YOURUSERNAME/\.claude-bin|$(_sed_escape "$claudedir")|g" \
        -e "s|/Users/YOURUSERNAME/miniconda3/bin/python3|$(_sed_escape "$py")|g" \
        -e "s|/Users/YOURUSERNAME/Projects/zelin-ai-assistant|$(_sed_escape "$repo")|g" \
        -e "s|/Users/YOURUSERNAME/miniconda3/bin|$(_sed_escape "$pydir")|g" \
        -e "s|/Users/YOURUSERNAME|$(_sed_escape "$HOME")|g" \
        -e "s|<key>ZAI_PORT</key><string>[0-9]*</string>|<key>ZAI_PORT</key><string>$(_sed_escape "$port")</string>|g" \
        "$src" > "$dest"
}

# §54: the board server's loopback port from config.yaml `server.port`
# (act.lib.config.server_port, 1..65535, default 47820). Fail-open to the
# default on any probe trouble — a port typo must never stop the install.
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

# Does anything answer GET /api/health on the board port right now? Used before
# loading the server agent: a shell-spawned fallback or a hand-run `-m server`
# holding the port would make the launchd job crash-loop (§54: two servers
# must never fight for the port). Reported, never killed — it is not ours.
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

# Cap an agent's launchd-managed log in the unload→load window — the only
# moment launchd holds no fd on it (§32.4: in-process replace is off-limits
# for *.launchd.log; §55). Keep the newest half past 1 MB (防腐 #4; same
# shape as scripts/auto-deploy.sh cap_log). Only labels this run reloads are
# touched; retired/orphan logs stay as forensics (§55).
cap_launchd_log() { # $1=label
    _lf="$HOME/Library/Logs/zelin-ai-assistant/${1##*.}.launchd.log"
    [ -f "$_lf" ] || return 0
    _lsz="$(wc -c < "$_lf" | tr -d ' ')"
    [ "$_lsz" -gt 1048576 ] || return 0
    if tail -c 524288 "$_lf" > "$_lf.tmp" && mv "$_lf.tmp" "$_lf"; then
        info "capped ${1##*.}.launchd.log (was $_lsz bytes; newest half kept)"
    fi
}

# `launchctl load` reports success even when the job crashes on spawn (e.g. a
# bad interpreter path) — check `launchctl list` (columns: PID Status Label)
# to prove the agent actually runs / exited cleanly.
verify_launchd_agent() {
    label="$1"
    line="$(launchctl list 2>/dev/null | awk -v l="$label" '$3 == l')"
    if [ -z "$line" ]; then
        echo "  [ERR ] $label not registered with launchd — try: launchctl load $LA_DIR/$label.plist" >&2
        return 1
    fi
    agent_pid="$(printf '%s' "$line" | awk '{print $1}')"
    agent_status="$(printf '%s' "$line" | awk '{print $2}')"
    if [ "$agent_pid" != "-" ]; then
        ok "$label running (pid $agent_pid)"
    elif [ "$agent_status" = "0" ]; then
        ok "$label loaded (last run exited 0)"
    else
        echo "  [ERR ] $label loaded but its process exits with status $agent_status" >&2
        launchd_failure_hint "${label##*.}"
        info "then: launchctl unload $LA_DIR/$label.plist && launchctl load $LA_DIR/$label.plist"
        return 1
    fi
}

# Name the ACTUAL cause from the agent's own log instead of guessing (§55).
# The two ModuleNotFoundErrors look identical in `launchctl list` but have
# opposite fixes, and asserting PyYAML at a repo the interpreter simply cannot
# SEE is what sent the 2026-08-31 debugging session down the wrong path for
# hours: /opt/homebrew/bin/python3 had PyYAML the whole time.
launchd_failure_hint() { # $1 = short agent name (actd, radar, …)
    _log="$HOME/Library/Logs/zelin-ai-assistant/$1.launchd.log"
    _tail="$(tail -n 40 "$_log" 2>/dev/null || true)"
    info "fix: read $_log —"
    case "$_tail" in
        *"No module named 'act'"*)
            info "  its log says \"No module named 'act'\": the interpreter cannot SEE the repo."
            info "  PyYAML is NOT the problem. Either the rendered PYTHONPATH is wrong, or"
            info "  ${RUNTIME_PY:-the daemon python} lacks Full Disk Access (TCC is granted per"
            info "  binary, and launchd jobs do not inherit your terminal's grant)."
            info "  re-run: bash $REPO_ROOT/install.sh   # now picks a launchd-viable interpreter"
            ;;
        *"No module named 'yaml'"*)
            info "  its log says \"No module named 'yaml'\": PyYAML is missing for the daemon python."
            info "  fix: ${RUNTIME_PY:-python3} -m pip install --user --break-system-packages pyyaml"
            ;;
        *)
            info "  usual causes:"
            info "  - the interpreter cannot see the repo (\"No module named 'act'\"): re-run install.sh"
            info "  - PyYAML missing (\"No module named 'yaml'\"): ${RUNTIME_PY:-python3} -m pip install --user pyyaml"
            info "  - Anthropic API key file missing: paste it in the app's Settings window"
            ;;
    esac
}

echo "=============================================="
echo " Zelin's AI Assistant — installer"
echo " repo: $REPO_ROOT"
echo "=============================================="

# --------------------------------------------------------------------------
echo ""
echo "==> 1. Dependency checks"

if [ "$PKG_POSTINSTALL" -eq 1 ]; then
    # Non-interactive: the pkg can't stop and ask the user to install anything.
    # claude/swiftc are only needed at runtime; python3 is best-effort here.
    PY="$(command -v python3 || true)"
    info "pkg postinstall mode — dependency checks skipped"
else

# claude (required). --non-interactive (§56) cannot stop to ask: it warns and
# keeps deploying the daemons — they only need claude at dispatch time, and
# the claude_bin step of the §23 report records the gap.
if command -v claude >/dev/null 2>&1; then
    ok "claude found: $(command -v claude)"
elif [ "$NON_INTERACTIVE" -eq 1 ]; then
    warn "claude CLI not found — daemons will fail to dispatch until Claude Code is installed"
else
    echo "  [ERR ] claude CLI not found (REQUIRED). Install Claude Code first, then re-run." >&2
    exit 1
fi

# swift toolchain (required to build the Mac app) — presence AND minimum
# version. MIN_SWIFT lives in mac/build.sh (single source); on failure it
# prints the exact fix (update Xcode, xcode-select) so we just exit.
# --non-interactive never builds the app (§56.5), so it does not need one.
if [ "$NON_INTERACTIVE" -eq 1 ]; then
    info "swift toolchain not required — --non-interactive never rebuilds the legacy Mac app (§56.5); the board shell (step 4b) is skipped when swiftc is absent"
elif bash "$REPO_ROOT/mac/build.sh" --check-toolchain; then
    ok "swift toolchain: $(swiftc --version 2>/dev/null | head -n1)"
else
    echo "  [ERR ] Swift toolchain check failed (see message above), then re-run this script." >&2
    exit 1
fi

# python3 (required for actd/radar)
if command -v python3 >/dev/null 2>&1; then
    ok "python3 found: $(command -v python3)"
    PY="$(command -v python3)"
else
    echo "  [ERR ] python3 not found (REQUIRED for actd/radar)." >&2
    exit 1
fi

# PyYAML (else pip install). Homebrew/system pythons are PEP 668 "externally
# managed" and refuse plain --user installs — retry with --break-system-packages
# (same fallback as .github/workflows/ci.yml). actd/radar cannot run without
# yaml, so a final failure is a hard stop, not a warn.
if "$PY" -c "import yaml" >/dev/null 2>&1; then
    ok "PyYAML available"
else
    warn "PyYAML missing; attempting: $PY -m pip install --user pyyaml"
    if "$PY" -m pip install --user pyyaml >/dev/null 2>&1 \
        || "$PY" -m pip install --user --break-system-packages pyyaml >/dev/null 2>&1; then
        ok "PyYAML installed"
    else
        echo "  [ERR ] PyYAML install failed (REQUIRED for actd/radar)." >&2
        info "fix: $PY -m pip install --user --break-system-packages pyyaml"
        info "  or use a conda/miniconda python3, then re-run this script"
        exit 1
    fi
fi

# node/npx — the recording engine is `npx screenpipe@<pin>` (canonical launch
# path, see mac/Sources/Recording.swift): no separate screenpipe install needed,
# but without node/npx the whole ingest side silently records nothing.
if command -v npx >/dev/null 2>&1; then
    ok "node/npx found: $(command -v npx) — screenpipe engine runs via npx, no separate install"
else
    warn "node/npx not found (needed for screen recording — the ingest source). Install: brew install node"
fi

# optional
if [ -d "/Applications/Obsidian.app" ] || command -v obsidian >/dev/null 2>&1; then
    ok "obsidian found (optional)"
else
    warn "obsidian not found (optional — radar reads the vault)"
fi
if command -v gh >/dev/null 2>&1; then
    ok "gh found (optional)"
else
    warn "gh not found (optional — draft-PR delivery)"
fi

# credential reminder (contract §19: actd reads key from file, not from this script)
if [ -s "$REPO_ROOT/config/secrets/anthropic-api-key.txt" ]; then
    ok "anthropic key present (config/secrets/anthropic-api-key.txt)"
elif [ -f "$HOME/.config/anthropic-key.txt" ]; then
    ok "anthropic key present (legacy ~/.config/anthropic-key.txt — 仍兜底可用)"
else
    warn "缺 Anthropic API key —— 推荐在 App 设置窗口粘贴保存（写入 config/secrets/anthropic-api-key.txt）；旧路径 ~/.config/anthropic-key.txt 仍兜底。headless claude 在 launchd 下读不了 Keychain OAuth。"
fi

fi # PKG_POSTINSTALL dependency-check skip

# --------------------------------------------------------------------------
echo ""
echo "==> 2. config.yaml + config/runtime.json"
if [ -f "$REPO_ROOT/config.yaml" ]; then
    ok "config.yaml already exists (left untouched)"
    report_step "config" "ok" "existing config.yaml kept"
else
    cp "$REPO_ROOT/config.example.yaml" "$REPO_ROOT/config.yaml"
    ok "created config.yaml from config.example.yaml — review it before first run"
    report_step "config" "ok" "created from config.example.yaml"
fi

# redaction terms live outside git (they hold the user's real sensitive terms)
if [ ! -f "$REPO_ROOT/config/redaction_terms.txt" ]; then
    cp "$REPO_ROOT/config/redaction_terms.example.txt" "$REPO_ROOT/config/redaction_terms.txt"
    ok "created config/redaction_terms.txt from template (gitignored)"
fi

# version stamp first (§56.1): every `import act` below — including the launchd
# viability probe a few lines down — reads act/_version.py.
stamp_version

# runtime python pointer (CONTRACT §19) — the interpreter launchd, cron and the
# Mac app all run. EVERY candidate must clear TWO gates before it is pinned
# (§55): `import yaml` (PyYAML is the daemons' only non-stdlib dependency) and
# LAUNCHD VIABILITY (it can really import act from the repo when launchd — not
# this shell — spawns it; TCC is per-binary). Candidate order comes from
# daemon_python_candidates: system python first when the repo is outside $HOME.
mkdir -p "$REPO_ROOT/config"
RUNTIME_PY=""
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
    if [ -n "$DAEMON_PY_NOTE" ]; then
        ok "config/runtime.json -> $RUNTIME_PY (PyYAML importable)"
        warn "$DAEMON_PY_NOTE"
        report_step "runtime_python" "ok" "$RUNTIME_PY ($DAEMON_PY_NOTE)"
    else
        ok "config/runtime.json -> $RUNTIME_PY (PyYAML importable; imports act under launchd)"
        report_step "runtime_python" "ok" "$RUNTIME_PY"
    fi
else
    echo "  [ERR ] no python3 with PyYAML found — the launchd agents would die on" >&2
    echo "         \"No module named 'yaml'\" the moment they spawn." >&2
    info "fix: $(command -v python3 || echo python3) -m pip install --user --break-system-packages pyyaml"
    info "  then re-run: bash $REPO_ROOT/install.sh"
    report_step "runtime_python" "fail" "no candidate python3 can import yaml"
fi

# claude for the DAEMONS (launchd plists + cron chain) — resolve the binary the
# user's LOGIN SHELL runs, not this script's PATH: the pkg postinstall carries
# a minimal PATH, and a second, OUTDATED claude install can rank first for
# launchd while the login shell uses the new one. 2026-07-08 incident:
# /opt/homebrew/bin/claude 2.1.16 (no --bg) shadowed ~/.local/bin 2.1.206 in
# the rendered plists — every dispatch died on "unknown option '--bg'" and
# retried forever. Its directory goes FIRST in every rendered PATH below.
CLAUDE_LOGIN_BIN=""
_c="$("${SHELL:-/bin/zsh}" -lc 'command -v claude' 2>/dev/null | tail -n 1 || true)"
case "$_c" in
    /*) [ -x "$_c" ] && CLAUDE_LOGIN_BIN="$_c" ;;
esac
if [ -z "$CLAUDE_LOGIN_BIN" ]; then
    CLAUDE_LOGIN_BIN="$(command -v claude 2>/dev/null || true)"
fi
if [ -z "$CLAUDE_LOGIN_BIN" ]; then
    for _c in "$HOME/.local/bin/claude" /opt/homebrew/bin/claude /usr/local/bin/claude; do
        [ -x "$_c" ] && { CLAUDE_LOGIN_BIN="$_c"; break; }
    done
fi
if [ -n "$CLAUDE_LOGIN_BIN" ]; then
    ok "daemon claude: $CLAUDE_LOGIN_BIN (login-shell resolution)"
    report_step "claude_bin" "ok" "$CLAUDE_LOGIN_BIN"
else
    warn "claude not resolvable from the login shell — daemon PATH falls back to ~/.local/bin first"
    report_step "claude_bin" "missing"
fi

# verify PyYAML against the DAEMON interpreter — RUNTIME_PY is what launchd
# and cron actually run, and it can differ from the shell python3 checked in
# step 1 (e.g. $AIASSISTANT_PYTHON override). Without yaml, actd exits on
# spawn with no visible error.
if [ -n "$RUNTIME_PY" ] && ! "$RUNTIME_PY" -c "import yaml" >/dev/null 2>&1; then
    warn "PyYAML missing for the daemon python ($RUNTIME_PY); attempting install"
    if "$RUNTIME_PY" -m pip install --user pyyaml >/dev/null 2>&1 \
        || "$RUNTIME_PY" -m pip install --user --break-system-packages pyyaml >/dev/null 2>&1; then
        ok "PyYAML installed for $RUNTIME_PY"
    elif [ "$PKG_POSTINSTALL" -eq 1 ]; then
        warn "PyYAML unavailable for $RUNTIME_PY — actd/radar will not start. fix: $RUNTIME_PY -m pip install --user --break-system-packages pyyaml"
    else
        echo "  [ERR ] PyYAML unavailable for the daemon python: $RUNTIME_PY" >&2
        info "fix: $RUNTIME_PY -m pip install --user --break-system-packages pyyaml   (then re-run this script)"
        exit 1
    fi
fi

# home pointer (CONTRACT §19) — the GUI app launches with no env vars, so a
# clone outside ~/Projects/zelin-ai-assistant would be invisible to it. Persist
# the repo root where the app can read it (env var AIASSISTANT_HOME still wins).
POINTER_DIR="$HOME/Library/Application Support/ZelinAIAssistant"
if mkdir -p "$POINTER_DIR" && printf '%s\n' "$REPO_ROOT" > "$POINTER_DIR/home.txt"; then
    ok "home pointer -> $POINTER_DIR/home.txt"
else
    warn "could not write $POINTER_DIR/home.txt — the app will assume ~/Projects/zelin-ai-assistant"
fi

# --------------------------------------------------------------------------
echo ""
echo "==> 3. state directories"
mkdir -p "$REPO_ROOT/state/inbox"
ok "state/ and state/inbox/ ready"
report_step "state_dirs" "ok"
# generate the initial dashboard from the (git-tracked) registry so the app renders
# before the daemon's first pass. Falls back to the seed if generation fails.
if [ ! -f "$REPO_ROOT/state/dashboard.json" ]; then
    if [ -n "$PY" ] && (cd "$REPO_ROOT" && AIASSISTANT_HOME="$REPO_ROOT" "$PY" -m act.lib.dashboard >/dev/null 2>&1); then
        ok "generated state/dashboard.json from registry"
    elif [ -f "$REPO_ROOT/state/dashboard.seed.json" ]; then
        cp "$REPO_ROOT/state/dashboard.seed.json" "$REPO_ROOT/state/dashboard.json"
        ok "seeded state/dashboard.json from dashboard.seed.json"
    else
        warn "could not generate dashboard.json (run: python -m act.lib.dashboard)"
    fi
fi

# --------------------------------------------------------------------------
echo ""
install_mac_app

# --------------------------------------------------------------------------
# §54 board server port (config.yaml server.port) — consumed by the shell
# build below (Info.plist ZAIServerPort) and by the server plist render in
# step 5 (ZAI_PORT). One resolution, both consumers.
SERVER_PORT="$(server_port)"
echo ""
install_ui

# --------------------------------------------------------------------------
# Runs in BOTH modes: a .pkg install that leaves actd unloaded ships an inert
# product (the app shows an orange banner and nothing ever executes). The
# radars are safe to load before credentials exist — they no-op and record a
# skip_reason until configured.
echo ""
echo "==> 5. launchd agents"
LAUNCHD_FAILED=0
mkdir -p "$LA_DIR"
info "rendering plist templates: python=${RUNTIME_PY:-python3} home=$REPO_ROOT server_port=$SERVER_PORT"
# v0.18.1: the Obsidian radar now runs ONLY through the cron ingest chain
# (step 6). Its old launchd agent was TCC-blocked from ~/Documents and only
# ever saw an empty vault — retire any previously-installed copy so an upgrade
# doesn't leave a redundant agent that logs empty passes forever.
RETIRED_RADAR_LABEL="com.zelin.aiassistant.radar"
launchd_retire "$RETIRED_RADAR_LABEL"
# v0.21.0: the iMessage transport was removed (Slack's phone-approval role too;
# the Mac app is now the sole approval surface). Its launchd agent is no longer
# shipped — retire any previously-installed copy so an upgrade unloads the
# already-loaded agent instead of leaving it polling chat.db forever.
RETIRED_IMESSAGE_LABEL="com.zelin.aiassistant.imessageradar"
launchd_retire "$RETIRED_IMESSAGE_LABEL"
# §55 retire assertion + orphan report (2026-08-31 audit L3): a retired label
# that survived bootout is a FAIL step; any other prefixed label with no
# template is reported (not touched) so it stops being structurally invisible.
if [ -n "$RETIRED_STILL_LOADED" ]; then
    report_step "launchd_retired" "fail" "still loaded:$RETIRED_STILL_LOADED"
else
    report_step "launchd_retired" "ok"
fi
ORPHAN_LABELS="$(launchd_orphans | tr '\n' ' ' | sed 's/ *$//')"
if [ -n "$ORPHAN_LABELS" ]; then
    warn "launchd agent(s) with our prefix but no template in act/launchd: $ORPHAN_LABELS"
    info "  each keeps running/logging until unloaded — launchctl bootout gui/$UID_NUM/<label>;"
    info "  rm ~/Library/LaunchAgents/<label>.plist   (python3 -m act.doctor lists them too)"
    report_step "launchd_orphans" "warn" "$ORPHAN_LABELS"
else
    report_step "launchd_orphans" "ok"
fi
# v0.47 (CONTRACT §48): per-source switch gate — a radar agent is installed
# ONLY when its source is enabled per the single source of truth
# (act/lib/sources.py: features.<src>_radar AND sources.<src>.enabled).
# A disabled source gets the RETIRED treatment above (unload + rm) instead,
# so a re-run of install.sh can no longer resurrect a switched-off radar.
# "off" is ONLY the dedicated exit code 3 + the literal stdout "off" — every
# other outcome (exit 1 python crash / ModuleNotFoundError / no PyYAML / exit
# 2 bad invocation) fails OPEN and installs as before. The probe runs from
# $REPO_ROOT like every other `-m act.*` call in this file: a pkg postinstall
# cwd is an Installer temp dir where `-m act.lib.sources` can't import at all.
radar_source_enabled() {   # $1 = source name; returns 0 on/probe-failed, 1 off
    rc=0
    out="$( (cd "$REPO_ROOT" && AIASSISTANT_HOME="$REPO_ROOT" \
        "${RUNTIME_PY:-python3}" -m act.lib.sources --enabled "$1") 2>/dev/null )" || rc=$?
    ! { [ "$rc" -eq 3 ] && [ "$out" = "off" ]; }
}
# CONTRACT §56: the self-updating deploy agent is installed ONLY for a git
# checkout (a .pkg copy has no .git — nothing to fast-forward) whose
# features.auto_deploy is on (default on). Same fail-open shape as the radar
# gate: only the dedicated exit 3 + literal "off" means off.
# §54 board server agent (every mode: without it the web board and the shell
# have nothing to connect to; the shell only spawns a fallback when this label
# is NOT loaded).
SERVER_LABEL="com.zelin.aiassistant.server"
SERVER_PORT_BUSY=0
AUTODEPLOY_LABEL="com.zelin.aiassistant.autodeploy"
autodeploy_wanted() {      # returns 0 wanted/probe-failed, 1 not wanted
    git -C "$REPO_ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1 || return 1
    rc=0
    out="$( (cd "$REPO_ROOT" && AIASSISTANT_HOME="$REPO_ROOT" "${RUNTIME_PY:-python3}" -c '
from act.lib import config
try:
    cfg = config.load_config()
except Exception:
    cfg = config.Config()
on = cfg.feature("auto_deploy")
print("on" if on else "off")
raise SystemExit(0 if on else 3)') 2>/dev/null )" || rc=$?
    ! { [ "$rc" -eq 3 ] && [ "$out" = "off" ]; }
}
for plist in "$REPO_ROOT"/act/launchd/*.plist; do
    [ -e "$plist" ] || continue
    base="$(basename "$plist")"
    label="${base%.plist}"
    dest="$LA_DIR/$base"
    case "$label" in
        *.gmailradar) plist_source="gmail" ;;
        *.slackradar) plist_source="slack" ;;
        *) plist_source="" ;;
    esac
    if [ -n "$plist_source" ] && ! radar_source_enabled "$plist_source"; then
        info "$plist_source source is switched off — not installing $label"
        launchd_unload "$dest" "$label"
        rm -f "$dest"
        continue
    fi
    if [ "$label" = "$AUTODEPLOY_LABEL" ]; then
        if ! autodeploy_wanted; then
            info "auto-deploy is off (not a git checkout, or features.auto_deploy: false) — not installing $label"
            launchd_unload "$dest" "$label"
            rm -f "$dest"
            continue
        fi
        if [ "${AIASSISTANT_AUTODEPLOY_ACTIVE:-0}" = "1" ]; then
            # We ARE that agent's process tree right now: bootout would kill
            # the deploy mid-flight. Re-render only; a changed template takes
            # effect on the next manual `bash install.sh`.
            render_launchd_plist "$plist" "$dest"
            info "$label re-rendered; reload deferred (this install.sh runs inside it)"
            continue
        fi
    fi
    if [ "$label" = "$SERVER_LABEL" ] && ! launchd_label_loaded "$label" \
        && board_server_answering "$SERVER_PORT"; then
        # §54: something not under launchd already answers on the port (the
        # shell's spawn fallback from before this version, or a hand-run
        # `python3 -m server`). The agent is loaded anyway — it takes the port
        # over as soon as that process exits — but say so: until then the
        # job exits 75 every throttle cycle and doctor's `board server` row
        # stays red.
        warn "a board server not managed by launchd answers on 127.0.0.1:$SERVER_PORT — quit the old shell app / hand-started 'python3 -m server' so $label can take the port"
        SERVER_PORT_BUSY=1
    fi
    # unload any previous version first (idempotent upgrades); cap the agent's
    # launchd log while launchd holds no fd on it (§55)
    launchd_unload "$dest" "$label"
    cap_launchd_log "$label"
    render_launchd_plist "$plist" "$dest"
    if launchd_load "$dest"; then
        ok "loaded $label"
        LOADED_LABELS="$LOADED_LABELS $label"
    else
        warn "failed to load $label (may need TCC/Full Disk Access approval — see below)"
        LAUNCHD_FAILED=$((LAUNCHD_FAILED + 1))
    fi
done
# give launchd a moment to spawn the jobs, then verify they really run
if [ -n "$LOADED_LABELS" ]; then
    sleep 2
    for label in $LOADED_LABELS; do
        verify_launchd_agent "$label"
    done
fi
if [ "$LAUNCHD_FAILED" -gt 0 ]; then
    report_step "launchd" "fail" "$LAUNCHD_FAILED agent(s) failed to load"
elif [ -n "$LOADED_LABELS" ]; then
    report_step "launchd" "ok" "$(echo "$LOADED_LABELS" | wc -w | tr -d ' ') agents loaded"
else
    report_step "launchd" "skipped" "no agents to load"
fi
if [ "$SERVER_PORT_BUSY" -eq 1 ]; then
    report_step "board_server_port" "warn" "127.0.0.1:$SERVER_PORT answered before $SERVER_LABEL loaded - quit the old shell app / hand-run server so launchd can take the port"
fi
# §56.5 relaunch rule — only now, with the server agent back under launchd
relaunch_shell_app

# --------------------------------------------------------------------------
echo ""
echo "==> 6. crontab — unified ingest chain + state digest (CONTRACT §18)"
chmod +x "$REPO_ROOT"/ingest/*.sh "$REPO_ROOT"/ingest/*.command 2>/dev/null || true

# cron runs outside the login shell — same validated-interpreter rule as the
# launchd plists (§55): -x alone once picked a miniconda python that had no
# PyYAML, so every cron radar pass died on `import yaml`.
_saved_ifs="$IFS"
IFS='
'
# shellcheck disable=SC2046 # newline word-splitting the candidate list is the point
CRON_PY="$(pick_python "${RUNTIME_PY:-}" $(daemon_python_candidates) \
    || echo "${PY:-python3}")"
IFS="$_saved_ifs"

# cron's PATH is minimal AND user-customizable — pin the login shell's claude
# dir first so process-screenpipe.sh's `command -v claude` and the radar's
# shutil.which never land on a second, outdated install (same 2026-07-08
# incident class the launchd plists guard against above).
CRON_CLAUDE_DIR="$HOME/.local/bin"
[ -n "$CLAUDE_LOGIN_BIN" ] && CRON_CLAUDE_DIR="$(dirname "$CLAUDE_LOGIN_BIN")"

# process-screenpipe.sh exits 3 when another run holds the lock — that's a
# skip, not a failure, so it must not break the chain (radar still runs).
# AIASSISTANT_CRON=1 arms the FDA probe in screenpipe-export.sh (CONTRACT §25):
# only real cron runs may write state/cron_probe.json — a manual in-app run
# has the app's own disk access and would falsify the verdict.
INGEST_CHAIN="*/30 * * * * cd $REPO_ROOT && export PATH=$CRON_CLAUDE_DIR:\$PATH AIASSISTANT_CRON=1 && ./ingest/screenpipe-export.sh && ./ingest/screenpipe-cleanup.sh && { ./ingest/process-screenpipe.sh || [ \$? -eq 3 ]; } && AIASSISTANT_HOME=$REPO_ROOT $CRON_PY -m act.radar --once >> $REPO_ROOT/state/radar.cron.log 2>&1"
# Daily 09:07 fire WITHOUT --now (CONTRACT §17 D19): act.digest self-gates on
# digest.frequency (off | daily | every2days | weekly, default off) + its
# state/digest.json marker, so a cadence change in Settings needs no crontab
# rewrite. Off/not-due fires exit silently (no log line).
DIGEST_LINE="7 9 * * * cd $REPO_ROOT && AIASSISTANT_HOME=$REPO_ROOT $CRON_PY -m act.digest >> $REPO_ROOT/state/digest.log 2>&1"
TELEMETRY_LINE="17 * * * * cd $REPO_ROOT && AIASSISTANT_HOME=$REPO_ROOT $CRON_PY -m act.analytics_sync --once >> $REPO_ROOT/state/analytics_sync.log 2>&1"

# 第 6 步的 crontab 写入与判决，抽成函数（tests 用 stub crontab 真跑它，同
# install_mac_app / failed_deploy_steps 的手法）。读全局 NEW_CRON / CURRENT_CRON /
# INGEST_CHAIN / DIGEST_LINE / CRON_PY。失败分两类（§23 / §56.5）：
#   - stderr 带 "Operation not permitted" = launchd 会话被 TCC 拒写 crontab
#     （2026-09-02 v0.48.12 实战：timer 触发的自动部署在这里翻车 → 回滚 → 回滚
#     的 install.sh 撞同一堵墙 → rollback_failed + sha 中毒，所有后续部署停摆。
#     回滚治不了环境问题）→ `cron=skipped_tcc`，不计入 failed_deploy_steps；
#     doctor 的 `cron write access` 行负责把它亮成 WARN + FDA 指引。
#   - 其余（语法错、crontab 不存在等）仍是 `cron=fail`，照旧算部署失败步。
# 报错里的 `tmp/tmp.<pid>` 是 crontab 自己的 spool 相对路径（它先 chdir 到
# /usr/lib/cron → /var/at，再写 tmp/tmp.<pid>，与 TMPDIR 无关），所以这里不
# 摆弄环境，只抓 stderr 原文判类。匹配的是 Darwin strerror 的英文（Darwin libc
# 不本地化 strerror）；万一没匹配上就落回 `fail`——旧行为，只会更保守。
apply_crontab() {
    if [ "$NEW_CRON" = "$CURRENT_CRON" ]; then
        report_step "cron" "ok" "already installed"
        return 0
    fi
    if _cron_err="$(printf '%s\n' "$NEW_CRON" | grep -v '^[[:space:]]*$' | crontab - 2>&1)"; then
        ok "crontab rewritten (other lines preserved)"
        report_step "cron" "ok" "ingest chain + digest + telemetry installed"
        return 0
    fi
    [ -n "$_cron_err" ] && printf '%s\n' "$_cron_err" >&2
    warn "crontab update failed — add these lines manually with 'crontab -e':"
    info "$INGEST_CHAIN"
    info "$DIGEST_LINE"
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

CURRENT_CRON="$(crontab -l 2>/dev/null || true)"
NEW_CRON="$CURRENT_CRON"

# idempotent: replace any legacy screenpipe-export line with the unified chain
if printf '%s\n' "$NEW_CRON" | grep -Fq "$INGEST_CHAIN"; then
    ok "ingest cron chain already installed"
else
    NEW_CRON="$(printf '%s\n' "$NEW_CRON" | grep -v 'screenpipe-export\.sh' || true)"
    NEW_CRON="$(printf '%s\n%s\n' "$NEW_CRON" "$INGEST_CHAIN")"
    ok "ingest cron chain installed (legacy screenpipe-export lines replaced)"
fi

# idempotent: exact line present -> keep; otherwise replace any older
# act.digest line (the pre-D19 Monday-only `--now` form would keep forcing a
# weekly card past an `off` knob) with the daily self-gating one.
if printf '%s\n' "$NEW_CRON" | grep -Fq "$DIGEST_LINE"; then
    ok "digest cron already installed"
else
    NEW_CRON="$(printf '%s\n' "$NEW_CRON" | grep -v 'act\.digest' || true)"
    NEW_CRON="$(printf '%s\n%s\n' "$NEW_CRON" "$DIGEST_LINE")"
    ok "digest cron installed (daily 09:07; cadence = digest.frequency, default off)"
fi

# idempotent: append the hourly telemetry sync if absent (default-on anonymous
# usage stats, docs/TELEMETRY.md — the sync is a silent no-op when the user
# opts out, so the line is harmless to keep)
if printf '%s\n' "$NEW_CRON" | grep -q 'act\.analytics_sync'; then
    ok "telemetry sync cron already installed"
else
    NEW_CRON="$(printf '%s\n%s\n' "$NEW_CRON" "$TELEMETRY_LINE")"
    ok "telemetry sync cron installed (hourly; opt out in app Settings or telemetry.enabled: false)"
fi

apply_crontab

# --------------------------------------------------------------------------
echo ""
if [ "$PKG_POSTINSTALL" -eq 1 ]; then
    echo "==> 7. diagnostics — skipped (non-interactive pkg mode; run anytime: bash install.sh --check)"
elif [ "$NON_INTERACTIVE" -eq 1 ]; then
    echo "==> 7. diagnostics — left to the caller (auto-deploy runs the doctor and gates on it, §56)"
elif [ -n "${RUNTIME_PY:-}" ] && [ -x "${RUNTIME_PY:-}" ]; then
    echo "==> 7. post-install diagnostics (python -m act.doctor)"
    if ! (cd "$REPO_ROOT" && AIASSISTANT_HOME="$REPO_ROOT" "$RUNTIME_PY" -m act.doctor); then
        warn "doctor reported problems above — fix them, then re-check: bash install.sh --check"
    fi
else
    echo "==> 7. diagnostics — skipped (no usable python3); run later: bash install.sh --check"
fi

# --------------------------------------------------------------------------
# install report (CONTRACT §23) — what this run actually did, machine-readable
echo ""
write_install_report

# --------------------------------------------------------------------------
# --non-interactive (§56): no banner; the exit code IS the verdict — one per
# failed step, the legacy Mac app (`app`) excepted (see header). The caller
# (scripts/auto-deploy.sh) rolls back on non-zero.
if [ "$NON_INTERACTIVE" -eq 1 ]; then
    FAILED_STEPS="$(failed_deploy_steps)"
    N_FAILED="$(printf '%s' "$FAILED_STEPS" | grep -c . || true)"
    if [ "$N_FAILED" -gt 0 ]; then
        echo "install.sh --non-interactive: $N_FAILED failed step(s): $(printf '%s' "$FAILED_STEPS" | cut -d= -f1 | tr '\n' ' ')"
    else
        echo "install.sh --non-interactive: ok (v${STAMPED_VERSION:-?})"
    fi
    exit "$N_FAILED"
fi

cat <<'EOF'

==============================================
 Install complete. Next steps:
==============================================
 1. Edit config.yaml (Slack IDs, watched people, source paths).
 2. Anthropic API key：推荐打开 App 的设置窗口，把 key 粘贴保存（自动写入
    config/secrets/anthropic-api-key.txt，目录 0700/文件 0600）。
    旧路径 ~/.config/anthropic-key.txt 仍兜底可用（launchd 的 daemon session
    读不了 Keychain OAuth，所以必须有文件形式的 key）。
 3. Grant TCC / privacy permissions:
      - actd (launchd) only touches the repo's state/ + calls claude,
        so it needs NO Full Disk Access.
      - RADAR reads "~/Documents/Obsidian Vault". It runs from the step-6
        crontab ingest chain installed above (`python3 -m act.radar --once`,
        every 30 min) — crontab HAS Full Disk Access once Terminal/cron is
        granted it in System Settings > Privacy > Full Disk Access. There is no
        launchd radar agent to manage: just grant that access and the ingest
        chain picks up the vault.
 4. The board app "Zelin's AI Assistant (Board)" (bundle: Zelin AI Board.app) is
    in /Applications (or ~/Applications); the board server runs as launchd agent
    com.zelin.aiassistant.server on http://127.0.0.1:<server.port, default 47820>/.
    The legacy menu-bar app "Zelin's AI Assistant.app" is left as it was (D3).
 5. First card in 5 minutes: docs/INSTALL.md →「第一张卡（5 分钟）」。
    menu-bar icon → quick capture → approve ✅ → a reviewable draft arrives minutes later
    (needs only claude + API key — no screenpipe/Obsidian material required).
 6. Anything off later? Re-run diagnostics anytime: bash install.sh --check
 7. Upgrading an existing install? One-command post-deploy smoke check:
    bash scripts/smoke-deploy.sh
    （版本匹配 / 二进制本版特征 / actd 活性 / doctor —— 任一不对就非零退出）
==============================================
EOF
