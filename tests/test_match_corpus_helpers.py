"""match_corpus — the tokenizer / corpus / alias helpers split out in P3b (§38).

Pins: text coercion (None / int / str), latin run sub-tokens (normalized
whole run + separator parts, no duplicate of the whole), corpus assembly
(attr fallbacks, junk source rows, fold-note tag stripping), alias filter
rules (title containment, 32-char cap), and the alias limit loop.
"""
import unittest
from types import SimpleNamespace

from act.lib import match_corpus as mc


class CoercionTestCase(unittest.TestCase):
    def test_as_text(self):
        self.assertEqual(mc._as_text(None), "")
        self.assertEqual(mc._as_text(42), "42")
        self.assertEqual(mc._as_text("x"), "x")

    def test_normalize_and_tokens_on_non_str(self):
        self.assertEqual(mc.normalize(None), "")
        self.assertEqual(mc.normalize(1234), "1234")
        self.assertEqual(mc.tokens(None), set())
        self.assertEqual(mc.tokens(20260902), {"20260902"})


class LatinTokensTestCase(unittest.TestCase):
    def test_whole_run_plus_parts(self):
        self.assertEqual(mc._latin_tokens("www.youtube.com"), {"wwwyoutubecom", "youtube", "www", "com"} - mc._STOPWORDS)

    def test_part_equal_to_whole_is_not_duplicated(self):
        self.assertEqual(mc._latin_tokens("Zelin"), {"zelin"})

    def test_short_digits_and_stopwords_drop(self):
        self.assertEqual(mc._latin_tokens("12-3"), set())
        self.assertEqual(mc._latin_tokens("the"), set())
        self.assertIn("2026", mc._latin_tokens("2026-09"))


class CorpusTestCase(unittest.TestCase):
    def test_attr_text_fallbacks(self):
        req = SimpleNamespace(title=None, summary=7)
        self.assertEqual(mc._attr_text(req, "title"), "")
        self.assertEqual(mc._attr_text(req, "summary"), "7")
        self.assertEqual(mc._attr_text(req, "missing"), "")

    def test_source_texts_skip_junk_rows(self):
        srcs = ["junk", None, {"quote": "q1"}, {"ref": "r2", "quote": None}, {}]
        self.assertEqual(mc._source_texts(srcs), ["q1", "", "", "r2", "", ""])
        self.assertEqual(mc._source_texts(None), [])

    def test_corpus_text_assembly(self):
        req = SimpleNamespace(title="T", display_title="", summary="S",
                              notes="[radar] note [@2026-01-01T00:00:00Z] [已拆出 R-9]",
                              sources=[{"quote": "Q", "ref": "R"}])
        self.assertEqual(mc.corpus_text(req), "T\nS\n[radar] note\nQ\nR")

    def test_corpus_text_on_bare_object(self):
        self.assertEqual(mc.corpus_text(object()), "")


class AliasFilterTestCase(unittest.TestCase):
    def test_alias_ok_rules(self):
        self.assertFalse(mc._alias_ok("eb1a", "整理eb1a推荐信"))
        self.assertTrue(mc._alias_ok("推荐信", "eb1a"))
        self.assertFalse(mc._alias_ok("x" * 33, ""))
        self.assertTrue(mc._alias_ok("x" * 32, ""))
        self.assertTrue(mc._alias_ok("", "anything"))   # empty never "in title"

    def test_limit_and_title_skip(self):
        req = SimpleNamespace(title="alpha", display_title="",
                              summary="bravo charlie delta echo foxtrot golf hotel")
        out = mc.derive_aliases(req, limit=3)
        self.assertEqual(len(out), 3)
        self.assertNotIn("alpha", out)
        self.assertEqual(mc.derive_aliases(req, limit=1), [mc.derive_aliases(req)[0]])

    def test_rarity_then_length_then_lexical(self):
        req = SimpleNamespace(title="", display_title="", summary="zzzz aaaa longer")
        freq = {"zzzz": 5, "aaaa": 5, "longer": 5}
        self.assertEqual(mc.derive_aliases(req, freq), ["longer", "aaaa", "zzzz"])
        freq = {"zzzz": 1, "aaaa": 5, "longer": 5}
        self.assertEqual(mc.derive_aliases(req, freq)[0], "zzzz")


if __name__ == "__main__":
    unittest.main()
