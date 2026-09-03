"""act/lib/detached.py — launch ``python -m act.<module> …`` detached from a daemon pass.

The inbox's no-id special forms (weekly digest on demand §24, the recap
buttons §63) need a claude call that can run for minutes; actd's pass is 10 s.
Pattern (the merge-review analysis subprocess precedent): ``Popen`` in a new
session, never waited on, stdout/err appended to ``state/<log_name>`` — the
child outlives the pass and the daemon never blocks on it.

:func:`launch` is the whole contract: it returns the §5.4 result_status
(``"running"`` started | ``"noop"`` launch failed) and reports through the
caller's ``log`` callable, so a button press can never take the daemon down.
"""
from __future__ import annotations

import subprocess
import sys
from typing import Callable, Optional

from act.lib import config

RUNNING = "running"
NOOP = "noop"


def spawn(module_argv: list, log_name: str) -> None:
    """``python -m <module_argv…>`` detached; raises on a failed launch."""
    config.ensure_state_dirs()
    with open(config.STATE_DIR / log_name, "ab") as fh:
        subprocess.Popen(
            [sys.executable, "-m"] + [str(a) for a in module_argv],
            cwd=str(config.HOME),
            stdin=subprocess.DEVNULL,
            stdout=fh,
            stderr=fh,
            start_new_session=True,  # detached: outlives the pass
        )


def launch(module_argv: list, log_name: str, label: str,
           log: Optional[Callable[[str], None]] = None) -> str:
    """Spawn and translate the outcome into the inbox ack vocabulary."""
    say = log or (lambda _msg: None)
    try:
        spawn(module_argv, log_name)
    except Exception as e:  # noqa: BLE001 — never let a button kill the pass
        say(f"inbox: {label} launch FAILED: {e}")
        return NOOP
    say(f"inbox: {label} — subprocess started")
    return RUNNING
