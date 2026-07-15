#!/bin/bash
# run.sh — behavior tests for the SHARED Foundation-only contract sources
# (shared/Sources), compiled with plain swiftc — no Xcode, no test framework.
# Mirrors ios/tests/interop/run.sh: a main.swift harness + the sources under
# test become one CLI tool; any failed assertion exits non-zero.
#
# Covers the two most intricate hand-written pieces both apps compile but no
# other check executes:
#   - Contract.swift: per-row lossy dashboard.json decode (decodeLossyRows,
#     stableFallbackID, decodeDrops, update_available suppression)
#   - BoardModel.swift: running-lane merge order + lane badge counts
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
SHARED_DIR="$REPO/shared/Sources"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "==> [1/2] Compile harness (swiftc, shared/Sources + main.swift)"
# swiftc only allows top-level statements in a file literally named main.swift,
# so compile the harness under that name (shared sources stay library files).
cp "$HERE/ContractHarness.swift" "$WORK/main.swift"
swiftc -O "$SHARED_DIR"/*.swift "$WORK/main.swift" -o "$WORK/ContractHarness"

echo "==> [2/2] Run assertions"
"$WORK/ContractHarness"
