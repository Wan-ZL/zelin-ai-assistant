#!/bin/bash
# Build + assemble the "Zelin's AI Assistant" shell (.app bundle) — no Xcode project.
#
# Usage:
#   ./build.sh                    # compile + assemble under shell/build/
#   ./build.sh --check-toolchain  # only verify swiftc presence + version, then exit
#   ZAI_PORT=47821 ./build.sh     # also stamp ZAIServerPort (install.sh passes
#                                 # config.yaml server.port this way, CONTRACT §54)
#
# swiftc + hand-assembled bundle + plutil lint + codesign。签名用稳定自签证书
# （shell/scripts/make-signing-cert.sh 生成一次；缺证书 / 签不动才回落 ad-hoc；#316，
# §54.3 2026-09-12 修正），且 codesign 用 --deep——bundle 里除主 Mach-O 还有 §68.13 的
# vault-sync-helper 一个可执行，--deep 让它与壳同一身份；安全的原因是这里没有
# Sparkle 那样的嵌套 bundle 需要 inside-out 逐个签。
# （壳自己不持有任何磁盘 TCC 授权——server 自 v0.48.18 起由 launchd 托管，壳只连接。）
# ad-hoc 签名 = 每次重建后 TCC 屏幕录制授权失效，所以只作退路
# （docs/TROUBLESHOOTING.md「换壳后的 TCC 重授权」）。
# 不 quit / 不 relaunch / 不装到 /Applications：安装动作归 install.sh 的 `ui` 步
# （§56.5 的 relaunch 规则住在那里）。
#
# Sources：shell/Sources/*.swift 全部编成一个 module（只有 main.swift 可含顶层
# 语句；I18n.swift 的 L() 双语文案自 lite 2.0 起也住在这里）。录制引擎
# Recording.swift 是 §61.3 的 source of truth（字节不动）。
#
# Naming (CONTRACT §54; owner 2026-09-02 — the shell is the product):
#   bundle:       Zelin's AI Assistant.app
#   executable:   ZelinAIBoard                 (unchanged — pgrep/pkill -x key)
#   bundle id:    com.zelin.ai-board           (unchanged — its own TCC identity)
#   display name: Zelin's AI Assistant         (Info.plist CFBundleDisplayName)
set -euo pipefail

# --- locate self (worktree-safe, handles spaces) ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- toolchain gate（same floor as ci.yml）---
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

APP_NAME="Zelin's AI Assistant"
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

# --- compile-failure policy: ANY swiftc failure = red build ---
build_failed() {   # $1 = which step broke
    echo "" >&2
    echo "BUILD FAILED: $1" >&2
    echo "  anything under $BUILD_DIR is from an EARLIER build — do not install or ship it." >&2
    exit 1
}

# --- version（先于 compile：答不上就别花 swiftc 的时间；上一次构建的 bundle 原样留着）---
# version truth = git tag（CONTRACT §56.1）：
# scripts/build_version.sh 挑一个真能读这个 checkout 的解释器（§55 第三幕：
# launchd 下 Homebrew python3 在外置卷上 EPERM）跑 stamper（顺手写 act/_version.py），
# 退而求其次用 act.__version__；都答不上 → 构建**失败**，绝不带着 Info.plist 的
# 占位版本出厂（2026-09-02：auto-deploy 装到 /Applications 的壳报 0.1.0）。
echo "==> Deriving the version (scripts/build_version.sh)"
VERSION="$(bash "$SCRIPT_DIR/../scripts/build_version.sh")" \
    || build_failed "could not derive the version to stamp (scripts/build_version.sh — see its messages above); refusing to ship the Info.plist placeholder"
echo "    version: $VERSION"

# --- compile ---
# AVFoundation: 麦克风授权探针（PermissionsProbe）；ScreenCaptureKit: 屏幕录制授权面；
# UserNotifications: Recording.swift 的 self-heal / TCC-loss 一次性通知 + §28 通知中继
# （NotifyRelay.swift）；ServiceManagement: 登录时启动（SMAppService）；Carbon: 登录项
# 启动事件的 Apple Event 常量（LaunchAtLogin.isLoginItemLaunch）。
echo "==> Compiling $SRC_DIR/*.swift"
mkdir -p "$BUILD_DIR"
swiftc -O "$SRC_DIR"/*.swift -o "$BIN" \
    -framework AppKit -framework WebKit -framework SwiftUI -framework Foundation \
    -framework AVFoundation -framework ScreenCaptureKit -framework UserNotifications \
    -framework ServiceManagement -framework Carbon \
    || build_failed "shell compile (swiftc) exited non-zero — errors above"
echo "    built binary: $BIN"

# --- helper binary（s4 Tier-0 0.2；CONTRACT §68.13）---
# vault-sync-helper：vault ⇄ 镜像的唯一 courier（~/Documents 的 TCC 授权按 bundle 记账，
# 装进壳 bundle 后与壳同一身份；ingest/vault-sync.sh find_vault_sync_helper 先找本壳）。
# 编译错误 = 构建失败。（framegrab 随 Slack radar 在 v2.0.0 退役，全文见 tag full。）
HELPERS_DIR="$SCRIPT_DIR/Helpers"
VAULTSYNC_SRC="$HELPERS_DIR/VaultSyncHelper.swift"
if [ -f "$VAULTSYNC_SRC" ]; then
    echo "==> Compiling vault-sync-helper"
    swiftc -O "$VAULTSYNC_SRC" -o "$BUILD_DIR/vault-sync-helper" -framework Foundation \
        || build_failed "vault-sync-helper compile (swiftc) exited non-zero — errors above"
    echo "    built binary: $BUILD_DIR/vault-sync-helper"
fi

# --- assemble .app bundle ---
echo "==> Assembling bundle: $APP_DIR"
rm -rf "$APP_DIR"
mkdir -p "$APP_DIR/Contents/MacOS"
mkdir -p "$APP_DIR/Contents/Resources"
cp "$BIN" "$APP_DIR/Contents/MacOS/$EXEC_NAME"
cp "$PLIST" "$APP_DIR/Contents/Info.plist"
if [ -x "$BUILD_DIR/vault-sync-helper" ]; then
    cp "$BUILD_DIR/vault-sync-helper" "$APP_DIR/Contents/MacOS/vault-sync-helper"
    echo "    bundled vault-sync-helper"
fi

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

# --- codesign: prefer the stable self-signed identity (shell/scripts/make-signing-cert.sh) so the
# shell's TCC grants (Screen Recording / Microphone / Documents) SURVIVE
# reinstalls. Ad-hoc ("-") identity = the binary's cdhash, which changes on every build:
# after each install.sh macOS treats the shell as a new app, the System Settings toggle
# still reads "on" for the old build, and every capture attempt re-prompts (#316).
# --deep is REQUIRED, not decoration: Contents/MacOS also holds the §68.13 helper
# vault-sync-helper, and --deep is what signs it with the same identity.
# It is safe here only because there is no Sparkle-style nested bundle needing inside-out
# signing.
SIGN_ID="Zelin AI Engineer Dev"
# No `-v`: the identity is a self-signed cert that is NOT trusted, and `-v` hides
# untrusted identities. Trust is irrelevant to codesign + TCC persistence.
if security find-identity -p codesigning 2>/dev/null | grep -q "$SIGN_ID"; then
    echo "==> Codesigning with '$SIGN_ID' (stable identity, TCC-safe)"
else
    SIGN_ID="-"
    echo "==> Ad-hoc codesigning (identity missing — TCC grants will reset on reinstall)"
fi

# Bounded + fall back（§54.3 2026-09-12 修正）：install.sh 的 `ui` 步在 launchd 下跑本脚本，
# 没有人能点对话框。用 keychain 里的私钥签名**可能**弹一次 GUI 授权框——
# shell/scripts/make-signing-cert.sh 的 `security set-key-partition-list`（那把钥匙的 ACL：
# `-T /usr/bin/codesign`）是**可跳过**的一步，跳过了的机器上 codesign 就停在那个框上等一个
# 不存在的人。`|| echo WARN` 接得住失败、接不住 hang：hang 会把 ui 步的 AIASSISTANT_UI_BUDGET
# （默认 600 s）整份烧光 → UI_SHELL_STATUS=fail → 自动部署失败。macOS 没有 timeout(1)，
# 所以照 install.sh ui_run_with_timeout 的形自己算墙钟，超时 / 失败都回落 ad-hoc——
# 授权掉一次远好过部署红一次。
CODESIGN_BUDGET_S="${ZAI_CODESIGN_BUDGET_S:-60}"

run_with_budget() {   # $1=seconds, rest=command；0 = ok，124 = timeout，其余 = 原始退出码
    local limit="$1"; shift
    "$@" &
    local pid=$!
    local ticks=0
    while kill -0 "$pid" 2>/dev/null; do
        if [ $((ticks / 4)) -ge "$limit" ]; then
            pkill -TERM -P "$pid" 2>/dev/null
            kill -TERM "$pid" 2>/dev/null
            sleep 1
            pkill -KILL -P "$pid" 2>/dev/null
            kill -KILL "$pid" 2>/dev/null
            wait "$pid" 2>/dev/null
            return 124
        fi
        sleep 0.25
        ticks=$((ticks + 1))
    done
    wait "$pid"
}

SIGN_RC=0
run_with_budget "$CODESIGN_BUDGET_S" codesign --force --deep -s "$SIGN_ID" "$APP_DIR" || SIGN_RC=$?
if [ "$SIGN_RC" -ne 0 ] && [ "$SIGN_ID" != "-" ]; then
    if [ "$SIGN_RC" -eq 124 ]; then
        echo "WARN: codesign with '$SIGN_ID' hung past ${CODESIGN_BUDGET_S}s — a keychain prompt"
        echo "      nobody can click? Run 'bash shell/scripts/make-signing-cert.sh' once so the key's"
        echo "      partition list lets /usr/bin/codesign use it non-interactively."
    else
        echo "WARN: codesign with '$SIGN_ID' failed (exit $SIGN_RC)."
    fi
    echo "      Falling back to ad-hoc — TCC grants will reset on the next reinstall (#316)."
    SIGN_ID="-"
    SIGN_RC=0
    run_with_budget "$CODESIGN_BUDGET_S" codesign --force --deep -s "$SIGN_ID" "$APP_DIR" || SIGN_RC=$?
fi
[ "$SIGN_RC" -eq 0 ] \
    || echo "WARN: codesign failed (app may still run after Gatekeeper prompt)."

echo ""
echo "DONE. App bundle: $APP_DIR"
echo "  Launch with: open \"$APP_DIR\"   (bash install.sh installs it to /Applications)"
