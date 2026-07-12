#!/bin/bash
# Download + verify + vendor Sparkle.framework and its bin/ tools into mac/Frameworks/.
# Idempotent; safe to run in CI and locally. Repo stays lean (mac/Frameworks/ is
# gitignored) and the framework's absence is a supported build state — build.sh
# compiles WITHOUT auto-update when it's missing (see #if canImport(Sparkle)).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FW_DIR="$SCRIPT_DIR/../Frameworks"
VER="2.9.4"
SHA="ce89daf967db1e1893ed3ebd67575ed82d3902563e3191ca92aaec9164fbdef9"
URL="https://github.com/sparkle-project/Sparkle/releases/download/${VER}/Sparkle-${VER}.tar.xz"

if [ -d "$FW_DIR/Sparkle.framework" ] && [ -x "$FW_DIR/bin/sign_update" ]; then
    echo "==> Sparkle already vendored ($FW_DIR) — skipping."
    exit 0
fi
mkdir -p "$FW_DIR"
TMP="$(mktemp -d)"
TARBALL="$TMP/sparkle.tar.xz"
echo "==> Downloading Sparkle $VER"
curl -fsSL "$URL" -o "$TARBALL"
echo "$SHA  $TARBALL" | shasum -a 256 -c - || { echo "ERROR: Sparkle checksum mismatch" >&2; exit 1; }
tar -xJf "$TARBALL" -C "$FW_DIR" Sparkle.framework bin
rm -rf "$TMP"
echo "==> Vendored Sparkle.framework + bin/ into $FW_DIR"
