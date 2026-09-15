"""CONTRACT §55 追记：退役的 job 在 systemd / Task Scheduler 里也必须看得见。

doctor 的 Linux/Windows 期望集合是 glob 模板目录得来的（`services.systemd_units`
/ `services.scheduled_tasks`），所以**删一个模板 = 那个 job 从期望集合里消失**——
而它在用户机器上仍 enable / registered 着（`zelin-webui.service` 带
`Restart=always`，`\\ZelinAIAssistant\\webui` 带 LogonTrigger）。这正是 §55 的
案底：v0.21 删掉的 imessageradar agent 又跑了 51 天、没有任何一行报告提过它。

`systemd orphans` / `scheduled task orphans` 两行是 launchd `check_orphans` 的
off-macOS 孪生：此刻在跑 → FAIL，只剩文件 / 只是 registered → WARN（下次登录或
daemon-reload 复活），干净 → OK。只报告，从不自动卸——显式授权名单住在两个装机
脚本里（`RETIRED_UNITS` / `$RetiredLeaves`）。
"""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from act import doctor
from act.lib import config


def _systemctl(rows):
    """`systemctl --user list-units --all` text from {unit: (active, sub)}."""
    return "".join("  %-28s loaded %-9s %-8s Zelin AI Assistant\n"
                   % (unit, active, sub) for unit, (active, sub) in rows.items())


def _schtasks(rows):
    """`schtasks /query /fo LIST /v` text from {task: status}."""
    return "".join(
        "Folder: \\ZelinAIAssistant\n"
        "TaskName: %s\n"
        "Status: %s\n"
        "Scheduled Task State: Enabled\n"
        "\n" % (task, status) for task, status in rows.items())


class _HomeWithTemplates(unittest.TestCase):
    """A throwaway AIASSISTANT_HOME carrying exactly the templates we ship."""

    UNITS = ("zelin-actd.service", "zelin-server.service",
             "zelin-gmail-radar.service", "zelin-gmail-radar.timer")
    TASKS = ("zelin-actd.xml", "zelin-server.xml", "zelin-gmail-radar.xml")

    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        home = Path(tmp.name)
        (home / "act" / "systemd").mkdir(parents=True)
        (home / "act" / "tasksched").mkdir(parents=True)
        for name in self.UNITS:
            (home / "act" / "systemd" / name).write_text("[Unit]\n")
        for name in self.TASKS:
            (home / "act" / "tasksched" / name).write_text("<Task/>\n")
        p = mock.patch.object(config, "HOME", home)
        p.start()
        self.addCleanup(p.stop)


class SystemdOrphansTestCase(_HomeWithTemplates):
    def _probes(self, listing, on_disk=()):
        return doctor.Probes(launchctl_list=lambda: listing,
                             installed_user_units=lambda: list(on_disk))

    def test_only_templated_units_is_ok(self):
        rows = {u: ("active", "running") for u in ("zelin-actd.service",
                                                   "zelin-server.service")}
        rows["zelin-gmail-radar.service"] = ("inactive", "dead")
        r = doctor._check_systemd_orphans(
            self._probes(_systemctl(rows), on_disk=self.UNITS))
        self.assertEqual(r.status, doctor.OK)
        self.assertEqual(r.name, "systemd orphans")

    def test_a_retired_unit_still_running_fails(self):
        rows = {"zelin-actd.service": ("active", "running"),
                "zelin-webui.service": ("active", "running")}
        r = doctor._check_systemd_orphans(self._probes(_systemctl(rows)))
        self.assertEqual(r.status, doctor.FAIL)
        self.assertIn("zelin-webui.service", r.detail)
        self.assertIn("systemctl --user disable --now zelin-webui.service", r.fix)

    def test_a_crash_looping_retired_unit_fails(self):
        rows = {"zelin-webui.service": ("failed", "failed")}
        self.assertEqual(
            doctor._check_systemd_orphans(self._probes(_systemctl(rows))).status,
            doctor.FAIL)

    def test_a_retired_unit_left_only_on_disk_warns(self):
        r = doctor._check_systemd_orphans(
            self._probes("", on_disk=("zelin-webui.service",)))
        self.assertEqual(r.status, doctor.WARN)
        self.assertIn("zelin-webui.service", r.detail)

    def test_a_loaded_but_dead_retired_unit_warns(self):
        rows = {"zelin-webui.service": ("inactive", "dead")}
        self.assertEqual(
            doctor._check_systemd_orphans(self._probes(_systemctl(rows))).status,
            doctor.WARN)

    def test_a_foreign_unit_is_never_ours_to_report(self):
        rows = {"docker.service": ("active", "running"),
                "zelin-actd.service": ("active", "running")}
        self.assertEqual(
            doctor._check_systemd_orphans(self._probes(_systemctl(rows))).status,
            doctor.OK)

    def test_the_row_rides_the_linux_check_list(self):
        with mock.patch("sys.platform", "linux"):
            names = [f.__name__ for f in doctor._checks_for_platform()]
        self.assertIn("_check_systemd_orphans", names)
        self.assertNotIn("_check_task_orphans", names)


class ScheduledTaskOrphansTestCase(_HomeWithTemplates):
    def _probes(self, listing):
        return doctor.Probes(launchctl_list=lambda: listing)

    def test_only_templated_tasks_is_ok(self):
        rows = {"\\ZelinAIAssistant\\actd": "Running",
                "\\ZelinAIAssistant\\server": "Running",
                "\\ZelinAIAssistant\\gmail-radar": "Ready",
                "\\Microsoft\\Windows\\UpdateOrchestrator\\Scan": "Ready"}
        r = doctor._check_task_orphans(self._probes(_schtasks(rows)))
        self.assertEqual(r.status, doctor.OK)
        self.assertEqual(r.name, "scheduled task orphans")

    def test_a_retired_task_still_running_fails(self):
        rows = {"\\ZelinAIAssistant\\actd": "Running",
                "\\ZelinAIAssistant\\webui": "Running"}
        r = doctor._check_task_orphans(self._probes(_schtasks(rows)))
        self.assertEqual(r.status, doctor.FAIL)
        self.assertIn("\\ZelinAIAssistant\\webui", r.detail)
        self.assertIn("Unregister-ScheduledTask", r.fix)
        self.assertIn("-TaskName webui", r.fix)

    def test_a_retired_task_merely_registered_warns(self):
        # Ready 也不是无害的：LogonTrigger 下次登录就把它拉起来
        rows = {"\\ZelinAIAssistant\\webui": "Ready"}
        r = doctor._check_task_orphans(self._probes(_schtasks(rows)))
        self.assertEqual(r.status, doctor.WARN)
        self.assertIn("next logon", r.detail)

    def test_a_foreign_task_is_never_ours_to_report(self):
        rows = {"\\Microsoft\\Windows\\UpdateOrchestrator\\Scan": "Running"}
        self.assertEqual(
            doctor._check_task_orphans(self._probes(_schtasks(rows))).status,
            doctor.OK)

    def test_the_row_rides_the_windows_check_list(self):
        with mock.patch("sys.platform", "win32"):
            names = [f.__name__ for f in doctor._checks_for_platform()]
        self.assertIn("_check_task_orphans", names)
        self.assertNotIn("_check_systemd_orphans", names)


if __name__ == "__main__":
    unittest.main()
