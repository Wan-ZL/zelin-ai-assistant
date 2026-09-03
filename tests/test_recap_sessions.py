"""§63 deterministic meeting-session detection (act/lib/recap_sessions.py).

Pins the owner's rules from issue #129: meeting-app table (empty window
accepted, Slack only with Huddle), per-minute buckets, gap > 5 min splits,
frames ∪ audio bridge each other, presence ≥ 3 frames + span ≥ 10 min,
CLOSED = quiet ≥ 5 min ∧ no pending chunk in the interval, forced close at
120 min, > 4 h segments, ``meeting:<PT start minute>-<app>`` keys, id-range
reads with a cursor, and late slices. Fixture DB: tests/recap_fixture.py.
"""
import tempfile
import unittest
from pathlib import Path

from tests import TMP_HOME  # noqa: F401 - sandbox env first
from tests import recap_fixture as fx

from act.lib import recap_sessions as rs

TZ = "America/Los_Angeles"


class MatchAppTestCase(unittest.TestCase):
    def test_default_table(self):
        self.assertEqual(rs.match_app("zoom.us", "Zoom Meeting", None), "zoom")
        self.assertEqual(rs.match_app("zoom.us", "", None), "zoom")          # blank window accepted
        self.assertEqual(rs.match_app("zoom.us", None, None), "zoom")
        self.assertEqual(rs.match_app("Microsoft Teams", "Chat | Teams", None), "teams")
        self.assertEqual(rs.match_app("Webex", "", None), "webex")
        self.assertEqual(rs.match_app("FaceTime", "", None), "facetime")
        self.assertEqual(rs.match_app("Google Chrome", "Meet – abc", "https://meet.google.com/abc-defg"), "meet")
        self.assertEqual(rs.match_app("Slack", "Huddle: #ml-infra", None), "slack-huddle")

    def test_non_meetings(self):
        self.assertIsNone(rs.match_app("Slack", "#general – Slack", None))   # Slack without Huddle
        self.assertIsNone(rs.match_app("Google Chrome", "GitHub", "https://github.com/x"))
        self.assertIsNone(rs.match_app(None, None, None))

    def test_config_rules_extend_the_table(self):
        opts = rs.Options.from_mapping({"meeting_windows": [
            {"slug": "gather", "app": "Gather"}, {"bad": 1}, "junk", {"slug": "", "app": "x"},
            {"slug": "noop"}]})
        self.assertEqual(rs.match_app("Gather", "", None, opts.rules), "gather")
        self.assertEqual(len(opts.rules), len(rs.DEFAULT_MEETING_RULES) + 1)


class OptionsTestCase(unittest.TestCase):
    def test_defaults(self):
        o = rs.Options.from_mapping(None)
        self.assertEqual((o.gap_s, o.quiet_s, o.min_span_s, o.min_frames), (300, 300, 600, 3))
        self.assertEqual((o.force_close_s, o.max_session_s), (7200, 4 * 3600))
        self.assertFalse(o.audio_only)
        self.assertEqual(o.timezone, rs.DEFAULT_TIMEZONE)

    def test_minutes_and_bad_values(self):
        o = rs.Options.from_mapping({"gap_minutes": 7, "quiet_minutes": "x", "min_span_minutes": -1,
                                     "min_presence_frames": "5", "audio_only_sessions": True,
                                     "timezone": "Asia/Shanghai", "force_close_minutes": True})
        self.assertEqual(o.gap_s, 420)
        self.assertEqual(o.quiet_s, 300)       # "x" keeps the default
        self.assertEqual(o.min_span_s, 600)    # negative keeps the default
        self.assertEqual(o.min_frames, 5)
        self.assertTrue(o.audio_only)
        self.assertEqual(o.timezone, "Asia/Shanghai")
        self.assertEqual(o.force_close_s, 7200)  # a bool is not a minute count
        self.assertEqual(rs.int_or(True, 3), 3)
        self.assertEqual(rs.int_or("9", 3), 9)


class TimestampTestCase(unittest.TestCase):
    def test_parse_variants(self):
        t = rs.parse_ts("2026-08-31T19:56:00.123456+00:00")
        self.assertAlmostEqual(t, fx.T0, delta=0.2)
        self.assertAlmostEqual(rs.parse_ts("2026-08-31T19:56:00Z"), fx.T0)
        self.assertAlmostEqual(rs.parse_ts("2026-08-31 19:56:00"), fx.T0)   # naive = UTC
        self.assertIsNone(rs.parse_ts(""))
        self.assertIsNone(rs.parse_ts(None))
        self.assertIsNone(rs.parse_ts("yesterday"))

    @unittest.skipUnless(fx.HAS_TZDATA, "no tz database on this interpreter (Windows without tzdata)")
    def test_meeting_key_uses_local_start_minute(self):
        self.assertEqual(rs.meeting_key(fx.T0, "zoom", TZ), "meeting:2026-08-31T1256-zoom")
        self.assertEqual(rs.meeting_key(fx.T0 + 30, "zoom", TZ), "meeting:2026-08-31T1256-zoom")
        self.assertEqual(rs.meeting_key(fx.T0, "zoom", "Asia/Shanghai"), "meeting:2026-09-01T0356-zoom")

    def test_unknown_timezone_falls_back_to_local(self):
        self.assertIsNotNone(rs.tzinfo_for("Mars/Olympus"))
        self.assertTrue(rs.meeting_key(fx.T0, "zoom", "Mars/Olympus").startswith("meeting:2026-"))

    def test_iso_utc_and_bucket(self):
        self.assertEqual(rs.iso_utc(fx.T0), "2026-08-31T19:56:00Z")
        self.assertEqual(rs.minute_bucket(fx.T0 + 59), int(fx.T0))


def _frames(start, minutes, app="zoom", n=2):
    return [(int(start + m * 60), rs.FRAME, app, n) for m in range(minutes)]


def _audio(start, minutes, n=2):
    return [(int(start + m * 60), rs.AUDIO, None, n) for m in range(minutes)]


class ClusterTestCase(unittest.TestCase):
    def setUp(self):
        self.opts = rs.Options()

    def test_merge_buckets_sums_same_minute(self):
        merged = rs.merge_buckets([(60, rs.FRAME, "zoom", 1), (60, rs.FRAME, "zoom", 1),
                                   (60, rs.AUDIO, None, 1), (0, rs.FRAME, "zoom", 1)])
        self.assertEqual(merged, [[0, rs.FRAME, "zoom", 1], [60, rs.AUDIO, None, 1],
                                  [60, rs.FRAME, "zoom", 2]])

    def test_gap_over_five_minutes_splits(self):
        events = _frames(fx.T0, 10) + _frames(fx.T0 + 16 * 60, 10)   # 6-min hole
        self.assertEqual(len(rs.cluster(events, 300)), 2)
        events = _frames(fx.T0, 10) + _frames(fx.T0 + 14 * 60, 10)   # 4-min hole bridges
        self.assertEqual(len(rs.cluster(events, 300)), 1)

    def test_audio_bridges_a_frame_hole(self):
        events = _frames(fx.T0, 5) + _audio(fx.T0 + 5 * 60, 8) + _frames(fx.T0 + 13 * 60, 5)
        sessions = rs.sessions_from(events, self.opts)
        self.assertEqual(len(sessions), 1)
        s = sessions[0]
        self.assertEqual((s.frames, s.audio_rows, s.app), (20, 16, "zoom"))
        self.assertEqual(s.span_s, 18 * 60)   # last bucket + 60 s

    def test_dominant_app_and_audio_only(self):
        s = rs.describe(_frames(fx.T0, 3, "zoom") + _frames(fx.T0 + 180, 3, "teams", n=5))
        self.assertEqual(s.app, "teams")
        self.assertEqual(rs.describe(_audio(fx.T0, 12)).app, rs.AUDIO_ONLY_APP)

    def test_eligibility(self):
        self.assertTrue(rs.describe(_frames(fx.T0, 10)).eligible(self.opts))
        self.assertFalse(rs.describe(_frames(fx.T0, 9)).eligible(self.opts))          # span 9 min
        self.assertFalse(rs.describe(_frames(fx.T0, 1, n=2) + _audio(fx.T0 + 60, 12)).eligible(self.opts))
        audio_only = rs.describe(_audio(fx.T0, 12))
        self.assertFalse(audio_only.eligible(self.opts))
        self.assertTrue(audio_only.eligible(rs.Options(audio_only=True)))

    def test_long_session_is_cut_into_four_hour_segments(self):
        events = _frames(fx.T0, 5 * 60)   # 5 hours, no gap
        sessions = rs.sessions_from(events, self.opts)
        self.assertEqual([round(s.span_s / 3600, 2) for s in sessions], [4.0, 1.0])
        self.assertEqual(sessions[1].start, fx.T0 + 4 * 3600)

    def test_verdict(self):
        s = rs.describe(_frames(fx.T0, 20))       # ends T0+20min
        end = s.end
        self.assertEqual(rs.verdict(s, end + 299, 0, self.opts), rs.OPEN)
        self.assertEqual(rs.verdict(s, end + 300, 0, self.opts), rs.CLOSED)
        self.assertEqual(rs.verdict(s, end + 3000, 1, self.opts), rs.OPEN)     # pending holds it
        self.assertEqual(rs.verdict(s, end + 7200, 1, self.opts), rs.CLOSED)   # forced at 120 min

    def test_late_slices_only_catch_audio_inside_closed_intervals(self):
        intervals = [("meeting:2026-08-31T1256-zoom", fx.T0, fx.T0 + 20 * 60)]
        new = [(int(fx.T0 + 600), rs.AUDIO, None, 3),           # inside → late slice
               (int(fx.T0 + 600), rs.FRAME, "zoom", 1),         # frames never count
               (int(fx.T0 + 20 * 60 + 200), rs.AUDIO, None, 1),  # within gap tolerance
               (int(fx.T0 + 3 * 3600), rs.AUDIO, None, 1)]      # elsewhere
        hits, rest = rs.late_slices(new, intervals, 300)
        self.assertEqual(hits, {"meeting:2026-08-31T1256-zoom": 4})
        self.assertEqual(len(rest), 2)


class SqliteReaderTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-recap-db-")
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "db.sqlite"
        self.w = fx.make_db(self.path)
        self.addCleanup(self.w.close)

    def test_readonly_connection_cannot_write(self):
        ro = rs.connect_readonly(self.path)
        self.addCleanup(ro.close)
        import sqlite3
        with self.assertRaises(sqlite3.OperationalError):
            ro.execute("INSERT INTO audio_chunks(timestamp) VALUES ('x')")

    def test_frame_events_follow_the_cursor_and_the_table(self):
        fx.add_frames(self.w, fx.T0, 2, app="Google Chrome", window="GitHub", url="https://github.com")
        fx.add_frames(self.w, fx.T0, 3, app="zoom.us", window="")
        self.assertEqual(rs.max_ids(self.w), {"frames": 10, "audio": 0})
        events, last = rs.read_frame_events(self.w, 0)
        self.assertEqual(last, 10)
        self.assertEqual(len(events), 6)
        self.assertTrue(all(e[1] == rs.FRAME and e[2] == "zoom" for e in events))
        again, last2 = rs.read_frame_events(self.w, last)
        self.assertEqual((again, last2), ([], 10))
        # LIMIT stops the cursor mid-table; the rest arrives next round
        part, mid = rs.read_frame_events(self.w, 0, limit=5)
        self.assertEqual(mid, 5)
        rest, end = rs.read_frame_events(self.w, mid, limit=5)
        self.assertEqual(end, 10)
        self.assertEqual(len(part) + len(rest), 6)

    def test_audio_events_skip_blank_rows(self):
        fx.add_audio(self.w, fx.T0, 2)
        fx.add_audio(self.w, fx.T0 + 600, 1, text="   ")
        events, last = rs.read_audio_events(self.w, 0)
        self.assertEqual(len(events), 4)
        self.assertEqual(last, 6)

    def test_pending_chunks_inside_interval_only(self):
        legacy = fx.add_pending_chunk(self.w, fx.T0 - 100 * 86400)   # the 2026-05 legacy rows
        inside = fx.add_pending_chunk(self.w, fx.T0 + 600)
        fx.add_pending_chunk(self.w, fx.T0 + 5 * 3600)
        self.assertEqual(rs.pending_chunks_between(self.w, fx.T0, fx.T0 + 1200), 1)
        fx.settle_chunk(self.w, inside)
        self.assertEqual(rs.pending_chunks_between(self.w, fx.T0, fx.T0 + 1200), 0)
        self.assertGreater(legacy, 0)

    def test_transcript_between_is_time_ordered_and_bounded(self):
        fx.add_audio(self.w, fx.T0 + 600, 1, text="second")
        fx.add_audio(self.w, fx.T0, 1, text="first", rows_per_minute=1)
        fx.add_audio(self.w, fx.T0 + 7200, 1, text="later")
        text = rs.transcript_between(self.w, fx.T0, fx.T0 + 1200)
        self.assertEqual(text.split("\n"), ["first", "second", "second"])
        self.assertEqual(rs.transcript_between(self.w, fx.T0 + 3600, fx.T0 + 3660), "")


if __name__ == "__main__":
    unittest.main()
