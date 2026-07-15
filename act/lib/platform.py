"""OS seam — the generic OS-specific effects behind one thin choke point.

Exactly three concerns in the python tree are generic enough to port (the
full audit lives in docs/PORTING.md): firing a user notification, opening a
path with the system file handler, and listing the user's background
services. The darwin implementations delegate to the exact commands this
codebase always ran (osascript / open / launchctl); linux gets the cheap
honest equivalent where one exists (notify-send, xdg-open) and a truthful
empty result where none does yet; windows uses the OS that is always present
(PowerShell toast, schtasks, os.startfile) with no pip dependency.

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

# Best-effort native Windows toast with NO pip dependency: drive the built-in
# WinRT ToastNotificationManager through PowerShell (the OS ships it). Attributed
# to PowerShell's registered AppUserModelID so it shows without our own app
# being installed/registered — a BurntToast-free path. If WinRT is unavailable
# (older Windows / Server Core), the script throws, the runner returns nonzero,
# and notify_user honestly returns False (Slack self-DM + the web dashboard
# badge still cover the user). @TITLE@/@BODY@ are filled by string replacement,
# each already escaped for a PowerShell single-quoted string (doubled quotes).
_WINDOWS_TOAST_PS = (
    "$ErrorActionPreference='Stop';"
    "[Windows.UI.Notifications.ToastNotificationManager,Windows.UI.Notifications,"
    "ContentType=WindowsRuntime]|Out-Null;"
    "$AppId='{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe';"
    "$tpl=[Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent("
    "[Windows.UI.Notifications.ToastTemplateType]::ToastText02);"
    "$n=$tpl.GetElementsByTagName('text');"
    "$n.Item(0).AppendChild($tpl.CreateTextNode('@TITLE@'))|Out-Null;"
    "$n.Item(1).AppendChild($tpl.CreateTextNode('@BODY@'))|Out-Null;"
    "$toast=[Windows.UI.Notifications.ToastNotification]::new($tpl);"
    "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($AppId).Show($toast)"
)


def is_darwin() -> bool:
    return sys.platform == "darwin"


def is_windows() -> bool:
    return sys.platform.startswith("win")


def _run(argv: List[str], timeout: float) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)


def notify_user(title: str, body: str, subtitle: Optional[str] = None,
                runner: Optional[Runner] = None) -> bool:
    """Fire a native user notification. True on success, never raises.

    darwin: osascript ``display notification``. linux: notify-send when
    present (desktop sessions; headless boxes just return False). windows:
    a WinRT toast via PowerShell (no pip dep; _WINDOWS_TOAST_PS). Other OSes:
    False until a port lands (docs/PORTING.md).
    """
    if is_darwin():
        def esc(s: str) -> str:
            return str(s).replace("\\", "\\\\").replace('"', '\\"')

        script = f'display notification "{esc(body)}" with title "{esc(title)}"'
        if subtitle:
            script += f' subtitle "{esc(subtitle)}"'
        argv = ["osascript", "-e", script]
    elif is_windows():
        # PowerShell single-quoted strings take everything literally; the only
        # metacharacter is the quote itself, escaped by doubling it.
        def psq(s) -> str:
            return str(s).replace("'", "''")

        text = f"{subtitle}\n{body}" if subtitle else str(body)
        script = (_WINDOWS_TOAST_PS
                  .replace("@TITLE@", psq(title))
                  .replace("@BODY@", psq(text)))
        argv = ["powershell", "-NoProfile", "-NonInteractive", "-Command", script]
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
            os.startfile(p)  # noqa: S606 # nosec B606 - the whole point of this function
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

    darwin: ``launchctl list`` (columns: PID / last exit status / label).
    linux: ``systemctl --user list-units --type=service,timer`` (columns:
    UNIT / LOAD / ACTIVE / SUB / DESCRIPTION). windows: ``schtasks /query /fo
    LIST /v`` — one verbose block per task (TaskName / Status / Scheduled Task
    State / ...). act.doctor parses whichever format the current OS produces to
    tell "running" from "loaded but crashing/failed" from "not registered", and
    (as on macOS/linux, where launchctl/systemctl list every label/unit) filters
    the full listing down to OUR tasks by their ``\\ZelinAIAssistant\\`` prefix.
    ``--all`` keeps cleanly-stopped units visible (so doctor can tell "inactive"
    from "never installed") and ``--no-legend``/``--no-pager`` keep the output
    to just the unit rows. Other OSes return "" (doctor then honestly reports
    the agents as not registered); see docs/PORTING.md.
    """
    if is_darwin():
        argv = ["launchctl", "list"]
    elif is_windows():
        argv = ["schtasks", "/query", "/fo", "LIST", "/v"]
    elif sys.platform.startswith("linux"):
        argv = ["systemctl", "--user", "list-units", "--type=service,timer",
                "--all", "--no-legend", "--no-pager"]
    else:
        return ""
    try:
        proc = (runner or _run)(argv, 10)
        return (proc.stdout or "") + (proc.stderr or "")
    except Exception:  # noqa: BLE001
        return ""
