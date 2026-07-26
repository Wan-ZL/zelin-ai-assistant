"""§45 来源角色决策表 — act/lib/provenance.py 的宪法级性质.

两种钉法，各司其职：

- **穷举证明**（有限域上 == Z3 的可满足性检查，零依赖）：TABLE 恰好覆盖
  全部 (provenance × speaker) 组合、每格取值合法——完备且无矛盾；
- **Hypothesis 性质测试**（装了才跑，CI ubuntu 腿装）：对任意垃圾输入，
  verdict 全函数、永不 raise；「screen 永远 CORROBORATE」「assistant 的话
  永远到不了 FULL」「FULL 只可能出自 audio」三条宪法性质在随机攻击下成立。

改 TABLE = 修法：这里挂红说明改动打破了哪条性质——要么改回来，要么显式
修宪（同步 CONTRACT §45 誊本 + 这里的性质）。
"""
import itertools
import unittest

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act.lib import provenance

try:
    from hypothesis import given, strategies as st
    HAS_HYPOTHESIS = True
except ImportError:  # pragma: no cover - optional dev dep, CI installs it
    HAS_HYPOTHESIS = False


class TableExhaustiveProofTestCase(unittest.TestCase):
    """有限域穷举 = 完备性/一致性的机器证明（Z3 的角色，免费版）。"""

    def test_table_covers_every_combination_exactly_once(self):
        expected = set(itertools.product(provenance.PROVENANCES,
                                         provenance.SPEAKERS))
        self.assertEqual(set(provenance.TABLE), expected)  # 无漏行、无野行
        # dict 键唯一性天然保证「无矛盾」；再钉每格取值合法。
        for key, value in provenance.TABLE.items():
            self.assertIn(value, provenance.VERDICTS, f"illegal verdict at {key}")

    def test_screen_row_is_all_corroborate(self):
        # Zelin 2026-07-25 拍板的那一刀，原文钉死：屏幕不发起卡片。
        for speaker in provenance.SPEAKERS:
            self.assertEqual(provenance.TABLE[("screen", speaker)],
                             provenance.CORROBORATE)

    def test_assistant_never_reaches_full(self):
        for prov in provenance.PROVENANCES:
            self.assertNotEqual(provenance.TABLE[(prov, "assistant")],
                                provenance.FULL)

    def test_full_only_lives_in_the_audio_row(self):
        for (prov, _spk), v in provenance.TABLE.items():
            if v == provenance.FULL:
                self.assertEqual(prov, "audio")


class NormalizeAndTotalityTestCase(unittest.TestCase):
    def test_normalize_accepts_case_and_whitespace(self):
        self.assertEqual(
            provenance.normalize(" Screen ", provenance.PROVENANCES), "screen")
        self.assertEqual(
            provenance.normalize("AUDIO", provenance.PROVENANCES), "audio")

    def test_normalize_garbage_falls_to_unknown(self):
        for garbage in (None, 123, [], {}, True, "definitely-not-a-value", ""):
            self.assertEqual(
                provenance.normalize(garbage, provenance.PROVENANCES), "unknown")

    def test_verdict_is_total_on_garbage(self):
        for p in (None, 42, "SCREEN", "screeen", [], "audio "):
            for s in (None, 3.14, "HUMAN", {}, "assistant", "宇宙"):
                self.assertIn(provenance.verdict(p, s), provenance.VERDICTS)

    def test_missing_fields_mean_limited_not_full(self):
        # 旧提取器输出（无 provenance/speaker）落 unknown×unknown = LIMITED：
        # 安静的安全网，绝不是 FULL。
        self.assertEqual(provenance.verdict(None, None), provenance.LIMITED)


if HAS_HYPOTHESIS:
    # 整个类须在 if 内定义：@given 在类定义期求值，skipUnless 救不了缺库
    # 的 import 现场（本地无 hypothesis 时 unittest 收集会直接 NameError）。
    class ConstitutionalPropertyTestCase(unittest.TestCase):
        """随机攻击下的宪法条款——Hypothesis 生成上千组合找反例。"""

        @given(st.one_of(st.none(), st.text(), st.integers(), st.booleans()),
               st.one_of(st.none(), st.text(), st.integers(), st.booleans()))
        def test_verdict_never_raises_and_stays_in_domain(self, p, s):
            self.assertIn(provenance.verdict(p, s), provenance.VERDICTS)

        @given(st.one_of(st.none(), st.text(), st.integers()))
        def test_screen_never_originates_whatever_the_speaker(self, s):
            self.assertEqual(provenance.verdict("screen", s),
                             provenance.CORROBORATE)

        @given(st.one_of(st.none(), st.text(), st.integers()))
        def test_assistant_speech_never_reaches_full(self, p):
            self.assertNotEqual(provenance.verdict(p, "assistant"),
                                provenance.FULL)

        @given(st.one_of(st.none(), st.text(), st.integers()),
               st.one_of(st.none(), st.text(), st.integers()))
        def test_full_implies_audio(self, p, s):
            if provenance.verdict(p, s) == provenance.FULL:
                self.assertEqual(
                    provenance.normalize(p, provenance.PROVENANCES), "audio")


if __name__ == "__main__":
    unittest.main()
