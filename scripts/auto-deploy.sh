#!/bin/bash
# scripts/auto-deploy.sh — 合并即上岗（CONTRACT §56，decision D17）。
#
# The owner Mac follows origin/main by itself: launchd runs this every 10 min
# (act/launchd/com.zelin.aiassistant.autodeploy.plist → `python3 -m
# act.auto_deploy` → this script). One run =
#
#   1. take the lock (HOME mirror dir, stale-PID aware), cap the log, then
#      PROBE VOLUME ACCESS before touching git: stat + read + mktemp in
#      state/ + read install.sh. macOS gates background access to removable
#      volumes per responsible executable (TCC) and a launchd job cannot
#      receive the prompt — the 2026-09-02 timer-fired run moved HEAD to
#      v0.48.11, then `bash install.sh` got EPERM (exit 126), rollback was
#      refused, write_state/notify/rm-lock all EPERM'd; the next run saw
#      HEAD == origin/main and wrote up_to_date while actd still ran v0.48.8.
#      EPERM here = `blocked_tcc`: log the exact interpreter to grant Full
#      Disk Access to, record it in the HOME mirror, notify once per day,
#      exit 0 with nothing changed. HEAD never moves before this passes.
#   2. refuse unless HEAD is on `main`; `git fetch origin main`;
#      HEAD == origin/main → DEPLOYED MEANS RUNNING: up_to_date only when
#      state/install_report.json carries the checkout's version AND
#      state/actd.heartbeat carries it too and is fresh (a stale-but-right
#      heartbeat gets AUTODEPLOY_HEARTBEAT_GRACE seconds to beat again — the
#      Mac just woke, actd is mid-restart). Otherwise `install_incomplete`
#      (mismatch spelled out); the FIRST sighting only records it, the
#      SECOND consecutive run re-runs install.sh ONCE — behind the same CI
#      gate as step 3 (never install a sha CI has not passed, even one the
#      owner pulled by hand); `--force` skips both waits. install.sh is
#      re-run at most AUTODEPLOY_INCOMPLETE_LIMIT times per sha, successes
#      included (a daemon that comes up and dies again every interval must
#      not be reinstalled forever) → then the sha is poisoned + one
#      notification. origin/main is the remembered failed sha → one log
#      line, exit (no retry storm)
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
#      only a hand-run `bash install.sh` does. It DOES build + install the
#      board UI (web/dist + shell app, the `ui` step): toolchain absent =
#      `ui=skipped` (still a success), build broken = `ui=fail` (rollback)
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
#      exactly that, never as "detached"; the store2 verdict is taken on a
#      FROZEN ledger — actd is booted out first (KeepAlive would respawn a
#      mere kill) and re-bootstrapped on every refusal exit — and if
#      state/store2_truth.json APPEARED during this deploy, or state/
#      store2.db's PRAGMA user_version INCREASED (schema bump on an
#      already-active ledger: the marker pre-exists then and alone would
#      miss it), or the user_version probe cannot answer (fail CLOSED:
#      "unknown" is never assumed to be 0), the rollback is REFUSED with a
#      pointer to docs/TROUBLESHOOTING.md「store2 回滚」— resetting the code
#      to a version whose runtime cannot read the ledger the new actd just
#      advanced; stays
#      on main so the next run can still fast-forward) + install.sh again +
#      notify "auto-deploy rolled back to PREV"; that origin/main sha is then
#      remembered as failed and skipped until main moves (or --force).
#  12. every outcome lands in TWO places: the HOME mirror
#      ~/Library/Application Support/ZelinAIAssistant/deploy_state.json (the
#      script's own truth + private bookkeeping; TCC never gates $HOME, so a
#      run that cannot touch /Volumes still records what happened) and
#      state/deploy_state.json in the repo (best-effort projection: dashboard
#      add-only key `deploy_state`, doctor row `auto-deploy`, web header —
#      readers prefer the mirror when it describes this checkout, so a
#      projection the job could not rewrite never masks the mirror's verdict),
#      plus ~/Library/Logs/zelin-ai-assistant/auto-deploy.log (1 MB self-cap).
#      A rollback verdict is ALSO kept in `last_incident` until the next
#      `deployed`: the routine up_to_date write of the following interval
#      must not make a refused rollback disappear from the dashboard.
#
# Never prompts, never pushes, never touches state/ or config/ beyond its own
# files. Exit 0 for every handled outcome (launchd's status column stays
# clean; the verdict lives in deploy_state.json), 1 for a broken environment
# (not a git checkout / no python), 2 for bad usage.
#
# Usage: bash scripts/auto-deploy.sh [--force]
#   --force   forget the remembered failed sha, skip the CI gate, deploy now
#
# Test seams (env, never set by the plist): AUTODEPLOY_LOG_DIR,
# AUTODEPLOY_HOME_DIR, AUTODEPLOY_INSTALL_TIMEOUT, AUTODEPLOY_HEARTBEAT_DEADLINE,
# AUTODEPLOY_HEARTBEAT_FRESH, AUTODEPLOY_HEARTBEAT_GRACE,
# AUTODEPLOY_INCOMPLETE_LIMIT, AUTODEPLOY_BRANCH,
# AUTODEPLOY_CI_REPO, AUTODEPLOY_CI_API, AUTODEPLOY_CI_CHECKS,
# AUTODEPLOY_DOCTOR_RETRIES, AUTODEPLOY_DOCTOR_SETTLE, AUTODEPLOY_TRIGGER,
# AUTODEPLOY_PLIST.
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
STATE_FILE="$REPO_ROOT/state/deploy_state.json"         # projection for dashboard/doctor (best-effort)
# HOME mirror (§56.4): the script's own truth. $HOME is never TCC-gated, the
# repo on a removable volume is (per responsible executable, no prompt for a
# launchd job) — so the lock, the bookkeeping and every verdict live here
# first and are copied into the repo second. Same directory as the §19 home
# pointer (~/Library/Application Support/ZelinAIAssistant/home.txt).
MIRROR_DIR="${AUTODEPLOY_HOME_DIR:-$HOME/Library/Application Support/ZelinAIAssistant}"
MIRROR_FILE="$MIRROR_DIR/deploy_state.json"
LOCK_DIR="$MIRROR_DIR/auto-deploy.lock"
INSTALL_TIMEOUT="${AUTODEPLOY_INSTALL_TIMEOUT:-1800}"   # covers the §56.5 ui step (npm ci + vite build +
                                                        # the thin shell's swiftc, each under its own
                                                        # AIASSISTANT_UI_BUDGET=600 s watchdog)
INSTALL_REPORT="$REPO_ROOT/state/install_report.json"   # §23, written by install.sh at the end of a run
HEARTBEAT_FILE="$REPO_ROOT/state/actd.heartbeat"        # §47.4, written by actd at every phase boundary
HEARTBEAT_DEADLINE="${AUTODEPLOY_HEARTBEAT_DEADLINE:-180}"  # the restarted actd must finish ONE pass
                                                        # (a pass may run `claude agents --json`,
                                                        # >30 s on a loaded machine)
HEARTBEAT_FRESH="${AUTODEPLOY_HEARTBEAT_FRESH:-600}"    # "deployed means running": a heartbeat older
                                                        # than one interval = that version is NOT running
HEARTBEAT_GRACE="${AUTODEPLOY_HEARTBEAT_GRACE:-90}"     # …unless it beats again within this (§47.4
                                                        # STALE_FLOOR: a pass can run >30 s; the Mac may
                                                        # have just woken with actd still asleep)
INCOMPLETE_LIMIT="${AUTODEPLOY_INCOMPLETE_LIMIT:-3}"    # install.sh re-runs per sha (successes included)
                                                        # before the sha is poisoned (+ one notification)
PLIST="${AUTODEPLOY_PLIST:-$HOME/Library/LaunchAgents/com.zelin.aiassistant.autodeploy.plist}"
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
TRIGGER=""      # terminal | launchd | $AUTODEPLOY_TRIGGER (detect_trigger)
INTERP=""       # the plist's ProgramArguments[0] — the binary TCC judges
VOLUME=""       # mount point the repo lives on
_now=""

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
#
# Two files, one truth: the HOME mirror is merged first and MUST succeed (its
# failure is logged as such); the repo copy is the same dict written best-
# effort — on a TCC-blocked volume it simply fails, and the failure line
# carries the child's exception (live 2026-09-01: `PermissionError: [Errno 1]`
# was only in launchd's untimestamped stderr file). The mirror did not exist
# before v0.48.20: when it is absent the merge seeds itself from the repo copy
# so failed_sha / notified_sha bookkeeping survives the upgrade.
#
# Every write also stamps this run's identity (trigger / interpreter / volume /
# repo), and — when the run is NOT attached to a terminal — mirrors `status` /
# `detail` into `unattended_status` / `unattended_last_run` /
# `unattended_detail`: a green `--force` from the owner's terminal inherits the
# terminal's TCC grants and proves nothing about the launchd job; the doctor's
# `launchd volume access` row reads the unattended triple, never the last run.
write_state() {
    _pairs=("$@" "trigger=$TRIGGER" "interpreter=$INTERP" "volume=$VOLUME" "repo=$REPO_ROOT")
    if [ "$TRIGGER" != "terminal" ]; then
        for _p in "$@"; do
            case "$_p" in
                status=*) _pairs+=("unattended_status=${_p#status=}" "unattended_last_run=$_now") ;;
                detail=*) _pairs+=("unattended_detail=${_p#detail=}") ;;
            esac
        done
    fi
    mkdir -p "$MIRROR_DIR" 2>/dev/null
    mkdir -p "$(dirname "$STATE_FILE")" 2>/dev/null
    _wserr="$("$PY" - "$MIRROR_FILE" "$STATE_FILE" "${_pairs[@]}" 2>&1 1>/dev/null <<'PY'
import json, os, sys
mirror, repo_copy, pairs = sys.argv[1], sys.argv[2], sys.argv[3:]

def load(path):
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}

def dump(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, path)

data = load(mirror) if os.path.exists(mirror) else load(repo_copy)
for pair in pairs:
    key, _, value = pair.partition("=")
    if value == "":
        data.pop(key, None)
    else:
        data[key] = value
dump(mirror, data)          # the truth — an exception here is the real failure
try:
    dump(repo_copy, data)   # the projection — best-effort on a gated volume
except Exception as exc:
    sys.stderr.write("%s: %s\n" % (type(exc).__name__, exc))
    sys.exit(3)
PY
)" || {
        if [ "$?" -eq 3 ]; then
            log "write_state: mirror written, repo copy failed (non-fatal): $(printf '%s' "$_wserr" | tail -n 1)"
        else
            log "write_state failed (non-fatal): $(printf '%s' "$_wserr" | tail -n 1)"
        fi
    }
}

# $1=key → its string value, "" when absent/unreadable. The mirror is the
# truth; the repo copy is only consulted while the mirror does not exist yet
# (first run after the v0.48.20 upgrade).
read_state() {
    _src="$MIRROR_FILE"
    [ -f "$_src" ] || _src="$STATE_FILE"
    [ -f "$_src" ] || return 0
    "$PY" - "$_src" "$1" <<'PY' 2>/dev/null || true
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

# Upgrade window (v0.48.20 moved the lock to $HOME): a pre-v0.48.20 run still
# holds state/auto-deploy.lock while it fast-forwards to THIS script and runs
# install.sh — a hand-started run of the new script would otherwise take the
# fresh HOME lock and deploy concurrently (Codex review P1 on #140). Honour a
# LIVE legacy holder; clear a dead one best-effort (the old EXIT trap may have
# EPERM'd on the volume). Drop this once no v0.48.19 checkout is left.
LEGACY_LOCK_DIR="$REPO_ROOT/state/auto-deploy.lock"
legacy_lock_live() {
    [ -d "$LEGACY_LOCK_DIR" ] || return 1
    _lholder="$(cat "$LEGACY_LOCK_DIR/pid" 2>/dev/null || true)"
    if [ -n "$_lholder" ] && kill -0 "$_lholder" 2>/dev/null; then
        log "a pre-v0.48.20 auto-deploy run still holds $LEGACY_LOCK_DIR (pid $_lholder) — skipping"
        return 0
    fi
    if [ -z "$_lholder" ] && [ -z "$(find "$LEGACY_LOCK_DIR" -maxdepth 0 -mmin +2 2>/dev/null)" ]; then
        log "a pre-v0.48.20 run holds $LEGACY_LOCK_DIR without a pid yet — skipping"
        return 0
    fi
    rm -rf "$LEGACY_LOCK_DIR" 2>/dev/null && log "removed stale legacy lock $LEGACY_LOCK_DIR (pid ${_lholder:-?} is gone)"
    return 1
}

take_lock() {
    legacy_lock_live && return 1
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

# How this run was started — the only distinction the process can honestly
# make. `terminal`: a tty is attached or TERM_PROGRAM / SSH_TTY is set = the
# owner (or an orchestrator started from a terminal) ran it, and it INHERITS
# that terminal's TCC grants. `launchd`: none of that = spawned by launchd,
# whether the StartInterval timer fired or someone ran `launchctl kickstart`
# — those two are indistinguishable from inside (same environment, same TCC
# identity: the job's own executable), and both are equally honest evidence
# about unattended runs. A wrapper that knows more sets AUTODEPLOY_TRIGGER.
detect_trigger() {
    if [ -n "${AUTODEPLOY_TRIGGER:-}" ]; then
        printf '%s' "$AUTODEPLOY_TRIGGER" | tr -c 'A-Za-z0-9_-' '_'
        return 0
    fi
    if [ -t 0 ] || [ -t 1 ] || [ -t 2 ] || [ -n "${TERM_PROGRAM:-}" ] || [ -n "${SSH_TTY:-}" ]; then
        printf 'terminal'
    else
        printf 'launchd'
    fi
}

# ProgramArguments[0] of the INSTALLED autodeploy plist — the exact binary
# macOS judges when the job runs (TCC is granted per executable path; the
# python launcher is the responsible process for bash/git/install.sh under
# it). Fallbacks: the launcher's own sys.executable (AIASSISTANT_PYTHON is
# exactly argv0 when we were started by the shim), then $PY.
plist_interpreter() {
    _pi=""
    [ -f "$PLIST" ] && _pi="$(tr -d '\n' < "$PLIST" 2>/dev/null \
        | sed -n 's#.*<key>ProgramArguments</key>[[:space:]]*<array>[[:space:]]*<string>\([^<]*\)</string>.*#\1#p')"
    [ -n "$_pi" ] || _pi="${AIASSISTANT_PYTHON:-}"
    [ -n "$_pi" ] || _pi="$PY"
    printf '%s' "$_pi"
}

# read + write + exec on the repo, BEFORE any git call. One line on stdout:
#   ok
#   denied <errno> <path>    PermissionError (TCC: errno 1 EPERM on macOS)
#   error <errno> <path>     any other OSError (volume unmounted, …)
# Also prints the mount point the repo lives on as a second line.
volume_probe() {
    "$PY" - "$REPO_ROOT" <<'PY' 2>/dev/null || printf 'error ? probe-crashed\n/\n'
import os, sys, tempfile
repo = sys.argv[1]
verdict, where = "ok", ""
try:
    where = repo
    os.stat(repo)
    os.listdir(repo)
    for rel in ("act/__init__.py", "install.sh", "scripts/auto-deploy.sh"):
        where = os.path.join(repo, rel)
        if os.path.exists(where):
            with open(where, "rb") as fh:
                fh.read(1)
    where = os.path.join(repo, "state")
    os.makedirs(where, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".volume-probe.", dir=where)
    os.write(fd, b"probe\n")
    os.close(fd)
    os.unlink(tmp)
except PermissionError as exc:
    verdict = "denied %s %s" % (exc.errno if exc.errno is not None else "?", where)
except OSError as exc:
    verdict = "error %s %s" % (exc.errno if exc.errno is not None else "?", where)
mount = repo
try:
    while mount != os.path.dirname(mount) and not os.path.ismount(mount):
        mount = os.path.dirname(mount)
except OSError:
    parts = repo.split("/")
    mount = "/".join(parts[:3]) if repo.startswith("/Volumes/") and len(parts) > 2 else "/"
print(verdict)
print(mount)
PY
}

# `version` recorded by install.sh in state/install_report.json (§23); "" when
# absent/unreadable — an install that never finished never wrote it.
install_report_version() {
    [ -f "$INSTALL_REPORT" ] || return 0
    "$PY" - "$INSTALL_REPORT" <<'PY' 2>/dev/null || true
import json, sys
try:
    with open(sys.argv[1], encoding="utf-8") as fh:
        v = json.load(fh).get("version", "")
    sys.stdout.write(v if isinstance(v, str) else "")
except Exception:
    pass
PY
}

# Seconds since the heartbeat's `ts`; "-" when absent or unparseable.
heartbeat_age() {
    "$PY" - "$HEARTBEAT_FILE" <<'PY' 2>/dev/null || printf -- '-'
import datetime, json, sys
try:
    with open(sys.argv[1], encoding="utf-8") as fh:
        ts = json.load(fh)["ts"]
    then = datetime.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
    print(max(0, int((datetime.datetime.now(datetime.timezone.utc) - then).total_seconds())))
except Exception:
    print("-")
PY
}

# DEPLOYED MEANS RUNNING (§56.3 step 2). HEAD == origin/main is necessary, not
# sufficient: 2026-09-02 the checkout sat at v0.48.11 while install.sh had
# never run (EPERM, exit 126) and actd still ran v0.48.8 in memory — and the
# next run wrote `up_to_date`. Three facts must agree with the checkout:
# state/install_report.json `version` (install.sh finished on this code),
# state/actd.heartbeat `version` (the daemon in memory IS this code) and the
# heartbeat's age (it is running now, not a corpse's last word). Sets
# MISMATCH_REASON (space-separated tokens) / MISMATCH_WHY (human detail) and
# the two observed versions for the state file; returns 0 when consistent,
# 1 on any mismatch. Globals, not stdout: callers need all four values and a
# $(...) subshell would keep them.
#
# A heartbeat that is right in every way except its age gets HEARTBEAT_GRACE
# seconds to beat again before it counts: launchd fires the interval this job
# missed the moment the Mac wakes, while actd is still finishing the sleep it
# went down in; install.sh by hand is mid-restart; a pass can run >30 s. Any
# other mismatch (wrong version, no report) is not a timing question and gets
# no grace.
RUNNING_VERSION=""
REPORT_VERSION=""
MISMATCH_REASON=""
MISMATCH_WHY=""
running_mismatch() { # $1=checkout version
    _grace="$HEARTBEAT_GRACE"
    while :; do
        running_snapshot "$1"
        if [ "$MISMATCH_REASON" != "heartbeat_stale" ] || [ "$_grace" -le 0 ]; then
            break
        fi
        sleep 1
        _grace=$((_grace - 1))
    done
    [ -z "$MISMATCH_REASON" ]
}
running_snapshot() { # $1=checkout version → sets the four globals from the files as they are now
    REPORT_VERSION="$(install_report_version)"
    _hb="$(heartbeat_fields)"
    RUNNING_VERSION="${_hb%% *}"
    _hb_rest="${_hb#* }"
    _hb_pid="${_hb_rest%% *}"
    _hb_phase="${_hb_rest#* }"
    _age="$(heartbeat_age)"
    _mm_reasons=""
    _mm_why=""
    if [ "$REPORT_VERSION" != "$1" ]; then
        _mm_reasons="install_report_version_mismatch"
        _mm_why="install_report.json says v${REPORT_VERSION:-none}, checkout is v$1 (install.sh never finished on this code)"
    fi
    if [ "$RUNNING_VERSION" = "-" ]; then
        RUNNING_VERSION=""
        _mm_reasons="${_mm_reasons:+$_mm_reasons }heartbeat_missing"
        _mm_why="${_mm_why:+$_mm_why; }no actd heartbeat at all (state/actd.heartbeat absent or torn) — nothing is running"
    elif [ "$RUNNING_VERSION" != "$1" ]; then
        _mm_reasons="${_mm_reasons:+$_mm_reasons }heartbeat_version_mismatch"
        _mm_why="${_mm_why:+$_mm_why; }actd heartbeat says v${RUNNING_VERSION} (pid ${_hb_pid}, phase ${_hb_phase}), checkout is v$1 — the daemon in memory is not this version"
    elif [ "$_age" = "-" ] || [ "$_age" -gt "$HEARTBEAT_FRESH" ]; then
        _mm_reasons="${_mm_reasons:+$_mm_reasons }heartbeat_stale"
        _mm_why="${_mm_why:+$_mm_why; }actd heartbeat is ${_age}s old (> ${HEARTBEAT_FRESH}s) — v$1 is not running"
    fi
    MISMATCH_REASON="$_mm_reasons"
    MISMATCH_WHY="$_mm_why"
}

# blocked_tcc notification, at most once per UTC day (the job fires every 10
# min; the fix is a one-time click in System Settings). The relay queue lives
# in state/notify_queue on the very volume we cannot reach, so this attempt
# will usually fail too — the log line, the HOME mirror and the doctor row are
# the channels that survive; the day stamp is written regardless so the log
# does not fill with 144 identical failures.
notify_tcc_once_daily() { # $1=body
    _today="$(date -u +%Y-%m-%d)"
    [ "$(read_state tcc_notified_day)" = "$_today" ] && return 0
    write_state "tcc_notified_day=$_today"
    notify "自动部署被 macOS 挡住 / auto-deploy blocked (Full Disk Access)" "$1"
}

# Tracked CONTENT changes (mode-only flips ignored: install.sh's own `chmod +x`
# on ingest scripts is not the owner's work and a reset simply restores them).
tracked_content_changes() {
    git_q -c core.fileMode=false status --porcelain --untracked-files=no 2>/dev/null || true
}

# PRAGMA user_version of the store2 ledger; "0" when the DB is genuinely
# absent, "unknown" when the probe errs (EPERM window, locked, corrupt,
# interpreter failure) — NEVER a guessed number: the rollback guard fails
# CLOSED on "unknown", because assuming 0 would silently disarm it in exactly
# the EPERM windows this PR exists for. Read-only URI open — the probe must
# never create the file.
store2_user_version() {
    [ -f "$STORE2_DB" ] || { printf '0'; return 0; }
    "$PY" - "$STORE2_DB" <<'PY' 2>/dev/null || printf 'unknown'
import sqlite3, sys
try:
    con = sqlite3.connect("file:%s?mode=ro" % sys.argv[1], uri=True)
    print(con.execute("PRAGMA user_version").fetchone()[0])
    con.close()
except Exception:
    print("unknown")
PY
}

# §56 rollback TOCTOU: the NEW actd keeps running while rollback() decides —
# it can finish a §53 migration or schema bump between our sample and the
# `reset --hard`. Stop it BEFORE sampling (bootout, not kill: KeepAlive would
# respawn a kill within seconds and reopen the window), decide on a frozen
# ledger, and restart it on every refusal exit — a refusal leaves the NEW
# code in place, so the right daemon for the on-disk ledger is exactly the
# one we stopped. The success path needs no restart: install.sh at PREV
# bootstraps everything. No launchctl (Linux dev box, CI) = no-op — §56
# deploys only to the owner Mac.
ACTD_LABEL="com.zelin.aiassistant.actd"
stop_actd() {
    command -v launchctl >/dev/null 2>&1 || return 0
    launchctl bootout "gui/$(id -u)/$ACTD_LABEL" 2>/dev/null
    return 0
}
restart_actd() {
    command -v launchctl >/dev/null 2>&1 || return 0
    launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/$ACTD_LABEL.plist" 2>/dev/null \
        || log "could not re-bootstrap $ACTD_LABEL after the refused rollback (non-fatal) — run install.sh or launchctl bootstrap by hand"
    return 0
}

# A rollback verdict (`rolled_back` / `rollback_failed`) is written twice: as
# this run's `status` AND as `last_incident` (add-only, projected). `status`
# is overwritten by the very next interval — after a REFUSED rollback HEAD sits
# on the new sha, so that run ends `up_to_date` / `deployed`-by-repair and the
# refusal vanished from the dashboard 10 min after it was raised (#135 review,
# live 2026-09-01). `last_incident` survives every routine write and is cleared
# only by a `deployed` (a real fast-forward or a completed repair).
write_rollback_state() { # $1=status $2=detail, rest = extra key=value pairs
    _rb_status="$1"; _rb_detail="$2"; shift 2
    write_state "status=$_rb_status" "last_run=$_now" "detail=$_rb_detail" \
                "last_incident=$_now $_rb_status: $_rb_detail" "$@"
}

rollback() { # $1=PREV $2=reason $3=target sha  → 0 rolled back, 1 refused / itself failed
    log "ROLLBACK to $(short "$1"): $2"
    # Freeze the ledger before deciding anything (TOCTOU above).
    stop_actd
    # store2 guard (§53 / §56 rollback): the new actd migrating the ledger
    # during THIS deploy — the truth marker appearing (YAML→SQLite, v0.48.8),
    # PRAGMA user_version increasing (schema bump on an already-active
    # ledger; the marker pre-exists then, so it alone would miss this, #135
    # review), or the probe unable to say (fail closed) — means PREV's
    # runtime may not read what is now on disk. Refuse the code rollback —
    # the by-hand procedure is docs/TROUBLESHOOTING.md「store2 回滚」.
    _s2why=""
    _uv_now="$(store2_user_version)"
    if [ "${_store2_before:-1}" -eq 0 ] && [ -f "$STORE2_MARKER" ]; then
        _s2why="store2 became the registry truth during this deploy ($STORE2_MARKER)"
    elif [ "$_uv_now" = "unknown" ] || [ "${_store2_uv_before:-0}" = "unknown" ]; then
        _s2why="store2 schema state is unknown (user_version probe: before=${_store2_uv_before:-?}, now=${_uv_now}) — refusing to assume the ledger did not move"
    elif [ "${_uv_now:-0}" -gt "${_store2_uv_before:-0}" ] 2>/dev/null; then
        _s2why="store2 schema was upgraded during this deploy (user_version ${_store2_uv_before:-0} -> ${_uv_now})"
    fi
    if [ -n "$_s2why" ]; then
        log "rollback REFUSED — $_s2why; a code rollback would strand the SQLite ledger — follow docs/TROUBLESHOOTING.md「store2 回滚」"
        write_rollback_state "rollback_failed" \
                             "rollback refused ($_s2why — code rollback would strand the SQLite truth; see docs/TROUBLESHOOTING.md store2 回滚): $2" \
                             "failed_sha=$3"
        # ${_s2why} braced: bash 3.2 would swallow the first byte of the
        # following fullwidth paren into the variable name (set -u abort).
        notify "自动部署回滚被拒 / auto-deploy rollback REFUSED" \
               "v$(repo_version) 需要回滚（$2），但本次部署中 store2 账本前进了或状态不明（${_s2why}）—— 回退代码可能让账本落在读不了它的版本上；请按 docs/TROUBLESHOOTING.md「store2 回滚」手动处理 $REPO_ROOT"
        restart_actd
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
        write_rollback_state "rollback_failed" "rollback refused ($_why): $2" "failed_sha=$3"
        notify "自动部署回滚被拒 / auto-deploy rollback REFUSED" \
               "v$(repo_version) 需要回滚（$2），但 $_why —— 未 reset 以免丢你的改动；请手动处理 $REPO_ROOT"
        restart_actd
        return 1
    fi
    if ! git_q reset --hard --quiet "$1"; then
        log "git reset --hard $1 FAILED — checkout left at ${_head_now:-unknown}"
        write_rollback_state "rollback_failed" "git reset --hard failed: $2" "failed_sha=$3"
        notify "自动部署回滚失败 / auto-deploy rollback FAILED" \
               "git reset --hard $(short "$1") 失败；请手动检查 $REPO_ROOT ($2)"
        restart_actd
        return 1
    fi
    run_install
    _rc=$?
    if [ "$_rc" -ne 0 ]; then
        log "install.sh at $(short "$1") exited $_rc after the rollback"
        write_rollback_state "rollback_failed" "rolled back to $(short "$1") but install.sh exited $_rc: $2" \
                             "failed_sha=$3" "head=$1" "version=$(repo_version)"
        notify "自动部署回滚失败 / auto-deploy rollback FAILED" \
               "已退回 $(short "$1") 但 install.sh 退出码 ${_rc}；请手动 bash install.sh ($2)"
        return 1
    fi
    write_rollback_state "rolled_back" "$2" "failed_sha=$3" "head=$1" "prev=$1" "version=$(repo_version)"
    notify "自动部署已回滚 / auto-deploy rolled back to $(short "$1")" \
           "$2 —— 已重装旧版；origin/main $(short "$3") 不再重试，修好后 bash scripts/auto-deploy.sh --force 或合并新提交"
    return 0
}

# HEAD == origin/main. `up_to_date` only when the machine is RUNNING this
# code (running_mismatch above); otherwise `install_incomplete`, and install.sh
# is re-run once — not a deploy (no PREV to fall back to, the checkout is
# already where it should be), a repair with the same readiness wait. Three
# brakes, because a repair that fires on a false positive costs an install.sh
# (every daemon restarted) plus a notification:
#   - CONFIRM FIRST: the first run that sees a mismatch only records it
#     (`incomplete_seen=<sha>`); install.sh is re-run by the next run if it
#     still sees one. Every transient the 10-min interval can straddle — the
#     owner's own `bash install.sh` mid-flight, a wake-up before actd's first
#     beat, a report being rewritten — resolves itself before that. `--force`
#     skips the wait (the owner is watching).
#   - BUDGET PER SHA, successes included: `incomplete_runs` counts every
#     install.sh re-run at `incomplete_runs_sha`; at INCOMPLETE_LIMIT the sha
#     is poisoned in its OWN ledger (`incomplete_sha`, not `failed_sha`: a
#     refused rollback — store2 advanced, owner edits — leaves HEAD on the new
#     sha with failed_sha set, and finishing THAT install is exactly the right
#     repair) + ONE notification per sha (`incomplete_notified_sha`). Counting
#     only consecutive failures would let a daemon that comes up, passes once
#     and dies again be reinstalled — and announced — every interval forever.
#     `up_to_date` (the owner repaired by hand, or the daemon is back) clears
#     the poison and the sighting but not the budget; `--force`, a new commit
#     on main (a real `deployed`) re-arm everything.
#   - the same CI gate as step 3 before the re-run.
# The poison check sits INSIDE the mismatch branch: a machine the owner
# repaired by hand becomes `up_to_date` on the next run without any --force.
verify_running() { # $1=sha (HEAD == origin/main)
    _version="$(repo_version)"
    if running_mismatch "$_version"; then
        # up_to_date never touches `last_incident` (a refused rollback's verdict
        # stays on the dashboard until a real deploy) nor the per-sha budget.
        write_state "status=up_to_date" "last_run=$_now" "head=$1" "version=$_version" \
                    "running_version=$RUNNING_VERSION" "install_report_version=$REPORT_VERSION" \
                    "reason=" "incomplete_seen=" "incomplete_sha=" "failed_sha=" "notified_sha=" "detail="
        return 0
    fi
    _reason="$MISMATCH_REASON"
    _why="$MISMATCH_WHY"
    if [ "$(read_state incomplete_sha)" = "$1" ]; then
        log "install still incomplete at $(short "$1") ($_reason) — gave up after ${INCOMPLETE_LIMIT} install.sh re-runs at this sha; waiting for a new commit (or --force, or bash install.sh by hand)"
        write_state "last_run=$_now" "running_version=$RUNNING_VERSION" "install_report_version=$REPORT_VERSION"
        return 0
    fi
    if [ "$FORCE" -ne 1 ] && [ "$(read_state incomplete_seen)" != "$1" ]; then
        log "install_incomplete at $(short "$1") (v${_version:-?}): $_why — first sighting; install.sh is re-run if the next run still sees it"
        write_state "status=install_incomplete" "last_run=$_now" "head=$1" "version=$_version" \
                    "running_version=$RUNNING_VERSION" "install_report_version=$REPORT_VERSION" \
                    "reason=$_reason" "incomplete_seen=$1" \
                    "detail=$_why (first sighting; install.sh is re-run if the next run still sees it)"
        return 0
    fi
    _n="$(read_state incomplete_runs)"
    case "$_n" in ''|*[!0-9]*) _n=0 ;; esac
    [ "$(read_state incomplete_runs_sha)" = "$1" ] || _n=0
    if [ "$_n" -ge "$INCOMPLETE_LIMIT" ]; then
        poison_incomplete "$1" "$_version" "$_reason" \
            "$_why (install.sh already re-run $_n times at this sha and the machine keeps falling out of its running state; giving up until main moves, --force, or bash install.sh by hand)"
        return 0
    fi
    # The repair installs the code that is ALREADY checked out — but §56.5 still
    # holds: never deploy a sha CI has not passed (the owner may have `git pull`ed
    # a red or still-running main by hand; Codex review P1 on #140). Same gate as
    # step 3, same `--force` exit; red poisons THIS ledger (incomplete_sha).
    if [ "$FORCE" -ne 1 ]; then
        _repo="$(github_repo)"
        if [ -z "$_repo" ]; then
            log "install_incomplete at $(short "$1") but CI cannot be verified ($REMOTE is not a github.com remote) — not re-running install.sh; set AUTODEPLOY_CI_REPO=owner/repo"
            write_state "status=install_incomplete" "last_run=$_now" "head=$1" "version=$_version" \
                        "running_version=$RUNNING_VERSION" "install_report_version=$REPORT_VERSION" \
                        "reason=$_reason ci_unverifiable" \
                        "detail=$_why; not re-running install.sh: CI gate impossible ($REMOTE is not a github.com remote; set AUTODEPLOY_CI_REPO)"
            return 0
        fi
        _ci="$(ci_verdict "$_repo" "$1")"
        case "$_ci" in
            success) ;;
            failure*)
                log "install_incomplete at $(short "$1") but CI is RED on it (${_ci#failure }) — not re-running install.sh on a red sha; waiting for a new commit (or --force)"
                write_state "status=install_incomplete" "last_run=$_now" "head=$1" "version=$_version" \
                            "running_version=$RUNNING_VERSION" "install_report_version=$REPORT_VERSION" \
                            "reason=$_reason ci_failed" "incomplete_sha=$1" \
                            "detail=$_why; HEAD $(short "$1") failed CI (${_ci#failure }) — install.sh not re-run on a red sha; wait for a green commit or --force"
                if [ "$(read_state notified_sha)" != "$1" ]; then
                    notify "自动部署未完成：HEAD 的 CI 红了 / auto-deploy: install incomplete, CI red" \
                           "checkout 在 $(short "$1")（v${_version:-?}）但机器没跑起来（${_why}）；该 sha 的 CI 红了，不会在红 sha 上重跑 install.sh——等下一个绿提交，或 bash scripts/auto-deploy.sh --force"
                    write_state "notified_sha=$1"
                fi
                return 0 ;;
            *)
                log "install_incomplete at $(short "$1") but CI is not green yet on it (${_ci#pending }) — will re-run install.sh once it is"
                write_state "status=install_incomplete" "last_run=$_now" "head=$1" "version=$_version" \
                            "running_version=$RUNNING_VERSION" "install_report_version=$REPORT_VERSION" \
                            "reason=$_reason ci_pending" \
                            "detail=$_why; waiting for CI on HEAD $(short "$1") (${_ci#pending }) before re-running install.sh"
                return 0 ;;
        esac
    fi
    _n=$((_n + 1))
    log "install_incomplete at $(short "$1") (v${_version:-?}): $_why — re-running install.sh ($_n/$INCOMPLETE_LIMIT at this sha)"
    # Spent BEFORE the run: an install.sh that takes this process down with it
    # (the 2026-09-02 EPERM shape) must still count against the budget.
    write_state "incomplete_runs=$_n" "incomplete_runs_sha=$1"
    _hb_before="$(heartbeat_fields)"
    run_install
    _rc=$?
    if [ "$_rc" -eq 0 ]; then
        wait_for_new_actd "$_version" "$_hb_before" || log "actd v${_version:-?} completed no pass within ${HEARTBEAT_DEADLINE}s after the re-run (heartbeat now: $(heartbeat_fields))"
    else
        log "install.sh exited $_rc on the re-run"
    fi
    # A non-zero install.sh is never `deployed`, even when report + heartbeat
    # now agree: the exit code counts failed steps (crontab, launchd…) that the
    # report still records under the new version (Codex review P1 on #140).
    if [ "$_rc" -eq 0 ] && running_mismatch "$_version"; then
        _detail="install completed on re-run at $(short "$1") (was: $_why)"
        write_state "status=deployed" "last_run=$_now" "last_deployed=$_now" "head=$1" "version=$_version" \
                    "running_version=$RUNNING_VERSION" "install_report_version=$REPORT_VERSION" \
                    "reason=" "incomplete_seen=" "incomplete_sha=" "failed_sha=" "notified_sha=" "last_incident=" "detail=$_detail"
        log "DEPLOYED v${_version:-?}: $_detail"
        notify "已自动部署 v${_version:-?} / auto-deployed (install re-run)" "$_detail"
        return 0
    fi
    if [ "$_rc" -ne 0 ]; then
        running_mismatch "$_version" || true
        _reason="install_failed${MISMATCH_REASON:+ $MISMATCH_REASON}"
        _why="install.sh exited $_rc${MISMATCH_WHY:+; $MISMATCH_WHY}"
    else
        _reason="$MISMATCH_REASON"
        _why="$MISMATCH_WHY"
    fi
    if [ "$_n" -ge "$INCOMPLETE_LIMIT" ]; then
        poison_incomplete "$1" "$_version" "$_reason" \
            "$_why ($_n install.sh re-runs at this sha did not complete it; giving up until main moves, --force, or bash install.sh by hand)"
        return 0
    fi
    write_state "status=install_incomplete" "last_run=$_now" "head=$1" "version=$_version" \
                "running_version=$RUNNING_VERSION" "install_report_version=$REPORT_VERSION" \
                "reason=$_reason" "detail=$_why (re-run $_n/$INCOMPLETE_LIMIT did not complete it)"
    return 0
}

# The install_incomplete poison: no more install.sh at this sha; ONE
# notification per sha, whatever else happens to it afterwards (a hand repair
# that clears the poison and a daemon that dies again re-poison silently — the
# dashboard and the doctor row carry it, the owner was told once).
poison_incomplete() { # $1=sha $2=version $3=reason $4=detail
    log "install_incomplete at $(short "$1") — install.sh re-run budget (${INCOMPLETE_LIMIT}) spent; poisoning the sha: no more install.sh until main moves or --force"
    write_state "status=install_incomplete" "last_run=$_now" "head=$1" "version=$2" \
                "running_version=$RUNNING_VERSION" "install_report_version=$REPORT_VERSION" \
                "reason=$3" "incomplete_sha=$1" "detail=$4"
    [ "$(read_state incomplete_notified_sha)" = "$1" ] && return 0
    notify "自动部署未完成 / auto-deploy: install incomplete" \
           "v${2:-?} 已 checkout 但没有跑起来（$4）；该 sha 的 install.sh 重跑次数已用完（${INCOMPLETE_LIMIT}），已停止重试——请手动 bash install.sh 或 bash scripts/auto-deploy.sh --force"
    write_state "incomplete_notified_sha=$1"
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
    TRIGGER="$(detect_trigger)"
    INTERP="$(plist_interpreter)"

    # The lock lives in $HOME (never TCC-gated): taking it cannot be what
    # fails, and `rm` at exit cannot leave a corpse the way the 2026-09-02 run
    # did on the volume (next run had to reclaim it as stale).
    take_lock || exit 0
    trap 'rm -rf "$LOCK_DIR" 2>/dev/null || log "could not remove $LOCK_DIR (non-fatal) — the next run reclaims it once pid $$ is gone"' EXIT
    _now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

    # VOLUME ACCESS PROBE — before the first git call. macOS gates background
    # access to removable volumes per responsible executable (TCC) and a
    # launchd job has no UI to receive the prompt; the terminal you test from
    # lends its own grant to every child, so "it works when I run it" proves
    # nothing (2026-09-02: every terminal-started run succeeded, the timer-
    # fired one EPERM'd through install.sh, rollback, write_state, notify and
    # the lock). Denied = record it where we CAN (the HOME mirror + log), name
    # the exact binary to grant, notify once a day, change nothing, exit 0.
    _probe="$(volume_probe)"
    _verdict="${_probe%%$'\n'*}"
    VOLUME="${_probe#*$'\n'}"
    case "$_verdict" in
        ok) ;;
        denied*)
            _errno="$(printf '%s' "$_verdict" | awk '{print $2}')"
            _where="${_verdict#denied * }"
            log "volume_access=denied (errno ${_errno}) — launchd job lacks access to ${VOLUME} (${_where}); grant Full Disk Access to ${INTERP} (System Settings → Privacy & Security → Full Disk Access; also ${HOME}/.local/bin/claude for dispatch; a run started from a terminal inherits the terminal's grant and proves nothing about timer-fired runs) — trigger=${TRIGGER}, nothing changed"
            # `detail` is projected into the dashboard (and, in cloud mode, into the
            # encrypted snapshot syncd uploads) — keep local paths out of it; the
            # exact volume / interpreter / denied path live in mirror-only keys the
            # doctor's `launchd volume access` row renders (§0 第 9 条).
            write_state "status=blocked_tcc" "last_run=$_now" "reason=volume_access_denied" "denied_path=${_where}" \
                        "detail=volume_access=denied (errno ${_errno}): the ${TRIGGER}-started job cannot read/write the repo's volume; grant Full Disk Access to the job's interpreter (exact paths: doctor launchd volume access row)"
            notify_tcc_once_daily "后台部署任务读不到 ${VOLUME}（errno ${_errno}，macOS 按程序授权、launchd 任务收不到弹窗）。系统设置 → 隐私与安全性 → 完全磁盘访问 → 加入 ${INTERP} 与 ${HOME}/.local/bin/claude；终端里跑绿了不算，等 timer 自己跑一轮。HEAD 未动、什么都没改。"
            exit 0 ;;
        *)
            log "volume probe: ${_verdict} — repo not reachable right now (unmounted?); will retry next interval"
            write_state "status=failed" "last_run=$_now" "reason=volume_access_error" \
                        "detail=volume probe ${_verdict%% *} (errno $(printf '%s' "$_verdict" | awk '{print $2}')): the repo is not reachable right now, will retry"
            exit 0 ;;
    esac

    if ! git_q rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        log "$REPO_ROOT is not a git checkout — auto-deploy needs a clone, not a .pkg copy"
        exit 1
    fi

    if [ "$FORCE" -eq 1 ]; then
        log "--force: forgetting failed/notified/incomplete shas"
        write_state "failed_sha=" "notified_sha=" "incomplete_sha=" "incomplete_seen=" \
                    "incomplete_runs=" "incomplete_runs_sha=" "incomplete_notified_sha="
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
        verify_running "$TARGET"
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
    _hb_now="$(heartbeat_fields)"
    write_state "status=deployed" "last_run=$_now" "last_deployed=$_now" \
                "head=$NEW" "prev=$PREV" "version=$VERSION" \
                "running_version=${_hb_now%% *}" "install_report_version=$(install_report_version)" \
                "reason=" "incomplete_seen=" "incomplete_runs=" "incomplete_runs_sha=" "incomplete_sha=" \
                "incomplete_notified_sha=" "failed_sha=" "notified_sha=" "last_incident=" "detail=$_detail"
    log "DEPLOYED v${VERSION:-?}: $_detail"
    notify "已自动部署 v${VERSION:-?} / auto-deployed" "$_detail"
    exit 0
}

main "$@"
