#!/bin/bash
# One-click installer for Zelin's AI Assistant on LINUX (v1 beta).
#
# The Linux mirror of install.sh. Linux v1 ships the headless core + systemd
# user units + the local web dashboard (the Linux UI) + Slack self-DM capture +
# notify-send desktop notifications. See docs/LINUX.md for exactly what works
# and what is DEFERRED (the Mac SwiftUI app; the screenpipe screen-ingest chain).
#
# What it does:
#   1. dependency checks (python3 + PyYAML required; claude required for
#      dispatch/extraction; gh + notify-send optional)
#   2. config.example.yaml -> config.yaml + config/runtime.json + secrets dir 0700
#   3. create state/ and state/inbox/ + seed state/dashboard.json
#   4. render act/systemd/*.service|*.timer (via `python3 -m act.lib.systemd`)
#      into ~/.config/systemd/user, then `systemctl --user enable --now` the
#      resident services (actd + webui) and the radar/digest timers
#   5. run the post-install diagnostics (python3 -m act.doctor)
#
# Run from anywhere; it locates the repo root via its own path.
#
# --check: run the doctor (python3 -m act.doctor) and exit with the number of
#   failing checks. Installs/changes nothing.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$SCRIPT_DIR"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

ok()   { printf "  [ ok ] %s\n" "$1"; }
warn() { printf "  [warn] %s\n" "$1"; }
info() { printf "  [info] %s\n" "$1"; }
err()  { printf "  [ERR ] %s\n" "$1" >&2; }

# The pinned daemon interpreter (config/runtime.json "python"), or a plain
# python3 fallback. Empty string when nothing usable is found.
runtime_python() {
    local rj="$REPO_ROOT/config/runtime.json"
    local py=""
    if [ -f "$rj" ]; then
        py="$(sed -n 's/.*"python"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$rj")"
    fi
    if [ -n "$py" ] && [ -x "$py" ]; then
        printf '%s' "$py"
        return 0
    fi
    command -v python3 2>/dev/null || true
}

# --check: delegate to act/doctor.py (its systemd branch validates the units)
if [ "${1:-}" = "--check" ]; then
    DOCTOR_PY="$(runtime_python)"
    [ -n "$DOCTOR_PY" ] || DOCTOR_PY="/usr/bin/python3"
    cd "$REPO_ROOT" || exit 1
    AIASSISTANT_HOME="$REPO_ROOT" exec "$DOCTOR_PY" -m act.doctor
fi

if [ "$(uname -s)" != "Linux" ]; then
    err "install-linux.sh targets Linux. On macOS run: bash install.sh"
    exit 1
fi

# --------------------------------------------------------------------------
echo "==> 1. dependencies"
PY="$(command -v python3 2>/dev/null || true)"
if [ -z "$PY" ]; then
    err "python3 not found — install Python 3.9+ (e.g. apt install python3 python3-pip)"
    exit 1
fi
ok "python3: $PY"

if ! command -v claude >/dev/null 2>&1; then
    warn "claude not found — dispatch and radar extraction need it (https://code.claude.com/docs/en/setup)"
else
    ok "claude: $(command -v claude)"
fi

if command -v gh >/dev/null 2>&1; then
    ok "gh found (optional — draft-PR delivery)"
else
    warn "gh not found (optional — cards deliver as local branches without it)"
fi

if command -v notify-send >/dev/null 2>&1; then
    ok "notify-send found (desktop notifications)"
else
    info "notify-send not found (optional; desktop notifications no-op on a headless box — Slack self-DM still works)"
fi

# The claude the LOGIN SHELL resolves — its directory goes FIRST on every unit
# PATH (systemd --user does not source ~/.profile; the 2026-07-08 "outdated
# claude shadowed the new one" guard the plists carry, applied to units).
CLAUDE_LOGIN_BIN=""
_c="$("${SHELL:-/bin/bash}" -lc 'command -v claude' 2>/dev/null | tail -n 1 || true)"
case "$_c" in
    /*) [ -x "$_c" ] && CLAUDE_LOGIN_BIN="$_c" ;;
esac
if [ -z "$CLAUDE_LOGIN_BIN" ]; then
    CLAUDE_LOGIN_BIN="$(command -v claude 2>/dev/null || true)"
fi
CLAUDE_BIN_DIR="$HOME/.local/bin"
if [ -n "$CLAUDE_LOGIN_BIN" ]; then
    CLAUDE_BIN_DIR="$(dirname "$CLAUDE_LOGIN_BIN")"
    ok "daemon claude dir: $CLAUDE_BIN_DIR (first on the unit PATH)"
else
    warn "claude not resolvable from the login shell — unit PATH falls back to $CLAUDE_BIN_DIR first"
fi

# --------------------------------------------------------------------------
echo ""
echo "==> 2. config.yaml + config/runtime.json + secrets"
if [ -f "$REPO_ROOT/config.yaml" ]; then
    ok "config.yaml already exists (left untouched)"
else
    cp "$REPO_ROOT/config.example.yaml" "$REPO_ROOT/config.yaml"
    ok "created config.yaml from config.example.yaml — review it before first run"
fi

if [ ! -f "$REPO_ROOT/config/redaction_terms.txt" ] \
    && [ -f "$REPO_ROOT/config/redaction_terms.example.txt" ]; then
    cp "$REPO_ROOT/config/redaction_terms.example.txt" \
        "$REPO_ROOT/config/redaction_terms.txt"
    ok "created config/redaction_terms.txt from template (gitignored)"
fi

# runtime python pointer (CONTRACT §19): $AIASSISTANT_PYTHON override, else the
# python3 found above. Pin the interpreter the units + doctor run.
mkdir -p "$REPO_ROOT/config"
RUNTIME_PY="$PY"
if [ -n "${AIASSISTANT_PYTHON:-}" ] && [ -x "${AIASSISTANT_PYTHON:-}" ]; then
    RUNTIME_PY="$AIASSISTANT_PYTHON"
fi
printf '{"python": "%s"}\n' "$RUNTIME_PY" > "$REPO_ROOT/config/runtime.json"
ok "config/runtime.json -> $RUNTIME_PY"

# secrets dir 0700; tighten any key file already present to 0600 (CONTRACT §19).
# A systemd --user session has no Keychain, so the Anthropic key MUST be a file.
mkdir -p "$REPO_ROOT/config/secrets"
chmod 700 "$REPO_ROOT/config/secrets" 2>/dev/null || true
find "$REPO_ROOT/config/secrets" -type f -exec chmod 600 {} + 2>/dev/null || true
if [ -s "$REPO_ROOT/config/secrets/anthropic-api-key.txt" ]; then
    ok "anthropic key present (config/secrets/anthropic-api-key.txt, 0600)"
else
    warn "no Anthropic API key — write it to config/secrets/anthropic-api-key.txt (chmod 600). Legacy ~/.config/anthropic-key.txt is still honored."
fi

# verify PyYAML against the DAEMON interpreter (what the units + doctor run).
if ! "$RUNTIME_PY" -c "import yaml" >/dev/null 2>&1; then
    warn "PyYAML missing for $RUNTIME_PY; attempting install"
    if "$RUNTIME_PY" -m pip install --user pyyaml >/dev/null 2>&1 \
        || "$RUNTIME_PY" -m pip install --user --break-system-packages pyyaml >/dev/null 2>&1; then
        ok "PyYAML installed for $RUNTIME_PY"
    else
        err "PyYAML unavailable for the daemon python: $RUNTIME_PY"
        info "fix: $RUNTIME_PY -m pip install --user --break-system-packages pyyaml   (then re-run)"
        exit 1
    fi
else
    ok "PyYAML importable for $RUNTIME_PY"
fi

# --------------------------------------------------------------------------
echo ""
echo "==> 3. state directories"
mkdir -p "$REPO_ROOT/state/inbox"
ok "state/ and state/inbox/ ready"
if [ ! -f "$REPO_ROOT/state/dashboard.json" ]; then
    if (cd "$REPO_ROOT" && AIASSISTANT_HOME="$REPO_ROOT" "$RUNTIME_PY" -m act.lib.dashboard >/dev/null 2>&1); then
        ok "generated state/dashboard.json from registry"
    elif [ -f "$REPO_ROOT/state/dashboard.seed.json" ]; then
        cp "$REPO_ROOT/state/dashboard.seed.json" "$REPO_ROOT/state/dashboard.json"
        ok "seeded state/dashboard.json from dashboard.seed.json"
    else
        warn "could not generate dashboard.json (run: $RUNTIME_PY -m act.lib.dashboard)"
    fi
fi

# --------------------------------------------------------------------------
echo ""
echo "==> 4. systemd user units (actd + web dashboard + radar/digest timers)"
if ! command -v systemctl >/dev/null 2>&1; then
    warn "systemctl not found — no systemd user session on this box."
    info "run the daemon + dashboard directly instead:"
    info "  AIASSISTANT_HOME=$REPO_ROOT $RUNTIME_PY -m act.actd &"
    info "  AIASSISTANT_HOME=$REPO_ROOT $RUNTIME_PY -m act.webui &"
else
    mkdir -p "$UNIT_DIR"
    # Render the templates into the user unit dir. act/lib/systemd is the single
    # source of truth for the @TOKEN@ substitution (tested in CI), so there is no
    # sed/install drift between "what CI validated" and "what runs here".
    if (cd "$REPO_ROOT" && AIASSISTANT_HOME="$REPO_ROOT" "$RUNTIME_PY" -m act.lib.systemd \
            --python "$RUNTIME_PY" --repo-root "$REPO_ROOT" \
            --claude-bin-dir "$CLAUDE_BIN_DIR" --out "$UNIT_DIR" >/dev/null); then
        ok "rendered units into $UNIT_DIR"
    else
        err "failed to render systemd units (python -m act.lib.systemd)"
        exit 1
    fi

    if ! systemctl --user daemon-reload >/dev/null 2>&1; then
        warn "systemctl --user daemon-reload failed — no user bus?"
        info "on a headless server enable it: sudo loginctl enable-linger \"$USER\", then re-run"
    fi

    # Enable + start the RESIDENT services and the timers (the oneshot radar/
    # digest .service units are timer-driven, so they are NOT enabled directly).
    ENABLE_UNITS=(
        "zelin-actd.service"
        "zelin-webui.service"
        "zelin-gmail-radar.timer"
        "zelin-slack-radar.timer"
        "zelin-obsidian-radar.timer"
        "zelin-weekly-digest.timer"
    )
    ENABLE_FAILED=0
    for unit in "${ENABLE_UNITS[@]}"; do
        if systemctl --user enable --now "$unit" >/dev/null 2>&1; then
            ok "enabled + started $unit"
        else
            warn "could not enable $unit — fix the user bus, then: systemctl --user enable --now $unit"
            ENABLE_FAILED=$((ENABLE_FAILED + 1))
        fi
    done

    # Headless boxes: keep --user units alive without an active login session.
    if command -v loginctl >/dev/null 2>&1; then
        if loginctl enable-linger "$USER" >/dev/null 2>&1; then
            ok "lingering enabled (units run without an active login)"
        else
            info "enable lingering so units survive logout: sudo loginctl enable-linger \"$USER\""
        fi
    fi

    if [ "$ENABLE_FAILED" -eq 0 ]; then
        ok "web dashboard: journalctl --user -u zelin-webui  (prints the http://127.0.0.1:PORT URL)"
    fi
fi

# --------------------------------------------------------------------------
echo ""
echo "==> 5. post-install diagnostics (python -m act.doctor)"
if [ -n "$RUNTIME_PY" ] && [ -x "$RUNTIME_PY" ]; then
    if ! (cd "$REPO_ROOT" && AIASSISTANT_HOME="$REPO_ROOT" "$RUNTIME_PY" -m act.doctor); then
        warn "doctor reported problems above — fix them, then re-check: bash install-linux.sh --check"
    fi
else
    warn "no usable python3 — run diagnostics later: bash install-linux.sh --check"
fi

# --------------------------------------------------------------------------
cat <<EOF

==============================================
 Linux install complete (v1 beta). Next steps:
==============================================
 1. Edit config.yaml (Slack IDs, watched people, source paths).
 2. Anthropic API key -> config/secrets/anthropic-api-key.txt (chmod 600).
    A systemd --user session has no Keychain, so a file-form key is required.
 3. Open the web dashboard (the Linux UI): find its URL with
      journalctl --user -u zelin-webui
    then open http://127.0.0.1:<port> in a browser on this machine. It reads
    state/dashboard.json and writes approvals to state/inbox/ (CONTRACT §3/§10).
 4. Phone / always-on channel = Slack self-DM quick capture (works today).
 5. Manage the units:
      systemctl --user status  zelin-actd.service zelin-webui.service
      systemctl --user list-timers 'zelin-*'
      journalctl --user -u zelin-actd -f
 6. Anything off later? Re-run diagnostics anytime: bash install-linux.sh --check

 DEFERRED on Linux v1 (see docs/LINUX.md): the screenpipe screen-ingest chain
 (needs a display) and the macOS SwiftUI app. The Obsidian radar still scans
 notes already in your vault via zelin-obsidian-radar.timer.
==============================================
EOF
