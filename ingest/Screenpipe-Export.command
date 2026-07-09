#!/bin/bash
# Manual "export now" trigger (double-click / 主窗口"立即导出" — CONTRACT §18).
# Calls the REPO's ingest scripts (export + process), NOT the legacy copies in
# ~/Applications, so cron / the app / this trigger all run the same code.
export PATH="/opt/homebrew/bin:$PATH"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "Exporting screenpipe data to Obsidian..."
"$REPO_ROOT/ingest/screenpipe-export.sh"
echo ""
echo "Processing the unprocessed inbox..."
# manual trigger: the export above just finished, no need for the cron-chain
# partial-write guard sleep
SCREENPIPE_NO_WAIT=1 "$REPO_ROOT/ingest/process-screenpipe.sh"
RC=$?
echo ""
if [ "$RC" -eq 3 ]; then
    echo "Another ingest is already running — skipped this one."
else
    echo "Done."
fi
osascript -e "tell application \"Terminal\" to close (every window whose tty is \"$(tty)\")" &
exit 0
