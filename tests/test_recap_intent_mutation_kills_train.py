"""§63.11 / §63.12 的问答闸门里夜报变异体活下来的那几格（`act/lib/recap_intent.py`）。

判例 tests/test_recap_intent.py 钉住了这一节的主干（问题从正文推出来、答案闭表、
指令只写编号、`prior=drop` 确定性落地），但夜间变异（§57）在这个模块上留了一串
存活体，全落在四类格子里——它们都是「畸形输入进来时这一步答什么」，而主干判例喂
的一直是形状正确的记录：

* **上 wire 的那个帽**（`MAX_SUBJECT_CHARS`）：一条 subject 截到 160 字符是
  投影的有界性（防腐 #4），主干判例的 split 行都短得碰不到它。
* **手改坏 / 不是记录的输入**：标签被改掉的 `Split:` 行（整行当正文）、
  `split_subjects` 拿到的不是 dict（答一张空表，不是 None，更不是抛）、
  空条目不是承诺（`;;` 切出来的空串不许变成一条可留可删的责任）。
* **公开谓词答真 bool**：`kind_of` 对非字符串答 `None` 而不是崩（整条请求畸形 =
  actd 诚实 noop）、`answers_ok` 答 `False` 而不是 `None`——答 `None` 就是把
  「判不了」和「判成假」混成一件事。
* **答案与它指的那一条之间的连线**：`baseline_block` 的编号从 1 起（`Item 3` 指
  的是围栏里第 3 行，宪法第 5 条的那条连线），上一版正文真的进围栏，答案上限
  （12）与 `derive` 能问出来的最大条数**恰好相等**——问出来的每一条都答得上去。

注入缝一个没有：本模块是纯函数（不调模型、不读盘），喂什么就判什么。

**一个体判为等价（可达输入上无可观察差异，不强杀）**：`_real_item` 的
`return False`（`→ return None`）——私名，两个消费者（`split_subjects` 的列表
过滤与 `_has_deadline` 的 `any(...)`）都只取真假，`None` 与 `False` 同为假值。
"""
import unittest

from tests import TMP_HOME  # noqa: F401 - sandbox env first

from act.lib import recap_intent as ri

SPLIT = "Split: Ann: ships the eval; Zelin: reads the papers"


def lines_record(split: str = SPLIT, deadline: str = "Deadline: Friday") -> dict:
    """一条五行形的记录（§63.3 的形状，`derive` 的输入）。"""
    return {"shape": "lines",
            "en": ["Decided: the run moves to Monday", split, deadline,
                   "Changed since last plan: none recorded", "Open: none"],
            "zh": ["定了：训练周一开始", "分工：甲", "截止：周五", "较上次变化：无记录", "待定：无"]}


def sections_record(deadline_items) -> dict:
    """一条可发送长版的记录（§63.10），`deadline` 那一节由调用方给。"""
    return {"shape": "sections",
            "sections_en": [{"key": "decided", "modality": "decided", "items": ["The run moves"]},
                            {"key": "split", "modality": "decided", "items": ["Ann ships the eval"]},
                            {"key": "deadline", "modality": "decided", "items": list(deadline_items)}],
            "sections_zh": [{"key": "decided", "modality": "decided", "items": ["训练周一开始"]},
                            {"key": "split", "modality": "decided", "items": ["评测归 Ann"]},
                            {"key": "deadline", "modality": "decided", "items": list(deadline_items)}]}


class SubjectCapTestCase(unittest.TestCase):
    """一条 subject 上 wire 的长度帽：投影里 6 条 × 60 行要有界（防腐 #4）。"""

    def test_a_long_commitment_rides_the_wire_cut_to_one_hundred_and_sixty_chars(self):
        long_item = "Ann: " + "reviews the evaluation harness with the vendor " * 6
        rec = lines_record(split="Split: " + long_item)
        subject = ri.split_subjects(rec)[0]
        # 160 是这一格的出厂数：原文 280 字符，上 wire 的是前 160 个字符，一个不多
        self.assertEqual(len(subject), 160)
        self.assertTrue(" ".join(long_item.split()).startswith(subject))
        # 问题里的那一份与 split_subjects 是同一串（面板渲染的就是它）
        self.assertEqual(ri.derive(rec)[0]["subject"], subject)

    def test_a_commitment_inside_the_cap_is_not_touched(self):
        rec = lines_record(split="Split: Ann: ships the eval")
        self.assertEqual(ri.split_subjects(rec), ["Ann: ships the eval"])


class MalformedRecordTestCase(unittest.TestCase):
    """手改坏的记录进来：答一张空表 / 整行当正文，永不抛（宪法第 11 条）。"""

    def test_a_split_line_whose_label_was_edited_away_is_one_whole_subject(self):
        # 标签对不上 = 整行当正文（英文记录、手改成中文的记录都认）
        rec = lines_record(split="Ann: ships the eval; Zelin: reads the papers")
        self.assertEqual(ri.split_subjects(rec), ["Ann: ships the eval", "Zelin: reads the papers"])
        zh_edited = lines_record(split="分工：甲：写评测；乙：读论文")
        self.assertEqual(ri.split_subjects(zh_edited), ["甲：写评测", "乙：读论文"])

    def test_anything_that_is_not_a_record_asks_for_an_empty_list_of_subjects(self):
        for junk in (None, "a recap", 7, [], {"en": None}, {"en": ["only", "two"]}):
            with self.subTest(junk=junk):
                self.assertEqual(ri.split_subjects(junk), [])

    def test_an_empty_piece_of_the_split_line_is_not_a_commitment(self):
        # 分号切出来的空串不是一条可留可删的责任——面板不许出现一个没有正文的格子
        rec = lines_record(split="Split: Ann: ships the eval; ; Zelin: reads the papers;")
        subjects = ri.split_subjects(rec)
        self.assertEqual(subjects, ["Ann: ships the eval", "Zelin: reads the papers"])
        self.assertTrue(all(subject.strip() for subject in subjects))


class DeadlineQuestionTestCase(unittest.TestCase):
    """截止那一条只在上一版真记了日期时才问（填充值不算）。"""

    def test_the_sendable_shape_asks_about_a_real_deadline(self):
        ids = [q["id"] for q in ri.derive(sections_record(["2026-09-30"]))]
        self.assertIn(ri.DEADLINE_ID, ids)
        asked = [q for q in ri.derive(sections_record(["2026-09-30"])) if q["id"] == ri.DEADLINE_ID]
        self.assertEqual(asked[0]["options"], ["keep", "drop"])

    def test_a_filler_deadline_section_is_not_asked_about(self):
        for items in (["none set"], ["未定"], [], ["  "]):
            with self.subTest(items=items):
                ids = [q["id"] for q in ri.derive(sections_record(items))]
                self.assertNotIn(ri.DEADLINE_ID, ids)


class AnswerIdTestCase(unittest.TestCase):
    """id 认不出 = None；wire 上什么都可能来，一条也不许把这一步崩掉。"""

    def test_a_non_string_id_is_unrecognised_not_a_crash(self):
        for junk in (None, 42, 3.5, [], {}, b"s1", ("split1",)):
            with self.subTest(junk=junk):
                self.assertIsNone(ri.kind_of(junk))
                self.assertEqual(ri.item_tag(junk), "")

    def test_the_two_shapes_that_are_recognised(self):
        self.assertEqual(ri.kind_of("split1"), ri.KIND_SPLIT)
        self.assertEqual(ri.kind_of("s2"), ri.KIND_ITEM)      # §63.12 标签形
        self.assertIsNone(ri.kind_of("split7"))               # 帽之外的号认不出


class AnswerSheetGateTestCase(unittest.TestCase):
    """答案闸：全有或全无，且**问出来的每一条都答得上去**。"""

    def crowded(self) -> dict:
        return lines_record(split="Split: A: one; B: two; C: three; D: four; E: five; F: six; G: seven")

    def test_the_most_questions_a_recap_can_ask_all_fit_in_one_answer_sheet(self):
        questions = ri.derive(self.crowded(), has_priors=True)
        self.assertEqual(len(questions), 12)                  # 6 逐条 + 截止 + 5 个全局
        sheet = ["%s=%s" % (q["id"], q["options"][0]) for q in questions]
        self.assertIs(ri.answers_ok(sheet), True)             # 上限与问题数恰好相等
        self.assertEqual(ri.clean_answers(sheet), sheet)

    def test_one_answer_past_the_ceiling_is_the_whole_sheet_being_malformed(self):
        questions = ri.derive(self.crowded(), has_priors=True)
        sheet = ["%s=%s" % (q["id"], q["options"][0]) for q in questions] + ["s1=drop"]
        self.assertEqual(len(sheet), 13)
        self.assertIs(ri.answers_ok(sheet), False)
        self.assertEqual(ri.clean_answers(sheet), [])

    def test_the_gate_answers_a_real_bool(self):
        # 答 None 就是把「判不了」和「判成假」混成一件事——公开谓词不许含糊
        for bad in (None, "split1=drop", {"split1": "drop"}, [], ["nonsense"],
                    ["split1=drop", "split1=keep"], ["split1=explode"]):
            with self.subTest(bad=bad):
                self.assertIs(ri.answers_ok(bad), False)
        self.assertIs(ri.answers_ok(["split1=drop"]), True)


class BaselineBlockTestCase(unittest.TestCase):
    """答案指的那一版：编号表从 1 起，上一版正文真的在块里（宪法第 5 条的那条连线）。"""

    def test_the_numbers_in_the_fence_are_the_numbers_the_instructions_name(self):
        subjects = ["Ann ships the eval", "Zelin reads the papers", "Bob books the room"]
        rows = ri.baseline_block(subjects, "").splitlines()
        self.assertEqual(rows[1:], ["1. Ann ships the eval", "2. Zelin reads the papers",
                                    "3. Bob books the room"])
        # `Item 3` 指的就是上面第 3 行——指令块里一个字的正文都没有
        instructions = ri.prompt_block(["split3=drop"])
        self.assertIn("Item 3:", instructions)
        self.assertNotIn("Bob books the room", instructions)

    def test_the_previous_version_rides_in_the_block_with_the_numbered_items(self):
        body = "Decided: the run moves to Monday\nOpen: none"
        block = ri.baseline_block(["Ann ships the eval"], body)
        self.assertIn("1. Ann ships the eval", block)
        self.assertIn(body, block)                            # 上一版正文原样在块里
        self.assertIn(body, ri.baseline_block([], body))      # 一条分工都没有时也在
        self.assertIsNone(ri.baseline_block([], "   "))       # 两样都没有 = 这一段不进 prompt


if __name__ == "__main__":
    unittest.main()
