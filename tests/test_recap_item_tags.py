"""CONTRACT §63.12（issue #300 的后半）：可发送长版的每一条有一个跨版稳定的标签。

判例（本文件钉的行为）：

1. **标签的形与词表**：字母表 D/S/P/L/C/O 由 `SECTION_KEYS` 逐位派生（与 §63.9 的
   `LINE_TAGS` 同源），`TAG_RE` 只认闭表字母 + 1–2 位序号。
2. **解析收的是「声明」不是事实**：模型写的 `[D1] …` 前缀被剥掉（正文不含它），
   标签落在与 `items` 逐位对齐的 add-only `tags` 上；校验只看 `items`。
3. **派发在存储侧**：没报标签 = 发新号；报对了 = 留着；报了一个上一版没发过的 /
   另一节的 / 重复的 = 丢掉重派（LLM 输出不可信，宪法第 11 条）。
4. **永不复用**：删掉一条之后新条目拿的是下一个号，不是那个空出来的号；计数器
   （记录上的 `tag_seq`）单调，丢了也能从上一版正文里兜回来。
5. **确定性回挂**：模型把标签漏掉时按归一正文的相似度回挂（门 + 与第二名的差距），
   认不出就发新号——错挂一条引用比多一个新标签贵得多。
6. **渲染**：有标签的条目写 `D1. …`（§63.10 的连续编号只留给没有标签的条目）；
   出稿 / 回退时落在 `copy_*` 上，所见即所复制。
7. **prompt**：上一版的带标签条目进 **UNTRUSTED 围栏**，「同一条承诺写回同一个标签」
   这条指令住在模板里（宪法第 5 条）。
8. **§63.11 的答案认标签形**（`s2=drop`）：位置形一个字符没动，server 侧的形状正则
   与 golden 的字节形因此也没动。
9. **回退**（§63.9）：那一版的标签原样回来，计数器不回头。
"""
from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import tests.recap_fixture as fx
from act import recap
from act.lib import config, notify
from act.lib import recap_intent as intent
from act.lib import recap_store as store
from act.lib import recap_text as rt

KEY = fx.KEY
MIN = fx.MIN


def sections(items, key="decided", modality="decided", tags=None):
    sec = {"key": key, "modality": modality, "items": list(items)}
    if tags is not None:
        sec["tags"] = list(tags)
    return sec


def payload(en, zh=None):
    return {"en": list(en), "zh": list(zh if zh is not None else en)}


def model_reply(en_items, zh_items=None, key="decided", modality="decided") -> str:
    zh_items = en_items if zh_items is None else zh_items
    doc = {"en": [{"key": key, "modality": modality, "items": list(en_items)}],
           "zh": [{"key": key, "modality": modality, "items": list(zh_items)}]}
    return "```json\n" + json.dumps(doc, ensure_ascii=False) + "\n```"


class TagVocabularyTestCase(unittest.TestCase):
    """字母表与标签形（闭表逐位派生，不是手抄的第二份）。"""

    def test_letters_are_derived_from_the_section_keys(self):
        self.assertEqual(rt.TAG_LETTERS, tuple(rt.SECTION_LETTERS[k] for k in rt.SECTION_KEYS))
        self.assertEqual(rt.TAG_LETTERS, ("D", "S", "P", "L", "C", "O"))
        # §63.9 的行标签是同一套字母（proposed 是 §63.10 新增的那一节）
        self.assertEqual(set(rt.TAG_LETTERS) - {"P"}, {"D", "S", "L", "C", "O"})
        self.assertEqual(sorted(rt.SECTION_LETTERS), sorted(rt.SECTION_KEYS))
        self.assertEqual(len(set(rt.TAG_LETTERS)), len(rt.SECTION_KEYS))   # 一个字母只属于一节

    def test_only_the_closed_shape_is_a_tag(self):
        for good in ("D1", "S9", "P10", "L99", "C7", "O1"):
            self.assertTrue(rt.tag_ok(good), good)
        for bad in ("A1", "d1", "D0", "D100", "D", "1D", "", None, 7, "D1 ", "DD1"):
            self.assertFalse(rt.tag_ok(bad), repr(bad))
        self.assertEqual(rt.tag_number("D12"), 12)
        self.assertEqual(rt.tag_number("nope"), 0)
        self.assertEqual(rt.tag_letter("proposed"), "P")
        self.assertEqual(rt.tag_letter("nonesuch"), "")


class WebMirrorTestCase(unittest.TestCase):
    """web 那份手抄的字母表与它认的行首形（防腐 #10 / #5：镜像要有机器执法，不是注释里的
    一句「逐字同源」——`recapText.ts` 的正则改不了 Python，只有这条断言会红）。"""

    def test_the_web_letter_class_mirrors_the_daemon_table(self):
        source = (Path(__file__).resolve().parent.parent
                  / "web" / "src" / "components" / "recaps" / "recapText.ts").read_text(encoding="utf-8")
        self.assertIn("const ITEM_TAG_LINE = /^([%s][1-9]\\d?)\\.\\s/;" % "".join(rt.TAG_LETTERS),
                      source)
        # 渲染出去的那一份就是这个形（`D1. …`），两侧读的是同一个字符串
        doc, _seq = rt.assign_tags(payload([sections(["a"])]))
        self.assertTrue(rt.render_sections(doc["en"], "en").endswith("\nD1. a"))


class ParseTestCase(unittest.TestCase):
    """解析：前缀剥掉、标签落在自己那一列、校验不受影响。"""

    def test_a_claimed_tag_is_parsed_out_of_the_item_text(self):
        doc = rt.parse_sections(model_reply(["[D1] kept item", "new item"]))
        self.assertEqual(doc["en"][0]["items"], ["kept item", "new item"])
        self.assertEqual(doc["en"][0]["tags"], ["D1", ""])

    def test_a_tag_shaped_prefix_is_always_stripped_even_when_it_is_junk(self):
        # `[Q9]` 是模型写出来的 markup，不是承诺的一部分——留在正文里会被粘出去
        doc = rt.parse_sections(model_reply(["[Q9] not a letter", "[d02] lowercase and padded"]))
        self.assertEqual(doc["en"][0]["items"], ["not a letter", "lowercase and padded"])
        self.assertEqual(doc["en"][0]["tags"], ["", "D2"])

    def test_tags_never_change_the_validator_verdict(self):
        clean = rt.parse_sections(model_reply(["[D1] a decided item"]))
        self.assertEqual(rt.validate_sections_detail(clean), [])
        # `item_numbered` 认的仍是模型自己编的号，标签前缀不触发它
        numbered = rt.parse_sections(model_reply(["[D1] 1. still numbered"]))
        self.assertEqual([f["code"] for f in rt.validate_sections_detail(numbered)],
                         ["item_numbered", "item_numbered"])

    def test_stored_sections_stay_wellformed_with_the_new_key(self):
        stored = [sections(["a"], tags=["D1"])]
        self.assertTrue(rt.sections_wellformed(stored))


class AssignTestCase(unittest.TestCase):
    """派发：三趟（收声明 → 确定性回挂 → 发新号），标签永远不是模型写的。"""

    def test_a_first_version_numbers_every_item_inside_its_section(self):
        doc, seq = rt.assign_tags(payload([sections(["a", "b"]),
                                           sections(["c"], key="open", modality="open")]))
        self.assertEqual([sec["tags"] for sec in doc["en"]], [["D1", "D2"], ["O1"]])
        self.assertEqual([sec["tags"] for sec in doc["zh"]], [["D1", "D2"], ["O1"]])
        self.assertEqual(seq, {"D": 2, "O": 1})

    def test_a_kept_claim_survives_a_rewording_and_a_dropped_one_is_never_reused(self):
        first, seq = rt.assign_tags(payload([sections(["Ann owns the data mix", "Ship behind a flag"])]))
        second, seq2 = rt.assign_tags(
            payload([sections(["Ann now owns the data mix", "A brand new commitment"],
                              tags=["D1", ""])]), first, seq)
        self.assertEqual(second["en"][0]["tags"], ["D1", "D3"])   # D2 被删掉 → 永不再发
        self.assertEqual(seq2, {"D": 3})

    def test_an_unknown_duplicate_or_foreign_claim_is_discarded_and_reassigned(self):
        first, seq = rt.assign_tags(payload([sections(["a", "b"])]))
        # D9 = 这个 key 从没发过（另一份纪要的标签、或模型编的）；D1 报两次 = 只认第一条
        second, seq2 = rt.assign_tags(
            payload([sections(["a", "b", "c"], tags=["D9", "D1", "D1"])]), first, seq)
        self.assertEqual(second["en"][0]["tags"], ["D3", "D1", "D4"])
        self.assertEqual(seq2, {"D": 4})

    def test_a_tag_never_follows_an_item_into_another_section(self):
        first, seq = rt.assign_tags(payload([sections(["the eval clears the baseline"])]))
        moved, _seq = rt.assign_tags(
            payload([sections(["the eval clears the baseline"], key="proposed",
                              modality="proposed", tags=["D1"])]), first, seq)
        # 节内标签：挂在另一节标题下的 `D1` 是一句关于「它属于哪一节」的假话
        self.assertEqual(moved["en"][0]["tags"], ["P1"])

    def test_a_dropped_tag_is_re_attached_deterministically(self):
        first, seq = rt.assign_tags(payload([sections(["Ann owns the data mix",
                                                       "Ship behind a flag"])]))
        again, seq2 = rt.assign_tags(
            payload([sections(["Ann owns the data mix, confirmed", "Ship behind a flag"])]),
            first, seq)
        self.assertEqual(again["en"][0]["tags"], ["D1", "D2"])
        self.assertEqual(seq2, {"D": 2})                      # 回挂不消耗新号

    def test_a_rewrite_below_the_threshold_gets_a_fresh_tag_instead_of_a_wrong_one(self):
        first, seq = rt.assign_tags(payload([sections(["Ann owns the data mix"])]))
        other, seq2 = rt.assign_tags(payload([sections(["Bob writes the migration plan"])]),
                                     first, seq)
        self.assertEqual(other["en"][0]["tags"], ["D2"])
        self.assertEqual(seq2, {"D": 2})

    def test_two_equally_similar_candidates_are_a_miss_not_a_guess(self):
        first, seq = rt.assign_tags(payload([sections(["Ann owns the eval harness",
                                                       "Bob owns the eval harness"])]))
        tie, _seq = rt.assign_tags(payload([sections(["Cid owns the eval harness"])]), first, seq)
        self.assertEqual(tie["en"][0]["tags"], ["D3"])

    def test_the_match_gate_is_the_documented_pair_of_numbers(self):
        self.assertEqual((rt.TAG_MATCH_MIN, rt.TAG_MATCH_MARGIN), (0.72, 0.05))
        self.assertEqual(rt._best_match("annownsthedatamix", []), "")
        rows = [("D1", "annownsthedatamix")]
        self.assertEqual(rt._best_match("annownsthedatamixconfirmed", rows), "D1")
        self.assertEqual(rt._best_match("bobwritesthemigrationplan", rows), "")

    def test_a_lost_counter_is_recovered_from_the_previous_version(self):
        first, _seq = rt.assign_tags(payload([sections(["a", "b", "c"])]))
        # 手改过的记录：`tag_seq` 丢了 / 是垃圾——已经发出去的号仍然不许被第二条拿到
        for broken in (None, {}, {"D": True}, {"D": "3"}, {"X": 9}, "nope"):
            grown, seq = rt.assign_tags(payload([sections(["a", "b", "c", "d"],
                                                          tags=["D1", "D2", "D3", ""])]),
                                        first, broken)
            self.assertEqual(grown["en"][0]["tags"], ["D1", "D2", "D3", "D4"], repr(broken))
            self.assertEqual(seq["D"], 4)

    def test_the_counter_saturates_instead_of_reusing_a_number(self):
        doc, seq = rt.assign_tags(payload([sections(["a", "b"])]), None, {"D": rt.MAX_TAG_SEQ})
        self.assertEqual(doc["en"][0]["tags"], ["", ""])       # 号用尽 = 不发标签
        self.assertEqual(seq, {"D": rt.MAX_TAG_SEQ})
        # 渲染因此回落到 §63.10 的连续编号，而不是复用 D1
        self.assertEqual(rt.render_sections(doc["en"], "en"), "Decided:\n1. a\n2. b")

    def test_both_languages_share_the_tag_of_the_same_position(self):
        doc, _seq = rt.assign_tags(payload([sections(["a", "b"])],
                                           [sections(["甲", "乙", "丙"])]))
        self.assertEqual(doc["en"][0]["tags"], ["D1", "D2"])
        self.assertEqual(doc["zh"][0]["tags"], ["D1", "D2", "D3"])

    def test_a_language_whose_sections_do_not_line_up_gets_no_tags(self):
        doc, _seq = rt.assign_tags(payload([sections(["a"])],
                                           [sections(["甲"], key="open", modality="open")]))
        self.assertEqual(doc["en"][0]["tags"], ["D1"])
        self.assertEqual(doc["zh"][0]["tags"], [])            # 对不上就不瞎挂

    def test_a_hand_mangled_payload_comes_back_untouched(self):
        for bad in ({"en": [], "zh": []}, {"en": [7], "zh": [7]}, {"en": None, "zh": None}):
            out, seq = rt.assign_tags(bad, None, {"D": 2})
            self.assertEqual(out, dict(bad))
            self.assertEqual(seq, {"D": 2})

    def test_an_unknown_section_key_renders_without_tags(self):
        doc, seq = rt.assign_tags(payload([sections(["a"], key="nonesuch")]))
        self.assertEqual(doc["en"][0]["tags"], [""])
        self.assertEqual(seq, {})


class RenderTestCase(unittest.TestCase):
    """渲染：`D1. …`，没有标签的条目才回落到连续编号。"""

    def test_the_tag_replaces_the_number_in_the_pasted_body(self):
        doc, _seq = rt.assign_tags(payload([sections(["a", "b"]),
                                            sections(["c"], key="open", modality="open")]))
        self.assertEqual(rt.render_sections(doc["en"], "en"),
                         "Decided:\nD1. a\nD2. b\n\nOpen:\nO1. c")
        self.assertEqual(rt.render_sections(doc["zh"], "zh"),
                         "定了：\nD1. a\nD2. b\n\n待定：\nO1. c")

    def test_a_record_generated_before_this_section_keeps_its_numbering(self):
        self.assertEqual(rt.render_sections([sections(["a", "b"])], "en"),
                         "Decided:\n1. a\n2. b")

    def test_filler_items_take_their_tag_with_them(self):
        secs = [sections(["a", "none", "b"], tags=["D1", "D2", "D3"])]
        self.assertEqual(rt.render_sections(secs, "en"), "Decided:\nD1. a\nD3. b")

    def test_a_hand_mangled_tag_column_falls_back_per_item(self):
        secs = [sections(["a", "b", "c"], tags=["D1", "nope"])]
        self.assertEqual(rt.render_sections(secs, "en"), "Decided:\nD1. a\n2. b\n3. c")


class PromptTestCase(unittest.TestCase):
    """prompt：指令在模板里，上一版的条目在围栏里（宪法第 5 条）。"""

    def test_the_template_tells_the_model_to_keep_a_tag(self):
        header = rt.prompt_header(rt.SHAPE_SECTIONS)
        self.assertIn("[D1] ", header)
        self.assertIn("same commitment", header)
        self.assertIn("never carry a tag into a different section", header)
        # 五行形的模板一个字符没动（§63.3 是它自己的判例）
        self.assertNotIn("[D1]", rt.prompt_header(rt.SHAPE_LINES))

    def test_the_tagged_items_ride_inside_the_untrusted_fence(self):
        doc, _seq = rt.assign_tags(payload([sections(["Ann owns the data mix"])]))
        block = rt.tagged_items_block(doc["en"])
        self.assertEqual(block, "[D1] Ann owns the data mix")
        prompt = rt.build_prompt("transcript", {"when": "w", "app": "zoom", "duration_min": 20},
                                 [], shape=rt.SHAPE_SECTIONS, tagged=block)
        head, _sep, fenced = prompt.partition("Items of the previous version")
        self.assertNotIn("[D1] Ann owns the data mix", head)     # 指令区没有正文
        self.assertIn("[D1] Ann owns the data mix", fenced)
        # 围栏里，而且是它自己那一段的围栏（不是蹭转写那一段的）
        opened = fenced.partition("UNTRUSTED SOURCE MATERIAL")[2]
        self.assertIn("[D1] Ann owns the data mix", opened.partition("END UNTRUSTED")[0])

    def test_no_tagged_items_means_the_block_is_absent(self):
        self.assertIsNone(rt.tagged_items_block([]))
        self.assertIsNone(rt.tagged_items_block([sections(["a"])]))
        self.assertIsNone(rt.tagged_items_block([sections(["none"], tags=["D1"])]))
        prompt = rt.build_prompt("t", {"when": "w", "app": "zoom", "duration_min": 20}, [],
                                 shape=rt.SHAPE_SECTIONS)
        self.assertNotIn("Items of the previous version", prompt)


class IntentAnswerTestCase(unittest.TestCase):
    """§63.11 的答案认标签形（add-only）：位置形与 wire 的形状正则一字未动。"""

    def test_a_tag_shaped_id_is_an_item_answer(self):
        self.assertEqual(intent.kind_of("s2"), intent.KIND_ITEM)
        self.assertEqual(intent.kind_of("o10"), intent.KIND_ITEM)
        self.assertEqual(intent.item_tag("s2"), "S2")
        # 位置形与闭表 id 的判决一个字符没动
        self.assertEqual(intent.kind_of("split1"), intent.KIND_SPLIT)
        self.assertEqual(intent.kind_of("dl"), intent.KIND_DEADLINE)
        self.assertEqual(intent.kind_of("aud"), intent.KIND_AUDIENCE)
        for bad in ("x1", "s0", "s100", "S2", "sd", "d", ""):
            self.assertIsNone(intent.kind_of(bad), bad)

    def test_the_wire_shape_did_not_change(self):
        self.assertTrue(intent.answers_ok(["s2=drop", "split1=keep", "aud=send"]))
        self.assertTrue(all(intent.ANSWER_RE.match(a) for a in ["s2=drop", "o1=propose"]))
        self.assertFalse(intent.answers_ok(["S2=drop"]))       # 大写进不来（形状正则未动）
        self.assertFalse(intent.answers_ok(["s2=nonesuch"]))
        self.assertFalse(intent.answers_ok(["s2=drop", "s2=keep"]))   # id 不重复

    def test_the_instruction_names_the_tag_and_carries_no_text(self):
        block = intent.prompt_block(["s2=drop", "d1=propose", "o3=keep"])
        self.assertIn("Item S2: drop it entirely", block)
        self.assertIn("Item D1: it is not agreed", block)
        self.assertIn("Item O3: keep it", block)
        self.assertNotIn("[", block)

    def test_a_tag_answer_reaches_the_daemon_as_one_argv_tail(self):
        argv = store.inbox_argv({"action": "recap_generate", "meeting_key": KEY,
                                 "answers": ["s2=drop"]})
        self.assertEqual(argv, ["--generate", KEY, "--answers", '["s2=drop"]'])
        self.assertIsNone(store.inbox_argv({"action": "recap_generate", "meeting_key": KEY,
                                            "answers": ["s2=drop", "x9=drop"]}))


class RecordTestCase(unittest.TestCase):
    """出稿 / 重新生成 / 回退 / 投影：标签落在记录上，计数器不回头。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-recap-tags-")
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        (root / "state").mkdir()
        mock.patch.object(config, "STATE_DIR", root / "state").start()
        mock.patch.object(notify, "notify", return_value=True).start()
        self.addCleanup(mock.patch.stopall)
        self.conn = fx.make_db(root / "db.sqlite")
        self.addCleanup(self.conn.close)
        self.cfg = config.Config(raw={"recap": {"default_shape": "sections"}})
        self.prompts = []
        self.reply = model_reply(["Ann owns the data mix", "Ship behind a flag"],
                                 ["数据配比归 Ann", "先挂开关上线"])
        recap.run_once(now=fx.T0 - 3600, conn=self.conn, runner=self._runner, cfg=self.cfg)
        fx.add_frames(self.conn, fx.T0, 20)
        fx.add_audio(self.conn, fx.T0, 20)

    def _runner(self, argv, **kwargs):
        self.prompts.append(str(kwargs.get("input") or (argv[2] if len(argv) > 2 else "")))
        return subprocess.CompletedProcess(argv, 0, stdout=self.reply, stderr="")

    def _closed(self):
        recap.run_once(now=fx.T0 + 34 * MIN, conn=self.conn, runner=self._runner, cfg=self.cfg)
        return store.load_recap(KEY)

    def _generate(self, at):
        recap.generate(KEY, now=at, conn=self.conn, runner=self._runner, cfg=self.cfg)
        return store.load_recap(KEY)

    def test_the_first_version_lands_with_tags_in_the_pasted_body(self):
        rec = self._closed()
        self.assertEqual([sec["tags"] for sec in rec["sections_en"]], [["D1", "D2"]])
        self.assertEqual([sec["tags"] for sec in rec["sections_zh"]], [["D1", "D2"]])
        self.assertEqual(rec["tag_seq"], {"D": 2})
        self.assertEqual(rec["copy_en"], "Decided:\nD1. Ann owns the data mix\nD2. Ship behind a flag")
        self.assertIn("D1. 数据配比归 Ann", rec["copy_zh"])
        # 第一版的 prompt 里没有那一块（还没有上一版）
        self.assertNotIn("Items of the previous version", self.prompts[-1])

    def test_a_regeneration_keeps_the_tags_and_only_new_items_get_new_ones(self):
        self._closed()
        self.reply = model_reply(["[D1] Ann owns the data mix from Monday", "A third commitment"],
                                 ["[D1] 数据配比自周一起归 Ann", "第三条"])
        rec = self._generate(fx.T0 + 40 * MIN)
        self.assertEqual(rec["sections_en"][0]["tags"], ["D1", "D3"])
        self.assertEqual(rec["tag_seq"], {"D": 3})
        self.assertIn("D1. Ann owns the data mix from Monday", rec["copy_en"])
        # 上一版的条目带着标签进了围栏，模型据此保留 D1
        self.assertIn("[D1] Ann owns the data mix", self.prompts[-1])
        self.assertIn("[D2] Ship behind a flag", self.prompts[-1])

    def test_the_counter_outlives_the_history_cap_and_the_five_line_shape(self):
        self._closed()
        # 中间插一版五行形（标签不适用），计数器不许因此回头
        recap.generate(KEY, shape="lines", now=fx.T0 + 40 * MIN, conn=self.conn, cfg=self.cfg,
                       runner=lambda argv, **kw: subprocess.CompletedProcess(
                           argv, 0, stdout=fx.good_output(), stderr=""))
        mid = store.load_recap(KEY)
        self.assertEqual(mid["tag_seq"], {"D": 2})
        self.reply = model_reply(["Something else entirely"], ["完全另一件事"])
        recap.generate(KEY, shape="sections", now=fx.T0 + 50 * MIN, conn=self.conn,
                       runner=self._runner, cfg=self.cfg)
        back = store.load_recap(KEY)
        self.assertEqual(back["sections_en"][0]["tags"], ["D3"])
        # 记录上那一版是五行形，没有条目可喂——围栏里那一块因此整块缺席，而 D1 / D2
        # 仍然不会被第二条承诺拿到（计数器在记录上，不在正文里）
        self.assertNotIn("Items of the previous version", self.prompts[-1])

    def test_a_revert_restores_the_versions_tags_unchanged(self):
        self._closed()
        self.reply = model_reply(["A different commitment"], ["另一条"])
        self._generate(fx.T0 + 40 * MIN)
        rec = recap.revert(KEY, 1, now=fx.T0 + 50 * MIN)
        self.assertEqual(rec["sections_en"][0]["tags"], ["D1", "D2"])
        self.assertEqual(rec["copy_en"], "Decided:\nD1. Ann owns the data mix\nD2. Ship behind a flag")
        self.assertEqual(rec["reverted_from"], 1)
        # 计数器不回头：回退之后的新条目仍拿下一个号
        self.assertEqual(rec["tag_seq"], {"D": 3})
        self.reply = model_reply(["Yet another commitment"], ["又一条"])
        self.assertEqual(self._generate(fx.T0 + 60 * MIN)["sections_en"][0]["tags"], ["D4"])

    def test_the_projection_carries_the_tags_and_the_counter_verbatim(self):
        self._closed()
        row = next(r for r in store.all_rows() if r["key"] == KEY)
        self.assertEqual(row["sections_en"][0]["tags"], ["D1", "D2"])
        self.assertEqual(row["tag_seq"], {"D": 2})
        self.assertIn("D1. Ann owns the data mix", row["copy_en"])
        # 标签不是卡片身份：recap 仍没有 id / status 机（§0 第 4 条）
        blob = json.dumps(row, ensure_ascii=False)
        for banned in ('"recipient"', '"channel"', '"tier"'):
            self.assertNotIn(banned, blob)

    def test_a_record_from_before_this_section_gets_tags_on_its_next_version(self):
        rec = self._closed()
        # 模拟 §63.12 之前入库的那一版：`tags` / `tag_seq` 都不在
        for sec in rec["sections_en"] + rec["sections_zh"]:
            sec.pop("tags", None)
        rec.pop("tag_seq", None)
        rec["copy_en"] = rt.render_sections(rec["sections_en"], "en")
        store.save_recap(rec)
        self.assertIn("1. Ann owns the data mix", rec["copy_en"])
        again = self._generate(fx.T0 + 40 * MIN)
        self.assertEqual(again["sections_en"][0]["tags"], ["D1", "D2"])
        self.assertEqual(again["tag_seq"], {"D": 2})


if __name__ == "__main__":
    unittest.main()
