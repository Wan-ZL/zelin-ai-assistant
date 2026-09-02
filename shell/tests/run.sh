#!/bin/bash
# run.sh — shell/ Swift gates, XCTest-free (CONTRACT §61.5):
#   [1] typecheck every shell source as ONE module (what build.sh compiles);
#   [2] compile + run BridgeHarness.swift against every shell source except
#       main.swift (the bootstrap has top-level statements; the harness is the
#       main.swift of this build) — pins the `zaiShell` wire vocabulary and
#       the LegacyPrefs seed rules.
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
            -framework AVFoundation -framework ScreenCaptureKit -framework UserNotifications)

echo "==> [1/3] Typecheck shell module (swiftc -typecheck, all Sources + shared I18n)"
swiftc -typecheck "$SRC_DIR"/*.swift "$SHARED_I18N" "${FRAMEWORKS[@]}"

echo "==> [2/3] Compile bridge harness (every shell source except main.swift)"
NON_MAIN=()
for f in "$SRC_DIR"/*.swift; do
    [ "$(basename "$f")" = "main.swift" ] && continue
    NON_MAIN+=("$f")
done
cp "$HERE/BridgeHarness.swift" "$WORK/main.swift"
swiftc -O "${NON_MAIN[@]}" "$SHARED_I18N" "$WORK/main.swift" \
    -o "$WORK/BridgeHarness" "${FRAMEWORKS[@]}"

echo "==> [3/3] Run assertions (AIASSISTANT_HOME sandboxed)"
AIASSISTANT_HOME="$WORK/home" "$WORK/BridgeHarness"
