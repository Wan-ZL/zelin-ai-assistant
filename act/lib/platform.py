"""OS seam — the generic OS-specific effects behind one thin choke point.

契约：CONTRACT §5（macOS 通知）/ §25（doctor 的 service 列表来源）/ §28（通知中继
的原生落点 `notify_user`）/ §71.1（机器电源状态探针）；移植清单 docs/PORTING.md。

Exactly four concerns in the python tree are generic enough to port (the
full audit lives in docs/PORTING.md): firing a user notification, opening a
path with the system file handler, listing the user's background services,
and reading this machine's power state (§71.1 —— the fourth concern, added
2026-09-14). The darwin implementations delegate to the exact commands this
codebase always ran (osascript / open / launchctl / pmset+ioreg); linux gets
the cheap honest equivalent where one exists (notify-send, xdg-open) and a
truthful empty result where none does yet; windows uses the OS that is always
present (PowerShell toast, schtasks, os.startfile) with no pip dependency.

NOT here on purpose:
  - anything already portable: claude / git / gh subprocess calls.

Every function is best-effort and NEVER raises — a failed notification or
reveal must not break the daemon loop (the posture act/lib/notify.py always
had). ``runner`` is the injectable subprocess runner used by tests.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from typing import Callable, Dict, List, Optional

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


def _darwin_notify_argv(title: str, body: str, subtitle: Optional[str]) -> List[str]:
    def esc(s: str) -> str:
        return str(s).replace("\\", "\\\\").replace('"', '\\"')

    script = f'display notification "{esc(body)}" with title "{esc(title)}"'
    if subtitle:
        script += f' subtitle "{esc(subtitle)}"'
    return ["osascript", "-e", script]


def _windows_notify_argv(title: str, body: str, subtitle: Optional[str]) -> List[str]:
    # PowerShell single-quoted strings take everything literally; the only
    # metacharacter is the quote itself, escaped by doubling it.
    def psq(s) -> str:
        return str(s).replace("'", "''")

    text = f"{subtitle}\n{body}" if subtitle else str(body)
    script = (_WINDOWS_TOAST_PS
              .replace("@TITLE@", psq(title))
              .replace("@BODY@", psq(text)))
    return ["powershell", "-NoProfile", "-NonInteractive", "-Command", script]


def _linux_notify_argv(title: str, body: str, subtitle: Optional[str]) -> List[str]:
    text = f"{subtitle}\n{body}" if subtitle else str(body)
    return ["notify-send", str(title), text]


def _notify_argv(title: str, body: str, subtitle: Optional[str]) -> Optional[List[str]]:
    """Per-OS notification command; None on an OS without a port."""
    if is_darwin():
        return _darwin_notify_argv(title, body, subtitle)
    if is_windows():
        return _windows_notify_argv(title, body, subtitle)
    if sys.platform.startswith("linux"):
        return _linux_notify_argv(title, body, subtitle)
    return None


def notify_user(title: str, body: str, subtitle: Optional[str] = None,
                runner: Optional[Runner] = None) -> bool:
    """Fire a native user notification. True on success, never raises.

    darwin: osascript ``display notification``. linux: notify-send when
    present (desktop sessions; headless boxes just return False). windows:
    a WinRT toast via PowerShell (no pip dep; _WINDOWS_TOAST_PS). Other OSes:
    False until a port lands (docs/PORTING.md).
    """
    argv = _notify_argv(title, body, subtitle)
    if argv is None:
        return False
    try:
        return (runner or _run)(argv, 10).returncode == 0
    except Exception:  # noqa: BLE001 - a notification must never break a caller
        return False


def _startfile(path: str) -> bool:
    """windows: os.startfile — the OS default handler. Never raises."""
    try:
        os.startfile(path)  # noqa: S606 # nosec B606 - the whole point of this function
        return True
    except Exception:  # noqa: BLE001
        return False


def open_path(path, runner: Optional[Runner] = None) -> bool:
    """Open ``path`` with the OS default handler / file manager. Never raises.

    darwin: open(1) — a directory reveals in Finder, a ``.command`` file runs
    in Terminal.app (the act.ai_fix flow). linux: xdg-open. windows:
    os.startfile.
    """
    p = str(path)
    if sys.platform.startswith("win"):
        return _startfile(p)
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
    argv = _service_list_argv()
    if argv is None:
        return ""
    try:
        return _combined_output((runner or _run)(argv, 10))
    except Exception:  # noqa: BLE001
        return ""


def _combined_output(proc) -> str:
    return (proc.stdout or "") + (proc.stderr or "")


def _service_list_argv() -> Optional[List[str]]:
    """Per-OS user-session service listing command; None on an OS without a port."""
    if is_darwin():
        return ["launchctl", "list"]
    if is_windows():
        return ["schtasks", "/query", "/fo", "LIST", "/v"]
    if sys.platform.startswith("linux"):
        return ["systemctl", "--user", "list-units", "--type=service,timer",
                "--all", "--no-legend", "--no-pager"]
    return None


# --------------------------------------------------------------------------- #
# 第四件事：机器电源状态（§71.1）
# --------------------------------------------------------------------------- #
# 判据只用 **Apple Silicon 上实测活着** 的两条命令（fixture tests/fixtures/power/
# 的真实输出，2026-09-14 采自 owner 同款机器：arm64 / macOS 26.5.2）：
#   pmset -g powerstate IOPMrootDomain  -> "IOPMrootDomain  4  4  ON"
#   ioreg -n IOPMrootDomain -r -d 1     -> "System Capabilities" / "IOPMUserIsActive"
#   pmset -g assertions                 -> 系统级 assertion 计数块
# 被否掉的旧判据（写在这里当 tombstone，别再回去试）：`pmset -g powerstate
# IODisplayWrangler` 在 arm64 上打印 "Internal failure: Failed to get power state
# information" 并 **exit 0**（IODisplayWrangler 没有 IOPowerManagement 字典），
# `ioreg -r -k AppleClamshellState -d 4` 零行——两条都恒等于「探不到」，拿它们
# 做闸等于让整条 §71.1 成为 no-op。
_PMSET_ROW_RE = re.compile(r"^IOPMrootDomain\s+(\d+)\s+(\d+)\b", re.MULTILINE)
_CAPABILITIES_RE = re.compile(r'"System Capabilities"\s*=\s*(\d+)')
_USER_ACTIVE_RE = re.compile(r'"IOPMUserIsActive"\s*=\s*(Yes|No)')
_ASSERTION_ROW_RE = re.compile(r"^\s+([A-Za-z][A-Za-z0-9]*)\s+(\d+)\s*$", re.MULTILINE)
_ASSERTION_HEAD = "Assertion status system-wide:"
_ASSERTION_TAIL = "Listed by owning process:"


def _probe_text(argv: List[str], runner: Optional[Runner], timeout: float = 5) -> str:
    """One power probe's combined output; "" on any surprise. NEVER raises.

    非 darwin 上**不起子进程**（探针无意义）——但注入了 ``runner`` 时照跑：
    判例在 linux CI 上也要走同一段解析（测试注入真机 fixture 文本）。
    """
    if runner is None and not is_darwin():
        return ""
    try:
        return _combined_output((runner or _run)(argv, timeout))
    except Exception:  # noqa: BLE001 - a power probe must never break a caller
        return ""


def power_state(runner: Optional[Runner] = None) -> Optional[tuple]:
    """IOPMrootDomain 的 ``(当前电源档, 最高电源档)``；探不到 → None。

    darwin: ``pmset -g powerstate IOPMrootDomain``（表头一行 + 数据行
    ``IOPMrootDomain 4 4 ON``）。清醒 = 当前档 == 最高档；dark wake / 正在
    睡下去 = 当前档 < 最高档。非 darwin / 命令缺席 / 格式漂移 → None
    （「探不到」不是「睡着了」，§71.1 fail-open）。
    """
    m = _PMSET_ROW_RE.search(_probe_text(
        ["pmset", "-g", "powerstate", "IOPMrootDomain"], runner))
    return (int(m.group(1)), int(m.group(2))) if m else None


def power_capabilities(runner: Optional[Runner] = None) -> Dict[str, object]:
    """IOPMrootDomain 的系统能力位与「用户在用」标记；探不到的键整键不出。

    darwin: ``ioreg -n IOPMrootDomain -r -d 1``。返回 `{"capabilities": int,
    "user_active": bool}`——`capabilities` 是 System Capabilities 位图
    （CPU 0x1 / Graphics 0x2 / Audio 0x4 / Network 0x8，满醒 = 15，dark wake
    没有 Graphics 位），`user_active` = `IOPMUserIsActive`。
    """
    text = _probe_text(["ioreg", "-n", "IOPMrootDomain", "-r", "-d", "1"], runner)
    out: Dict[str, object] = {}
    caps = _CAPABILITIES_RE.search(text)
    if caps:
        out["capabilities"] = int(caps.group(1))
    active = _USER_ACTIVE_RE.search(text)
    if active:
        out["user_active"] = active.group(1) == "Yes"
    return out


def power_assertions(runner: Optional[Runner] = None) -> Dict[str, int]:
    """``pmset -g assertions`` 的系统级 assertion 计数（`{}` = 探不到）。

    只读 "Assertion status system-wide:" 那一块（到 "Listed by owning process:"
    为止）——逐进程明细里有进程名与用户文案，不进任何判据也不落盘。
    §71.1 只用两个键：`UserIsActive` / `PreventUserIdleDisplaySleep`（任一
    非零 = 有人/有东西正把这台机器摁醒着）。
    """
    text = _probe_text(["pmset", "-g", "assertions"], runner)
    head = text.find(_ASSERTION_HEAD)
    if head < 0:
        return {}
    tail = text.find(_ASSERTION_TAIL, head)
    block = text[head:tail] if tail > head else text[head:]
    return {m.group(1): int(m.group(2)) for m in _ASSERTION_ROW_RE.finditer(block)}
