"""Install report writer (CONTRACT §23) — ``state/install_report.json``.

install.sh (both the interactive run and the .pkg's ``--pkg-postinstall``
mode) records what it actually did — one entry per step plus the list of
launchd agents it loaded — so the app's first-run surfaces and ``act.doctor``
can tell "installed but inert" apart from "healthy" without the user reading
``/var/log/install.log``.

Shell interface (what install.sh calls)::

    printf '%s' "$STEPS" | python3 -m act.lib.install_report \
        --mode pkg-postinstall --steps-stdin --agents "label1 label2"

where ``$STEPS`` is newline-separated ``name=status[:detail]`` lines
(status ∈ ok|warn|fail|skipped; detail is free text and may contain colons).

The write is atomic (.tmp + os.replace) so a concurrent reader never sees a
torn file. The report is diagnostics, not control flow — failures here must
never break an install, hence the CLI catches everything and exits 1 quietly.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import getpass
import json
import os
import sys
from pathlib import Path
from typing import List, Optional

from act import __version__
from act.lib import config

REPORT_PATH: Path = config.STATE_DIR / "install_report.json"

_VALID_STATUS = {"ok", "warn", "fail", "skipped"}


def parse_steps(text: str) -> List[dict]:
    """Parse newline-separated ``name=status[:detail]`` lines.

    Unknown statuses are preserved verbatim (add-only contract: readers must
    tolerate values they don't know); blank lines are skipped; a line without
    ``=`` is recorded as a fail-shaped entry rather than dropped, so a
    malformed installer edit is visible instead of silent.
    """
    steps: List[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if "=" not in line:
            steps.append({"name": line, "status": "fail", "detail": "unparsable step line"})
            continue
        name, rest = line.split("=", 1)
        status, _, detail = rest.partition(":")
        steps.append({
            "name": name.strip(),
            "status": status.strip() or "fail",
            "detail": detail.strip() or None,
        })
    return steps


def write_report(
    mode: str,
    steps: List[dict],
    agents_loaded: List[str],
    user: Optional[str] = None,
    path: Optional[Path] = None,
) -> Path:
    """Assemble and atomically write the report; returns the path written."""
    if user is None:
        try:
            user = getpass.getuser()
        except Exception:
            user = os.environ.get("USER", "unknown")
    target = path or REPORT_PATH
    report = {
        "version": __version__,
        "generated_at": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "mode": mode,
        "user": user,
        "steps": steps,
        "agents_loaded": agents_loaded,
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, target)
    return target


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="act.lib.install_report")
    parser.add_argument("--mode", required=True,
                        help="interactive | pkg-postinstall")
    parser.add_argument("--steps-stdin", action="store_true",
                        help="read name=status[:detail] lines from stdin")
    parser.add_argument("--step", action="append", default=[],
                        help="one name=status[:detail] entry (repeatable)")
    parser.add_argument("--agents", default="",
                        help="space-separated launchd labels that were loaded")
    args = parser.parse_args(argv)

    text = "\n".join(args.step)
    if args.steps_stdin:
        text += ("\n" if text else "") + sys.stdin.read()
    try:
        path = write_report(
            mode=args.mode,
            steps=parse_steps(text),
            agents_loaded=args.agents.split(),
        )
    except Exception as exc:  # diagnostics must never break an install
        print(f"install_report: {exc}", file=sys.stderr)
        return 1
    print(str(path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
