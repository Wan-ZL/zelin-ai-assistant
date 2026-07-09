#!/bin/bash
# Build an unsigned product .pkg that installs the WHOLE suite (HANDOFF §6 #1):
#   component 1: the menu-bar app            -> /Applications
#   component 2: the pipeline (repo export)  -> /Library/Application Support/
#                ZelinAIAssistant/pipeline   (root-owned, versioned master copy)
# plus a postinstall that rsyncs the master copy into the console user's
# ~/Projects/zelin-ai-assistant and runs `install.sh --pkg-postinstall`
# (config copy-if-absent, state dirs, ingest cron chain — CONTRACT §18).
#
# Usage:  bash mac/package.sh
# Needs:  the app already built (runs mac/build.sh itself if missing); no root.
# Output: mac/build/ZelinAIAssistant-<version>.pkg
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BUILD_DIR="$SCRIPT_DIR/build"
APP_NAME="Zelin's AI Assistant"
APP_PATH="$BUILD_DIR/$APP_NAME.app"
APP_PKG_ID="com.zelin.aiassistant.app"
PIPELINE_PKG_ID="com.zelin.aiassistant.pipeline"
PIPELINE_DEST="/Library/Application Support/ZelinAIAssistant/pipeline"

# version single source of truth: act/__init__.py
VERSION="$(sed -n 's/^__version__ = "\([^"]*\)".*/\1/p' "$REPO_ROOT/act/__init__.py")"
if [ -z "$VERSION" ]; then
    echo "ERROR: could not read __version__ from act/__init__.py" >&2
    exit 1
fi
echo "==> Packaging version $VERSION"

if [ ! -d "$APP_PATH" ]; then
    echo "==> App bundle missing — running mac/build.sh first"
    bash "$SCRIPT_DIR/build.sh"
fi

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

# pkgbuild auto-detects bundles (anything with an Info.plist — the .app, and
# also mac/ in the pipeline export) and marks them RELOCATABLE by default:
# Installer would then "upgrade" a stray copy found via Spotlight (e.g. a dev
# build dir) instead of the intended path. Analyze first, then pin every
# detected bundle: no relocation, no version gating.
pin_bundles() {
    local plist="$1" i=0
    while plutil -replace "$i.BundleIsRelocatable" -bool NO "$plist" >/dev/null 2>&1; do
        plutil -replace "$i.BundleIsVersionChecked" -bool NO "$plist" >/dev/null 2>&1 || true
        i=$((i + 1))
    done
}

# --- component 1: the .app -> /Applications -------------------------------
echo "==> pkgbuild: app component"
APP_ROOT="$STAGE/app-root"
mkdir -p "$APP_ROOT"
ditto "$APP_PATH" "$APP_ROOT/$APP_NAME.app"
# best-effort xattr strip (quarantine etc.); com.apple.provenance is
# SIP-held and survives as ._ payload entries — harmless, Installer just
# restores it as an xattr.
xattr -rc "$APP_ROOT" 2>/dev/null || true
pkgbuild --analyze --root "$APP_ROOT" "$STAGE/app-components.plist" >/dev/null
pin_bundles "$STAGE/app-components.plist"
pkgbuild --root "$APP_ROOT" \
    --component-plist "$STAGE/app-components.plist" \
    --install-location /Applications \
    --identifier "$APP_PKG_ID" \
    --version "$VERSION" \
    "$STAGE/ZelinAIAssistant-app.pkg"

# --- component 2: pipeline master copy ------------------------------------
# git archive = clean export of HEAD (no state/, secrets, build junk).
PIPELINE_ROOT="$STAGE/pipeline-root"
mkdir -p "$PIPELINE_ROOT"
git -C "$REPO_ROOT" archive HEAD | tar -x -C "$PIPELINE_ROOT"
rm -rf "$PIPELINE_ROOT/mac/build"   # belt & suspenders (gitignored anyway)
xattr -rc "$PIPELINE_ROOT" 2>/dev/null || true
if [ -n "$(git -C "$REPO_ROOT" status --porcelain)" ]; then
    echo "WARN: working tree is dirty — the pkg payload is HEAD, not your edits." >&2
fi

SCRIPTS_DIR="$STAGE/scripts"
mkdir -p "$SCRIPTS_DIR"
# Quoted heredoc: everything below is written verbatim and expands at INSTALL
# time (as root), not at package time.
cat > "$SCRIPTS_DIR/postinstall" <<'POSTINSTALL'
#!/bin/bash
# pkg postinstall — runs as root after both payloads are on disk.
# Seeds the console user's working copy from the root-owned master and runs
# the repo's own idempotent setup. Output lands in /var/log/install.log.
set -u

MASTER="/Library/Application Support/ZelinAIAssistant/pipeline"
CONSOLE_USER="$(stat -f%Su /dev/console 2>/dev/null || true)"

case "$CONSOLE_USER" in
    ""|root|_windowserver|loginwindow)
        echo "postinstall: no console user — skipped per-user setup." >&2
        echo "postinstall: run later as your user: bash \"$MASTER/install.sh\"" >&2
        exit 0
        ;;
esac

USER_HOME="$(dscl . -read "/Users/$CONSOLE_USER" NFSHomeDirectory 2>/dev/null \
    | sed 's/^NFSHomeDirectory: //')"
[ -d "$USER_HOME" ] || USER_HOME="/Users/$CONSOLE_USER"
DEST="$USER_HOME/Projects/zelin-ai-assistant"

echo "postinstall: syncing pipeline -> $DEST (user: $CONSOLE_USER)"
sudo -u "$CONSOLE_USER" -H mkdir -p "$DEST"
# No --delete, and never clobber the user's own config.yaml / secrets / state.
if ! sudo -u "$CONSOLE_USER" -H rsync -a \
    --exclude 'config.yaml' \
    --exclude 'config/secrets/' \
    --exclude 'state/' \
    "$MASTER/" "$DEST/"; then
    echo "postinstall: rsync into $DEST failed" >&2
    exit 1
fi

# config copy-if-absent, state dirs, ingest cron chain (CONTRACT §18) — the
# logic lives in install.sh so pkg and from-source installs can't drift.
if ! sudo -u "$CONSOLE_USER" -H bash "$DEST/install.sh" --pkg-postinstall; then
    echo "postinstall: install.sh --pkg-postinstall reported errors (non-fatal)" >&2
fi

exit 0
POSTINSTALL
chmod +x "$SCRIPTS_DIR/postinstall"

echo "==> pkgbuild: pipeline component"
pkgbuild --analyze --root "$PIPELINE_ROOT" "$STAGE/pipeline-components.plist" >/dev/null
pin_bundles "$STAGE/pipeline-components.plist"
pkgbuild --root "$PIPELINE_ROOT" \
    --component-plist "$STAGE/pipeline-components.plist" \
    --install-location "$PIPELINE_DEST" \
    --identifier "$PIPELINE_PKG_ID" \
    --version "$VERSION" \
    --scripts "$SCRIPTS_DIR" \
    "$STAGE/ZelinAIAssistant-pipeline.pkg"

# --- product archive -------------------------------------------------------
DIST="$STAGE/distribution.xml"
cat > "$DIST" <<EOF
<?xml version="1.0" encoding="utf-8"?>
<installer-gui-script minSpecVersion="2">
    <title>Zelin's AI Assistant $VERSION</title>
    <options customize="never"/>
    <domains enable_localSystem="true"/>
    <choices-outline>
        <line choice="default"/>
    </choices-outline>
    <choice id="default" title="Zelin's AI Assistant">
        <pkg-ref id="$APP_PKG_ID"/>
        <pkg-ref id="$PIPELINE_PKG_ID"/>
    </choice>
    <pkg-ref id="$APP_PKG_ID">ZelinAIAssistant-app.pkg</pkg-ref>
    <pkg-ref id="$PIPELINE_PKG_ID">ZelinAIAssistant-pipeline.pkg</pkg-ref>
</installer-gui-script>
EOF

OUT="$BUILD_DIR/ZelinAIAssistant-$VERSION.pkg"
mkdir -p "$BUILD_DIR"
echo "==> productbuild"
productbuild --distribution "$DIST" --package-path "$STAGE" "$OUT"

echo ""
echo "DONE. Product pkg (unsigned): $OUT"
echo "  Inspect with: pkgutil --expand \"$OUT\" /tmp/pkg-inspect"
