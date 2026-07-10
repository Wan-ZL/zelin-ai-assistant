"""Telemetry sync entrypoint — upload new analytics events to Supabase.

Default ON with opt-out (config.yaml ``telemetry:`` block or the app's
Settings toggle; see docs/TELEMETRY.md). Disabled/unconfigured -> exits 0
silently; before the first-run consent surface was shown (and without any
explicit telemetry config) it exits 0 with a "waiting for first-run consent
surface" log line and uploads nothing. All logic in act/lib/analytics_sync.

Run standalone:  python -m act.analytics_sync --once
"""
from __future__ import annotations

import argparse
from typing import Optional

from act.lib import analytics_sync


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="analytics_sync")
    ap.add_argument("--once", action="store_true",
                    help="run one sync pass and exit (the only mode)")
    ap.parse_args(argv)
    analytics_sync.sync_once()
    return 0  # never non-zero: telemetry must never fail a cron chain


if __name__ == "__main__":
    raise SystemExit(main())
