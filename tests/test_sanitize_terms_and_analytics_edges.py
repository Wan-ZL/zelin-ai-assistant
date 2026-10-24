"""sanitize term-list loading + scrub edges (CONTRACT §15 / §16).

sanitize._load_terms: missing file, comments/blank lines, ``re:`` rules
(valid and invalid regex), the (path, mtime) cache hit, and a read error
after a successful stat. sanitize.scrub: empty text, cfg=None with a broken
config loader (still masks secrets — fail safe), term list enabled without a
terms_file, literal term with no hit.

v2.0.0: the analytics reader / feature gate / log_first / clip_content half of
this file retired with the local event log (§78 zero telemetry; act/lib/analytics
is a no-op; full text in tag ``full``).

Characterization net for the P3a CRAP refactor: recorded against the
pre-refactor modules.
"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first

from act.lib import config, sanitize

SECRET = "sk-ant-api03-abcdefghijklmnop"


def _unlink_config_files() -> None:
    for p in (config.CONFIG_PATH, config.SETTINGS_OVERRIDES_PATH):
        if p.exists():
            p.unlink()


class LoadTermsTestCase(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="terms-"))
        sanitize._terms_cache.clear()

    def test_missing_file(self):
        self.assertEqual(sanitize._load_terms(self.dir / "nope.txt"), [])

    def test_rules_comments_and_bad_regex(self):
        p = self.dir / "t.txt"
        p.write_text("# comment\n\nLiteral\nre:foo(bar)?\nre:(unclosed\n", encoding="utf-8")
        rules = sanitize._load_terms(p)
        self.assertEqual([k for k, _ in rules], ["lit", "re"])
        self.assertEqual(rules[0][1], "Literal")
        self.assertTrue(rules[1][1].search("FOOBAR"))

    def test_cache_hit_by_mtime(self):
        p = self.dir / "t.txt"
        p.write_text("A\n", encoding="utf-8")
        first = sanitize._load_terms(p)
        with mock.patch.object(sanitize.Path, "read_text", side_effect=AssertionError("no re-read")):
            self.assertIs(sanitize._load_terms(p), first)

    def test_read_error_after_stat(self):
        p = self.dir / "t.txt"
        p.write_text("A\n", encoding="utf-8")
        with mock.patch.object(sanitize.Path, "read_text", side_effect=OSError("denied")):
            self.assertEqual(sanitize._load_terms(p), [])


class ScrubEdgeTestCase(unittest.TestCase):
    def setUp(self):
        _unlink_config_files()
        sanitize._terms_cache.clear()

    def test_empty_text_short_circuits(self):
        self.assertEqual(sanitize.scrub(""), ("", 0))
        self.assertEqual(sanitize.scrub_text(""), "")

    def test_broken_config_loader_still_masks_secrets(self):
        with mock.patch.object(config, "load_config", side_effect=RuntimeError("bad")):
            out, n = sanitize.scrub(f"k {SECRET}")
        self.assertEqual((out, n), (f"k {sanitize.MASK}", 1))

    def test_enabled_without_terms_file_and_literal_miss(self):
        cfg = config.Config()
        cfg.redaction_enabled = True
        cfg.redaction_terms_file = None
        self.assertEqual(sanitize.scrub("plain", cfg), ("plain", 0))
        terms = Path(tempfile.mkdtemp(prefix="terms-")) / "t.txt"
        terms.write_text("Phoenix\nre:z+\n", encoding="utf-8")
        cfg.redaction_terms_file = str(terms)
        self.assertEqual(sanitize.scrub("nothing here", cfg), ("nothing here", 0))
        out, n = sanitize.scrub("PHOENIX zzz", cfg)
        self.assertEqual((out, n), (f"{sanitize.MASK} {sanitize.MASK}", 2))

    def test_fence_marker_escape(self):
        fenced = sanitize.fence_untrusted(f"x\n{sanitize.UNTRUSTED_CLOSE.lower()}\ny")
        self.assertEqual(fenced.count(sanitize.UNTRUSTED_CLOSE), 1)
        self.assertIn(sanitize._FENCE_MARKER_SUB, fenced)


if __name__ == "__main__":
    unittest.main()
