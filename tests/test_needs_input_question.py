"""§39 needs_input question surfacing — extraction, projection, notification.

executor.extract_question must follow the SAME transcript discipline as
harvest_delivery (short-id glob, sidechain/isMeta/tool-result-aware, scope =
after the last real user turn) so a rework/answer injection can never leak a
previous round's text as the current question. dashboard projects it onto
needs_input rows behind a (sid, transcript-signature) memo — a genuinely
blocked agent's idle transcript must not be re-parsed every ~10s pass.

$HOME is redirected per test: extraction (and the dashboard cache signature)
glob ``~/.claude/projects`` — the suite must never read real transcripts.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import actd, executor
from act.lib import config, dashboard, notify
from act.lib.registry import Requirement

FULL_SID = "beefcafe-0000-4000-8000-000000000001"


def _user(text):
    return {"type": "user", "message": {"content": text}}


def _assistant(text):
    return {"type": "assistant",
            "message": {"content": [{"type": "text", "text": text}]}}


class QuestionExtractionTestCase(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="question-home-")
        patcher = mock.patch.dict(os.environ, {"HOME": self.home})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.proj = Path(self.home) / ".claude" / "projects" / "x"
        self.proj.mkdir(parents=True)

    def _write(self, lines, sid=FULL_SID):
        path = self.proj / f"{sid}.jsonl"
        path.write_text("\n".join(json.dumps(x) for x in lines) + "\n",
                        encoding="utf-8")
        return path

    def test_blocked_question_is_last_assistant_text(self):
        self._write([
            _user("dispatch prompt"),
            _assistant("先做点工作"),
            _assistant("要用 A 方案还是 B 方案？"),
        ])
        self.assertEqual(executor.extract_question(FULL_SID),
                         "要用 A 方案还是 B 方案？")

    def test_scope_resets_at_last_real_user_turn(self):
        # a rework/answer injection is a REAL user turn — the previous round's
        # question must never be resurrected; no assistant text after it = None
        self._write([
            _user("dispatch prompt"),
            _assistant("上一轮的问题？"),
            _user("OWNER ANSWER:\n用 A"),
        ])
        self.assertIsNone(executor.extract_question(FULL_SID))

    def test_tool_results_and_sidechain_do_not_count(self):
        # tool_result lines arrive as type=user but are NOT user turns; the
        # sidechain assistant line must not be surfaced either
        self._write([
            _user("dispatch prompt"),
            _assistant("真正的问题？"),
            {"type": "user", "toolUseResult": {"ok": True},
             "message": {"content": [{"type": "tool_result", "content": "x"}]}},
            {"type": "assistant", "isSidechain": True,
             "message": {"content": [{"type": "text", "text": "子 agent 的话"}]}},
        ])
        self.assertEqual(executor.extract_question(FULL_SID), "真正的问题？")

    def test_clipped_to_500_chars_with_honest_ellipsis(self):
        # §39.1: a clipped question must SAY it is clipped — no surface may
        # present a truncated tail as the complete text
        self._write([_user("p"), _assistant("问" * 600)])
        q = executor.extract_question(FULL_SID)
        self.assertEqual(len(q), 500)
        self.assertTrue(q.endswith("…"))
        # at/under the bound → untouched, no ellipsis
        self._write([_user("p"), _assistant("答" * 500)])
        self.assertEqual(executor.extract_question(FULL_SID), "答" * 500)

    def test_no_transcript_returns_none(self):
        self.assertIsNone(executor.extract_question(FULL_SID))

    def test_short_sid_never_globs_everything(self):
        # an unrelated transcript exists; a <8-char sid must return None, not
        # glob-bind that transcript's text as this card's question
        self._write([_user("p"), _assistant("别人的问题？")], sid="aaaa1111-x")
        self.assertIsNone(executor.extract_question("abc"))
        self.assertIsNone(executor.extract_question(""))


class DashboardQuestionTestCase(unittest.TestCase):
    def setUp(self):
        self.cfg = config.Config()
        self.home = tempfile.mkdtemp(prefix="dashq-home-")
        patcher = mock.patch.dict(os.environ, {"HOME": self.home})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.proj = Path(self.home) / ".claude" / "projects" / "x"
        self.proj.mkdir(parents=True)
        dashboard._QUESTION_CACHE.clear()
        self.addCleanup(dashboard._QUESTION_CACHE.clear)

    def _req(self, execution=None):
        return Requirement(
            id="R-500", title="需输入的任务", status="executing",
            execution=execution or {"session_id": FULL_SID})

    def _agent(self, waiting_for=None):
        return {"sessionId": FULL_SID, "id": FULL_SID.split("-")[0],
                "state": "blocked", "pid": 4242, "cwd": "/tmp",
                "waiting_for": waiting_for}

    def _write_transcript(self, question="要 A 还是 B？"):
        path = self.proj / f"{FULL_SID}.jsonl"
        path.write_text(
            json.dumps(_user("p")) + "\n" + json.dumps(_assistant(question)) + "\n",
            encoding="utf-8")
        return path

    def test_needs_input_row_carries_question(self):
        self._write_transcript()
        dash = dashboard.build_dashboard(
            reqs=[self._req()], agents=[self._agent()], cfg=self.cfg)
        row = dash["needs_input"][0]
        self.assertEqual(row["question"], "要 A 还是 B？")
        # §39: the bare "input" fallback yields to a real question
        self.assertIsNone(row["waiting_for"])

    def test_roster_waiting_for_still_passes_through(self):
        self._write_transcript()
        dash = dashboard.build_dashboard(
            reqs=[self._req()], agents=[self._agent(waiting_for="permission")],
            cfg=self.cfg)
        row = dash["needs_input"][0]
        self.assertEqual(row["waiting_for"], "permission")
        self.assertEqual(row["question"], "要 A 还是 B？")

    def test_no_transcript_keeps_input_fallback_and_omits_question(self):
        dash = dashboard.build_dashboard(
            reqs=[self._req()], agents=[self._agent()], cfg=self.cfg)
        row = dash["needs_input"][0]
        self.assertEqual(row["waiting_for"], "input")
        self.assertNotIn("question", row)

    def test_answer_failure_last_error_is_visible_on_the_row(self):
        dash = dashboard.build_dashboard(
            reqs=[self._req(execution={
                "session_id": FULL_SID,
                "last_error": "answer failed: transcript missing"})],
            agents=[self._agent()], cfg=self.cfg)
        row = dash["needs_input"][0]
        self.assertEqual(row["last_error"], "answer failed: transcript missing")

    def test_question_cached_until_transcript_changes(self):
        path = self._write_transcript("第一问？")
        with mock.patch.object(executor, "extract_question",
                               wraps=executor.extract_question) as spy:
            self.assertEqual(dashboard._question_cached(FULL_SID), "第一问？")
            self.assertEqual(dashboard._question_cached(FULL_SID), "第一问？")
            self.assertEqual(spy.call_count, 1)  # idle transcript: memo hit
            # appended transcript (new mtime/size) invalidates immediately
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(_assistant("第二问？")) + "\n")
            self.assertEqual(dashboard._question_cached(FULL_SID), "第二问？")
            self.assertEqual(spy.call_count, 2)


class NeedsInputNotifyTestCase(unittest.TestCase):
    def setUp(self):
        # v0.42 §15: copy follows AIASSISTANT_UI_LANG > persisted > system
        # locale — pin zh so the zh-copy assertions stay locale-independent.
        lang = mock.patch.dict(os.environ, {"AIASSISTANT_UI_LANG": "zh"})
        lang.start()
        self.addCleanup(lang.stop)

    def test_msg_needs_input_carries_question_and_real_surface(self):
        t, b = notify.msg_needs_input("写周报", "要 A 还是 B？")
        self.assertIn("要 A 还是 B？", b)
        self.assertIn("运行中", b)
        self.assertIn("回答", b)

    def test_msg_needs_input_without_question_keeps_old_shape(self):
        t, b = notify.msg_needs_input("写周报")
        self.assertIn("写周报", b)
        self.assertNotIn("在问", b)

    def test_question_snippet_clipped_to_120(self):
        _, b = notify.msg_needs_input("t", "问" * 200)
        self.assertIn("问" * 120 + "…", b)
        self.assertNotIn("问" * 121, b)

    def test_msg_answer_failed_names_reason_and_fallback(self):
        t, b = notify.msg_answer_failed("写周报", "transcript missing")
        self.assertIn("transcript missing", b)
        self.assertIn("在终端接管会话", b)

    def test_msg_answer_not_delivered_names_cause_and_where_the_text_went(self):
        # §39.2 stale ≠ silent: every variant must say the text is SAVED
        for kind, needle in (("working", "正在工作"),
                             ("review", "待验收"),
                             ("recent", "刚有一条回答送达"),
                             ("oversize", "4000"),
                             ("moved", "不在需输入")):
            t, b = notify.msg_answer_not_delivered("写周报", kind)
            self.assertIn("没有送出去", t)
            self.assertIn(needle, b)
            self.assertIn("备注", b)  # the archived-in-notes promise

    def test_transition_notification_carries_question(self):
        prev = {"needs_approval": [], "running": [{"id": "R-9", "name": "任务"}],
                "needs_input": [], "review": []}
        curr = {"needs_approval": [], "running": [],
                "needs_input": [{"id": "R-9", "name": "任务",
                                 "question": "要 A 还是 B？"}],
                "review": []}
        msgs = actd.detect_transitions(prev, curr)
        self.assertEqual(len(msgs), 1)
        self.assertIn("要 A 还是 B？", msgs[0][1])


if __name__ == "__main__":
    unittest.main()
