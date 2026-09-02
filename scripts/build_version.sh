#!/bin/bash
# The version a build must ship (CONTRACT §56.1) — used by mac/build.sh and
# shell/build.sh to stamp CFBundleShortVersionString / CFBundleVersion.
#
#   VERSION="$(bash scripts/build_version.sh)" || build_failed "..."
#
# stdout: exactly one line, the version. stderr: what was tried. Exit 1 = no
# answer — callers FAIL the build instead of shipping the Info.plist
# placeholder (2026-09-02, v0.48.21 first contact: install.sh's ui step built
# and installed a shell bundle (then "Zelin AI Board.app") reporting 0.1.0 because the build script
# swallowed the stamper's stderr and kept the placeholder when VERSION came
# back empty).
#
# WHICH python matters (§55 第三幕): TCC judges every non-platform binary on
# its own, even as a child of the launchd job's FDA-granted interpreter — under
# auto-deploy a Homebrew python3 first on PATH cannot even open a file of a
# checkout on an external volume (`[Errno 1] Operation not permitted`), while
# Apple's /usr/bin/python3 can. Candidates, most trusted first:
#   $AIASSISTANT_PYTHON      the launchd job's own interpreter (act/auto_deploy.py hands it down)
#   config/runtime.json      the daemon pin a previous install.sh proved viable (§19/§55)
#   /usr/bin/python3         Apple's platform binary
#   python3 on PATH          whatever the shell resolves (a terminal lends its own grant)
# Each is tried twice: scripts/version_stamp.py --write (also writes the
# git-ignored act/_version.py the daemons shipped next to this app read), then
# the SAME decision read-only (scripts/version_stamp.py without --write: git
# describe first, an existing stamp only when git cannot answer, else the
# baked fallback) — which still answers when the stamper can read the checkout
# but not WRITE it. NOT `import act; act.__version__`: that puts the stamp
# first, so a stale act/_version.py (an older deploy's, a hand-written one)
# would label a new build with the OLD number (Codex review P1 on #145). The
# first non-empty answer wins.
set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"

candidates() {
    printf '%s\n' "${AIASSISTANT_PYTHON:-}"
    sed -n 's/.*"python"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$REPO_ROOT/config/runtime.json" 2>/dev/null
    printf '%s\n' /usr/bin/python3 "$(command -v python3 2>/dev/null || true)"
}

err_file="$(mktemp 2>/dev/null || printf '/tmp/zai-build-version.%s' "$$")"
trap 'rm -f "$err_file"' EXIT
tried=""; why=""
saved_ifs="$IFS"
IFS='
'
# shellcheck disable=SC2046 # newline word-splitting the candidate list is the point
for py in $(candidates); do
    IFS="$saved_ifs"
    case "$py" in /*) ;; *) continue ;; esac
    [ -x "$py" ] || continue
    case " $tried " in *" $py "*) continue ;; esac
    tried="$tried $py"
    version="$("$py" "$REPO_ROOT/scripts/version_stamp.py" --write 2>"$err_file")"
    rc=$?
    if [ "$rc" -eq 0 ] && [ -n "$version" ]; then
        echo "build_version: $version (scripts/version_stamp.py --write via $py)" >&2
        [ -z "$why" ] || echo "build_version: skipped interpreter(s) that could not run the stamper — $why" >&2
        printf '%s\n' "$version"
        exit 0
    fi
    last="$(tail -n 1 "$err_file" 2>/dev/null | tr -d '\r')"
    last="${last:-exit $rc, no stderr}"
    why="${why:+$why; }$py: $last"
    version="$("$py" "$REPO_ROOT/scripts/version_stamp.py" 2>"$err_file")"
    if [ -n "$version" ]; then
        echo "build_version: WARN scripts/version_stamp.py --write failed via $py ($last) — using its read-only answer $version instead; act/_version.py was NOT written" >&2
        printf '%s\n' "$version"
        exit 0
    fi
    last="$(tail -n 1 "$err_file" 2>/dev/null | tr -d '\r')"
    why="$why; read-only: ${last:-no stderr}"
done
IFS="$saved_ifs"

if [ -z "$tried" ]; then
    echo "build_version: ERROR no python3 (tried AIASSISTANT_PYTHON, config/runtime.json, /usr/bin/python3, PATH) — cannot derive the version" >&2
else
    echo "build_version: ERROR no interpreter could derive the version — $why" >&2
fi
exit 1
