#!/bin/bash
# Build + assemble the "Zelin's AI Assistant" menu-bar app (.app bundle).
#
# Usage:
#   ./build.sh                    # compile + assemble the bundle under mac/build/
#   ./build.sh --install          # also copy the bundle to /Applications (fallback ~/Applications)
#   ./build.sh --check-toolchain  # only verify swiftc presence + version, then exit
#
# Naming (v0.4 §12 — MUST stay in sync with install.sh / glue / launchd):
#   bundle:     Zelin's AI Assistant.app
#   executable: ZelinAIEngineer
#   bundle id:  com.zelin.ai-engineer
#   (launchd label + AIASSISTANT_HOME env var name intentionally unchanged.)
set -euo pipefail

# --- locate self (worktree-safe, handles spaces) ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- toolchain gate ---
# The sources use Swift 6 concurrency (main-actor isolation, same floor as
# .github/workflows/ci.yml); an older default Xcode fails MID-COMPILE with
# confusing actor errors, so check the version up front and print the fix.
# install.sh step 1 calls `mac/build.sh --check-toolchain` (single source).
MIN_SWIFT="6.0"

check_toolchain() {
    if ! command -v swiftc >/dev/null 2>&1; then
        echo "ERROR: swiftc not found. Install Xcode Command Line Tools: xcode-select --install" >&2
        return 1
    fi
    local ver
    ver="$(swiftc --version 2>/dev/null | sed -n 's/.*Swift version \([0-9][0-9.]*\).*/\1/p' | head -n1)"
    if [ -z "$ver" ]; then
        # unparseable banner — don't block the build on a cosmetic format change
        echo "WARN: could not parse Swift version from: $(swiftc --version 2>/dev/null | head -n1)" >&2
        return 0
    fi
    if [ "$(printf '%s\n%s\n' "$MIN_SWIFT" "$ver" | sort -V | head -n1)" != "$MIN_SWIFT" ]; then
        echo "ERROR: Swift $ver is too old — this app needs Swift >= $MIN_SWIFT (main-actor isolation rules)." >&2
        echo "  fix: update Xcode via the App Store (or install newer Command Line Tools), then:" >&2
        echo "       sudo xcode-select -s /Applications/Xcode.app/Contents/Developer" >&2
        echo "       verify with: swiftc --version" >&2
        return 1
    fi
    return 0
}

if [ "${1:-}" = "--check-toolchain" ]; then
    if check_toolchain; then exit 0; else exit 1; fi
fi

APP_NAME="Zelin's AI Assistant"
EXEC_NAME="ZelinAIEngineer"
# all module files in Sources/ compile as ONE module; only main.swift may hold
# top-level statements (the bootstrap), per swiftc rules.
SRC_DIR="$SCRIPT_DIR/Sources"
PLIST="$SCRIPT_DIR/Info.plist"
BUILD_DIR="$SCRIPT_DIR/build"
BIN="$BUILD_DIR/$EXEC_NAME"
APP_DIR="$BUILD_DIR/$APP_NAME.app"

INSTALL=0
[ "${1:-}" = "--install" ] && INSTALL=1

# --- sanity checks ---
check_toolchain || exit 1
if [ ! -f "$SRC_DIR/main.swift" ]; then
    echo "ERROR: Swift source not found at: $SRC_DIR/main.swift" >&2
    exit 1
fi
if [ ! -f "$PLIST" ]; then
    echo "ERROR: Info.plist not found at: $PLIST" >&2
    exit 1
fi

# --- compile ---
echo "==> Compiling $SRC_DIR/*.swift"
mkdir -p "$BUILD_DIR"
swiftc -O "$SRC_DIR"/*.swift -o "$BIN" \
    -framework AppKit -framework SwiftUI -framework Foundation
echo "    built binary: $BIN"

# --- compile framegrab helper (§13: video → evenly spaced JPEG frames) ---
# Failure here is non-fatal: Slack video capture falls back to ffmpeg.
FRAMEGRAB_SRC="$SCRIPT_DIR/framegrab.swift"
if [ -f "$FRAMEGRAB_SRC" ]; then
    echo "==> Compiling framegrab"
    if swiftc -O "$FRAMEGRAB_SRC" -o "$BUILD_DIR/framegrab" \
        -framework AVFoundation -framework CoreImage -framework Foundation; then
        echo "    built binary: $BUILD_DIR/framegrab"
    else
        echo "WARN: framegrab compile failed — video frame extraction will rely on ffmpeg."
    fi
fi

# --- assemble .app bundle ---
echo "==> Assembling bundle: $APP_DIR"
rm -rf "$APP_DIR"
mkdir -p "$APP_DIR/Contents/MacOS"
mkdir -p "$APP_DIR/Contents/Resources"
cp "$BIN" "$APP_DIR/Contents/MacOS/$EXEC_NAME"
cp "$PLIST" "$APP_DIR/Contents/Info.plist"
# version single source of truth: act/__init__.py (same extraction as
# mac/package.sh). Stamp the STAGED plist only — the source Info.plist keeps
# its values as a fallback for when the version cannot be read.
VERSION="$(sed -n 's/^__version__ = "\([^"]*\)".*/\1/p' "$SCRIPT_DIR/../act/__init__.py" 2>/dev/null || true)"
if [ -n "$VERSION" ]; then
    plutil -replace CFBundleShortVersionString -string "$VERSION" "$APP_DIR/Contents/Info.plist"
    plutil -replace CFBundleVersion -string "$VERSION" "$APP_DIR/Contents/Info.plist"
    echo "    stamped version $VERSION (from act/__init__.py)"
else
    echo "WARN: could not read __version__ from act/__init__.py — bundle keeps the Info.plist fallback version."
fi
# app icon (optional — present after icon generation)
if [ -f "$SCRIPT_DIR/AppIcon.icns" ]; then
    cp "$SCRIPT_DIR/AppIcon.icns" "$APP_DIR/Contents/Resources/AppIcon.icns"
    echo "    bundled AppIcon.icns"
fi

# --- codesign: prefer the stable self-signed identity so TCC grants (screen
# recording etc.) SURVIVE reinstalls; ad-hoc ("-") invalidates them every build.
SIGN_ID="Zelin AI Engineer Dev"
# NOTE: no `-v` — this identity is a self-signed cert that is NOT trusted
# (CSSMERR_TP_NOT_TRUSTED), so `-v` (valid/trusted-only) would hide it. Trust is
# irrelevant to codesign + TCC persistence; the untrusted cert still signs fine
# and yields a stable cert-based Designated Requirement.
if security find-identity -p codesigning 2>/dev/null | grep -q "$SIGN_ID"; then
    echo "==> Codesigning with '$SIGN_ID' (stable identity, TCC-safe)"
else
    SIGN_ID="-"
    echo "==> Ad-hoc codesigning (identity missing — TCC grants will reset on reinstall)"
fi
codesign --force --deep --sign "$SIGN_ID" "$APP_DIR" || \
    echo "WARN: codesign failed (app may still run after Gatekeeper prompt)."

# --- optional install ---
FINAL="$APP_DIR"
if [ "$INSTALL" -eq 1 ]; then
    DEST="/Applications"
    if [ -w "$DEST" ] || [ ! -e "$DEST/$APP_NAME.app" ] && touch "$DEST/.aiassistant_write_test" 2>/dev/null; then
        rm -f "$DEST/.aiassistant_write_test" 2>/dev/null || true
    else
        echo "WARN: no write permission to /Applications; falling back to ~/Applications"
        DEST="$HOME/Applications"
        mkdir -p "$DEST"
    fi
    # Quit a running instance BEFORE swapping the bundle: overwriting a live
    # app leaves the OLD version running (menu bar still shows it) until a
    # manual quit — nobody should have to know that. Graceful quit via Apple
    # Event first, pkill only as the fallback; relaunch after the copy so the
    # upgrade is invisible.
    WAS_RUNNING=0
    if pgrep -x "$EXEC_NAME" >/dev/null 2>&1; then
        WAS_RUNNING=1
        echo "==> Quitting the running $APP_NAME instance"
        osascript -e "tell application \"$APP_NAME\" to quit" >/dev/null 2>&1 || true
        for _ in 1 2 3 4 5 6 7 8 9 10; do
            pgrep -x "$EXEC_NAME" >/dev/null 2>&1 || break
            sleep 0.5
        done
        pkill -x "$EXEC_NAME" 2>/dev/null || true
    fi
    echo "==> Installing to $DEST"
    rm -rf "$DEST/$APP_NAME.app"
    if cp -R "$APP_DIR" "$DEST/"; then
        FINAL="$DEST/$APP_NAME.app"
        # re-sign in place (cp can perturb signature)
        codesign --force --deep --sign "$SIGN_ID" "$FINAL" 2>/dev/null || true
        if [ "$WAS_RUNNING" -eq 1 ]; then
            echo "==> Relaunching $APP_NAME ($DEST)"
            open "$FINAL" || echo "WARN: relaunch failed — start it manually: open \"$FINAL\""
        fi
    else
        echo "WARN: copy to $DEST failed; using built bundle in place."
    fi
fi

echo ""
echo "DONE. App bundle: $FINAL"
echo "  Launch with: open \"$FINAL\"   (menu-bar only, no Dock icon)"
