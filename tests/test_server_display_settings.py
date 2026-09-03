"""server/ display-preference face (CONTRACT §54.1 item 12; §49 routes):
GET/PUT /api/settings/display.

- effective values layered overrides → config.yaml ``ui.display`` → default
  (m / regular / normal); the three vocabulary lists ride along (the web
  renders its segmented controls from them, never from a client copy).
- PUT diff-writes the flat ``ui_display_*`` keys: equal-to-effective deletes
  the key, every other key in the file is preserved; unknown field / value
  outside the vocabulary / empty body → 400; the write gates apply (401
  without the instance token); an unparsable overrides file is a 409 and is
  never overwritten.
- the flat keys have no daemon reader: act/lib/config.py must keep ignoring
  them (a hand-written overrides file with them in it must not change any
  Config field).
Real server on a random port (tests/test_server_common.py); stdlib client.
"""
import json
import tempfile
import unittest
from pathlib import Path

from tests import TMP_HOME  # noqa: F401 - sandbox env first
from tests.test_server_common import (assert_envelope, auth_headers, get_json,
                                      http_request, start_server, write_text)

DEFAULT_SNAPSHOT = {"text_size": "m", "text_weight": "regular", "stroke": "normal"}
VOCAB = {"text_sizes": ["s", "m", "l", "xl"],
         "text_weights": ["regular", "medium", "bold"],
         "strokes": ["thin", "normal", "thick"]}


def put_json(port, path, payload):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    status, _h, data = http_request(port, "PUT", path, body=body, headers=auth_headers(port))
    return status, json.loads(data.decode("utf-8"))


class DisplaySettingsTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-display-")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        (self.home / "state").mkdir(parents=True)
        _httpd, self.port = start_server(self, self.home)

    def overrides(self) -> dict:
        p = self.home / "state" / "settings_overrides.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}

    def test_defaults_and_server_owned_vocabulary(self):
        status, snap = get_json(self.port, "/api/settings/display")
        self.assertEqual(status, 200)
        for key, value in DEFAULT_SNAPSHOT.items():
            self.assertEqual(snap[key], value)
        for key, values in VOCAB.items():
            self.assertEqual(snap[key], values)
        self.assertEqual(snap["source"], {k: "default" for k in DEFAULT_SNAPSHOT})

    def test_config_yaml_layer_with_bad_values_degrading_to_default(self):
        write_text(self.home / "config.yaml",
                   "ui:\n  display:\n    text_size: XL\n    text_weight: bold\n    stroke: hairline\n")
        _s, snap = get_json(self.port, "/api/settings/display")
        self.assertEqual((snap["text_size"], snap["text_weight"], snap["stroke"]), ("xl", "bold", "normal"))
        self.assertEqual(snap["source"], {"text_size": "config", "text_weight": "config", "stroke": "config"})
        write_text(self.home / "config.yaml", "ui: [not, a, map]\n")
        _s, snap = get_json(self.port, "/api/settings/display")
        self.assertEqual(snap["text_size"], "m")
        self.assertEqual(snap["source"]["text_size"], "default")

    def test_put_diff_writes_and_preserves_other_keys(self):
        write_text(self.home / "state" / "settings_overrides.json",
                   json.dumps({"language": "en", "models_pipeline": "claude-opus-5"}))
        status, snap = put_json(self.port, "/api/settings/display",
                                {"text_size": "L", "stroke": "thick"})
        self.assertEqual(status, 200)
        self.assertEqual((snap["text_size"], snap["text_weight"], snap["stroke"]), ("l", "regular", "thick"))
        self.assertEqual(snap["source"], {"text_size": "override", "text_weight": "default", "stroke": "override"})
        ov = self.overrides()
        self.assertEqual(ov["ui_display_text_size"], "l")
        self.assertEqual(ov["ui_display_stroke"], "thick")
        self.assertNotIn("ui_display_text_weight", ov)
        self.assertEqual((ov["language"], ov["models_pipeline"]), ("en", "claude-opus-5"))
        # back to the default → the key is deleted, not written as "m"
        status, snap = put_json(self.port, "/api/settings/display", {"text_size": "m"})
        self.assertEqual(snap["text_size"], "m")
        self.assertEqual(snap["source"]["text_size"], "default")
        self.assertNotIn("ui_display_text_size", self.overrides())
        self.assertEqual(self.overrides()["ui_display_stroke"], "thick")

    def test_put_equal_to_config_value_deletes_the_override(self):
        write_text(self.home / "config.yaml", "ui:\n  display:\n    text_weight: medium\n")
        write_text(self.home / "state" / "settings_overrides.json",
                   json.dumps({"ui_display_text_weight": "bold"}))
        _s, snap = put_json(self.port, "/api/settings/display", {"text_weight": "medium"})
        self.assertEqual(snap["text_weight"], "medium")
        self.assertEqual(snap["source"]["text_weight"], "config")
        self.assertEqual(self.overrides(), {})

    def test_bad_override_entry_is_skipped(self):
        write_text(self.home / "state" / "settings_overrides.json",
                   json.dumps({"ui_display_text_size": "huge", "ui_display_stroke": "THIN"}))
        _s, snap = get_json(self.port, "/api/settings/display")
        self.assertEqual(snap["text_size"], "m")
        self.assertEqual(snap["stroke"], "thin")

    def test_put_rejects_unknown_fields_bad_values_and_empty(self):
        status, body = put_json(self.port, "/api/settings/display", {"theme": "dark"})
        self.assertEqual(status, 400)
        assert_envelope(self, body, "UNKNOWN_FIELD")
        self.assertEqual(body["error"]["details"]["fields"], ["theme"])
        status, body = put_json(self.port, "/api/settings/display", {"text_size": "xxl"})
        self.assertEqual(status, 400)
        assert_envelope(self, body, "INVALID_FIELD")
        self.assertIn("text_size must be one of s, m, l, xl", body["error"]["message"])
        status, body = put_json(self.port, "/api/settings/display", {"stroke": None})
        self.assertEqual(status, 400)
        assert_envelope(self, body, "INVALID_FIELD")
        status, body = put_json(self.port, "/api/settings/display", {})
        self.assertEqual(status, 400)
        assert_envelope(self, body, "INVALID_FIELD")
        self.assertEqual(self.overrides(), {})

    def test_unparsable_overrides_are_a_409_never_overwritten(self):
        p = self.home / "state" / "settings_overrides.json"
        write_text(p, "{not json")
        status, body = get_json(self.port, "/api/settings/display")
        self.assertEqual(status, 409)
        assert_envelope(self, body, "CONFLICT")
        status, _body = put_json(self.port, "/api/settings/display", {"stroke": "thick"})
        self.assertEqual(status, 409)
        self.assertEqual(p.read_text(encoding="utf-8"), "{not json")

    def test_put_needs_the_write_gates(self):
        body = json.dumps({"stroke": "thick"}).encode("utf-8")
        status, _h, _d = http_request(self.port, "PUT", "/api/settings/display", body=body,
                                      headers={"Content-Type": "application/json"})
        self.assertEqual(status, 401)
        self.assertEqual(self.overrides(), {})
        status, _h, _d = http_request(self.port, "PUT", "/api/settings/display", body=body,
                                      headers={**auth_headers(self.port), "Content-Type": "text/plain"})
        self.assertEqual(status, 415)
        self.assertEqual(self.overrides(), {})


class DaemonIgnoresDisplayKeysTestCase(unittest.TestCase):
    """The flat ui_display_* keys are server-only: the pipeline's overrides
    overlay must leave every Config field untouched when it meets them."""

    def test_config_overlay_skips_the_display_keys(self):
        import dataclasses

        from act.lib import config
        from server import display

        with tempfile.TemporaryDirectory(prefix="zai-display-cfg-") as tmp:
            overrides = Path(tmp) / "settings_overrides.json"
            overrides.write_text(json.dumps({k: "thick" for k in display.OVERRIDE_KEYS.values()}),
                                 encoding="utf-8")
            before = config.Config()
            after = config.Config()
            original = config.SETTINGS_OVERRIDES_PATH
            config.SETTINGS_OVERRIDES_PATH = overrides
            try:
                config._apply_settings_overrides(after)
            finally:
                config.SETTINGS_OVERRIDES_PATH = original
            self.assertEqual(dataclasses.asdict(before), dataclasses.asdict(after))
            for key in display.OVERRIDE_KEYS.values():
                self.assertNotIn(key, config._OVERRIDE_FIELDS)


if __name__ == "__main__":
    unittest.main()
