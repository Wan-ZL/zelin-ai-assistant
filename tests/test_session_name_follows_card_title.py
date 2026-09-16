"""会话名跟着卡片的活标题走（CONTRACT §37.1 追记 / §60.4 / §4）。

一张卡改了名（用户钦定或 CARD TITLE 收割），下一次 dispatch / resume 传给
claude 的 `--name` 就该是新名字，看板行也该叫新名字——此前 `session_name`
用的是**冻结 title**，所以「看板一个名、`claude agents` 一个名」。

本文件只钉这一个行为的四个面：

  * `dispatch_prompt.session_name`（= `executor.session_name` 别名）走
    §37.1 那条链（display_title → sanitize_title(title) → 冻结 title），
    清洗/截断/空名回落编号照旧；
  * 两个 `--name` 落点（dispatch 的 runner、`_run_resume` 的 argv）拿到的
    是同一个新名字；
  * dashboard 会话行的 `name` = 卡此刻的显示名，而**冻结 title 改发自己的
    `title` 键**——`name` 不再捎带它，§37.2 的搜索词表不许因此掉一维；
  * `agent_name_stale`：roster 上那条会话的名字与「此刻该叫什么」不一致时
    才发键（CLI 改不了运行中会话的名字，只能等下一次 resume），且**只发给
    还能再 resume 的行**——已验收行不发，那上面「下次 resume 才跟上」是一句
    永不兑现的承诺。
"""
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME

from act import executor
from act.lib import config, dashboard, dispatch_prompt
from act.lib.registry import Requirement


def _req(**kw) -> Requirement:
    base = dict(id="R-500", title="原始的冻结标题")
    base.update(kw)
    return Requirement(**base)


class SessionNameSourceTestCase(unittest.TestCase):
    def test_executor_alias_is_the_lib_function(self):
        """§60.4 仍说「bg 会话名 = executor.session_name」——实现搬了家，名字还在。"""
        self.assertIs(executor.session_name, dispatch_prompt.session_name)

    def test_display_title_beats_the_frozen_title(self):
        self.assertEqual(executor.session_name(_req(display_title="整理保险合同")),
                         "R-500 · 整理保险合同")

    def test_user_pinned_title_is_the_name(self):
        req = _req(display_title="我起的名字", user_titled=True)
        self.assertEqual(executor.session_name(req), "R-500 · 我起的名字")

    def test_no_display_title_falls_back_to_the_sanitized_title(self):
        """§37.1 链的第二档：裸 URL 卡的会话名也不再是一串 URL。"""
        req = _req(title="https://www.youtube.com/watch?v=abc123")
        self.assertEqual(executor.session_name(req), "R-500 · youtube.com ▸ abc123")

    def test_overlong_title_takes_the_clause_clip_not_a_raw_48_cut(self):
        """>60 字的长文本先过 sanitize 的首句截断，再过 48 字硬帽。"""
        req = _req(title="整理并核对这份合同。" + "后面还有很多很多很多很多的补充说明" * 4)
        self.assertEqual(executor.session_name(req), "R-500 · 整理并核对这份合同…")

    def test_work_id_prefix_and_cleaning_still_apply(self):
        """编号前缀（§60）、路径分隔符/控制字符折叠、48 字硬帽、空名回落。"""
        req = _req(work_id="R-77", display_title="a/b\nc\\d")
        self.assertEqual(executor.session_name(req), "R-77 · a b c d")
        self.assertEqual(executor.session_name(_req(display_title="长" * 100)),
                         "R-500 · " + "长" * 48)
        self.assertEqual(executor.session_name(_req(title="")), "R-500")

    def test_dispatch_runner_is_bound_to_the_live_name(self):
        """--name 落点①：dispatch 的默认 runner（`_named_runner`）。"""
        req = _req(display_title="改过名的卡")
        runner = executor._named_runner(req, config.Config())
        self.assertEqual(runner.keywords["name"], "R-500 · 改过名的卡")

    def test_resume_argv_carries_the_live_name(self):
        """--name 落点②：`_run_resume`（resume / rework / brief / steer 共用）。"""
        req = _req(display_title="改过名的卡")
        runs = []

        def _run(argv, **kw):
            runs.append(list(argv))
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

        with mock.patch.object(executor.subprocess, "run", _run):
            executor._run_resume(config.Config(), req, "sid-1", Path(TMP_HOME))
        argv = runs[-1]
        self.assertEqual(argv[argv.index("--name") + 1], "R-500 · 改过名的卡")


class DashboardSessionNameTestCase(unittest.TestCase):
    def test_row_name_is_the_display_title(self):
        req = _req(display_title="改过名的卡")
        self.assertEqual(dashboard._session_name(req, {}), "改过名的卡")

    def test_roster_name_and_id_are_only_fallbacks(self):
        empty = Requirement(id="R-501", title="")
        self.assertEqual(dashboard._session_name(empty, {"name": "roster 名"}), "roster 名")
        self.assertEqual(dashboard._session_name(empty, {}), "R-501")

    def test_stale_flag_only_when_the_roster_name_lags(self):
        req = _req(display_title="改过名的卡")
        current = executor.session_name(req)
        self.assertFalse(dashboard._agent_name_stale(req, {"name": current}))
        self.assertTrue(dashboard._agent_name_stale(req, {"name": "R-500 · 原始的冻结标题"}))

    def test_no_session_no_flag(self):
        """会话不在册（或 roster 名为空）= 没有可过时的东西，整键不发。"""
        req = _req(display_title="改过名的卡")
        self.assertFalse(dashboard._agent_name_stale(req, {}))
        self.assertFalse(dashboard._agent_name_stale(req, {"name": ""}))


class SessionRowFrozenTitleTestCase(unittest.TestCase):
    """四个会话行仍把冻结 `title` 发上 wire（§2 追记 + §37.2 搜索词表）。

    `name` 从冻结 title 改成活标题之后，「用户最初那句原话」在 运行中 / 待验收 /
    已完成 三条 lane 上就没有别的载体了（`former_titles` 只记**上一个
    display_title**，首次改名时它是空的），搜不到 = 用户按自己记得的话搜不到
    自己的卡。所以这三条 lane 的四个行构造各自发一个 `title` 键。
    """

    FROZEN = "帮我把 401k rollover 的手续走完"

    def _req(self) -> Requirement:
        return Requirement(id="R-510", title=self.FROZEN, display_title="整理转存材料",
                           execution={"session_id": "sid-9"})

    def _sx(self, roster_name: str = "") -> dashboard._Session:
        req = self._req()
        a = {"name": roster_name} if roster_name else {}
        return dashboard._Session(
            name=dashboard._session_name(req, a), cwd="/tmp/wt", state="working",
            resume_sid="sid-9", short_id="sid-9", copy_cmd=None,
            agent_name=a.get("name"),
            agent_name_stale=dashboard._agent_name_stale(req, a), agent=a)

    def _rows(self) -> dict:
        req, sx, ex = self._req(), self._sx(), {}
        return {
            "running": dashboard._running_row(req, ex, sx),
            "from_review": dashboard._from_review_row(req, ex, sx),
            "review": dashboard._review_row(req, ex, sx, config.Config()),
            "delivered": dashboard._delivered_row(req, ex, sx),
        }

    def test_every_session_row_carries_the_frozen_title(self):
        for lane, row in self._rows().items():
            with self.subTest(lane=lane):
                self.assertEqual(row["title"], self.FROZEN)
                self.assertEqual(row["name"], "整理转存材料")

    def test_the_original_words_are_searchable_on_the_row(self):
        """§37.2 词表是逐字段搜的——原话必须在这一行的某个键里。"""
        for lane, row in self._rows().items():
            with self.subTest(lane=lane):
                self.assertTrue(any("401k rollover" in str(v) for v in row.values()))

    def test_stale_flag_skips_the_delivered_row(self):
        """已验收卡不会再 resume——那一行不发这个键（宪法第 3 条：不许空口承诺）。"""
        req, ex, sx = self._req(), {}, self._sx(roster_name="R-510 · 老名字")
        self.assertTrue(sx.agent_name_stale)
        self.assertNotIn("agent_name_stale", dashboard._delivered_row(req, ex, sx))
        for lane, row in (("running", dashboard._running_row(req, ex, sx)),
                          ("from_review", dashboard._from_review_row(req, ex, sx)),
                          ("review", dashboard._review_row(req, ex, sx, config.Config()))):
            with self.subTest(lane=lane):
                self.assertTrue(row["agent_name_stale"])


if __name__ == "__main__":
    unittest.main()
