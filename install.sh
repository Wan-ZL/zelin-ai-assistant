#!/bin/bash
# One-click installer for Zelin's AI Assistant (Act pipeline + menu-bar app).
#
# What it does:
#   1. dependency checks (claude/swift required; python3/PyYAML; screenpipe/obsidian/gh optional)
#   2. config.example.yaml -> config.yaml (if absent)
#   3. create state/ and state/inbox/
#   4. build + install the Mac app (mac/build.sh --install)
#   5. install launchd agents (actd resident + radar periodic) and load them
#   6. unify the user crontab (CONTRACT §18): screenpipe ingest chain now runs
#      the repo's ingest/ scripts + `python -m act.radar --once`, and Monday
#      09:07 runs `python -m act.digest --now`
#
# Run from anywhere; it locates the repo root via its own path.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$SCRIPT_DIR"
LA_DIR="$HOME/Library/LaunchAgents"

ok()   { printf "  [ ok ] %s\n" "$1"; }
warn() { printf "  [warn] %s\n" "$1"; }
info() { printf "  [info] %s\n" "$1"; }

echo "=============================================="
echo " Zelin's AI Assistant — installer"
echo " repo: $REPO_ROOT"
echo "=============================================="

# --------------------------------------------------------------------------
echo ""
echo "==> 1. Dependency checks"

# claude (required)
if command -v claude >/dev/null 2>&1; then
    ok "claude found: $(command -v claude)"
else
    echo "  [ERR ] claude CLI not found (REQUIRED). Install Claude Code first, then re-run." >&2
    exit 1
fi

# swift / swiftc (required)
if command -v swiftc >/dev/null 2>&1; then
    ok "swiftc found: $(command -v swiftc)"
else
    warn "swiftc not found (REQUIRED to build the Mac app)."
    info "Install with: xcode-select --install   (then re-run this script)"
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

# PyYAML (else pip install)
if "$PY" -c "import yaml" >/dev/null 2>&1; then
    ok "PyYAML available"
else
    warn "PyYAML missing; attempting: pip install pyyaml"
    if "$PY" -m pip install --user pyyaml >/dev/null 2>&1; then
        ok "PyYAML installed"
    else
        warn "PyYAML install failed; install manually: $PY -m pip install pyyaml"
    fi
fi

# optional
command -v screenpipe >/dev/null 2>&1 && ok "screenpipe found (optional)" || warn "screenpipe not found (optional — ingest source)"
if [ -d "/Applications/Obsidian.app" ] || command -v obsidian >/dev/null 2>&1; then
    ok "obsidian found (optional)"
else
    warn "obsidian not found (optional — radar reads the vault)"
fi
command -v gh >/dev/null 2>&1 && ok "gh found (optional)" || warn "gh not found (optional — draft-PR delivery)"

# credential reminder (contract §19: actd reads key from file, not from this script)
if [ -s "$REPO_ROOT/config/secrets/anthropic-api-key.txt" ]; then
    ok "anthropic key present (config/secrets/anthropic-api-key.txt)"
elif [ -f "$HOME/.config/anthropic-key.txt" ]; then
    ok "anthropic key present (legacy ~/.config/anthropic-key.txt — 仍兜底可用)"
else
    warn "缺 Anthropic API key —— 推荐在 App 设置窗口粘贴保存（写入 config/secrets/anthropic-api-key.txt）；旧路径 ~/.config/anthropic-key.txt 仍兜底。headless claude 在 launchd 下读不了 Keychain OAuth。"
fi

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
printf '{"python": "%s"}\n' "$RUNTIME_PY" > "$REPO_ROOT/config/runtime.json"
ok "config/runtime.json -> $RUNTIME_PY"

# --------------------------------------------------------------------------
echo ""
echo "==> 3. state directories"
mkdir -p "$REPO_ROOT/state/inbox"
ok "state/ and state/inbox/ ready"
# generate the initial dashboard from the (git-tracked) registry so the app renders
# before the daemon's first pass. Falls back to the seed if generation fails.
if [ ! -f "$REPO_ROOT/state/dashboard.json" ]; then
    if (cd "$REPO_ROOT" && AIASSISTANT_HOME="$REPO_ROOT" "$PY" -m act.lib.dashboard >/dev/null 2>&1); then
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
echo "==> 4. build + install Mac app"
if bash "$REPO_ROOT/mac/build.sh" --install; then
    ok "Mac app built + installed"
else
    warn "Mac app build failed — see output above"
fi

# --------------------------------------------------------------------------
echo ""
echo "==> 5. launchd agents"
mkdir -p "$LA_DIR"
for plist in "$REPO_ROOT"/act/launchd/*.plist; do
    [ -e "$plist" ] || continue
    base="$(basename "$plist")"
    label="${base%.plist}"
    dest="$LA_DIR/$base"
    # unload any previous version first
    launchctl unload "$dest" >/dev/null 2>&1 || true
    cp "$plist" "$dest"
    if launchctl load "$dest" >/dev/null 2>&1; then
        ok "loaded $label"
    else
        warn "failed to load $label (may need TCC/Full Disk Access approval — see below)"
    fi
done

# --------------------------------------------------------------------------
echo ""
echo "==> 6. crontab — unified ingest chain + Monday digest (CONTRACT §18)"
chmod +x "$REPO_ROOT"/ingest/*.sh "$REPO_ROOT"/ingest/*.command 2>/dev/null || true

# cron runs outside the login shell; prefer the miniconda python (has PyYAML),
# fall back to whatever python3 the installer found.
CRON_PY="$HOME/miniconda3/bin/python3"
[ -x "$CRON_PY" ] || CRON_PY="$PY"

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
      - actd (launchd) only touches ~/Projects/zelin-ai-assistant/state + calls claude,
        so it needs NO Full Disk Access.
      - RADAR reads "~/Documents/Obsidian Vault". launchd is TCC-BLOCKED from
        ~/Documents. Recommended: run radar via crontab (crontab HAS Full Disk
        Access if Terminal/cron is granted it) instead of the radar launchd agent.
        See the comment at the top of act/launchd/com.zelin.aiassistant.radar.plist
        for the exact crontab line.
 4. The menu-bar app is installed; launch it from /Applications (or ~/Applications).
    It reads state/dashboard.json every 5s and writes approvals to state/inbox/.
==============================================
EOF
