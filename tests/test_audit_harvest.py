"""Audit 2026-07 — harvest_delivery hardening (act/executor.py).

Three confirmed failure modes, each pinned here:
1. fence-blindness: a ``FINAL DRAFT:`` line QUOTED inside a ``` fence in the
   draft body used to win over the real marker, truncating the deliverable;
2. trailing bare marker: falling back past the last marker promoted
   mid-summary prose ("FINAL DRAFT: see the doc I committed") to final_draft;
3. last-message-only: a short closing remark AFTER the delivery message
   (final check / cleanup) hid the draft entirely, defeating
   _promote_if_delivered and burning resumes on a finished session.

Plus the §15 html-file delivery: a draft that references ONE absolute *.html
path is hydrated from that file (≤20000 chars) so 复制成稿 stays paste-ready,
while the path stays visible in delivered_summary.

Same fixture style as tests/test_harvest_delivery.py: fake $HOME, fixture
transcripts under ~/.claude/projects — the real ~/.claude is never read.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - ensures the sandbox env is set first

from act import executor

SID = "bbbb2222-0000-4000-8000-000000000001"  # short id = bbbb2222


def _assistant(text: str, sidechain: bool = False) -> str:
    d = {"type": "assistant",
         "message": {"content": [{"type": "text", "text": text}]}}
    if sidechain:
        d["isSidechain"] = True
    return json.dumps(d, ensure_ascii=False)


def _user(text: str, meta: bool = False, as_blocks: bool = False) -> str:
    content = [{"type": "text", "text": text}] if as_blocks else text
    d: dict = {"type": "user", "message": {"content": content}}
    if meta:
        d["isMeta"] = True
    return json.dumps(d, ensure_ascii=False)


def _tool_result() -> str:
    # shape verified against live transcripts: type=user + tool_result blocks
    # + top-level toolUseResult — NOT a user turn
    return json.dumps({
        "type": "user",
        "message": {"content": [{"type": "tool_result",
                                 "tool_use_id": "toolu_01", "content": "ok"}]},
        "toolUseResult": {"stdout": "ok"},
    }, ensure_ascii=False)


@unittest.skipIf(
    sys.platform.startswith("win"),
    "sandbox via HOME env (Windows expanduser uses USERPROFILE) — the harvest "
    "transcript-path area is not ported yet, same as test_harvest_delivery")
class AuditHarvestBase(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="audit-harvest-home-")
        patcher = mock.patch.dict(os.environ, {"HOME": self.home})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.proj = Path(self.home) / ".claude" / "projects" / "some-project"
        self.proj.mkdir(parents=True)

    def _write(self, lines: list, sid: str = SID) -> Path:
        p = self.proj / f"{sid}.jsonl"
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return p


# --------------------------------------------------------------------------- #
# 1. fence-aware marker parsing
# --------------------------------------------------------------------------- #
class FenceAwareMarkerTestCase(AuditHarvestBase):
    def test_fenced_marker_inside_draft_does_not_truncate_it(self):
        # the draft itself quotes the marker in a fenced example — the OUTER
        # marker is the real one; the old reversed() scan picked the inner
        # fenced one and cut the draft head into delivered_summary.
        self._write([_assistant(
            "做完了，说明如下\n"
            "FINAL DRAFT:\n"
            "Subject: 真正的成稿开头\n"
            "```\n"
            "FINAL DRAFT:\n"
            "<引用的示例全文>\n"
            "```\n"
            "成稿结尾一行"
        )])
        out = executor.harvest_delivery(SID)
        self.assertEqual(out["delivered_summary"], "做完了，说明如下")
        self.assertTrue(out["final_draft"].startswith("Subject: 真正的成稿开头"))
        self.assertTrue(out["final_draft"].endswith("成稿结尾一行"))
        self.assertIn("<引用的示例全文>", out["final_draft"])

    def test_only_fenced_markers_means_no_draft(self):
        # every marker is inside a fence -> a quoted example must never be
        # promoted to a delivery.
        text = "我引用一下交付指令\n```\nFINAL DRAFT:\n示例正文\n```\n还没有成稿"
        self._write([_assistant(text)])
        out = executor.harvest_delivery(SID)
        self.assertIsNone(out["final_draft"])
        self.assertEqual(out["delivered_summary"], text[:500])

    def test_trailing_bare_marker_never_promotes_summary_prose(self):
        # confirmed variant: bare trailing marker + an earlier mid-summary
        # reference line — the old fallback returned "see the doc I committed
        # …" as the final draft.
        text = ("FINAL DRAFT: see the doc I committed\n"
                "其余总结内容\n"
                "FINAL DRAFT:")
        self._write([_assistant(text)])
        out = executor.harvest_delivery(SID)
        self.assertIsNone(out["final_draft"])
        self.assertEqual(out["delivered_summary"], text[:500])


# --------------------------------------------------------------------------- #
# 2. multi-message harvest — closing remark after the delivery message
# --------------------------------------------------------------------------- #
class MultiMessageHarvestTestCase(AuditHarvestBase):
    def test_closing_remark_after_delivery_message_keeps_the_draft(self):
        # agent prints summary+FINAL DRAFT, runs one more tool call, ends with
        # a short remark — the marker-bearing message is the delivery.
        self._write([
            _assistant("总结在此\nFINAL DRAFT:\n成稿全文第一段\n第二段"),
            _assistant("All checks pass."),
        ])
        out = executor.harvest_delivery(SID)
        self.assertEqual(out["final_draft"], "成稿全文第一段\n第二段")
        self.assertEqual(out["delivered_summary"], "总结在此")

    def test_last_marker_bearing_message_wins(self):
        # two delivery rounds in one session (rework) — take the newest.
        self._write([
            _assistant("第一版\nFINAL DRAFT:\n初稿"),
            _assistant("第二版\nFINAL DRAFT:\n终稿"),
            _assistant("收尾备注"),
        ])
        out = executor.harvest_delivery(SID)
        self.assertEqual(out["final_draft"], "终稿")
        self.assertEqual(out["delivered_summary"], "第二版")

    def test_no_marker_anywhere_last_message_is_summary_only(self):
        self._write([
            _assistant("中间回复"),
            _assistant("已完成，改动在 feat/x 分支"),
        ])
        out = executor.harvest_delivery(SID)
        self.assertIsNone(out["final_draft"])
        self.assertEqual(out["delivered_summary"], "已完成，改动在 feat/x 分支")

    def test_sidechain_marker_is_never_harvested(self):
        # a subagent's FINAL DRAFT is not the main-thread delivery.
        self._write([
            _assistant("子代理草稿\nFINAL DRAFT:\n不该被收割", sidechain=True),
            _assistant("主线收尾，无成稿"),
        ])
        out = executor.harvest_delivery(SID)
        self.assertIsNone(out["final_draft"])
        self.assertEqual(out["delivered_summary"], "主线收尾，无成稿")


# --------------------------------------------------------------------------- #
# 3. html-path hydration (§15 html output format)
# --------------------------------------------------------------------------- #
class HtmlPathHydrationTestCase(AuditHarvestBase):
    def setUp(self):
        super().setUp()
        self.outdir = Path(tempfile.mkdtemp(prefix="audit-harvest-html-"))
        self.html = self.outdir / "report.html"
        self.html.write_text("<!DOCTYPE html>\n<html><body>正文</body></html>",
                             encoding="utf-8")

    def _deliver(self, draft: str, summary: str = "做完了") -> dict:
        self._write([_assistant(f"{summary}\nFINAL DRAFT:\n{draft}")])
        return executor.harvest_delivery(SID)

    def test_lone_html_path_hydrates_file_contents(self):
        out = self._deliver(f"{self.html}\n改了三处措辞\n结构不变")
        self.assertEqual(out["final_draft"],
                         "<!DOCTYPE html>\n<html><body>正文</body></html>")
        # the path (and the 3-5 line summary) stay visible on the card
        self.assertIn(str(self.html), out["delivered_summary"])
        self.assertIn("改了三处措辞", out["delivered_summary"])
        self.assertIn("做完了", out["delivered_summary"])

    def test_backtick_quoted_path_hydrates_too(self):
        out = self._deliver(f"`{self.html}`\n摘要一行")
        self.assertTrue(out["final_draft"].startswith("<!DOCTYPE html>"))

    def test_missing_file_keeps_the_path_draft(self):
        gone = self.outdir / "nope.html"
        out = self._deliver(f"{gone}\n摘要")
        self.assertEqual(out["final_draft"], f"{gone}\n摘要")

    def test_empty_file_keeps_the_path_draft(self):
        self.html.write_text("", encoding="utf-8")
        out = self._deliver(f"{self.html}\n摘要")
        self.assertEqual(out["final_draft"], f"{self.html}\n摘要")

    def test_two_html_paths_are_ambiguous_no_hydration(self):
        other = self.outdir / "other.html"
        other.write_text("<html></html>", encoding="utf-8")
        draft = f"{self.html}\n{other}"
        out = self._deliver(draft)
        self.assertEqual(out["final_draft"], draft)

    def test_non_html_absolute_path_not_hydrated(self):
        md = self.outdir / "notes.md"
        md.write_text("# notes", encoding="utf-8")
        out = self._deliver(f"{md}\n摘要")
        self.assertEqual(out["final_draft"], f"{md}\n摘要")

    def test_hydrated_contents_capped_at_20000(self):
        self.html.write_text("<p>" + "文" * 25000, encoding="utf-8")
        out = self._deliver(str(self.html))
        self.assertEqual(len(out["final_draft"]), 20000)


# --------------------------------------------------------------------------- #
# 4. rework-round scoping — only messages after the last real user turn count
# --------------------------------------------------------------------------- #
class ReworkRoundScopingTestCase(AuditHarvestBase):
    def test_stale_draft_before_feedback_is_not_resurrected(self):
        # round-1 delivery -> 打回 feedback (user turn) -> rework agent blocks
        # on a question: the rejected round-1 draft must NOT re-promote the
        # card to 待验收.
        self._write([
            _user("dispatch prompt"),
            _assistant("第一轮总结\nFINAL DRAFT:\n第一轮成稿"),
            _user("打回：换个口吻重写"),
            _assistant("好的，先确认一下：面向内部还是客户？"),
        ])
        out = executor.harvest_delivery(SID)
        self.assertIsNone(out["final_draft"])
        self.assertEqual(out["delivered_summary"], "好的，先确认一下：面向内部还是客户？")

    def test_no_assistant_reply_after_feedback_returns_both_none(self):
        # feedback sent, rework agent died before answering — nothing has been
        # delivered THIS round, so nothing is harvested (both None).
        self._write([
            _user("dispatch prompt"),
            _assistant("第一轮总结\nFINAL DRAFT:\n第一轮成稿"),
            _user("打回：换个口吻重写"),
        ])
        self.assertEqual(executor.harvest_delivery(SID),
                         {"delivered_summary": None, "final_draft": None})

    def test_new_draft_after_feedback_wins(self):
        self._write([
            _user("dispatch prompt"),
            _assistant("第一轮总结\nFINAL DRAFT:\n第一轮成稿"),
            _user("打回：换个口吻重写"),
            _assistant("第二版总结\nFINAL DRAFT:\n第二轮成稿"),
        ])
        out = executor.harvest_delivery(SID)
        self.assertEqual(out["final_draft"], "第二轮成稿")
        self.assertEqual(out["delivered_summary"], "第二版总结")

    def test_first_delivery_after_dispatch_prompt_unchanged(self):
        self._write([
            _user("dispatch prompt"),
            _assistant("总结\nFINAL DRAFT:\n成稿全文"),
        ])
        out = executor.harvest_delivery(SID)
        self.assertEqual(out["final_draft"], "成稿全文")
        self.assertEqual(out["delivered_summary"], "总结")

    def test_tool_result_line_does_not_mask_the_draft(self):
        # tool results arrive as type=user lines — they are NOT user turns, so
        # the delivery→tool call→closing remark pattern keeps its draft.
        self._write([
            _user("dispatch prompt"),
            _assistant("总结\nFINAL DRAFT:\n成稿全文"),
            _tool_result(),
            _assistant("All checks pass."),
        ])
        out = executor.harvest_delivery(SID)
        self.assertEqual(out["final_draft"], "成稿全文")
        self.assertEqual(out["delivered_summary"], "总结")

    def test_meta_user_line_does_not_mask_the_draft(self):
        # harness-injected isMeta lines are not user turns either.
        self._write([
            _user("dispatch prompt"),
            _assistant("总结\nFINAL DRAFT:\n成稿全文"),
            _user("Caveat: injected by the harness", meta=True),
            _assistant("收尾备注"),
        ])
        out = executor.harvest_delivery(SID)
        self.assertEqual(out["final_draft"], "成稿全文")

    def test_block_form_user_feedback_also_scopes(self):
        # user turns may carry content as [{"type":"text",...}] blocks too.
        self._write([
            _user("dispatch prompt"),
            _assistant("第一轮总结\nFINAL DRAFT:\n第一轮成稿"),
            _user("打回：重写", as_blocks=True),
            _assistant("收到，处理中的提问？"),
        ])
        out = executor.harvest_delivery(SID)
        self.assertIsNone(out["final_draft"])


if __name__ == "__main__":
    unittest.main()
