"""ui_scout 的 issue 计划：先查已开的，命中就去补评论（CONTRACT §79.4 指纹 / §79.5 报告）。

issue #449 点名要的行为——「Check existing issues first and comment on a match instead of
filing a new one」。这一份钉三件事：指纹是唯一的去重键、gh 不可用时宁可多提醒一次也不静默、
本脚本自己永不开 issue 永不发评论（对外动作是 owner 的一次点击，§79.6）。
gh 一律走注入缝：套件里真 gh 是被 tests/__init__.py 的出网守卫禁掉的。
"""

import json
import os
import shlex
import sys
import unittest

from tests import TMP_HOME  # noqa: F401 - ensures the sandbox env is set first

_QA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "qa")
if _QA_DIR not in sys.path:
    sys.path.insert(0, _QA_DIR)

import ui_scout  # noqa: E402


class _Proc(object):
    def __init__(self, stdout="", returncode=0, stderr=""):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr


def _finding(fingerprint="abcd1234", severity="error", **over):
    finding = {
        "journey": "board_tour", "oracle": "lane_count", "severity": severity,
        "summary": "列头写着 4，数据里是 7 张", "detail": "GET /api/board 的 review[] 长度 7。",
        "step": 3, "url": "http://127.0.0.1:1/", "screenshot": "board_tour/step-03.png",
        "fingerprint": fingerprint,
    }
    finding.update(over)
    return finding


def _report(findings):
    return {"protocol": 1, "pilot": "scripted", "startedAt": "2026-09-28T10:00:00.000Z",
            "durationMs": 1, "journeys": [], "findings": findings, "counts": {}}


def _gh(rows, returncode=0):
    """注入缝：记下 argv，回一份 `gh issue list --json` 形状的 stdout。"""
    seen = []

    def runner(argv):
        seen.append(argv)
        return _Proc(json.dumps(rows), returncode)

    runner.seen = seen
    return runner


class BodyFingerprintTestCase(unittest.TestCase):
    """落款只认 `ui_scout-fingerprint: <8 位 hex>`——正文里随便一个十六进制词不算。"""

    def test_reads_the_marker(self):
        self.assertEqual(ui_scout.body_fingerprint("前言\nui_scout-fingerprint: 0a1b2c3d\n"), "0a1b2c3d")

    def test_no_marker_is_none(self):
        self.assertIsNone(ui_scout.body_fingerprint("一段正文里有 deadbeef 这个词"))

    def test_marker_with_garbage_is_none(self):
        self.assertIsNone(ui_scout.body_fingerprint("ui_scout-fingerprint: 不是十六进制"))
        self.assertIsNone(ui_scout.body_fingerprint("ui_scout-fingerprint:"))

    def test_none_body(self):
        self.assertIsNone(ui_scout.body_fingerprint(None))


class IssuePlanTestCase(unittest.TestCase):
    def test_new_finding_gets_a_fileable_title_and_body(self):
        plan = ui_scout.issue_plan(_report([_finding()]), "error", _gh([]))
        self.assertEqual(len(plan), 1)
        self.assertIsNone(plan[0]["existing"])
        self.assertIn("ui_scout:", plan[0]["title"])
        self.assertIn("board_tour", plan[0]["title"])
        # 正文带落款——下一次跑才认得出「这条已经有 issue 了」
        self.assertIn("%s abcd1234" % ui_scout.FINGERPRINT_MARKER, plan[0]["body"])
        # 复现坐标齐了：哪一趟、第几步、什么地址、哪张截图
        for needle in ("board_tour", "第 3 步", "lane_count", "step-03.png"):
            self.assertIn(needle, plan[0]["body"])

    def test_known_fingerprint_points_at_the_existing_issue(self):
        rows = [{"number": 451, "title": "旧的", "body": "ui_scout-fingerprint: abcd1234"}]
        plan = ui_scout.issue_plan(_report([_finding()]), "error", _gh(rows))
        self.assertEqual(plan[0]["existing"]["number"], 451)
        lines = ui_scout.plan_lines(plan)
        self.assertTrue(any("已有 #451" in line for line in lines))
        self.assertFalse(any("gh issue create" in line for line in lines))

    def test_a_different_fingerprint_is_still_new(self):
        rows = [{"number": 451, "title": "旧的", "body": "ui_scout-fingerprint: 99999999"}]
        plan = ui_scout.issue_plan(_report([_finding()]), "error", _gh(rows))
        self.assertIsNone(plan[0]["existing"])

    def test_threshold_decides_what_is_worth_an_issue(self):
        report = _report([_finding("aaaaaaaa", "error"), _finding("bbbbbbbb", "warn"),
                          _finding("cccccccc", "info")])
        self.assertEqual(len(ui_scout.issue_plan(report, "error", _gh([]))), 1)
        self.assertEqual(len(ui_scout.issue_plan(report, "warn", _gh([]))), 2)
        self.assertEqual(len(ui_scout.issue_plan(report, "info", _gh([]))), 3)
        self.assertEqual(ui_scout.issue_plan(report, "never", _gh([])), [])

    def test_plan_lines_say_something_when_there_is_nothing(self):
        self.assertEqual(ui_scout.plan_lines([]), ["ui_scout: 没有够格开 issue 的发现。"])

    def test_the_printed_command_is_actually_runnable(self):
        plan = ui_scout.issue_plan(_report([_finding()]), "error", _gh([]))
        command = ui_scout.create_command(plan[0])
        # 开头一条 gh 命令 + 引号版 heredoc（壳不展开正文里的 $ 与反引号），结尾把它收掉
        self.assertTrue(command[0].startswith("gh issue create --label ui_scout --title "))
        self.assertIn("<<'ZAI_UI_SCOUT_BODY'", command[0])
        self.assertEqual(command[-2], "ZAI_UI_SCOUT_BODY")
        self.assertEqual(command[-1], ')"')
        # 正文原样夹在中间，落款也在
        self.assertIn("%s abcd1234" % ui_scout.FINGERPRINT_MARKER, command)

    def _title_token(self, plan_entry):
        """把打印出来的那一行**当壳来切**，取出 --title 真正会收到的那个 argv。"""
        head = ui_scout.create_command(plan_entry)[0].split(" --body ", 1)[0]
        tokens = shlex.split(head)
        return tokens[tokens.index("--title") + 1]

    def test_the_title_is_shell_quoted_not_json_quoted(self):
        """标题是**页面文本**拼出来的，而这条命令是给人粘进终端的——命令替换必须失效。

        JSON 的引号不是壳的引号：`json.dumps` 只转义 `"` 和 `\\`，双引号里的反引号与
        `$(…)` 照样被壳展开。判据只有一个——按壳的规矩切出来的那个 argv 逐字等于标题。
        """
        nasty = '保存失败 `id` $(id) $HOME "引号" 与\'单引号\''
        plan = ui_scout.issue_plan(_report([_finding(summary=nasty)]), "error", _gh([]))
        self.assertEqual(self._title_token(plan[0]), plan[0]["title"])
        self.assertIn("$(id)", plan[0]["title"])  # 原文留在标题里，只是不再是活的语法

    def test_a_quote_in_a_finding_cannot_break_the_command(self):
        plan = ui_scout.issue_plan(_report([_finding(summary='卡片 "标题" 里有引号')]), "error", _gh([]))
        self.assertEqual(self._title_token(plan[0]), plan[0]["title"])

    def test_control_characters_never_reach_the_printed_command(self):
        """换行能把「一条」命令变成两条，其余控制字符能在终端里改写光标——进门就拍平。"""
        plan = ui_scout.issue_plan(
            _report([_finding(summary="第一行\n第二行\x07\x1b[2J")]), "error", _gh([]))
        title = plan[0]["title"]
        self.assertNotIn("\n", title)
        self.assertNotIn("\x07", title)
        self.assertNotIn("\x1b", title)
        self.assertEqual(len(ui_scout.create_command(plan[0])[0].splitlines()), 1)

    def test_a_body_line_equal_to_the_delimiter_cannot_close_the_heredoc_early(self):
        """正文也是页面文本：一行正好等于定界符，heredoc 就提前收口，后面全被壳当命令读。"""
        plan = ui_scout.issue_plan(
            _report([_finding(detail="先一句\nZAI_UI_SCOUT_BODY\nrm -rf ~/Downloads")]),
            "error", _gh([]))
        command = ui_scout.create_command(plan[0])
        # 定界符只许出现在收口那一行（倒数第二行）；正文里那一行被顶开了一格
        self.assertEqual([i for i, line in enumerate(command) if line == ui_scout._HEREDOC],
                         [len(command) - 2])
        self.assertIn(" ZAI_UI_SCOUT_BODY", command)


class GhFailureTestCase(unittest.TestCase):
    """gh 坏了不许静默变成「都是新的」以外的任何东西，也不许把整条路径带崩。"""

    def test_non_zero_returncode_means_no_known_fingerprints(self):
        def runner(_argv):
            return _Proc("", 1, "gh: not logged in")
        self.assertEqual(ui_scout.open_issue_fingerprints(runner), {})

    def test_garbage_stdout(self):
        def runner(_argv):
            return _Proc("<html>login</html>", 0)
        self.assertEqual(ui_scout.open_issue_fingerprints(runner), {})

    def test_runner_that_raises_is_caught(self):
        def runner(_argv):
            raise OSError("gh 不在 PATH 上")
        self.assertEqual(ui_scout.open_issue_fingerprints(runner), {})

    def test_rows_that_are_not_dicts_are_skipped(self):
        self.assertEqual(ui_scout.open_issue_fingerprints(_gh(["歪的", None])), {})

    def test_query_asks_only_for_open_issues_carrying_the_marker(self):
        runner = _gh([])
        ui_scout.open_issue_fingerprints(runner)
        argv = runner.seen[0]
        self.assertEqual(argv[:4], ["gh", "issue", "list", "--state"])
        self.assertIn("open", argv)
        self.assertIn(ui_scout.FINGERPRINT_MARKER, argv)


class NoOutboundActionTestCase(unittest.TestCase):
    """§79.6：ui_scout 永不自己开 issue、永不自己发评论——只读一次，其余都是打印。"""

    def test_plan_only_ever_reads(self):
        runner = _gh([])
        ui_scout.plan_lines(ui_scout.issue_plan(_report([_finding()]), "error", runner))
        self.assertEqual(len(runner.seen), 1)
        self.assertEqual(runner.seen[0][1:3], ["issue", "list"])

    def test_module_never_spells_a_write_subcommand(self):
        with open(os.path.join(_QA_DIR, "ui_scout.py"), encoding="utf-8") as handle:
            source = handle.read()
        # `gh issue create` 只许出现在**打印给人看的**那一行里，不许出现在任何 argv 列表里
        self.assertNotIn('"create"', source)
        self.assertNotIn('"comment"', source)


if __name__ == "__main__":
    unittest.main()
