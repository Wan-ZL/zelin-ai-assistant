"""act/lib/notify.py §28 app relay queue — queue write format + atomicity,
relay-only routing (NO osascript fallback, by owner decision), and the
write-time stale sweep (10 min guard, injectable clock).

The burst cap + consumer-side stale drop live in mac/Sources/NotifyRelay.swift
(compile-gated); this suite pins the python half of the contract. sys.platform
is pinned where routing matters (same discipline as tests/test_platform.py),
so the suite behaves identically on the macOS and ubuntu CI jobs.
"""
import json
import os
import time
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - ensures the sandbox env is set first

from act.lib import config, notify


def _clear_queue():
    qdir = config.NOTIFY_QUEUE_DIR
    if qdir.exists():
        for f in qdir.iterdir():
            f.unlink()


class QueueWriteTestCase(unittest.TestCase):
    def setUp(self):
        _clear_queue()

    def test_format(self):
        path = notify._queue_write("标题", "正文", "sub")
        self.assertIsNotNone(path)
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["title"], "标题")
        self.assertEqual(data["body"], "正文")
        self.assertEqual(data["subtitle"], "sub")
        # filename is derived from the id — the app posts with identifier=id
        self.assertEqual(data["id"] + ".json", path.name)
        self.assertIsInstance(data["created_at"], int)
        self.assertAlmostEqual(data["created_at"], time.time(), delta=5)

    def test_subtitle_key_absent_when_not_given(self):
        path = notify._queue_write("t", "b")
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertNotIn("subtitle", data)

    def test_atomicity_no_tmp_leftover(self):
        notify._queue_write("t", "b")
        names = [p.name for p in config.NOTIFY_QUEUE_DIR.iterdir()]
        self.assertEqual(len(names), 1)
        self.assertFalse(any(n.endswith(".tmp") for n in names))

    def test_write_failure_returns_none_and_leaves_no_corpse(self):
        with mock.patch("act.lib.notify.os.replace", side_effect=OSError("boom")):
            self.assertIsNone(notify._queue_write("t", "b"))
        self.assertEqual(list(config.NOTIFY_QUEUE_DIR.iterdir()), [])


class NativeNotifyRoutingTestCase(unittest.TestCase):
    def setUp(self):
        _clear_queue()

    def test_darwin_is_relay_only(self):
        """The queue is THE native path — no timer, no pgrep, no osascript."""
        with mock.patch("sys.platform", "darwin"), \
             mock.patch("act.lib.platform.notify_user") as nu:
            self.assertTrue(notify._native_notify("t", "b", "s"))
        nu.assert_not_called()
        files = list(config.NOTIFY_QUEUE_DIR.glob("*.json"))
        self.assertEqual(len(files), 1)

    def test_queue_failure_means_no_notification_at_all(self):
        """NO fallback by owner decision: unwritable queue -> False, and the
        osascript seam is never touched."""
        with mock.patch("sys.platform", "darwin"), \
             mock.patch("act.lib.notify._queue_write", return_value=None), \
             mock.patch("act.lib.platform.notify_user") as nu:
            self.assertFalse(notify._native_notify("t", "b"))
        nu.assert_not_called()

    def test_non_darwin_skips_the_queue(self):
        with mock.patch("sys.platform", "linux"), \
             mock.patch("act.lib.platform.notify_user", return_value=True) as nu:
            self.assertTrue(notify._native_notify("t", "b"))
        nu.assert_called_once_with("t", "b", None)
        self.assertEqual(list(config.NOTIFY_QUEUE_DIR.glob("*.json")), [])

    def test_notify_routes_through_relay(self):
        with mock.patch("act.lib.notify._native_notify", return_value=True) as nn, \
             mock.patch("act.lib.notify._phone_mirror"):
            self.assertTrue(notify.notify("t", "b", subtitle="s"))
        nn.assert_called_once_with("t", "b", "s")


class StaleSweepTestCase(unittest.TestCase):
    """§28 write-time sweep: entries older than STALE_AFTER_S are deleted on
    the next write, so an always-closed app can't grow the dir forever."""

    def setUp(self):
        _clear_queue()

    def _age(self, path, seconds):
        old = time.time() - seconds
        os.utime(path, (old, old))

    def test_stale_sibling_swept_on_write(self):
        stale = notify._queue_write("old", "old")
        self._age(stale, notify.STALE_AFTER_S + 60)
        fresh = notify._queue_write("new", "new")
        remaining = list(config.NOTIFY_QUEUE_DIR.iterdir())
        self.assertEqual(remaining, [fresh])

    def test_fresh_sibling_survives_the_sweep(self):
        first = notify._queue_write("a", "a")
        second = notify._queue_write("b", "b")
        remaining = sorted(p.name for p in config.NOTIFY_QUEUE_DIR.iterdir())
        self.assertEqual(remaining, sorted([first.name, second.name]))

    def test_sweep_boundary_uses_injectable_clock(self):
        path = notify._queue_write("t", "b")
        qdir = config.NOTIFY_QUEUE_DIR
        just_fresh = time.time() + notify.STALE_AFTER_S - 5
        self.assertEqual(notify._sweep_stale(qdir, now=just_fresh), 0)
        self.assertTrue(path.exists())
        just_stale = time.time() + notify.STALE_AFTER_S + 5
        self.assertEqual(notify._sweep_stale(qdir, now=just_stale), 1)
        self.assertFalse(path.exists())

    def test_sweep_also_removes_tmp_corpses(self):
        qdir = config.NOTIFY_QUEUE_DIR
        qdir.mkdir(parents=True, exist_ok=True)
        corpse = qdir / "dead.json.tmp"   # crash mid-write leftover
        corpse.write_text("{", encoding="utf-8")
        self._age(corpse, notify.STALE_AFTER_S + 60)
        notify._sweep_stale(qdir)
        self.assertFalse(corpse.exists())

    def test_sweep_never_raises_on_missing_dir(self):
        missing = config.NOTIFY_QUEUE_DIR / "no-such-subdir"
        self.assertEqual(notify._sweep_stale(missing), 0)


if __name__ == "__main__":
    unittest.main()
