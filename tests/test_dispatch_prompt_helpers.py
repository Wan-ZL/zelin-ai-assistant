"""act/lib/dispatch_prompt — the small helpers behind the golden prompts.

tests/test_executor_prompt_golden.py pins the assembled text; this file pins
the defaults and edge inputs the goldens cannot reach: the memory head cap
(MEMORY_HEAD_LINES), a missing memory file reading as "", the quality-gate
block's ``remote`` default, the §37.1 tier / display-name / voice helpers on
objects that lack the optional attributes (older registry rows), a card whose
title is None, and attachment shapes that are not a list.
"""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act.lib import config, dispatch_prompt
from act.lib.registry import Requirement, State


class MemoryHeadTestCase(unittest.TestCase):
    def test_head_is_capped_at_memory_head_lines(self):
        with tempfile.TemporaryDirectory(prefix="memhead-") as td:
            mem = Path(td) / "MEMORY.md"
            mem.write_text("\n".join(f"line {i}" for i in range(100)), encoding="utf-8")
            with mock.patch.object(config, "MEMORY_PATH", mem):
                head = dispatch_prompt.read_memory_head()
        lines = head.split("\n")
        self.assertEqual(len(lines), 60)
        self.assertEqual(dispatch_prompt.MEMORY_HEAD_LINES, 60)
        self.assertEqual(lines[-1], "line 59")

    def test_missing_memory_file_reads_as_empty_string(self):
        with mock.patch.object(config, "MEMORY_PATH", Path("/nonexistent/MEMORY.md")):
            self.assertEqual(dispatch_prompt.read_memory_head(), "")
            self.assertEqual(dispatch_prompt.memory_blocks(config.Config()), [])


class GateBlockTestCase(unittest.TestCase):
    def test_remote_defaults_to_true(self):
        cfg = config.Config()
        text = dispatch_prompt.quality_gate_block(cfg)
        self.assertIn("open a DRAFT PR", text)
        self.assertNotIn("No git remote is configured", text)
        self.assertEqual(text, dispatch_prompt.quality_gate_block(cfg, remote=True))
        self.assertIn("No git remote is configured",
                      dispatch_prompt.quality_gate_block(cfg, remote=False))
        self.assertEqual(dispatch_prompt.training_block()[:19], "TRAINING DISCIPLINE")


class TierHelpersTestCase(unittest.TestCase):
    def test_tier_on_a_row_without_the_optional_attributes(self):
        # older registry rows: no user_titled / display_title attributes at all
        bare = SimpleNamespace(notes="", title="整理推荐信")
        self.assertEqual(dispatch_prompt.card_title_tier(bare), ("recheck", False))
        direct = SimpleNamespace(notes="[direct-run] 用户直接开跑", title="x")
        self.assertEqual(dispatch_prompt.card_title_tier(direct), ("forced", True))
        self.assertEqual(dispatch_prompt.card_title_tier(SimpleNamespace(
            notes=None, title="https://example.com/a/b")), ("forced", False))

    def test_user_titled_wins(self):
        req = Requirement(id="R-1", title="x", status=State.APPROVED.value, user_titled=True,
                          notes="[direct-run] 用户直接开跑")
        self.assertEqual(dispatch_prompt.card_title_tier(req), ("user", True))

    def test_display_name_of_a_nameless_card_is_empty(self):
        req = Requirement(id="R-1", title=None, status=State.APPROVED.value)
        self.assertEqual(dispatch_prompt.current_display_name(req), "")
        req.title = "  整理   推荐信  "
        self.assertEqual(dispatch_prompt.current_display_name(req), "整理 推荐信")
        req.display_title = "钦定名"
        self.assertEqual(dispatch_prompt.current_display_name(req), "钦定名")


class VoiceBlockTestCase(unittest.TestCase):
    def test_voice_defaults_on_for_a_config_without_the_switch(self):
        with mock.patch.object(dispatch_prompt, "resolve_voice_profile",
                               return_value=Path("/golden/voice.md")):
            blocks = dispatch_prompt.voice_blocks(SimpleNamespace())
            self.assertEqual(len(blocks), 1)
            self.assertIn("/golden/voice.md", blocks[0])
            self.assertEqual(dispatch_prompt.voice_blocks(SimpleNamespace(voice_enabled=False)), [])
        with mock.patch.object(dispatch_prompt, "resolve_voice_profile", return_value=None):
            self.assertEqual(dispatch_prompt.voice_blocks(config.Config()), [])


class AttachmentsTestCase(unittest.TestCase):
    def test_non_list_shapes_yield_nothing(self):
        for execution in (None, {}, {"attachments": "a.png"}, {"attachments": {"p": 1}}, "junk"):
            req = Requirement(id="R-1", title="x", status=State.APPROVED.value, execution=execution)
            self.assertEqual(dispatch_prompt.attachment_paths(req), [])
            self.assertEqual(dispatch_prompt.attachment_blocks(req), [])

    def test_only_non_blank_strings_survive(self):
        req = Requirement(id="R-1", title="x", status=State.APPROVED.value,
                          execution={"attachments": [" /a.png ", "", 3, None, "/b.png"]})
        self.assertEqual(dispatch_prompt.attachment_paths(req), ["/a.png", "/b.png"])


if __name__ == "__main__":
    unittest.main()
