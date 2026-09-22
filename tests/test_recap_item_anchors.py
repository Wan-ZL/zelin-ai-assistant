"""§63.13（issue #440 第 1 件，源自 #332）：可发送长版逐条带自己的语气与一条转写锚。

#332 的三方核对只查出一类错：想要被写成了规则、提议被写成了决定——格式在**制造确定性**，
而一节一个语气分不出节内那一句试探性的话。§63.7 登记这件事没做的理由是「锚要把时间戳
写进正文，而 §63.3 的禁项明令禁时间戳」。本节的解法是**锚不进正文**：条目自此是对象
``{text, modality, at, quote}``，语气与锚住在与 ``items`` 逐位对齐的 add-only ``modalities`` /
``anchors`` 列上，正文（``items`` / ``copy_*``）里照旧一个时间戳、一句原话都没有。

本文件钉的是整条路：模板（转写逐行带 ``[HH:MM]`` 戳、条目四个字段）、解析（对象与老的
字符串都认；字符串 = 没有语气没有锚的一条）、校验（`item_unanchored` 形状闸、`anchor_unverified`
对照转写、`item_modality` 闭表与两语言一致；**没声明那一列的节不判**——手拼的节、§63.13 之前
入库的记录都没有它）、渲染（条目自己的语气与本节不同时才带 `` (floated)`` / ``（有人提过）``；
锚永不渲染）、发现行不带原话（宪法第 9 条）、以及 `fill_record` / 回退 / 投影一路上锚都在。
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
from act.lib import recap_sessions as rs
from act.lib import recap_store as store
from act.lib import recap_text as rt

KEY = fx.KEY
MIN = fx.MIN
STAMP = fx.stamp_at(0)
META = {"when": "w", "app": "zoom", "duration_min": 20}


def section(items, key="decided", modality="decided", lang_items=None) -> dict:
    return {"key": key, "modality": modality, "items": list(items)}


def reply(en_sections, zh_sections=None) -> str:
    doc = {"en": en_sections, "zh": zh_sections if zh_sections is not None else en_sections}
    return "```json\n" + json.dumps(doc, ensure_ascii=False) + "\n```"


def parsed(en_sections, zh_sections=None) -> dict:
    return rt.parse_sections(reply(en_sections, zh_sections))


def context(*extra_stamps) -> dict:
    """fixture 转写的对照：每一行都是 SENTENCE，戳 = 20 分钟里的每一分钟。"""
    stamps = [fx.stamp_at(m) for m in range(20)] + list(extra_stamps)
    return rt.anchor_context("\n".join([fx.SENTENCE.strip()] * 20), stamps)


class TemplateTestCase(unittest.TestCase):
    def test_the_sections_template_asks_for_four_fields_and_keeps_its_old_promises(self):
        header = rt.PROMPT_HEADER_SECTIONS
        for phrase in ('"text"', '"modality"', '"at"', '"quote"', "without at and quote",
                       "verbatim in the transcript", "Omit a section entirely", "Section rules"):
            self.assertIn(phrase, header)
        # 锚的两个字段明说不进正文：§63.3 的禁项对 text 照旧成立
        self.assertIn("never in text", header)
        # 五行模板一个字符没动：它不认识条目对象
        self.assertNotIn('"quote"', rt.PROMPT_HEADER)


class ParseTestCase(unittest.TestCase):
    def test_object_items_parse_into_aligned_columns(self):
        doc = parsed([section([{"text": "[D1] Ann owns the data mix", "modality": "decided",
                                "at": "12:57", "quote": "  Ann  will own the data mix "},
                               {"text": "Bo may take the handover", "modality": "FLOATED"},
                               "a plain old string item"])])
        sec = doc["en"][0]
        self.assertEqual(sec["items"], ["Ann owns the data mix", "Bo may take the handover",
                                        "a plain old string item"])
        self.assertEqual(sec["tags"], ["D1", "", ""])
        self.assertEqual(sec["modalities"], ["decided", "floated", ""])
        # 锚：两个字符串都在才算；空白归一；缺一个 = None；字符串条目 = None
        self.assertEqual(sec["anchors"], [{"at": "12:57", "quote": "Ann will own the data mix"}, None, None])
        self.assertTrue(rt.sections_wellformed(doc["en"]))

    def test_a_structurally_broken_item_fails_the_whole_parse(self):
        for items in ([{"modality": "decided", "at": "12:57", "quote": "x"}],   # no text
                      [{"text": 7}], [7], [None], [["nested"]]):
            self.assertIsNone(parsed([section(items)]), items)
        # §63.13 之前的形（纯字符串）仍解析得出——它只是一条没有语气、没有锚的条目
        self.assertIsNotNone(parsed([section(["plain"])]))


class ValidatorTestCase(unittest.TestCase):
    """校验：形状闸、转写对照、闭表、两语言一致、以及「没声明的列不判」。"""

    def codes(self, doc, ctx=None) -> list:
        return [f["code"] for f in rt.validate_sections_detail(doc, ctx)]

    def test_the_fixture_reply_is_clean_with_and_without_the_transcript(self):
        doc = rt.parse_sections(fx.good_sections_output())
        self.assertEqual(rt.validate_sections(doc), [])
        self.assertEqual(rt.validate_sections(doc, context()), [])
        self.assertEqual(rt.validate_for(rt.SHAPE_SECTIONS, doc, context()), [])

    def test_an_item_without_an_anchor_is_rejected_with_its_continuous_number(self):
        doc = parsed([section([fx.item("a"), "no anchor at all"]),
                      section([{"text": "no quote", "at": STAMP}], key="open", modality="open")])
        found = rt.validate_sections_detail(doc)
        codes = [(f["code"], f["lang"], f["line"]) for f in found]
        self.assertEqual(codes, [("item_unanchored", "en", 2), ("item_unanchored", "en", 3)])
        self.assertIn("has no transcript anchor", found[0]["text"])
        # 重试喂回模型的 text 列与结构化列同源
        self.assertEqual(rt.validate_sections(doc), [f["text"] for f in found])

    def test_anchor_shape_gates(self):
        bad_at = parsed([section([{"text": "x", "at": "12:5", "quote": fx.QUOTE}])])
        self.assertEqual(self.codes(bad_at), ["item_unanchored"])
        self.assertIn("HH:MM", rt.validate_sections(bad_at)[0])
        short = parsed([section([{"text": "x", "at": STAMP, "quote": "run"}])])
        self.assertIn("too short", rt.validate_sections(short)[0])
        long = parsed([section([{"text": "x", "at": STAMP, "quote": "w" * (rt.MAX_QUOTE_CHARS + 1)}])])
        self.assertIn("too long", rt.validate_sections(long)[0])
        self.assertEqual((rt.MIN_QUOTE_CHARS, rt.MAX_QUOTE_CHARS), (6, 160))

    def test_the_transcript_check_wants_a_real_stamp_and_verbatim_words(self):
        doc = parsed([section([fx.item("a"), {"text": "b", "at": "03:03", "quote": fx.QUOTE},
                               {"text": "c", "at": STAMP, "quote": "the run will move to Monday"}])])
        # 没有对照：形状都对 = 干净（回退的重算走这条路）
        self.assertEqual(self.codes(doc), [])
        found = rt.validate_sections_detail(doc, context())
        self.assertEqual([(f["code"], f["line"]) for f in found],
                         [("anchor_unverified", 2), ("anchor_unverified", 3)])
        self.assertIn("not a stamp", found[0]["text"])
        self.assertIn("verbatim", found[1]["text"])
        # 「逐字」容忍标点与大小写，不容忍改写
        tolerant = parsed([section([{"text": "a", "at": STAMP,
                                     "quote": "TRAINING run, moves to the new data-mix."}])])
        self.assertEqual(self.codes(tolerant, context()), [])

    def test_sections_that_never_declared_the_columns_are_not_judged_on_them(self):
        # 判例手拼的节 / §63.13 之前入库的记录 / 手改过的文件：没有 anchors / modalities 列
        legacy = [{"key": "decided", "modality": "decided", "items": ["a", "b"]}]
        self.assertEqual(rt.validate_sections_detail({"en": legacy, "zh": legacy}, context()), [])
        junk = [{"key": "decided", "modality": "decided", "items": ["a"], "anchors": "nope",
                 "modalities": 7}]
        self.assertEqual(rt.validate_sections_detail({"en": junk, "zh": junk}), [])

    def test_only_the_english_side_is_judged_on_anchors(self):
        zh = [section([{"text": "甲"}])]                     # 锚缺席（zh 侧不判——锚是转写的事实，与语言无关）
        doc = parsed([section([fx.item("a")])], zh)
        self.assertEqual(self.codes(doc, context()), [])
        # zh 侧带着的锚原样存着（add-only 列），不判
        self.assertEqual(doc["zh"][0]["anchors"], [None])

    def test_item_modality_is_a_closed_table_and_agrees_across_languages(self):
        doc = parsed([section([fx.item("a", "floated"), fx.item("b", "certain"), fx.item("c")])],
                     [section([fx.item("甲", "floated"), fx.item("乙", "certain"), fx.item("丙", "open")])])
        found = rt.validate_sections_detail(doc)
        # en 第 2 条不在闭表；zh 第 2 条同样不在；第 3 条 en 没声明 = 沿用本节，不算不一致
        self.assertEqual([(f["code"], f["lang"], f["line"]) for f in found],
                         [("item_modality", "en", 2), ("item_modality", "zh", 2)])
        mismatch = parsed([section([fx.item("a", "floated")])], [section([fx.item("甲", "proposed")])])
        found = rt.validate_sections_detail(mismatch)
        self.assertEqual([(f["code"], f["lang"], f["line"]) for f in found], [("item_modality", "zh", 1)])
        self.assertIn("differs from the English item", found[0]["text"])
        # 节都对不上时只说 section_mismatch，不再逐条比语气（那是同节同序之上的判决）
        skew = parsed([section([fx.item("a", "floated")])],
                      [section([fx.item("甲", "proposed")], key="open", modality="open")])
        self.assertEqual(self.codes(skew), ["section_mismatch"])

    def test_findings_never_carry_the_quote_or_the_modality_word(self):
        doc = parsed([section([{"text": "secret commitment", "modality": "hushhush", "at": STAMP,
                                "quote": "secret verbatim words from the transcript"}])])
        blob = json.dumps(rt.validate_sections_detail(doc, context()), ensure_ascii=False)
        for banned in ("secret", "hushhush", "verbatim words"):
            self.assertNotIn(banned, blob)

    def test_the_five_line_gate_ignores_the_context(self):
        doc = rt.parse_output(fx.good_output())
        self.assertEqual(rt.validate_for(rt.SHAPE_LINES, doc, context()), [])
        self.assertEqual(rt.validate_detail_for(rt.SHAPE_LINES, doc, context()), [])


class RenderTestCase(unittest.TestCase):
    """条目自己的语气与本节不同时才上纸；锚永不上纸。"""

    def test_the_item_modality_suffix_appears_only_when_it_differs(self):
        secs = [{"key": "split", "modality": "decided", "tags": ["S1", "S2", "S3"],
                 "items": ["Ann ships the eval", "Bo may take the handover", "Cy owns the docs"],
                 "modalities": ["decided", "floated", "klingon"],
                 "anchors": [fx.anchor(), fx.anchor(1), fx.anchor(2)]}]
        # 节标题照 §63.10：语气与节名不同名就带后缀（`Split (decided)`）；条目只在与**本节的语气**
        # 不同时才带自己的尾巴
        self.assertEqual(rt.render_sections(secs, "en").split("\n"), [
            "Split (decided):", "S1. Ann ships the eval", "S2. Bo may take the handover (floated)",
            "S3. Cy owns the docs"])                       # 闭表外的声明不上纸
        zh = [dict(secs[0], items=["评测归 Ann", "交接也许归 Bo", "文档归 Cy"])]
        self.assertIn("S2. 交接也许归 Bo（有人提过）", rt.render_sections(zh, "zh"))
        # 节的语气与条目同名 = 沿用，不重复（`Split (floated)` 下面的 floated 条目不再带尾巴）
        floated = [dict(secs[0], modality="floated")]
        body = rt.render_sections(floated, "en")
        self.assertIn("Split (floated):", body)
        self.assertIn("S2. Bo may take the handover\n", body)
        self.assertNotIn("(floated)\n", body.split("\n", 1)[1])

    def test_anchors_never_reach_the_pasted_body_and_filler_takes_its_modality_along(self):
        secs = [{"key": "decided", "modality": "decided", "tags": ["D1", "D2"],
                 "items": ["a real item", "none"], "modalities": ["floated", "open"],
                 "anchors": [fx.anchor(), fx.anchor(1)]}]
        body = rt.render_sections(secs, "en")
        self.assertEqual(body, "Decided:\nD1. a real item (floated)")
        self.assertNotIn(STAMP, body)
        self.assertNotIn(fx.QUOTE, body)
        # 一条都不剩的回落（整份照原样出）同样带语气尾巴
        whole = [{"key": "open", "modality": "open", "items": ["none"], "modalities": ["floated"]}]
        self.assertEqual(rt.render_sections(whole, "en"), "Open:\n1. none (floated)")
        # 手改坏的列：不是表 / 越界 = 没有尾巴，不崩
        self.assertEqual(rt.render_sections([{"key": "open", "modality": "open", "items": ["x"],
                                              "modalities": "floated"}], "en"), "Open:\n1. x")


class StampTestCase(unittest.TestCase):
    """转写逐行带戳（模板要模型引用它），对照据同一份戳算。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-recap-stamp-")
        self.addCleanup(self.tmp.cleanup)
        self.conn = fx.make_db(Path(self.tmp.name) / "db.sqlite")
        self.addCleanup(self.conn.close)

    def test_rows_are_time_ordered_bounded_and_stamped_in_the_key_timezone(self):
        fx.add_audio(self.conn, fx.T0 + 600, 1, text="second")
        fx.add_audio(self.conn, fx.T0, 1, text="first", rows_per_minute=1)
        fx.add_audio(self.conn, fx.T0 + 7200, 1, text="later")
        rows = rs.transcript_rows_between(self.conn, fx.T0, fx.T0 + 1200)
        self.assertEqual([text for _ts, text in rows], ["first", "second", "second"])
        self.assertEqual([ts for ts, _t in rows], [fx.T0, fx.T0 + 600, fx.T0 + 630])
        self.assertEqual(rs.transcript_between(self.conn, fx.T0, fx.T0 + 1200), "first\nsecond\nsecond")
        stamped = rs.stamped_transcript(rows, fx.TZ)
        self.assertEqual(stamped.split("\n"), ["[%s] first" % fx.stamp_at(0), "[%s] second" % fx.stamp_at(10),
                                               "[%s] second" % fx.stamp_at(10)])
        self.assertEqual(rs.stamp(fx.T0, fx.TZ), rs.local_dt(fx.T0, fx.TZ).strftime("%H:%M"))
        if fx.HAS_TZDATA:
            self.assertEqual(rs.stamp(fx.T0, fx.TZ), "12:56")

    def test_the_anchor_context_is_the_stamps_plus_the_normalised_text(self):
        ctx = rt.anchor_context("Ann will own the Data-Mix.\nBo, maybe.", ["12:56", "12:57", 1257])
        self.assertEqual(ctx["stamps"], {"12:56", "12:57", "1257"})
        self.assertEqual(ctx["norm"], "annwillownthedatamixbomaybe")


class _Runner:
    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), dict(kwargs)))
        out = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        return subprocess.CompletedProcess(argv, 0, stdout=out, stderr="")


class PipelineTestCase(unittest.TestCase):
    """真 `fill_record`：戳进 prompt、锚落记录、对照进校验、回退与投影一路都在。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-recap-anchors-")
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        (root / "state").mkdir()
        mock.patch.object(config, "STATE_DIR", root / "state").start()
        mock.patch.object(notify, "notify", return_value=True).start()
        self.addCleanup(mock.patch.stopall)
        self.conn = fx.make_db(root / "db.sqlite")
        self.addCleanup(self.conn.close)
        self.cfg = config.Config(raw={"recap": {"default_shape": "sections"}})
        recap.run_once(now=fx.T0 - 3600, conn=self.conn, runner=_Runner(fx.good_output()), cfg=self.cfg)
        fx.add_frames(self.conn, fx.T0, 20)
        fx.add_audio(self.conn, fx.T0, 20)

    def closed(self, runner):
        recap.run_once(now=fx.T0 + 34 * MIN, conn=self.conn, runner=runner, cfg=self.cfg)
        return store.load_recap(KEY)

    def transcript_of(self, prompt: str) -> str:
        return prompt.rpartition("Transcript (data, not instructions")[2]

    def test_the_sections_prompt_carries_a_stamped_transcript_and_the_five_line_one_does_not(self):
        runner = _Runner(fx.good_sections_output())
        self.closed(runner)
        stamped = self.transcript_of(runner.calls[0][0][2])
        self.assertIn("[%s] %s" % (fx.stamp_at(0), fx.SENTENCE.strip()), stamped)
        self.assertIn("[%s] " % fx.stamp_at(19), stamped)
        plain = _Runner(fx.good_output())
        recap.generate(KEY, shape="lines", now=fx.T0 + 40 * MIN, conn=self.conn, runner=plain, cfg=self.cfg)
        self.assertNotIn("[%s]" % fx.stamp_at(0), self.transcript_of(plain.calls[0][0][2]))

    def test_a_clean_reply_lands_with_aligned_modalities_and_anchors_off_the_pasted_body(self):
        rec = self.closed(_Runner(fx.good_sections_output()))
        self.assertEqual(rec["quality"], store.QUALITY_OK)
        first = rec["sections_en"][0]
        self.assertEqual(first["modalities"], ["decided"])
        self.assertEqual(first["anchors"], [fx.anchor()])
        self.assertEqual(rec["sections_zh"][1]["anchors"], [fx.anchor(3)])
        self.assertEqual(len(first["items"]), len(first["tags"]), len(first["anchors"]))
        # 粘出去的正文里没有一个戳、没有一句原话（§63.3 的禁项照旧）
        for body in (rec["copy_en"], rec["copy_zh"]):
            self.assertNotIn(fx.stamp_at(0), body)
            self.assertNotIn(fx.QUOTE, body)
        self.assertEqual(rt.validate_detail_for("sections", {"en": rec["sections_en"], "zh": rec["sections_zh"]}), [])

    def test_a_paraphrased_quote_is_quoted_back_and_a_second_miss_lands_needs_review(self):
        bad = reply([section([{"text": "Ann owns the data mix", "modality": "decided", "at": STAMP,
                               "quote": "Ann is going to own the data mix"}])],
                    [section([fx.item("数据配比归 Ann")])])
        runner = _Runner(bad, fx.good_sections_output())
        rec = self.closed(runner)
        self.assertEqual(len(runner.calls), 2)
        self.assertIn("en item 1 anchor quote does not appear verbatim", runner.calls[1][0][2])
        self.assertEqual(rec["quality"], store.QUALITY_OK)          # 重试那份干净
        recap.generate(KEY, now=fx.T0 + 40 * MIN, conn=self.conn, runner=_Runner(bad), cfg=self.cfg)
        stubborn = store.load_recap(KEY)
        # 两次都对不上：需复核 + 结构化原因，正文仍可复制、原话不进台账
        self.assertEqual(stubborn["quality"], store.QUALITY_NEEDS_REVIEW)
        self.assertEqual([f["code"] for f in stubborn["problems"]], ["anchor_unverified"])
        self.assertTrue(stubborn["copy_en"])
        self.assertNotIn("going to own", json.dumps(stubborn["problems"]))

    def test_legacy_string_items_land_needs_review_as_unanchored(self):
        legacy = reply([section(["Ann owns the data mix"])], [section(["数据配比归 Ann"])])
        rec = self.closed(_Runner(legacy))
        self.assertEqual(rec["quality"], store.QUALITY_NEEDS_REVIEW)
        self.assertEqual([f["code"] for f in rec["problems"]], ["item_unanchored"])
        self.assertEqual(rec["sections_en"][0]["anchors"], [None])
        self.assertIn("D1. Ann owns the data mix", rec["copy_en"])

    def test_an_item_voice_that_differs_from_its_section_reaches_the_pasted_body(self):
        out = reply([section([fx.item("Ann ships the eval", "decided"),
                              fx.item("Bo may take the handover", "floated", minute=2)],
                             key="split", modality="decided")],
                    [section([fx.item("评测归 Ann", "decided"), fx.item("交接也许归 Bo", "floated", minute=2)],
                             key="split", modality="decided")])
        rec = self.closed(_Runner(out))
        self.assertEqual(rec["quality"], store.QUALITY_OK)
        self.assertEqual(rec["copy_en"],
                         "Split (decided):\nS1. Ann ships the eval\nS2. Bo may take the handover (floated)")
        self.assertEqual(rec["copy_zh"], "分工（已定）：\nS1. 评测归 Ann\nS2. 交接也许归 Bo（有人提过）")

    def test_history_revert_and_the_projection_all_keep_the_anchors(self):
        self.closed(_Runner(fx.good_sections_output()))
        recap.generate(KEY, now=fx.T0 + 40 * MIN, conn=self.conn,
                       runner=_Runner(reply([section([fx.item("Something else", minute=5)])],
                                            [section([fx.item("另一件事", minute=5)])])), cfg=self.cfg)
        rec = store.load_recap(KEY)
        self.assertEqual(rec["history"][0]["sections_en"][0]["anchors"], [fx.anchor()])
        self.assertEqual(rec["sections_en"][0]["anchors"], [fx.anchor(5)])
        back = recap.revert(KEY, 1, now=fx.T0 + 50 * MIN)
        self.assertEqual(back["sections_en"][0]["anchors"], [fx.anchor()])
        self.assertEqual(back["sections_en"][0]["modalities"], ["decided"])
        row = {r["key"]: r for r in store.all_rows()}[KEY]
        self.assertEqual(row["sections_en"][0]["anchors"], [fx.anchor()])
        blob = json.dumps(row, ensure_ascii=False)
        for banned in ('"recipient"', '"channel"', '"tier"'):
            self.assertNotIn(banned, blob)

    def test_a_reverted_needs_review_version_recomputes_without_the_transcript(self):
        legacy = reply([section(["Ann owns the data mix"])], [section(["数据配比归 Ann"])])
        self.closed(_Runner(legacy))                                   # v1 needs_review（无锚）
        recap.generate(KEY, now=fx.T0 + 40 * MIN, conn=self.conn,
                       runner=_Runner(fx.good_sections_output()), cfg=self.cfg)
        back = recap.revert(KEY, 1, now=fx.T0 + 50 * MIN)
        self.assertEqual(back["quality"], store.QUALITY_NEEDS_REVIEW)
        # 形状上的缺锚重算得出来；对照转写那一半没有转写可对，不编一条 anchor_unverified
        self.assertEqual([f["code"] for f in back["problems"]], ["item_unanchored"])


if __name__ == "__main__":
    unittest.main()
