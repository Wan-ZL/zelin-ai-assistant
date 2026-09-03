"""§63 recap runner end to end (act/recap.py) on a fixture screenpipe DB.

One real meeting → exactly one recap (the P5b done-criterion): first run sets
the marker and backfills nothing; an in-progress meeting is OPEN (no model
call); the 13:00 OPEN and the 13:30 CLOSED rounds share one key; pending
transcripts hold the close, the 120-min force overrides them; a late audio
slice regenerates as version 2 with the old text in history; thin / silent
meetings never reach the model; validator failures retry once then land as
需复核; a crashing model call is retried across rounds and given up after
three; per-run / per-day caps hold meetings for the next round; retention
prunes; 「重新生成」carries the owner's note; 「现在生成」on an OPEN session
is partial. The fake runner stands in for claude (tests never spawn it).
"""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first
from tests import recap_fixture as fx

from act import recap
from act.lib import config, notify
from act.lib import recap_sessions as rs
from act.lib import recap_store as store

KEY = fx.KEY
MIN = 60.0


class FakeRunner:
    """llm.run's runner seam: records argv, replies from a queue."""

    def __init__(self, *replies):
        self.replies = list(replies) or [fx.good_output()]
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), dict(kwargs)))
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(reply, Exception):
            raise reply
        return subprocess.CompletedProcess(argv, 0, stdout=reply, stderr="")


class RecapCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-recap-")
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        (root / "state").mkdir()
        self.addCleanup(mock.patch.object(config, "STATE_DIR", root / "state").start)
        mock.patch.object(config, "STATE_DIR", root / "state").start()
        self.addCleanup(mock.patch.stopall)
        self.notified = []
        mock.patch.object(notify, "notify", side_effect=lambda *a, **k: self.notified.append((a, k)) or True).start()
        self.conn = fx.make_db(root / "db.sqlite")
        self.addCleanup(self.conn.close)
        self.cfg = config.Config(raw={"recap": {}})
        self.runner = FakeRunner()

    def run_once(self, now, **kw):
        return recap.run_once(now=now, conn=self.conn, runner=self.runner, cfg=self.cfg, **kw)

    def first_run(self, now=fx.T0 - 3600):
        summary = self.run_once(now)
        self.assertTrue(summary["first_run"])
        return summary

    def meeting(self, minutes=20, audio=True, start=fx.T0):
        fx.add_frames(self.conn, start, minutes)
        if audio:
            fx.add_audio(self.conn, start, minutes)


class FirstRunTestCase(RecapCase):
    def test_marker_is_now_no_backfill(self):
        self.meeting()                          # a meeting that happened BEFORE install
        summary = self.first_run(now=fx.T0 + 3600)
        state = store.load_state()
        self.assertEqual(state["cursor"], rs.max_ids(self.conn))
        self.assertEqual(summary["generated"], 0)
        self.assertEqual(self.runner.calls, [])
        # the next round sees nothing new: that meeting is history
        summary = self.run_once(fx.T0 + 7200)
        self.assertEqual((summary["generated"], summary["open"]), (0, 0))
        self.assertEqual(store.list_recaps(), [])

    def test_disabled_and_missing_db(self):
        self.cfg.recap_enabled = False
        self.assertEqual(self.run_once(fx.T0)["skipped"], "disabled")
        self.cfg.recap_enabled = True
        self.cfg.raw["recap"]["db_path"] = str(Path(self.tmp.name) / "nope.sqlite")
        self.assertEqual(recap.run_once(now=fx.T0, runner=self.runner, cfg=self.cfg)["skipped"], "no_db")


class SessionLifecycleTestCase(RecapCase):
    def test_open_then_closed_is_one_recap(self):
        self.first_run()
        self.meeting(minutes=20)                                   # 12:56–13:16
        # 13:00 round: still going (last frame 3 min ago) → OPEN, no model call
        summary = self.run_once(fx.T0 + 19 * MIN)
        self.assertEqual((summary["open"], summary["generated"]), (1, 0))
        self.assertEqual(self.runner.calls, [])
        state = store.load_state()
        self.assertEqual(state["open"][0]["key"], KEY)
        self.assertEqual(state["open"][0]["status"], "open")
        self.assertEqual(store.projection()[0]["status"], "open")
        # 13:30 round: quiet ≥ 5 min, nothing pending → CLOSED → one call, one file
        summary = self.run_once(fx.T0 + 34 * MIN)
        self.assertEqual((summary["open"], summary["generated"]), (0, 1))
        self.assertEqual(len(self.runner.calls), 1)
        rec = store.load_recap(KEY)
        self.assertEqual(rec["status"], "closed")
        self.assertEqual(rec["version"], 1)
        self.assertEqual(rec["quality"], "ok")
        self.assertEqual(rec["en"][0], "Decided: the training run moves to the new data mix from Monday")
        self.assertEqual(rec["zh"][4], "待定：无")
        self.assertEqual(rec["duration_min"], 20)
        self.assertEqual(rec["frames"], 40)
        self.assertEqual(len(self.notified), 1)
        self.assertEqual(self.notified[0][1].get("kind"), recap.NOTIFY_KIND)
        self.assertEqual(store.load_state()["events"], [])       # buffer drained
        # a third round adds nothing: exactly one recap for one meeting
        self.run_once(fx.T0 + 64 * MIN)
        self.assertEqual(len(store.list_recaps()), 1)
        self.assertEqual(len(self.runner.calls), 1)

    def test_pending_transcript_holds_the_close_until_settled(self):
        self.first_run()
        self.meeting(minutes=20)
        chunk = fx.add_pending_chunk(self.conn, fx.T0 + 15 * MIN)
        summary = self.run_once(fx.T0 + 34 * MIN)
        self.assertEqual((summary["open"], summary["generated"]), (1, 0))
        fx.settle_chunk(self.conn, chunk)
        summary = self.run_once(fx.T0 + 64 * MIN)
        self.assertEqual(summary["generated"], 1)

    def test_forced_close_after_120_minutes_despite_pending(self):
        self.first_run()
        self.meeting(minutes=20)
        fx.add_pending_chunk(self.conn, fx.T0 + 15 * MIN)
        self.assertEqual(self.run_once(fx.T0 + 60 * MIN)["generated"], 0)
        self.assertEqual(self.run_once(fx.T0 + 20 * MIN + 121 * MIN)["generated"], 1)

    def test_gap_over_five_minutes_makes_two_meetings(self):
        self.first_run()
        self.meeting(minutes=12, start=fx.T0)
        self.meeting(minutes=12, start=fx.T0 + 20 * MIN)
        summary = self.run_once(fx.T0 + 60 * MIN)
        self.assertEqual(summary["generated"], 2)
        self.assertEqual(sorted(r["key"] for r in store.list_recaps()),
                         sorted([fx.KEY, fx.key_at(fx.T0 + 20 * MIN)]))

    def test_short_or_sparse_presence_is_not_a_meeting(self):
        self.first_run()
        fx.add_frames(self.conn, fx.T0, 8)                 # 8 minutes: span < 10
        fx.add_frames(self.conn, fx.T0 + 3600, 1, per_minute=2)   # 2 frames: presence < 3
        summary = self.run_once(fx.T0 + 2 * 3600)
        self.assertEqual((summary["open"], summary["generated"]), (0, 0))
        self.assertEqual(store.load_state()["events"], [])   # dropped, not held forever

    def test_late_audio_slice_regenerates_as_version_two(self):
        self.first_run()
        self.meeting(minutes=20)
        self.run_once(fx.T0 + 34 * MIN)
        self.assertEqual(store.load_recap(KEY)["version"], 1)
        # the 12:48–12:55 audio landed in the 13:30 dump: rows inside the interval
        fx.add_audio(self.conn, fx.T0 + 2 * MIN, 3, text="late words arrive here now ok")
        summary = self.run_once(fx.T0 + 64 * MIN)
        self.assertEqual(summary["regenerated"], 1)
        rec = store.load_recap(KEY)
        self.assertEqual(rec["version"], 2)
        self.assertEqual(len(rec["history"]), 1)
        self.assertEqual(rec["history"][0]["version"], 1)
        self.assertEqual(store.projection()[0]["history_count"], 1)
        self.assertNotIn("history", store.projection()[0])
        self.assertEqual(len(self.runner.calls), 2)


class GenerationQualityTestCase(RecapCase):
    def test_no_audio_and_thin_transcripts_skip_the_model(self):
        self.first_run()
        self.meeting(minutes=20, audio=False)
        self.run_once(fx.T0 + 34 * MIN)
        rec = store.load_recap(KEY)
        self.assertEqual((rec["quality"], rec["en"], rec["transcript_words"]), ("no_audio", None, 0))
        self.assertEqual(self.runner.calls, [])
        self.assertEqual(self.notified, [])                 # nothing to copy = no ping
        fx.add_frames(self.conn, fx.T0 + 3600, 15)
        fx.add_audio(self.conn, fx.T0 + 3600, 2, text="few words")
        self.run_once(fx.T0 + 3600 + 30 * MIN)
        thin = store.load_recap(fx.key_at(fx.T0 + 3600))
        self.assertEqual(thin["quality"], "thin_transcript")
        self.assertEqual(self.runner.calls, [])

    def test_validator_failure_retries_once_then_needs_review(self):
        bad = fx.good_output(en_tail=" as Arash said")
        self.runner = FakeRunner(bad, bad)
        self.first_run()
        self.meeting()
        self.run_once(fx.T0 + 34 * MIN)
        self.assertEqual(len(self.runner.calls), 2)
        self.assertIn("violated these rules", self.runner.calls[1][0][2])
        rec = store.load_recap(KEY)
        self.assertEqual(rec["quality"], "needs_review")
        self.assertTrue(rec["en"][0].endswith("as Arash said"))   # still copyable

    def test_retry_that_passes_is_ok(self):
        self.runner = FakeRunner(fx.good_output(en_tail=" as Arash said"), fx.good_output())
        self.first_run()
        self.meeting()
        self.run_once(fx.T0 + 34 * MIN)
        self.assertEqual(store.load_recap(KEY)["quality"], "ok")

    def test_unparsable_twice_is_generation_failed_but_recorded(self):
        self.runner = FakeRunner("nonsense", "still nonsense")
        self.first_run()
        self.meeting()
        self.run_once(fx.T0 + 34 * MIN)
        rec = store.load_recap(KEY)
        self.assertEqual((rec["quality"], rec["en"]), ("generation_failed", None))

    def test_crashing_model_is_retried_across_rounds_then_given_up(self):
        self.runner = FakeRunner(RuntimeError("boom"))
        self.first_run()
        self.meeting()
        for i in range(recap.MAX_GENERATION_FAILURES - 1):
            summary = self.run_once(fx.T0 + (34 + 30 * i) * MIN)
            self.assertEqual(summary["generated"], 0)
            self.assertIsNone(store.load_recap(KEY))
            self.assertEqual(store.load_state()["failures"][KEY], i + 1)
        summary = self.run_once(fx.T0 + 200 * MIN)
        self.assertEqual(summary["generated"], 1)
        self.assertEqual(store.load_recap(KEY)["quality"], "generation_failed")
        self.assertNotIn(KEY, store.load_state()["failures"])

    def test_prior_recaps_feed_the_prompt(self):
        self.first_run()
        self.meeting(minutes=15, start=fx.T0 - 3 * 86400)
        self.run_once(fx.T0 - 3 * 86400 + 30 * MIN)
        self.meeting(minutes=15, start=fx.T0)
        self.run_once(fx.T0 + 30 * MIN)
        prompt = self.runner.calls[1][0][2]
        self.assertIn("Prior recap dated 2026-08-28", prompt)


class CapsAndRetentionTestCase(RecapCase):
    def test_max_per_run_holds_the_rest_for_the_next_round(self):
        self.cfg.raw["recap"]["max_per_run"] = 1
        self.first_run()
        self.meeting(minutes=12, start=fx.T0)
        self.meeting(minutes=12, start=fx.T0 + 20 * MIN)
        self.assertEqual(self.run_once(fx.T0 + 60 * MIN)["generated"], 1)
        self.assertEqual(len(store.load_state()["events"]), 24)     # held meeting: 12 min × frame+audio
        self.assertEqual(self.run_once(fx.T0 + 90 * MIN)["generated"], 1)
        self.assertEqual(len(store.list_recaps()), 2)

    def test_max_per_day_counts_across_rounds(self):
        self.cfg.raw["recap"]["max_per_day"] = 1
        self.first_run()
        self.meeting(minutes=12, start=fx.T0)
        self.assertEqual(self.run_once(fx.T0 + 30 * MIN)["generated"], 1)
        self.meeting(minutes=12, start=fx.T0 + 60 * MIN)
        self.assertEqual(self.run_once(fx.T0 + 90 * MIN)["generated"], 0)    # today's cap
        self.assertEqual(self.run_once(fx.T0 + 20 * 3600)["generated"], 1)   # next local day

    def test_retention_prunes_old_recaps(self):
        self.cfg.raw["recap"]["retention_days"] = 10
        self.first_run()
        self.meeting(minutes=12)
        self.run_once(fx.T0 + 30 * MIN)
        self.assertEqual(len(store.list_recaps()), 1)
        summary = self.run_once(fx.T0 + 11 * 86400)
        self.assertEqual(summary["pruned"], 1)
        self.assertEqual(store.list_recaps(), [])


class InboxEntryPointsTestCase(RecapCase):
    def test_regenerate_with_note(self):
        self.first_run()
        self.meeting()
        self.run_once(fx.T0 + 34 * MIN)
        rec = recap.generate(KEY, note="the deadline is Friday", now=fx.T0 + 3600,
                             conn=self.conn, runner=self.runner, cfg=self.cfg)
        self.assertEqual(rec["version"], 2)
        self.assertEqual(rec["note"], "the deadline is Friday")
        self.assertIn("the deadline is Friday", self.runner.calls[1][0][2])
        self.assertEqual(store.load_recap(KEY)["history"][0]["version"], 1)

    def test_generate_now_on_an_open_session_is_partial_then_superseded(self):
        self.first_run()
        self.meeting(minutes=20)
        self.run_once(fx.T0 + 19 * MIN)                         # OPEN
        rec = recap.generate(KEY, now=fx.T0 + 20 * MIN, conn=self.conn, runner=self.runner, cfg=self.cfg)
        self.assertTrue(rec["partial"])
        self.assertEqual(rec["status"], "open")
        self.assertIn("IN PROGRESS", self.runner.calls[0][0][2])
        self.assertEqual(store.projection()[0]["partial"], True)
        self.run_once(fx.T0 + 34 * MIN)                         # CLOSED overrides the partial
        rec = store.load_recap(KEY)
        self.assertEqual((rec["status"], rec["partial"], rec["version"]), ("closed", False, 2))
        self.assertTrue(rec["history"][0]["partial"])

    def test_unknown_key_is_a_quiet_none(self):
        self.first_run()
        self.assertIsNone(recap.generate("meeting:2026-01-01T0000-zoom", conn=self.conn,
                                         runner=self.runner, cfg=self.cfg))
        self.assertEqual(self.runner.calls, [])


class LockTestCase(RecapCase):
    def test_second_holder_gives_up_immediately(self):
        with recap.Lock(0.0) as first:
            self.assertTrue(first)
            with recap.Lock(0.0) as second:
                self.assertEqual(second, recap.fcntl is None)   # False wherever flock exists


class CliTestCase(RecapCase):
    def test_once_without_a_db_exits_zero(self):
        self.cfg.raw["recap"]["db_path"] = str(Path(self.tmp.name) / "missing.sqlite")
        with mock.patch.object(config, "load_config", return_value=self.cfg):
            self.assertEqual(recap.main(["--once"]), 0)
            self.assertEqual(recap.main(["--generate", "meeting:2026-01-01T0000-zoom"]), 1)
            self.assertEqual(recap.main(["--slack-draft", KEY, "--channel-id", "C0123456789"]), 1)

    def test_crash_is_logged_not_raised(self):
        with mock.patch.object(recap, "run_once", side_effect=RuntimeError("kaboom")):
            self.assertEqual(recap.main([]), 1)
        self.assertIn("kaboom", store.log_path().read_text(encoding="utf-8"))


class RecordShapeTestCase(RecapCase):
    def test_recap_json_carries_no_send_fields_and_is_not_a_card(self):
        self.first_run()
        self.meeting()
        self.run_once(fx.T0 + 34 * MIN)
        doc = json.loads(store.recap_path(KEY).read_text(encoding="utf-8"))

        def keys(o, acc):
            if isinstance(o, dict):
                for k, v in o.items():
                    acc.add(k)
                    keys(v, acc)
            elif isinstance(o, list):
                for v in o:
                    keys(v, acc)
            return acc
        all_keys = keys(doc, set())
        for banned in ("recipient", "channel", "to", "id", "tier", "target_repo", "execution"):
            self.assertNotIn(banned, all_keys)
        self.assertFalse((config.STATE_DIR.parent / "act" / "registry").exists())


if __name__ == "__main__":
    unittest.main()
