"""Transcript reading (~/.claude/projects/*/<sid>*.jsonl) — the tolerant-parse
edges shared by resume/rework (``_transcript_info``), delivery harvesting
(``harvest_delivery``) and the §37 search text (``transcript_plain_text``).

Every reader must survive what real transcripts contain: unparseable lines,
non-object lines, messages whose ``message`` is not an object, content that
is neither a string nor a block list, user lines that are tool results or
image-only, and one unreadable file among several matches (skip it, keep
looking). ``_transcript_info`` additionally: a glob failure is None, a
transcript with no cwd at all is skipped in favour of the next match, and
the LAST cwd wins (worktree hop). Fixtures live under a throwaway $HOME.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import executor
from act.lib import transcripts

SID = "abcd1234-0000-4000-8000-000000000001"


def _line(kind, content, **extra):
    d = {"type": kind, "message": {"content": content}}
    d.update(extra)
    return json.dumps(d, ensure_ascii=False)


def _blocks(*texts):
    return [{"type": "text", "text": t} for t in texts]


class _Home(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="transcript-edges-")
        patcher = mock.patch.dict(os.environ, {"HOME": self.home})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.projects = Path(self.home) / ".claude" / "projects"

    def _write(self, lines, project="p", sid=SID):
        d = self.projects / project
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{sid}.jsonl"
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return p


class TranscriptInfoTestCase(_Home):
    def test_last_cwd_wins_and_unparseable_lines_are_skipped(self):
        # status quo: only JSON *decode* errors are skipped here — the cwd
        # scan assumes every parseable line is an object (real transcripts
        # are); the delivery / search readers below are the tolerant ones.
        self._write(['{"cwd": "/first"}', "not json", '{"no": "cwd"}',
                     json.dumps({"type": "assistant"}), '{"cwd": "/last/worktree"}'])
        self.assertEqual(executor._transcript_info("abcd1234"), (SID, Path("/last/worktree")))

    def test_transcript_without_cwd_yields_to_the_next_match(self):
        self._write([json.dumps({"type": "user"})], project="a-first")
        self._write(['{"cwd": "/second"}'], project="b-second",
                    sid="abcd1234-ffff-4000-8000-000000000002")
        full, cwd = executor._transcript_info("abcd1234")
        self.assertEqual((full, cwd), ("abcd1234-ffff-4000-8000-000000000002", Path("/second")))

    def test_unreadable_transcript_is_skipped(self):
        first = self._write(['{"cwd": "/unreadable"}'], project="a-first")
        self._write(['{"cwd": "/readable"}'], project="b-second",
                    sid="abcd1234-ffff-4000-8000-000000000002")
        real_open = open

        def flaky_open(path, *a, **kw):
            if str(path) == str(first):
                raise OSError("EACCES")
            return real_open(path, *a, **kw)
        with mock.patch("builtins.open", flaky_open):
            info = executor._transcript_info("abcd1234")
        self.assertEqual(info[1], Path("/readable"))

    def test_no_match_or_glob_failure_is_none(self):
        self.projects.mkdir(parents=True)
        self.assertIsNone(executor._transcript_info("abcd1234"))
        with mock.patch.object(Path, "glob", side_effect=OSError("EIO")):
            self.assertIsNone(executor._transcript_info("abcd1234"))

    def test_short_id_needs_the_full_eight_hex_segment(self):
        # a 7-char prefix that DOES match a transcript on disk is still refused
        self._write(['{"cwd": "/x"}'], sid="abcdef12-0000-4000-8000-000000000009")
        self.assertIsNone(executor._transcript_info("abcdef1"))
        self.assertIsNone(transcripts.plain_text("abcdef1"))
        self.assertEqual(executor._transcript_info("abcdef12")[1], Path("/x"))
        self.assertEqual(transcripts.MIN_SHORT_LEN, 8)

    def test_transcript_cwd_is_the_final_cwd_or_none(self):
        # both spellings: the lib function (merge_review) and the executor's
        # private alias (actd's merge-review worktree lookup)
        self._write(['{"cwd": "/first"}', '{"cwd": "/last"}'])
        self.assertEqual(transcripts.transcript_cwd("abcd1234"), Path("/last"))
        self.assertEqual(executor._transcript_cwd(SID), Path("/last"))
        self.assertIsNone(transcripts.transcript_cwd("nomatch1"))
        self.assertIsNone(executor._transcript_cwd(""))


class HarvestEdgeTestCase(_Home):
    def test_string_content_and_odd_shapes_are_tolerated(self):
        self._write([
            _line("user", "开始"),
            "garbage line",
            json.dumps("a bare string line"),
            json.dumps({"type": "assistant", "message": "not-an-object"}),
            json.dumps({"type": "assistant", "message": {"content": 42}}),
            json.dumps({"type": "user", "message": "not-an-object"}),     # not a user turn
            json.dumps({"type": "user", "message": {"content": 7}}),        # not a user turn
            json.dumps({"type": "user", "message": {"content": [
                {"type": "tool_result", "content": "x"}, {"type": "text", "text": "y"}]}}),
            _line("assistant", "字符串形态的交付总结"),
        ])
        self.assertEqual(executor.harvest_delivery(SID)["delivered_summary"], "字符串形态的交付总结")

    def test_non_turn_user_lines_after_the_delivery_do_not_reset_it(self):
        # tool results (even with a text block beside them) and odd content
        # shapes are not user turns — the delivery before them stands
        self._write([
            _line("user", "开始"),
            _line("assistant", _blocks("交付总结")),
            json.dumps({"type": "user", "message": {"content": [
                {"type": "tool_result", "content": "x"}, {"type": "text", "text": "y"}]}}),
            json.dumps({"type": "user", "message": {"content": 7}}),
            json.dumps({"type": "user", "message": {"content": []}}),
            json.dumps({"type": "user", "message": {"content": "   "}}),
            json.dumps({"type": "user", "isMeta": True, "message": {"content": "meta"}}),
            json.dumps({"type": "user", "toolUseResult": {}, "message": {"content": "tr"}}),
        ])
        self.assertEqual(executor.harvest_delivery(SID)["delivered_summary"], "交付总结")

    def test_non_dict_blocks_inside_content_are_ignored(self):
        self._write([
            _line("user", "开始"),
            _line("assistant", ["stray string block", 42, {"type": "text", "text": "真正的总结"},
                                {"type": "text"}, {"type": "tool_use", "text": "no"}]),
        ])
        self.assertEqual(executor.harvest_delivery(SID)["delivered_summary"], "真正的总结")

    def test_image_only_user_turn_resets_the_delivery_window(self):
        self._write([
            _line("user", "开始"),
            _line("assistant", _blocks("上一轮的总结")),
            json.dumps({"type": "user", "message": {"content": [{"type": "image", "source": {}}]}}),
            _line("assistant", _blocks("这一轮的总结")),
        ])
        self.assertEqual(executor.harvest_delivery(SID)["delivered_summary"], "这一轮的总结")

    def test_unreadable_first_match_is_skipped(self):
        first = self._write([_line("assistant", _blocks("坏文件"))], project="a-first")
        self._write([_line("assistant", _blocks("好文件"))], project="b-second",
                    sid="abcd1234-ffff-4000-8000-000000000002")
        real_open = open

        def flaky_open(path, *a, **kw):
            if str(path) == str(first):
                raise OSError("EACCES")
            return real_open(path, *a, **kw)
        with mock.patch("builtins.open", flaky_open):
            out = executor.harvest_delivery(SID)
        self.assertEqual(out["delivered_summary"], "好文件")

    def test_empty_short_id_and_internal_errors_answer_all_none(self):
        empty = {"delivered_summary": None, "final_draft": None, "card_title": None}
        self.assertEqual(executor.harvest_delivery(""), empty)
        self.assertEqual(executor.harvest_delivery("-abc"), empty)
        self._write([_line("assistant", _blocks("x"))])
        with mock.patch.object(executor, "_fence_marker_idxs", side_effect=RuntimeError("boom")):
            self.assertEqual(executor.harvest_delivery(SID), empty)


class UserTurnPredicateTestCase(unittest.TestCase):
    def test_answers_are_real_booleans(self):
        turn = {"type": "user", "message": {"content": "hi"}}
        self.assertIs(transcripts.is_user_turn(turn), True)
        self.assertIs(transcripts.is_user_turn({"type": "assistant", "message": {"content": "hi"}}), False)
        self.assertIs(transcripts.is_user_turn({"type": "user", "message": "not-an-object"}), False)
        self.assertIs(transcripts.is_user_turn({"type": "user", "message": {"content": 7}}), False)
        self.assertIs(transcripts.is_user_turn({"type": "user", "isSidechain": True,
                                                "message": {"content": "hi"}}), False)
        self.assertIs(transcripts.is_user_turn(
            {"type": "user", "message": {"content": [{"type": "image"}]}}), True)


class AssistantTextsDefaultTestCase(_Home):
    def test_default_keeps_texts_before_the_last_user_turn(self):
        path = self._write([_line("assistant", "a1"), _line("user", "u"), _line("assistant", "a2")])
        self.assertEqual(transcripts.assistant_texts(path), ["a1", "a2"])
        self.assertEqual(transcripts.assistant_texts(path, since_last_user=True), ["a2"])


class PlainTextEdgeTestCase(_Home):
    def test_odd_shapes_are_tolerated_and_first_user_turn_dropped(self):
        self._write([
            "garbage",
            json.dumps([1, 2]),
            _line("user", "dispatch prompt boilerplate"),
            json.dumps({"type": "user", "message": {"content": 7}}),
            _line("user", "第二条用户消息（字符串）"),
            json.dumps({"type": "user", "message": {"content": [{"type": "image"}]}}),
            json.dumps({"type": "assistant", "message": "not-an-object"}),
            json.dumps({"type": "assistant", "message": {"content": 42}}),
            _line("assistant", "助手字符串回复"),
            _line("assistant", _blocks("助手块回复", "")),
            json.dumps({"type": "system", "message": {"content": "ignored"}}),
        ])
        text = executor.transcript_plain_text(SID)
        self.assertEqual(text.split("\n"), ["第二条用户消息（字符串）", "助手字符串回复", "助手块回复"])

    def test_unreadable_first_match_is_skipped(self):
        first = self._write([_line("user", "p"), _line("assistant", "坏")], project="a-first")
        self._write([_line("user", "p"), _line("assistant", "好")], project="b-second",
                    sid="abcd1234-ffff-4000-8000-000000000002")
        real_open = open

        def flaky_open(path, *a, **kw):
            if str(path) == str(first):
                raise OSError("EACCES")
            return real_open(path, *a, **kw)
        with mock.patch("builtins.open", flaky_open):
            self.assertEqual(executor.transcript_plain_text(SID), "好")

    def test_internal_error_is_none(self):
        self._write([_line("user", "p"), _line("assistant", "x")])
        with mock.patch.object(Path, "glob", side_effect=RuntimeError("boom")):
            self.assertIsNone(executor.transcript_plain_text(SID))

    def test_only_boilerplate_is_none_and_cap_is_a_tail(self):
        self._write([_line("user", "dispatch prompt only")])
        self.assertIsNone(executor.transcript_plain_text(SID))
        self._write([_line("user", "p"), _line("assistant", "a" * 30), _line("assistant", "b" * 30)])
        self.assertEqual(executor.transcript_plain_text(SID, cap=10), "b" * 10)
        self.assertEqual(len(executor.transcript_plain_text(SID, cap=61)), 61)

    def test_default_cap_is_fifty_thousand_chars(self):
        self._write([_line("user", "p"), _line("assistant", "x" * 60_000)])
        self.assertEqual(len(executor.transcript_plain_text(SID)), 50_000)


if __name__ == "__main__":
    unittest.main()
