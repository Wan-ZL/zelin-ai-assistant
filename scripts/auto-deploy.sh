#!/bin/bash
# scripts/auto-deploy.sh — 合并即上岗（CONTRACT §56，decision D17）。
#
# The owner Mac follows origin/main by itself: launchd runs this every 10 min
# (act/launchd/com.zelin.aiassistant.autodeploy.plist → `python3 -m
# act.auto_deploy` → this script). One run =
#
#   1. take the lock (state/auto-deploy.lock/, stale-PID aware), cap the log
#   2. refuse unless HEAD is on `main` AND the tracked tree is clean
#      (a dirty tree is the owner's work in progress — never touched; one
#      notification per pending origin/main commit, then silence)
#   3. `git fetch origin main`; HEAD == origin/main → record up_to_date, exit
#   4. PREV=HEAD; `git merge --ff-only origin/main` (diverged local main =
#      refuse + notify, never force)
#   5. doctor BASELINE with the NEW code, before installing anything
#   6. `bash install.sh --non-interactive` under a watchdog timeout
#   7. settle, doctor again; rollback iff the install failed or a check that
#      was green in the baseline is now FAIL. Pre-existing red is reported,
#      not blamed on the new version (otherwise a machine with one stale
#      finding could never update — including to the fix).
#   8. rollback = `git reset --hard PREV` (tree is clean; stays on main so
#      the next run can still fast-forward) + install.sh again + notify
#      "auto-deploy rolled back to PREV"; that origin/main sha is then
#      remembered as failed and skipped until main moves (or --force).
#   9. every outcome lands in state/deploy_state.json (dashboard add-only key
#      `deploy_state`, doctor row `auto-deploy`, web header) and in
#      ~/Library/Logs/zelin-ai-assistant/auto-deploy.log (1 MB self-cap).
#
# Never prompts, never pushes, never touches state/ or config/ beyond its own
# two files. Exit 0 for every handled outcome (launchd's status column stays
# clean; the verdict lives in deploy_state.json), 1 for a broken environment
# (not a git checkout / no python), 2 for bad usage.
#
# Usage: bash scripts/auto-deploy.sh [--force]
#   --force   forget the remembered failed sha and retry it now
#
# Test seams (env, never set by the plist): AUTODEPLOY_LOG_DIR,
# AUTODEPLOY_INSTALL_TIMEOUT, AUTODEPLOY_DOCTOR_SETTLE, AUTODEPLOY_BRANCH.
set -uo pipefail

# Everything lives in functions and runs from main "$@" at the very end: bash
# then has the whole file parsed before the ff-merge in step 4 replaces this
# very script on disk.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd -P)"
BRANCH="${AUTODEPLOY_BRANCH:-main}"
REMOTE=origin
LOG_DIR="${AUTODEPLOY_LOG_DIR:-$HOME/Library/Logs/zelin-ai-assistant}"
LOG="$LOG_DIR/auto-deploy.log"
LOG_CAP_BYTES=1048576                     # 防腐 #4：日志必有帽
STATE_FILE="$REPO_ROOT/state/deploy_state.json"
LOCK_DIR="$REPO_ROOT/state/auto-deploy.lock"
INSTALL_TIMEOUT="${AUTODEPLOY_INSTALL_TIMEOUT:-1800}"   # swift build included
DOCTOR_SETTLE="${AUTODEPLOY_DOCTOR_SETTLE:-5}"          # agents need a moment
FORCE=0
PY=""

log() {
    _ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf '%s %s\n' "$_ts" "$1" >> "$LOG"
    [ -t 1 ] && printf '%s\n' "$1"
    return 0
}

cap_log() {
    [ -f "$LOG" ] || return 0
    _size="$(wc -c < "$LOG" | tr -d ' ')"
    [ "$_size" -gt "$LOG_CAP_BYTES" ] || return 0
    tail -c "$((LOG_CAP_BYTES / 2))" "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
    log "log capped (was ${_size} bytes)"
}

# The interpreter launchd/cron run (CONTRACT §19/§55): the launcher passes its
# own sys.executable; a manual run reads the pin; last resort system python.
pick_python() {
    for _cand in "${AIASSISTANT_PYTHON:-}" \
                 "$(sed -n 's/.*"python"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' \
                        "$REPO_ROOT/config/runtime.json" 2>/dev/null)" \
                 "$(command -v python3 2>/dev/null)" /usr/bin/python3; do
        case "$_cand" in /*) ;; *) continue ;; esac
        [ -x "$_cand" ] && { printf '%s' "$_cand"; return 0; }
    done
    return 1
}

git_q() { git -C "$REPO_ROOT" "$@"; }

short() { printf '%s' "${1:0:7}"; }

repo_version() {
    sed -n 's/^__version__ = "\([^"]*\)".*/\1/p' "$REPO_ROOT/act/__init__.py" 2>/dev/null
}

# Merge key=value pairs into deploy_state.json (atomic tmp+rename). An empty
# value deletes the key. Values are all strings; readers type-check per field
# (act/lib/deploy_state.py). Written on every run: `status`/`last_run` describe
# THIS run, `last_deployed`/`prev` the last successful deploy (carried over).
write_state() {
    mkdir -p "$(dirname "$STATE_FILE")"
    "$PY" - "$STATE_FILE" "$@" <<'PY' || log "write_state failed (non-fatal)"
import json, os, sys
path, pairs = sys.argv[1], sys.argv[2:]
try:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        data = {}
except Exception:
    data = {}
for pair in pairs:
    key, _, value = pair.partition("=")
    if value == "":
        data.pop(key, None)
    else:
        data[key] = value
tmp = path + ".tmp"
with open(tmp, "w", encoding="utf-8") as fh:
    json.dump(data, fh, ensure_ascii=False, indent=2, sort_keys=True)
    fh.write("\n")
os.replace(tmp, path)
PY
}

read_state() { # $1=key → its string value, "" when absent/unreadable
    [ -f "$STATE_FILE" ] || return 0
    "$PY" - "$STATE_FILE" "$1" <<'PY' 2>/dev/null || true
import json, sys
try:
    with open(sys.argv[1], encoding="utf-8") as fh:
        value = json.load(fh).get(sys.argv[2], "")
    sys.stdout.write(value if isinstance(value, str) else "")
except Exception:
    pass
PY
}

# §28 relay queue — the app posts it with the product identity. Never fatal.
notify() { # $1=title $2=body
    (cd "$REPO_ROOT" && AIASSISTANT_HOME="$REPO_ROOT" PYTHONPATH="$REPO_ROOT" \
        "$PY" -c 'import sys
from act.lib import notify
notify.notify(sys.argv[1], sys.argv[2])' "$1" "$2") >/dev/null 2>&1 \
        || log "notify failed (non-fatal): $1"
}

# Sorted FAIL check names from `act.doctor --fast --json`, one per line. The
# doctor's exit code alone cannot separate "the new version broke X" from "X
# was already red"; names can. Unparseable output is itself a named failure.
doctor_fail_names() {
    (cd "$REPO_ROOT" && AIASSISTANT_HOME="$REPO_ROOT" PYTHONPATH="$REPO_ROOT" \
        "$PY" -m act.doctor --fast --json 2>/dev/null) | "$PY" -c 'import json, sys
try:
    rows = json.load(sys.stdin).get("checks", [])
    names = sorted({str(r.get("name")) for r in rows if r.get("status") == "fail"})
except Exception:
    names = ["doctor:unparseable"]
sys.stdout.write("\n".join(names))'
}

# Lines of $2 that are not in $1 (both newline-separated name lists).
new_names() {
    _before="$(mktemp)"
    printf '%s\n' "$1" > "$_before"
    printf '%s\n' "$2" | grep -v '^$' | grep -Fxv -f "$_before" || true
    rm -f "$_before"
}

# Run a command with a wall-clock limit; 124 on timeout (children reaped too).
run_with_timeout() { # $1=seconds, rest=command
    _limit="$1"; shift
    "$@" &
    _pid=$!
    _waited=0
    while kill -0 "$_pid" 2>/dev/null; do
        if [ "$_waited" -ge "$_limit" ]; then
            log "timeout after ${_limit}s — killing pid $_pid and its children"
            pkill -TERM -P "$_pid" 2>/dev/null
            kill -TERM "$_pid" 2>/dev/null
            sleep 2
            pkill -KILL -P "$_pid" 2>/dev/null
            kill -KILL "$_pid" 2>/dev/null
            wait "$_pid" 2>/dev/null
            return 124
        fi
        sleep 1
        _waited=$((_waited + 1))
    done
    wait "$_pid"
}

# install.sh in its never-prompting mode; output goes to our log. The env flag
# tells install.sh it is running INSIDE the autodeploy agent, so it re-renders
# that one plist but does not bootout/bootstrap it (that would kill this run).
run_install() {
    log "install.sh --non-interactive (timeout ${INSTALL_TIMEOUT}s)"
    AIASSISTANT_AUTODEPLOY_ACTIVE=1 run_with_timeout "$INSTALL_TIMEOUT" \
        bash "$REPO_ROOT/install.sh" --non-interactive >> "$LOG" 2>&1
}

# `app` step of the §23 install report ("" when unreadable): the Mac app is a
# frozen legacy surface (D3) whose build failure leaves the installed app
# untouched, so it is reported alongside the deploy rather than rolling it back.
install_report_app_status() {
    "$PY" - "$REPO_ROOT/state/install_report.json" <<'PY' 2>/dev/null || true
import json, sys
try:
    with open(sys.argv[1], encoding="utf-8") as fh:
        steps = json.load(fh).get("steps", [])
    for step in steps:
        if step.get("name") == "app":
            sys.stdout.write(str(step.get("status", "")))
            break
except Exception:
    pass
PY
}

take_lock() {
    mkdir -p "$(dirname "$LOCK_DIR")"
    if mkdir "$LOCK_DIR" 2>/dev/null; then
        printf '%s\n' "$$" > "$LOCK_DIR/pid"
        return 0
    fi
    _holder="$(cat "$LOCK_DIR/pid" 2>/dev/null || true)"
    if [ -n "$_holder" ] && kill -0 "$_holder" 2>/dev/null; then
        log "another auto-deploy run is active (pid $_holder) — skipping"
        return 1
    fi
    log "removing stale lock (pid ${_holder:-?} is gone)"
    rm -rf "$LOCK_DIR"
    if mkdir "$LOCK_DIR" 2>/dev/null; then
        printf '%s\n' "$$" > "$LOCK_DIR/pid"
        return 0
    fi
    log "could not take the lock — skipping"
    return 1
}

# shellcheck disable=SC2329 # invoked through the EXIT trap in main
release_lock() { rm -rf "$LOCK_DIR"; }

rollback() { # $1=PREV $2=reason $3=target sha  → 0 rolled back, 1 rollback itself failed
    log "ROLLBACK to $(short "$1"): $2"
    _dirty="$(git_q status --porcelain --untracked-files=no 2>/dev/null || true)"
    [ -n "$_dirty" ] && log "tracked changes discarded by the rollback: $(printf '%s' "$_dirty" | tr '\n' ' ')"
    if ! git_q reset --hard --quiet "$1"; then
        log "git reset --hard $1 FAILED — checkout left at $(git_q rev-parse HEAD 2>/dev/null)"
        write_state "status=rollback_failed" "failed_sha=$3" "detail=git reset --hard failed: $2"
        notify "自动部署回滚失败 / auto-deploy rollback FAILED" \
               "git reset --hard $(short "$1") 失败；请手动检查 $REPO_ROOT ($2)"
        return 1
    fi
    run_install
    _rc=$?
    if [ "$_rc" -ne 0 ]; then
        log "install.sh at $(short "$1") exited $_rc after the rollback"
        write_state "status=rollback_failed" "failed_sha=$3" "head=$1" \
                    "version=$(repo_version)" "detail=rolled back to $(short "$1") but install.sh exited $_rc: $2"
        notify "自动部署回滚失败 / auto-deploy rollback FAILED" \
               "已退回 $(short "$1") 但 install.sh 退出码 ${_rc}；请手动 bash install.sh ($2)"
        return 1
    fi
    write_state "status=rolled_back" "failed_sha=$3" "head=$1" "prev=$1" \
                "version=$(repo_version)" "detail=$2"
    notify "自动部署已回滚 / auto-deploy rolled back to $(short "$1")" \
           "$2 —— 已重装旧版；origin/main $(short "$3") 不再重试，修好后 bash scripts/auto-deploy.sh --force 或合并新提交"
    return 0
}

usage() {
    printf 'usage: bash scripts/auto-deploy.sh [--force]\n' >&2
    exit 2
}

main() {
    for arg in "$@"; do
        case "$arg" in
            --force) FORCE=1 ;;
            *) usage ;;
        esac
    done

    mkdir -p "$LOG_DIR"
    cap_log
    if ! PY="$(pick_python)"; then
        log "no python3 found — cannot run"
        exit 1
    fi
    if ! git_q rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        log "$REPO_ROOT is not a git checkout — auto-deploy needs a clone, not a .pkg copy"
        exit 1
    fi

    take_lock || exit 0
    trap release_lock EXIT

    _now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    if [ "$FORCE" -eq 1 ]; then
        log "--force: forgetting failed/notified shas"
        write_state "failed_sha=" "notified_sha="
    fi

    _branch="$(git_q symbolic-ref --short -q HEAD 2>/dev/null || true)"
    if [ "$_branch" != "$BRANCH" ]; then
        log "HEAD is not on $BRANCH (got '${_branch:-detached}') — refusing to touch this checkout"
        write_state "status=refused_branch" "last_run=$_now" \
                    "head=$(git_q rev-parse HEAD)" "version=$(repo_version)" \
                    "detail=HEAD is on '${_branch:-detached}', not $BRANCH"
        exit 0
    fi

    if ! GIT_TERMINAL_PROMPT=0 \
         GIT_SSH_COMMAND="${GIT_SSH_COMMAND:-ssh -oBatchMode=yes -oConnectTimeout=30}" \
         git_q fetch --quiet "$REMOTE" "$BRANCH" 2>>"$LOG"; then
        log "git fetch $REMOTE $BRANCH failed (offline? ssh agent?) — will retry next interval"
        write_state "status=fetch_failed" "last_run=$_now" \
                    "head=$(git_q rev-parse HEAD)" "version=$(repo_version)" \
                    "detail=git fetch $REMOTE $BRANCH failed"
        exit 0
    fi

    PREV="$(git_q rev-parse HEAD)"
    TARGET="$(git_q rev-parse "refs/remotes/$REMOTE/$BRANCH" 2>/dev/null || git_q rev-parse FETCH_HEAD)"

    if [ "$PREV" = "$TARGET" ]; then
        write_state "status=up_to_date" "last_run=$_now" "head=$PREV" \
                    "version=$(repo_version)" "failed_sha=" "notified_sha=" "detail="
        exit 0
    fi

    if [ "$(read_state failed_sha)" = "$TARGET" ]; then
        log "origin/$BRANCH $(short "$TARGET") already failed deploy and was rolled back — waiting for a new commit (or --force)"
        exit 0
    fi

    _dirty="$(git_q status --porcelain --untracked-files=no)"
    if [ -n "$_dirty" ]; then
        _files="$(printf '%s' "$_dirty" | awk '{print $2}' | head -n 5 | tr '\n' ' ')"
        log "working tree has tracked changes ($_files) — refusing to deploy $(short "$TARGET")"
        write_state "status=refused_dirty" "last_run=$_now" "head=$PREV" \
                    "version=$(repo_version)" "detail=dirty tracked files: $_files"
        if [ "$(read_state notified_sha)" != "$TARGET" ]; then
            notify "自动部署暂停：工作树有改动 / auto-deploy refused: dirty tree" \
                   "origin/$BRANCH $(short "$TARGET") 待部署，但 $REPO_ROOT 有未提交改动：${_files}—— commit/stash 后自动继续"
            write_state "notified_sha=$TARGET"
        fi
        exit 0
    fi

    log "deploying $(short "$PREV") -> $(short "$TARGET")"
    if ! git_q merge --ff-only --quiet "$TARGET" 2>>"$LOG"; then
        log "fast-forward to $(short "$TARGET") impossible (local $BRANCH diverged?) — refusing"
        write_state "status=failed" "last_run=$_now" "head=$PREV" \
                    "version=$(repo_version)" "detail=git merge --ff-only $(short "$TARGET") failed"
        if [ "$(read_state notified_sha)" != "$TARGET" ]; then
            notify "自动部署失败：无法 fast-forward / auto-deploy: cannot fast-forward" \
                   "本地 $BRANCH 与 origin/$BRANCH 分叉；请在 $REPO_ROOT 手动处理"
            write_state "notified_sha=$TARGET"
        fi
        exit 0
    fi
    NEW="$(git_q rev-parse HEAD)"
    VERSION="$(repo_version)"
    log "checkout now at $(short "$NEW") (v${VERSION:-?})"

    _baseline="$(doctor_fail_names)"
    [ -n "$_baseline" ] && log "doctor baseline (pre-install) FAIL: $(printf '%s' "$_baseline" | tr '\n' ' ')"

    run_install
    _rc=$?
    if [ "$_rc" -ne 0 ]; then
        rollback "$PREV" "install.sh exited $_rc on v${VERSION:-?} ($(short "$NEW"))" "$TARGET"
        exit 0
    fi

    [ "$DOCTOR_SETTLE" -gt 0 ] && sleep "$DOCTOR_SETTLE"
    _after="$(doctor_fail_names)"
    _new="$(new_names "$_baseline" "$_after")"
    if [ -n "$_new" ]; then
        rollback "$PREV" "doctor new FAIL after v${VERSION:-?}: $(printf '%s' "$_new" | tr '\n' ' ')" "$TARGET"
        exit 0
    fi

    _detail="deployed $(short "$PREV") -> $(short "$NEW")"
    if [ -n "$_after" ]; then
        _detail="$_detail; doctor pre-existing FAIL: $(printf '%s' "$_after" | tr '\n' ' ')"
    fi
    if [ "$(install_report_app_status)" = "fail" ]; then
        _detail="$_detail; mac app build failed (previous app kept)"
    fi
    write_state "status=deployed" "last_run=$_now" "last_deployed=$_now" \
                "head=$NEW" "prev=$PREV" "version=$VERSION" \
                "failed_sha=" "notified_sha=" "detail=$_detail"
    log "DEPLOYED v${VERSION:-?}: $_detail"
    notify "已自动部署 v${VERSION:-?} / auto-deployed" "$_detail"
    exit 0
}

main "$@"
