"""§63.10（issue #303）可发送长版：一份纪要能出成分节稿，而且下游没有一处把它当「没出稿」。

五行契约装不下人真正发出去的那份文档（owner 实测：5 节 14 条 / 3 节 20 条，
issue #303 / #332），所以出稿多了第二种形状 `sections`：分节 + 每节一个语气
（decided / proposed / floated / open）+ 跨节连续编号。本文件钉的是这一形状的
**整条路**——模板与解析、确定性校验（节表 / 语气表 / 上限 / 自编号 / 两语言对齐）、
渲染（标题 + 连续编号 + 略掉空节）、记录字段（add-only shape / sections_* / copy_*）、
形状怎么选出来（按钮 > 记录上一版 > 配置）、以及**每一处「有正文吗」的判决**：
history 入库、通知、Slack 草稿正文、下一场会的「较上次变化」锚、投影句柄与回退。
无发送路径不因新形状松一寸（argv 仍是 NO_EGRESS_ARGV，记录仍无 recipient/channel）。
"""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first
from tests import recap_fixture as fx

from act import recap
from act.lib import config, notify
from act.lib import recap_slack_draft as slack_draft
from act.lib import recap_store as store
from act.lib import recap_text as rt

KEY = fx.KEY
MIN = 60.0


def sections(*keys) -> list:
    return [{"key": key, "modality": key if key in rt.MODALITIES else "decided",
             "items": ["item for %s" % key]} for key in keys]


class TemplateTestCase(unittest.TestCase):
    """模板与解析：模型回的是那个 JSON 对象，形状之外的一律 None。"""

    def test_prompt_header_is_the_sections_one_and_states_the_caps(self):
        prompt = rt.build_prompt("transcript", {"when": "w", "app": "zoom", "duration_min": 20},
                                 [], shape=rt.SHAPE_SECTIONS)
        self.assertIn("Section rules", prompt)
        self.assertNotIn('{"en": [5 strings], "zh": [5 strings]}', prompt)
        for number in (rt.MAX_SECTIONS, rt.MAX_ITEMS, rt.MAX_ITEM_CHARS_EN, rt.MAX_ITEM_CHARS_ZH):
            self.assertIn(str(number), rt.PROMPT_HEADER_SECTIONS)
        # 空的那一节由模型整节略掉——模板必须明说，否则又会回到 filler 行
        self.assertIn("Omit a section entirely", rt.PROMPT_HEADER_SECTIONS)
        # 转写仍在围栏里（宪法第 5 条），形状不改这一条
        self.assertIn("UNTRUSTED SOURCE MATERIAL", prompt)
        # 形状认不出 = 五行形（配置被手改过也不会没稿）
        self.assertIn('{"en": [5 strings], "zh": [5 strings]}',
                      rt.build_prompt("t", {"when": "w"}, [], shape="garbage"))

    def test_parse_sections_tolerates_the_fence_and_rejects_other_shapes(self):
        parsed = rt.parse_sections("Sure:\n" + fx.good_sections_output() + "\nDone.")
        self.assertEqual([sec["key"] for sec in parsed["en"]], ["decided", "proposed"])
        self.assertEqual(parsed["zh"][1]["items"], ["结项标准：评测超过当前基线"])
        for bad in ("", "no json", '{"en": []}', '{"en": [{"key": "decided"}], "zh": []}',
                    '{"en": [{"key": 1, "modality": "decided", "items": []}], "zh": []}',
                    '{"en": [{"key": "decided", "modality": "decided", "items": [7]}], "zh": []}',
                    json.dumps({"en": ["Decided: x"], "zh": ["定了：x"]})):
            self.assertIsNone(rt.parse_sections(bad), bad)
        # 两种形状各认各的解析器
        self.assertIsNone(rt.parse_for(rt.SHAPE_SECTIONS, fx.good_output()))
        self.assertIsNone(rt.parse_for(rt.SHAPE_LINES, fx.good_sections_output()))


class ValidatorTestCase(unittest.TestCase):
    """确定性校验：节表 / 语气表 / 两个上限 / 自编号 / 两语言对齐 + 老禁项照旧。"""

    def clean(self) -> dict:
        return rt.parse_sections(fx.good_sections_output())

    def test_a_clean_sections_recap_has_no_findings(self):
        self.assertEqual(rt.validate_sections(self.clean()), [])
        self.assertEqual(rt.validate_for(rt.SHAPE_SECTIONS, self.clean()), [])

    def test_unknown_key_duplicate_and_wrong_order_are_findings(self):
        codes = lambda doc: [f["code"] for f in rt.validate_sections_detail(doc)]  # noqa: E731
        self.assertIn("section_key", codes({"en": sections("agenda"), "zh": sections("agenda")}))
        self.assertIn("section_key", codes({"en": sections("open", "decided"),
                                            "zh": sections("open", "decided")}))
        dup = sections("decided", "decided")
        self.assertIn("section_key", codes({"en": dup, "zh": dup}))
        self.assertIn("section_modality", codes({"en": [{"key": "decided", "modality": "certain",
                                                         "items": ["x"]}],
                                                 "zh": [{"key": "decided", "modality": "certain",
                                                         "items": ["甲"]}]}))

    def test_the_caps_come_from_the_measured_samples(self):
        # issue #332：发出去的最大一份是 3 节 20 条（32 条草稿被本人删到 20）；
        # issue #303 的那一份是 5 节 14 条——所以 6 节 / 24 条容得下两份实测样本
        self.assertEqual((rt.MAX_SECTIONS, rt.MAX_ITEMS), (6, 24))
        self.assertEqual((rt.MAX_ITEM_CHARS_EN, rt.MAX_ITEM_CHARS_ZH), (240, 100))
        twenty = [{"key": "decided", "modality": "decided", "items": ["x %d" % i for i in range(20)]}]
        self.assertEqual(rt.validate_sections({"en": twenty, "zh": twenty}), [])
        too_many = [{"key": "decided", "modality": "decided",
                     "items": ["x %d" % i for i in range(rt.MAX_ITEMS + 1)]}]
        found = rt.validate_sections_detail({"en": too_many, "zh": too_many})
        self.assertEqual([f["code"] for f in found], ["item_count", "item_count"])
        self.assertEqual((found[0]["limit"], found[0]["over"]), (rt.MAX_ITEMS, 1))
        seven = sections(*(rt.SECTION_KEYS + ("decided",)))
        self.assertIn("section_count", [f["code"] for f in rt.validate_sections_detail(
            {"en": seven, "zh": seven})])

    def test_long_items_self_numbering_and_the_old_bans_are_findings(self):
        doc = {"en": [{"key": "decided", "modality": "decided",
                       "items": ["x" * (rt.MAX_ITEM_CHARS_EN + 9), "1. already numbered",
                                 "Ann said the run slips"]}],
               "zh": [{"key": "decided", "modality": "decided", "items": ["甲"]}]}
        found = {f["code"]: f for f in rt.validate_sections_detail(doc)}
        self.assertEqual((found["item_too_long"]["line"], found["item_too_long"]["over"]), (1, 9))
        self.assertEqual(found["item_too_long"]["limit"], rt.MAX_ITEM_CHARS_EN)
        self.assertEqual(found["item_numbered"]["line"], 2)
        self.assertEqual(found["reported_speech"]["line"], 3)
        banned = {"en": [{"key": "decided", "modality": "decided",
                          "items": ["at 12:30 see https://x.test @ann `code` 🙂"]}],
                  "zh": [{"key": "decided", "modality": "decided", "items": ["甲"]}]}
        codes = {f["code"] for f in rt.validate_sections_detail(banned)}
        self.assertTrue({"timestamp", "link", "mention", "markup", "emoji"} <= codes)

    def test_the_two_languages_must_carry_the_same_sections(self):
        found = rt.validate_sections_detail({"en": sections("decided", "open"),
                                             "zh": sections("decided")})
        self.assertIn("section_mismatch", [f["code"] for f in found])
        empty = [{"key": "decided", "modality": "decided", "items": []}]
        self.assertIn("section_empty", [f["code"] for f in rt.validate_sections_detail(
            {"en": empty, "zh": empty})])
        self.assertTrue(rt.validate_sections({"en": [], "zh": []}))

    def test_findings_never_carry_the_recap_text(self):
        doc = {"en": [{"key": "decided", "modality": "decided",
                       "items": ["secret commitment " + "x" * rt.MAX_ITEM_CHARS_EN]}],
               "zh": [{"key": "decided", "modality": "decided", "items": ["甲"]}]}
        blob = json.dumps(rt.validate_sections_detail(doc), ensure_ascii=False)
        self.assertNotIn("secret commitment", blob)


class RenderTestCase(unittest.TestCase):
    """渲染：节标题 + 语气后缀 + **跨节连续编号**（issue #332 实测的粘贴形）。"""

    def test_titles_numbering_and_the_modality_suffix(self):
        doc = rt.parse_sections(fx.good_sections_output())
        body = rt.render_sections(doc["en"], "en")
        self.assertEqual(body.split("\n"), [
            "Decided:",
            "1. Ann owns the data mix from Monday",
            "",
            "Proposed:",
            "2. Exit criteria: the eval clears the current baseline"])
        zh = rt.render_sections(doc["zh"], "zh")
        self.assertIn("定了：", zh)
        self.assertIn("2. 结项标准：评测超过当前基线", zh)
        # 语气与节名不同名时才出现后缀（`Decided (decided)` 是废话）
        floated = [{"key": "split", "modality": "floated", "items": ["Bo may take the handover"]}]
        self.assertIn("Split (floated):", rt.render_sections(floated, "en"))
        self.assertIn("分工（有人提过）：", rt.render_sections(
            [{"key": "split", "modality": "floated", "items": ["交接也许归 Bo"]}], "zh"))

    def test_rendering_survives_a_hand_mangled_file(self):
        self.assertEqual(rt.render_sections([], "en"), "")
        self.assertEqual(rt.render_sections(None, "en"), "")
        self.assertEqual(rt.render_sections([7, {"key": "x", "modality": "", "items": ["a"]}], "en"),
                         "x:\n1. a")
        self.assertEqual(rt.render_for(rt.SHAPE_SECTIONS, None), "")


class PriorLinesTestCase(unittest.TestCase):
    """下一场会的「较上次变化」锚：两种形状都给得出英文行，坏记录给空表。"""

    def test_lines_sections_and_a_record_whose_copy_key_is_missing(self):
        five = ["Decided: x", "Split: y", "Deadline: z", "Changed since last plan: w", "Open: v"]
        self.assertEqual(store.prior_lines({"en": five}), five)
        secs = [{"key": "decided", "modality": "decided", "items": ["a", "b"]}]
        self.assertEqual(store.prior_lines({"en": None, "sections_en": secs, "copy_en": "1. a"}),
                         ["1. a"])
        # copy_* 缺席（本键之前入库的记录 / 手改过的文件）→ 现渲染一遍，空行不进锚
        self.assertEqual(store.prior_lines({"en": None, "sections_en": secs}),
                         ["Decided:", "1. a", "2. b"])
        self.assertEqual(store.prior_lines({"en": None, "copy_en": "   "}), [])
        self.assertEqual(store.prior_lines({}), [])


class RecordTestCase(unittest.TestCase):
    """记录与下游：add-only 字段、形状怎么选、每一处「有正文吗」。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-recap-shapes-")
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        (root / "state").mkdir()
        mock.patch.object(config, "STATE_DIR", root / "state").start()
        self.notified = []
        mock.patch.object(notify, "notify",
                          side_effect=lambda *a, **k: self.notified.append(a) or True).start()
        self.addCleanup(mock.patch.stopall)
        self.conn = fx.make_db(root / "db.sqlite")
        self.addCleanup(self.conn.close)
        self.cfg = config.Config(raw={"recap": {"default_shape": "sections"}})
        self.calls = []
        recap.run_once(now=fx.T0 - 3600, conn=self.conn, runner=self._runner, cfg=self.cfg)
        fx.add_frames(self.conn, fx.T0, 20)
        fx.add_audio(self.conn, fx.T0, 20)

    def _runner(self, argv, **kwargs):
        self.calls.append((list(argv), dict(kwargs)))
        return subprocess.CompletedProcess(argv, 0, stdout=fx.good_sections_output(), stderr="")

    def closed_round(self, now=None):
        return recap.run_once(now=fx.T0 + 34 * MIN if now is None else now,
                              conn=self.conn, runner=self._runner, cfg=self.cfg)

    def test_config_default_shape_produces_a_sections_recap(self):
        self.closed_round()
        rec = store.load_recap(KEY)
        self.assertEqual(rec["shape"], "sections")
        self.assertEqual(rec["quality"], store.QUALITY_OK)
        self.assertIsNone(rec["en"])
        self.assertIsNone(rec["zh"])
        self.assertEqual([sec["key"] for sec in rec["sections_en"]], ["decided", "proposed"])
        # copy_* = 粘出去的那一份（渲染只在 recap_text 一处，页面照着显示）
        self.assertEqual(rec["copy_en"], rt.render_sections(rec["sections_en"], "en"))
        self.assertEqual(rec["copy_zh"], rt.render_sections(rec["sections_zh"], "zh"))
        self.assertIn("1. Ann owns the data mix from Monday", rec["copy_en"])
        # 通知与 badge 不许把它说成没出稿
        self.assertTrue(self.notified)
        self.assertTrue(store.has_text(rec))
        # 无发送路径一寸没松
        blob = json.dumps(rec, ensure_ascii=False)
        for banned in ('"recipient"', '"channel"', '"tier"', '"status": "approved"'):
            self.assertNotIn(banned, blob)
        self.assertEqual(self.calls[0][0][3:], ["--output-format", "text",
                                                "--fallback-model", config.DEFAULT_MODEL_FALLBACK,
                                                *rt.NO_EGRESS_ARGV])

    def test_the_shape_is_sticky_and_the_button_overrides_it(self):
        self.cfg.raw["recap"] = {}                       # 出厂默认 = 五行
        recap.run_once(now=fx.T0 + 34 * MIN, conn=self.conn, cfg=self.cfg,
                       runner=lambda argv, **kw: subprocess.CompletedProcess(
                           argv, 0, stdout=fx.good_output(), stderr=""))
        self.assertEqual(store.load_recap(KEY)["shape"], "lines")
        # 按钮把它改成可发送长版
        recap.generate(KEY, shape="sections", now=fx.T0 + 40 * MIN, conn=self.conn,
                       runner=self._runner, cfg=self.cfg)
        rec = store.load_recap(KEY)
        self.assertEqual((rec["shape"], rec["version"]), ("sections", 2))
        # 之后不带形状的重新生成沿用它（晚到切片不该把选好的形状打回去）
        recap.generate(KEY, now=fx.T0 + 50 * MIN, conn=self.conn, runner=self._runner, cfg=self.cfg)
        self.assertEqual(store.load_recap(KEY)["shape"], "sections")
        # 认不出的形状 = 记录上一次的（永不因为一个坏字符串没稿）
        recap.generate(KEY, shape="garbage", now=fx.T0 + 60 * MIN, conn=self.conn,
                       runner=self._runner, cfg=self.cfg)
        self.assertEqual(store.load_recap(KEY)["shape"], "sections")

    def test_history_the_slack_draft_body_and_the_priors_all_see_the_text(self):
        self.closed_round()
        first = store.load_recap(KEY)["copy_en"]
        recap.generate(KEY, now=fx.T0 + 40 * MIN, conn=self.conn, runner=self._runner, cfg=self.cfg)
        rec = store.load_recap(KEY)
        # 1) 上一版进了 history（旧代码在 `if not rec["en"]` 上早退 = 整段历史丢掉）
        self.assertEqual(len(rec["history"]), 1)
        entry = rec["history"][0]
        self.assertEqual((entry["version"], entry["shape"]), (1, "sections"))
        self.assertEqual(entry["copy_en"], first)
        self.assertEqual([h["version"] for h in store.history_versions(rec)], [1])
        # 2) Slack 草稿正文 = 粘出去的那一份，不是空
        st = dict(store.settings(self.cfg), slack_draft_enabled=True)
        with mock.patch.object(slack_draft, "build_prompt",
                               side_effect=lambda channel, body: self.calls.append(("draft", body)) or "P"):
            with mock.patch.object(slack_draft, "parse_result",
                                   return_value={"status": "posted", "channel_link": None}):
                receipt = recap.post_slack_draft(rec, "C0123456789", st, self._runner, self.cfg,
                                                 fx.T0 + 60 * MIN)
        self.assertEqual(receipt["status"], "posted")
        self.assertIn(("draft", rec["copy_zh"]), self.calls)
        # 3) 下一场会的「较上次变化」拿得到它（旧 priors_for 只看 en = 静默消失）
        priors = store.priors_for(fx.T0 + 86400, "America/Los_Angeles")
        self.assertEqual(len(priors), 1)
        self.assertIn("1. Ann owns the data mix from Monday", "\n".join(priors[0]["en"]))

    def test_a_stored_sections_version_can_be_reverted_to(self):
        self.closed_round()
        v1 = store.load_recap(KEY)["copy_en"]
        # 第 2 版改回五行形，再回退到第 1 版：正文、形状、copy_* 一起回来
        recap.generate(KEY, shape="lines", now=fx.T0 + 40 * MIN, conn=self.conn, cfg=self.cfg,
                       runner=lambda argv, **kw: subprocess.CompletedProcess(
                           argv, 0, stdout=fx.good_output(), stderr=""))
        self.assertEqual(store.load_recap(KEY)["shape"], "lines")
        rec = recap.revert(KEY, 1, now=fx.T0 + 50 * MIN)
        self.assertEqual((rec["version"], rec["shape"], rec["reverted_from"]), (3, "sections", 1))
        self.assertEqual(rec["copy_en"], v1)
        self.assertIsNone(rec["en"])
        self.assertEqual([sec["key"] for sec in rec["sections_en"]], ["decided", "proposed"])

    def test_a_failing_sections_generation_lands_needs_review_with_its_findings(self):
        bad = json.dumps({"en": [{"key": "decided", "modality": "certain", "items": ["x"]}],
                          "zh": [{"key": "decided", "modality": "certain", "items": ["甲"]}]})
        self.closed_round()
        recap.generate(KEY, now=fx.T0 + 40 * MIN, conn=self.conn, cfg=self.cfg,
                       runner=lambda argv, **kw: subprocess.CompletedProcess(argv, 0, stdout=bad, stderr=""))
        rec = store.load_recap(KEY)
        self.assertEqual(rec["quality"], store.QUALITY_NEEDS_REVIEW)
        self.assertEqual({f["code"] for f in rec["problems"]}, {"section_modality"})
        self.assertEqual(rec["repairs"], [])              # 长度修剪是五行形自己的动作
        self.assertTrue(rec["copy_en"])                   # 仍可复制（判决是「读一遍再粘」）

    def test_giving_up_after_n_failures_keeps_the_shape(self):
        """连炸 N 轮 = 放弃，但那一版仍带着**解析出来的形状**（粘性的下半边）。

        放弃那一处不传 shape 就回落到参数默认的五行形：#332 的 4,493 词转写连着
        超时三轮之后，一份配置成可发送长版的纪要会被永久打回五行——`record_shape`
        的第二级读的正是记录上这个键，此后每一次重新生成（包括从看板按下的那一次）
        都出五行，而没有任何一句话说得出为什么。"""
        def boom(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="timed out")
        for i in range(recap.MAX_GENERATION_FAILURES):
            recap.run_once(now=fx.T0 + (34 + i) * MIN, conn=self.conn, runner=boom, cfg=self.cfg)
        rec = store.load_recap(KEY)
        self.assertEqual((rec["quality"], rec["shape"]), (store.QUALITY_FAILED, "sections"))
        self.assertIsNone(rec["copy_en"])                 # 放弃 = 没有正文，两种形状都一样
        self.assertFalse(store.has_text(rec))
        # 之后不带形状的重新生成仍然出可发送长版（配置说的是它，失败没有资格改这件事）
        self.assertEqual(recap.record_shape(rec, store.settings(self.cfg)), "sections")
        recap.generate(KEY, now=fx.T0 + 60 * MIN, conn=self.conn, runner=self._runner, cfg=self.cfg)
        self.assertEqual(store.load_recap(KEY)["shape"], "sections")

    def test_the_inbox_form_only_accepts_the_two_literals(self):
        base = {"action": "recap_generate", "meeting_key": KEY}
        self.assertEqual(store.inbox_argv(dict(base, shape="sections")),
                         ["--generate", KEY, "--shape", "sections"])
        self.assertEqual(store.inbox_argv(dict(base, shape="lines", partial=True)),
                         ["--generate", KEY, "--partial", "--shape", "lines"])
        self.assertEqual(store.inbox_argv(base), ["--generate", KEY])
        for bad in ("Sections", "", True, 1, ["sections"]):
            self.assertIsNone(store.inbox_argv(dict(base, shape=bad)), bad)


if __name__ == "__main__":
    unittest.main()
