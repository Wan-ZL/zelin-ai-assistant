"""sources.is_stale — the liveness verdict's own edges (CONTRACT §48.3).

Pins the P3b split: unknown source / non-dict entry → False, no parseable
stamp → False, the NEWER of last_ok / last_attempt is the signal, the
threshold is a strict ``>``, and ``now`` defaults to the wall clock.
"""
import datetime as _dt
import unittest
from unittest import mock

from act.lib import sources


def _iso(dt: _dt.datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


NOW = _dt.datetime(2026, 9, 2, 12, 0, 0, tzinfo=_dt.timezone.utc)


class LatestSignalTestCase(unittest.TestCase):
    def test_newer_stamp_wins_regardless_of_key(self):
        older, newer = NOW - _dt.timedelta(hours=2), NOW - _dt.timedelta(hours=1)
        self.assertEqual(sources._latest_signal({"last_ok": _iso(older),
                                                 "last_attempt": _iso(newer)}), newer)
        self.assertEqual(sources._latest_signal({"last_ok": _iso(newer),
                                                 "last_attempt": _iso(older)}), newer)

    def test_single_or_bad_stamps(self):
        only = NOW - _dt.timedelta(minutes=5)
        self.assertEqual(sources._latest_signal({"last_attempt": _iso(only)}), only)
        self.assertEqual(sources._latest_signal({"last_ok": "garbage", "last_attempt": 12}), None)
        self.assertIsNone(sources._latest_signal({}))


class IsStaleTestCase(unittest.TestCase):
    def test_unknown_source_or_bad_entry_is_false(self):
        fresh = {"last_ok": _iso(NOW - _dt.timedelta(days=30))}
        self.assertFalse(sources.is_stale("screenpipe", fresh, now=NOW))
        self.assertFalse(sources.is_stale("gmail", None, now=NOW))
        self.assertFalse(sources.is_stale("gmail", ["not", "a", "dict"], now=NOW))

    def test_no_baseline_is_false(self):
        self.assertFalse(sources.is_stale("gmail", {}, now=NOW))
        self.assertFalse(sources.is_stale("gmail", {"last_ok": None, "last_attempt": ""}, now=NOW))

    def test_threshold_is_strict(self):
        threshold = sources.LIVENESS_THRESHOLDS["gmail"]
        at = {"last_ok": _iso(NOW - _dt.timedelta(seconds=threshold))}
        self.assertFalse(sources.is_stale("gmail", at, now=NOW))
        over = {"last_ok": _iso(NOW - _dt.timedelta(seconds=threshold + 1))}
        self.assertTrue(sources.is_stale("gmail", over, now=NOW))

    def test_newer_attempt_keeps_a_failing_radar_alive(self):
        threshold = sources.LIVENESS_THRESHOLDS["slack"]
        entry = {"last_ok": _iso(NOW - _dt.timedelta(seconds=threshold * 5)),
                 "last_attempt": _iso(NOW - _dt.timedelta(seconds=10))}
        self.assertFalse(sources.is_stale("slack", entry, now=NOW))

    def test_now_defaults_to_wall_clock(self):
        threshold = sources.LIVENESS_THRESHOLDS["gmail"]
        entry = {"last_ok": _iso(NOW - _dt.timedelta(seconds=threshold + 60))}

        class _Clock(_dt.datetime):
            @classmethod
            def now(cls, tz=None):
                return NOW

        with mock.patch.object(sources._dt, "datetime", _Clock):
            self.assertTrue(sources.is_stale("gmail", entry))
            recent = {"last_ok": _iso(NOW - _dt.timedelta(seconds=1))}
            self.assertFalse(sources.is_stale("gmail", recent))


if __name__ == "__main__":
    unittest.main()
