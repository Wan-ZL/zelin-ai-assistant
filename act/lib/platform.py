"""OS seam — the generic OS-specific effects behind one thin choke point.

Exactly three concerns in the python tree are generic enough to port (the
full audit lives in docs/PORTING.md): firing a user notification, opening a
path with the system file handler, and listing the user's background
services. The darwin implementations delegate to the exact commands this
codebase always ran (osascript / open / launchctl); linux gets the cheap
honest equivalent where one exists (notify-send, xdg-open) and a truthful
empty result where none does yet.

NOT here on purpose:
  - anything already portable: claude / git / gh subprocess calls.

Every function is best-effort and NEVER raises — a failed notification or
reveal must not break the daemon loop (the posture act/lib/notify.py always
had). ``runner`` is the injectable subprocess runner used by tests.
"""
from __future__ import annotations

import os
import subprocess
import sys
from typing import Callable, List, Optional

Runner = Callable[[List[str], float], "subprocess.CompletedProcess"]


def is_darwin() -> bool:
    return sys.platform == "darwin"


def _run(argv: List[str], timeout: float) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)


def notify_user(title: str, body: str, subtitle: Optional[str] = None,
                runner: Optional[Runner] = None) -> bool:
    """Fire a native user notification. True on success, never raises.

    darwin: osascript ``display notification``. linux: notify-send when
    present (desktop sessions; headless boxes just return False). Other OSes:
    False until a port lands (docs/PORTING.md).
    """
    if is_darwin():
        def esc(s: str) -> str:
            return str(s).replace("\\", "\\\\").replace('"', '\\"')

        script = f'display notification "{esc(body)}" with title "{esc(title)}"'
        if subtitle:
            script += f' subtitle "{esc(subtitle)}"'
        argv = ["osascript", "-e", script]
    elif sys.platform.startswith("linux"):
        text = f"{subtitle}\n{body}" if subtitle else str(body)
        argv = ["notify-send", str(title), text]
    else:
        return False
    try:
        return (runner or _run)(argv, 10).returncode == 0
    except Exception:  # noqa: BLE001 - a notification must never break a caller
        return False


def open_path(path, runner: Optional[Runner] = None) -> bool:
    """Open ``path`` with the OS default handler / file manager. Never raises.

    darwin: open(1) — a directory reveals in Finder, a ``.command`` file runs
    in Terminal.app (the act.ai_fix flow). linux: xdg-open. windows:
    os.startfile.
    """
    p = str(path)
    if sys.platform.startswith("win"):
        try:
            os.startfile(p)  # noqa: S606 - the whole point of this function
            return True
        except Exception:  # noqa: BLE001
            return False
    argv = ["open", p] if is_darwin() else ["xdg-open", p]
    try:
        return (runner or _run)(argv, 15).returncode == 0
    except Exception:  # noqa: BLE001
        return False


def service_list_text(runner: Optional[Runner] = None) -> str:
    """The user-session service table as raw text. Never raises.

    darwin: ``launchctl list`` (columns: PID / last exit status / label) —
    act.doctor parses it to tell "running" from "loaded but crashing" from
    "not registered". Other OSes return "" (doctor then honestly reports the
    agents as not registered); a port plugs its service manager in here
    (``systemctl --user`` / Task Scheduler, see docs/PORTING.md) and the
    doctor checks start working unchanged.
    """
    if not is_darwin():
        return ""
    try:
        proc = (runner or _run)(["launchctl", "list"], 10)
        return (proc.stdout or "") + (proc.stderr or "")
    except Exception:  # noqa: BLE001
        return ""
