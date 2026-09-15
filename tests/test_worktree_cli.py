"""§75.4：`python3 -m act.lib.worktrees` 的四形与两个默认 runner 的边界。

`--dry-run` 是这条法的诊断口：owner（或下一个 session）得能先看会删谁再决定要不要删，
所以它必须与 `--sweep` 走同一段判决、且一个字节都不动。默认的 `git` / `du` runner 是
两条与外界打交道的边：它们**永不抛**——起不来、超时、输出读不出数字，一律给「没答案」
（`(None, "")` / `None`），而不是让一次扫地把每日循环崩掉（宪法第 11 条 / 第 3 条）。

`subprocess.run` 经注入替换——本文件不起任何真子进程（仓规：unit 层禁真 subprocess）。
"""
import io
import json
import subprocess
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env before act imports

from act.lib import worktrees


class _Proc:
    def __init__(self, rc=0, stdout="", stderr=""):
        self.returncode = rc
        self.stdout = stdout
        self.stderr = stderr


class DefaultRunnersTestCase(unittest.TestCase):
    def test_git_returns_rc_and_stdout(self):
        with mock.patch.object(subprocess, "run", return_value=_Proc(0, "out\n")) as run:
            self.assertEqual(worktrees.default_git(["status", "--porcelain"], "/r"), (0, "out\n"))
        self.assertEqual(run.call_args[0][0], ["git", "status", "--porcelain"])

    def test_git_that_cannot_start_or_times_out_has_no_answer(self):
        for boom in (OSError("no git"), subprocess.TimeoutExpired("git", 1)):
            with mock.patch.object(subprocess, "run", side_effect=boom):
                self.assertEqual(worktrees.default_git(["status"], "/r"), (None, ""))

    def test_git_output_is_capped(self):
        with mock.patch.object(subprocess, "run", return_value=_Proc(0, "x" * 400_000)):
            _rc, out = worktrees.default_git(["log"], "/r")
        self.assertEqual(len(out), worktrees.OUTPUT_CAP)

    def test_du_reports_bytes_from_the_last_line(self):
        with mock.patch.object(subprocess, "run", return_value=_Proc(0, "12\t/a\n34\t/b\n")):
            self.assertEqual(worktrees.default_du("/b"), 34 * 1024)

    def test_du_that_is_missing_or_unreadable_reports_null_not_zero(self):
        with mock.patch.object(subprocess, "run", side_effect=OSError("no du")):
            self.assertIsNone(worktrees.default_du("/b"))
        with mock.patch.object(subprocess, "run", return_value=_Proc(0, "")):
            self.assertIsNone(worktrees.default_du("/b"))
        with mock.patch.object(subprocess, "run", return_value=_Proc(0, "nonsense\n")):
            self.assertIsNone(worktrees.default_du("/b"))


INVENTORY = {"ok": True, "worktrees": 2, "removable": 1, "bytes": 3_000_000_000,
             "repos": [{"repo": "/r", "managed": 2, "error": None,
                        "rows": [{"name": "a", "branch": "feat/a", "age_days": 30.0,
                                  "verdict": "remove", "reason": "stale"},
                                 {"name": "b", "branch": "", "age_days": 1.0,
                                  "verdict": "keep", "reason": "active"}]}]}


class CliTestCase(unittest.TestCase):
    def setUp(self):
        self.buf = io.StringIO()
        patched = mock.patch.object(worktrees.sys, "stdout", self.buf)
        patched.start()
        self.addCleanup(patched.stop)

    def _run(self, argv, **fakes):
        with mock.patch.multiple(worktrees, **fakes):
            rc = worktrees.main(argv)
        return rc, self.buf.getvalue()

    def test_the_bare_form_prints_a_human_summary_and_writes_nothing(self):
        inv = mock.Mock(return_value=INVENTORY)
        rc, out = self._run([], inventory=inv, sweep=mock.Mock())
        self.assertEqual(rc, 0)
        self.assertIn("worktrees: 2（可清理 1）", out)
        self.assertIn("3.0 GB", out)
        self.assertIn("detached", out)          # 没有分支名的那条如实说 detached

    def test_json_prints_the_inventory_verbatim(self):
        rc, out = self._run(["--json"], inventory=mock.Mock(return_value=INVENTORY),
                            sweep=mock.Mock())
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["worktrees"], 2)

    def test_dry_run_asks_the_sweep_for_a_dry_run(self):
        swept = mock.Mock(return_value={"ok": True, "removed": []})
        self._run(["--dry-run"], inventory=mock.Mock(), sweep=swept)
        self.assertTrue(swept.call_args[1]["dry_run"])

    def test_sweep_is_the_only_form_that_removes_anything(self):
        swept = mock.Mock(return_value={"ok": True, "removed": []})
        self._run(["--sweep", "--days", "30", "--limit", "3"], inventory=mock.Mock(), sweep=swept)
        self.assertFalse(swept.call_args[1]["dry_run"])
        self.assertEqual(swept.call_args[1]["days"], 30)
        self.assertEqual(swept.call_args[1]["limit"], 3)

    def test_no_bytes_skips_the_du_measurement(self):
        inv = mock.Mock(return_value=INVENTORY)
        self._run(["--no-bytes"], inventory=inv, sweep=mock.Mock())
        self.assertFalse(inv.call_args[1]["measure"])

    def test_an_unmeasured_size_and_a_broken_repo_are_said_out_loud(self):
        doc = {"worktrees": 0, "removable": 0, "bytes": None,
               "repos": [{"repo": "/r", "managed": 0, "error": "git worktree list failed",
                          "rows": []}]}
        rc, out = self._run(["--no-bytes"], inventory=mock.Mock(return_value=doc),
                            sweep=mock.Mock())
        self.assertEqual(rc, 0)
        self.assertIn("占用: 未知", out)
        self.assertIn("git worktree list failed", out)


if __name__ == "__main__":
    unittest.main()
