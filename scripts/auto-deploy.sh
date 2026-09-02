#!/bin/bash
# scripts/auto-deploy.sh — 合并即上岗（CONTRACT §56，decision D17）。
#
# The owner Mac follows origin/main by itself: launchd runs this every 10 min
# (act/launchd/com.zelin.aiassistant.autodeploy.plist → `python3 -m
# act.auto_deploy` → this script). One run =
#
#   1. take the lock (state/auto-deploy.lock/, stale-PID aware), cap the log
#   2. refuse unless HEAD is on `main`; `git fetch origin main`;
#      HEAD == origin/main → record up_to_date, exit; origin/main is the
#      remembered failed sha → one log line, exit (no retry storm)
#   3. CI GATE on the exact origin/main sha: the GitHub check-runs API must
#      say the `ci` run on THAT commit completed green. The ruleset only
#      requires green on the PR head (non-strict), so a merge commit's tree
#      may never have been tested; the `ci` run on main takes ~8 min and this
#      job fires at +10 — not done yet / API unreachable = `ci_pending`, retry
#      next interval; red = `ci_failed`, sha poisoned, one notification.
#      `--force` skips the gate (the owner asked for THIS sha).
#   4. refuse when the tracked tree is dirty (the owner's work in progress —
#      never touched; one notification per pending commit, then silence)
#   5. PREV=HEAD; `git merge --ff-only origin/main` (diverged local main =
#      refuse + notify, never force)
#   6. SELF-CHECK the new deploy agent (`bash -n` this script, `import
#      act.auto_deploy`): a merge that breaks either would silently end every
#      future deploy — rollback before installing anything
#   7. doctor BASELINE with the NEW code, still before installing — a doctor
#      that cannot even produce JSON (import error, no yaml) is fatal here,
#      never "pre-existing": rollback without installing
#   8. `bash install.sh --non-interactive` under a watchdog timeout — that
#      mode never rebuilds the frozen legacy Mac app (§56.5: build.sh would
#      quit + relaunch it and take screenpipe / live captions down mid-use);
#      only a hand-run `bash install.sh` does
#   9. READINESS: wait until state/actd.heartbeat (§47.4) is written by a NEW
#      actd process (pid changed), stamped with the NEW version, in phase
#      `idle` = one full pass completed on the new code. Deadline
#      (AUTODEPLOY_HEARTBEAT_DEADLINE, 180 s) → FAIL
#      `actd:no_heartbeat_from_new_version` → rollback. A fixed settle was a
#      coin: a KeepAlive actd that dies on import shows a pid ~0.5 s of every
#      throttle cycle, and the old daemon's heartbeat/dashboard files stay
#      "fresh" for 90 s.
#  10. doctor again — as a settle-aware retry loop, not a single sample. A
#      verdict taken seconds after install.sh restarted every daemon is a
#      coin (first contact 2026-09-01: the verdict ran 12 s after restart,
#      mid store2 first-run migration and inside a transient EPERM window on
#      the external volume — 6 false "new FAIL"s, one spurious rollback):
#      up to AUTODEPLOY_DOCTOR_RETRIES (3) runs, AUTODEPLOY_DOCTOR_SETTLE
#      (45 s) apart, and only the FINAL run's new-vs-baseline FAILs are real
#      → rollback (`doctor:unparseable` is never in the baseline — fatal
#      there — so a transient one retries the same way). Pre-existing red is
#      reported, not blamed on the new version (otherwise a machine with one
#      stale finding could never update — including to the fix).
#  11. rollback = `git reset --hard PREV` (re-verified right before: still on
#      main, no tracked content edits since step 5 — otherwise the rollback
#      is REFUSED and notified rather than destroying the owner's work; a git
#      that cannot even answer (EPERM window, volume offline) is reported as
#      exactly that, never as "detached"; and if state/store2_truth.json
#      APPEARED during this deploy — or state/store2.db's PRAGMA user_version
#      INCREASED (schema bump on an already-active ledger: the marker
#      pre-exists then and alone would miss it) — the rollback is REFUSED
#      with a pointer to docs/TROUBLESHOOTING.md「store2 回滚」— resetting
#      the code to a version whose runtime cannot read the ledger the new
#      actd just advanced; stays
#      on main so the next run can still fast-forward) + install.sh again +
#      notify "auto-deploy rolled back to PREV"; that origin/main sha is then
#      remembered as failed and skipped until main moves (or --force).
#  12. every outcome lands in state/deploy_state.json (dashboard add-only key
#      `deploy_state`, doctor row `auto-deploy`, web header) and in
#      ~/Library/Logs/zelin-ai-assistant/auto-deploy.log (1 MB self-cap).
#
# Never prompts, never pushes, never touches state/ or config/ beyond its own
# two files. Exit 0 for every handled outcome (launchd's status column stays
# clean; the verdict lives in deploy_state.json), 1 for a broken environment
# (not a git checkout / no python), 2 for bad usage.
#
# Usage: bash scripts/auto-deploy.sh [--force]
#   --force   forget the remembered failed sha, skip the CI gate, deploy now
#
# Test seams (env, never set by the plist): AUTODEPLOY_LOG_DIR,
# AUTODEPLOY_INSTALL_TIMEOUT, AUTODEPLOY_HEARTBEAT_DEADLINE, AUTODEPLOY_BRANCH,
# AUTODEPLOY_CI_REPO, AUTODEPLOY_CI_API, AUTODEPLOY_CI_CHECKS,
# AUTODEPLOY_DOCTOR_RETRIES, AUTODEPLOY_DOCTOR_SETTLE.
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
INSTALL_TIMEOUT="${AUTODEPLOY_INSTALL_TIMEOUT:-1800}"   # no swift build in this mode; generous anyway
HEARTBEAT_FILE="$REPO_ROOT/state/actd.heartbeat"        # §47.4, written by actd at every phase boundary
HEARTBEAT_DEADLINE="${AUTODEPLOY_HEARTBEAT_DEADLINE:-180}"  # the restarted actd must finish ONE pass
                                                        # (a pass may run `claude agents --json`,
                                                        # >30 s on a loaded machine)
DOCTOR_RETRIES="${AUTODEPLOY_DOCTOR_RETRIES:-3}"        # post-install doctor verdict: attempts (§56.3 step 10)
DOCTOR_SETTLE="${AUTODEPLOY_DOCTOR_SETTLE:-45}"         # seconds between those attempts
STORE2_MARKER="$REPO_ROOT/state/store2_truth.json"      # §53 activation marker — rollback guard
STORE2_DB="$REPO_ROOT/state/store2.db"                  # §53 ledger — its PRAGMA user_version guards too
CI_API="${AUTODEPLOY_CI_API:-https://api.github.com}"
CI_CHECKS="${AUTODEPLOY_CI_CHECKS:-ci}"                 # check-run names that must be green on the
                                                        # deployed sha (comma-separated); `ci` is the
                                                        # macOS job: compileall + full unittest +
                                                        # version tri-pin
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
# On failure the child's stderr (last line = the exception, e.g. the live
# 2026-09-01 `PermissionError: [Errno 1]` EPERM window) lands in OUR log —
# stderr inherited from launchd goes to a different file with no timestamps,
# which made the first live failure undiagnosable from here.
write_state() {
    mkdir -p "$(dirname "$STATE_FILE")" 2>/dev/null
    _wserr="$("$PY" - "$STATE_FILE" "$@" 2>&1 1>/dev/null <<'PY'
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
)" || log "write_state failed (non-fatal): $(printf '%s' "$_wserr" | tail -n 1)"
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

# §28 relay queue — the app posts it with the product identity. Never fatal,
# but the failure line carries the child's exception (same rationale as
# write_state: the live EPERM window was invisible from this log). notify()
# itself never raises — a swallowed queue-write failure only returns False —
# so map False to exit 1 with a hint, or that loss would be silent here too.
notify() { # $1=title $2=body
    _nerr="$( (cd "$REPO_ROOT" && AIASSISTANT_HOME="$REPO_ROOT" PYTHONPATH="$REPO_ROOT" \
        "$PY" -c 'import sys
from act.lib import notify
if not notify.notify(sys.argv[1], sys.argv[2]):
    sys.stderr.write("notify.notify returned False (state/notify_queue unwritable?)\n")
    sys.exit(1)' "$1" "$2") 2>&1 >/dev/null )" \
        || log "notify failed (non-fatal): $1 — $(printf '%s' "$_nerr" | tail -n 1)"
}

# Sorted FAIL check names from `act.doctor --fast --json`, one per line. The
# doctor's exit code alone cannot separate "the new version broke X" from "X
# was already red"; names can. Unparseable output (doctor crashed on import,
# printed garbage, interpreter lost yaml…) is itself a named failure — and
# main() treats it as fatal on EITHER run: both runs use the new code, so an
# unparseable baseline would be "pre-existing" and blind the one safety gate
# to exactly the class of commit it exists to catch.
UNPARSEABLE="doctor:unparseable"
doctor_fail_names() {
    (cd "$REPO_ROOT" && AIASSISTANT_HOME="$REPO_ROOT" PYTHONPATH="$REPO_ROOT" \
        "$PY" -m act.doctor --fast --json 2>/dev/null) | "$PY" -c 'import json, sys
try:
    rows = json.load(sys.stdin).get("checks", [])
    names = sorted({str(r.get("name")) for r in rows if r.get("status") == "fail"})
except Exception:
    names = [sys.argv[1]]
sys.stdout.write("\n".join(names))' "$UNPARSEABLE"
}

has_line() { # $1=newline-separated list, $2=exact line
    printf '%s\n' "$1" | grep -qxF -- "$2"
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

# owner/repo of the GitHub remote ("" when origin is not on github.com —
# then the CI gate cannot run and main() refuses rather than guessing).
github_repo() {
    if [ -n "${AUTODEPLOY_CI_REPO:-}" ]; then
        printf '%s' "$AUTODEPLOY_CI_REPO"
        return 0
    fi
    git_q remote get-url "$REMOTE" 2>/dev/null \
        | sed -n -E 's#/*$##; s#\.git$##; s#^.*github\.com[:/]([^/]+)/([^/]+)$#\1/\2#p'
}

# CI verdict for one commit from the GitHub check-runs API — unauthenticated
# (public repo; ≤6 calls/h against the 60/h limit), `-f` so any HTTP error is
# "unreachable". `filter=latest` (the API default) already collapses re-runs to
# the newest run per check; the highest id wins if several still come back.
# One line on stdout:
#   success                       every name in CI_CHECKS completed green
#   failure <name> <conclusion>   a required run completed non-green
#   pending <why>                 no run yet / still running / API unreachable
ci_verdict() { # $1=owner/repo $2=sha
    _body="$(curl -fsS --max-time 30 -H 'Accept: application/vnd.github+json' \
                  -H 'X-GitHub-Api-Version: 2022-11-28' \
                  "$CI_API/repos/$1/commits/$2/check-runs?per_page=100" 2>>"$LOG")" \
        || { printf 'pending check-runs API unreachable'; return 0; }
    printf '%s' "$_body" | "$PY" -c 'import json, sys
required = [n.strip() for n in sys.argv[1].split(",") if n.strip()]

def verdict():
    try:
        runs = json.load(sys.stdin).get("check_runs", [])
    except Exception:
        return "pending check-runs API returned no JSON"
    latest = {}
    for run in runs:
        name = str(run.get("name"))
        if name not in required:
            continue
        try:
            rid = int(run.get("id") or 0)
        except (TypeError, ValueError):
            rid = 0
        if name not in latest or rid > latest[name][0]:
            latest[name] = (rid, run)
    for name in required:
        if name not in latest:
            return "pending no %s check-run yet" % name
        run = latest[name][1]
        if run.get("status") != "completed":
            return "pending %s is %s" % (name, run.get("status"))
        if run.get("conclusion") != "success":
            return "failure %s %s" % (name, run.get("conclusion"))
    return "success"

sys.stdout.write(verdict())' "$CI_CHECKS"
}

# The deploy agent must still be able to run its own successor: a merge that
# breaks this script or the launchd shim would otherwise end every future
# deploy silently (launchd's status column being the only witness). Prints the
# first broken piece; "" when both are fine. `$BASH` = the very binary running
# this script (the shim execs /bin/bash), not whatever bash PATH finds first.
self_check() {
    "${BASH:-bash}" -n "$REPO_ROOT/scripts/auto-deploy.sh" 2>>"$LOG" \
        || { printf 'scripts/auto-deploy.sh does not parse (bash -n)'; return 0; }
    (cd "$REPO_ROOT" && AIASSISTANT_HOME="$REPO_ROOT" PYTHONPATH="$REPO_ROOT" \
        "$PY" -c 'import act.auto_deploy' 2>>"$LOG") \
        || printf 'act.auto_deploy does not import'
}

# "<version> <pid> <phase>" of state/actd.heartbeat (§47.4); "-" per missing
# field, "- - -" when the file is absent or torn.
heartbeat_fields() {
    "$PY" - "$HEARTBEAT_FILE" <<'PY' 2>/dev/null || printf -- '- - -'
import json, sys
try:
    with open(sys.argv[1], encoding="utf-8") as fh:
        hb = json.load(fh)
    sys.stdout.write("%s %s %s" % (hb.get("version") or "-", hb.get("pid") or "-",
                                   hb.get("phase") or "-"))
except Exception:
    sys.stdout.write("- - -")
PY
}

# Readiness of the actd that install.sh just restarted (§56.3 step 9): the
# heartbeat must come from a NEW process (pid differs from the pre-install
# beat — a same-version deploy would otherwise be satisfied by the OLD
# daemon's file), carry the NEW version, and say `idle` = one full pass
# completed on the new code. `failed` (the pass threw) counts only when the
# pre-install daemon was already failing its passes — pre-existing, not the
# new version's fault. Anything else until the deadline = not ready.
wait_for_new_actd() { # $1=version $2=pre-install "<version> <pid> <phase>" → 0 ready, 1 deadline
    _old_pid="$(printf '%s' "$2" | awk '{print $2}')"
    _old_phase="$(printf '%s' "$2" | awk '{print $3}')"
    _waited=0
    while :; do
        _hb="$(heartbeat_fields)"
        _hb_version="${_hb%% *}"
        _hb_rest="${_hb#* }"
        _hb_pid="${_hb_rest%% *}"
        _hb_phase="${_hb_rest#* }"
        if [ "$_hb_version" = "$1" ] && [ "$_hb_pid" != "$_old_pid" ]; then
            [ "$_hb_phase" = "idle" ] && return 0
            [ "$_hb_phase" = "failed" ] && [ "$_old_phase" = "failed" ] && return 0
        fi
        [ "$_waited" -ge "$HEARTBEAT_DEADLINE" ] && return 1
        sleep 1
        _waited=$((_waited + 1))
    done
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
    if [ -z "$_holder" ] && [ -z "$(find "$LOCK_DIR" -maxdepth 0 -mmin +2 2>/dev/null)" ]; then
        # No pid file yet and the directory is fresh: the other instance won
        # mkdir a moment ago and has not written its pid — a live holder, not
        # a stale lock (reclaiming here would let two runs merge/install/reset
        # at once). A pid-less lock is only stale once it is older than 2 min
        # (a crash between mkdir and printf).
        log "lock held by a run that has not written its pid yet — skipping"
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

# Tracked CONTENT changes (mode-only flips ignored: install.sh's own `chmod +x`
# on ingest scripts is not the owner's work and a reset simply restores them).
tracked_content_changes() {
    git_q -c core.fileMode=false status --porcelain --untracked-files=no 2>/dev/null || true
}

# PRAGMA user_version of the store2 ledger; "0" when the DB is absent or
# unreadable. Read-only URI open — this probe must never create the file.
store2_user_version() {
    [ -f "$STORE2_DB" ] || { printf '0'; return 0; }
    "$PY" - "$STORE2_DB" <<'PY' 2>/dev/null || printf '0'
import sqlite3, sys
try:
    con = sqlite3.connect("file:%s?mode=ro" % sys.argv[1], uri=True)
    print(con.execute("PRAGMA user_version").fetchone()[0])
    con.close()
except Exception:
    print(0)
PY
}

rollback() { # $1=PREV $2=reason $3=target sha  → 0 rolled back, 1 refused / itself failed
    log "ROLLBACK to $(short "$1"): $2"
    # store2 guard (§53 / §56 rollback): the new actd migrating the ledger
    # during THIS deploy — either the truth marker appearing (YAML→SQLite,
    # v0.48.8) or PRAGMA user_version increasing (schema bump on an already-
    # active ledger; the marker pre-exists then, so it alone would miss this,
    # #135 review) — means PREV's runtime cannot read what is now on disk.
    # Refuse the code rollback — the by-hand procedure (restore the backup
    # first) is docs/TROUBLESHOOTING.md「store2 回滚」.
    _s2why=""
    if [ "${_store2_before:-1}" -eq 0 ] && [ -f "$STORE2_MARKER" ]; then
        _s2why="store2 became the registry truth during this deploy ($STORE2_MARKER)"
    else
        _uv_now="$(store2_user_version)"
        [ "${_uv_now:-0}" -gt "${_store2_uv_before:-0}" ] 2>/dev/null \
            && _s2why="store2 schema was upgraded during this deploy (user_version ${_store2_uv_before:-0} -> ${_uv_now})"
    fi
    if [ -n "$_s2why" ]; then
        log "rollback REFUSED — $_s2why; a code rollback would strand the SQLite ledger — follow docs/TROUBLESHOOTING.md「store2 回滚」"
        write_state "status=rollback_failed" "last_run=$_now" "failed_sha=$3" \
                    "detail=rollback refused ($_s2why — code rollback would strand the SQLite truth; see docs/TROUBLESHOOTING.md store2 回滚): $2"
        # ${_s2why} braced: bash 3.2 would swallow the first byte of the
        # following fullwidth paren into the variable name (set -u abort).
        notify "自动部署回滚被拒 / auto-deploy rollback REFUSED" \
               "v$(repo_version) 需要回滚（$2），但本次部署中 store2 账本前进了（${_s2why}）—— 回退代码会让账本落在读不了它的版本上；请按 docs/TROUBLESHOOTING.md「store2 回滚」手动处理 $REPO_ROOT"
        return 1
    fi
    # Re-verify the checkout right before `reset --hard`: minutes have passed
    # since step 4 (install + settle + doctor) and the owner may have edited a
    # tracked file or switched branches in that window. `reset --hard` would
    # silently destroy that work (宪法 §0.2: no irreversible automatic
    # deletion) — so refuse, leave the new version in place, and say so.
    # symbolic-ref: rc 1 (with -q) = genuinely detached; rc >1 = git could not
    # read the checkout at all (live 2026-09-01: an EPERM window made both
    # git calls come back empty and the refusal blamed a phantom 'detached')
    # — its own refusal, reported as a git failure, never as a branch verdict.
    _brc=0
    _branch_now="$(git_q symbolic-ref --short -q HEAD 2>/dev/null)" || _brc=$?
    _head_now="$(git_q rev-parse --verify -q HEAD 2>/dev/null)" || true
    _dirty="$(tracked_content_changes)"
    _why=""
    if [ "$_brc" -gt 1 ]; then
        _why="git cannot read the checkout (symbolic-ref exit $_brc — volume/permissions hiccup?)"
    elif [ -n "$_dirty" ]; then
        _why="tracked edits since the deploy started: $(printf '%s' "$_dirty" | awk '{print $2}' | head -n 5 | tr '\n' ' ')"
    elif [ "$_branch_now" != "$BRANCH" ]; then
        _why="HEAD is on '${_branch_now:-detached}'"
    fi
    if [ -n "$_why" ]; then
        log "rollback REFUSED — $_why; checkout left at ${_head_now:-unknown}"
        write_state "status=rollback_failed" "last_run=$_now" "failed_sha=$3" \
                    "detail=rollback refused ($_why): $2"
        notify "自动部署回滚被拒 / auto-deploy rollback REFUSED" \
               "v$(repo_version) 需要回滚（$2），但 $_why —— 未 reset 以免丢你的改动；请手动处理 $REPO_ROOT"
        return 1
    fi
    if ! git_q reset --hard --quiet "$1"; then
        log "git reset --hard $1 FAILED — checkout left at ${_head_now:-unknown}"
        write_state "status=rollback_failed" "last_run=$_now" "failed_sha=$3" \
                    "detail=git reset --hard failed: $2"
        notify "自动部署回滚失败 / auto-deploy rollback FAILED" \
               "git reset --hard $(short "$1") 失败；请手动检查 $REPO_ROOT ($2)"
        return 1
    fi
    run_install
    _rc=$?
    if [ "$_rc" -ne 0 ]; then
        log "install.sh at $(short "$1") exited $_rc after the rollback"
        write_state "status=rollback_failed" "last_run=$_now" "failed_sha=$3" "head=$1" \
                    "version=$(repo_version)" "detail=rolled back to $(short "$1") but install.sh exited $_rc: $2"
        notify "自动部署回滚失败 / auto-deploy rollback FAILED" \
               "已退回 $(short "$1") 但 install.sh 退出码 ${_rc}；请手动 bash install.sh ($2)"
        return 1
    fi
    write_state "status=rolled_back" "last_run=$_now" "failed_sha=$3" "head=$1" "prev=$1" \
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
    # A failed removal (live 2026-09-01: EPERM on the volume) leaves a lock
    # the next run must reclaim as stale — say so instead of failing silently.
    trap 'rm -rf "$LOCK_DIR" 2>/dev/null || log "could not remove state/auto-deploy.lock (non-fatal) — the next run reclaims it once pid $$ is gone"' EXIT

    _now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    if [ "$FORCE" -eq 1 ]; then
        log "--force: forgetting failed/notified shas"
        write_state "failed_sha=" "notified_sha="
    fi

    # rc 1 (with -q) = genuinely detached → refused_branch; rc >1 = git could
    # not read the checkout (EPERM window / volume offline) — an environment
    # hiccup, not a branch verdict: report it as such and retry next interval.
    _brc=0
    _branch="$(git_q symbolic-ref --short -q HEAD 2>>"$LOG")" || _brc=$?
    if [ "$_brc" -gt 1 ]; then
        log "git symbolic-ref failed (rc $_brc) — checkout unreadable right now (volume/permissions?); will retry next interval"
        write_state "status=failed" "last_run=$_now" \
                    "detail=git cannot read HEAD (symbolic-ref rc $_brc) — checkout unreadable, will retry"
        exit 0
    fi
    if [ "$_branch" != "$BRANCH" ]; then
        log "HEAD is not on $BRANCH (got '${_branch:-detached}') — refusing to touch this checkout"
        write_state "status=refused_branch" "last_run=$_now" \
                    "head=$(git_q rev-parse --verify -q HEAD 2>/dev/null)" "version=$(repo_version)" \
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
    # §53 ledger state as of this run's start: if the marker APPEARS or the
    # DB's user_version INCREASES during the deploy (the new actd migrated
    # the registry truth / bumped the schema), rollback() refuses.
    _store2_before=0
    [ -f "$STORE2_MARKER" ] && _store2_before=1
    _store2_uv_before="$(store2_user_version)"

    if [ "$PREV" = "$TARGET" ]; then
        write_state "status=up_to_date" "last_run=$_now" "head=$PREV" \
                    "version=$(repo_version)" "failed_sha=" "notified_sha=" "detail="
        exit 0
    fi

    if [ "$(read_state failed_sha)" = "$TARGET" ]; then
        log "origin/$BRANCH $(short "$TARGET") already failed (deploy rolled back, or CI red) — waiting for a new commit (or --force)"
        write_state "last_run=$_now"
        exit 0
    fi

    # CI gate (§56.3 step 3): green on THIS sha, not on the PR head that
    # produced it. Runs before the dirty-tree check so a red main is reported
    # (and poisoned) whatever this machine's tree looks like.
    if [ "$FORCE" -eq 1 ]; then
        log "--force: CI gate skipped for $(short "$TARGET")"
    else
        _repo="$(github_repo)"
        if [ -z "$_repo" ]; then
            _remote_url="$(git_q remote get-url "$REMOTE" 2>/dev/null || true)"
            log "cannot tell the GitHub repo from '$_remote_url' — CI gate impossible, not deploying (set AUTODEPLOY_CI_REPO=owner/repo)"
            write_state "status=failed" "last_run=$_now" "head=$PREV" "version=$(repo_version)" \
                        "detail=CI gate: $REMOTE is not a github.com remote; set AUTODEPLOY_CI_REPO=owner/repo"
            if [ "$(read_state notified_sha)" != "$TARGET" ]; then
                notify "自动部署无法验 CI / auto-deploy: cannot verify CI" \
                       "origin/$BRANCH $(short "$TARGET") 待部署，但 $REMOTE 不是 github.com 远端，无法查 CI；请设 AUTODEPLOY_CI_REPO"
                write_state "notified_sha=$TARGET"
            fi
            exit 0
        fi
        _ci="$(ci_verdict "$_repo" "$TARGET")"
        case "$_ci" in
            success)
                log "CI green on $(short "$TARGET") ($CI_CHECKS)" ;;
            failure*)
                log "CI RED on origin/$BRANCH $(short "$TARGET"): ${_ci#failure } — not deploying; waiting for a new commit (or --force)"
                write_state "status=ci_failed" "last_run=$_now" "head=$PREV" "version=$(repo_version)" \
                            "failed_sha=$TARGET" \
                            "detail=origin/$BRANCH $(short "$TARGET") failed CI (${_ci#failure }); not deployed"
                notify "main 的 CI 红了，未部署 / auto-deploy: main CI red" \
                       "origin/$BRANCH $(short "$TARGET") 的 ${_ci#failure }；未部署，等下一个绿的提交（或 bash scripts/auto-deploy.sh --force）"
                exit 0 ;;
            *)
                log "CI not green yet on $(short "$TARGET"): ${_ci#pending } — will retry next interval"
                write_state "status=ci_pending" "last_run=$_now" "head=$PREV" "version=$(repo_version)" \
                            "detail=waiting for CI on origin/$BRANCH $(short "$TARGET"): ${_ci#pending }"
                exit 0 ;;
        esac
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

    _self="$(self_check)"
    if [ -n "$_self" ]; then
        rollback "$PREV" "self-check on v${VERSION:-?} ($(short "$NEW")): $_self — the deploy agent could not run again" "$TARGET"
        exit 0
    fi

    _baseline="$(doctor_fail_names)"
    [ -n "$_baseline" ] && log "doctor baseline (pre-install) FAIL: $(printf '%s' "$_baseline" | tr '\n' ' ')"
    if has_line "$_baseline" "$UNPARSEABLE"; then
        rollback "$PREV" "doctor unparseable on v${VERSION:-?} ($(short "$NEW")) — new code cannot even run its own diagnostics" "$TARGET"
        exit 0
    fi

    _hb_before="$(heartbeat_fields)"
    run_install
    _rc=$?
    if [ "$_rc" -ne 0 ]; then
        rollback "$PREV" "install.sh exited $_rc on v${VERSION:-?} ($(short "$NEW"))" "$TARGET"
        exit 0
    fi

    if ! wait_for_new_actd "$VERSION" "$_hb_before"; then
        rollback "$PREV" "actd:no_heartbeat_from_new_version — actd on v${VERSION:-?} ($(short "$NEW")) completed no pass within ${HEARTBEAT_DEADLINE}s (heartbeat now: $(heartbeat_fields); before install: $_hb_before) — crash loop or stall" "$TARGET"
        exit 0
    fi
    log "actd v${VERSION:-?} completed a pass (heartbeat: $(heartbeat_fields))"
    # Settle-before-verdict (§56.3 step 10): a doctor sample taken seconds
    # after install.sh restarted every daemon is a coin — first contact
    # 2026-09-01 (v0.48.8) took it 12 s in, mid store2 first-run migration and
    # inside a transient EPERM window: 6 false "new FAIL"s, one spurious
    # rollback. Only new FAILs that survive to the FINAL attempt are real.
    # `doctor:unparseable` is never in the baseline (fatal there, above), so
    # new_names carries a transient one through the same retries.
    _attempt=1
    while :; do
        _after="$(doctor_fail_names)"
        _new="$(new_names "$_baseline" "$_after")"
        [ -z "$_new" ] && break
        if [ "$_attempt" -ge "$DOCTOR_RETRIES" ]; then
            rollback "$PREV" "doctor new FAIL after v${VERSION:-?} (persisted through $_attempt runs ${DOCTOR_SETTLE}s apart): $(printf '%s' "$_new" | tr '\n' ' ')" "$TARGET"
            exit 0
        fi
        log "doctor new FAIL (attempt $_attempt/$DOCTOR_RETRIES): $(printf '%s' "$_new" | tr '\n' ' ') — daemons may still be settling; doctor again in ${DOCTOR_SETTLE}s"
        sleep "$DOCTOR_SETTLE"
        _attempt=$((_attempt + 1))
    done

    _detail="deployed $(short "$PREV") -> $(short "$NEW")"
    if [ -n "$_after" ]; then
        _detail="$_detail; doctor pre-existing FAIL: $(printf '%s' "$_after" | tr '\n' ' ')"
    fi
    write_state "status=deployed" "last_run=$_now" "last_deployed=$_now" \
                "head=$NEW" "prev=$PREV" "version=$VERSION" \
                "failed_sha=" "notified_sha=" "detail=$_detail"
    log "DEPLOYED v${VERSION:-?}: $_detail"
    notify "已自动部署 v${VERSION:-?} / auto-deployed" "$_detail"
    exit 0
}

main "$@"
