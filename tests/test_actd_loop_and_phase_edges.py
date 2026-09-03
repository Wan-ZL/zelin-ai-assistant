"""actd main loop + phase-helper edges (CONTRACT §47.3 / §47.4 / §34bis / §37).

The resident loop (``main`` without ``--once``) was the one path no judgment
walked: one good pass records success + ``idle`` heartbeat, one crashing pass
records the failure + ``failed`` heartbeat and the loop keeps turning. The sleep
seam ends the test. Also: ``--once`` exit codes, the startup config fallback,
``process_raising`` short-circuits (analyze unavailable / nothing raising),
the snapshot sweep on an empty or unreadable directory, and a harvested title
whose ``set_display_title`` raises (logged, never fatal).
"""
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env before act imports

from act import actd
from act.lib import config, heartbeat, registry
from act.lib.registry import Requirement, State


class _StopLoop(BaseException):
    """Escape hatch for the resident loop under test（BaseException：the loop
    swallows Exception on purpose）."""


class ResidentLoopTest(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        self.health = config.STATE_DIR / actd.LOOP_HEALTH_NAME
        self.health.unlink(missing_ok=True)
        self.addCleanup(lambda: self.health.unlink(missing_ok=True))

    def test_loop_records_success_then_failure_and_keeps_turning(self):
        passes = iter([{"ok": 1}, RuntimeError("pass two exploded"), {"ok": 3}])

        def fake_run_once(*a, **kw):
            item = next(passes)
            if isinstance(item, Exception):
                raise item
            return item

        sleeps = []

        def fake_sleep(seconds):
            sleeps.append(seconds)
            if len(sleeps) == 3:
                raise _StopLoop()

        beats = []
        with mock.patch.object(actd, "run_once", side_effect=fake_run_once), \
                mock.patch.object(actd.time, "sleep", side_effect=fake_sleep), \
                mock.patch.object(heartbeat, "beat", side_effect=lambda phase, *_: beats.append(phase)), \
                mock.patch.object(actd, "_log") as log:
            with self.assertRaises(_StopLoop):
                actd.main(["--interval", "7"])
        self.assertEqual(sleeps, [7, 7, 7])
        self.assertEqual(beats, ["starting", "idle", "failed", "idle"])
        self.assertTrue(any("loop pass FAILED: pass two exploded" in str(c) for c in log.call_args_list))
        self.assertTrue(any("actd starting (interval=7s" in str(c) for c in log.call_args_list))
        # the third (successful) pass wrote the zero receipt after the failure
        self.assertIn('"consecutive_failures": 0', self.health.read_text(encoding="utf-8"))

    def test_once_exit_codes(self):
        with mock.patch.object(actd, "run_once", return_value={}):
            self.assertEqual(actd.main(["--once"]), 0)
        with mock.patch.object(actd, "run_once", side_effect=RuntimeError("boom")), \
                mock.patch.object(actd, "_log") as log:
            self.assertEqual(actd.main(["--once"]), 1)
        self.assertTrue(any("run_once FAILED: boom" in str(c) for c in log.call_args_list))

    def test_startup_config_falls_back_to_defaults(self):
        with mock.patch.object(config, "load_config", side_effect=RuntimeError("bad yaml")), \
                mock.patch.object(actd, "run_once", return_value={}) as run, \
                mock.patch.object(actd, "_log") as log:
            self.assertEqual(actd.main(["--once"]), 0)
        self.assertIsInstance(run.call_args.args[0], config.Config)
        self.assertTrue(any("load_config FAILED at startup" in str(c) for c in log.call_args_list))


class PhaseShortCircuitTest(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()

    def test_process_raising_short_circuits(self):
        with mock.patch.object(actd, "analyze", None):
            self.assertEqual(actd.process_raising(config.Config()), 0)
        registry.save(Requirement(id="R-1", title="t", status=State.CARD_SENT.value))
        with mock.patch.object(actd.analyze, "expand_debt") as expand:
            self.assertEqual(actd.process_raising(config.Config()), 0)
        expand.assert_not_called()

    def test_snapshot_sweep_on_empty_or_unreadable_dir_is_a_noop(self):
        root = config.STATE_DIR / "triage_snapshots"
        root.mkdir(parents=True, exist_ok=True)
        for p in root.glob("*.json"):
            p.unlink()
        with mock.patch.object(registry, "load_all") as load_all:
            actd._sweep_triage_snapshots()
        load_all.assert_not_called()          # nothing to judge → registry never read
        with mock.patch.object(Path, "glob", side_effect=OSError("denied")), \
                mock.patch.object(registry, "load_all") as load_all:
            actd._sweep_triage_snapshots()
        load_all.assert_not_called()

    def test_harvest_title_failure_is_logged_not_raised(self):
        req = Requirement(id="R-2", title="t", status=State.EXECUTING.value)
        with mock.patch.object(registry, "set_display_title", side_effect=RuntimeError("db")), \
                mock.patch.object(actd, "_log") as log:
            actd._apply_harvest_title(req, {"card_title": "新名"})
        self.assertTrue(any("harvest title apply failed for R-2" in str(c)
                            for c in log.call_args_list))


if __name__ == "__main__":
    unittest.main()
