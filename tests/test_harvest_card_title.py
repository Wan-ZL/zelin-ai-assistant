"""§37 CARD TITLE harvest line — executor.harvest_delivery add-only key.

Same fixture style as tests/test_harvest_delivery.py (fake $HOME transcripts).
Contract cases:
- standalone out-of-fence ``CARD TITLE:`` line near FINAL DRAFT -> parsed into
  ``card_title`` and STRIPPED from delivered_summary/final_draft;
- fence-quoted marker -> ignored (stays in the draft verbatim);
- absent -> card_title None (and the v0.36 outputs byte-identical);
- oversize -> clipped to titles.MAX_DISPLAY_TITLE with …;
- repo mode (no FINAL DRAFT marker) -> parsed from the closing summary too.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - ensures the sandbox env is set first

from act import executor
from act.lib import titles

SID = "bbbb2222-0000-4000-8000-000000000002"  # short id = bbbb2222


def _assistant(text: str) -> str:
    return json.dumps(
        {"type": "assistant",
         "message": {"content": [{"type": "text", "text": text}]}},
        ensure_ascii=False)


class HarvestCardTitleTestCase(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="cardtitle-home-")
        patcher = mock.patch.dict(os.environ, {"HOME": self.home})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.proj = Path(self.home) / ".claude" / "projects" / "p"
        self.proj.mkdir(parents=True)

    def _write(self, lines: list) -> None:
        (self.proj / f"{SID}.jsonl").write_text(
            "\n".join(lines) + "\n", encoding="utf-8")

    def test_card_title_before_final_draft_parsed_and_stripped(self):
        self._write([_assistant(
            "做完了，讨论演化成了新方向\n"
            "CARD TITLE: 起草绿卡推荐信初稿\n"
            "FINAL DRAFT:\n正文第一段\n正文第二段")])
        out = executor.harvest_delivery(SID)
        self.assertEqual(out["card_title"], "起草绿卡推荐信初稿")
        self.assertEqual(out["delivered_summary"], "做完了，讨论演化成了新方向")
        self.assertEqual(out["final_draft"], "正文第一段\n正文第二段")

    def test_card_title_inside_fence_is_ignored(self):
        self._write([_assistant(
            "说明如下\n"
            "```\nCARD TITLE: 假标题（在 fence 里）\n```\n"
            "FINAL DRAFT:\n正文")])
        out = executor.harvest_delivery(SID)
        self.assertIsNone(out["card_title"])
        self.assertIn("CARD TITLE: 假标题（在 fence 里）",
                      out["delivered_summary"])

    def test_absent_marker_keeps_v036_shape(self):
        self._write([_assistant("总结\nFINAL DRAFT:\n正文")])
        out = executor.harvest_delivery(SID)
        self.assertIsNone(out["card_title"])
        self.assertEqual(out["delivered_summary"], "总结")
        self.assertEqual(out["final_draft"], "正文")

    def test_oversize_title_clipped(self):
        long_title = "长" * 100
        self._write([_assistant(
            f"总结\nCARD TITLE: {long_title}\nFINAL DRAFT:\n正文")])
        out = executor.harvest_delivery(SID)
        self.assertEqual(len(out["card_title"]), titles.MAX_DISPLAY_TITLE)
        self.assertTrue(out["card_title"].endswith("…"))

    def test_empty_title_remainder_is_none(self):
        self._write([_assistant("总结\nCARD TITLE:   \nFINAL DRAFT:\n正文")])
        out = executor.harvest_delivery(SID)
        self.assertIsNone(out["card_title"])
        # the bare marker line is still stripped from the summary
        self.assertEqual(out["delivered_summary"], "总结")

    def test_repo_mode_no_final_draft_still_parses(self):
        self._write([_assistant(
            "交付完成，PR 在 feat/x\nCARD TITLE: 重构登录流程")])
        out = executor.harvest_delivery(SID)
        self.assertEqual(out["card_title"], "重构登录流程")
        self.assertEqual(out["delivered_summary"], "交付完成，PR 在 feat/x")
        self.assertIsNone(out["final_draft"])

    def test_card_title_after_final_draft_stripped_from_draft(self):
        self._write([_assistant(
            "总结\nFINAL DRAFT:\n正文\nCARD TITLE: 后置的新标题")])
        out = executor.harvest_delivery(SID)
        self.assertEqual(out["card_title"], "后置的新标题")
        self.assertEqual(out["final_draft"], "正文")

    def test_last_marker_wins(self):
        self._write([_assistant(
            "CARD TITLE: 第一个\n总结\nCARD TITLE: 第二个\nFINAL DRAFT:\n正文")])
        out = executor.harvest_delivery(SID)
        self.assertEqual(out["card_title"], "第二个")
        self.assertEqual(out["delivered_summary"], "总结")


if __name__ == "__main__":
    unittest.main()
