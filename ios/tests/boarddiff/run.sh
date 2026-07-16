#!/bin/bash
# run.sh — behavior tests for the kanban flight layer's pure differ
# (mac/Sources/BoardDiff.swift: per-lane snapshot diff → moves/inserts/
# removals + the >6-change crossfade cap), compiled with plain swiftc — no
# Xcode, no test framework. Mirrors ios/tests/captions/run.sh: a main.swift
# harness + the source under test become one CLI tool; any failed assertion
# exits non-zero.
#
# BoardDiff.swift is Foundation-only by design — the whole SwiftUI side
# (BoardMotion.swift overlay/proxies, Kanban wiring) stays out of this
# compile so the diff semantics are testable headlessly (the flights
# themselves are on the PR's manual checklist).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
CORE="$REPO/mac/Sources/BoardDiff.swift"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "==> [1/2] Compile harness (swiftc, BoardDiff.swift + main.swift)"
# swiftc only allows top-level statements in a file literally named main.swift,
# so compile the harness under that name (the source under test stays a
# library file).
cp "$HERE/BoardDiffHarness.swift" "$WORK/main.swift"
swiftc -O "$CORE" "$WORK/main.swift" -o "$WORK/BoardDiffHarness"

echo "==> [2/2] Run assertions"
"$WORK/BoardDiffHarness"
