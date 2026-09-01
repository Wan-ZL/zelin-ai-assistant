"""§47.4 actd heartbeat — ``state/actd.heartbeat`` (write side + shape).

2026-08-31 22:31: actd kept its pid for 2.5 h, no children, parked in
time.sleep, dashboard frozen — every liveness signal the product had said
"fine". The heartbeat is touched at every phase boundary of every pass; its
mtime is the truth, ``stale_after_s`` is the WRITER's threshold (3 × interval,
floor 90 s) so doctor and server/health never re-derive it. Read side: doctor
(tests/test_doctor.py) and server/health.py (tests/test_server_health.py).
"""
import json
import os
import time
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import actd
from act.lib import config, heartbeat


class BeatShapeTestCase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        self.path = heartbeat.HEARTBEAT_PATH
        self.path.unlink(missing_ok=True)
        self.addCleanup(lambda: self.path.unlink(missing_ok=True))

    def test_beat_writes_the_contract_shape_atomically(self):
        heartbeat.beat("idle", 10)
        body = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(body["phase"], "idle")
        self.assertEqual(body["pid"], os.getpid())
        self.assertEqual(body["interval"], 10)
        self.assertEqual(body["stale_after_s"], 90)      # floor beats 3×10
        self.assertRegex(body["ts"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        self.assertIn("version", body)
        self.assertFalse(self.path.with_suffix(".heartbeat.tmp").exists())

    def test_stale_threshold_is_three_intervals_with_a_floor(self):
        self.assertEqual(heartbeat.stale_after_seconds(10), 90)
        self.assertEqual(heartbeat.stale_after_seconds(30), 90)
        self.assertEqual(heartbeat.stale_after_seconds(60), 180)
        self.assertEqual(heartbeat.stale_after_seconds(600), 1800)
        self.assertEqual(heartbeat.stale_after_seconds(None), 90)
        self.assertEqual(heartbeat.stale_after_seconds("junk"), 90)

    def test_read_reports_age_from_mtime_and_is_stale_uses_writer_threshold(self):
        heartbeat.beat("reconcile", 60)
        old = time.time() - 400
        os.utime(self.path, (old, old))
        hb = heartbeat.read()
        self.assertGreaterEqual(hb["age_s"], 399)
        self.assertEqual(hb["phase"], "reconcile")
        self.assertEqual(hb["stale_after_s"], 180)
        self.assertTrue(heartbeat.is_stale(hb))
        fresh = time.time()
        os.utime(self.path, (fresh, fresh))
        self.assertFalse(heartbeat.is_stale(heartbeat.read()))

    def test_torn_body_still_yields_the_mtime_age(self):
        self.path.write_text("{not json", encoding="utf-8")
        hb = heartbeat.read()
        self.assertIn("age_s", hb)
        self.assertNotIn("phase", hb)
        self.assertFalse(heartbeat.is_stale(hb))          # fresh mtime, floor threshold

    def test_missing_file_reads_none(self):
        self.assertIsNone(heartbeat.read())
        self.assertIsNone(heartbeat.is_stale(None))

    def test_beat_never_raises_on_an_unwritable_path(self):
        bad = Path(TMP_HOME) / "no-such-dir-as-file"
        bad.write_text("x", encoding="utf-8")            # a FILE where a dir is needed
        self.addCleanup(lambda: bad.unlink(missing_ok=True))
        heartbeat.beat("idle", 10, path=bad / "actd.heartbeat")   # must not raise


class RunOnceBeatsTestCase(unittest.TestCase):
    """The loop touches the heartbeat at every phase boundary of a pass."""

    def setUp(self):
        config.ensure_state_dirs()
        heartbeat.HEARTBEAT_PATH.unlink(missing_ok=True)
        self.addCleanup(lambda: heartbeat.HEARTBEAT_PATH.unlink(missing_ok=True))

    def test_phases_are_beaten_in_order_during_a_pass(self):
        phases = []
        real_beat = heartbeat.beat

        def spy(phase, interval=None, path=None):
            phases.append((phase, interval))
            real_beat(phase, interval, path)

        with mock.patch.object(actd.heartbeat, "beat", spy), \
                mock.patch.object(actd, "process_inbox", return_value=0), \
                mock.patch.object(actd, "auto_dispatch_pass", return_value=0), \
                mock.patch.object(actd, "dispatch_approved", return_value=0), \
                mock.patch.object(actd, "reconcile_executing"), \
                mock.patch.object(actd, "process_raising", return_value=0), \
                mock.patch.object(actd, "purge_trash"), \
                mock.patch.object(actd, "archive_stale"), \
                mock.patch.object(actd, "cleanup_merge_jobs"), \
                mock.patch.object(actd, "auto_merge", None), \
                mock.patch.object(actd, "feedback", None), \
                mock.patch.object(actd, "update_check", None), \
                mock.patch.object(actd, "build_dashboard", return_value={}), \
                mock.patch.object(actd, "write_dashboard"), \
                mock.patch.object(actd, "detect_transitions", return_value=[]), \
                mock.patch.object(actd, "_check_auth_failures", return_value=[]), \
                mock.patch.object(actd, "_check_radar_liveness", return_value=[]):
            actd.run_once(config.Config(), None, set(), set(), set(), interval=10)
        self.assertEqual([p for p, _ in phases],
                         ["inbox", "dispatch", "reconcile", "housekeeping", "dashboard"])
        self.assertTrue(all(iv == 10 for _, iv in phases))
        body = json.loads(heartbeat.HEARTBEAT_PATH.read_text(encoding="utf-8"))
        self.assertEqual(body["phase"], "dashboard")


if __name__ == "__main__":
    unittest.main()
