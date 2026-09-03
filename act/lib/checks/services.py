"""doctor 探针家族：launchd 之外的服务管理器镜像（CONTRACT §25；docs/LINUX.md
systemd --user；docs/WINDOWS.md Task Scheduler）。

行：每个 unit / task 一行（short name）——actd 是常驻守护（缺席 / 失败 FAIL），
雷达与 digest 由 timer / repetition 驱动，只 WARN。文本来源 = OS seam
``platform.service_list_text()``（``systemctl --user list-units`` /
``schtasks /query /fo LIST /v``），经 ``Probes.launchctl_list`` 注入。
"""
from __future__ import annotations

from typing import List

from act.lib import config, taskscheduler
from act.lib.checks.core import (ACTD_TASK, ACTD_UNIT, FAIL, OK,
                                 SYSTEMD_RESIDENT, WARN, CheckResult, pick)


# --------------------------------------------------------------------------- #
# systemd --user (Linux)
# --------------------------------------------------------------------------- #
def systemd_units() -> List[str]:
    """Expected checkable units: resident services + every timer template."""
    d = config.HOME / "act" / "systemd"
    residents = [u for u in SYSTEMD_RESIDENT if (d / u).exists()]
    timers = sorted(p.name for p in d.glob("*.timer"))
    return residents + timers


def _systemd_table(text: str) -> dict:
    """unit → (ACTIVE, SUB) from ``systemctl --user list-units``; a failed-unit
    bullet (●) is stripped before splitting."""
    table = {}
    for line in text.splitlines():
        parts = line.replace("●", " ").split()
        if len(parts) >= 4 and (parts[0].endswith(".service")
                                or parts[0].endswith(".timer")):
            table[parts[0]] = (parts[2], parts[3])
    return table


def _unit_row(unit: str, table: dict) -> CheckResult:
    short = unit.rsplit(".", 1)[0].replace("zelin-", "")
    is_actd = unit == ACTD_UNIT
    severity = FAIL if is_actd else WARN
    if unit not in table:
        return CheckResult(
            short, severity,
            "%s not registered with systemd --user%s" % (
                unit, " - cards never move" if is_actd else ""),
            "bash install-linux.sh (renders + enables the user units)",
        ).with_failure("agent_unloaded")
    active, sub = table[unit]
    if active == "active":
        return CheckResult(short, OK, "active (%s)" % sub)
    if active == "failed":
        return CheckResult(
            short, severity,
            "%s failed to start" % unit,
            "journalctl --user -u %s -n 20  # usual causes: PyYAML missing "
            "for the daemon python, missing API key" % unit,
        ).with_failure("agent_unloaded")
    # inactive / dead — enabled unit that is not up
    return CheckResult(
        short, severity,
        "%s is %s (not running)" % (unit, active),
        "systemctl --user enable --now %s" % unit,
    ).with_failure("agent_unloaded")


def check_systemd(probes):
    """Linux service check — the systemd --user mirror of launchd.check_agents.

    Parses ``systemctl --user list-units`` (UNIT / LOAD / ACTIVE / SUB) that
    the OS seam returns off-macOS. actd is the resident daemon (FAIL if not
    active); the radar/digest work is timer-driven, so the *.timer being
    active is what we check (the oneshot .service is correctly inactive between
    fires). A failed-unit bullet (●) is stripped before splitting.
    """
    units = probes.systemd_units
    if units is None:
        units = systemd_units()
    if not units:
        return CheckResult(
            "systemd units", WARN,
            pick("act/systemd 下没有 unit 模板——checkout 不完整？",
                 "no unit templates under act/systemd - incomplete checkout?"),
            "git -C '%s' checkout act/systemd" % config.HOME)
    table = _systemd_table(probes.launchctl_list())
    return [_unit_row(unit, table) for unit in units]


# --------------------------------------------------------------------------- #
# Task Scheduler (Windows)
# --------------------------------------------------------------------------- #
def scheduled_tasks() -> List[str]:
    """Expected checkable Windows tasks — full ``\\ZelinAIAssistant\\<leaf>``
    names derived from the act/tasksched/*.xml templates."""
    d = config.HOME / "act" / "tasksched"
    return [taskscheduler.full_task_name(p.name) for p in sorted(d.glob("*.xml"))]


def parse_schtasks(text: str) -> dict:
    """Parse ``schtasks /query /fo LIST /v`` into {TaskName: {field: value}}.

    LIST output is one "Field: Value" block per task (verbose can emit a block
    per trigger; same Status each, so last-wins is correct). Only the first ":"
    splits key from value so clock values ("9:00:00 AM") survive intact.
    """
    table: dict = {}
    cur: dict = {}

    def flush() -> None:
        name = cur.get("TaskName")
        if name:
            table[name] = dict(cur)

    for raw in text.splitlines():
        if not raw.strip():
            flush()
            cur = {}
            continue
        key, sep, val = raw.partition(":")
        if sep:
            cur[key.strip()] = val.strip()
    flush()
    return table


def _task_status_row(short: str, full: str, severity: str, info: dict) -> CheckResult:
    status = info.get("Status", "")
    state = info.get("Scheduled Task State", "")
    if state == "Disabled" or status == "Disabled":
        return CheckResult(
            short, severity,
            "%s is disabled (not running)" % full,
            "schtasks /Change /TN \"%s\" /ENABLE" % full,
        ).with_failure("agent_unloaded")
    if status == "Running":
        return CheckResult(short, OK, "running")
    if status == "Ready":
        return CheckResult(short, OK, "registered (ready)")
    return CheckResult(
        short, severity,
        "%s status is %r (not ready/running)" % (full, status or "unknown"),
        "schtasks /Query /TN \"%s\" /V /FO LIST  # inspect; then re-run install.ps1" % full,
    ).with_failure("agent_unloaded")


def _task_row(full: str, table: dict) -> CheckResult:
    short = full.rsplit("\\", 1)[-1]
    is_actd = full == ACTD_TASK
    severity = FAIL if is_actd else WARN
    info = table.get(full)
    if info is None:
        return CheckResult(
            short, severity,
            "%s not registered with Task Scheduler%s" % (
                full, " - cards never move" if is_actd else ""),
            "powershell -ExecutionPolicy Bypass -File install.ps1 "
            "(renders + registers the tasks)",
        ).with_failure("agent_unloaded")
    return _task_status_row(short, full, severity, info)


def check_scheduled_tasks(probes):
    """Windows service check — the Task Scheduler mirror of launchd.check_agents /
    check_systemd.

    Parses ``schtasks /query /fo LIST /v`` (what the OS seam returns on Windows)
    filtered to our ``\\ZelinAIAssistant\\`` tasks. actd is the resident daemon
    (FAIL if missing/disabled); the radar/digest tasks are repetition-driven and
    only WARN. NOTE (docs/WINDOWS.md): schtasks reports Ready vs Running vs
    Disabled — it does NOT expose "registered but crash-looping" the way systemd
    does, so a healthy-looking "Ready"/"Running" still needs a real box to prove
    the daemon actually dispatches.
    """
    tasks = probes.scheduled_tasks
    if tasks is None:
        tasks = scheduled_tasks()
    if not tasks:
        return CheckResult(
            "scheduled tasks", WARN,
            pick("act/tasksched 下没有任务模板——checkout 不完整？",
                 "no task templates under act/tasksched - incomplete checkout?"),
            "git -C '%s' checkout act/tasksched" % config.HOME)
    table = parse_schtasks(probes.launchctl_list())
    return [_task_row(full, table) for full in tasks]
