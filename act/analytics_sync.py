"""Telemetry sync entrypoint — upload new analytics events to Supabase.

CONTRACT §15（telemetry 开关 + consent 门）/ §16（隐私 gate）。The cron line
install.sh writes is ``python -m act.analytics_sync --once`` (hourly).

Default ON with opt-out (config.yaml ``telemetry:`` block or the app's
Settings toggle; see docs/TELEMETRY.md). Disabled/unconfigured -> exits 0
silently; before the first-run consent surface was shown (and without any
explicit telemetry config) it exits 0 with a "waiting for first-run consent
surface" log line and uploads nothing. All logic in act/lib/telemetry_upload.

Run standalone:  python -m act.analytics_sync --once
"""
from __future__ import annotations

import argparse
from typing import Optional

from act.lib import telemetry_upload


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="analytics_sync")
    ap.add_argument("--once", action="store_true",
                    help="run one sync pass and exit (the only mode)")
    ap.parse_args(argv)
    telemetry_upload.sync_once()
    return 0  # never non-zero: telemetry must never fail a cron chain


if __name__ == "__main__":
    raise SystemExit(main())
