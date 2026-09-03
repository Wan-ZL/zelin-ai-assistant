"""act/radar_claude_sessions — transcript parsing primitives (§22): the head/
tail readers, entry text extraction, gist/title shaping and the cwd/binding
facts a candidate is built from.

Pinned (P3 mutation net):
- ``_entry_text``: only user/assistant main-chain lines have text; string and
  block-list content; sidechain/meta/tool lines -> "";
- ``_clean_head``: tag wrappers stripped, whitespace collapsed, over-limit
  text truncated with an ellipsis at exactly ``limit`` chars;
- ``_head_entries`` stops at the line and byte budgets; ``_tail_entries``
  drops the partial first line of a tail window; blank/garbage lines skipped;
- ``_parse_ts``: Z suffix, naive -> UTC, junk -> None;
- ``_candidate_from_file``: ai-title wins the title, the LAST main-chain cwd
  wins (head cwd only as fallback), the last-prompt marker feeds the gist
  when the head has no user text, the newest timestamp is the activity,
  the file mtime is the fallback, a foreign sessionId on either window
  flags ``session_mismatch``, bookkeeping-only files are None;
- CLI: ``--scan`` without a Claude dir / with one, ``--once``, no flags.
"""
import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first

from act import radar_claude_sessions as rcs
from act.lib import config


def _iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _user(text, ts=None, **extra):
    e = {"type": "user", "message": {"role": "user", "content": text}}
    if ts is not None:
        e["timestamp"] = _iso(ts)
    e.update(extra)
    return e


def _assistant(text, ts=None, **extra):
    e = {"type": "assistant",
         "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}
    if ts is not None:
        e["timestamp"] = _iso(ts)
    e.update(extra)
    return e


class EntryTextTestCase(unittest.TestCase):
    def test_roles_and_shapes(self):
        self.assertEqual(rcs._entry_text(_user("  hi  ")), "hi")
        self.assertEqual(rcs._entry_text(_assistant("yo")), "yo")
        blocks = {"type": "assistant", "message": {"content": [
            {"type": "text", "text": "a"}, {"type": "tool_use", "name": "x"},
            {"type": "text", "text": ""}, "junk", {"type": "text", "text": "b"}]}}
        self.assertEqual(rcs._entry_text(blocks), "a\nb")
        self.assertEqual(rcs._entry_text({"type": "assistant",
                                          "message": {"content": 42}}), "")

    def test_non_conversation_lines_are_empty(self):
        self.assertEqual(rcs._entry_text({"type": "queue-operation"}), "")
        self.assertEqual(rcs._entry_text(_user("x", isSidechain=True)), "")
        self.assertEqual(rcs._entry_text(_user("x", isMeta=True)), "")
        self.assertEqual(rcs._entry_text({"type": "user", "message": "not a dict"}), "")


class CleanHeadTestCase(unittest.TestCase):
    def test_strip_collapse_truncate(self):
        # only the <tag> wrappers go; their inner text stays
        self.assertEqual(rcs._clean_head("<command-name>/foo</command-name>  a   b\n c", 80),
                         "/foo a b c")
        long = "x" * 100
        out = rcs._clean_head(long, 10)
        self.assertEqual(len(out), 10)
        self.assertTrue(out.endswith("…"))
        self.assertEqual(rcs._clean_head("x" * 10, 10), "x" * 10)   # at limit: untouched
        self.assertEqual(rcs._clean_head(None, 10), "")


class WindowReadersTestCase(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="rcs-win-"))
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def _write(self, lines):
        p = self.dir / "s.jsonl"
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return p

    def test_parse_lines_skips_blank_and_garbage(self):
        out = rcs._parse_lines('\n{"a": 1}\nnot json\n\n[1,2]\n{"b": 2}\n')
        self.assertEqual(out, [{"a": 1}, {"b": 2}])

    def test_head_stops_at_line_budget(self):
        p = self._write([json.dumps({"i": i}) for i in range(rcs._HEAD_MAX_LINES + 10)])
        head = rcs._head_entries(p)
        self.assertEqual(len(head), rcs._HEAD_MAX_LINES)
        self.assertEqual(head[-1], {"i": rcs._HEAD_MAX_LINES - 1})

    def test_head_stops_at_byte_budget(self):
        big = json.dumps({"pad": "x" * 1000})
        p = self._write([big] * 20)
        with mock.patch.object(rcs, "_HEAD_MAX_BYTES", 2500):
            head = rcs._head_entries(p)
        self.assertEqual(len(head), 3)    # 1000, 2000 < 2500 -> third read, then stop

    def test_head_missing_file_is_empty(self):
        self.assertEqual(rcs._head_entries(self.dir / "nope.jsonl"), [])
        self.assertEqual(rcs._tail_entries(self.dir / "nope.jsonl"), [])

    def test_tail_window_drops_the_partial_first_line(self):
        lines = [json.dumps({"i": i, "pad": "y" * 50}) for i in range(40)]
        p = self._write(lines)
        size = p.stat().st_size
        with mock.patch.object(rcs, "_TAIL_MAX_BYTES", size // 2):
            tail = rcs._tail_entries(p)
        self.assertTrue(tail)
        self.assertLess(len(tail), 40)
        self.assertEqual(tail[-1]["i"], 39)
        # every recovered entry is whole (the cut line was dropped, not mangled)
        self.assertTrue(all("pad" in e for e in tail))
        # small file: read whole
        self.assertEqual(len(rcs._tail_entries(p)), 40)

    def test_tail_window_with_no_newline_is_empty(self):
        p = self.dir / "one.jsonl"
        p.write_text("x" * 100, encoding="utf-8")
        with mock.patch.object(rcs, "_TAIL_MAX_BYTES", 10):
            self.assertEqual(rcs._tail_entries(p), [])


class ParseTsTestCase(unittest.TestCase):
    def test_forms(self):
        z = rcs._parse_ts("2026-07-13T10:00:00Z")
        self.assertEqual(z, datetime(2026, 7, 13, 10, tzinfo=timezone.utc))
        naive = rcs._parse_ts("2026-07-13T10:00:00")
        self.assertEqual(naive.tzinfo, timezone.utc)
        self.assertIsNone(rcs._parse_ts(""))
        self.assertIsNone(rcs._parse_ts(None))
        self.assertIsNone(rcs._parse_ts(123))
        self.assertIsNone(rcs._parse_ts("not a time"))


class CandidateTestCase(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="rcs-cand-"))
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.now = datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc)

    def _write(self, sid, entries, mtime=None):
        p = self.dir / f"{sid}.jsonl"
        for e in entries:
            e.setdefault("sessionId", sid)
        p.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")
        if mtime is not None:
            os.utime(p, (mtime.timestamp(), mtime.timestamp()))
        return p

    def test_title_gist_cwd_and_activity(self):
        p = self._write("s1", [
            {"type": "ai-title", "aiTitle": "  Fix the login bug  "},
            _user("please fix login", self.now - timedelta(hours=2), cwd="/head/cwd"),
            _assistant("looking", self.now - timedelta(hours=1), cwd="/mid/cwd"),
            _assistant("done — should I also add tests?", self.now, cwd="/tail/cwd"),
        ])
        c = rcs._candidate_from_file(p)
        self.assertEqual(c["title"], "Fix the login bug")
        self.assertEqual(c["gist"], "please fix login → done — should I also add tests?")
        self.assertEqual(c["project_dir"], "/tail/cwd")      # last main-chain cwd wins
        self.assertEqual(c["project"], "cwd")
        self.assertEqual(c["last_activity"], _iso(self.now))
        self.assertTrue(c["ended_waiting_on_user"])
        self.assertFalse(c["session_mismatch"])
        self.assertFalse(c["answered"])

    def test_head_cwd_is_the_fallback_and_title_falls_back_to_first_user(self):
        p = self._write("s2", [
            _user("Write the weekly report", cwd="/only/head"),
            _assistant("Here is the report.", cwd=""),
        ])
        c = rcs._candidate_from_file(p)
        self.assertEqual(c["project_dir"], "/only/head")
        self.assertEqual(c["title"], "Write the weekly report")
        self.assertFalse(c["ended_waiting_on_user"])

    def test_last_prompt_marker_feeds_gist_when_head_has_no_user_text(self):
        # head window has only an assistant line; the last-prompt marker
        # (Claude Code's bookkeeping) supplies the user side of the gist
        p = self._write("s3", [
            _assistant("Sure, done.", cwd="/p"),
            {"type": "last-prompt", "lastPrompt": "What is the capital of France?"},
        ])
        c = rcs._candidate_from_file(p)
        self.assertTrue(c["gist"].startswith("What is the capital of France? →"))
        self.assertEqual(c["title"], "What is the capital of France?")
        # the heuristic never judges the lastPrompt fallback
        self.assertFalse(c["answered"])

    def test_answered_closed_loop_qa(self):
        p = self._write("s4", [
            _user("What is the capital of France?", cwd="/p"),
            _assistant("Paris.", cwd="/p"),
        ])
        self.assertTrue(rcs._candidate_from_file(p)["answered"])
        p2 = self._write("s5", [
            _user("What is the capital of France?", cwd="/p"),
            _assistant("Paris. I'll send the full list next.", cwd="/p"),
        ])
        self.assertFalse(rcs._candidate_from_file(p2)["answered"])   # follow-up promised

    def test_file_mtime_is_the_activity_fallback(self):
        when = self.now - timedelta(days=2)
        p = self._write("s6", [_user("no timestamps here", cwd="/p")], mtime=when)
        c = rcs._candidate_from_file(p)
        self.assertEqual(c["last_activity"], _iso(when))

    def test_foreign_session_id_flags_mismatch_from_either_window(self):
        head_foreign = self._write("s7", [
            _user("work", cwd="/p", sessionId="someone-else"),
            _assistant("ok", cwd="/p"),
        ])
        self.assertTrue(rcs._candidate_from_file(head_foreign)["session_mismatch"])
        tail_foreign = self._write("s8", [
            _user("work", cwd="/p"),
            _assistant("ok", cwd="/p", sessionId="someone-else"),
        ])
        self.assertTrue(rcs._candidate_from_file(tail_foreign)["session_mismatch"])
        # bookkeeping lines never bind
        meta_foreign = self._write("s9", [
            _user("work", cwd="/p"),
            {"type": "queue-operation", "sessionId": "someone-else"},
            _assistant("ok", cwd="/p"),
        ])
        self.assertFalse(rcs._candidate_from_file(meta_foreign)["session_mismatch"])

    def test_bookkeeping_only_file_is_none(self):
        p = self._write("s10", [{"type": "queue-operation"}, {"type": "snapshot"}])
        self.assertIsNone(rcs._candidate_from_file(p))

    def test_project_name_falls_back_to_the_parent_dir(self):
        p = self._write("s11", [_user("work with no cwd")])
        c = rcs._candidate_from_file(p)
        self.assertEqual(c["project_dir"], "")
        self.assertEqual(c["project"], self.dir.name)


class CliTestCase(unittest.TestCase):
    def setUp(self):
        self.claude_dir = Path(tempfile.mkdtemp(prefix="claude-cfg-"))
        os.environ["CLAUDE_CONFIG_DIR"] = str(self.claude_dir)
        self.addCleanup(os.environ.pop, "CLAUDE_CONFIG_DIR", None)
        self.addCleanup(shutil.rmtree, self.claude_dir, ignore_errors=True)
        config.ensure_state_dirs()

    def _run(self, argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = rcs._main(argv)
        return rc, buf.getvalue()

    def test_scan_without_claude_dir(self):
        rc, out = self._run(["--scan"])
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual(payload["ok"], False)
        self.assertEqual(payload["reason"], "no_claude_dir")

    def test_scan_with_claude_dir_prints_candidates(self):
        (self.claude_dir / "projects").mkdir()
        with mock.patch.object(rcs, "scan", return_value=[{"session_id": "abc"}]) as scan:
            rc, out = self._run(["--scan", "--window", "3"])
        self.assertEqual(rc, 0)
        scan.assert_called_once_with(3)
        payload = json.loads(out)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["candidates"], [{"session_id": "abc"}])

    def test_once_imports(self):
        with mock.patch.object(rcs, "run_once", return_value=2) as run_once:
            rc, out = self._run(["--once", "--all"])
        self.assertEqual(rc, 0)
        run_once.assert_called_once_with(rcs.DEFAULT_WINDOW_DAYS, include_all=True)
        self.assertIn("2 card(s)", out)

    def test_no_flags_prints_help_and_exits_2(self):
        rc, out = self._run([])
        self.assertEqual(rc, 2)
        self.assertIn("usage:", out)


if __name__ == "__main__":
    unittest.main()
