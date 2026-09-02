"""scripts/qa/mutation_issue.py 的判例（CONTRACT §57）—— pinned issue 的
幂等 create-or-update：注入假 gh runner，零网络零 subprocess；dry-run 一次
gh 都不许碰。与 insights.yml 的 shell 版同一语义（精确标题、open+closed 全集、
绝不开第二张、pin 尽力而为）。
"""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

_SPEC = importlib.util.spec_from_file_location(
    "qa_mutation_issue",
    Path(__file__).resolve().parent.parent / "scripts" / "qa" / "mutation_issue.py",
)
mutation_issue = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mutation_issue)

TITLE = mutation_issue.DEFAULT_TITLE


class _FakeGh:
    """gh argv → (rc, stdout)；记录全部调用。issues = list 命令返回的行。"""

    def __init__(self, issues=(), fail=()):
        self.issues = list(issues)
        self.fail = set(fail)  # 这些子命令返回 rc=1
        self.calls = []

    def __call__(self, args):
        self.calls.append(args)
        key = " ".join(args[:2])
        if key in self.fail:
            return 1, ""
        if args[:2] == ["issue", "list"]:
            return 0, json.dumps(self.issues)
        if args[:2] == ["issue", "create"]:
            return 0, "https://github.com/o/r/issues/42\n"
        if args[:2] in (["issue", "reopen"], ["issue", "edit"]):
            return 0, ""
        if args[0] == "api" and args[1].startswith("repos/"):
            return 0, json.dumps({"node_id": "NODE"})
        if args[:2] == ["api", "graphql"]:
            return 0, "{}"
        raise AssertionError(f"unexpected gh call: {args}")


def _body_file(tmp):
    path = Path(tmp) / "report.md"
    path.write_text("# Nightly mutation report\nbody\n", encoding="utf-8")
    return str(path)


def _run(gh, tmp, extra=()):
    return mutation_issue.main(
        ["--body-file", _body_file(tmp), "--repo", "o/r", *extra],
        runner=gh, log=lambda *_: None)


class CreateOrUpdateTestCase(unittest.TestCase):
    def test_creates_when_absent_then_pins(self):
        gh = _FakeGh(issues=[{"number": 7, "title": "别的 issue", "state": "OPEN"}])
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(_run(gh, tmp), 0)
        kinds = [" ".join(c[:2]) for c in gh.calls]
        self.assertEqual(kinds, ["issue list", "issue create",
                                 "api repos/o/r/issues/42", "api graphql"])

    def test_updates_existing_open_issue_never_creates_a_second(self):
        gh = _FakeGh(issues=[{"number": 9, "title": TITLE, "state": "OPEN"}])
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(_run(gh, tmp), 0)
        kinds = [" ".join(c[:2]) for c in gh.calls]
        self.assertNotIn("issue create", kinds)
        self.assertNotIn("issue reopen", kinds)
        edit = [c for c in gh.calls if c[:2] == ["issue", "edit"]][0]
        self.assertEqual(edit[2], "9")

    def test_reopens_closed_issue_before_edit(self):
        gh = _FakeGh(issues=[{"number": 9, "title": TITLE, "state": "CLOSED"}])
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(_run(gh, tmp), 0)
        kinds = [" ".join(c[:2]) for c in gh.calls]
        self.assertEqual(kinds.index("issue reopen"), kinds.index("issue edit") - 1)
        self.assertNotIn("issue create", kinds)

    def test_listing_is_scoped_by_title_search(self):
        # 不带 --search 时 gh 按创建时间取前 100 张：仓库累计 100+ 张更新的
        # issue 后报告隐身 → 第二张被铸出来（v0.48.13 审查 finding 1）。
        # in:title 收窄候选集让 limit 永远够用；精确匹配仍在 find_issue。
        gh = _FakeGh(issues=[{"number": 9, "title": TITLE, "state": "OPEN"}])
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(_run(gh, tmp), 0)
        listing = [c for c in gh.calls if c[:2] == ["issue", "list"]][0]
        self.assertIn("--search", listing)
        self.assertIn(f'in:title "{TITLE}"', listing)
        self.assertIn("all", listing)  # open+closed 全集不因 search 而丢

    def test_title_match_is_exact(self):
        gh = _FakeGh(issues=[
            {"number": 3, "title": TITLE + " (archive)", "state": "OPEN"}])
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(_run(gh, tmp), 0)
        self.assertIn("issue create", [" ".join(c[:2]) for c in gh.calls])

    def test_pin_failure_is_tolerated(self):
        gh = _FakeGh(issues=[{"number": 9, "title": TITLE, "state": "OPEN"}],
                     fail={"api graphql"})
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(_run(gh, tmp), 0)  # pin 红不许拖垮整轮

    def test_list_failure_is_an_error(self):
        gh = _FakeGh(fail={"issue list"})
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(_run(gh, tmp), 1)

    def test_edit_failure_is_an_error(self):
        gh = _FakeGh(issues=[{"number": 9, "title": TITLE, "state": "OPEN"}],
                     fail={"issue edit"})
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(_run(gh, tmp), 1)


class DryRunTestCase(unittest.TestCase):
    def test_dry_run_never_touches_gh(self):
        gh = _FakeGh()
        lines = []
        with tempfile.TemporaryDirectory() as tmp:
            rc = mutation_issue.main(
                ["--body-file", _body_file(tmp), "--repo", "o/r", "--dry-run"],
                runner=gh, log=lines.append)
        self.assertEqual(rc, 0)
        self.assertEqual(gh.calls, [])
        self.assertTrue(any("dry-run" in line for line in lines))

    def test_missing_body_file_is_a_quiet_no_op(self):
        gh = _FakeGh()
        rc = mutation_issue.main(
            ["--body-file", "/nonexistent/report.md", "--repo", "o/r"],
            runner=gh, log=lambda *_: None)
        self.assertEqual(rc, 0)
        self.assertEqual(gh.calls, [])


if __name__ == "__main__":
    unittest.main()
