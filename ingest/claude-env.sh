#!/bin/bash
# claude-env.sh — the shell half of the §78 headless-claude isolation constants
# (constitution art.13). Sourced by process-screenpipe.sh; running it directly does nothing useful.
# Mode 755 like its siblings: install.sh chmods ingest/*.sh, and a 644 file here showed up
# as a tracked mode change that would make scripts/upgrade.sh refuse the checkout as dirty (§79).
#
# act/lib/config.py + act/llm.py are the Python definition; this file spells the same
# literals for the ingest chain (equality pinned by tests/test_isolation_footprint.py and
# tests/test_ingest_claude_isolation.py). After sourcing:
#
#   CLAUDE_CONFIG_DIR    the app's own Claude Code home (never the owner's), exported
#   DISABLE_AUTOUPDATER  1 — install.sh, not Claude Code's updater, moves the stable copy
#   ANTHROPIC_API_KEY    exported when one resolves (§19 order, below); --bare reads
#                        nothing else, so no key = claude fails loudly in the log
#   CLAUDE_BIN           the same binary recap uses (config.resolve_claude_bin)
#   HEADLESS_CWD         an empty directory outside the repo tree (config.headless_cwd)
#   ISOLATION_ARGV       bash array == act/llm.py ISOLATION_ARGV, expand as
#                        "${ISOLATION_ARGV[@]}" (an array, because --setting-sources
#                        takes an empty-string argument that word splitting would drop)
#
# Expects REPO_ROOT and AIASSISTANT_HOME to be set by the sourcing script. Never fails:
# every python lookup has a plain-shell fallback, because cron must not break on a
# missing dependency; the caller decides what an empty CLAUDE_BIN means.

# AIASSISTANT_CLAUDE_CONFIG_DIR relocates it (test sandbox; same seam as config.py).
# An inherited CLAUDE_CONFIG_DIR is overridden on purpose: a cron or server started from
# inside the owner's own Claude Code session would otherwise hand that session's home on.
CLAUDE_CONFIG_DIR="${AIASSISTANT_CLAUDE_CONFIG_DIR:-$HOME/Library/Application Support/ZelinAIAssistant/claude-config}"
export CLAUDE_CONFIG_DIR
export DISABLE_AUTOUPDATER=1

# shellcheck disable=SC2034  # consumed by the sourcing script (process-screenpipe.sh)
ISOLATION_ARGV=(
    --bare
    --no-session-persistence
    --setting-sources ""
    --strict-mcp-config
    --mcp-config '{"mcpServers":{}}'
)

# The daemon's interpreter from config/runtime.json, else PATH python3.
claude_env_python() {
    local py
    py="$(sed -n 's/.*"python"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$REPO_ROOT/config/runtime.json" 2>/dev/null)"
    [ -x "$py" ] || py="$(command -v python3 2>/dev/null)"
    printf '%s\n' "$py"
}

# First non-blank line of a key file, trimmed (CONTRACT §19: one line, the token only;
# mirrors act/lib/secrets._first_token_line). Empty when the file is missing or blank.
claude_env_first_line() {
    sed -n '/[^[:space:]]/{s/^[[:space:]]*//;s/[[:space:]]*$//;p;q;}' "$1" 2>/dev/null
}

# §19 order, the same as act/llm.runner_env: a key already in the environment, then
# config/secrets/anthropic-api-key.txt, then ~/.config/anthropic-key.txt. Never the
# owner's Claude Code login or the keychain (--bare would not read them anyway).
claude_env_resolve_key() {
    local f key
    [ -n "${ANTHROPIC_API_KEY:-}" ] && return 0
    for f in "$AIASSISTANT_HOME/config/secrets/anthropic-api-key.txt" "$HOME/.config/anthropic-key.txt"; do
        key="$(claude_env_first_line "$f")"
        if [ -n "$key" ]; then
            ANTHROPIC_API_KEY="$key"
            export ANTHROPIC_API_KEY
            return 0
        fi
    done
    unset ANTHROPIC_API_KEY
    return 1
}

CLAUDE_ENV_PY="$(claude_env_python)"
CLAUDE_BIN=""
HEADLESS_CWD=""
if [ -n "$CLAUDE_ENV_PY" ]; then
    CLAUDE_BIN="$(cd "$REPO_ROOT" 2>/dev/null && "$CLAUDE_ENV_PY" -m act.lib.config --print-value claude_bin 2>/dev/null)"
    HEADLESS_CWD="$(cd "$REPO_ROOT" 2>/dev/null && "$CLAUDE_ENV_PY" -m act.lib.config --print-path headless_cwd 2>/dev/null)"
fi
if [ -z "$CLAUDE_BIN" ]; then
    CLAUDE_BIN="$(command -v claude 2>/dev/null || true)"
    [ -n "$CLAUDE_BIN" ] || CLAUDE_BIN="$HOME/.local/bin/claude"
fi
[ -x "$CLAUDE_BIN" ] || CLAUDE_BIN=""
if [ -z "$HEADLESS_CWD" ]; then
    HEADLESS_CWD="$CLAUDE_CONFIG_DIR/cwd"
fi
mkdir -p "$HEADLESS_CWD" 2>/dev/null && chmod 700 "$CLAUDE_CONFIG_DIR" "$HEADLESS_CWD" 2>/dev/null
claude_env_resolve_key || true
