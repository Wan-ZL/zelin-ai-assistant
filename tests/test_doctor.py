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
import unittest
from pathlib import Path

from tests import TMP_HOME

from act import doctor
from act.lib import config, secrets

NOW = 1_700_000_000.0

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


class DoctorTestCase(unittest.TestCase):
    def setUp(self):
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


if __name__ == "__main__":
    unittest.main()
