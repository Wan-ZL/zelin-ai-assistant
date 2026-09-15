"""§63.3 追记 (issue #298): a length-only recap failure is trimmed back to the cap
instead of landing as 需复核, and every trim is on the record.

`recap_text.repair_lengths` is deliberately narrow and all-or-nothing: it fires
only when every remaining finding is `line_too_long` and every line reaches its
cap by DELETING at most MAX_TRIM_EN / MAX_TRIM_ZH characters (the budget bounds
the cut, not the overrun — an EN line goes back by whole words); it never eats a
label or drops a line below MIN_BODY_CHARS; and it reports one
`{lang, line, over, removed}` row per trimmed line so the panel can never show
silently edited text, nor understate the cut. The structured findings behind a
real 需复核 (`validate_detail`) are what the record persists.
"""
import unittest

from tests import TMP_HOME  # noqa: F401 - sandbox env first
from tests import recap_fixture as fx

from act.lib import recap_text as rt


def _clean():
    return rt.parse_output(fx.good_output())


def _over(recap: dict, lang: str, line: int) -> int:
    limit = rt.MAX_CHARS_EN if lang == "en" else rt.MAX_CHARS_ZH
    return len(recap[lang][line - 1]) - limit


class RepairLengthsTestCase(unittest.TestCase):
    def test_a_six_character_overrun_is_trimmed_and_reported(self):
        # 真实那一例（#298）：英文第一行 146 字符，别处全干净
        rec = _clean()
        rec["en"][0] += " and the evaluation harness is reused verbatim for the next weekly training reviews"
        self.assertEqual((len(rec["en"][0]), _over(rec, "en", 1)), (146, 6))
        fixed, repairs = rt.repair_lengths(rec)
        self.assertEqual(rt.validate(fixed), [])
        self.assertEqual(repairs, [{"lang": "en", "line": 1, "over": 6, "removed": 8}])
        self.assertLessEqual(len(fixed["en"][0]), rt.MAX_CHARS_EN)
        self.assertTrue(fixed["en"][0].startswith(rt.LABELS_EN[0]))
        self.assertEqual(fixed["en"][1:], rec["en"][1:])         # 别的行一字不动
        self.assertEqual(fixed["zh"], rec["zh"])

    def test_the_cut_falls_on_a_word_boundary_and_leaves_no_dangling_punctuation(self):
        rec = _clean()
        rec["en"][4] = "Open: " + " ".join(["alpha"] * 20) + ", whether the second cluster is funded"
        fixed, repairs = rt.repair_lengths(rec)
        self.assertEqual(len(repairs), 1)
        self.assertIn(fixed["en"][4], rec["en"][4])               # 只从行尾剪，前面逐字保留
        self.assertNotIn("  ", fixed["en"][4])
        self.assertFalse(fixed["en"][4].endswith(","))
        self.assertTrue(rec["en"][4].startswith(fixed["en"][4].split(",")[0]))

    def test_chinese_trims_by_characters(self):
        rec = _clean()
        rec["zh"][2] = "截止：" + "下周五交第一版评测报告，" * 5 + "下周五交第一版评"
        over = _over(rec, "zh", 3)
        self.assertEqual(over, 11)                                # ≤ MAX_TRIM_ZH
        fixed, repairs = rt.repair_lengths(rec)
        self.assertEqual(repairs, [{"lang": "zh", "line": 3, "over": over, "removed": over}])
        self.assertEqual(len(fixed["zh"][2]), rt.MAX_CHARS_ZH)    # 中文按字剪，不找词边界
        self.assertTrue(rec["zh"][2].startswith(fixed["zh"][2]))  # 只从行尾剪
        self.assertTrue(fixed["zh"][2].startswith(rt.LABELS_ZH[2]))
        self.assertEqual(rt.validate(fixed), [])

    def test_both_languages_over_in_one_pass(self):
        rec = _clean()
        rec["en"][1] = "Split: " + " ".join(["owner-item"] * 14)
        rec["zh"][1] = "分工：" + "各自负责一块，" * 9
        fixed, repairs = rt.repair_lengths(rec)
        self.assertEqual([(r["lang"], r["line"]) for r in repairs], [("en", 2), ("zh", 2)])
        self.assertEqual(rt.validate(fixed), [])

    def test_an_overrun_past_the_budget_is_not_repaired(self):
        rec = _clean()
        rec["en"][0] = "Decided: " + " ".join(["alpha"] * 28)      # 176 字符 = 超 36
        self.assertGreater(_over(rec, "en", 1), rt.MAX_TRIM_EN)
        fixed, repairs = rt.repair_lengths(rec)
        self.assertEqual(repairs, [])
        self.assertEqual(fixed["en"], rec["en"])                  # 原样退回，交给人
        self.assertTrue(rt.validate(fixed))

    def test_any_other_violation_blocks_the_whole_repair(self):
        for lang, line, patch in (("en", 1, "Decided: the run moves as Arash said"),
                                  ("en", 3, "Deadline: 12:30 on Friday"),
                                  ("zh", 1, "定了：他提到训练要改")):
            with self.subTest(patch=patch):
                rec = _clean()
                rec[lang][line - 1] = patch
                rec["en"][4] = "Open: " + " ".join(["alpha"] * 26)    # 161 字符 = 超 21
                self.assertTrue(0 < _over(rec, "en", 5) <= rt.MAX_TRIM_EN)
                fixed, repairs = rt.repair_lengths(rec)
                self.assertEqual(repairs, [])
                self.assertEqual(fixed["en"][4], rec["en"][4])    # 长的那行也不剪：全有或全无

    def test_a_small_overrun_behind_a_long_trailing_token_is_not_repaired(self):
        # 预算量的是**删掉的量**：超出 3 个字符，但唯一的词边界切口要丢掉 39 个字符
        # （一整句话），那不是「一次格式手滑」——原样退回交给人
        rec = _clean()
        rec["en"][0] = "Decided: " + "ok " * 32 + "irreversible_commitment_to_the_new_mix"
        self.assertEqual((len(rec["en"][0]), _over(rec, "en", 1)), (143, 3))
        fixed, repairs = rt.repair_lengths(rec)
        self.assertEqual((repairs, fixed["en"][0]), ([], rec["en"][0]))
        self.assertTrue(rt.validate(fixed))                       # 走 needs_review + 逐行原因

    def test_every_receipt_states_how_many_characters_were_removed(self):
        rec = _clean()
        rec["en"][4] = "Open: " + " ".join(["alpha"] * 20) + ", whether the second cluster is funded"
        fixed, repairs = rt.repair_lengths(rec)
        removed = len(rec["en"][4]) - len(fixed["en"][4])
        self.assertEqual(repairs[0]["removed"], removed)
        self.assertGreater(removed, repairs[0]["over"])            # 词边界回退删得比超出量多
        self.assertLessEqual(removed, rt.MAX_TRIM_EN)              # 且从不超过预算

    def test_a_line_that_cannot_lose_the_characters_is_left_alone(self):
        # 标签之后只剩一个巨长 token：剪了就只剩标签，不许剪
        rec = _clean()
        rec["en"][0] = "Decided: " + "x" * (rt.MAX_CHARS_EN - 9 + 5)
        self.assertTrue(0 < _over(rec, "en", 1) <= rt.MAX_TRIM_EN)
        fixed, repairs = rt.repair_lengths(rec)
        self.assertEqual((repairs, fixed["en"][0]), ([], rec["en"][0]))

    def test_a_clean_recap_is_untouched(self):
        rec = _clean()
        fixed, repairs = rt.repair_lengths(rec)
        self.assertEqual((fixed, repairs), (rec, []))

    def test_repair_is_idempotent(self):
        rec = _clean()
        rec["en"][0] += " and the evaluation harness is reused verbatim for the next weekly training reviews"
        once, first = rt.repair_lengths(rec)
        twice, second = rt.repair_lengths(once)
        self.assertEqual((twice, second), (once, []))
        self.assertEqual(len(first), 1)


class ValidateDetailTestCase(unittest.TestCase):
    def test_findings_carry_the_code_the_language_the_line_and_the_overrun(self):
        rec = _clean()
        rec["en"][0] = "Open: " + "x" * 150
        rec["zh"][4] = "待定：见 https://example.com"
        findings = rt.validate_detail(rec)
        by_code = {f["code"]: f for f in findings}
        self.assertEqual(set(by_code), {"label_mismatch", "line_too_long", "link"})
        self.assertEqual((by_code["line_too_long"]["lang"], by_code["line_too_long"]["line"],
                          by_code["line_too_long"]["limit"], by_code["line_too_long"]["over"]),
                         ("en", 1, rt.MAX_CHARS_EN, 156 - rt.MAX_CHARS_EN))
        self.assertEqual(by_code["label_mismatch"]["line"], 1)
        # 整语言级的禁项没有行号（joined 扫描），行号必须允许 null
        self.assertEqual((by_code["link"]["lang"], by_code["link"]["line"]), ("zh", None))

    def test_no_finding_ever_carries_recap_text(self):
        rec = _clean()
        rec["en"][0] = "Decided: the secret budget number is 41 as Arash said, see https://x.test"
        rec["zh"][0] = "定了：他提到预算要改，见 https://x.test"
        blob = repr(rt.validate_detail(rec))
        for leak in ("secret budget", "Arash", "x.test", "预算"):
            self.assertNotIn(leak, blob)


if __name__ == "__main__":
    unittest.main()
