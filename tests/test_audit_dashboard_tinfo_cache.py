"""Audit regression — dashboard transcript-info memoization (finding 56).

The dashboard used to call executor._transcript_info — a full read + per-line
json parse of the session transcript — for EVERY executing/review/delivered
card without a live pid, on EVERY ~10s pass. Delivered cards are never
auto-archived, so that set grows forever: unbounded IO per pass, ending in a
false "后台服务可能没在运行" banner once a pass outlasts the app's freshness
window. _transcript_info_cached memoizes per session id, validated by the
(path, mtime_ns, size) signature of the transcript files the lookup would
scan, and falls through UNCACHED whenever no signature can be computed —
never a stale answer, never a guess.

Runs entirely inside the sandbox AIASSISTANT_HOME (tests/__init__.py).
"""
import os
import tempfile
import sys
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import executor
from act.lib import config, dashboard
from act.lib.registry import Requirement

SID = "deadbeef-cafe-4000-8000-feedfacebeef"


class TinfoCacheUnitTestCase(unittest.TestCase):
    def setUp(self):
        dashboard._TINFO_CACHE.clear()
        self.calls = []
        patcher = mock.patch.object(
            executor, "_transcript_info",
            side_effect=lambda sid: (self.calls.append(sid) or ("full-" + sid,
                                                                "/tmp/cwd")))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(dashboard._TINFO_CACHE.clear)

    def test_cache_hits_while_signature_unchanged(self):
        with mock.patch.object(dashboard, "_transcript_sig",
                               return_value=(("t.jsonl", 1, 100),)):
            first = dashboard._transcript_info_cached(SID)
            second = dashboard._transcript_info_cached(SID)
        self.assertEqual(first, ("full-" + SID, "/tmp/cwd"))
        self.assertEqual(second, first)
        self.assertEqual(len(self.calls), 1)  # parsed exactly once

    def test_cache_invalidates_when_signature_changes(self):
        sigs = iter([(("t.jsonl", 1, 100),), (("t.jsonl", 2, 250),)])
        with mock.patch.object(dashboard, "_transcript_sig",
                               side_effect=lambda sid: next(sigs)):
            dashboard._transcript_info_cached(SID)
            dashboard._transcript_info_cached(SID)
        self.assertEqual(len(self.calls), 2)  # mtime/size moved -> re-parse

    def test_no_caching_without_a_signature(self):
        # sig None = short sid or OSError: fall through uncached, never stale
        with mock.patch.object(dashboard, "_transcript_sig", return_value=None):
            dashboard._transcript_info_cached(SID)
            dashboard._transcript_info_cached(SID)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(dashboard._TINFO_CACHE, {})

    def test_negative_result_is_cached_too(self):
        # a missing transcript (info=None) is as expensive to recompute
        with mock.patch.object(executor, "_transcript_info",
                               side_effect=lambda sid: (self.calls.append(sid)
                                                        or None)), \
                mock.patch.object(dashboard, "_transcript_sig",
                                  return_value=()):
            self.assertIsNone(dashboard._transcript_info_cached(SID))
            self.assertIsNone(dashboard._transcript_info_cached(SID))
        self.assertEqual(len(self.calls), 1)

    def test_cache_stays_bounded(self):
        with mock.patch.object(dashboard, "_transcript_sig",
                               return_value=()):
            for i in range(dashboard._TINFO_CACHE_MAX + 10):
                dashboard._transcript_info_cached(f"deadbee{i:03d}-x")
        self.assertLessEqual(len(dashboard._TINFO_CACHE),
                             dashboard._TINFO_CACHE_MAX)


class TinfoSigTestCase(unittest.TestCase):
    def test_short_sid_yields_no_signature(self):
        self.assertIsNone(dashboard._transcript_sig("abc"))
        self.assertIsNone(dashboard._transcript_sig(""))

    @unittest.skipIf(sys.platform.startswith("win"),
                     "transcript dir resolution unported on Windows "
                     "(HOME-based sandbox; same area as harvest)")
    def test_signature_tracks_the_transcript_file(self):
        home = tempfile.mkdtemp(prefix="tinfo-home-")
        proj = os.path.join(home, ".claude", "projects", "proj-x")
        os.makedirs(proj)
        path = os.path.join(proj, SID + ".jsonl")
        with mock.patch.dict(os.environ, {"HOME": home}):
            self.assertEqual(dashboard._transcript_sig(SID), ())  # no file yet
            with open(path, "w", encoding="utf-8") as fh:
                fh.write('{"cwd": "/tmp/wt"}\n')
            sig1 = dashboard._transcript_sig(SID)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write('{"cwd": "/tmp/wt2"}\n')
            sig2 = dashboard._transcript_sig(SID)
        self.assertEqual(len(sig1), 1)
        self.assertNotEqual(sig1, sig2)  # append -> size changed -> invalidate


class BuildDashboardUsesCacheTestCase(unittest.TestCase):
    """Integration: two consecutive passes over the same delivered card parse
    the transcript once (HOME is pointed at an empty dir, same convention as
    test_dashboard.py, so the real glob deterministically finds nothing)."""

    def setUp(self):
        dashboard._TINFO_CACHE.clear()
        self.addCleanup(dashboard._TINFO_CACHE.clear)
        home = tempfile.mkdtemp(prefix="dash-cache-home-")
        patcher = mock.patch.dict(os.environ, {"HOME": home})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_second_pass_reuses_the_first_lookup(self):
        req = Requirement.from_dict({
            "id": "R-100", "title": "已交付卡", "status": "delivered",
            "execution": {"session_id": SID,
                          "accepted_at": "2026-07-01T00:00:00Z"},
        })
        cfg = config.Config()
        with mock.patch.object(executor, "_transcript_info",
                               return_value=None) as ti:
            dashboard.build_dashboard(reqs=[req], agents=[], cfg=cfg,
                                      archived=[])
            dashboard.build_dashboard(reqs=[req], agents=[], cfg=cfg,
                                      archived=[])
        self.assertEqual(ti.call_count, 1)


if __name__ == "__main__":
    unittest.main()
