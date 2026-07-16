"""act/doctor.py — post-install diagnostics, with injected probes.

The doctor must (a) never raise, (b) exit with the number of FAILs,
(c) label every check ok/warn/fail with a one-line fix for non-ok states.
All machine access goes through doctor.Probes; these tests inject fakes for
every probe and build the on-disk fixtures inside the sandbox
AIASSISTANT_HOME (tests/__init__.py) — nothing outside it is ever touched.
"""
import contextlib
import datetime as _dt
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME

from act import doctor
from act.lib import config, secrets

NOW = 1_700_000_000.0

# The launchd/cron/screenpipe checks model a macOS install and lean on POSIX
# file modes + executable shell shims; skip them on Windows (the systemd branch
# has its own Windows-safe suite below). Not darwin-only in spirit — Linux runs
# them fine — so key on "not Windows".
_WIN = sys.platform.startswith("win")

LABELS = ["com.zelin.aiassistant.actd", "com.zelin.aiassistant.radar"]

HEALTHY_LAUNCHCTL = (
    "4242\t0\tcom.zelin.aiassistant.actd\n"
    "-\t0\tcom.zelin.aiassistant.radar\n"
    "77\t0\tcom.apple.unrelated\n"
)

HEALTHY_CRON = (
    "*/30 * * * * cd /repo && ./ingest/screenpipe-export.sh && "
    "python3 -m act.radar --once\n"
    "7 9 * * 1 cd /repo && python3 -m act.digest --now\n"
)


def _iso(ts: float) -> str:
    return _dt.datetime.fromtimestamp(ts, _dt.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


class FakeRun:
    """Injectable probes.run — records calls, answers from a canned table."""

    def __init__(self, table=None):
        self.calls = []
        self.table = table or {}

    def __call__(self, cmd, env=None, timeout=None):
        self.calls.append({"cmd": list(cmd), "env": env})
        prog = os.path.basename(cmd[0])
        if cmd[0] == sys.executable:
            return self.table.get("python", (0, "3.11"))
        if prog == "claude" and "--version" in cmd:
            return self.table.get("claude --version", (0, "2.0.14 (Claude Code)"))
        if prog == "claude":
            return self.table.get("claude -p", (0, "ok"))
        if prog == "gh":
            return self.table.get("gh auth", (0, "Logged in to github.com"))
        return (0, "")

    def commands(self):
        return [" ".join(c["cmd"][:2]) for c in self.calls]


def by_name(results, name):
    matches = [r for r in results if r.name == name]
    assert matches, "no check named %r in %s" % (name, [r.name for r in results])
    return matches[0]


@unittest.skipIf(_WIN, "macOS/POSIX install checks (launchd/cron/modes)")
class DoctorTestCase(unittest.TestCase):
    def setUp(self):
        # This suite exercises the macOS launchd/cron/screenpipe checks; pin
        # darwin so it validates that branch identically on the macOS and
        # ubuntu/windows CI runners. The systemd branch has its own suite below.
        p = mock.patch("sys.platform", "darwin")
        p.start()
        self.addCleanup(p.stop)

        self.home = Path(TMP_HOME)
        self._created = []

        # another test's leftover overrides would leak into load_config()
        self._stashed_overrides = None
        if config.SETTINGS_OVERRIDES_PATH.exists():
            self._stashed_overrides = config.SETTINGS_OVERRIDES_PATH.read_text(
                encoding="utf-8")
            config.SETTINGS_OVERRIDES_PATH.unlink()

        config.ensure_state_dirs()
        self._touch(self.home / "install.sh", "#!/bin/bash\n")

        vault = self.home / "vault"
        self.raw_dir = vault / "2 - raw"
        self.unprocessed_dir = vault / "1 - unprocessed"
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.unprocessed_dir.mkdir(parents=True, exist_ok=True)
        self._touch(
            config.CONFIG_PATH,
            "sources:\n  obsidian_raw: %s\n" % json.dumps(str(self.raw_dir)))

        self._touch(self.home / "config" / "runtime.json",
                    json.dumps({"python": sys.executable}))

        self.dashboard = config.DASHBOARD_PATH
        self._write_dashboard(NOW - 5)

        self.key_file = secrets.write_secret(
            secrets.ANTHROPIC_API_KEY_FILE, "sk-ant-test-123")
        self._created.append(self.key_file)

        self.db_file = self.home / "fake-screenpipe.sqlite"
        self._touch(self.db_file, "not-a-real-db")
        os.utime(self.db_file, (NOW - 300, NOW - 300))

        self.missing_legacy = self.home / "no-such-legacy-key.txt"

    def tearDown(self):
        for p in self._created:
            if p.exists():
                p.unlink()
        if self._stashed_overrides is not None:
            config.SETTINGS_OVERRIDES_PATH.write_text(
                self._stashed_overrides, encoding="utf-8")

    def _touch(self, path: Path, content: str):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        self._created.append(path)

    def _write_dashboard(self, generated_ts: float):
        self._touch(self.dashboard,
                    json.dumps({"generated_at": _iso(generated_ts)}))

    def make_probes(self, run=None, launchctl=None, cron=None, which_map=None,
                    now=None, db=None, legacy=None):
        if which_map is None:
            which_map = {"claude": "/fake/bin/claude",
                         "npx": "/fake/bin/npx",
                         "gh": "/fake/bin/gh"}
        return doctor.Probes(
            which=which_map.get,
            run=run if run is not None else FakeRun(),
            launchctl_list=lambda: (
                HEALTHY_LAUNCHCTL if launchctl is None else launchctl),
            crontab=lambda: HEALTHY_CRON if cron is None else cron,
            now=now or (lambda: NOW),
            launchd_labels=LABELS,
            screenpipe_db=db or self.db_file,
            legacy_key_path=legacy or self.missing_legacy,
            # hermetic: never read the REAL ~/Library/LaunchAgents plist or
            # probe the real login shell from the sandboxed suite
            daemon_path_env=lambda: None,
            login_shell_claude=lambda: None,
        )

    def _main(self, probes, argv=None):
        """Run doctor.main with stdout captured; returns (exit_code, output)."""
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = doctor.main(argv or [], probes=probes)
        return code, buf.getvalue()

    # -- healthy baseline ---------------------------------------------------- #
    def test_healthy_setup_has_no_fails_and_exits_zero(self):
        probes = self.make_probes()
        results = doctor.run_checks(probes)
        fails = [r for r in results if r.status == doctor.FAIL]
        self.assertEqual(fails, [], "unexpected FAILs: %s" % fails)
        for name in ("AIASSISTANT_HOME", "claude CLI", "daemon python",
                     "config.yaml", "anthropic key", "state dirs", "actd",
                     "radar", "cron ingest chain", "cron digest", "dashboard",
                     "obsidian vault", "screenpipe db", "node/npx", "gh CLI",
                     "claude auth"):
            self.assertEqual(by_name(results, name).status, doctor.OK, name)
        code, out = self._main(self.make_probes())
        self.assertEqual(code, 0)
        self.assertIn("[ ok ] actd: running (pid 4242)", out)

    # -- key resolution (CONTRACT §19) --------------------------------------- #
    def test_missing_key_is_subscription_mode_warn(self):
        self.key_file.unlink()
        results = doctor.run_checks(self.make_probes(), fast=True)
        r = by_name(results, "anthropic key")
        self.assertEqual(r.status, doctor.WARN)
        self.assertIn("subscription-auth", r.detail)
        self.assertIn("Settings", r.fix)

    def test_legacy_key_path_still_resolves(self):
        self.key_file.unlink()
        legacy = self.home / "legacy-key.txt"
        self._touch(legacy, "sk-ant-legacy\n")
        results = doctor.run_checks(self.make_probes(legacy=legacy), fast=True)
        r = by_name(results, "anthropic key")
        self.assertEqual(r.status, doctor.OK)
        self.assertIn("legacy", r.detail)

    def test_world_readable_key_file_warns_chmod(self):
        os.chmod(self.key_file, 0o644)
        results = doctor.run_checks(self.make_probes(), fast=True)
        r = by_name(results, "anthropic key")
        self.assertEqual(r.status, doctor.WARN)
        self.assertIn("chmod 600", r.fix)

    # -- live auth probe ------------------------------------------------------ #
    def test_live_probe_passes_resolved_key_in_env(self):
        run = FakeRun()
        doctor.run_checks(self.make_probes(run=run))
        probe_calls = [c for c in run.calls if "-p" in c["cmd"]]
        self.assertEqual(len(probe_calls), 1)
        self.assertEqual(probe_calls[0]["env"]["ANTHROPIC_API_KEY"],
                         "sk-ant-test-123")

    def test_live_probe_failure_is_fail_with_fix(self):
        run = FakeRun(table={"claude -p": (1, "Invalid API key")})
        results = doctor.run_checks(self.make_probes(run=run))
        r = by_name(results, "claude auth")
        self.assertEqual(r.status, doctor.FAIL)
        self.assertIn("Invalid API key", r.detail)
        self.assertTrue(r.fix)

    def test_fast_skips_live_probe(self):
        run = FakeRun()
        results = doctor.run_checks(self.make_probes(run=run), fast=True)
        self.assertNotIn("claude auth", [r.name for r in results])
        self.assertFalse([c for c in run.calls if "-p" in c["cmd"]])

    # -- launchd agents -------------------------------------------------------- #
    def test_crashing_actd_is_fail_with_log_pointer(self):
        out = "-\t78\tcom.zelin.aiassistant.actd\n-\t0\tcom.zelin.aiassistant.radar\n"
        results = doctor.run_checks(self.make_probes(launchctl=out), fast=True)
        r = by_name(results, "actd")
        self.assertEqual(r.status, doctor.FAIL)
        self.assertIn("78", r.detail)
        self.assertIn("actd.launchd.log", r.fix)

    def test_unregistered_actd_fails_but_radar_only_warns(self):
        results = doctor.run_checks(self.make_probes(launchctl=""), fast=True)
        self.assertEqual(by_name(results, "actd").status, doctor.FAIL)
        self.assertEqual(by_name(results, "radar").status, doctor.WARN)

    # -- dashboard freshness ----------------------------------------------------- #
    def test_stale_dashboard_is_fail(self):
        self._write_dashboard(NOW - 1200)  # 20 min old
        results = doctor.run_checks(self.make_probes(), fast=True)
        r = by_name(results, "dashboard")
        self.assertEqual(r.status, doctor.FAIL)
        self.assertIn("stale", r.detail)
        self.assertIn("20 min", r.detail)

    def test_missing_dashboard_is_fail(self):
        self.dashboard.unlink()
        results = doctor.run_checks(self.make_probes(), fast=True)
        self.assertEqual(by_name(results, "dashboard").status, doctor.FAIL)

    # -- cron ----------------------------------------------------------------- #
    def test_empty_crontab_fails_ingest_and_warns_digest(self):
        results = doctor.run_checks(self.make_probes(cron=""), fast=True)
        self.assertEqual(by_name(results, "cron ingest chain").status, doctor.FAIL)
        self.assertEqual(by_name(results, "cron digest").status, doctor.WARN)

    # -- daemon python / PyYAML ------------------------------------------------ #
    def test_daemon_python_without_pyyaml_is_fail(self):
        run = FakeRun(table={"python": (1, "ModuleNotFoundError: yaml")})
        results = doctor.run_checks(self.make_probes(run=run), fast=True)
        r = by_name(results, "daemon python")
        self.assertEqual(r.status, doctor.FAIL)
        self.assertIn("pip install", r.fix)
        self.assertIn("--break-system-packages", r.fix)

    def test_missing_runtime_json_is_warn(self):
        (self.home / "config" / "runtime.json").unlink()
        results = doctor.run_checks(self.make_probes(), fast=True)
        self.assertEqual(by_name(results, "daemon python").status, doctor.WARN)

    # -- robustness ------------------------------------------------------------ #
    def test_probe_exception_becomes_fail_never_raises(self):
        def boom():
            raise RuntimeError("launchctl exploded")

        probes = self.make_probes()
        probes.launchctl_list = boom
        results = doctor.run_checks(probes, fast=True)  # must not raise
        crashed = [r for r in results if "diagnostic crashed" in r.detail]
        self.assertEqual(len(crashed), 1)
        self.assertEqual(crashed[0].status, doctor.FAIL)

    def test_exit_code_equals_number_of_fails(self):
        self.dashboard.unlink()                       # 1 fail
        probes = self.make_probes(cron="")            # +1 fail (ingest chain)
        code, out = self._main(probes, argv=["--fast"])
        self.assertEqual(code, 2)
        self.assertIn("2 fail", out)

    def test_missing_claude_is_fail(self):
        results = doctor.run_checks(
            self.make_probes(which_map={"npx": "/fake/npx", "gh": "/fake/gh"}),
            fast=True)
        self.assertEqual(by_name(results, "claude CLI").status, doctor.FAIL)


@unittest.skipIf(_WIN, "uses #!/bin/sh executable shims resolved via PATH")
class DaemonClaudeCheckTestCase(unittest.TestCase):
    """_check_daemon_claude — the 2026-07-08 two-installs incident: launchd's
    PATH resolved an outdated claude (no --bg) while the login shell used the
    new one; every dispatch failed with "unknown option '--bg'" forever.

    Real executable shims in tempdirs (echoing different --version / --help)
    exercise the real resolution path; only the plist/login-shell probes are
    injected."""

    NEW_HELP = "Usage: claude [options]\n  --bg, --background  background\n"
    OLD_HELP = "Usage: claude [options]\n  -p, --print  print mode\n"

    def setUp(self):
        # pinned darwin: this is the launchd two-installs incident; the fix
        # lines reference the OS installer (install.sh on darwin).
        p = mock.patch("sys.platform", "darwin")
        p.start()
        self.addCleanup(p.stop)
        self.tmp = Path(tempfile.mkdtemp(prefix="daemon-claude-"))

    def _shim(self, sub: str, version: str, help_text: str,
              bg_supported: bool) -> Path:
        d = self.tmp / sub
        d.mkdir(parents=True, exist_ok=True)
        p = d / "claude"
        bg_case = ("  --bg) echo backgrounded ;;\n" if bg_supported else
                   "  --bg) echo \"error: unknown option '--bg'\" >&2; exit 1 ;;\n")
        p.write_text("#!/bin/sh\ncase \"$1\" in\n"
                     "  --version) echo \"%s\" ;;\n"
                     "  --help) printf '%%b' \"%s\" ;;\n%s"
                     "esac\n" % (version, help_text.replace("\n", "\\n"), bg_case),
                     encoding="utf-8")
        p.chmod(0o755)
        return p

    def _probes(self, daemon_dir, shell_claude):
        return doctor.Probes(
            daemon_path_env=lambda: str(daemon_dir) if daemon_dir else None,
            login_shell_claude=lambda: str(shell_claude) if shell_claude else None,
        )

    def test_version_mismatch_is_fail_with_outdated_classification(self):
        old = self._shim("old", "2.1.16 (Claude Code)", self.OLD_HELP, False)
        new = self._shim("new", "2.1.206 (Claude Code)", self.NEW_HELP, True)
        res = doctor._check_daemon_claude(self._probes(old.parent, new))
        self.assertEqual(res.status, doctor.FAIL)
        self.assertEqual(res.failure_id, "claude_cli_outdated")
        self.assertEqual(res.action_id, "open_deps")
        self.assertIn("2.1.16", res.detail)
        self.assertIn("2.1.206", res.detail)
        self.assertIn("install.sh", res.fix)

    def test_same_binary_everywhere_is_ok(self):
        new = self._shim("new", "2.1.206 (Claude Code)", self.NEW_HELP, True)
        res = doctor._check_daemon_claude(self._probes(new.parent, new))
        self.assertEqual(res.status, doctor.OK)
        self.assertIn("same as your login shell", res.detail)

    def test_bg_unsupported_fails_even_without_a_shell_comparison(self):
        old = self._shim("old", "2.1.16 (Claude Code)", self.OLD_HELP, False)
        res = doctor._check_daemon_claude(self._probes(old.parent, None))
        self.assertEqual(res.status, doctor.FAIL)
        self.assertEqual(res.failure_id, "claude_cli_outdated")
        self.assertIn("--bg", res.detail)

    def test_two_copies_same_version_is_ok(self):
        a = self._shim("a", "2.1.206 (Claude Code)", self.NEW_HELP, True)
        b = self._shim("b", "2.1.206 (Claude Code)", self.NEW_HELP, True)
        res = doctor._check_daemon_claude(self._probes(a.parent, b))
        self.assertEqual(res.status, doctor.OK)

    def test_missing_plist_is_honest_warn(self):
        res = doctor._check_daemon_claude(self._probes(None, None))
        self.assertEqual(res.status, doctor.WARN)
        self.assertIn("install.sh", res.fix)

    def test_no_claude_on_daemon_path_is_fail(self):
        empty = self.tmp / "empty"
        empty.mkdir()
        res = doctor._check_daemon_claude(self._probes(empty, None))
        self.assertEqual(res.status, doctor.FAIL)
        self.assertEqual(res.failure_id, "claude_cli_missing")


SYSTEMD_UNITS = [
    "zelin-actd.service", "zelin-webui.service",
    "zelin-gmail-radar.timer", "zelin-slack-radar.timer",
    "zelin-obsidian-radar.timer", "zelin-weekly-digest.timer",
]


def _systemctl(rows, bullets=()):
    """Build `systemctl --user list-units` output from {unit: (active, sub)}.

    ``bullets`` names units that get the failed-unit ● prefix systemd emits.
    """
    out = []
    for unit, (active, sub) in rows.items():
        prefix = "● " if unit in bullets else "  "
        out.append("%s%-28s loaded %-9s %-8s Zelin AI Assistant\n"
                   % (prefix, unit, active, sub))
    return "".join(out)


class SystemdDoctorTestCase(unittest.TestCase):
    """_check_systemd — the Linux systemd --user mirror of the launchd check.

    Feeds `systemctl --user list-units` fixture text (what the OS seam returns
    off-macOS) and asserts the parse: resident services must be active, timers
    must be active, actd is the only FAIL-if-down unit, a ● failed line parses.
    """

    def setUp(self):
        p = mock.patch("sys.platform", "linux")
        p.start()
        self.addCleanup(p.stop)

    def _probes(self, listing):
        return doctor.Probes(launchctl_list=lambda: listing,
                             systemd_units=list(SYSTEMD_UNITS))

    def _healthy_rows(self):
        rows = {"zelin-actd.service": ("active", "running"),
                "zelin-webui.service": ("active", "running"),
                # a timer-driven oneshot .service is correctly inactive between
                # fires — present in --all output but NOT in our expected list
                "zelin-gmail-radar.service": ("inactive", "dead")}
        for t in ("gmail-radar", "slack-radar", "obsidian-radar", "weekly-digest"):
            rows["zelin-%s.timer" % t] = ("active", "waiting")
        return rows

    def test_healthy_units_all_ok(self):
        results = doctor._check_systemd(self._probes(
            _systemctl(self._healthy_rows())))
        by = {r.name: r for r in results}
        self.assertEqual(by["actd"].status, doctor.OK)
        self.assertIn("active (running)", by["actd"].detail)
        self.assertEqual(by["webui"].status, doctor.OK)
        for t in ("gmail-radar", "slack-radar", "obsidian-radar", "weekly-digest"):
            self.assertEqual(by[t].status, doctor.OK)
            self.assertIn("waiting", by[t].detail)
        # the inactive oneshot .service is NOT reported (only residents+timers)
        self.assertNotIn("gmail-radar.service", by)

    def test_actd_down_fails_but_timer_only_warns(self):
        rows = self._healthy_rows()
        rows["zelin-actd.service"] = ("inactive", "dead")
        rows["zelin-gmail-radar.timer"] = ("inactive", "dead")
        by = {r.name: r for r in doctor._check_systemd(
            self._probes(_systemctl(rows)))}
        self.assertEqual(by["actd"].status, doctor.FAIL)
        self.assertIn("not running", by["actd"].detail)
        self.assertIn("systemctl --user enable --now", by["actd"].fix)
        self.assertEqual(by["actd"].failure_id, "agent_unloaded")
        self.assertEqual(by["gmail-radar"].status, doctor.WARN)

    def test_failed_unit_bullet_is_parsed(self):
        rows = self._healthy_rows()
        rows["zelin-actd.service"] = ("failed", "failed")
        by = {r.name: r for r in doctor._check_systemd(
            self._probes(_systemctl(rows, bullets=("zelin-actd.service",))))}
        self.assertEqual(by["actd"].status, doctor.FAIL)
        self.assertIn("failed to start", by["actd"].detail)
        self.assertIn("journalctl", by["actd"].fix)

    def test_not_registered_when_manager_absent(self):
        # empty listing (e.g. `systemctl --user` could not reach the bus)
        by = {r.name: r for r in doctor._check_systemd(self._probes(""))}
        self.assertEqual(by["actd"].status, doctor.FAIL)
        self.assertIn("not registered", by["actd"].detail)
        self.assertIn("install-linux.sh", by["actd"].fix)
        self.assertEqual(by["webui"].status, doctor.WARN)

    def test_platform_composition_drops_macos_only_checks(self):
        names = {f.__name__ for f in doctor._checks_for_platform()}
        self.assertIn("_check_systemd", names)
        for macos_only in ("_check_launchd", "_check_cron",
                           "_check_screenpipe", "_check_npx"):
            self.assertNotIn(macos_only, names)
        with mock.patch("sys.platform", "darwin"):
            dnames = {f.__name__ for f in doctor._checks_for_platform()}
        self.assertIn("_check_launchd", dnames)
        self.assertIn("_check_cron", dnames)
        self.assertNotIn("_check_systemd", dnames)


# Full \ZelinAIAssistant\ task names doctor expects, mirroring SYSTEMD_UNITS.
TASKS = [
    "\\ZelinAIAssistant\\actd", "\\ZelinAIAssistant\\webui",
    "\\ZelinAIAssistant\\gmail-radar", "\\ZelinAIAssistant\\slack-radar",
    "\\ZelinAIAssistant\\obsidian-radar", "\\ZelinAIAssistant\\weekly-digest",
]


def _schtasks(rows):
    """Build `schtasks /query /fo LIST /v` output from {task: (status, state)}.

    LIST is one "Field: Value" block per task, blocks separated by a blank line.
    """
    out = []
    for task, (status, state) in rows.items():
        out.append(
            "Folder: \\ZelinAIAssistant\n"
            "HostName: FRIEND-PC\n"
            "TaskName: %s\n"
            "Next Run Time: 7/11/2026 9:00:00 AM\n"
            "Status: %s\n"
            "Logon Mode: Interactive only\n"
            "Scheduled Task State: %s\n"
            "\n" % (task, status, state))
    return "".join(out)


class WindowsScheduledTasksDoctorTestCase(unittest.TestCase):
    """_check_scheduled_tasks — the Windows Task Scheduler mirror of the launchd
    / systemd checks.

    Feeds `schtasks /query /fo LIST /v` fixture text (what the OS seam returns on
    Windows) and asserts the parse: resident tasks Running/Ready are OK, a
    Disabled task is down, actd is the only FAIL-if-down task, and unrelated
    OS tasks are ignored.
    """

    def setUp(self):
        p = mock.patch("sys.platform", "win32")
        p.start()
        self.addCleanup(p.stop)

    def _probes(self, listing):
        return doctor.Probes(launchctl_list=lambda: listing,
                             scheduled_tasks=list(TASKS))

    def _healthy_rows(self):
        rows = {"\\ZelinAIAssistant\\actd": ("Running", "Enabled"),
                "\\ZelinAIAssistant\\webui": ("Running", "Enabled")}
        for t in ("gmail-radar", "slack-radar", "obsidian-radar", "weekly-digest"):
            rows["\\ZelinAIAssistant\\" + t] = ("Ready", "Enabled")
        return rows

    def test_healthy_tasks_all_ok(self):
        # add an unrelated Windows task to prove it is filtered out
        rows = self._healthy_rows()
        rows["\\Microsoft\\Windows\\UpdateOrchestrator\\Scan"] = ("Ready", "Enabled")
        by = {r.name: r for r in doctor._check_scheduled_tasks(
            self._probes(_schtasks(rows)))}
        self.assertEqual(by["actd"].status, doctor.OK)
        self.assertIn("running", by["actd"].detail)
        self.assertEqual(by["webui"].status, doctor.OK)
        for t in ("gmail-radar", "slack-radar", "obsidian-radar", "weekly-digest"):
            self.assertEqual(by[t].status, doctor.OK)
            self.assertIn("ready", by[t].detail)
        self.assertNotIn("Scan", by)

    def test_actd_missing_fails_but_radar_only_warns(self):
        rows = self._healthy_rows()
        del rows["\\ZelinAIAssistant\\actd"]
        del rows["\\ZelinAIAssistant\\gmail-radar"]
        by = {r.name: r for r in doctor._check_scheduled_tasks(
            self._probes(_schtasks(rows)))}
        self.assertEqual(by["actd"].status, doctor.FAIL)
        self.assertIn("not registered", by["actd"].detail)
        self.assertIn("install.ps1", by["actd"].fix)
        self.assertEqual(by["actd"].failure_id, "agent_unloaded")
        self.assertEqual(by["gmail-radar"].status, doctor.WARN)

    def test_disabled_task_is_down(self):
        rows = self._healthy_rows()
        rows["\\ZelinAIAssistant\\actd"] = ("Ready", "Disabled")
        by = {r.name: r for r in doctor._check_scheduled_tasks(
            self._probes(_schtasks(rows)))}
        self.assertEqual(by["actd"].status, doctor.FAIL)
        self.assertIn("disabled", by["actd"].detail)
        self.assertIn("/ENABLE", by["actd"].fix)

    def test_not_registered_when_schtasks_empty(self):
        by = {r.name: r for r in doctor._check_scheduled_tasks(self._probes(""))}
        self.assertEqual(by["actd"].status, doctor.FAIL)
        self.assertIn("not registered", by["actd"].detail)
        self.assertEqual(by["webui"].status, doctor.WARN)

    def test_platform_composition_uses_tasks_not_launchd_or_systemd(self):
        names = {f.__name__ for f in doctor._checks_for_platform()}
        self.assertIn("_check_scheduled_tasks", names)
        for other in ("_check_launchd", "_check_cron", "_check_systemd",
                      "_check_screenpipe", "_check_npx"):
            self.assertNotIn(other, names)

    def test_installer_is_ps1_on_windows(self):
        self.assertEqual(doctor._installer(), "install.ps1")


class DoctorLanguageRoutingTestCase(unittest.TestCase):
    """v0.42 (audit #16): the unclassified checks' detail/fix prose follows the
    §15 language resolution (act/lib/failures.ui_lang): AIASSISTANT_UI_LANG
    env var (app-spawned) > persisted setting (overrides/config.yaml) >
    system locale (zh* → zh, else en). Commands stay English in every case.
    _check_gh's missing-binary WARN is the probe — it only touches
    probes.which, so the test needs no filesystem fixtures."""

    def setUp(self):
        config.ensure_state_dirs()
        # hermetic: neither source may carry a persisted language going in
        self._stashed_overrides = self._stash(config.SETTINGS_OVERRIDES_PATH)
        self._stashed_config = self._stash(config.CONFIG_PATH)
        self.addCleanup(self._restore)

    @staticmethod
    def _stash(path):
        if path.exists():
            content = path.read_text(encoding="utf-8")
            path.unlink()
            return content
        return None

    def _restore(self):
        for path, content in ((config.SETTINGS_OVERRIDES_PATH, self._stashed_overrides),
                              (config.CONFIG_PATH, self._stashed_config)):
            if content is not None:
                path.write_text(content, encoding="utf-8")
            elif path.exists():
                path.unlink()

    def _gh(self, env=None, persisted=None):
        """Run the check with a controlled environment: the language-relevant
        vars are removed, then `env` applied; `persisted` writes the §15
        overrides file first."""
        if persisted is not None:
            config.SETTINGS_OVERRIDES_PATH.write_text(
                json.dumps({"language": persisted}), encoding="utf-8")
        base = {k: v for k, v in os.environ.items()
                if k not in ("AIASSISTANT_UI_LANG", "LANG", "LC_ALL")}
        base.update(env or {})
        with mock.patch.dict(os.environ, base, clear=True):
            return doctor._check_gh(doctor.Probes(which=lambda _n: None))

    def test_persisted_zh_detail_with_english_command_fix(self):
        r = self._gh(persisted="zh")
        self.assertEqual(r.status, doctor.WARN)
        self.assertIn("缺失", r.detail)
        self.assertIn("brew install gh", r.fix)   # the command stays a command

    def test_persisted_en_detail_with_english_command_fix(self):
        r = self._gh(persisted="en")
        self.assertEqual(r.status, doctor.WARN)
        self.assertIn("missing", r.detail)
        self.assertNotIn("缺失", r.detail)
        self.assertIn("brew install gh", r.fix)

    def test_env_var_wins_over_persisted_setting(self):
        # app-spawned: the Mac app passes its EFFECTIVE language — it must
        # beat a stale persisted value so app output always matches the app.
        r = self._gh(env={"AIASSISTANT_UI_LANG": "en"}, persisted="zh")
        self.assertIn("missing", r.detail)
        r = self._gh(env={"AIASSISTANT_UI_LANG": "zh"}, persisted="en")
        self.assertIn("缺失", r.detail)

    def test_system_locale_fallback_when_nothing_persisted(self):
        # cron/CLI with no persisted setting: system locale decides —
        # matching the Swift first-run default instead of hardcoded zh.
        self.assertIn("缺失", self._gh(env={"LANG": "zh_CN.UTF-8"}).detail)
        self.assertIn("缺失", self._gh(env={"LC_ALL": "zh_TW.UTF-8"}).detail)
        self.assertIn("missing", self._gh(env={"LANG": "en_US.UTF-8"}).detail)
        self.assertIn("missing", self._gh().detail)   # no locale at all → en


class CronProbeSchemaTestCase(unittest.TestCase):
    """cron_probe.json 半截损坏（read_ok 缺键 / 非 bool）的容错要与其它损坏
    probe 文件一致：WARN unreadable，绝不据半截数据给出「FDA 被禁」的红色
    确定性诊断 + 授权指引（shell writer 只写字面量 true/false）。"""

    def setUp(self):
        config.ensure_state_dirs()
        self.addCleanup(lambda: doctor.CRON_PROBE_PATH.unlink(missing_ok=True))
        self.probes = doctor.Probes(crontab=lambda: HEALTHY_CRON)

    def _write(self, payload: dict) -> None:
        doctor.CRON_PROBE_PATH.write_text(json.dumps(payload), encoding="utf-8")

    @staticmethod
    def _fresh_ts() -> str:
        return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _check(self):
        return doctor._check_cron_probe(self.probes, cron_installed=True)

    def test_missing_read_ok_is_warn_not_fda_fail(self):
        self._write({"ts": self._fresh_ts(), "protected_path": "/v"})
        r = self._check()
        self.assertEqual(r.status, doctor.WARN)
        self.assertEqual(r.failure_id, "")
        # v0.42: detail prose is language-routed — anchor the language-stable
        # file name, not the English word.
        self.assertIn("cron_probe.json", r.detail)

    def test_non_bool_read_ok_is_warn(self):
        for bad in (0, 1, None, "false", "true", []):
            self._write({"ts": self._fresh_ts(), "read_ok": bad,
                         "protected_path": "/v"})
            r = self._check()
            self.assertEqual(r.status, doctor.WARN, f"read_ok={bad!r}")
            self.assertEqual(r.failure_id, "", f"read_ok={bad!r}")

    def test_real_false_still_fails_as_fda_blocked(self):
        self._write({"ts": self._fresh_ts(), "read_ok": False,
                     "protected_path": "/v"})
        r = self._check()
        self.assertEqual(r.status, doctor.FAIL)
        self.assertEqual(r.failure_id, "cron_fda_blocked")

    def test_real_true_is_ok(self):
        self._write({"ts": self._fresh_ts(), "read_ok": True,
                     "protected_path": "/v"})
        self.assertEqual(self._check().status, doctor.OK)


if __name__ == "__main__":
    unittest.main()
