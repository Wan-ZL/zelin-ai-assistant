"""doctor 默认探针实现的判例（CONTRACT §25；探针家族 act/lib/checks/）。

test_doctor.py 一律注入假 Probes，所以 ``Probes`` 的缺省实现——子进程 runner、
crontab 读取、已装 plist / agent 日志 / LaunchAgents 目录的文件面、daemon
PATH 读取、登录 shell 的 claude 解析、pid 探活、config/runtime.json 的解释器
pin——此前在判卷环境里零覆盖（P3a 审计：`_login_shell_claude` 20.7、
`_installed_actd_path_env` 24.5、`_pid_alive` 10）。这里全部 hermetic：
subprocess / os.kill 打桩，文件面落在临时 HOME。
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401

from act.lib import config, platform
from act.lib.checks import core, environment, launchd, pipeline, services


class RunnerTestCase(unittest.TestCase):
    def test_success_concatenates_streams(self):
        done = subprocess.CompletedProcess(["x"], 3, stdout="out", stderr="err")
        with mock.patch.object(subprocess, "run", return_value=done) as run:
            self.assertEqual(core.run(["x"], timeout=5), (3, "outerr"))
        self.assertEqual(run.call_args.kwargs["stdin"], subprocess.DEVNULL)

    def test_none_streams_read_as_empty(self):
        done = subprocess.CompletedProcess(["x"], 0, stdout=None, stderr=None)
        with mock.patch.object(subprocess, "run", return_value=done):
            self.assertEqual(core.run(["x"]), (0, ""))

    def test_timeout_is_124(self):
        with mock.patch.object(subprocess, "run",
                               side_effect=subprocess.TimeoutExpired(["x"], 7)):
            rc, out = core.run(["x"], timeout=7)
        self.assertEqual(rc, 124)
        self.assertIn("7s", out)

    def test_spawn_error_is_127(self):
        with mock.patch.object(subprocess, "run", side_effect=OSError("no such file")):
            self.assertEqual(core.run(["nope"]), (127, "no such file"))

    def test_crontab_empty_on_failure(self):
        with mock.patch.object(core, "run", return_value=(1, "no crontab")):
            self.assertEqual(core.crontab(), "")
        with mock.patch.object(core, "run", return_value=(0, "* * * * * x\n")):
            self.assertEqual(core.crontab(), "* * * * * x\n")

    def test_launchctl_list_goes_through_the_os_seam(self):
        with mock.patch.object(platform, "service_list_text", return_value="a\tb\tc"):
            self.assertEqual(core.launchctl_list(), "a\tb\tc")


class _FakeHome(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="zai-doctor-home-"))
        p = mock.patch.object(Path, "home", return_value=self.home)
        p.start()
        self.addCleanup(p.stop)


class PlistFileFaceTestCase(_FakeHome):
    def _plist(self, label: str, text: str) -> None:
        d = self.home / "Library" / "LaunchAgents"
        d.mkdir(parents=True, exist_ok=True)
        (d / (label + ".plist")).write_text(text, encoding="utf-8")

    def test_installed_plist_text_and_labels(self):
        self.assertIsNone(core.installed_plist_text(core.ACTD_LABEL))
        self.assertEqual(launchd.installed_agent_labels(), [])
        self._plist(core.ACTD_LABEL, "<plist/>")
        self._plist("com.other.thing", "<plist/>")
        self.assertEqual(core.installed_plist_text(core.ACTD_LABEL), "<plist/>")
        self.assertEqual(launchd.installed_agent_labels(), [core.ACTD_LABEL])

    def test_daemon_path_env_darwin(self):
        with mock.patch("sys.platform", "darwin"):
            self.assertIsNone(environment.installed_actd_path_env())
            self._plist(core.ACTD_LABEL, "<key>PATH</key>\n<string>/a:/b</string>")
            self.assertEqual(environment.installed_actd_path_env(), "/a:/b")
            self._plist(core.ACTD_LABEL, "<key>Other</key><string>x</string>")
            self.assertIsNone(environment.installed_actd_path_env())

    def test_daemon_path_env_linux_last_wins(self):
        with mock.patch("sys.platform", "linux"):
            self.assertIsNone(environment.installed_actd_path_env())
            unit = self.home / ".config" / "systemd" / "user" / core.ACTD_UNIT
            unit.parent.mkdir(parents=True)
            unit.write_text("[Service]\nEnvironment=PATH=/first\n  Environment=PATH=/second \n",
                            encoding="utf-8")
            self.assertEqual(environment.installed_actd_path_env(), "/second")
            unit.write_text("[Service]\nExecStart=x\n", encoding="utf-8")
            self.assertIsNone(environment.installed_actd_path_env())

    def test_daemon_path_env_windows_is_none(self):
        with mock.patch("sys.platform", "win32"):
            self.assertIsNone(environment.installed_actd_path_env())


class LaunchdLogFaceTestCase(_FakeHome):
    def test_new_location_wins_then_legacy_then_empty(self):
        short = "actd"
        self.assertEqual(launchd.launchd_log_tail(short), "")
        self.assertIsNone(launchd.launchd_log_mtime(short))
        legacy = config.HOME / "state" / ("%s.launchd.log" % short)
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_text("legacy tail", encoding="utf-8")
        self.addCleanup(lambda: legacy.unlink(missing_ok=True))
        self.assertEqual(launchd.launchd_log_tail(short), "legacy tail")
        self.assertIsNotNone(launchd.launchd_log_mtime(short))
        new = self.home / "Library" / "Logs" / "zelin-ai-assistant" / ("%s.launchd.log" % short)
        new.parent.mkdir(parents=True)
        new.write_text("x" * 5000, encoding="utf-8")
        self.assertEqual(len(launchd.launchd_log_tail(short)), 4000)
        self.assertEqual(launchd.launchd_log_paths(short)[0], new)


class LoginShellClaudeTestCase(unittest.TestCase):
    def test_last_absolute_line_wins(self):
        runner = mock.Mock(return_value=(0, "some banner\n/Users/u/.local/bin/claude\n"))
        with mock.patch.dict(os.environ, {"SHELL": "/bin/bash"}):
            self.assertEqual(environment.login_shell_claude(runner), "/Users/u/.local/bin/claude")
        self.assertEqual(runner.call_args.args[0][0], "/bin/bash")

    def test_failures_read_as_none(self):
        self.assertIsNone(environment.login_shell_claude(mock.Mock(return_value=(1, "/x"))))
        self.assertIsNone(environment.login_shell_claude(mock.Mock(return_value=(0, "  "))))
        self.assertIsNone(environment.login_shell_claude(mock.Mock(return_value=(0, "claude: alias"))))

    def test_default_shell_when_env_unset(self):
        runner = mock.Mock(return_value=(0, "/bin/claude"))
        with mock.patch.dict(os.environ, {}, clear=True):
            environment.login_shell_claude(runner)
        self.assertEqual(runner.call_args.args[0][0], "/bin/zsh")


class PidAliveTestCase(unittest.TestCase):
    def test_own_pid_is_alive(self):
        self.assertTrue(pipeline.pid_alive(os.getpid()))

    def test_kill_outcomes(self):
        with mock.patch.object(os, "kill", side_effect=ProcessLookupError()):
            self.assertFalse(pipeline.pid_alive(4242))
        with mock.patch.object(os, "kill", side_effect=PermissionError()):
            self.assertTrue(pipeline.pid_alive(1))
        with mock.patch.object(os, "kill", side_effect=OSError("odd")):
            self.assertIsNone(pipeline.pid_alive(1))
        self.assertIsNone(pipeline.pid_alive("not-a-pid"))

    def test_windows_never_probes(self):
        with mock.patch("sys.platform", "win32"), \
                mock.patch.object(os, "kill", side_effect=AssertionError("must not kill")):
            self.assertIsNone(pipeline.pid_alive(os.getpid()))

    def test_restart_command_per_os(self):
        with mock.patch("sys.platform", "darwin"):
            self.assertIn("kickstart", pipeline.actd_restart_cmd())
        with mock.patch("sys.platform", "win32"):
            self.assertIn("schtasks", pipeline.actd_restart_cmd())
        with mock.patch("sys.platform", "linux"):
            self.assertIn("systemctl", pipeline.actd_restart_cmd())

    def test_heartbeat_pid_shape(self):
        self.assertIsNone(pipeline._heartbeat_pid(None))
        self.assertIsNone(pipeline._heartbeat_pid({"pid": True}))
        self.assertIsNone(pipeline._heartbeat_pid({"pid": 0}))
        self.assertIsNone(pipeline._heartbeat_pid({"pid": "7"}))
        self.assertEqual(pipeline._heartbeat_pid({"pid": 7}), 7)


class PinnedInterpreterTestCase(unittest.TestCase):
    def setUp(self):
        self.rj = config.HOME / "config" / "runtime.json"
        self.rj.parent.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: self.rj.unlink(missing_ok=True))

    def test_reads_python_key(self):
        self.rj.write_text(json.dumps({"python": "/usr/bin/python3"}), encoding="utf-8")
        self.assertEqual(core.pinned_interpreter(), "/usr/bin/python3")

    def test_missing_or_bad_is_empty(self):
        self.rj.unlink(missing_ok=True)
        self.assertEqual(core.pinned_interpreter(), "")
        self.rj.write_text("{not json", encoding="utf-8")
        self.assertEqual(core.pinned_interpreter(), "")
        self.rj.write_text(json.dumps({"python": None}), encoding="utf-8")
        self.assertEqual(core.pinned_interpreter(), "")
        self.assertEqual(environment._runtime_pin(self.rj), "")


class TemplateDerivationTestCase(unittest.TestCase):
    """Expected labels / units / tasks derive from the template dirs under HOME."""

    def test_systemd_units_from_templates(self):
        d = config.HOME / "act" / "systemd"
        d.mkdir(parents=True, exist_ok=True)
        (d / "zelin-actd.service").write_text("", encoding="utf-8")
        (d / "zelin-radar.timer").write_text("", encoding="utf-8")
        self.addCleanup(lambda: [p.unlink() for p in d.glob("*")])
        self.assertEqual(services.systemd_units(), ["zelin-actd.service", "zelin-radar.timer"])

    def test_scheduled_tasks_from_templates(self):
        d = config.HOME / "act" / "tasksched"
        d.mkdir(parents=True, exist_ok=True)
        (d / "actd.xml").write_text("", encoding="utf-8")
        self.addCleanup(lambda: [p.unlink() for p in d.glob("*")])
        self.assertEqual(services.scheduled_tasks(), [core.ACTD_TASK])

    def test_templated_labels_from_plists(self):
        d = config.HOME / "act" / "launchd"
        d.mkdir(parents=True, exist_ok=True)
        (d / (core.ACTD_LABEL + ".plist")).write_text("", encoding="utf-8")
        self.addCleanup(lambda: [p.unlink() for p in d.glob("*")])
        probes = mock.Mock(launchd_labels=None)
        self.assertEqual(core.templated_labels(probes), [core.ACTD_LABEL])
        self.assertEqual(core.templated_labels(mock.Mock(launchd_labels=["x"])), ["x"])


class MiscHelpersTestCase(unittest.TestCase):
    def test_launchctl_table_swallows_seam_failure(self):
        probes = mock.Mock()
        probes.launchctl_list.side_effect = RuntimeError("no launchctl")
        self.assertEqual(core.launchctl_table(probes), {})
        probes.launchctl_list.side_effect = None
        probes.launchctl_list.return_value = "PID\tStatus\tLabel\n12\t0\tcom.x\n-\t1\tcom.y\nshort\n"
        self.assertEqual(core.launchctl_table(probes), {"Label": ("PID", "Status"),
                                                        "com.x": ("12", "0"), "com.y": ("-", "1")})

    def test_installer_per_os(self):
        with mock.patch("sys.platform", "darwin"):
            self.assertEqual(core.installer(), "install.sh")
        with mock.patch("sys.platform", "linux"):
            self.assertEqual(core.installer(), "install-linux.sh")

    def test_row_from_attaches_failure(self):
        res = core.row_from({"status": "fail", "detail": "d", "failure_id": "cron_missing"}, "n")
        self.assertEqual((res.name, res.status, res.fix, res.failure_id), ("n", "fail", "", "cron_missing"))
        self.assertTrue(res.action_id)
        plain = core.row_from({"status": "ok", "detail": "d", "fix": ""}, "n")
        self.assertEqual(plain.failure_id, "")

    def test_too_old(self):
        self.assertTrue(environment._too_old("3.8"))
        self.assertFalse(environment._too_old("3.9"))
        self.assertFalse(environment._too_old(""))

    def test_symlink_shaped(self):
        self.assertFalse(launchd.symlink_shaped(None))
        self.assertFalse(launchd.symlink_shaped("relative"))
        self.assertFalse(launchd.symlink_shaped("/nonexistent-zai-path-xyz/"))
        tmp = Path(tempfile.mkdtemp(prefix="zai-sym-"))
        real = tmp / "real"
        real.mkdir()
        link = tmp / "link"
        try:
            link.symlink_to(real)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        self.assertTrue(launchd.symlink_shaped(str(link) + "/"))
        self.assertFalse(launchd.symlink_shaped(str(real.resolve())))

    def test_interpreter_ok_requires_absolute_executable(self):
        probes = mock.Mock()
        probes.run.return_value = (0, "")
        self.assertFalse(launchd.interpreter_ok(probes, "python3"))
        self.assertFalse(launchd.interpreter_ok(probes, "/nonexistent/python3"))
        self.assertTrue(launchd.interpreter_ok(probes, "/bin/sh"))
        probes.run.return_value = (1, "No module named yaml")
        self.assertFalse(launchd.interpreter_ok(probes, "/bin/sh"))

    def test_log_missing_module_last_match_wins(self):
        tail = "No module named 'yaml'\n...\nNo module named 'act'\nNo module named 'other'\n"
        self.assertEqual(launchd.log_missing_module(tail), launchd.MISSING_ACT)
        self.assertIsNone(launchd.log_missing_module("all good"))

    def test_crashing_agents_parses_and_survives_seam_failure(self):
        probes = mock.Mock()
        probes.launchctl_list.return_value = ("PID\tStatus\tLabel\n-\t1\tcom.zelin.aiassistant.actd\n"
                                              "-\t0\tcom.zelin.aiassistant.syncd\n12\t0\tcom.zelin.aiassistant.server\n")
        self.assertEqual(launchd.crashing_agents(probes), {"actd"})
        probes.launchctl_list.side_effect = RuntimeError()
        self.assertEqual(launchd.crashing_agents(probes), set())


if __name__ == "__main__":
    unittest.main()
