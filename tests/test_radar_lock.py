"""state/radar.lock — whole-pass mutex (2026-07-08 backfill-storm regression).

A backfill pass over months of historical notes outlives the 30-min cron
cadence, so without the lock two passes interleave and double every claude
call and notification. scan() holds a non-blocking flock for the whole pass;
a pass that finds it held exits as a clean no-op (the running pass's marker
write covers it). The meeting action-items feature that amplified the storm
was removed in v0.14; the lock protects the whole pass and stays.

Runs entirely inside the sandbox AIASSISTANT_HOME (tests/__init__.py).
"""
import contextlib
import fcntl
import io
import json
import unittest

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports
from tests.test_radar import BASE, RadarScanBase

from act import radar
from act.lib import config


class PassLockTestCase(RadarScanBase):
    @contextlib.contextmanager
    def _held_lock(self):
        config.ensure_state_dirs()
        fh = open(config.STATE_DIR / radar.LOCK_PATH_NAME, "w")
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield
        finally:
            fh.close()

    def test_lock_held_skips_the_pass(self):
        self._note("2026-07-01 note.md", "synthetic content", BASE)
        with self._held_lock():
            summary = radar.scan(
                runner=lambda t: self.fail("scanned while the lock was held"))
        self.assertEqual(summary["files_scanned"], 0)
        self.assertTrue(any("radar.lock" in s for s in summary["skipped"]))
        # the running pass covers it: marker untouched, next pass rescans
        second = radar.scan(runner=lambda t: "[]")
        self.assertEqual(second["files_scanned"], 1)

    def test_once_exits_zero_when_locked(self):
        with self._held_lock():
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = radar.main(["--once"])
        self.assertEqual(rc, 0)
        printed = json.loads(out.getvalue())
        self.assertTrue(any("radar.lock" in s for s in printed["skipped"]))

    def test_lock_released_after_a_pass(self):
        radar.scan(runner=lambda t: "[]")
        lock = radar._acquire_pass_lock()
        self.assertIsNotNone(lock)
        lock.close()


if __name__ == "__main__":
    unittest.main()
