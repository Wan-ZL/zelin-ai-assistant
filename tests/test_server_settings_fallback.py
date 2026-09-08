"""server/ settings face for the third model knob ``fallback`` (CONTRACT §59 D53;
§15 add-only key ``models_fallback``; §49 route ``/api/settings/models``).

- GET carries ``fallback`` / ``off`` / ``fallback_default`` / ``source.fallback``
  add-only next to the two D22 knobs; the product default draws no warning, a
  free-text alias draws the fallback-specific one.
- PUT ``fallback`` accepts an id or ``off``; diff-write against the
  config.yaml/default effective value (equal → key deleted); malformed → 400
  INVALID_FIELD naming the field; the two D22 knobs and every other override
  key are preserved.
- server ↔ act mirror: constants and the coercion truth table agree, and the
  daemon's config layer reads what the web wrote.

Real server on a random port (tests/test_server_common.py); stdlib client.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first
from tests.test_server_common import (assert_envelope, auth_headers, get_json,
                                      http_request, start_server, write_text)

from act.lib import config as act_config
from server import settings as settings_mod

DEFAULT = "claude-opus-5[1m]"


def put_json(port, path, payload):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    status, _h, data = http_request(port, "PUT", path, body=body, headers=auth_headers(port))
    return status, json.loads(data.decode("utf-8"))


class _ServerCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-fallback-")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        (self.home / "state").mkdir(parents=True)
        user_home = Path(self.tmp.name) / "user"
        user_home.mkdir()
        env = mock.patch.dict(os.environ, {"HOME": str(user_home), "USERPROFILE": str(user_home)})
        env.start()
        self.addCleanup(env.stop)
        _httpd, self.port = start_server(self, self.home)

    @property
    def overrides_path(self) -> Path:
        return self.home / "state" / "settings_overrides.json"

    def _overrides(self):
        return json.loads(self.overrides_path.read_text(encoding="utf-8"))


class GetTestCase(_ServerCase):
    def test_default_snapshot_carries_the_third_knob(self):
        status, obj = get_json(self.port, "/api/settings/models")
        self.assertEqual(status, 200)
        self.assertEqual(obj["fallback"], DEFAULT)
        self.assertEqual(obj["off"], "off")
        self.assertEqual(obj["fallback_default"], DEFAULT)
        self.assertEqual(obj["source"]["fallback"], "default")
        self.assertEqual(obj["warnings"], [])            # the product default is not a warning
        # the D22 half is untouched
        self.assertEqual((obj["dispatch"], obj["pipeline"], obj["follow"]), ("follow", "follow", "follow"))

    def test_layering_override_over_config_over_default(self):
        write_text(self.home / "config.yaml", "models:\n  fallback: claude-sonnet-5\n")
        _s, obj = get_json(self.port, "/api/settings/models")
        self.assertEqual((obj["fallback"], obj["source"]["fallback"]), ("claude-sonnet-5", "config"))
        write_text(self.overrides_path, json.dumps({"models_fallback": "off"}))
        _s, obj = get_json(self.port, "/api/settings/models")
        self.assertEqual((obj["fallback"], obj["source"]["fallback"]), ("off", "override"))
        self.assertEqual(obj["warnings"], [])

    def test_alias_fallback_draws_the_fallback_specific_warning(self):
        write_text(self.overrides_path, json.dumps({"models_fallback": "claude-opus-5-eap"}))
        _s, obj = get_json(self.port, "/api/settings/models")
        self.assertEqual(len(obj["warnings"]), 1)
        self.assertIn("claude-opus-5-eap", obj["warnings"][0])
        self.assertTrue(obj["warnings"][0].startswith("fallback"))
        self.assertIn("回退", obj["warnings"][0])

    def test_malformed_override_is_skipped_like_the_pipeline(self):
        write_text(self.overrides_path, json.dumps({"models_fallback": "has space"}))
        _s, obj = get_json(self.port, "/api/settings/models")
        self.assertEqual((obj["fallback"], obj["source"]["fallback"]), (DEFAULT, "default"))

    def test_bad_yaml_value_reads_as_default_not_off(self):
        write_text(self.home / "config.yaml", "models:\n  fallback: 'has space'\n")
        _s, obj = get_json(self.port, "/api/settings/models")
        self.assertEqual((obj["fallback"], obj["source"]["fallback"]), (DEFAULT, "config"))


class PutTestCase(_ServerCase):
    def test_put_off_writes_the_key_and_keeps_siblings(self):
        write_text(self.overrides_path, json.dumps({"language": "en", "models_dispatch": "claude-opus-5"}))
        status, obj = put_json(self.port, "/api/settings/models", {"fallback": "off"})
        self.assertEqual(status, 200)
        self.assertEqual((obj["fallback"], obj["source"]["fallback"]), ("off", "override"))
        self.assertEqual(self._overrides(), {"language": "en", "models_dispatch": "claude-opus-5",
                                             "models_fallback": "off"})

    def test_put_default_deletes_the_key_diff_write(self):
        write_text(self.overrides_path, json.dumps({"models_fallback": "off", "language": "zh"}))
        _s, obj = put_json(self.port, "/api/settings/models", {"fallback": DEFAULT})
        self.assertEqual((obj["fallback"], obj["source"]["fallback"]), (DEFAULT, "default"))
        self.assertEqual(self._overrides(), {"language": "zh"})

    def test_put_equal_to_config_yaml_value_deletes_the_key(self):
        write_text(self.home / "config.yaml", "models:\n  fallback: off\n")
        write_text(self.overrides_path, json.dumps({"models_fallback": "claude-opus-5"}))
        _s, obj = put_json(self.port, "/api/settings/models", {"fallback": "OFF"})
        self.assertEqual((obj["fallback"], obj["source"]["fallback"]), ("off", "config"))
        self.assertEqual(self._overrides(), {})

    def test_put_explicit_id_is_stripped_and_kept(self):
        _s, obj = put_json(self.port, "/api/settings/models", {"fallback": " claude-sonnet-5 "})
        self.assertEqual(obj["fallback"], "claude-sonnet-5")
        self.assertEqual(self._overrides(), {"models_fallback": "claude-sonnet-5"})

    def test_put_all_three_knobs_at_once(self):
        _s, obj = put_json(self.port, "/api/settings/models",
                           {"dispatch": "claude-opus-5", "pipeline": "follow", "fallback": "off"})
        self.assertEqual((obj["dispatch"], obj["pipeline"], obj["fallback"]), ("claude-opus-5", "follow", "off"))
        self.assertEqual(self._overrides(), {"models_dispatch": "claude-opus-5", "models_fallback": "off"})

    def test_blank_reads_as_default(self):
        write_text(self.overrides_path, json.dumps({"models_fallback": "off"}))
        _s, obj = put_json(self.port, "/api/settings/models", {"fallback": ""})
        self.assertEqual(obj["fallback"], DEFAULT)
        self.assertEqual(self._overrides(), {})

    def test_malformed_is_400_naming_the_field_and_writes_nothing(self):
        for bad in ("has space", 12, "-lead", "x" * 70, "a,b"):
            with self.subTest(bad=bad):
                status, obj = put_json(self.port, "/api/settings/models", {"fallback": bad})
                self.assertEqual(status, 400)
                assert_envelope(self, obj, "INVALID_FIELD")
                self.assertEqual(obj["error"]["details"], {"field": "fallback"})
        self.assertFalse(self.overrides_path.exists())

    def test_follow_is_not_a_fallback_sentinel(self):
        # "follow" passes the id shape gate and is stored as typed — the CLI
        # decides model names; the fallback-specific warning flags it
        _s, obj = put_json(self.port, "/api/settings/models", {"fallback": "follow"})
        self.assertEqual(obj["fallback"], "follow")
        self.assertEqual(len(obj["warnings"]), 1)

    def test_pipeline_reads_what_the_web_wrote(self):
        put_json(self.port, "/api/settings/models", {"fallback": "off"})
        with mock.patch.object(act_config, "SETTINGS_OVERRIDES_PATH", self.overrides_path):
            cfg = act_config.load_config()
        self.assertEqual(cfg.models_fallback, "off")
        put_json(self.port, "/api/settings/models", {"fallback": "claude-sonnet-5"})
        with mock.patch.object(act_config, "SETTINGS_OVERRIDES_PATH", self.overrides_path):
            self.assertEqual(act_config.load_config().models_fallback, "claude-sonnet-5")


class MirrorTestCase(unittest.TestCase):
    """server/ does not import act (§49): the hand-copied constants must agree."""

    def test_constants_mirror_config(self):
        self.assertEqual(settings_mod.MODEL_FALLBACK_OFF, act_config.MODEL_FALLBACK_OFF)
        self.assertEqual(settings_mod.DEFAULT_MODEL_FALLBACK, act_config.DEFAULT_MODEL_FALLBACK)
        self.assertEqual(settings_mod.MODEL_KNOBS, act_config.MODEL_MODES + ("fallback",))
        self.assertIn(settings_mod.OVERRIDE_KEY % settings_mod.MODEL_FALLBACK, act_config._OVERRIDE_FIELDS)

    def test_coerce_fallback_agrees_on_a_table(self):
        table = (None, "", "  ", "off", " OFF ", "Off", False, "follow", " claude-opus-5 ", DEFAULT,
                 "claude-fable-5-1[1m]", "has space", "-lead", "a" * 65, 12, True, "x\ny", "q'uote")
        for value in table:
            with self.subTest(value=value):
                try:
                    a = ("ok", act_config.coerce_fallback_model(value))
                except ValueError:
                    a = ("err", None)
                try:
                    b = ("ok", settings_mod.coerce_fallback_model(value))
                except ValueError:
                    b = ("err", None)
                self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
