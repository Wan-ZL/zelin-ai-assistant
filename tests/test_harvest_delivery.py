"""executor.harvest_delivery — transcript -> delivered_summary/final_draft (契约 C).

harvest_delivery globs ``~/.claude/projects/*/{short}*.jsonl`` at call time, so
each test points $HOME at its own throwaway dir and writes fixture transcripts
there — the real ``~/.claude`` is never read.

Contract cases covered:
- no ``FINAL DRAFT:`` line  -> whole last assistant text (<=500) as summary;
- standalone ``FINAL DRAFT:`` line -> before (<=500) = summary, after (<=20000)
  = draft (marker-line remainder belongs to the draft);
- empty / missing transcript -> both None;
- corrupt JSONL lines are skipped, never raise.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - ensures the sandbox env is set first

from act import executor

SID = "aaaa1111-0000-4000-8000-000000000001"  # short id = aaaa1111


def _assistant(text: str, sidechain: bool = False) -> str:
    d = {"type": "assistant",
         "message": {"content": [{"type": "text", "text": text}]}}
    if sidechain:
        d["isSidechain"] = True
    return json.dumps(d, ensure_ascii=False)


def _user(text: str) -> str:
    return json.dumps({"type": "user", "message": {"content": text}},
                      ensure_ascii=False)


class HarvestDeliveryTestCase(unittest.TestCase):
    def setUp(self):
        # fake $HOME so Path("~/.claude/projects").expanduser() lands here
        self.home = tempfile.mkdtemp(prefix="harvest-home-")
        patcher = mock.patch.dict(os.environ, {"HOME": self.home})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.proj = Path(self.home) / ".claude" / "projects" / "some-project"
        self.proj.mkdir(parents=True)

    def _write(self, lines: list, sid: str = SID) -> Path:
        p = self.proj / f"{sid}.jsonl"
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return p

    # -- case 1: no marker ---------------------------------------------------- #
    def test_no_marker_last_assistant_text_becomes_summary(self):
        self._write([
            _user("请开始"),
            _assistant("第一版中间回复"),
            _assistant("已完成任务，改动在 feat/x 分支"),
            _assistant("子代理的收尾话（应被跳过）", sidechain=True),
        ])
        out = executor.harvest_delivery(SID)
        self.assertEqual(out, {
            "delivered_summary": "已完成任务，改动在 feat/x 分支",
            "final_draft": None,
            "card_title": None,
        })
        # short id works the same as the full UUID
        self.assertEqual(executor.harvest_delivery("aaaa1111"), out)

    # -- case 2: FINAL DRAFT marker ------------------------------------------- #
    def test_marker_splits_summary_and_draft(self):
        self._write([
            _assistant("做完了，成稿如下\nFINAL DRAFT:\n第一段正文\n第二段正文"),
        ])
        out = executor.harvest_delivery(SID)
        self.assertEqual(out["delivered_summary"], "做完了，成稿如下")
        self.assertEqual(out["final_draft"], "第一段正文\n第二段正文")

    def test_marker_line_remainder_belongs_to_draft(self):
        self._write([_assistant("说明\nFINAL DRAFT: 全文就这一行")])
        out = executor.harvest_delivery(SID)
        self.assertEqual(out["delivered_summary"], "说明")
        self.assertEqual(out["final_draft"], "全文就这一行")

    def test_marker_at_first_line_yields_none_summary(self):
        self._write([_assistant("FINAL DRAFT:\n只有成稿没有总结")])
        out = executor.harvest_delivery(SID)
        self.assertIsNone(out["delivered_summary"])  # None, not empty string
        self.assertEqual(out["final_draft"], "只有成稿没有总结")

    def test_empty_draft_after_marker_falls_back_to_whole_summary(self):
        # "marker 后为空视为无 marker" — whole text becomes the summary
        text = "总结在此\nFINAL DRAFT:"
        self._write([_assistant(text)])
        out = executor.harvest_delivery(SID)
        self.assertIsNone(out["final_draft"])
        self.assertEqual(out["delivered_summary"], text[:500])

    def test_double_marker_takes_the_last_occurrence(self):
        # LLM 在总结正文里引述指令行（行首 FINAL DRAFT:）时会出现两个 marker；
        # 契约语义是"结束总结的末尾"跟成稿——取最后一个，final_draft 必须是
        # 纯成稿，不混入总结残余和第二个 marker 行。
        self._write([_assistant(
            "I must end with a line\n"
            "FINAL DRAFT:\n"
            "(quoting the marker) my summary...\n"
            "real deliverable:\n"
            "FINAL DRAFT:\n"
            "Dear team, actual final text."
        )])
        out = executor.harvest_delivery(SID)
        self.assertEqual(out["final_draft"], "Dear team, actual final text.")
        self.assertNotIn("FINAL DRAFT:", out["final_draft"])
        self.assertIn("I must end with a line", out["delivered_summary"])

    def test_trailing_empty_marker_means_no_draft_no_fallback(self):
        # audit 2026-07：以前最后一个 marker 后为空会回退到更早的 marker——
        # 这会把 mid-summary 的引述（"FINAL DRAFT: see the doc"）提拔成成稿。
        # 现在最后一个 out-of-fence marker 后为空 = 无成稿，整段降级为 summary
        # （成稿要引 marker 请放进 ``` fence，见 test_audit_harvest）。
        text = "总结\nFINAL DRAFT:\n正文全文\nFINAL DRAFT:"
        self._write([_assistant(text)])
        out = executor.harvest_delivery(SID)
        self.assertIsNone(out["final_draft"])
        self.assertEqual(out["delivered_summary"], text[:500])

    def test_truncation_500_and_20000(self):
        before = "摘" * 600
        draft = "文" * 25000
        self._write([_assistant(f"{before}\nFINAL DRAFT:\n{draft}")])
        out = executor.harvest_delivery(SID)
        self.assertEqual(len(out["delivered_summary"]), 500)
        self.assertEqual(len(out["final_draft"]), 20000)
        self.assertEqual(out["final_draft"], draft[:20000])

    # -- case 3: empty / missing ----------------------------------------------- #
    def test_empty_transcript_returns_both_none(self):
        (self.proj / f"{SID}.jsonl").write_text("", encoding="utf-8")
        self.assertEqual(executor.harvest_delivery(SID),
                         {"delivered_summary": None, "final_draft": None,
                          "card_title": None})

    def test_unknown_session_returns_both_none(self):
        self.assertEqual(executor.harvest_delivery("deadbeef-0000"),
                         {"delivered_summary": None, "final_draft": None,
                          "card_title": None})

    def test_blank_session_id_returns_both_none(self):
        self.assertEqual(executor.harvest_delivery(""),
                         {"delivered_summary": None, "final_draft": None,
                          "card_title": None})

    # -- case 4: corrupt lines -------------------------------------------------- #
    def test_corrupt_jsonl_lines_are_skipped_not_fatal(self):
        self._write([
            "{{{ not json",
            _assistant("有效的最终回复"),
            "\x00garbage\ttail",
            json.dumps({"type": "assistant", "message": "not-a-dict-content"}),
        ])
        out = executor.harvest_delivery(SID)
        self.assertEqual(out["delivered_summary"], "有效的最终回复")
        self.assertIsNone(out["final_draft"])

    def test_transcript_with_only_garbage_returns_both_none(self):
        self._write(["not json", "also not json"])
        self.assertEqual(executor.harvest_delivery(SID),
                         {"delivered_summary": None, "final_draft": None,
                          "card_title": None})


if __name__ == "__main__":
    unittest.main()
