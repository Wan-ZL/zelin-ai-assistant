"""§63.10（issue #303）空的部分不再占着纸面：两种形状粘出去时都略掉自己的填充值。

owner 两次要求「没内容的那行删掉」（#303；#332 的第二份样本里「较上次变化」又一次
是填充值）。本文件钉的是这条略行规则的**判据与边界**：

- 判据是**查表**，不是对模型散文的正则——模板逐字规定了填充串
  （`FILLER_BY_LABEL` 按标签逐位、`FILLER_ITEMS` 逐条），标签之后整段等于它才算空；
- 存储不变：`en` / `zh` 仍恰是五行，`validate()` 仍要求五行——略行只发生在
  **渲染出去的那一份**（`copy_*` / Slack 草稿正文 / 页面 `<pre>`），与 §63.5 追记的
  表头同一层；
- 一条也不剩时整份照原样渲染：一份空白纪要比一行「无」糟糕得多。
"""
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first
from tests import recap_fixture as fx

from act import recap
from act.lib import config, notify
from act.lib import recap_store as store
from act.lib import recap_text as rt

KEY = fx.KEY
MIN = 60.0
FULL_EN = ["Decided: the run moves to the new mix", "Split: Ann owns the eval",
           "Deadline: none set", "Changed since last plan: none recorded", "Open: none"]
FULL_ZH = ["定了：训练改用新配比", "分工：评测归 Ann", "截止：未定", "较上次变化：无记录", "待定：无"]


def _never_called(argv, **kwargs):
    raise AssertionError("no model call may happen for a recap without a body: %r" % (argv,))


class LinesTestCase(unittest.TestCase):
    def test_the_template_fillers_are_dropped_in_both_languages(self):
        self.assertEqual(rt.render(FULL_EN).split("\n"), FULL_EN[:2])
        self.assertEqual(rt.render(FULL_ZH).split("\n"), FULL_ZH[:2])
        # 收尾标点与大小写不影响判定（「Deadline: None set.」也是空）
        self.assertEqual(rt.render(["Decided: x", "Split: y", "Deadline: None set.",
                                    "Changed since last plan: none recorded",
                                    "Open: none"]).split("\n"),
                         ["Decided: x", "Split: y"])

    def test_a_line_that_says_more_than_the_filler_stays(self):
        kept = list(FULL_EN)
        kept[2] = "Deadline: none set for the API work, Ann confirms on Friday"
        self.assertIn(kept[2], rt.render(kept))
        # 「未分配」不是填充值：分工未定是一条真信息，不许被略掉
        self.assertIn("Split: not assigned", rt.render(["Decided: x", "Split: not assigned",
                                                        "Deadline: none set",
                                                        "Changed since last plan: none recorded",
                                                        "Open: none"]))
        # 填充串出现在**别的**标签下不算空（位置是判据的一半）
        self.assertIn("Decided: none recorded", rt.render(["Decided: none recorded", "Split: y",
                                                           "Deadline: z",
                                                           "Changed since last plan: w",
                                                           "Open: v"]))
        self.assertFalse(rt.is_filler_line("Decided: nothing new", 4))
        self.assertFalse(rt.is_filler_line("Decided: nothing new", 99))

    def test_an_all_filler_recap_renders_whole(self):
        # 一场什么都没产出的会：四行是模板填充值，只有分工那行留下（它没有填充值）
        allfiller = ["Decided: nothing new", "Split: not assigned", "Deadline: none set",
                     "Changed since last plan: none recorded", "Open: none"]
        self.assertEqual(rt.render(allfiller).split("\n"), ["Split: not assigned"])
        # 一条都不剩时整份照原样出（手改坏的短记录是唯一到得了这里的路）——
        # 一份空白纪要比一行「无」糟糕得多
        self.assertEqual(rt.render(["Decided: none"]), "Decided: none")
        self.assertEqual(rt.render([]), "")
        self.assertEqual(rt.render(None), "")

    def test_validate_and_storage_still_demand_all_five_lines(self):
        parsed = rt.parse_output(fx.good_output())
        self.assertEqual(rt.validate(parsed), [])          # 填充行**通过**校验，一如既往
        self.assertEqual(len(parsed["en"]), rt.LINE_COUNT)
        # 略行只发生在渲染层：校验拿到的仍是五行
        self.assertEqual(rt.validate({"en": rt.render(FULL_EN).split("\n"),
                                      "zh": rt.render(FULL_ZH).split("\n")}),
                         ["exactly 5 lines per language"])


class SectionsTestCase(unittest.TestCase):
    def test_a_section_whose_items_are_all_filler_is_dropped_whole(self):
        secs = [{"key": "decided", "modality": "decided", "items": ["Ann owns the eval"]},
                {"key": "deadline", "modality": "decided", "items": ["none set"]},
                {"key": "open", "modality": "open", "items": ["无"]}]
        self.assertEqual(rt.render_sections(secs, "en").split("\n"),
                         ["Decided:", "1. Ann owns the eval"])
        # 编号在略掉之后仍然连续（粘出去的是 1..N，中间不许有洞）
        mixed = [{"key": "decided", "modality": "decided", "items": ["a", "none", "b"]},
                 {"key": "open", "modality": "open", "items": ["c"]}]
        self.assertEqual(rt.render_sections(mixed, "en").split("\n"),
                         ["Decided:", "1. a", "2. b", "", "Open:", "3. c"])
        self.assertTrue(rt.is_filler_item("None."))
        self.assertFalse(rt.is_filler_item("none of the vendors replied"))

    def test_an_all_filler_sections_recap_renders_whole_like_the_five_lines(self):
        """一条都不剩时长版也照原样出——`render` 的那条最后款对两种形状同时成立。

        不这样的话「空」在系统里有两个判据：`has_text` 说有正文、渲染说空串，
        于是通知说「已生成」、面板给一个空 `<pre>` 配一颗可用的复制键、
        Slack 草稿把空正文送进模型，而这三件事没有一件说得出为什么。"""
        secs = [{"key": "decided", "modality": "decided", "items": ["none"]},
                {"key": "open", "modality": "open", "items": ["无"]}]
        self.assertEqual(rt.render_sections(secs, "en").split("\n"),
                         ["Decided:", "1. none", "", "Open:", "2. 无"])
        # 模型整节回了空 items（validate 的 `section_empty`，重试再失败 = 需复核但仍可复制）
        self.assertEqual(rt.render_sections([{"key": "open", "modality": "open", "items": []}], "en"),
                         "Open:")
        # 真的没有节 / 不是节的东西：空串（`has_text` 据此说「这份没出稿」）
        self.assertEqual(rt.render_sections([], "en"), "")
        self.assertEqual(rt.render_sections([1, "x"], "en"), "")

    def test_an_all_filler_sections_recap_is_never_announced_as_text(self):
        """「有正文吗」只有一个判据：`has_text` 与渲染出来的那份必须同时成立。"""
        secs = [{"key": "open", "modality": "open", "items": ["无"]}]
        rec = {"shape": "sections", "en": None, "zh": None, "sections_en": secs, "sections_zh": secs}
        recap._write_copy_bodies(rec)
        self.assertEqual(rec["copy_en"], recap.copy_body(rec, "en"))
        self.assertTrue(rec["copy_en"])                    # 照原样出 = 仍是可复制的一份
        self.assertTrue(store.has_text(rec))
        # 渲染不出任何东西的那一种（手改坏的文件）：一处都不许说它有正文
        mangled = {"shape": "sections", "en": None, "zh": None, "sections_en": [1, 2],
                   "sections_zh": [1, 2]}
        recap._write_copy_bodies(mangled)
        self.assertIsNone(mangled["copy_en"])
        self.assertFalse(store.has_text(mangled))
        self.assertIsNone(store._version_handle(dict(mangled, version=1)))
        st = dict(store.settings(config.Config(raw={"recap": {}})), slack_draft_enabled=True)
        receipt = recap.post_slack_draft(mangled, "C0123456789", st, _never_called,
                                         config.Config(), 0.0)
        self.assertEqual(receipt["status"], "failed")      # 空草稿比一句诚实的 failed 差得多


class EndToEndTestCase(unittest.TestCase):
    """落到记录上：`copy_*` 是略过之后的那一份，`en` / `zh` 仍是完整五行。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-recap-filler-")
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        (root / "state").mkdir()
        mock.patch.object(config, "STATE_DIR", root / "state").start()
        mock.patch.object(notify, "notify", return_value=True).start()
        self.addCleanup(mock.patch.stopall)
        self.conn = fx.make_db(root / "db.sqlite")
        self.addCleanup(self.conn.close)
        self.cfg = config.Config(raw={"recap": {}})
        self.runner = lambda argv, **kw: subprocess.CompletedProcess(
            argv, 0, stdout=fx.good_output(), stderr="")
        recap.run_once(now=fx.T0 - 3600, conn=self.conn, runner=self.runner, cfg=self.cfg)
        fx.add_frames(self.conn, fx.T0, 20)
        fx.add_audio(self.conn, fx.T0, 20)

    def test_the_stored_lines_are_five_and_the_pasted_body_is_two(self):
        recap.run_once(now=fx.T0 + 34 * MIN, conn=self.conn, runner=self.runner, cfg=self.cfg)
        rec = store.load_recap(KEY)
        self.assertEqual(len(rec["en"]), rt.LINE_COUNT)
        self.assertEqual(len(rec["zh"]), rt.LINE_COUNT)
        # fixture 的那一份后三行都是模板填充值 → 粘出去只剩两行
        self.assertEqual(rec["copy_en"].split("\n"), rec["en"][:2])
        self.assertEqual(rec["copy_zh"].split("\n"), rec["zh"][:2])
        self.assertEqual(rec["shape"], "lines")


if __name__ == "__main__":
    unittest.main()
