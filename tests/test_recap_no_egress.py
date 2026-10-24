"""§63 no-egress pin: the recap generation call is a sealed box, and nothing in
the recap path can send anything.

Owner (issue #129): the only exit for a recap is the clipboard. Constitution
art.4 (v2.0) cites this file: recorded screen becomes notes and recaps only,
never tasks, never auto-egress; the only thing that leaves the machine is the
model call. Five layers, each pinned here: (1) the recap is a file under
state/recap/, not a card — no approve, no dispatch; (2) the generation argv is
exactly ``claude -p <prompt> --output-format text --model <id> --fallback-model
<id> <ISOLATION_ARGV> --tools "" --strict-mcp-config --mcp-config
'{"mcpServers":{}}'`` — §78 isolation (``--bare``, no session transcript, no
setting sources, no MCP servers) plus no built-in tools; (3) it runs under
``CLAUDE_CONFIG_DIR`` = the app's own claude-config and with the neutral
HEADLESS_CWD as cwd, so no CLAUDE.md / .claude/ is in reach; (4) the recap JSON
and the server's request forms carry no recipient / channel field; (5) the
§63.4 Slack-draft path retired in v2.0.0 — no Slack tool, module or flag exists.
"""
import contextlib
import io
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
from act.lib import recap_store as store
from act.lib import recap_text as rt

KEY = fx.KEY
MIN = 60.0

# §78 isolation, spelled out literally (not imported from llm) so a change to
# the tuple shows up here as a law change, not silently.
ISOLATION = ["--bare", "--no-session-persistence", "--setting-sources", "",
             "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}']
NO_EGRESS = ["--tools", "", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}']
# v2.0.0: --model is always explicit (no "follow" mode); the D53 fallback pair
# rides right behind it, ahead of the isolation and no-egress tails.
MODEL = ["--model", config.DEFAULT_MODEL_PIPELINE]
FB = ["--fallback-model", config.DEFAULT_MODEL_FALLBACK]
PINNED_TAIL = ["--output-format", "text", *MODEL, *FB, *ISOLATION, *NO_EGRESS]


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
        self.assertEqual(tuple(ISOLATION), llm.ISOLATION_ARGV)
        # §78: the neutral cwd outside the repo tree + the app's own Claude Code home
        self.assertEqual(kwargs["cwd"], config.headless_cwd())
        self.assertEqual(kwargs["env"]["CLAUDE_CONFIG_DIR"], str(config.claude_config_dir()))
        self.assertFalse((Path(kwargs["cwd"]) / "CLAUDE.md").exists())
        self.assertFalse((Path(kwargs["cwd"]) / ".claude").exists())
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
                                    *FB, *ISOLATION, *NO_EGRESS])

    def test_the_slack_draft_path_is_gone(self):
        """§63.4 retired v2.0.0: no module, no CLI flag, no settings knob, no request form."""
        import importlib.util
        self.assertIsNone(importlib.util.find_spec("act.lib.recap_slack_draft"))
        self.assertFalse(hasattr(recap, "post_slack_draft"))
        self.assertFalse(hasattr(self.cfg, "recap_slack_draft_enabled"))
        self.assertIsNone(store.request_argv({"action": "recap_slack_draft", "meeting_key": KEY,
                                              "channel_id": "C0123456789"}))
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            recap.main(["--slack-draft", KEY])
        self._closed_round()
        self.assertEqual(len(self.rec.calls), 1)

    def test_recap_json_and_request_forms_carry_no_recipient(self):
        self._closed_round()
        doc = json.loads(store.recap_path(KEY).read_text(encoding="utf-8"))
        text = json.dumps(doc)
        for banned in ('"recipient"', '"channel"', '"to"', '"cc"', '"status": "approved"', '"tier"'):
            self.assertNotIn(banned, text)
        self.assertEqual(store.request_argv({"action": "recap_generate", "meeting_key": KEY}),
                         ["--generate", KEY])
        # a stray channel on the generate form is never forwarded (no recipient exists)
        self.assertNotIn("C123", store.request_argv({"action": "recap_generate", "meeting_key": KEY,
                                                   "channel": "C123"}))

    def test_recap_lives_outside_the_registry(self):
        self._closed_round()
        path = store.recap_path(KEY)
        self.assertTrue(path.is_file())
        self.assertEqual(path.parent, config.STATE_DIR / "recap" / "recaps")
        self.assertNotIn("registry", str(path))
        self.assertFalse((config.STATE_DIR / "inbox").exists())   # no decision file was minted
        self.assertFalse((config.STATE_DIR / "registry").exists())

    def test_boundary_builds_the_same_shape(self):
        """The pin above is what act/llm.py produces for these extra args — a
        future llm.py change that reorders flags shows up here first."""
        argv = llm.build_argv("P", extra_argv=rt.NO_EGRESS_ARGV, cfg=self.cfg)
        self.assertEqual(argv, ["claude", "-p", "P"] + PINNED_TAIL)


if __name__ == "__main__":
    unittest.main()
