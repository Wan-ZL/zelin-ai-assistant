#!/bin/bash
# run.sh — end-to-end cross-language E2E crypto interop test.
#
# Proves ios/Sources/E2E.swift byte-interops with act/lib/e2e.py in BOTH
# directions (Python encrypt → Swift decrypt; Swift encrypt → Python decrypt)
# plus the QR pairing blob. Needs NO Xcode: E2E.swift is Foundation + CryptoKit,
# and CryptoKit ships in the macOS SDK, so swiftc builds a plain CLI tool.
#
# act/lib/e2e.py lives in THIS repo; python3 needs its lazy `cryptography`
# dependency installed. E2E_PYREPO can point at another checkout to cross-test
# branches. A missing e2e.py is a HARD failure — this script is a CI gate
# (ci.yml) and the project's only cross-language crypto guard, so it must
# never silently self-skip.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IOS_DIR="$(cd "$HERE/../.." && pwd)"          # …/ios
REPO="$(cd "$IOS_DIR/.." && pwd)"             # repo root
E2E_SWIFT="$IOS_DIR/Sources/E2E.swift"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

PYREPO="${E2E_PYREPO:-$REPO}"
if [ ! -f "$PYREPO/act/lib/e2e.py" ]; then
    echo "ERROR: act/lib/e2e.py not found under $PYREPO — the interop gate cannot run." >&2
    echo "       (E2E_PYREPO=/path/to/checkout overrides the default: this repo root)" >&2
    exit 1
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
# PASS[3] = the 4th (last) Swift-encrypted blob verified — a canary so a
# fixture-key rename can never turn this gate into a vacuous zero-case pass
# (interop.py also enforces the exact blob count; belt and suspenders).
if grep -q "verify: ALL PASS" "$WORK/verify.log" && grep -qF "PASS[3]" "$WORK/verify.log"; then
    echo "  record interop PASS (board/label/action, Python<->Swift)"
else
    echo "  record interop FAIL (need 'verify: ALL PASS' plus the 4th blob's PASS[3] line)" >&2; exit 1
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
