#!/bin/bash
# run.sh — shell/ Swift gates, XCTest-free (CONTRACT §61.5):
#   [1/9] typecheck every shell source as ONE module (what build.sh compiles);
#   [2/9]+[3/9] compile + run BridgeHarness.swift against every shell source
#       except main.swift (the bootstrap has top-level statements; the harness
#       is the main.swift of this build) — pins the `zaiShell` wire vocabulary
#       and the LegacyPrefs seed rules;
#   [4/9]+[5/9] compile + run PolicyHarness.swift the same way — pins the
#       window policies of §54 追记 (ExternalLinkPolicy / ReopenPolicy /
#       WindowTitlePolicy) that AppDelegate consults;
#   [6/9]+[7/9] compile + run MenuHarness.swift the same way — pins the
#       main-menu table of §54 追记「菜单 l10n」(MenuSpec: bilingual titles, key
#       equivalents, actions; ⌥⌘S retired) that AppDelegate installs verbatim;
#   [8/9]+[9/9] compile + run LaunchHarness.swift the same way — pins the
#       launch-source policy of D38 (§56.5 / §61 追记: `--background` argv or a
#       login-item launch builds the window without ordering it front).
# Mirrors ios/tests/captions/run.sh: plain swiftc, no Xcode project, any
# failed assertion exits non-zero.
#
# Sandbox: AIASSISTANT_HOME points at a throwaway dir so nothing the engines'
# singletons touch at init (config.yaml / overrides reads, analytics dir) can
# reach the live checkout.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SHELL_DIR="$(cd "$HERE/.." && pwd)"
REPO="$(cd "$SHELL_DIR/.." && pwd)"
SRC_DIR="$SHELL_DIR/Sources"
SHARED_I18N="$REPO/shared/Sources/I18n.swift"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

FRAMEWORKS=(-framework AppKit -framework WebKit -framework SwiftUI -framework Foundation
            -framework AVFoundation -framework ScreenCaptureKit -framework UserNotifications
            -framework ServiceManagement -framework Carbon)

echo "==> [1/9] Typecheck shell module (swiftc -typecheck, all Sources + shared I18n)"
swiftc -typecheck "$SRC_DIR"/*.swift "$SHARED_I18N" "${FRAMEWORKS[@]}"

# Every shell source except main.swift — each harness below becomes the
# main.swift of its own build.
NON_MAIN=()
for f in "$SRC_DIR"/*.swift; do
    [ "$(basename "$f")" = "main.swift" ] && continue
    NON_MAIN+=("$f")
done

# compile_harness <Name>: shell/tests/<Name>.swift → $WORK/<Name>/<Name>
compile_harness() {
    local name="$1"
    mkdir -p "$WORK/$name"
    cp "$HERE/$name.swift" "$WORK/$name/main.swift"
    swiftc -O "${NON_MAIN[@]}" "$SHARED_I18N" "$WORK/$name/main.swift" \
        -o "$WORK/$name/$name" "${FRAMEWORKS[@]}"
}

echo "==> [2/9] Compile bridge harness (every shell source except main.swift)"
compile_harness BridgeHarness

echo "==> [3/9] Run bridge assertions (AIASSISTANT_HOME sandboxed)"
AIASSISTANT_HOME="$WORK/home" "$WORK/BridgeHarness/BridgeHarness"

echo "==> [4/9] Compile policy harness (window policies, §54 追记)"
compile_harness PolicyHarness

echo "==> [5/9] Run policy assertions (AIASSISTANT_HOME sandboxed)"
AIASSISTANT_HOME="$WORK/home" "$WORK/PolicyHarness/PolicyHarness"

echo "==> [6/9] Compile menu harness (main-menu table, §54 追记「菜单 l10n」)"
compile_harness MenuHarness

echo "==> [7/9] Run menu assertions (AIASSISTANT_HOME sandboxed)"
AIASSISTANT_HOME="$WORK/home" "$WORK/MenuHarness/MenuHarness"

echo "==> [8/9] Compile launch harness (launch-source policy, D38)"
compile_harness LaunchHarness

echo "==> [9/9] Run launch assertions (AIASSISTANT_HOME sandboxed)"
AIASSISTANT_HOME="$WORK/home" "$WORK/LaunchHarness/LaunchHarness"
