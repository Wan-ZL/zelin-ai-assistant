#!/bin/bash
# run.sh — end-to-end cross-language E2E crypto interop test.
#
# Proves ios/Sources/E2E.swift byte-interops with act/lib/e2e.py in BOTH
# directions (Python encrypt → Swift decrypt; Swift encrypt → Python decrypt)
# plus the QR pairing blob. Needs NO Xcode: E2E.swift is Foundation + CryptoKit,
# and CryptoKit ships in the macOS SDK, so swiftc builds a plain CLI tool.
#
# act/lib/e2e.py must exist on the PYTHONPATH repo. It lives on the Phase-1a
# branch feat/ios-cloud-crypto; this branch (feat/ios-app) does not carry it.
# Point E2E_PYREPO at a checkout that has it (default: auto-detect a sibling
# worktree, else this repo root once crypto is merged).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IOS_DIR="$(cd "$HERE/../.." && pwd)"          # …/ios
REPO="$(cd "$IOS_DIR/.." && pwd)"             # branch worktree root
E2E_SWIFT="$IOS_DIR/Sources/E2E.swift"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# --- locate a Python repo that has act/lib/e2e.py ---
find_pyrepo() {
    [ -n "${E2E_PYREPO:-}" ] && { echo "$E2E_PYREPO"; return; }
    [ -f "$REPO/act/lib/e2e.py" ] && { echo "$REPO"; return; }
    # sibling worktrees under ../.claude/worktrees/*/act/lib/e2e.py
    local wt
    for wt in "$REPO"/../*/act/lib/e2e.py; do
        [ -f "$wt" ] && { echo "$(cd "$(dirname "$wt")/../.." && pwd)"; return; }
    done
    return 1
}

PYREPO="$(find_pyrepo || true)"
if [ -z "$PYREPO" ] || [ ! -f "$PYREPO/act/lib/e2e.py" ]; then
    echo "SKIP: act/lib/e2e.py not found (Phase-1a crypto branch not merged here)." >&2
    echo "      Re-run with E2E_PYREPO=/path/to/repo-with-act-lib-e2e" >&2
    exit 0
fi
echo "==> Python e2e.py from: $PYREPO"

export PYTHONPATH="$PYREPO"
export AIASSISTANT_HOME="$WORK/home"          # keep e2e's config import off real state
mkdir -p "$AIASSISTANT_HOME"

echo "==> [1/4] Python emits fixtures"
python3 "$HERE/interop.py" emit "$WORK/python_fixtures.json"

echo "==> [2/4] Compile Swift harness (swiftc, macOS CLI + CryptoKit)"
# swiftc only allows top-level statements in a file literally named main.swift,
# so compile the harness under that name (E2E.swift stays a library file).
cp "$HERE/InteropHarness.swift" "$WORK/main.swift"
swiftc -O "$E2E_SWIFT" "$WORK/main.swift" -o "$WORK/InteropHarness"

echo "==> [3/4] Swift decrypts Python blobs + encrypts its own (+ parses/builds pairing v2)"
"$WORK/InteropHarness" "$WORK/python_fixtures.json" "$WORK/swift_out.json" | tee "$WORK/swift.log"

echo "==> [4/4] Python verifies Swift-encrypted blobs (+ pairing v2 byte-identity)"
python3 "$HERE/interop.py" verify "$WORK/swift_out.json" | tee "$WORK/verify.log"

# --- explicit gates: record blobs AND the v2 channel pairing blob both ways ---
echo ""
echo "==> Gate summary"
if grep -q "verify: ALL PASS" "$WORK/verify.log"; then
    echo "  record interop PASS (board/label/action, Python<->Swift)"
else
    echo "  record interop FAIL" >&2; exit 1
fi
if grep -q "channel_pairing.build_matches_python" "$WORK/swift.log" \
   && grep -q "PASS channel_pairing.channel_id" "$WORK/swift.log" \
   && ! grep -q "FAIL channel_pairing" "$WORK/swift.log" \
   && grep -q "PASS channel_pairing: Swift-built blob byte-matches Python build" "$WORK/verify.log" \
   && grep -q "PASS channel_pairing: Python re-parsed Swift blob" "$WORK/verify.log"; then
    echo "  pairing interop PASS (ZQR1 channel blob, Python<->Swift byte-identical)"
else
    echo "  pairing interop FAIL" >&2; exit 1
fi

echo ""
echo "INTEROP OK — Swift E2E.swift byte-matches Python act/lib/e2e.py both ways"
echo "            (record blobs AND the v2 channel pairing blob)."
