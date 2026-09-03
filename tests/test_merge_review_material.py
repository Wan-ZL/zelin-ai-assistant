"""merge_review material gathering + CLI (merge-review 契约 五, CONTRACT §21).

The evidence bundle per card: registry YAML, delivery fields, the transcript
tail (same short-id glob over ~/.claude/projects as executor's harvest) and
the worktree ``git log`` / ``git diff --stat`` — every source is best-effort
and「失败跳过」. No real subprocess: ``git`` is stubbed at
``merge_review.subprocess.run``; transcripts are JSONL fixtures under a
sandboxed $HOME.

Characterization net for the P3a CRAP refactor (these paths had 4–17%
coverage): every assertion was recorded against the pre-refactor module.
"""
import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first

from act import merge_review
from act.lib.registry import Requirement


def _jsonl(path: Path, records) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for r in records:
            fh.write(r if isinstance(r, str) else json.dumps(r))
            fh.write("\n")


def _msg(role, content, **extra):
    d = {"type": role, "message": {"content": content}}
    d.update(extra)
    return d


class TailMessagesTestCase(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="mr-tail-"))

    def test_filters_and_caps(self):
        p = self.dir / "t.jsonl"
        _jsonl(p, [
            "{not json",
            "[1, 2]",
            _msg("assistant", "side", isSidechain=True),
            _msg("system", "ignored"),
            {"type": "user"},                       # no message dict
            _msg("user", "  hello  "),
            _msg("assistant", [{"type": "text", "text": "a"}, {"type": "tool_use"},
                               "str-block", {"type": "text", "text": None}]),
            _msg("assistant", 42),                  # neither str nor list
            _msg("assistant", "   "),               # empty after strip
            _msg("user", "x" * (merge_review._MSG_CAP + 5)),
        ])
        msgs = merge_review._tail_messages(p, 10)
        self.assertEqual(msgs[0], "[user] hello")
        self.assertEqual(msgs[1], "[assistant] a")
        self.assertEqual(len(msgs[2]), len("[user] ") + merge_review._MSG_CAP)
        self.assertEqual(len(msgs), 3)
        self.assertEqual(merge_review._tail_messages(p, 1), [msgs[-1]])


class TranscriptTailTextTestCase(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="mr-home-")
        patcher = mock.patch.dict(os.environ, {"HOME": self.home})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.proj = Path(self.home) / ".claude" / "projects"

    def test_first_usable_transcript_wins(self):
        _jsonl(self.proj / "a" / "abcd1234-first.jsonl", [_msg("system", "nope")])
        _jsonl(self.proj / "b" / "abcd1234-second.jsonl", [_msg("user", "real")])
        self.assertEqual(merge_review._transcript_tail_text("abcd1234-zzzz"), "[user] real")

    def test_missing_or_empty_gives_none(self):
        self.assertIsNone(merge_review._transcript_tail_text("nothing-here"))
        self.assertIsNone(merge_review._transcript_tail_text(""))
        self.assertIsNone(merge_review._transcript_tail_text("-dash-first"))

    def test_unreadable_file_is_skipped(self):
        _jsonl(self.proj / "a" / "abcd1234-x.jsonl", [_msg("user", "real")])
        with mock.patch.object(merge_review, "_tail_messages",
                               side_effect=OSError("denied")):
            self.assertIsNone(merge_review._transcript_tail_text("abcd1234"))

    def test_glob_failure_is_swallowed(self):
        with mock.patch.object(merge_review.Path, "glob", side_effect=RuntimeError("x")):
            self.assertIsNone(merge_review._transcript_tail_text("abcd1234"))


def _git(rc_log=0, log="c0ffee first", rc_diff=0, diff=" a.py | 1 +"):
    def run(argv, **kw):
        if argv[1] == "log":
            return subprocess.CompletedProcess(argv, rc_log, stdout=log, stderr="")
        return subprocess.CompletedProcess(argv, rc_diff, stdout=diff, stderr="")
    return run


class WorktreeGitTextTestCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="mr-wt-")

    def test_missing_dir_or_none_skips(self):
        self.assertIsNone(merge_review._worktree_git_text(None))
        self.assertIsNone(merge_review._worktree_git_text("/definitely/not/here"))

    def test_both_sections(self):
        with mock.patch.object(merge_review.subprocess, "run", side_effect=_git()):
            text = merge_review._worktree_git_text(self.dir)
        self.assertEqual(text, "$ git log --oneline -5\nc0ffee first\n"
                               "$ git diff --stat\na.py | 1 +")

    def test_partial_and_empty(self):
        with mock.patch.object(merge_review.subprocess, "run",
                               side_effect=_git(rc_log=128, diff="  ")):
            self.assertIsNone(merge_review._worktree_git_text(self.dir))
        with mock.patch.object(merge_review.subprocess, "run",
                               side_effect=_git(log="", rc_diff=0)):
            self.assertEqual(merge_review._worktree_git_text(self.dir),
                             "$ git diff --stat\na.py | 1 +")

    def test_subprocess_failure_skips(self):
        with mock.patch.object(merge_review.subprocess, "run",
                               side_effect=OSError("no git")):
            self.assertIsNone(merge_review._worktree_git_text(self.dir))
        with mock.patch.object(merge_review.subprocess, "run",
                               side_effect=subprocess.TimeoutExpired("git", 15)):
            self.assertIsNone(merge_review._worktree_git_text(self.dir))


class InferCwdTestCase(unittest.TestCase):
    def _req(self, **kw):
        return Requirement.from_dict({"id": "R-1", "title": "t", "status": "review", **kw})

    def test_transcript_cwd_wins(self):
        with mock.patch.object(merge_review.transcripts, "transcript_cwd", return_value="/wt"):
            self.assertEqual(merge_review._infer_cwd(self._req(target_repo="~/x"), "sid"), "/wt")

    def test_falls_back_to_target_repo_then_none(self):
        with mock.patch.object(merge_review.transcripts, "transcript_cwd", return_value=None):
            self.assertEqual(merge_review._infer_cwd(self._req(target_repo="~/x"), "sid"),
                             Path("~/x").expanduser())
        with mock.patch.object(merge_review.transcripts, "transcript_cwd", side_effect=RuntimeError()):
            self.assertIsNone(merge_review._infer_cwd(self._req(), "sid"))
        self.assertIsNone(merge_review._infer_cwd(self._req(), None))


class MaterialForTestCase(unittest.TestCase):
    def test_missing_card(self):
        with mock.patch.object(merge_review, "load", return_value=None):
            self.assertEqual(merge_review._material_for("R-404"),
                             "## 卡片 R-404\n(registry 中不存在——材料缺失)")

    def test_full_bundle(self):
        req = Requirement.from_dict({
            "id": "R-1", "title": "t", "status": "review", "target_repo": "/tmp",
            "execution": {"delivered_summary": "摘要", "final_draft": "稿" * 3000,
                          "aborted_session_id": "abcd1234"},
        })
        with mock.patch.object(merge_review, "load", return_value=req), \
                mock.patch.object(merge_review, "_transcript_tail_text",
                                  return_value="[user] hi") as tail, \
                mock.patch.object(merge_review, "_infer_cwd", return_value="/wt"), \
                mock.patch.object(merge_review, "_worktree_git_text",
                                  return_value="$ git log") as git:
            text = merge_review._material_for("R-1")
        tail.assert_called_once_with("abcd1234")
        git.assert_called_once_with("/wt")
        self.assertIn("## 卡片 R-1（status=review）", text)
        self.assertIn("### registry YAML\nid: R-1", text)
        self.assertIn("### 交付摘要 delivered_summary\n摘要", text)
        self.assertIn("### 交付成稿 final_draft（截断）\n" + "稿" * merge_review._DRAFT_CAP + "\n", text)
        self.assertIn("### session transcript 尾部（最近 ≤30 条 assistant/user 文本）\n[user] hi", text)
        self.assertIn("### worktree /wt\n$ git log", text)

    def test_yaml_dump_failure_degrades(self):
        req = Requirement.from_dict({"id": "R-2", "title": "t2", "status": "detected"})
        with mock.patch.object(merge_review, "load", return_value=req), \
                mock.patch.object(merge_review.yaml, "safe_dump",
                                  side_effect=merge_review.yaml.YAMLError("x")), \
                mock.patch.object(merge_review, "_worktree_git_text", return_value=None):
            text = merge_review._material_for("R-2")
        self.assertIn("### registry YAML\n(dump failed) title='t2'", text)
        self.assertNotIn("transcript", text)


class MainTestCase(unittest.TestCase):
    def _main(self, argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = merge_review._main(argv)
        return rc, buf.getvalue()

    def test_usage(self):
        rc, out = self._main([])
        self.assertEqual(rc, 2)
        self.assertIn("usage:", out)

    def test_missing_job(self):
        with mock.patch.object(merge_review, "analyze_suggestion",
                               side_effect=FileNotFoundError("no job")):
            rc, out = self._main(["MS-x"])
        self.assertEqual(rc, 1)
        self.assertIn("error: no job", out)

    def test_unexpected_exception_lands_on_failed(self):
        with mock.patch.object(merge_review, "analyze_suggestion",
                               side_effect=RuntimeError("boom")), \
                mock.patch.object(merge_review, "mark_failed") as mf:
            rc, out = self._main(["MS-x"])
        mf.assert_called_once_with("MS-x", "boom")
        self.assertEqual(rc, 1)
        self.assertIn("analysis failed: boom", out)

    def test_done_and_failed_exit_codes(self):
        with mock.patch.object(merge_review, "analyze_suggestion",
                               return_value={"status": "done", "verdict": "merge"}):
            rc, out = self._main(["MS-y"])
        self.assertEqual(rc, 0)
        self.assertIn("MS-y -> done (verdict=merge, error=None)", out)
        with mock.patch.object(merge_review, "analyze_suggestion",
                               return_value={"status": "failed", "error": "e"}):
            self.assertEqual(self._main(["MS-y"])[0], 1)


class SmallHelpersTestCase(unittest.TestCase):
    def test_coerce_action_plan(self):
        self.assertEqual(merge_review._coerce_action_plan(["a", " ", 2]), ["a", "2"])
        self.assertEqual(merge_review._coerce_action_plan("x\n\n y "), ["x", "y"])
        self.assertEqual(merge_review._coerce_action_plan("  "), [])
        self.assertEqual(merge_review._coerce_action_plan(None), [])

    def test_mark_failed_leaves_dismissed_alone(self):
        job = {"id": "MS-d", "ids": ["a", "b"], "status": "dismissed"}
        with mock.patch.object(merge_review, "load_job", return_value=job), \
                mock.patch.object(merge_review, "write_job") as wj:
            self.assertIs(merge_review.mark_failed("MS-d", "x"), job)
        wj.assert_not_called()

    def test_dismiss_job_unknown_or_idless(self):
        with mock.patch.object(merge_review, "load_job", return_value=None):
            self.assertIsNone(merge_review.dismiss_job("MS-none"))
        self.assertIsNone(merge_review.dismiss_job({"status": "done"}))

    def test_validate_groups_rejects_bad_shapes(self):
        ids = ["a", "b", "c"]
        self.assertIsNone(merge_review._validate_groups("nope", ids))
        self.assertIsNone(merge_review._validate_groups([], ids))
        self.assertIsNone(merge_review._validate_groups(["x"], ids))
        self.assertIsNone(merge_review._validate_groups([{"primary": "z", "ids": []}], ids))
        self.assertIsNone(merge_review._validate_groups([{"primary": "a", "ids": "b"}], ids))
        self.assertIsNone(merge_review._validate_groups([{"primary": "a", "ids": [""]}], ids))
        self.assertIsNone(merge_review._validate_groups(
            [{"primary": "a", "ids": ["b"]}, {"primary": "b", "ids": ["c"]}], ids))
        self.assertIsNone(merge_review._validate_groups([{"primary": "a", "ids": ["a"]}], ids))
        self.assertEqual(merge_review._validate_groups(
            [{"primary": "a", "ids": ["b", "a", "b"], "reason": " r "}], ids),
            [{"primary": "a", "ids": ["a", "b"], "reason": "r"}])

    def test_validate_result_partition_primary_fallback(self):
        data = {"verdict": "PARTITION", "primary": "zz", "confidence": "?",
                "groups": [{"primary": "b", "ids": ["a"]}]}
        out = merge_review._validate_result(data, ["a", "b"])
        self.assertEqual(out["primary"], "b")
        self.assertEqual(out["confidence"], "medium")
        self.assertEqual(out["groups"][0]["ids"], ["b", "a"])
        bad = merge_review._validate_result(
            {"verdict": "partition", "groups": "x", "primary": "b"}, ["a", "b"])
        self.assertEqual(bad["verdict"], "keep_separate")
        self.assertEqual(bad["primary"], "b")
        self.assertNotIn("groups", bad)
        with self.assertRaises(ValueError):
            merge_review._validate_result({"verdict": "merge", "primary": "zz"}, ["a"])
        with self.assertRaises(ValueError):
            merge_review._validate_result({"verdict": "nope"}, ["a"])
        self.assertEqual(merge_review._validate_result(
            {"verdict": "keep_separate"}, [])["primary"], "")

    def test_extract_verdict_json_edge_shapes(self):
        self.assertIsNone(merge_review._extract_verdict_json("   "))
        self.assertIsNone(merge_review._extract_verdict_json("{ \"a\": 1 }"))
        self.assertEqual(merge_review._extract_verdict_json(
            'x {"verdict": "a", "s": "}"} y {"verdict": "b"}')["verdict"], "b")
        self.assertEqual(merge_review._extract_verdict_json(
            '{"a": {"verdict": "inner"}}')["verdict"], "inner")
        self.assertEqual(merge_review._extract_verdict_json(
            '{"q": "\\"{"} {"verdict": "esc"}')["verdict"], "esc")
        self.assertIsNone(merge_review._extract_verdict_json('{"verdict": "x" {'))


class AnalyzeSuggestionEdgeTestCase(unittest.TestCase):
    def setUp(self):
        d = Path(tempfile.mkdtemp(prefix="mr-jobs-"))
        patcher = mock.patch.object(merge_review, "MERGE_DIR", d)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_missing_job_raises(self):
        with self.assertRaises(FileNotFoundError):
            merge_review.analyze_suggestion("MS-missing", runner=lambda p: None)

    def test_too_few_ids_fails_job(self):
        job = merge_review.create_job(["only"])
        out = merge_review.analyze_suggestion(job["id"], runner=lambda p: None)
        self.assertEqual(out["status"], "failed")
        self.assertIn("need >=2", out["error"])

    def test_runner_failure_and_no_json(self):
        job = merge_review.create_job(["a", "b"])
        with mock.patch.object(merge_review, "build_analysis_prompt", return_value="P"):
            out = merge_review.analyze_suggestion(
                job["id"], runner=lambda p: subprocess.CompletedProcess(
                    [], 3, stdout="", stderr="  bad  "))
            self.assertIn("claude -p exited 3: bad", out["error"])
            out = merge_review.analyze_suggestion(
                job["id"], runner=lambda p: subprocess.CompletedProcess([], 0, stdout="no json"))
            self.assertIn("no JSON object", out["error"])

    def test_dismissed_during_analysis_stays_dismissed(self):
        job = merge_review.create_job(["a", "b"])
        verdict = json.dumps({"verdict": "keep_separate", "primary": "a",
                              "rationale": "r", "action_plan": [], "confidence": "low"})

        def runner(prompt):
            merge_review.dismiss_job(job["id"])
            return subprocess.CompletedProcess([], 0, stdout=verdict)

        with mock.patch.object(merge_review, "build_analysis_prompt", return_value="P"):
            out = merge_review.analyze_suggestion(job["id"], runner=runner)
        self.assertEqual(out["status"], "dismissed")

    def test_default_runner_goes_through_llm(self):
        with mock.patch.object(merge_review.llm, "run",
                               return_value=subprocess.CompletedProcess([], 0)) as run:
            merge_review._default_runner("hello")
        self.assertEqual(run.call_args.args, ("hello",))
        self.assertEqual(run.call_args.kwargs["timeout"], merge_review.CLAUDE_TIMEOUT)

    def test_registry_load_is_the_material_source(self):
        # build_analysis_prompt fences + scrubs the per-card material
        with mock.patch.object(merge_review, "_material_for",
                               side_effect=lambda i: f"M[{i}]"):
            prompt = merge_review.build_analysis_prompt({"ids": ["a", 2]})
        self.assertIn("CARDS: a, 2", prompt)
        self.assertIn("M[a]\n\nM[2]", prompt)


if __name__ == "__main__":
    unittest.main()
