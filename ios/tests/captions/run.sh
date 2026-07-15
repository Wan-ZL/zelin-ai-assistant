#!/bin/bash
# run.sh — behavior tests for the 实时字幕 pure-logic layer
# (mac/Sources/CaptionCore.swift: Doubao ASR frame codec + gzip decode +
# payload interpreter + 2-line caption reducer), compiled with plain swiftc —
# no Xcode, no test framework. Mirrors ios/tests/contract/run.sh: a main.swift
# harness + the source under test become one CLI tool; any failed assertion
# exits non-zero.
#
# CaptionCore.swift is Foundation+Compression only by design — the whole
# network/audio/UI side (LiveCaptions.swift, CaptionOverlay.swift) stays out
# of this compile so the wire framing and roll-up semantics are testable
# headlessly (audio capture is not — see the PR's manual checklist).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
CORE="$REPO/mac/Sources/CaptionCore.swift"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "==> [1/2] Compile harness (swiftc, CaptionCore.swift + main.swift)"
# swiftc only allows top-level statements in a file literally named main.swift,
# so compile the harness under that name (the source under test stays a
# library file).
cp "$HERE/CaptionsHarness.swift" "$WORK/main.swift"
swiftc -O "$CORE" "$WORK/main.swift" -o "$WORK/CaptionsHarness"

echo "==> [2/2] Run assertions"
"$WORK/CaptionsHarness"
