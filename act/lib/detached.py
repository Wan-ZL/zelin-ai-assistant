"""act/lib/detached.py — launch ``python -m act.<module> …`` detached from a request.

The recap buttons (§63: 「重新生成 / 现在生成 / 回退到这一版」) need a claude call
that can run for minutes; an HTTP request cannot wait that long. The board
server (``server/recaps.py``) spawns ``python -m act.recap …`` through here:
``Popen`` in a new session, never waited on, stdout/err appended to
``state/<log_name>`` — the child outlives the request.

:func:`launch` is the whole contract: it returns ``"running"`` (started) or
``"noop"`` (launch failed) and reports through the caller's ``log`` callable, so
a button press can never take the server down. ``home`` (add-only, v2.0.0) pins
the child's ``AIASSISTANT_HOME`` / cwd / log location to the caller's home
instead of this process's env.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional

from act.lib import config

RUNNING = "running"
NOOP = "noop"


def spawn(module_argv: list, log_name: str, home: Optional[Path] = None) -> None:
    """``python -m <module_argv…>`` detached; raises on a failed launch."""
    if home is None:
        config.ensure_state_dirs()
        root, env = config.HOME, None
    else:
        root = Path(home)
        env = dict(os.environ, AIASSISTANT_HOME=str(root))
    state = root / "state"
    state.mkdir(parents=True, exist_ok=True)
    with open(state / log_name, "ab") as fh:
        subprocess.Popen(
            [sys.executable, "-m"] + [str(a) for a in module_argv],
            cwd=str(root),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=fh,
            stderr=fh,
            start_new_session=True,  # detached: outlives the request
        )


def launch(module_argv: list, log_name: str, label: str,
           log: Optional[Callable[[str], None]] = None,
           home: Optional[Path] = None) -> str:
    """Spawn and translate the outcome into ``running`` / ``noop``."""
    say = log or (lambda _msg: None)
    try:
        spawn(module_argv, log_name, home=home)
    except Exception as e:  # noqa: BLE001 — never let a button kill the server
        say(f"{label} launch FAILED: {e}")
        return NOOP
    say(f"{label} — subprocess started")
    return RUNNING
