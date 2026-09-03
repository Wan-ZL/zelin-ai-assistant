"""§28 relay queue sweep — the edges tests/test_notify_relay.py leaves open.

Nightly mutants surviving in ``notify._sweep_stale`` / ``_queue_write``
(2026-09-02): the exact-boundary entry (mtime == cutoff) must SURVIVE; a
sibling that vanishes between stat and unlink (the app deleting it first) is
still counted as removed (``missing_ok``); a stat failure on one entry must
not end the sweep for the rest (``continue``, not ``break``); an unreadable
queue dir sweeps nothing and never raises; and the JSON entry keeps CJK text
verbatim (``ensure_ascii=False`` — the app relay reads it, humans debug it).
Fake entries stand in for filesystem races that cannot be staged for real.
"""
import os
import time
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - ensures the sandbox env is set first

from act.lib import config, notify


class _Stat:
    def __init__(self, mtime):
        self.st_mtime = mtime


class _Entry:
    """A queue file whose stat / unlink behaviour is scripted."""

    def __init__(self, mtime, stat_error=None, vanished=False):
        self._mtime, self._stat_error, self._vanished = mtime, stat_error, vanished
        self.unlinked = []

    def stat(self):
        if self._stat_error is not None:
            raise self._stat_error
        return _Stat(self._mtime)

    def unlink(self, missing_ok=False):
        self.unlinked.append(missing_ok)
        if self._vanished and not missing_ok:
            raise FileNotFoundError(2, "raced with the app")


class _Dir:
    def __init__(self, entries=None, error=None):
        self._entries, self._error = entries or [], error

    def iterdir(self):
        if self._error is not None:
            raise self._error
        return iter(self._entries)


class SweepEdgeTestCase(unittest.TestCase):
    NOW = 1_800_000_000.0

    def test_entry_exactly_at_the_cutoff_survives(self):
        exact = _Entry(self.NOW - notify.STALE_AFTER_S)
        older = _Entry(self.NOW - notify.STALE_AFTER_S - 0.001)
        removed = notify._sweep_stale(_Dir([exact, older]), now=self.NOW)
        self.assertEqual(removed, 1)
        self.assertEqual(exact.unlinked, [])
        self.assertEqual(older.unlinked, [True])   # missing_ok=True: race-safe

    def test_entry_deleted_by_the_app_first_still_counts_as_removed(self):
        gone = _Entry(self.NOW - 2 * notify.STALE_AFTER_S, vanished=True)
        self.assertEqual(notify._sweep_stale(_Dir([gone]), now=self.NOW), 1)

    def test_stat_failure_on_one_entry_does_not_end_the_sweep(self):
        broken = _Entry(0, stat_error=OSError("stat failed"))
        stale = _Entry(self.NOW - 2 * notify.STALE_AFTER_S)
        self.assertEqual(notify._sweep_stale(_Dir([broken, stale]), now=self.NOW), 1)
        self.assertEqual(stale.unlinked, [True])

    def test_unreadable_queue_dir_sweeps_nothing_and_never_raises(self):
        self.assertEqual(notify._sweep_stale(_Dir(error=OSError("no dir")), now=self.NOW), 0)

    def test_default_clock_is_wall_time(self):
        qdir = config.NOTIFY_QUEUE_DIR
        qdir.mkdir(parents=True, exist_ok=True)
        for f in qdir.iterdir():
            f.unlink()
        path = notify._queue_write("t", "b")
        old = time.time() - notify.STALE_AFTER_S - 60
        os.utime(path, (old, old))
        self.assertEqual(notify._sweep_stale(qdir), 1)
        self.assertFalse(path.exists())


class QueueEntryEncodingTestCase(unittest.TestCase):
    def setUp(self):
        qdir = config.NOTIFY_QUEUE_DIR
        qdir.mkdir(parents=True, exist_ok=True)
        for f in qdir.iterdir():
            f.unlink()

    def test_cjk_is_written_verbatim_not_escaped(self):
        path = notify._queue_write("有新需求待审批", "正文 —— 打开面板", "副标题")
        raw = path.read_text(encoding="utf-8")
        self.assertIn("有新需求待审批", raw)
        self.assertIn("副标题", raw)
        self.assertNotIn("\\u", raw)

    def test_tmp_write_failure_returns_none(self):
        with mock.patch("act.lib.notify.Path.write_text", side_effect=OSError("disk full")):
            self.assertIsNone(notify._queue_write("t", "b"))
        self.assertEqual(list(config.NOTIFY_QUEUE_DIR.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
