"""doctor 探针家族的共享件（CONTRACT §25；入口 act/doctor.py）。

住这里的东西被两个以上家族用到：状态常量、``CheckResult``、唯一的 launchd label
（与 act/launchd 模板逐字一致，tests/test_doctor.py 钉住漂移；§78 封闭清单）、永不
raise 的子进程 runner、§15 双语 ``pick``、``launchctl list`` 表解析、config/runtime.json
的解释器 pin。v2.0.0 起只有 macOS（Linux / Windows 的 systemd / Task Scheduler 镜像随
Act 管线退役，全文见 tag full）。
``probes`` 参数一律是 ``act.doctor.Probes``（注入缝）——本层只调用它的字段，
不 import 它（lib 永不向上，§58.3）。
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from act.lib import board_server, config, failures, platform

OK = "ok"
WARN = "warn"
FAIL = "fail"

# §54 看板 server（`python3 -m server`）——由 launchd 托管而非壳 app 的子进程（GUI app
# 是子进程的 TCC responsible process，壳没有磁盘授权）。探针与判决的纯逻辑住
# act/lib/board_server.py。§78：它是封闭常驻清单里**唯一**的 LaunchAgent。
SERVER_LABEL = board_server.LABEL
# 常驻 agent（模板 KeepAlive=true）：「已加载、无 pid、上次退出码非 0」= crash-loop → FAIL
# （§55）。集合与 act/launchd/*.plist 的 KeepAlive 键逐字一致，tests/test_doctor.py 钉住漂移。
RESIDENT_LABELS = frozenset({SERVER_LABEL})
LABEL_PREFIX = "com.zelin.aiassistant."

PROBE_TIMEOUT = 90  # ceiling for the live claude call


def installer() -> str:
    """The installer to point fixes at (macOS only since v2.0.0)."""
    return "install.sh"


def pick(zh: str, en: str) -> str:
    """§15 single language switch (act/lib/failures.pick) for the doctor's
    user-facing detail/fix prose (v0.42, audit #16). Classified rows already
    speak the UI language via the Swift FailureCatalog; this covers the
    unclassified ones. Commands, paths and technical tokens stay English
    inside BOTH variants — they are commands, not prose."""
    return failures.pick(zh, en)


# --------------------------------------------------------------------------- #
# Default probe implementations shared by several families
# --------------------------------------------------------------------------- #
def run(cmd: List[str], env: Optional[dict] = None,
        timeout: Optional[float] = PROBE_TIMEOUT,
        cwd: Optional[str] = None) -> Tuple[int, str]:
    """(exit code, combined stdout+stderr). Never raises: 124 timeout, 127 spawn error.
    ``cwd`` (add-only, §78): the live claude probes run in ``config.headless_cwd()``."""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout, env=env, cwd=cwd,
                              stdin=subprocess.DEVNULL)
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, "timed out after %ss" % timeout
    except OSError as exc:
        return 127, str(exc)


def launchctl_list() -> str:
    # via the OS seam: "" off-macOS (the agents then honestly read unregistered)
    return platform.service_list_text()


def crontab() -> str:
    rc, out = run(["crontab", "-l"], timeout=10)
    return out if rc == 0 else ""


def installed_plist_text(label: str) -> Optional[str]:
    """已安装（非模板）plist 的原文；None = 该 agent 没装。§55 迁移探测用。"""
    p = Path.home() / "Library" / "LaunchAgents" / (label + ".plist")
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return None


# --------------------------------------------------------------------------- #
# CheckResult
# --------------------------------------------------------------------------- #
@dataclass
class CheckResult:
    name: str
    status: str  # OK | WARN | FAIL
    detail: str  # the symptom, one line
    fix: str = ""  # one-line fix (empty for OK)
    # §25 classification (act/lib/failures.py) — empty when unclassified; the
    # app maps action_id to a one-click repair, falling back to the raw fix.
    failure_id: str = ""
    action_id: str = ""
    # §25 row class, derived from failure_id: "owner_action" when the remedy is
    # the owner's manual grant (a TCC toggle) — no code change can clear it.
    row_class: str = ""

    def with_failure(self, failure_id: str) -> "CheckResult":
        """Attach a catalog id (and its action + class) to a non-ok result."""
        self.failure_id = failure_id
        self.action_id = failures.action_id(failure_id) or ""
        self.row_class = failures.row_class(failure_id)
        return self


def row_from(row: dict, name: str) -> CheckResult:
    """A lib module's plain-data row ``{status, detail, fix, failure_id}`` → CheckResult."""
    res = CheckResult(name, row["status"], row["detail"], row.get("fix", ""))
    if row.get("failure_id"):
        res.with_failure(row["failure_id"])
    return res


# --------------------------------------------------------------------------- #
# Shared readers over the probes
# --------------------------------------------------------------------------- #
def launchctl_table(probes) -> dict:
    """label → (pid, last exit status) from `launchctl list`; {} when it fails."""
    table = {}
    try:
        for line in probes.launchctl_list().splitlines():
            parts = line.split()
            if len(parts) >= 3:
                table[parts[2]] = (parts[0], parts[1])
    except Exception:  # noqa: BLE001 - 探针不许崩（宪法第 11 条）
        pass
    return table


def templated_labels(probes) -> List[str]:
    """The launchd labels doctor should expect: ``Probes.launchd_labels`` when
    injected, else derived from the act/launchd/*.plist template basenames."""
    labels = probes.launchd_labels
    if labels is None:
        labels = sorted(p.stem for p in (config.HOME / "act" / "launchd").glob("*.plist"))
    return labels


def pinned_interpreter() -> str:
    """config/runtime.json 里 pin 的解释器；"" = 没 pin / 读不了。"""
    try:
        rj = config.HOME / "config" / "runtime.json"
        return str(json.loads(rj.read_text(encoding="utf-8")).get("python") or "")
    except Exception:  # noqa: BLE001 - 探针不许崩（宪法第 11 条）
        return ""
