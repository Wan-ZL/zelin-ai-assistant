#!/bin/bash
# One-click installer for Zelin's AI Assistant (Act pipeline + menu-bar app).
#
# What it does:
#   1. dependency checks (claude/swift required; python3/PyYAML; node+npx for
#      the recording engine; obsidian/gh optional)
#   2. config.example.yaml -> config.yaml (if absent) + runtime/home pointers
#   3. create state/ and state/inbox/
#   4. build + install the Mac app (mac/build.sh --install)
#   5. install launchd agents (actd resident + radar periodic): render the
#      plist templates (replace /Users/YOURUSERNAME placeholders with the real
#      python/repo/home paths), load them, then verify they actually spawn
#   6. unify the user crontab (CONTRACT §18): screenpipe ingest chain now runs
#      the repo's ingest/ scripts + `python -m act.radar --once`, and Monday
#      09:07 runs `python -m act.digest --now`
#   7. run the post-install diagnostics (python -m act.doctor)
#
# Run from anywhere; it locates the repo root via its own path.
#
# --pkg-postinstall: non-interactive mode used by the .pkg installer's
#   postinstall (mac/package.sh). Skips dependency checks (nothing to ask a
#   user for), the Mac app build/install (the pkg already installed it) and
#   the launchd agents (they need per-user config that doesn't exist yet),
#   but still does config files, state dirs and the ingest cron chain.
#
# --check: run the post-install doctor (python -m act.doctor) and exit with
#   the number of failing checks. Installs/changes nothing.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$SCRIPT_DIR"
LA_DIR="$HOME/Library/LaunchAgents"

# --check: delegate to act/doctor.py — re-validates every runtime assumption
# (deps, key resolution, launchd agents alive, cron lines, dashboard freshness)
# symptom-first, one fix line per finding. Exit code = number of FAILs.
if [ "${1:-}" = "--check" ]; then
    DOCTOR_PY="$(command -v python3 || echo /usr/bin/python3)"
    if [ -f "$REPO_ROOT/config/runtime.json" ]; then
        # prefer the pinned daemon interpreter — it is what launchd/cron run
        RJ_PY="$(sed -n 's/.*"python"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$REPO_ROOT/config/runtime.json")"
        [ -n "$RJ_PY" ] && [ -x "$RJ_PY" ] && DOCTOR_PY="$RJ_PY"
    fi
    cd "$REPO_ROOT" || exit 1
    AIASSISTANT_HOME="$REPO_ROOT" exec "$DOCTOR_PY" -m act.doctor
fi

PKG_POSTINSTALL=0
[ "${1:-}" = "--pkg-postinstall" ] && PKG_POSTINSTALL=1

ok()   { printf "  [ ok ] %s\n" "$1"; }
warn() { printf "  [warn] %s\n" "$1"; }
info() { printf "  [info] %s\n" "$1"; }

# escape a value for use on the replacement side of sed s|…|…| (delimiter |)
_sed_escape() { printf '%s' "$1" | sed -e 's/[\\&|]/\\&/g'; }

# Render a launchd plist template into place. The repo plists carry
# /Users/YOURUSERNAME placeholders (plists don't expand ~) — substitute the
# detected python interpreter, this repo root and $HOME before installing.
# Kept as a function so both the interactive path and --pkg-postinstall (which
# currently skips launchd) render identically if they ever load agents.
render_launchd_plist() {
    src="$1"; dest="$2"
    py="${RUNTIME_PY:-$(command -v python3 || echo /usr/bin/python3)}"
    pydir="$(dirname "$py")"
    sed -e "s|/Users/YOURUSERNAME/miniconda3/bin/python3|$(_sed_escape "$py")|g" \
        -e "s|/Users/YOURUSERNAME/Projects/zelin-ai-assistant|$(_sed_escape "$REPO_ROOT")|g" \
        -e "s|/Users/YOURUSERNAME/miniconda3/bin|$(_sed_escape "$pydir")|g" \
        -e "s|/Users/YOURUSERNAME|$(_sed_escape "$HOME")|g" \
        "$src" > "$dest"
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
        info "fix: read $REPO_ROOT/state/${label##*.}.launchd.log — usual causes:"
        info "  - PyYAML missing for the daemon python: ${RUNTIME_PY:-python3} -m pip install --user pyyaml"
        info "  - Anthropic API key file missing: paste it in the app's Settings window"
        info "then: launchctl unload $LA_DIR/$label.plist && launchctl load $LA_DIR/$label.plist"
        return 1
    fi
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

# claude (required)
if command -v claude >/dev/null 2>&1; then
    ok "claude found: $(command -v claude)"
else
    echo "  [ERR ] claude CLI not found (REQUIRED). Install Claude Code first, then re-run." >&2
    exit 1
fi

# swift toolchain (required to build the Mac app) — presence AND minimum
# version. MIN_SWIFT lives in mac/build.sh (single source); on failure it
# prints the exact fix (update Xcode, xcode-select) so we just exit.
if bash "$REPO_ROOT/mac/build.sh" --check-toolchain; then
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
else
    cp "$REPO_ROOT/config.example.yaml" "$REPO_ROOT/config.yaml"
    ok "created config.yaml from config.example.yaml — review it before first run"
fi

# redaction terms live outside git (they hold the user's real sensitive terms)
if [ ! -f "$REPO_ROOT/config/redaction_terms.txt" ]; then
    cp "$REPO_ROOT/config/redaction_terms.example.txt" "$REPO_ROOT/config/redaction_terms.txt"
    ok "created config/redaction_terms.txt from template (gitignored)"
fi

# runtime python pointer (CONTRACT §19) — the Mac app shells out to this python
# for its dependency checks (Swift can't guess the conda env). Detection order:
#   $AIASSISTANT_PYTHON env -> miniconda python3 (if it can import yaml) -> which python3
mkdir -p "$REPO_ROOT/config"
RUNTIME_PY=""
if [ -n "${AIASSISTANT_PYTHON:-}" ] && [ -x "${AIASSISTANT_PYTHON:-}" ]; then
    RUNTIME_PY="$AIASSISTANT_PYTHON"
elif [ -x "$HOME/miniconda3/bin/python3" ] && "$HOME/miniconda3/bin/python3" -c "import yaml" >/dev/null 2>&1; then
    RUNTIME_PY="$HOME/miniconda3/bin/python3"
else
    RUNTIME_PY="$PY"
fi
if [ -n "$RUNTIME_PY" ]; then
    printf '{"python": "%s"}\n' "$RUNTIME_PY" > "$REPO_ROOT/config/runtime.json"
    ok "config/runtime.json -> $RUNTIME_PY"
else
    warn "python3 not found — skipped config/runtime.json (re-run install.sh once python3 exists)"
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
if [ "$PKG_POSTINSTALL" -eq 1 ]; then
    echo "==> 4. build + install Mac app — skipped (the .pkg already installed it)"
else
    echo "==> 4. build + install Mac app"
    if bash "$REPO_ROOT/mac/build.sh" --install; then
        ok "Mac app built + installed"
    else
        warn "Mac app build failed — see output above"
    fi
fi

# --------------------------------------------------------------------------
echo ""
if [ "$PKG_POSTINSTALL" -eq 1 ]; then
    echo "==> 5. launchd agents — skipped (edit config.yaml first, then re-run install.sh)"
else
    echo "==> 5. launchd agents"
    mkdir -p "$LA_DIR"
    info "rendering plist templates: python=${RUNTIME_PY:-python3} home=$REPO_ROOT"
    # feature gate: the imessage radar only loads when config selects the
    # channel. Read through act.lib.config (so settings_overrides.json is
    # honored too); an unreadable config counts as "none".
    PHONE_CHANNEL="$(cd "$REPO_ROOT" && AIASSISTANT_HOME="$REPO_ROOT" "${RUNTIME_PY:-python3}" -c 'from act.lib import config; print(getattr(config.load_config(), "phone_channel", "none"))' 2>/dev/null || echo none)"
    LOADED_LABELS=""
    for plist in "$REPO_ROOT"/act/launchd/*.plist; do
        [ -e "$plist" ] || continue
        base="$(basename "$plist")"
        label="${base%.plist}"
        dest="$LA_DIR/$base"
        if [ "$label" = "com.zelin.aiassistant.imessageradar" ] && [ "$PHONE_CHANNEL" != "imessage" ]; then
            # feature off: also unload+remove any previously-installed copy
            launchctl unload "$dest" >/dev/null 2>&1 || true
            rm -f "$dest"
            info "skipped $label (phone_channel=$PHONE_CHANNEL — set phone_channel: imessage in config.yaml to enable, see docs/IMESSAGE_SETUP.md)"
            continue
        fi
        # unload any previous version first
        launchctl unload "$dest" >/dev/null 2>&1 || true
        render_launchd_plist "$plist" "$dest"
        if launchctl load "$dest" >/dev/null 2>&1; then
            ok "loaded $label"
            LOADED_LABELS="$LOADED_LABELS $label"
        else
            warn "failed to load $label (may need TCC/Full Disk Access approval — see below)"
        fi
    done
    # give launchd a moment to spawn the jobs, then verify they really run
    if [ -n "$LOADED_LABELS" ]; then
        sleep 2
        for label in $LOADED_LABELS; do
            verify_launchd_agent "$label"
        done
    fi
fi

# --------------------------------------------------------------------------
echo ""
echo "==> 6. crontab — unified ingest chain + Monday digest (CONTRACT §18)"
chmod +x "$REPO_ROOT"/ingest/*.sh "$REPO_ROOT"/ingest/*.command 2>/dev/null || true

# cron runs outside the login shell; prefer the miniconda python (has PyYAML),
# fall back to whatever python3 the installer found.
CRON_PY="$HOME/miniconda3/bin/python3"
[ -x "$CRON_PY" ] || CRON_PY="${PY:-python3}"

# process-screenpipe.sh exits 3 when another run holds the lock — that's a
# skip, not a failure, so it must not break the chain (radar still runs).
INGEST_CHAIN="*/30 * * * * cd $REPO_ROOT && ./ingest/screenpipe-export.sh && ./ingest/screenpipe-cleanup.sh && { ./ingest/process-screenpipe.sh || [ \$? -eq 3 ]; } && AIASSISTANT_HOME=$REPO_ROOT $CRON_PY -m act.radar --once >> $REPO_ROOT/state/radar.cron.log 2>&1"
DIGEST_LINE="7 9 * * 1 cd $REPO_ROOT && AIASSISTANT_HOME=$REPO_ROOT $CRON_PY -m act.digest --now >> $REPO_ROOT/state/digest.log 2>&1"

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

# idempotent: append the Monday digest line if absent
if printf '%s\n' "$NEW_CRON" | grep -q 'act\.digest'; then
    ok "Monday digest cron already installed"
else
    NEW_CRON="$(printf '%s\n%s\n' "$NEW_CRON" "$DIGEST_LINE")"
    ok "Monday digest cron installed (Mon 09:07)"
fi

if [ "$NEW_CRON" != "$CURRENT_CRON" ]; then
    if printf '%s\n' "$NEW_CRON" | grep -v '^[[:space:]]*$' | crontab -; then
        ok "crontab rewritten (other lines preserved)"
    else
        warn "crontab update failed — add these lines manually with 'crontab -e':"
        info "$INGEST_CHAIN"
        info "$DIGEST_LINE"
    fi
fi

# --------------------------------------------------------------------------
echo ""
if [ "$PKG_POSTINSTALL" -eq 1 ]; then
    echo "==> 7. diagnostics — skipped (agents not loaded yet; after configuring, run: bash install.sh --check)"
elif [ -n "${RUNTIME_PY:-}" ] && [ -x "${RUNTIME_PY:-}" ]; then
    echo "==> 7. post-install diagnostics (python -m act.doctor)"
    if ! (cd "$REPO_ROOT" && AIASSISTANT_HOME="$REPO_ROOT" "$RUNTIME_PY" -m act.doctor); then
        warn "doctor reported problems above — fix them, then re-check: bash install.sh --check"
    fi
else
    echo "==> 7. diagnostics — skipped (no usable python3); run later: bash install.sh --check"
fi

# --------------------------------------------------------------------------
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
      - RADAR reads "~/Documents/Obsidian Vault". launchd is TCC-BLOCKED from
        ~/Documents. Recommended: run radar via crontab (crontab HAS Full Disk
        Access if Terminal/cron is granted it) instead of the radar launchd agent.
        See the comment at the top of act/launchd/com.zelin.aiassistant.radar.plist
        for the exact crontab line.
 4. The menu-bar app is installed; launch it from /Applications (or ~/Applications).
    It reads state/dashboard.json every 5s and writes approvals to state/inbox/.
 5. Anything off later? Re-run diagnostics anytime: bash install.sh --check
==============================================
EOF
