"""§63.11（issue #302）：出稿之前先问几句，第二版按答案出，第一版永久留着。

同一份转写按不同的用途要的是不同的文档（issue #302 的实测：生成稿是一份中立摘要，
真发出去的那份是一份承诺记录，逐条删过 / 改过），而管线从来没问过。所以第一版照旧
出（§63.3 / §63.10 一字未动），然后从**那一版自己身上**推出一组问题摆在它旁边，
owner 答完第二版按答案重出，第一版作为 `baseline` 永久留在记录上。

本文件钉的是这一条的整条路：问题怎么从存着的正文推出来（逐条分工 / 截止 / #332
实测排序补的两条 / issue 的第三条候选 / 只有真有上一份时才问的那条）、答案的闸
（字符串列表、闭表、全有或全无）、答案怎么进 prompt（指令只写编号，条目原文进
围栏——宪法第 5 条）、`prior=drop` 的**确定性**落地（不求模型）、`baseline` 只写
一次且不被 HISTORY_CAP 挤掉、`intent` 每版重写、投影里的 `questions`、
以及 inbox 特形的 argv。
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
from act.lib import recap_intent as ri
from act.lib import recap_store as store
from act.lib import recap_text as rt

KEY = fx.KEY
MIN = 60.0

SPLIT_LINE = "Split: Ann: ships the eval; Zelin: reads the papers"


def lines_record(split: str = SPLIT_LINE, deadline: str = "Deadline: Friday") -> dict:
    return {"shape": "lines",
            "en": ["Decided: the run moves to Monday", split, deadline,
                   "Changed since last plan: none recorded", "Open: none"],
            "zh": ["定了：训练周一开始", "分工：甲", "截止：周五", "较上次变化：无记录", "待定：无"]}


def sections_record() -> dict:
    return {"shape": "sections",
            "sections_en": [{"key": "decided", "modality": "decided", "items": ["The run moves"]},
                            {"key": "split", "modality": "decided",
                             "items": ["Ann ships the eval", "Zelin reads the papers"]},
                            {"key": "changed", "modality": "decided", "items": ["nothing moved"]}],
            "sections_zh": [{"key": "decided", "modality": "decided", "items": ["训练周一开始"]},
                            {"key": "split", "modality": "decided",
                             "items": ["评测归 Ann", "论文归 Zelin"]},
                            {"key": "changed", "modality": "decided", "items": ["没有变化"]}]}


class DeriveTestCase(unittest.TestCase):
    """问题是从这一份纪要自己身上推出来的，不是一张写死的问卷。"""

    def ids(self, rec, has_priors=False) -> list:
        return [q["id"] for q in ri.derive(rec, has_priors)]

    def test_the_split_line_becomes_one_question_per_commitment(self):
        questions = ri.derive(lines_record())
        splits = [q for q in questions if q["kind"] == ri.KIND_SPLIT]
        self.assertEqual([q["id"] for q in splits], ["split1", "split2"])
        self.assertEqual([q["subject"] for q in splits],
                         ["Ann: ships the eval", "Zelin: reads the papers"])
        # 选项是闭表，一条问题的 wire 形键恒在（web 只渲染这份数据，不自己造问题）
        self.assertEqual(splits[0]["options"], ["keep", "drop", "propose"])
        self.assertEqual(sorted(splits[0]), ["id", "kind", "options", "subject"])

    def test_the_globals_follow_the_measured_cut_order_and_the_issues_third_question(self):
        """#332 的实测排序：本人的承诺 > 对方的要求/归属/进度 > 研究级细节与保留说法。

        后两类在 #302 的候选清单里**没有**（它们是 #332 第二份样本数出来的第二、
        第三高频手删类），`own` 是 #302 自己的第三条候选（「is this something the
        user wants to own?」）——三条都在，否则实测样本白量了一遍。"""
        ids = self.ids(lines_record())
        self.assertEqual(ids, ["split1", "split2", "dl", "others", "detail", "aud", "own"])
        kinds = {q["id"]: q["kind"] for q in ri.derive(lines_record())}
        self.assertEqual(kinds["others"], ri.KIND_OTHERS)
        self.assertEqual(kinds["detail"], ri.KIND_DETAIL)
        self.assertEqual(kinds["own"], ri.KIND_OWN)

    def test_the_prior_question_only_exists_when_a_prior_does(self):
        self.assertNotIn("prior", self.ids(lines_record()))
        self.assertIn("prior", self.ids(lines_record(), has_priors=True))

    def test_filler_and_unassigned_are_not_questions(self):
        """模板自己规定的空写法不是一条可留可删的承诺（查表，不是对散文做正则）。"""
        self.assertEqual(self.ids(lines_record(split="Split: not assigned",
                                               deadline="Deadline: none set")),
                         ["others", "detail", "aud", "own"])
        self.assertEqual(self.ids(lines_record(split="分工：未分配", deadline="截止：未定")),
                         ["others", "detail", "aud", "own"])
        # 「截止：未定但周五确认」不是填充值（§63.10 的同一条判据）——照旧问
        self.assertIn("dl", self.ids(lines_record(deadline="Deadline: none set but confirmed Friday")))

    def test_a_split_line_whose_label_is_gone_is_read_as_one_whole_body(self):
        """标签对不上 = 整行当正文（模型漏了标签的那一版、手改过的记录都算）——
        那一行里的承诺照旧一条条问得出来，不是静默吞掉一整行分工。"""
        self.assertEqual(ri.split_subjects(lines_record(split="Ann: eval; Zelin: papers")),
                         ["Ann: eval", "Zelin: papers"])
        # 中文标签也认（owner 手改成中文的那一版）
        self.assertEqual(ri.split_subjects(lines_record(split="分工：Ann: eval")),
                         ["Ann: eval"])

    def test_an_empty_slot_between_two_semicolons_is_not_a_commitment(self):
        """分号切出来的空条目不算一条（模板要求 `owner: item` 对，分号是分隔符）。"""
        self.assertEqual(
            ri.split_subjects(lines_record(split="Split: ; Ann: eval ;; not assigned")),
            ["Ann: eval"])

    def test_something_that_is_not_a_record_has_no_split_at_all(self):
        """投影行 / 手改坏的文件都会走到这里（`split_subjects` 是 CLI 与投影共用的
        那一支）：不是表 = 一条都问不出，永不抛。"""
        for rec in (None, "not a record", 7, [], {"shape": "lines"}):
            with self.subTest(rec=rec):
                self.assertEqual(ri.split_subjects(rec), [])

    def test_the_sendable_shape_asks_about_its_split_section(self):
        questions = ri.derive(sections_record(), has_priors=True)
        self.assertEqual([q["subject"] for q in questions if q["kind"] == ri.KIND_SPLIT],
                         ["Ann ships the eval", "Zelin reads the papers"])
        self.assertNotIn("dl", [q["id"] for q in questions])   # 没有 deadline 那一节

    def test_a_recap_without_text_is_asked_nothing_and_the_caps_hold(self):
        # 「有没有正文」问的是 `recap_store.has_text` 的同一个判据（§63.10：空只有一个判据）
        for empty in ({}, {"en": None}, {"shape": "sections", "sections_en": []},
                      "not a dict", None):
            self.assertEqual(ri.derive(empty), [], empty)
            self.assertFalse(store.has_text(empty), empty)
        # 手改坏的记录（有正文但不是五行）：逐条的那几个问不出来，全局的照旧问
        mangled = {"shape": "lines", "en": ["Decided: x"]}
        self.assertTrue(store.has_text(mangled))
        self.assertEqual([q["id"] for q in ri.derive(mangled)],
                         ["others", "detail", "aud", "own"])
        many = "Split: " + "; ".join("Person %d: item %d" % (i, i) for i in range(20))
        questions = ri.derive(lines_record(split=many), has_priors=True)
        self.assertEqual(len([q for q in questions if q["kind"] == ri.KIND_SPLIT]),
                         ri.MAX_SPLIT_QUESTIONS)
        self.assertLessEqual(len(questions), ri.MAX_QUESTIONS)
        # 问出来的每一条都必须答得上去：问题上限恒 ≤ 答案上限
        self.assertEqual(ri.MAX_QUESTIONS, ri.MAX_ANSWERS)
        long_split = "Split: Ann: " + "x" * 400
        self.assertEqual(len(ri.derive(lines_record(split=long_split))[0]["subject"]),
                         ri.MAX_SUBJECT_CHARS)


class AnswersTestCase(unittest.TestCase):
    """答案的闸：字符串列表、闭表、id 不重复、**全有或全无**。"""

    def test_a_well_formed_answer_sheet_passes(self):
        self.assertTrue(ri.answers_ok(["split1=drop", "split6=propose", "dl=keep", "others=drop",
                                       "detail=drop", "aud=send", "own=decline", "prior=compare"]))
        self.assertEqual(ri.answer_map(["split1=drop", "aud=send"]),
                         {"split1": "drop", "aud": "send"})

    def test_everything_else_is_the_whole_request_being_malformed(self):
        for bad in (None, "split1=drop", [], [{"split1": "drop"}], ["split1"], ["split1=drop=keep"],
                    ["split1=nope"], ["nosuch=drop"], ["split0=drop"], ["split7=drop"],
                    ["split1=drop", "split1=keep"], ["SPLIT1=drop"], ["aud=SEND"],
                    ["x" * 12 + "=drop"], ["aud=" + "x" * 13], True,
                    ["aud=send"] * (ri.MAX_ANSWERS + 1)):
            with self.subTest(bad=bad):
                self.assertFalse(ri.answers_ok(bad))
                self.assertEqual(ri.clean_answers(bad), [])
                self.assertEqual(ri.answer_map(bad), {})
        self.assertFalse(ri.drops_prior(["prior=bogus"]))
        self.assertTrue(ri.drops_prior(["prior=drop"]))
        self.assertFalse(ri.drops_prior(["prior=compare"]))

    def test_the_wire_shape_is_a_list_of_strings_because_the_serializer_says_so(self):
        """inbox 的字节序列化器只认 null / bool / 整数 / 字符串 / 列表——dict 当场 TypeError。

        `answers` 因此是 `["split1=drop"]` 而不是 `{"split1": "drop"}`：37 份 golden 的
        Mac 字节形里一处嵌套对象都没有，换 dict 要连带重钉全部 golden。"""
        from server import inbox_writer
        self.assertEqual(inbox_writer._dump_value(["split1=drop"], 2),
                         '[\n    "split1=drop"\n  ]')
        with self.assertRaises(TypeError):
            inbox_writer._dump_value({"split1": "drop"}, 2)


class PromptTestCase(unittest.TestCase):
    """答案怎么进 prompt：指令只写编号，条目原文进围栏（宪法第 5 条）。"""

    def test_the_instruction_block_names_numbers_and_never_the_text(self):
        block = ri.prompt_block(["split1=drop", "split2=propose", "aud=send", "own=decline"])
        self.assertIn("Item 1: drop it entirely", block)
        self.assertIn("Item 2:", block)
        self.assertIn("sent to the other party", block)
        self.assertIn("does not want to own", block)
        self.assertNotIn("Ann", block)                      # 条目原文一个字都不在指令里
        self.assertIsNone(ri.prompt_block([]))
        self.assertIsNone(ri.prompt_block(["bogus=drop"]))  # 畸形 = 这一段根本不进 prompt

    def test_an_option_the_instruction_table_has_no_line_for_is_skipped(self):
        """两张 add-only 闭表是同一件事的两半（`OPTIONS` = 问得出什么，
        `_INSTRUCTIONS` = 那句指令）。真加一条选项时两半一起加；万一只加了一半，
        那条答案被**静默跳过**，其余答案照常进 prompt——既不写出一句空指令，也不
        崩掉这一版生成（宪法第 11 条）。"""
        drifted = {ri.KIND_PRIOR: ("compare", "drop", "postpone")}
        with mock.patch.dict(ri.OPTIONS, drifted):
            block = ri.prompt_block(["prior=postpone", "aud=send"])
            self.assertIsNone(ri.prompt_block(["prior=postpone"]))
        self.assertIn("sent to the other party", block)
        self.assertNotIn("postpone", block)

    def test_the_items_the_answers_name_ride_in_the_untrusted_fence(self):
        rec = lines_record()
        subjects = ri.split_subjects(rec)
        prompt = rt.build_prompt("transcript", {"when": "w", "app": "zoom", "duration_min": 20}, [],
                                 intent=ri.prompt_block(["split1=drop"]),
                                 baseline=ri.baseline_block(subjects, rt.render(rec["en"])))
        self.assertIn("Item 1: drop it entirely", prompt)
        head = prompt.index("UNTRUSTED")
        # 编号表与上一版正文都在围栏**之后**出现，指令块在围栏之前
        self.assertLess(prompt.index("Item 1: drop it entirely"), head)
        fenced = prompt[prompt.index("The previous version these answers refer to"):]
        self.assertIn("1. Ann: ships the eval", fenced)
        self.assertIn("UNTRUSTED", fenced[:fenced.index("1. Ann: ships the eval")])
        self.assertIsNone(ri.baseline_block([], ""))

    def test_dropping_the_prior_is_executed_not_asked_for(self):
        """`prior=drop` 是唯一一条确定性落地的答案：五行钉成模板的填充串（渲染整行略掉），
        长版把 `changed` 整节去掉；一节都不剩时照原样出（「空」只有一个判据，§63.10）。"""
        parsed = {"en": list(lines_record()["en"]), "zh": list(lines_record()["zh"])}
        parsed["en"][rt.CHANGED_INDEX] = "Changed since last plan: the deadline moved to Friday"
        out = rt.drop_prior(rt.SHAPE_LINES, parsed)
        self.assertEqual(out["en"][rt.CHANGED_INDEX], rt.PRIOR_DROPPED_EN)
        self.assertEqual(out["zh"][rt.CHANGED_INDEX], rt.PRIOR_DROPPED_ZH)
        self.assertTrue(rt.is_filler_line(out["en"][rt.CHANGED_INDEX], rt.CHANGED_INDEX))
        self.assertNotIn("Changed since last plan", rt.render(out["en"]))
        self.assertEqual(rt.validate(out), [])              # 钉出来的那行永远合法
        secs = {"en": sections_record()["sections_en"], "zh": sections_record()["sections_zh"]}
        kept = rt.drop_prior(rt.SHAPE_SECTIONS, secs)
        self.assertEqual([sec["key"] for sec in kept["en"]], ["decided", "split"])
        self.assertEqual([sec["key"] for sec in kept["zh"]], ["decided", "split"])
        only = {"en": [secs["en"][2]], "zh": [secs["zh"][2]]}
        self.assertEqual([sec["key"] for sec in rt.drop_prior(rt.SHAPE_SECTIONS, only)["en"]],
                         ["changed"])
        for junk in (None, "x", {"en": "x", "zh": "y"}):
            rt.drop_prior(rt.SHAPE_SECTIONS, junk)          # 手改坏的输入永不抛
            rt.drop_prior(rt.SHAPE_LINES, junk)


class GenerationTestCase(unittest.TestCase):
    """整条路：答案落到记录上、baseline 只写一次、投影里的 questions。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-recap-intent-")
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        (root / "state").mkdir()
        mock.patch.object(config, "STATE_DIR", root / "state").start()
        mock.patch.object(notify, "notify", return_value=True).start()
        self.addCleanup(mock.patch.stopall)
        self.conn = fx.make_db(root / "db.sqlite")
        self.addCleanup(self.conn.close)
        self.cfg = config.Config(raw={})
        self.prompts = []
        recap.run_once(now=fx.T0 - 3600, conn=self.conn, runner=self._runner, cfg=self.cfg)
        fx.add_frames(self.conn, fx.T0, 20)
        fx.add_audio(self.conn, fx.T0, 20)
        recap.run_once(now=fx.T0 + 34 * MIN, conn=self.conn, runner=self._runner, cfg=self.cfg)

    def _runner(self, argv, **kwargs):
        self.prompts.append(argv[2] if len(argv) > 2 else "")   # claude -p <prompt> …
        return subprocess.CompletedProcess(argv, 0, stdout=fx.good_output(), stderr="")

    def _generate(self, answers, now_min=40, runner=None):
        return recap.generate(KEY, answers=answers, now=fx.T0 + now_min * MIN, conn=self.conn,
                              runner=runner or self._runner, cfg=self.cfg)

    def test_the_answers_land_on_the_record_and_the_first_version_stays(self):
        first = store.load_recap(KEY)
        self.assertIsNone(first["baseline"])                # 只有一版 = 没有「原版」可切
        self.assertIsNone(first["intent"])
        body = first["copy_en"]
        rec = self._generate(["split1=drop", "aud=send"])
        self.assertEqual(rec["version"], 2)
        self.assertEqual(rec["intent"]["answers"], ["split1=drop", "aud=send"])
        self.assertEqual(rec["intent"]["version"], 2)
        self.assertTrue(rec["intent"]["at"])
        # baseline = 我们见过的第一版的**可粘正文**，形状与版本号一起带着
        self.assertEqual(rec["baseline"], {"version": 1, "generated_at": first["generated_at"],
                                           "shape": "lines", "copy_en": body,
                                           "copy_zh": first["copy_zh"]})

    def test_the_baseline_is_written_once_and_survives_the_history_cap(self):
        """history 的帽是 5 版——第一版早晚被挤掉，而「转写原版」必须一直在。"""
        first = store.load_recap(KEY)["copy_en"]
        for i in range(recap.HISTORY_CAP + 2):
            self._generate(["detail=drop"], now_min=40 + i)
        rec = store.load_recap(KEY)
        self.assertEqual(len(rec["history"]), recap.HISTORY_CAP)
        self.assertNotIn(1, [h["version"] for h in rec["history"]])   # 第一版已被挤出历史
        self.assertEqual(rec["baseline"]["version"], 1)               # 但 baseline 还在
        self.assertEqual(rec["baseline"]["copy_en"], first)

    def test_a_generation_without_answers_clears_the_receipt_but_not_the_baseline(self):
        self._generate(["aud=self"])
        rec = self._generate(None, now_min=50)
        self.assertIsNone(rec["intent"])            # 回执属于产出这版正文的那次生成
        self.assertEqual(rec["baseline"]["version"], 1)

    def test_a_malformed_answer_sheet_is_dropped_whole(self):
        rec = self._generate(["split1=drop", "bogus=x"])
        self.assertIsNone(rec["intent"])
        self.assertNotIn("Item 1: drop it entirely", self.prompts[-1])
        self.assertEqual(recap._cli_answers('["split1=drop"]'), ["split1=drop"])
        for bad in ("", "not json", '{"split1": "drop"}', '["split9=drop"]'):
            self.assertEqual(recap._cli_answers(bad), [], bad)

    def test_the_answers_reach_the_prompt_and_prior_drop_reaches_the_text(self):
        self._generate(["split1=propose", "prior=drop"])
        prompt = self.prompts[-1]
        self.assertIn("The owner answered these questions", prompt)
        self.assertIn("Item 1:", prompt)
        rec = store.load_recap(KEY)
        # 模型回的那一行被钉成填充串（fx.good_output 本来就写填充值，这里钉的是它恒等于模板串）
        self.assertEqual(rec["en"][rt.CHANGED_INDEX], rt.PRIOR_DROPPED_EN)
        self.assertNotIn("Changed since last plan", rec["copy_en"])
        self.assertEqual(rec["quality"], store.QUALITY_OK)

    def test_prior_drop_overrides_a_model_line_that_would_have_failed(self):
        """答案覆盖的那一行**在校验之前**被钉住：那行的问题不进 problems，
        因为它根本不是落地正文的一部分（一条对不上正文的原因就是一条假回执）。"""
        long_line = "Changed since last plan: " + "x" * 200
        bad = json.dumps({"en": ["Decided: ok", "Split: not assigned", "Deadline: none set",
                                 long_line, "Open: none"],
                          "zh": ["定了：好", "分工：未分配", "截止：未定", "较上次变化：" + "甲" * 80,
                                 "待定：无"]})
        rec = self._generate(["prior=drop"], runner=lambda argv, **kw: subprocess.CompletedProcess(
            argv, 0, stdout=bad, stderr=""))
        self.assertEqual(rec["quality"], store.QUALITY_OK)
        self.assertEqual(rec["problems"], [])
        self.assertEqual(rec["en"][rt.CHANGED_INDEX], rt.PRIOR_DROPPED_EN)

    def test_the_projection_carries_the_questions_and_nothing_stores_them(self):
        rows = store.all_rows()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual([q["id"] for q in row["questions"]],
                         [q["id"] for q in ri.derive(store.load_recap(KEY))])
        self.assertNotIn("questions", store.load_recap(KEY))   # 投影层现算，不落盘
        self.assertNotIn("prior", [q["id"] for q in row["questions"]])
        self.assertEqual(store.projection()[0]["questions"], row["questions"])

    def test_the_prior_question_appears_once_an_earlier_recap_exists(self):
        """`prior` 那条问题的判据与 `priors_for` 的 14 天窗同源，且**不为每行再扫一遍磁盘**。"""
        second = fx.T0 + 26 * 3600
        fx.add_frames(self.conn, second, 20)
        fx.add_audio(self.conn, second, 20)
        recap.run_once(now=second + 34 * MIN, conn=self.conn, runner=self._runner, cfg=self.cfg)
        rows = {row["key"]: row for row in store.all_rows()}
        self.assertEqual(len(rows), 2)
        newer = max(rows.values(), key=lambda row: row["start"])
        older = min(rows.values(), key=lambda row: row["start"])
        self.assertIn("prior", [q["id"] for q in newer["questions"]])
        self.assertNotIn("prior", [q["id"] for q in older["questions"]])

    def test_the_inbox_form_carries_the_answers_to_the_cli(self):
        base = {"action": "recap_generate", "meeting_key": KEY}
        self.assertEqual(store.inbox_argv(dict(base, answers=["split1=drop", "aud=send"])),
                         ["--generate", KEY, "--answers", '["split1=drop","aud=send"]'])
        self.assertEqual(store.inbox_argv(dict(base, shape="sections", answers=["dl=drop"])),
                         ["--generate", KEY, "--shape", "sections", "--answers", '["dl=drop"]'])
        self.assertEqual(store.inbox_argv(base), ["--generate", KEY])
        for bad in ([], ["split1=nope"], "split1=drop", [{"a": "b"}], ["aud=send"] * 13):
            self.assertIsNone(store.inbox_argv(dict(base, answers=bad)), bad)
        # 无发送路径一寸没松（§63 第 4 层）：答案带不进任何会话 / 收件人
        self.assertNotIn("C0123456789", " ".join(
            store.inbox_argv(dict(base, answers=["aud=send"]))))


if __name__ == "__main__":
    unittest.main()
