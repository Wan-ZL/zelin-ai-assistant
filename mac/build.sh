#!/bin/bash
# Build + assemble the LEGACY "Zelin's AI Assistant (old)" menu-bar app (.app
# bundle) — frozen since D3 (docs/design/vnext2-plan.md); the product is the
# board shell (shell/build.sh).
#
# Usage:
#   ./build.sh                    # compile + assemble the bundle under mac/build/
#   ./build.sh --install          # also copy the bundle to /Applications (fallback ~/Applications)
#   ./build.sh --check-toolchain  # only verify swiftc presence + version, then exit
#
# Naming (v0.4 §12; §54 name swap 2026-09-02 — MUST stay in sync with
# install.sh / uninstall.sh / mac/package.sh / release.yml / vault-sync.sh):
#   bundle:     Zelin's AI Assistant (old).app   (the product name went to the shell;
#                                                install.sh moves an installed legacy
#                                                bundle to this name — this script
#                                                builds and installs straight to it)
#   executable: ZelinAIEngineer                  (unchanged)
#   bundle id:  com.zelin.ai-engineer            (unchanged — TCC grants key on it)
#   display:    Zelin's AI Assistant (old)       (mac/Info.plist, inside the seal)
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

APP_NAME="Zelin's AI Assistant (old)"
EXEC_NAME="ZelinAIEngineer"
BUNDLE_ID="com.zelin.ai-engineer"   # == mac/Info.plist CFBundleIdentifier (never changes, §12)
# all module files in Sources/ compile as ONE module; only main.swift may hold
# top-level statements (the bootstrap), per swiftc rules.
SRC_DIR="$SCRIPT_DIR/Sources"
# shared/ — Foundation-only contract types compiled into BOTH this Mac app and
# the iOS app (Contract/I18n/Lanes/InboxAction/BoardModel). They join this same
# single module, so there is no duplicate symbol. The iOS Xcode target compiles
# the very same files (ios/project.yml). Lint gate below keeps shared/ portable.
SHARED_DIR="$SCRIPT_DIR/../shared/Sources"
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

# --- Sparkle (optional) — auto-update framework, vendored by mac/scripts/fetch-sparkle.sh.
# Absent (e.g. forks, offline dev) => build compiles WITHOUT it; the Swift code is
# guarded by #if canImport(Sparkle), so no auto-update, no build failure.
FRAMEWORKS_DIR="$SCRIPT_DIR/Frameworks"
SPARKLE_FW="$FRAMEWORKS_DIR/Sparkle.framework"
SPARKLE_FLAGS=()
if [ -d "$SPARKLE_FW" ]; then
    echo "==> Sparkle present — linking auto-update support"
    SPARKLE_FLAGS=(-F "$FRAMEWORKS_DIR" -framework Sparkle
                   -Xlinker -rpath -Xlinker "@executable_path/../Frameworks")
else
    echo "==> Sparkle absent — building without auto-update (run mac/scripts/fetch-sparkle.sh to enable)"
fi

# --- shared/ portability lint gate ---
# shared/Sources/*.swift are compiled into BOTH the Mac app and the iOS app, so
# they must import ONLY Foundation — any AppKit/UIKit/SwiftUI/Combine import
# would break the iOS build (UIKit) or the portability contract. Fail loud here.
if [ -d "$SHARED_DIR" ]; then
    if grep -REn '^\s*import\s+(AppKit|UIKit|SwiftUI|Combine|Cocoa)\b' "$SHARED_DIR" 2>/dev/null; then
        echo "ERROR: shared/Sources must import only Foundation (found a UI/platform import above)." >&2
        echo "       shared/ is compiled into both the Mac and iOS targets — keep it portable." >&2
        exit 1
    fi
fi

# --- compile-failure policy: ANY swiftc failure = red build ---
# 曾经 helper 编译失败只 WARN 着继续、照样打 DONE——发版差点把缺 helper 的旧
# 产物当新构建发出去。现在任何 swiftc 步骤非零：立刻 BUILD FAILED + 非零退出，
# 绝不打 DONE。mac/build/ 里残留的上一次产物不是本次构建的结果。（运行时对缺失
# helper 二进制的 fallback 不变——旧安装仍照常降级，只是构建期错误不再被吞。）
build_failed() {   # $1 = which compile step broke
    echo "" >&2
    echo "BUILD FAILED: $1" >&2
    echo "  anything under $BUILD_DIR is from an EARLIER build — do not install or ship it." >&2
    exit 1
}

# --- version (before the compile: no answer = no swiftc time spent; the previous
# build's bundle stays as it was) ---
# version truth = the git tag (CONTRACT §56.1): scripts/build_version.sh picks an
# interpreter that can actually read this checkout (§55 第三幕: under launchd a
# Homebrew python3 is TCC-denied on an external volume), runs the stamper (exact
# tag, tag+N when ahead, else the baked fallback; also writes the git-ignored
# act/_version.py so the daemons shipped next to this app report the same
# number), falls back to act.__version__, and exits non-zero when nothing
# answers — then this build FAILS rather than shipping the Info.plist placeholder.
echo "==> Deriving the version (scripts/build_version.sh)"
VERSION="$(bash "$SCRIPT_DIR/../scripts/build_version.sh")" \
    || build_failed "could not derive the version to stamp (scripts/build_version.sh — see its messages above); refusing to ship the Info.plist placeholder"
echo "    version: $VERSION"

# --- compile ---
echo "==> Compiling $SRC_DIR/*.swift + $SHARED_DIR/*.swift"
mkdir -p "$BUILD_DIR"
# canImport(Sparkle) is false when the -F/-framework flags aren't passed, so the
# Sparkle code compiles out cleanly with no extra -D flag.
# AVFoundation + ScreenCaptureKit: 实时字幕 in-process audio capture
# (LiveCaptions.swift); Speech (macOS 26 SpeechAnalyzer) auto-links on import.
swiftc -O "$SRC_DIR"/*.swift "$SHARED_DIR"/*.swift -o "$BIN" \
    -framework AppKit -framework SwiftUI -framework Foundation \
    -framework AVFoundation -framework ScreenCaptureKit \
    ${SPARKLE_FLAGS[@]+"${SPARKLE_FLAGS[@]}"} \
    || build_failed "app compile (swiftc) exited non-zero — errors above"
echo "    built binary: $BIN"

# --- compile vault-sync helper (claude TCC isolation, 2026-07-14) ---
# Ships INSIDE the app bundle so it inherits the bundle's stable TCC identity:
# the user grants Documents access once via the app (one GUI prompt), and this
# courier reuses that grant from cron forever — claude/python/bash never touch
# the vault again, so a claude CLI update can no longer re-prompt or EPERM.
# A compile ERROR here is fatal (build_failed) — the runtime fallback (legacy
# direct-vault mode when the helper binary is absent) covers old installs, not
# a broken source file riding a release.
VAULTSYNC_SRC="$SCRIPT_DIR/VaultSyncHelper.swift"
if [ -f "$VAULTSYNC_SRC" ]; then
    echo "==> Compiling vault-sync-helper"
    swiftc -O "$VAULTSYNC_SRC" -o "$BUILD_DIR/vault-sync-helper" -framework Foundation \
        || build_failed "vault-sync-helper compile (swiftc) exited non-zero — errors above"
    echo "    built binary: $BUILD_DIR/vault-sync-helper"
fi

# --- compile framegrab helper (§13: video → evenly spaced JPEG frames) ---
# A compile ERROR here is fatal too — the ffmpeg fallback exists for machines
# without the binary, not for shipping a release that silently dropped it.
FRAMEGRAB_SRC="$SCRIPT_DIR/framegrab.swift"
if [ -f "$FRAMEGRAB_SRC" ]; then
    echo "==> Compiling framegrab"
    swiftc -O "$FRAMEGRAB_SRC" -o "$BUILD_DIR/framegrab" \
        -framework AVFoundation -framework CoreImage -framework Foundation \
        || build_failed "framegrab compile (swiftc) exited non-zero — errors above"
    echo "    built binary: $BUILD_DIR/framegrab"
fi

# --- assemble .app bundle ---
echo "==> Assembling bundle: $APP_DIR"
rm -rf "$APP_DIR"
mkdir -p "$APP_DIR/Contents/MacOS"
mkdir -p "$APP_DIR/Contents/Resources"
cp "$BIN" "$APP_DIR/Contents/MacOS/$EXEC_NAME"
cp "$PLIST" "$APP_DIR/Contents/Info.plist"
# vault-sync courier rides in the bundle → same bundle id + signature = the
# one TCC Documents grant the user gives the app covers it (incl. from cron).
if [ -x "$BUILD_DIR/vault-sync-helper" ]; then
    cp "$BUILD_DIR/vault-sync-helper" "$APP_DIR/Contents/MacOS/vault-sync-helper"
    echo "    bundled vault-sync-helper"
fi
# version (§56.1): VERSION was derived before the compile step. Stamp the
# STAGED plist only — the source Info.plist keeps its placeholder.
plutil -replace CFBundleShortVersionString -string "$VERSION" "$APP_DIR/Contents/Info.plist"
plutil -replace CFBundleVersion -string "$VERSION" "$APP_DIR/Contents/Info.plist"
echo "    stamped version $VERSION (git tag truth, scripts/build_version.sh)"
# app icon (optional — present after icon generation)
if [ -f "$SCRIPT_DIR/AppIcon.icns" ]; then
    cp "$SCRIPT_DIR/AppIcon.icns" "$APP_DIR/Contents/Resources/AppIcon.icns"
    echo "    bundled AppIcon.icns"
fi

# --- embed Sparkle.framework (ditto preserves the Versions/ symlinks + exec bits) ---
if [ -d "$SPARKLE_FW" ]; then
    echo "==> Embedding Sparkle.framework"
    mkdir -p "$APP_DIR/Contents/Frameworks"
    ditto "$SPARKLE_FW" "$APP_DIR/Contents/Frameworks/Sparkle.framework"
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

# Sign a .app bundle correctly whether or not Sparkle is embedded. Sparkle ships
# its own nested Mach-O / bundles (XPC services, the Autoupdate helper, Updater.app);
# those MUST be signed inside-out (deepest first), THEN the framework, THEN the
# outer app WITHOUT --deep. --deep re-signs the nested Sparkle code with generic
# flags and is the classic cause of "nested code is modified / invalid" seal
# breakage — so we drop it and sign the nested items explicitly.
sign_bundle() {   # $1 = .app path
    local app="$1" spk="$1/Contents/Frameworks/Sparkle.framework"
    if [ -d "$spk" ]; then
        local ver="$spk/Versions/B"
        for nested in \
            "$ver/XPCServices/Installer.xpc" \
            "$ver/XPCServices/Downloader.xpc" \
            "$ver/Updater.app" \
            "$ver/Autoupdate"; do
            [ -e "$nested" ] && codesign --force --sign "$SIGN_ID" --timestamp=none "$nested"
        done
        codesign --force --sign "$SIGN_ID" --timestamp=none "$spk"
    fi
    # OUTER app last, WITHOUT --deep — nested code (if any) is already signed above.
    codesign --force --sign "$SIGN_ID" "$app"
}
sign_bundle "$APP_DIR" || \
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
    # Stage-then-swap: copy the new bundle NEXT TO the installed one first and
    # only replace the old app after the copy fully succeeded — deleting the
    # old app before cp meant a failed/interrupted copy left the user with no
    # (or a half-copied, broken-signature) app in $DEST. The running instance
    # is quit only around the near-instant rm+mv, not the whole copy: quitting
    # matters because overwriting a live app leaves the OLD version running
    # (menu bar still shows it) until a manual quit. Graceful quit via Apple
    # Event first, pkill only as the fallback; relaunch so the upgrade is
    # invisible.
    STAGED="$DEST/.$APP_NAME.app.staged"
    echo "==> Staging new bundle at $STAGED"
    rm -rf "$STAGED"
    if cp -R "$APP_DIR" "$STAGED"; then
        WAS_RUNNING=0
        if pgrep -x "$EXEC_NAME" >/dev/null 2>&1; then
            WAS_RUNNING=1
            echo "==> Quitting the running $APP_NAME instance"
            # by bundle id, not by name: the running copy may still sit under the
            # pre-swap folder name (§54), and an unknown name makes AppleScript ask
            osascript -e "tell application id \"$BUNDLE_ID\" to quit" >/dev/null 2>&1 || true
            for _ in 1 2 3 4 5 6 7 8 9 10; do
                pgrep -x "$EXEC_NAME" >/dev/null 2>&1 || break
                sleep 0.5
            done
            pkill -x "$EXEC_NAME" 2>/dev/null || true
        fi
        echo "==> Installing to $DEST"
        rm -rf "$DEST/$APP_NAME.app"
        mv "$STAGED" "$DEST/$APP_NAME.app"
        FINAL="$DEST/$APP_NAME.app"
        # re-sign in place (cp can perturb signature) — same inside-out helper.
        sign_bundle "$FINAL" 2>/dev/null || true
        if [ "$WAS_RUNNING" -eq 1 ]; then
            echo "==> Relaunching $APP_NAME ($DEST)"
            open "$FINAL" || echo "WARN: relaunch failed — start it manually: open \"$FINAL\""
        fi
    else
        rm -rf "$STAGED"
        echo "WARN: copy to $DEST failed; the installed app was left untouched — using built bundle in place."
    fi
fi

echo ""
echo "DONE. App bundle: $FINAL"
echo "  Launch with: open \"$FINAL\"   (menu-bar only, no Dock icon)"
