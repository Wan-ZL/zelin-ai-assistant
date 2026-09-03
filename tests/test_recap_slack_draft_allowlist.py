"""§63.4 Slack DRAFT delivery is a whitelist, not a prompt (act/lib/recap_slack_draft.py).

Pins: the argv is exactly ``claude -p <prompt> --output-format text
--allowedTools mcp__slack__slack_send_message_draft,mcp__slack__slack_search_users``
with no ``--dangerously-skip-permissions``; the allowlist contains no send /
schedule / reaction / post tool; the toggle OFF short-circuits before any
call; a CLOSED recap with a configured target is drafted once; no target →
未投草稿; ``draft_already_exists`` is recorded, never overwritten or retried;
a regenerated recap is not re-posted by itself; the explicit page pick works
through ``slack_draft_for``; malformed model replies read as failed.
"""
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first
from tests import recap_fixture as fx

from act import recap
from act.lib import config, notify
from act.lib import recap_slack_draft as sd
from act.lib import recap_store as store

KEY = fx.KEY
MIN = 60.0
CHANNEL = "C0123456789"
DRAFT_OK = '{"status": "posted", "channel_link": "https://acme.slack.com/archives/C0123456789"}'


class Recorder:
    """Recap call → good recap; draft call → the queued draft reply."""

    def __init__(self, *draft_replies):
        self.calls = []
        self.draft_replies = list(draft_replies) or [DRAFT_OK]

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), dict(kwargs)))
        if "--allowedTools" in argv:
            reply = self.draft_replies.pop(0) if len(self.draft_replies) > 1 else self.draft_replies[0]
        else:
            reply = fx.good_output()
        return subprocess.CompletedProcess(argv, 0, stdout=reply, stderr="")

    @property
    def draft_calls(self):
        return [c for c in self.calls if "--allowedTools" in c[0]]


class AllowlistShapeTestCase(unittest.TestCase):
    def test_allowlist_is_exactly_two_read_or_draft_tools(self):
        self.assertEqual(sd.ALLOWED_TOOLS, ("mcp__slack__slack_send_message_draft",
                                            "mcp__slack__slack_search_users"))
        self.assertEqual(sd.ALLOWLIST_ARGV, ("--allowedTools",
                                             "mcp__slack__slack_send_message_draft,mcp__slack__slack_search_users"))
        for tool in sd.ALLOWED_TOOLS:
            for frag in ("schedule", "reaction", "post_message", "chat_post"):
                self.assertNotIn(frag, tool)
        self.assertNotIn("mcp__slack__slack_send_message", sd.ALLOWED_TOOLS)   # the real send

    def test_sealed_oracle(self):
        good = ["claude", "-p", "x", "--output-format", "text", *sd.ALLOWLIST_ARGV]
        self.assertTrue(sd.allowlist_is_sealed(good))
        self.assertFalse(sd.allowlist_is_sealed(good + ["--dangerously-skip-permissions"]))
        self.assertFalse(sd.allowlist_is_sealed(
            ["claude", "-p", "x", "--allowedTools", "mcp__slack__slack_send_message_draft,mcp__slack__slack_send_message"]))
        self.assertFalse(sd.allowlist_is_sealed(["claude", "-p", "x"]))

    def test_parse_result_is_fail_closed(self):
        self.assertEqual(sd.parse_result(DRAFT_OK),
                         {"status": "posted", "channel_link": "https://acme.slack.com/archives/C0123456789"})
        self.assertEqual(sd.parse_result('{"status": "draft_already_exists"}'),
                         {"status": "draft_already_exists", "channel_link": None})
        self.assertEqual(sd.parse_result('{"status": "sent"}')["status"], "failed")
        self.assertEqual(sd.parse_result("I posted it!")["status"], "failed")
        self.assertIsNone(sd.parse_result('{"status": "posted", "channel_link": "http://evil.example/x"}')["channel_link"])

    def test_resolve_target_never_guesses(self):
        targets = {"zoom": CHANNEL}
        self.assertEqual(sd.resolve_target(targets, "zoom"), CHANNEL)
        self.assertEqual(sd.resolve_target(targets, "Zoom"), CHANNEL)
        self.assertIsNone(sd.resolve_target(targets, "teams"))
        self.assertEqual(sd.resolve_target({}, "teams", explicit="D987654321"), "D987654321")

    def test_prompt_fences_the_text_and_forbids_sending(self):
        prompt = sd.build_prompt(CHANNEL, "Decided: x\nSplit: y")
        self.assertIn(CHANNEL, prompt)
        self.assertIn("Do NOT send", prompt)
        self.assertIn("mcp__slack__slack_send_message_draft", prompt)
        from act.lib import sanitize
        self.assertEqual(prompt.count(sanitize.UNTRUSTED_OPEN), 1)


class DraftFlowTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-recap-draft-")
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        (root / "state").mkdir()
        mock.patch.object(config, "STATE_DIR", root / "state").start()
        mock.patch.object(notify, "notify", return_value=True).start()
        self.addCleanup(mock.patch.stopall)
        self.conn = fx.make_db(root / "db.sqlite")
        self.addCleanup(self.conn.close)
        self.cfg = config.Config(raw={"recap": {"slack_draft": {"targets": {"zoom": CHANNEL, "bad": "nope"}}}})
        self.cfg.claude_bin = "claude"
        self.rec = Recorder()
        recap.run_once(now=fx.T0 - 3600, conn=self.conn, runner=self.rec, cfg=self.cfg)
        fx.add_frames(self.conn, fx.T0, 20)
        fx.add_audio(self.conn, fx.T0, 20)

    def close_round(self, at=34 * MIN):
        return recap.run_once(now=fx.T0 + at, conn=self.conn, runner=self.rec, cfg=self.cfg)

    def test_toggle_off_means_no_draft_call_at_all(self):
        self.close_round()
        self.assertEqual(self.rec.draft_calls, [])
        self.assertIsNone(store.load_recap(KEY)["slack_draft"])

    def test_toggle_on_with_target_drafts_once_with_the_pinned_argv(self):
        self.cfg.recap_slack_draft_enabled = True
        self.close_round()
        self.assertEqual(len(self.rec.draft_calls), 1)
        argv, kwargs = self.rec.draft_calls[0]
        self.assertEqual(argv[0:2], ["claude", "-p"])
        self.assertEqual(argv[3:], ["--output-format", "text", "--allowedTools",
                                    "mcp__slack__slack_send_message_draft,mcp__slack__slack_search_users"])
        self.assertTrue(sd.allowlist_is_sealed(argv))
        self.assertNotIn("--dangerously-skip-permissions", argv)
        self.assertIn(CHANNEL, argv[2])
        self.assertIn("定了：", argv[2])              # default_language auto → cfg.language zh
        rec = store.load_recap(KEY)
        self.assertEqual(rec["slack_draft"]["status"], "posted")
        self.assertEqual(rec["slack_draft"]["channel_link"], "https://acme.slack.com/archives/C0123456789")
        # a late slice regenerates the recap but does NOT re-post the draft
        fx.add_audio(self.conn, fx.T0 + 2 * MIN, 3, text="late slice words for the record")
        self.close_round(at=64 * MIN)
        self.assertEqual(store.load_recap(KEY)["version"], 2)
        self.assertEqual(len(self.rec.draft_calls), 1)

    def test_english_default_language_drafts_the_english_lines(self):
        self.cfg.recap_slack_draft_enabled = True
        self.cfg.recap_default_language = "en"
        self.close_round()
        self.assertIn("Decided:", self.rec.draft_calls[0][0][2])
        self.assertNotIn("定了：", self.rec.draft_calls[0][0][2])

    def test_no_configured_target_marks_the_row_and_calls_nothing(self):
        self.cfg.recap_slack_draft_enabled = True
        self.cfg.raw["recap"]["slack_draft"]["targets"] = {}
        self.close_round()
        self.assertEqual(self.rec.draft_calls, [])
        self.assertEqual(store.load_recap(KEY)["slack_draft"]["status"], "no_target")

    def test_draft_already_exists_is_recorded_not_retried(self):
        self.cfg.recap_slack_draft_enabled = True
        self.rec = Recorder('{"status": "draft_already_exists", "channel_link": ""}')
        self.close_round()
        self.assertEqual(store.load_recap(KEY)["slack_draft"]["status"], "draft_already_exists")
        self.close_round(at=64 * MIN)
        self.assertEqual(len(self.rec.draft_calls), 1)

    def test_explicit_pick_from_the_page(self):
        self.close_round()                                    # toggle off: recap only
        self.assertIsNone(store.load_recap(KEY)["slack_draft"])
        receipt = recap.slack_draft_for(KEY, "D987654321", runner=self.rec, cfg=self.cfg)
        self.assertEqual(receipt["status"], "disabled")       # toggle still off → refused
        self.assertEqual(self.rec.draft_calls, [])
        self.cfg.recap_slack_draft_enabled = True
        receipt = recap.slack_draft_for(KEY, "D987654321", runner=self.rec, cfg=self.cfg)
        self.assertEqual(receipt["status"], "posted")
        self.assertIn("D987654321", self.rec.draft_calls[0][0][2])
        self.assertIsNone(recap.slack_draft_for(KEY, "not-a-channel", runner=self.rec, cfg=self.cfg))
        self.assertIsNone(recap.slack_draft_for("meeting:2026-01-01T0000-zoom", CHANNEL,
                                                runner=self.rec, cfg=self.cfg))

    def test_recap_without_text_is_never_drafted(self):
        self.cfg.recap_slack_draft_enabled = True
        self.rec = Recorder()
        # replace the transcript with silence: no_audio recap
        self.conn.execute("DELETE FROM audio_transcriptions")
        self.conn.commit()
        self.close_round()
        rec = store.load_recap(KEY)
        self.assertEqual(rec["quality"], "no_audio")
        self.assertIsNone(rec["slack_draft"])
        receipt = recap.slack_draft_for(KEY, CHANNEL, runner=self.rec, cfg=self.cfg)
        self.assertEqual(receipt["status"], "failed")
        self.assertEqual(self.rec.draft_calls, [])

    def test_model_crash_during_draft_is_a_row_badge(self):
        self.cfg.recap_slack_draft_enabled = True
        self.close_round()
        with mock.patch.object(recap, "_call_model", side_effect=RuntimeError("mcp down")):
            receipt = recap.slack_draft_for(KEY, CHANNEL, runner=self.rec, cfg=self.cfg)
        self.assertEqual(receipt["status"], "failed")
        self.assertIn("mcp down", store.log_path().read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
