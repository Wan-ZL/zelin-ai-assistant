#!/bin/bash
# scripts/skills_sync.sh — refresh this machine's skill store (CONTRACT §67; D13 / R2.7.2).
#
# The store is the repo's skills/ directory + skills/index.yaml; Claude Code and
# dispatched agents read ~/.claude/skills/<name>. This script makes the second
# match the first on THIS machine:
#   bash scripts/skills_sync.sh          # refresh links (what install.sh runs, step `skills`)
#   bash scripts/skills_sync.sh --pull   # another machine: git pull --ff-only, then refresh
#   --no-defaults   do not switch on default_enabled skills that have no recorded decision
#   --json          print the store snapshot as JSON instead of the one-line summary
#
# What "refresh" does (act/lib/skills.py sync): re-point ~/.claude/skills links
# that still aim at another checkout / a moved repo, refresh store-owned copies
# whose repo version moved, apply `default_enabled: true` where this machine
# recorded no decision (state/skills.json), re-create links the decisions say
# should exist. It NEVER writes into skills/ (git is the only writer, 防腐 #8)
# and NEVER touches a custom copy (the owner's own edit) or a foreign entry.
#
# Interpreter: the same candidate order as scripts/build_version.sh (§55 —
# under launchd every non-platform binary is TCC-judged on its own):
#   $AIASSISTANT_PYTHON → config/runtime.json pin → /usr/bin/python3 → python3 on PATH;
# the first one that can `import yaml` wins.
#
# Exit codes: 0 refreshed · 1 git pull failed · 2 bad usage · 3 skills/index.yaml
# broken · 4 the store refused (custom copy in the way…) · 5 no usable python3.
set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"

usage() {
    sed -n '2,12p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

PULL=0
EXTRA=""
for arg in "$@"; do
    case "$arg" in
        --pull) PULL=1 ;;
        --json|--no-defaults) EXTRA="$EXTRA $arg" ;;
        -h|--help) usage; exit 0 ;;
        *) echo "skills_sync: unknown argument $arg" >&2; usage >&2; exit 2 ;;
    esac
done

if [ "$PULL" -eq 1 ]; then
    if ! git -C "$REPO_ROOT" pull --ff-only; then
        echo "skills_sync: git pull --ff-only failed — resolve the checkout first, then rerun" >&2
        exit 1
    fi
fi

candidates() {
    printf '%s\n' "${AIASSISTANT_PYTHON:-}"
    sed -n 's/.*"python"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$REPO_ROOT/config/runtime.json" 2>/dev/null
    printf '%s\n' /usr/bin/python3 "$(command -v python3 2>/dev/null || true)"
}

PY=""
saved_ifs="$IFS"
IFS='
'
# shellcheck disable=SC2046 # newline word-splitting the candidate list is the point
for cand in $(candidates); do
    IFS="$saved_ifs"
    case "$cand" in /*) ;; *) continue ;; esac
    [ -x "$cand" ] || continue
    if "$cand" -c "import yaml" >/dev/null 2>&1; then PY="$cand"; break; fi
done
IFS="$saved_ifs"

if [ -z "$PY" ]; then
    echo "skills_sync: no python3 that can import yaml (tried \$AIASSISTANT_PYTHON, config/runtime.json, /usr/bin/python3, PATH)" >&2
    exit 5
fi

cd "$REPO_ROOT" || exit 5
# shellcheck disable=SC2086 # EXTRA is a whitelist of literal flags
AIASSISTANT_HOME="$REPO_ROOT" exec "$PY" -m act.lib.skills sync $EXTRA
