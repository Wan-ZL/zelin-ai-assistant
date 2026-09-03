#!/usr/bin/env bash
# Select the PINNED Xcode on a GitHub macOS runner (issue #15).
#
# Single source of truth for the toolchain = .github/xcode-version (one line,
# e.g. "26.6"); ci.yml and release.yml both run this script, so the CI run
# and the release build of the same commit can never resolve different
# Xcodes. Before this, both workflows did `find /Applications -name
# 'Xcode*.app' | sort -V | tail -n1` — "whatever is newest on the image" —
# and a runner-image refresh could swap the toolchain between a green CI
# run and the tag build minutes later.
#
# Fail loudly: a pinned version missing from the image is an error that
# lists what IS installed, never a silent fallback to another toolchain.
#
# Bumping: edit .github/xcode-version in a PR (CI proves the new pin exists
# on the current image). The Swift sources need a recent toolchain (local
# dev builds on Swift 6.x / macOS 26 SDK; older Xcodes fail on main-actor
# isolation rules), so only bump forward.
#
# Test seams (tests/integration/test_select_xcode.py drives the real script):
#   XCODE_VERSION_FILE  pin file (default .github/xcode-version)
#   XCODE_APPS_DIR      where Xcode_<ver>.app bundles live (default /Applications)
#   XCODE_SUDO          prefix for xcode-select (default sudo; set empty to skip)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PIN_FILE="${XCODE_VERSION_FILE:-$ROOT/.github/xcode-version}"
APPS="${XCODE_APPS_DIR:-/Applications}"
SUDO="${XCODE_SUDO-sudo}"

if [ ! -s "$PIN_FILE" ]; then
  echo "::error::Xcode pin file missing or empty: $PIN_FILE" >&2
  exit 1
fi
VERSION="$(tr -d '[:space:]' < "$PIN_FILE")"
case "$VERSION" in
  ''|*[!0-9.]*)
    echo "::error::Xcode pin must look like 26.6 (got '$VERSION' in $PIN_FILE)" >&2
    exit 1 ;;
esac

XCODE="$APPS/Xcode_$VERSION.app"
if [ ! -d "$XCODE/Contents/Developer" ]; then
  echo "::error::Pinned Xcode $VERSION is not on this runner image ($XCODE missing)." >&2
  echo "Installed Xcodes under $APPS:" >&2
  # shellcheck disable=SC2012  # listing bundle names for a human, not parsing
  ls -1d "$APPS"/Xcode*.app 2>/dev/null | sed 's/^/  /' >&2 || echo "  (none)" >&2
  echo "Bump .github/xcode-version in a PR to a version the image ships, or wait for the image to catch up — never fall back to 'newest'." >&2
  exit 1
fi

# shellcheck disable=SC2086  # SUDO is intentionally word-split (may be empty)
$SUDO xcode-select -s "$XCODE/Contents/Developer"
echo "selected Xcode $VERSION ($XCODE)"
swiftc --version
