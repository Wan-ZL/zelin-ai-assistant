"""match_corpus (§38) — the deterministic matching corpus behind 少建卡.

Pins: the SearchMatch.normalize python-twin semantics (separator-free latin,
CJK pass-through — §37 sibling contract), tokenization, alias derivation
determinism, and the score_pair thresholds auto_merge relies on.
"""
import unittest

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act.lib import match_corpus
from act.lib.registry import Requirement


class NormalizeTwinTestCase(unittest.TestCase):
    """§37 sibling: same rules as shared/Sources/SearchMatch.swift."""

    def test_strips_separators_and_whitespace(self):
        self.assertEqual(match_corpus.normalize("EB-1A"), "eb1a")
        self.assertEqual(match_corpus.normalize("H_1 B."), "h1b")
        self.assertEqual(match_corpus.normalize("v0.33.1"), "v0331")

    def test_substring_semantics_match_searchmatch(self):
        # "eb1" finds "EB-1A"; "eb2" must NOT
        hay = match_corpus.normalize("EB-1A 推荐信")
        self.assertIn(match_corpus.normalize("eb1"), hay)
        self.assertNotIn(match_corpus.normalize("eb2"), hay)

    def test_cjk_passes_through(self):
        self.assertEqual(match_corpus.normalize("推荐 信"), "推荐信")

    def test_non_str_is_safe(self):
        self.assertEqual(match_corpus.normalize(None), "")
        self.assertEqual(match_corpus.normalize(123), "123")


class TokensTestCase(unittest.TestCase):
    def test_latin_runs_normalize_as_one_token(self):
        ts = match_corpus.tokens("EB-1A petition for WeGreened")
        self.assertIn("eb1a", ts)
        self.assertIn("petition", ts)
        self.assertIn("wegreened", ts)

    def test_stopwords_and_short_digits_drop(self):
        ts = match_corpus.tokens("the new update for 12 things in 2026")
        self.assertNotIn("the", ts)
        self.assertNotIn("new", ts)
        self.assertNotIn("12", ts)
        self.assertIn("2026", ts)          # 4+ digit ids/years stay
        self.assertIn("things", ts)

    def test_cjk_short_run_is_the_word_long_run_bigrams(self):
        ts = match_corpus.tokens("推荐信")
        self.assertIn("推荐信", ts)          # ≤4 chars: kept whole
        ts2 = match_corpus.tokens("推荐信清单整理")
        self.assertIn("推荐", ts2)           # >4 chars: bigrams
        self.assertIn("清单", ts2)

    def test_url_yields_matchable_tokens(self):
        ts = match_corpus.tokens("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        self.assertIn("dqw4w9wgxcq", ts)
        self.assertIn("youtube", ts)


class AliasesTestCase(unittest.TestCase):
    def _req(self, **kw):
        base = dict(id="R-001", title="t", status="card_sent")
        base.update(kw)
        return Requirement(**base)

    def test_aliases_from_summary_never_notes_or_quotes(self):
        # review blocker 3: notes/source quotes carry untrusted third-party
        # text and pasted secrets — alias mining must not touch them.
        r = self._req(
            title="https://example.com/threads/12345",
            summary="给 Quinton 开通 PRD permissions",
            sources=[{"who": "quinton", "channel": "slack",
                      "date": "2026-07-01",
                      "quote": "quoteonlyword should never surface"}],
            notes="[radar] noteonlyword neither")
        aliases = match_corpus.derive_aliases(r)
        self.assertLessEqual(len(aliases), match_corpus.MAX_ALIASES)
        self.assertIn("permissions", aliases)
        self.assertIn("quinton", aliases)
        self.assertNotIn("quoteonlyword", aliases)
        self.assertNotIn("noteonlyword", aliases)
        # tokens already inside the (normalized) title are skipped
        self.assertNotIn("12345", aliases)
        self.assertNotIn("example", aliases)

    def test_deterministic_and_rarity_ranked(self):
        r1 = self._req(id="R-001", summary="fix login oauth bug")
        r2 = self._req(id="R-002", summary="fix signup oauth flow")
        sets = [match_corpus.corpus_tokens(x) for x in (r1, r2)]
        freq = match_corpus.doc_frequencies(sets)
        a1 = match_corpus.derive_aliases(r1, freq)
        # rare-first: "login" (1 card) ranks before "oauth" (2 cards)
        self.assertLess(a1.index("login"), a1.index("oauth"))
        self.assertEqual(a1, match_corpus.derive_aliases(r1, freq))  # stable


class CjkStopAndEvidenceTestCase(unittest.TestCase):
    """Review blocker 2: zh function words must never be match evidence."""

    def test_particles_and_politeness_never_tokenize(self):
        ts = match_corpus.tokens("帮我看一下这个，麻烦了，谢谢")
        for stop in ("帮我", "我看", "一下", "这个", "麻烦", "谢谢"):
            self.assertNotIn(stop, ts)

    def test_two_different_asks_from_particles_share_nothing(self):
        # the reproduced false positive: same colleague, two different asks
        a = match_corpus.tokens("帮我看一下报销流程")
        b = match_corpus.tokens("帮我看简历")
        self.assertEqual(a & b, set())

    def test_weak_grams_score_but_never_count(self):
        # 2-char CJK grams are context, not identity
        strong = match_corpus.strong_evidence(["报销", "流程", "taxform", "报税材料"])
        self.assertEqual(strong, ["taxform", "报税材料"])
        self.assertTrue(match_corpus.is_weak_gram("权限"))
        self.assertFalse(match_corpus.is_weak_gram("推荐信"))
        self.assertFalse(match_corpus.is_weak_gram("ab"))   # latin len-2 is not a CJK gram

    def test_scrub_mask_is_not_evidence(self):
        # two cards each holding a (different) masked secret must not match
        # on the mask text itself
        self.assertNotIn("脱敏", match_corpus.tokens("key 是 [脱敏] 那个"))


class PrivacyScrubTestCase(unittest.TestCase):
    """Review blocker 3: secrets in notes/quotes must never reach any prompt
    surface — normalize() strips the separators the runner-side scrub anchors
    on, so masking happens BEFORE tokenizing."""

    _SK = "sk-abcdefghijklmnop12345678"
    _GHP = "ghp_abcdefghijklmnopqrstuvwxyz123456"
    _PHONE = "+1-415-555-0123"

    def _req(self):
        return Requirement(
            id="R-001", title="正常标题", status="card_sent",
            summary="正常 summary keyword",
            sources=[{"who": "x", "channel": "slack", "date": "2026-07-01",
                      "quote": f"my key is {self._SK} and pat {self._GHP}"}],
            notes=f"[radar] call {self._PHONE} token {self._SK}")

    def test_corpus_tokens_carry_no_secret_fragments(self):
        toks = match_corpus.corpus_tokens(self._req())
        joined = " ".join(toks)
        self.assertNotIn("abcdefghijklmnop", joined)   # sk- body
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz", joined)  # ghp body
        self.assertNotIn("skabcdefghijklmnop", joined)  # normalized laundering

    def test_aliases_carry_no_secret_or_phone_fragments(self):
        aliases = match_corpus.derive_aliases(self._req())
        joined = " ".join(aliases)
        for frag in ("abcdefghijklmnop", "ghp", "4155550123", "415", "0123"):
            self.assertNotIn(frag, joined)

    def test_phone_shaped_digit_runs_never_display(self):
        self.assertEqual(match_corpus.display_tokens(["14155550123", "burton"]),
                         ["burton"])
        # short numeric ids (years, small counters) still display
        self.assertEqual(match_corpus.display_tokens(["2026"]), ["2026"])


class ScorePairTestCase(unittest.TestCase):
    def test_single_short_shared_token_is_no_signal(self):
        s, m = match_corpus.score_pair({"abc", "def"}, {"abc", "xyz"})
        self.assertEqual((s, m), (0.0, []))

    def test_single_long_shared_token_is_strong(self):
        s, m = match_corpus.score_pair({"dqw4w9wgxcq", "def"},
                                       {"dqw4w9wgxcq", "xyz"})
        self.assertGreater(s, 0)
        self.assertEqual(m, ["dqw4w9wgxcq"])

    def test_overlap_coefficient(self):
        a = {"推荐", "荐信", "清单", "整理"}
        b = {"推荐", "荐信", "清单", "别的", "东西", "很多"}
        s, m = match_corpus.score_pair(a, b)
        self.assertAlmostEqual(s, 3 / 4)
        self.assertEqual(set(m), {"推荐", "荐信", "清单"})

    def test_empty_sets_zero(self):
        self.assertEqual(match_corpus.score_pair(set(), {"a"}), (0.0, []))


class RankCandidatesTestCase(unittest.TestCase):
    def _req(self, rid, summary):
        return Requirement(id=rid, title=rid, status="card_sent", summary=summary)

    def test_top3_best_first_and_threshold(self):
        reqs = [
            self._req("R-001", "整理 EB-1A 推荐信清单 wegreened"),
            self._req("R-002", "修复 login oauth bug"),
            self._req("R-003", "EB-1A 推荐信初稿"),
            self._req("R-004", "完全无关的另一件事"),
        ]
        ranked = match_corpus.rank_candidates("EB-1A 推荐信 进展：wegreened 回了", reqs)
        ids = [r.id for r, _s, _m in ranked]
        self.assertLessEqual(len(ids), 3)
        self.assertIn("R-001", ids)
        self.assertIn("R-003", ids)
        self.assertNotIn("R-002", ids)
        self.assertNotIn("R-004", ids)

    def test_empty_incoming_is_empty(self):
        self.assertEqual(match_corpus.rank_candidates("", [self._req("R-001", "x")]), [])
