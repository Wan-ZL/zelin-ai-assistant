#!/bin/bash
# Build + assemble the "Zelin AI Board" shell (.app bundle) — no Xcode project.
#
# Usage:
#   ./build.sh                    # compile + assemble under shell/build/
#   ./build.sh --check-toolchain  # only verify swiftc presence + version, then exit
#   ZAI_PORT=47821 ./build.sh     # also stamp ZAIServerPort (install.sh passes
#                                 # config.yaml server.port this way, CONTRACT §54)
#
# Conventions mirror mac/build.sh (swiftc + hand-assembled bundle + plutil lint
# + codesign)。差异点：ad-hoc 签名（P4 过渡期；稳定证书随 Mac-retire 清单 0.9
# 一起决定；壳不持有任何磁盘 TCC 授权——server 自 v0.48.18 起由 launchd 托管，
# 壳只连接），且 codesign 用 --deep（bundle 里只有一个 Mach-O，没有 Sparkle
# 嵌套结构要保护）。注意 ad-hoc 签名 = 每次重建后 TCC 屏幕录制授权失效
# （docs/TROUBLESHOOTING.md「换壳后的 TCC 重授权」）。
# 不 quit / 不 relaunch / 不装到 /Applications：安装动作归 install.sh 的 `ui` 步
# （§56.5 的 relaunch 规则住在那里）。
#
# Sources：shell/Sources/*.swift 全部编成一个 module（只有 main.swift 可含顶层
# 语句）+ shared/Sources/I18n.swift（L() 双语文案；Foundation-only 共享文件）。
# 录制/字幕引擎（Recording / CaptionCore / LiveCaptions / CaptionOverlay）自
# mac/Sources 搬入（CONTRACT §61.3），所以链接的框架与 mac/build.sh 同一组。
#
# Naming (CONTRACT §54; vnext2-plan §8 — the final name swap waits for P8):
#   bundle:       Zelin AI Board.app          (folder name kept — id/TCC continuity)
#   executable:   ZelinAIBoard
#   bundle id:    com.zelin.ai-board
#   display name: Zelin's AI Assistant (Board)  (Info.plist CFBundleDisplayName)
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
# 只借 I18n.swift（L() + LanguageMirror）；shared/ 的其余文件是看板模型，壳不要。
SHARED_I18N="$SCRIPT_DIR/../shared/Sources/I18n.swift"
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
if [ ! -f "$SHARED_I18N" ]; then
    echo "ERROR: shared I18n source not found at: $SHARED_I18N" >&2
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

# --- version（先于 compile：答不上就别花 swiftc 的时间；上一次构建的 bundle 原样留着）---
# version truth = git tag（CONTRACT §56.1；same helper as mac/build.sh）：
# scripts/build_version.sh 挑一个真能读这个 checkout 的解释器（§55 第三幕：
# launchd 下 Homebrew python3 在外置卷上 EPERM）跑 stamper（顺手写 act/_version.py），
# 退而求其次用 act.__version__；都答不上 → 构建**失败**，绝不带着 Info.plist 的
# 占位版本出厂（2026-09-02：auto-deploy 装到 /Applications 的壳报 0.1.0）。
echo "==> Deriving the version (scripts/build_version.sh)"
VERSION="$(bash "$SCRIPT_DIR/../scripts/build_version.sh")" \
    || build_failed "could not derive the version to stamp (scripts/build_version.sh — see its messages above); refusing to ship the Info.plist placeholder"
echo "    version: $VERSION"

# --- compile ---
# AVFoundation + ScreenCaptureKit: 实时字幕 in-process audio capture
# (LiveCaptions.swift); Speech (macOS 26 SpeechAnalyzer) auto-links on import;
# UserNotifications: Recording.swift 的 self-heal / TCC-loss 一次性通知。
echo "==> Compiling $SRC_DIR/*.swift + $SHARED_I18N"
mkdir -p "$BUILD_DIR"
swiftc -O "$SRC_DIR"/*.swift "$SHARED_I18N" -o "$BIN" \
    -framework AppKit -framework WebKit -framework SwiftUI -framework Foundation \
    -framework AVFoundation -framework ScreenCaptureKit -framework UserNotifications \
    || build_failed "shell compile (swiftc) exited non-zero — errors above"
echo "    built binary: $BIN"

# --- assemble .app bundle ---
echo "==> Assembling bundle: $APP_DIR"
rm -rf "$APP_DIR"
mkdir -p "$APP_DIR/Contents/MacOS"
mkdir -p "$APP_DIR/Contents/Resources"
cp "$BIN" "$APP_DIR/Contents/MacOS/$EXEC_NAME"
cp "$PLIST" "$APP_DIR/Contents/Info.plist"

# version（§56.1）：上面 compile 前已算出 VERSION。Stamp the STAGED plist only —
# 源 Info.plist 保留占位值。
plutil -replace CFBundleShortVersionString -string "$VERSION" "$APP_DIR/Contents/Info.plist"
plutil -replace CFBundleVersion -string "$VERSION" "$APP_DIR/Contents/Info.plist"
echo "    stamped version $VERSION (git tag truth, scripts/build_version.sh)"

# server repo: stamp the ACTUAL repo root this shell is built from（same
# staged-plist mechanism as the version stamp; 源 Info.plist 留空 = 未解析，
# 壳在需要 spawn 时礼貌报错而非猜路径）。
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd -P)"
plutil -replace ZAIServerRepo -string "$REPO_ROOT" "$APP_DIR/Contents/Info.plist"
echo "    stamped ZAIServerRepo $REPO_ROOT"

# server port（§54）：install.sh 把 config.yaml server.port 经 env ZAI_PORT 交进来；
# 未设 = 留空 = 壳按 server 侧同一默认 47820 连。只收 1..65535 的整数。
case "${ZAI_PORT:-}" in
    ''|*[!0-9]*) ;;
    *)  if [ "$ZAI_PORT" -ge 1 ] && [ "$ZAI_PORT" -le 65535 ]; then
            plutil -replace ZAIServerPort -string "$ZAI_PORT" "$APP_DIR/Contents/Info.plist"
            echo "    stamped ZAIServerPort $ZAI_PORT"
        fi ;;
esac

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
echo "  Launch with: open \"$APP_DIR\"   (bash install.sh installs it to /Applications)"
