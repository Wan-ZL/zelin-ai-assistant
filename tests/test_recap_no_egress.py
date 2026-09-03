"""§63 no-egress pin: the recap generation call is a sealed box, and nothing in
the recap path can send anything.

Owner (issue #129): the only exit for a recap is the clipboard. Five layers,
each pinned here: (1) the recap is a file under state/recap/, not a registry
card — no approve, no dispatch; (2) the generation argv is exactly
``claude -p <prompt> --output-format text --tools "" --strict-mcp-config
--mcp-config '{"mcpServers":{}}'`` — no built-in tools, no MCP servers,
whatever ~/.claude carries; (3)+(4) the recap JSON and the two inbox special
forms carry no recipient / channel field; (5) with the Slack-draft toggle
OFF the argv never mentions a Slack tool and act/lib/recap_slack_draft.py is
never reached. ``ask.py``-style "same shape" is not a wall; this file is.
"""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first
from tests import recap_fixture as fx

from act import llm, recap
from act.lib import config, notify
from act.lib import recap_slack_draft as slack_draft
from act.lib import recap_store as store
from act.lib import recap_text as rt

KEY = fx.KEY
MIN = 60.0

PINNED_TAIL = ["--output-format", "text", "--tools", "", "--strict-mcp-config",
               "--mcp-config", '{"mcpServers":{}}']


class Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), dict(kwargs)))
        return subprocess.CompletedProcess(argv, 0, stdout=fx.good_output(), stderr="")


class NoEgressTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-recap-egress-")
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        (root / "state").mkdir()
        mock.patch.object(config, "STATE_DIR", root / "state").start()
        mock.patch.object(notify, "notify", return_value=True).start()
        self.addCleanup(mock.patch.stopall)
        self.conn = fx.make_db(root / "db.sqlite")
        self.addCleanup(self.conn.close)
        self.cfg = config.Config(raw={"recap": {}})
        self.cfg.claude_bin = "claude"
        self.rec = Recorder()
        recap.run_once(now=fx.T0 - 3600, conn=self.conn, runner=self.rec, cfg=self.cfg)
        fx.add_frames(self.conn, fx.T0, 20)
        fx.add_audio(self.conn, fx.T0, 20)

    def _closed_round(self):
        return recap.run_once(now=fx.T0 + 34 * MIN, conn=self.conn, runner=self.rec, cfg=self.cfg)

    def test_generation_argv_is_pinned(self):
        self._closed_round()
        self.assertEqual(len(self.rec.calls), 1)
        argv, kwargs = self.rec.calls[0]
        self.assertEqual(argv[0], "claude")
        self.assertEqual(argv[1], "-p")
        self.assertTrue(argv[2].startswith(rt.PROMPT_HEADER[:40]))
        self.assertEqual(argv[3:], PINNED_TAIL)
        self.assertEqual(json.loads(argv[-1]), {"mcpServers": {}})
        # neutral cwd + the boundary's legacy kwargs
        self.assertEqual(kwargs["cwd"], str(config.STATE_DIR))
        self.assertTrue(kwargs["capture_output"])
        self.assertEqual(kwargs["timeout"], recap.LLM_TIMEOUT_S)

    def test_argv_has_no_tool_no_mcp_no_permission_skip_no_slack(self):
        self._closed_round()
        argv = self.rec.calls[0][0]
        joined = " ".join(argv[3:])
        for banned in ("--allowedTools", "--dangerously-skip-permissions", "mcp__slack",
                       "slack_send_message", "--bg", "--resume"):
            self.assertNotIn(banned, joined)
        self.assertNotIn("mcp__", argv[2])          # the prompt asks for no tool either

    def test_explicit_pipeline_model_sits_behind_output_format(self):
        self.cfg.models_pipeline = "claude-haiku-4-5-20251001"
        self._closed_round()
        argv = self.rec.calls[0][0]
        self.assertEqual(argv[3:], ["--output-format", "text", "--model", "claude-haiku-4-5-20251001",
                                    "--tools", "", "--strict-mcp-config",
                                    "--mcp-config", '{"mcpServers":{}}'])

    def test_toggle_off_never_reaches_the_slack_module(self):
        self.assertFalse(self.cfg.recap_slack_draft_enabled)
        with mock.patch.object(slack_draft, "build_prompt", side_effect=AssertionError("reached")):
            self._closed_round()
        rec = store.load_recap(KEY)
        self.assertIsNone(rec["slack_draft"])
        self.assertEqual(len(self.rec.calls), 1)

    def test_recap_json_and_inbox_forms_carry_no_recipient(self):
        self._closed_round()
        doc = json.loads(store.recap_path(KEY).read_text(encoding="utf-8"))
        text = json.dumps(doc)
        for banned in ('"recipient"', '"channel"', '"to"', '"cc"', '"status": "approved"', '"tier"'):
            self.assertNotIn(banned, text)
        self.assertEqual(store.inbox_argv({"action": "recap_generate", "meeting_key": KEY}),
                         ["--generate", KEY])
        # a stray channel on the generate form is never forwarded (no recipient exists)
        self.assertNotIn("C123", store.inbox_argv({"action": "recap_generate", "meeting_key": KEY,
                                                   "channel": "C123"}))

    def test_recap_lives_outside_the_registry(self):
        self._closed_round()
        path = store.recap_path(KEY)
        self.assertTrue(path.is_file())
        self.assertEqual(path.parent, config.STATE_DIR / "recap" / "recaps")
        self.assertNotIn("registry", str(path))
        self.assertFalse((config.STATE_DIR / "inbox").exists())   # no decision file was minted

    def test_boundary_builds_the_same_shape(self):
        """The pin above is what act/llm.py produces for these extra args — a
        future llm.py change that reorders flags shows up here first."""
        argv = llm.build_argv("P", extra_argv=rt.NO_EGRESS_ARGV, cfg=self.cfg)
        self.assertEqual(argv, ["claude", "-p", "P"] + PINNED_TAIL)


if __name__ == "__main__":
    unittest.main()
