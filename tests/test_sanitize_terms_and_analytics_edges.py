"""sanitize term-list loading + analytics reader/gate edges (CONTRACT §15 / §16).

sanitize._load_terms: missing file, comments/blank lines, ``re:`` rules
(valid and invalid regex), the (path, mtime) cache hit, and a read error
after a successful stat. sanitize.scrub: empty text, cfg=None with a broken
config loader (still masks secrets — fail safe), term list enabled without a
terms_file, literal term with no hit, analytics failure swallowed.

analytics: read_events (missing file / corrupt line / bad ts under ``since``
/ older-than-since filtered), feature_gate with a raising cfg, the gate
cache hit, log_first (marker present / log_event False / exception),
clip_content fail-closed when masking itself raises, and the
_config_sources_intact / feature_gate_fresh corruption shapes that were not
yet pinned (yaml None, non-dict yaml, non-dict overrides, flat key).

Characterization net for the P3a CRAP refactor: recorded against the
pre-refactor modules.
"""
import datetime as _dt
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first

from act.lib import analytics, config, sanitize

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
        # a scrub under a broken config loader caches feature_gate=False for
        # GATE_TTL — never let that leak into the next test module
        analytics.reset_feature_gate_cache()
        self.addCleanup(analytics.reset_feature_gate_cache)

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

    def test_analytics_failure_is_swallowed(self):
        cfg = config.Config()
        with mock.patch.object(analytics, "log_event", side_effect=RuntimeError("io")):
            out, n = sanitize.scrub(f"k {SECRET}", cfg)
        self.assertEqual(n, 1)

    def test_fence_marker_escape(self):
        fenced = sanitize.fence_untrusted(f"x\n{sanitize.UNTRUSTED_CLOSE.lower()}\ny")
        self.assertEqual(fenced.count(sanitize.UNTRUSTED_CLOSE), 1)
        self.assertIn(sanitize._FENCE_MARKER_SUB, fenced)


class ReadEventsTestCase(unittest.TestCase):
    def setUp(self):
        d = Path(tempfile.mkdtemp(prefix="ana-"))
        for attr, val in (("ANALYTICS_DIR", d), ("EVENTS_PATH", d / "events.jsonl")):
            p = mock.patch.object(analytics, attr, val)
            p.start()
            self.addCleanup(p.stop)

    def test_missing_file_yields_nothing(self):
        self.assertEqual(list(analytics.read_events()), [])

    def test_filters(self):
        rows = [
            "{corrupt",
            json.dumps({"ts": "bad", "event": "a"}),
            json.dumps({"ts": "2026-01-01T00:00:00Z", "event": "old"}),
            json.dumps({"ts": "2026-09-01T00:00:00Z", "event": "new"}),
            json.dumps({"event": "no-ts"}),
        ]
        analytics.EVENTS_PATH.write_text("\n".join(rows) + "\n", encoding="utf-8")
        self.assertEqual([e["event"] for e in analytics.read_events()],
                         ["a", "old", "new", "no-ts"])
        since = _dt.datetime(2026, 6, 1, tzinfo=_dt.timezone.utc)
        self.assertEqual([e["event"] for e in analytics.read_events(since=since)], ["new"])


class GateEdgeTestCase(unittest.TestCase):
    def setUp(self):
        _unlink_config_files()
        self.addCleanup(_unlink_config_files)
        analytics.reset_feature_gate_cache()
        self.addCleanup(analytics.reset_feature_gate_cache)

    def test_raising_cfg_is_off(self):
        class Bad:
            def feature(self, name):
                raise RuntimeError("x")
        self.assertFalse(analytics.feature_gate(Bad()))

    def test_cache_hit_skips_reload(self):
        self.assertTrue(analytics.feature_gate())
        with mock.patch.object(config, "load_config", side_effect=AssertionError("cached")):
            self.assertTrue(analytics.feature_gate())

    def test_yaml_missing_parser_and_bad_shapes(self):
        config.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        config.CONFIG_PATH.write_text("features:\n  analytics: true\n", encoding="utf-8")
        with mock.patch.object(config, "yaml", None):
            self.assertFalse(analytics._config_sources_intact())
            self.assertFalse(analytics.feature_gate_fresh())
        config.CONFIG_PATH.write_text("- a\n- b\n", encoding="utf-8")
        self.assertFalse(analytics._config_sources_intact())
        self.assertFalse(analytics.feature_gate_fresh())
        config.CONFIG_PATH.write_text("features:\n  analytics: banana\n", encoding="utf-8")
        self.assertFalse(analytics._config_sources_intact())
        self.assertFalse(analytics.feature_gate_fresh())
        config.CONFIG_PATH.unlink()

    def test_overrides_shapes(self):
        config.SETTINGS_OVERRIDES_PATH.parent.mkdir(parents=True, exist_ok=True)
        config.SETTINGS_OVERRIDES_PATH.write_text("[1]", encoding="utf-8")
        self.assertFalse(analytics._config_sources_intact())
        self.assertFalse(analytics.feature_gate_fresh())
        config.SETTINGS_OVERRIDES_PATH.write_text(
            json.dumps({"features.analytics": False}), encoding="utf-8")
        self.assertTrue(analytics._config_sources_intact())
        self.assertFalse(analytics.feature_gate_fresh())
        config.SETTINGS_OVERRIDES_PATH.write_text(
            json.dumps({"features": {"analytics": True}, "features.analytics": False}),
            encoding="utf-8")
        self.assertTrue(analytics.feature_gate_fresh())   # nested wins over flat
        config.SETTINGS_OVERRIDES_PATH.write_text(
            json.dumps({"features.analytics": "banana"}), encoding="utf-8")
        self.assertFalse(analytics._config_sources_intact())
        self.assertFalse(analytics.feature_gate_fresh())


class LogFirstAndClipTestCase(unittest.TestCase):
    def setUp(self):
        d = Path(tempfile.mkdtemp(prefix="ana-"))
        for attr, val in (("ANALYTICS_DIR", d), ("EVENTS_PATH", d / "events.jsonl"),
                          ("FIRST_DIR", d / "first")):
            p = mock.patch.object(analytics, attr, val)
            p.start()
            self.addCleanup(p.stop)
        _unlink_config_files()
        self.addCleanup(_unlink_config_files)
        analytics.reset_feature_gate_cache()
        self.addCleanup(analytics.reset_feature_gate_cache)

    def test_marker_present_is_noop(self):
        analytics.FIRST_DIR.mkdir(parents=True)
        (analytics.FIRST_DIR / "m1").touch()
        analytics.log_first("m1")
        self.assertFalse(analytics.EVENTS_PATH.exists())

    def test_failed_write_leaves_no_marker(self):
        with mock.patch.object(analytics, "log_event", return_value=False):
            analytics.log_first("m2")
        self.assertFalse((analytics.FIRST_DIR / "m2").exists())
        with mock.patch.object(analytics, "log_event", side_effect=RuntimeError("io")):
            analytics.log_first("m3")     # swallowed
        analytics.log_first("weird/name!!")
        self.assertTrue((analytics.FIRST_DIR / "weird_name").exists())

    def test_clip_content_fail_closed(self):
        with mock.patch.object(analytics, "_secret_positions", side_effect=RuntimeError("x")):
            self.assertIsNone(analytics.clip_content("hello"))
        self.assertIsNone(analytics.clip_content("   "))
        self.assertEqual(analytics.clip_content(f"a {SECRET}"), f"a {sanitize.MASK}")
        # a key split by whitespace is re-joined for the scan; the greedy
        # pattern then swallows the adjoining word too（宁可多掩不可半漏）
        split = "sk-ant-api03-abcdefgh ijklmnop tail; then"
        self.assertEqual(analytics.clip_content(split), f"{sanitize.MASK}; then")

    def test_log_event_off_when_gate_closed(self):
        with mock.patch.object(analytics, "feature_gate", return_value=False):
            self.assertFalse(analytics.log_event("x"))
        with mock.patch.object(analytics, "feature_gate", return_value=True), \
                mock.patch.object(analytics.Path, "mkdir", side_effect=OSError("ro")):
            self.assertFalse(analytics.log_event("x"))

    def test_content_gate_failure_is_closed(self):
        class Bad:
            def capture_input_active(self):
                raise RuntimeError("x")
        self.assertFalse(analytics.content_gate(Bad()))
        self.assertIsNone(analytics.clip(None))
        self.assertEqual(analytics.clip(" a  b ", 2), "a ")
        self.assertIsNone(analytics.parse_ts("nope"))
        self.assertIsInstance(os.environ.get("AIASSISTANT_HOME"), str)


if __name__ == "__main__":
    unittest.main()
