"""server/ settings face for the model knobs (CONTRACT §59, D22 / D53; §49 routes).

- GET /api/settings/models: effective knobs + server-owned canonical list.
- PUT /api/settings/models: all four write gates, field whitelist, id-shape
  validation, diff-write into state/settings_overrides.json (equal-to-effective
  deletes the key; other keys preserved), 409 on an unparsable overrides file.

v2.0.0: the ``dispatch`` knob and the "follow the Claude Code global default"
mode retired (§78: the app never reads the owner's Claude Code settings), and so
did ``GET/POST /api/claude-code/default-model`` (the writer of
~/.claude/settings.json) — both are 404 now; a stored / PUT ``"follow"`` reads as
the product default. ``HOME`` is pointed at a tempdir for the whole case so a
regression could never touch the developer's real ~/.claude.

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
                                      http_request, post_json, start_server,
                                      write_text)

from server import settings as settings_mod

OPUS = "claude-opus-5"
SONNET = "claude-sonnet-5"


def put_json(port, path, payload, headers=None):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    status, _h, data = http_request(port, "PUT", path, body=body,
                                    headers=headers if headers is not None
                                    else auth_headers(port))
    return status, json.loads(data.decode("utf-8"))


class _ServerCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-settings-")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        (self.home / "state").mkdir(parents=True)
        self.user_home = Path(self.tmp.name) / "user"
        self.user_home.mkdir()
        env = mock.patch.dict(os.environ, {"HOME": str(self.user_home),
                                           "USERPROFILE": str(self.user_home)})
        env.start()
        self.addCleanup(env.stop)
        _httpd, self.port = start_server(self, self.home)

    @property
    def overrides_path(self) -> Path:
        return self.home / "state" / "settings_overrides.json"

    def _overrides(self):
        return json.loads(self.overrides_path.read_text(encoding="utf-8"))


class ModelsGetTestCase(_ServerCase):
    def test_defaults_with_canonical_catalog(self):
        status, snap = get_json(self.port, "/api/settings/models")
        self.assertEqual(status, 200)
        self.assertEqual(snap["pipeline"], OPUS)
        self.assertEqual(snap["pipeline_default"], OPUS)
        self.assertEqual(snap["fallback"], settings_mod.DEFAULT_MODEL_FALLBACK)
        self.assertEqual(snap["canonical"], list(settings_mod.CANONICAL_MODELS))
        self.assertEqual(snap["source"], {"pipeline": "default", "fallback": "default"})
        self.assertEqual(snap["warnings"], [])
        self.assertNotIn("dispatch", snap)

    def test_layering_override_over_config_over_default(self):
        write_text(self.home / "config.yaml", "models:\n  pipeline: %s\n" % SONNET)
        _s, snap = get_json(self.port, "/api/settings/models")
        self.assertEqual((snap["pipeline"], snap["source"]["pipeline"]), (SONNET, "config"))
        write_text(self.overrides_path, json.dumps({"models_pipeline": OPUS}))
        _s, snap = get_json(self.port, "/api/settings/models")
        self.assertEqual((snap["pipeline"], snap["source"]["pipeline"]), (OPUS, "override"))

    def test_a_stored_follow_reads_as_the_default(self):
        write_text(self.overrides_path, json.dumps({"models_pipeline": "follow",
                                                    "models_dispatch": SONNET}))
        _s, snap = get_json(self.port, "/api/settings/models")
        self.assertEqual(snap["pipeline"], OPUS)
        self.assertNotIn("dispatch", snap)

    def test_get_is_token_light(self):
        status, _h, _d = http_request(self.port, "GET", "/api/settings/models")
        self.assertEqual(status, 200)


class ModelsPutTestCase(_ServerCase):
    def test_put_writes_override_and_preserves_other_keys(self):
        write_text(self.overrides_path, json.dumps({"language": "en"}))
        status, snap = put_json(self.port, "/api/settings/models", {"pipeline": SONNET})
        self.assertEqual(status, 200)
        self.assertEqual(snap["pipeline"], SONNET)
        self.assertEqual(self._overrides(), {"language": "en", "models_pipeline": SONNET})

    def test_put_follow_or_the_default_deletes_the_key_diff_write(self):
        for value in ("follow", OPUS):
            with self.subTest(value=value):
                write_text(self.overrides_path, json.dumps({"models_pipeline": SONNET}))
                status, snap = put_json(self.port, "/api/settings/models", {"pipeline": value})
                self.assertEqual(status, 200)
                self.assertEqual(snap["pipeline"], OPUS)
                self.assertEqual(self._overrides(), {})

    def test_put_equal_to_config_yaml_value_deletes_the_key(self):
        write_text(self.home / "config.yaml", "models:\n  pipeline: %s\n" % SONNET)
        write_text(self.overrides_path, json.dumps({"models_pipeline": OPUS}))
        _s, snap = put_json(self.port, "/api/settings/models", {"pipeline": SONNET})
        self.assertEqual(snap["source"]["pipeline"], "config")
        self.assertEqual(self._overrides(), {})

    def test_put_both_knobs_at_once(self):
        _s, snap = put_json(self.port, "/api/settings/models", {"pipeline": SONNET, "fallback": "off"})
        self.assertEqual((snap["pipeline"], snap["fallback"]), (SONNET, "off"))
        self.assertEqual(self._overrides(), {"models_pipeline": SONNET, "models_fallback": "off"})

    def test_free_text_is_accepted_with_a_warning(self):
        status, snap = put_json(self.port, "/api/settings/models", {"pipeline": "claude-fable-5-1[1m]"})
        self.assertEqual(status, 200)
        self.assertEqual(snap["pipeline"], "claude-fable-5-1[1m]")
        self.assertEqual(len(snap["warnings"]), 1)

    def test_unknown_field_is_400(self):
        status, obj = put_json(self.port, "/api/settings/models", {"dispatch": OPUS})
        self.assertEqual(status, 400)
        assert_envelope(self, obj, "UNKNOWN_FIELD")
        self.assertFalse(self.overrides_path.exists())

    def test_malformed_id_is_400_with_plain_reason(self):
        for bad in ("has space", 12, "-lead", "x" * 70):
            with self.subTest(bad=bad):
                status, obj = put_json(self.port, "/api/settings/models", {"pipeline": bad})
                self.assertEqual(status, 400)
                assert_envelope(self, obj, "INVALID_FIELD")
                self.assertEqual(obj["error"]["details"], {"field": "pipeline"})
        self.assertFalse(self.overrides_path.exists())

    def test_empty_body_is_400(self):
        status, obj = put_json(self.port, "/api/settings/models", {})
        self.assertEqual(status, 400)
        assert_envelope(self, obj, "INVALID_FIELD")

    def test_unparsable_overrides_file_is_409_and_untouched(self):
        write_text(self.overrides_path, "{not json")
        status, obj = put_json(self.port, "/api/settings/models", {"pipeline": SONNET})
        self.assertEqual(status, 409)
        assert_envelope(self, obj, "CONFLICT")
        self.assertEqual(self.overrides_path.read_text(encoding="utf-8"), "{not json")

    def test_put_without_token_is_401(self):
        headers = auth_headers(self.port)
        headers.pop("X-Zai-Token")
        status, obj = put_json(self.port, "/api/settings/models", {"pipeline": SONNET}, headers)
        self.assertEqual(status, 401)
        assert_envelope(self, obj, "UNAUTHORIZED")
        self.assertFalse(self.overrides_path.exists())

    def test_put_cross_origin_is_403(self):
        headers = auth_headers(self.port)
        headers["Origin"] = "https://evil.example"
        status, obj = put_json(self.port, "/api/settings/models", {"pipeline": SONNET}, headers)
        self.assertEqual(status, 403)
        assert_envelope(self, obj, "FORBIDDEN")

    def test_put_text_plain_is_415(self):
        headers = auth_headers(self.port, content_type="text/plain")
        status, obj = put_json(self.port, "/api/settings/models", {"pipeline": SONNET}, headers)
        self.assertEqual(status, 415)
        assert_envelope(self, obj, "INVALID_FIELD")

    def test_put_unknown_path_is_404(self):
        status, obj = put_json(self.port, "/api/settings/nope", {"x": 1})
        self.assertEqual(status, 404)
        assert_envelope(self, obj, "NOT_FOUND")

    def test_pipeline_reads_what_the_web_wrote(self):
        """End to end: the pipeline's config layer sees the PUT."""
        put_json(self.port, "/api/settings/models", {"pipeline": SONNET})
        from act.lib import config
        with mock.patch.object(config, "SETTINGS_OVERRIDES_PATH", self.overrides_path):
            cfg = config.load_config()
        self.assertEqual(cfg.models_pipeline, SONNET)


class RetiredClaudeCodeDefaultTestCase(_ServerCase):
    """§78: the app never reads or writes the owner's ~/.claude/settings.json."""

    def test_both_verbs_are_404_and_nothing_is_written(self):
        status, _obj = get_json(self.port, "/api/claude-code/default-model")
        self.assertEqual(status, 404)
        status, _obj = post_json(self.port, "/api/claude-code/default-model", {"model": OPUS})
        self.assertEqual(status, 404)
        self.assertFalse((self.user_home / ".claude").exists())


if __name__ == "__main__":
    unittest.main()
