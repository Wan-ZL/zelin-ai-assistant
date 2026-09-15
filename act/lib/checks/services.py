"""doctor 探针家族：launchd 之外的服务管理器镜像（CONTRACT §25；§49 看板 server
是三平台唯一的 UI；§55 退役自证与孤儿可见；docs/LINUX.md systemd --user；
docs/WINDOWS.md Task Scheduler）。

行：每个 unit / task 一行（short name）——actd 是常驻守护（缺席 / 失败 FAIL），
雷达与 digest 由 timer / repetition 驱动，只 WARN。文本来源 = OS seam
``platform.service_list_text()``（``systemctl --user list-units`` /
``schtasks /query /fo LIST /v``），经 ``Probes.launchctl_list`` 注入。

期望集合都是从模板目录 glob 出来的，所以删一个模板 = 那个 job 从期望集合里
消失——但它在用户机器上仍 enable / registered 着。`systemd orphans` /
`scheduled task orphans` 两行是 §55 的 off-macOS 孪生，专门把这种「已退役却还
在跑」重新变成看得见的。
"""
from __future__ import annotations

import os
from pathlib import Path
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


# --------------------------------------------------------------------------- #
# §55 退役自证的 off-macOS 孪生（launchd.check_orphans 的两个镜像）
# --------------------------------------------------------------------------- #
UNIT_PREFIX = "zelin-"
# systemd ACTIVE 值里「此刻在耗资源」的那几个（failed = 正在崩循环）
_LIVE_ACTIVE = ("active", "activating", "reloading", "failed")


def user_unit_dir() -> Path:
    """``~/.config/systemd/user`` —— install-linux.sh 渲染 unit 的地方（同一个
    ``XDG_CONFIG_HOME`` 表达式，两处不许分叉）。"""
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "systemd" / "user"


def installed_user_units() -> List[str]:
    """文件面：user unit 目录里带 ``zelin-`` 前缀的 unit 文件名（读不到 = 空）。"""
    try:
        return sorted(p.name for p in user_unit_dir().glob(UNIT_PREFIX + "*"))
    except OSError:  # noqa: BLE001 - 探针不许崩
        return []


def _templated_units() -> set:
    try:
        return {p.name for p in (config.HOME / "act" / "systemd").iterdir()}
    except OSError:  # noqa: BLE001 - 探针不许崩
        return set()


def _orphan_units(table: dict, known: set) -> tuple:
    """(此刻活着的, 已载入但 dead 的) —— 两组都是「模板没了却还在」的 unit。"""
    live: List[str] = []
    idle: List[str] = []
    for unit in sorted(table):
        if unit.startswith(UNIT_PREFIX) and unit not in known:
            (live if table[unit][0] in _LIVE_ACTIVE else idle).append(unit)
    return live, idle


def _unit_retire_fix(units: List[str]) -> str:
    unit_dir = user_unit_dir()
    return "bash install-linux.sh  # retires them; or by hand: " + "; ".join(
        "systemctl --user disable --now %s && rm -f %s" % (u, unit_dir / u)
        for u in units)


def check_systemd_orphans(probes):
    """§55 孤儿行的 Linux 孪生：带 ``zelin-`` 前缀、act/systemd 里已无模板的 unit。

    ``systemd_units()`` 的期望集合是 glob 模板目录得来的，所以删一个模板会让那个
    unit 从期望集合里**消失**——而它在用户机器上仍 `enable`d、仍 `Restart=always`
    地跑（2026-08-31 审计：v0.21 删掉的 imessageradar agent 又跑了 51 天没人看
    见）。两个面都扫：``systemctl --user list-units --all`` 里此刻活着的 → FAIL，
    只剩 unit 文件 / 已载入但 dead 的 → WARN（daemon-reload 或下次登录复活）。
    """
    known = _templated_units()
    live, idle = _orphan_units(_systemd_table(probes.launchctl_list()), known)
    seen = set(live) | set(idle)
    on_disk = [u for u in probes.installed_user_units()
               if u not in known and u not in seen]
    if live:
        return CheckResult(
            "systemd orphans", FAIL,
            "retired unit(s) still running under systemd --user (no template in "
            "act/systemd any more): %s - each keeps serving its own board on its "
            "own port with its own token, and logging, forever" % ", ".join(live),
            _unit_retire_fix(live))
    if idle or on_disk:
        return CheckResult(
            "systemd orphans", WARN,
            "retired unit(s) still known to systemd --user or left in %s (not "
            "running now, but a daemon-reload or the next login brings them back): "
            "%s" % (user_unit_dir(), ", ".join(idle + on_disk)),
            _unit_retire_fix(idle + on_disk))
    return CheckResult("systemd orphans", OK,
                       "no retired unit left enabled or in %s" % user_unit_dir())


def _templated_tasks() -> set:
    try:
        return {taskscheduler.full_task_name(p.name)
                for p in (config.HOME / "act" / "tasksched").glob("*.xml")}
    except OSError:  # noqa: BLE001 - 探针不许崩
        return set()


def _orphan_tasks(table: dict, known: set) -> tuple:
    """(此刻 Running 的, 仅 registered 的) —— 两组都是「模板没了却还在」的任务。"""
    running: List[str] = []
    registered: List[str] = []
    for full in sorted(table):
        if full.startswith(taskscheduler.TASK_PATH_PREFIX) and full not in known:
            (running if table[full].get("Status") == "Running"
             else registered).append(full)
    return running, registered


def _task_retire_fix(tasks: List[str]) -> str:
    return ("powershell -ExecutionPolicy Bypass -File install.ps1  # unregisters "
            "them; or by hand: " + "; ".join(
                "Unregister-ScheduledTask -TaskPath '%s' -TaskName %s -Confirm:$false"
                % (taskscheduler.TASK_PATH_PREFIX, t.rsplit("\\", 1)[-1])
                for t in tasks))


def check_task_orphans(probes):
    """§55 孤儿行的 Windows 孪生：``\\ZelinAIAssistant\\`` 下、act/tasksched 里已无
    模板的任务。

    与 Linux 同理（期望集合 glob 模板目录 → 删模板等于结构性失明）。schtasks 的
    ``Status`` 是 Running 的 → FAIL（此刻在跑）；Ready / Disabled 的仍带
    LogonTrigger，下次登录就回来 → WARN。只**报告**，从不自动注销：显式授权名单
    住在 install.ps1 的 ``$RetiredLeaves`` 里。
    """
    known = _templated_tasks()
    running, registered = _orphan_tasks(parse_schtasks(probes.launchctl_list()),
                                        known)
    if running:
        return CheckResult(
            "scheduled task orphans", FAIL,
            "retired task(s) RUNNING under Task Scheduler (no template in "
            "act/tasksched any more): %s - each keeps serving its own board on its "
            "own port with its own token, and logging, forever" % ", ".join(running),
            _task_retire_fix(running))
    if registered:
        return CheckResult(
            "scheduled task orphans", WARN,
            "retired task(s) still registered under %s (not running right now, but "
            "their LogonTrigger starts them again at the next logon): %s"
            % (taskscheduler.TASK_PATH_PREFIX, ", ".join(registered)),
            _task_retire_fix(registered))
    return CheckResult("scheduled task orphans", OK,
                       "no retired task left under %s"
                       % taskscheduler.TASK_PATH_PREFIX)
