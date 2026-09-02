"""vnext2-plan §8 进度日志 fragment 判例（CONTRACT §56.7）：scripts/ci/progress_log.py。

  - parse_fragment：文件名 `<YYYY-MM-DD>-<kebab-slug>.md`（日期进 日期 列）；头部 = 首个空行前的
    `pr:` / `phase:` / `law:`（顺序不限、缺一即问题、未知键即问题）；正文非空；多段正文压成一格、`|` 转义；
  - load_rows：按日期排序、README.md 跳过、坏文件点名；
  - historical_rows：只取 `## 8.` 到下一个 `## ` 之间以 `|` 开头的行（含表头）；
  - render_table：历史行 + fragment 行；plan 没有 §8 表 → 默认表头；
  - CLI：check 坏 → ::error:: + rc 1；render 打完整表到 stdout，坏 fragment 只 warning。
"""
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr

_CI_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "ci")
if _CI_DIR not in sys.path:
    sys.path.insert(0, _CI_DIR)
import progress_log as pl  # noqa: E402

GOOD = "pr: `ci/x`（PR #1）\nphase: P2\nlaw: §56.7（新增）\n\n做了什么，第一段。\n\n第二段 with a | pipe.\n"
PLAN = ("# plan\n\n## 7. refs\n\n| not | this |\n\n## 8. 进度日志\n\nintro line\n\n"
        "| 日期 | PR / 分支 | 阶段 | 做了什么 | 法典 |\n|---|---|---|---|---|\n"
        "| 2026-09-01 | `a` | P0 | did a | §1 |\n\n## 9. next\n\n| nor | this |\n")


class ParseTestCase(unittest.TestCase):
    def test_good_fragment_row(self):
        row, problems = pl.parse_fragment("2026-09-02-ci-x.md", GOOD)
        self.assertEqual(problems, [])
        self.assertEqual(row, {"date": "2026-09-02", "pr": "`ci/x`（PR #1）", "phase": "P2", "law": "§56.7（新增）",
                               "body": "做了什么，第一段。 第二段 with a \\| pipe."})

    def test_header_keys_any_order_and_each_missing_key_reported(self):
        row, problems = pl.parse_fragment("2026-09-02-x.md", "law: —\npr: p\nphase: ph\n\nbody\n")
        self.assertEqual((problems, row["law"]), ([], "—"))
        _, problems = pl.parse_fragment("2026-09-02-x.md", "pr: p\n\nbody\n")
        self.assertEqual(sorted(problems), ["missing `law:` in the header", "missing `phase:` in the header"])

    def test_unknown_header_key_and_missing_blank_line(self):
        _, problems = pl.parse_fragment("2026-09-02-x.md", "pr: p\nphase: ph\nlaw: l\nowner: me\n\nbody\n")
        self.assertEqual(len(problems), 1)
        self.assertIn("header line 'owner: me'", problems[0])
        # no blank line: the body is read as header lines and rejected as such
        _, problems = pl.parse_fragment("2026-09-02-x.md", "pr: p\nphase: ph\nlaw: l\nbody here\n")
        self.assertTrue(any("header line" in p for p in problems), problems)
        self.assertTrue(any("empty body" in p for p in problems), problems)

    def test_empty_body_is_a_problem(self):
        _, problems = pl.parse_fragment("2026-09-02-x.md", "pr: p\nphase: ph\nlaw: l\n\n   \n")
        self.assertEqual(problems, ["empty body — say what the PR did (the 做了什么 column)"])

    def test_file_name_pattern(self):
        for bad in ("ci-x.md", "2026-9-2-x.md", "2026-09-02-X.md", "2026-09-02-x.txt", "2026-09-02.md"):
            _, problems = pl.parse_fragment(bad, GOOD)
            self.assertTrue(any("file name" in p for p in problems), bad)
        _, problems = pl.parse_fragment("2026-09-02-a-b-3.md", GOOD)
        self.assertEqual(problems, [])


class DirectoryTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pl-")

    def write(self, name, text):
        with open(os.path.join(self.tmp, name), "w", encoding="utf-8") as fh:
            fh.write(text)

    def test_load_sorted_by_date_skips_readme_reports_bad(self):
        self.write("2026-09-03-late.md", GOOD)
        self.write("2026-09-01-early.md", GOOD)
        self.write("README.md", "# shape\n")
        self.write("2026-09-02-bad.md", "pr: only\n\nbody\n")
        rows, problems = pl.load_rows(self.tmp)
        self.assertEqual([r["date"] for r in rows], ["2026-09-01", "2026-09-03"])
        self.assertEqual(len(problems), 2)
        self.assertTrue(all(p.startswith("2026-09-02-bad.md: ") for p in problems), problems)

    def test_missing_dir(self):
        self.assertEqual(pl.load_rows(os.path.join(self.tmp, "nope")), ([], []))


class RenderTestCase(unittest.TestCase):
    def test_historical_rows_bounded_to_section_8(self):
        self.assertEqual(pl.historical_rows(PLAN), ["| 日期 | PR / 分支 | 阶段 | 做了什么 | 法典 |",
                                                    "|---|---|---|---|---|", "| 2026-09-01 | `a` | P0 | did a | §1 |"])
        self.assertEqual(pl.historical_rows("# no section 8\n| x |\n"), [])

    def test_render_table_appends_fragment_rows(self):
        row = pl.parse_fragment("2026-09-02-ci-x.md", GOOD)[0]
        table = pl.render_table(PLAN, [row])
        self.assertEqual(table.splitlines()[-2:], [
            "| 2026-09-01 | `a` | P0 | did a | §1 |",
            "| 2026-09-02 | `ci/x`（PR #1） | P2 | 做了什么，第一段。 第二段 with a \\| pipe. | §56.7（新增） |"])

    def test_render_table_without_plan_uses_default_header(self):
        table = pl.render_table("", [])
        self.assertEqual(table, "\n".join(pl.DEFAULT_HEADER))


class CliTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pl-cli-")
        self.plan = os.path.join(self.tmp, "plan.md")
        with open(self.plan, "w", encoding="utf-8") as fh:
            fh.write(PLAN)
        self.dir = os.path.join(self.tmp, "progress")
        os.mkdir(self.dir)

    def write(self, name, text):
        with open(os.path.join(self.dir, name), "w", encoding="utf-8") as fh:
            fh.write(text)

    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stderr(err):
            rc = pl.main(argv, stdout=out)
        return rc, out.getvalue(), err.getvalue()

    def test_check_ok_then_fail(self):
        self.write("2026-09-02-ok.md", GOOD)
        rc, out, _ = self.run_main(["check", self.dir])
        self.assertEqual((rc, out), (0, "progress fragments: ok (1)\n"))
        self.write("2026-09-02-bad.md", "nope\n")
        rc, out, _ = self.run_main(["check", self.dir])
        self.assertEqual(rc, 1)
        self.assertIn("::error::docs/design/progress/2026-09-02-bad.md: ", out)

    def test_render_full_table_and_warn_on_bad(self):
        self.write("2026-09-02-ok.md", GOOD)
        self.write("2026-09-02-bad.md", "nope\n")
        rc, out, err = self.run_main(["render", self.dir, "--plan", self.plan])
        self.assertEqual(rc, 0)
        lines = out.splitlines()
        self.assertEqual(lines[0], "| 日期 | PR / 分支 | 阶段 | 做了什么 | 法典 |")
        self.assertEqual(len(lines), 4)
        self.assertTrue(lines[-1].startswith("| 2026-09-02 | `ci/x`"))
        self.assertIn("::warning::docs/design/progress/2026-09-02-bad.md", err)


if __name__ == "__main__":
    unittest.main()
