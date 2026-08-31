#!/bin/bash
# Build + assemble the "Zelin AI Board" thin shell (.app bundle) — no Xcode project.
#
# Usage:
#   ./build.sh                    # compile + assemble under shell/build/
#   ./build.sh --check-toolchain  # only verify swiftc presence + version, then exit
#
# Conventions mirror mac/build.sh (swiftc + hand-assembled bundle + plutil lint
# + codesign)。差异点：这里是 preview shell —— ad-hoc 签名足够（无 TCC 授权要保），
# 且 codesign 用 --deep（bundle 里只有一个 Mach-O，没有 Sparkle 嵌套结构要保护）。
#
# Naming:
#   bundle:     Zelin AI Board.app
#   executable: ZelinAIBoard
#   bundle id:  com.zelin.ai-board
set -euo pipefail

# --- locate self (worktree-safe, handles spaces) ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- toolchain gate（same floor as mac/build.sh / ci.yml）---
MIN_SWIFT="6.0"

check_toolchain() {
    if ! command -v swiftc >/dev/null 2>&1; then
        echo "ERROR: swiftc not found. Install Xcode Command Line Tools: xcode-select --install" >&2
        return 1
    fi
    local ver
    ver="$(swiftc --version 2>/dev/null | sed -n 's/.*Swift version \([0-9][0-9.]*\).*/\1/p' | head -n1)"
    if [ -z "$ver" ]; then
        echo "WARN: could not parse Swift version from: $(swiftc --version 2>/dev/null | head -n1)" >&2
        return 0
    fi
    if [ "$(printf '%s\n%s\n' "$MIN_SWIFT" "$ver" | sort -V | head -n1)" != "$MIN_SWIFT" ]; then
        echo "ERROR: Swift $ver is too old — need Swift >= $MIN_SWIFT." >&2
        echo "  fix: update Xcode, then: sudo xcode-select -s /Applications/Xcode.app/Contents/Developer" >&2
        return 1
    fi
    return 0
}

if [ "${1:-}" = "--check-toolchain" ]; then
    if check_toolchain; then exit 0; else exit 1; fi
fi

APP_NAME="Zelin AI Board"
EXEC_NAME="ZelinAIBoard"
SRC_DIR="$SCRIPT_DIR/Sources"
PLIST="$SCRIPT_DIR/Info.plist"
BUILD_DIR="$SCRIPT_DIR/build"
BIN="$BUILD_DIR/$EXEC_NAME"
APP_DIR="$BUILD_DIR/$APP_NAME.app"

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

# --- plist lint（先 lint 源文件，坏 plist 不进 bundle）---
plutil -lint "$PLIST" >/dev/null

# --- compile-failure policy: ANY swiftc failure = red build（同 mac/build.sh）---
build_failed() {   # $1 = which step broke
    echo "" >&2
    echo "BUILD FAILED: $1" >&2
    echo "  anything under $BUILD_DIR is from an EARLIER build — do not install or ship it." >&2
    exit 1
}

# --- compile ---
echo "==> Compiling $SRC_DIR/main.swift"
mkdir -p "$BUILD_DIR"
swiftc -O "$SRC_DIR/main.swift" -o "$BIN" \
    -framework AppKit -framework WebKit -framework Foundation \
    || build_failed "shell compile (swiftc) exited non-zero — errors above"
echo "    built binary: $BIN"

# --- assemble .app bundle ---
echo "==> Assembling bundle: $APP_DIR"
rm -rf "$APP_DIR"
mkdir -p "$APP_DIR/Contents/MacOS"
mkdir -p "$APP_DIR/Contents/Resources"
cp "$BIN" "$APP_DIR/Contents/MacOS/$EXEC_NAME"
cp "$PLIST" "$APP_DIR/Contents/Info.plist"

# version single source of truth: act/__init__.py（same extraction as mac/build.sh）。
# Stamp the STAGED plist only — 源 Info.plist 保留 fallback 值。
VERSION="$(sed -n 's/^__version__ = "\([^"]*\)".*/\1/p' "$SCRIPT_DIR/../act/__init__.py" 2>/dev/null || true)"
if [ -n "$VERSION" ]; then
    plutil -replace CFBundleShortVersionString -string "$VERSION" "$APP_DIR/Contents/Info.plist"
    plutil -replace CFBundleVersion -string "$VERSION" "$APP_DIR/Contents/Info.plist"
    echo "    stamped version $VERSION (from act/__init__.py)"
else
    echo "WARN: could not read __version__ from act/__init__.py — bundle keeps the Info.plist fallback version."
fi

# server repo: stamp the ACTUAL repo root this shell is built from（same
# staged-plist mechanism as the version stamp; 源 Info.plist 留空 = 未解析，
# 壳在需要 spawn 时礼貌报错而非猜路径）。
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
plutil -replace ZAIServerRepo -string "$REPO_ROOT" "$APP_DIR/Contents/Info.plist"
echo "    stamped ZAIServerRepo $REPO_ROOT"

# app icon — 复用主 app 的 AppIcon.icns（构建期已 vendored 进 shell/）
if [ -f "$SCRIPT_DIR/AppIcon.icns" ]; then
    cp "$SCRIPT_DIR/AppIcon.icns" "$APP_DIR/Contents/Resources/AppIcon.icns"
    echo "    bundled AppIcon.icns"
fi

# lint the staged plist too（plutil -replace 之后再验一次）
plutil -lint "$APP_DIR/Contents/Info.plist" >/dev/null

# --- codesign: ad-hoc（preview shell，无需稳定身份；--deep 安全——无嵌套 bundle）---
echo "==> Ad-hoc codesigning"
codesign --force --deep -s - "$APP_DIR" \
    || echo "WARN: codesign failed (app may still run after Gatekeeper prompt)."

echo ""
echo "DONE. App bundle: $APP_DIR"
echo "  Launch with: open \"$APP_DIR\""
