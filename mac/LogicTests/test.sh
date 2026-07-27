#!/bin/bash
# mac/LogicTests/test.sh — run the Swift logic tests (本地第四道门, CONTRIBUTING.md).
#
# With full Xcode selected (CI, most dev machines) this is EXACTLY
#   swift test --package-path mac/LogicTests
# and the wrapper adds nothing. On a Command-Line-Tools-only machine
# (xcode-select -p → /Library/Developer/CommandLineTools) the CLT ships
# Testing.framework but (CLT 26.x packaging bug) neither puts it on the
# default search path nor resolves its lib_TestingInterop.dylib @rpath —
# the framework's own relative rpath assumes the Xcode.app layout, one
# directory level off. So on CLT toolchains we pass -F and the two -rpath
# entries explicitly. XCTest is NOT an option here: CLT ships no
# XCTest.framework at all, which is why the tests use Swift Testing.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

FLAGS=()
DEV_DIR="$(xcode-select -p 2>/dev/null || true)"
case "$DEV_DIR" in
    *CommandLineTools*)
        FW="$DEV_DIR/Library/Developer/Frameworks"
        RPATH_LIB="$DEV_DIR/Library/Developer/usr/lib"
        if [ ! -d "$FW/Testing.framework" ]; then
            echo "ERROR: this Command Line Tools install has no Testing.framework —" >&2
            echo "  update the CLT (softwareupdate) or install full Xcode, then retry." >&2
            exit 1
        fi
        FLAGS=( -Xswiftc -F -Xswiftc "$FW"
                -Xlinker -F -Xlinker "$FW"
                -Xlinker -rpath -Xlinker "$FW"
                -Xlinker -rpath -Xlinker "$RPATH_LIB" )
        ;;
esac

exec swift test --package-path "$SCRIPT_DIR" ${FLAGS[@]+"${FLAGS[@]}"}
