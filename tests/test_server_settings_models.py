"""server/ settings face for the model knobs (CONTRACT §59, D22; §49 routes).

- GET /api/settings/models: effective knobs + server-owned canonical list.
- PUT /api/settings/models: all four write gates (same as POST), field
  whitelist, id-shape validation, diff-write into state/settings_overrides.json
  (equal-to-effective deletes the key; other keys preserved), 409 on an
  unparsable overrides file.
- GET/POST /api/claude-code/default-model: read / edit-only-``model`` of
  ~/.claude/settings.json with a backup first; 409 when unparsable; 400 for
  ``follow`` / malformed ids. ``HOME`` is pointed at a tempdir for the whole
  case so the developer's real ~/.claude is never touched.

Real server on a random port (tests/test_server_common.py); stdlib client.
"""
import json
import os
import stat
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
        # HOME → tempdir: ~/.claude/settings.json lives under it for the case
        self.user_home = Path(self.tmp.name) / "user"
        self.user_home.mkdir()
        env = mock.patch.dict(os.environ, {"HOME": str(self.user_home)})
        env.start()
        self.addCleanup(env.stop)
        self.cc_path = self.user_home / ".claude" / "settings.json"
        _httpd, self.port = start_server(self, self.home)

    @property
    def overrides_path(self) -> Path:
        return self.home / "state" / "settings_overrides.json"

    def _overrides(self):
        return json.loads(self.overrides_path.read_text(encoding="utf-8"))


class ModelsGetTestCase(_ServerCase):
    def test_defaults_follow_with_canonical_catalog(self):
        status, obj = get_json(self.port, "/api/settings/models")
        self.assertEqual(status, 200)
        self.assertEqual(obj["dispatch"], "follow")
        self.assertEqual(obj["pipeline"], "follow")
        self.assertEqual(obj["follow"], "follow")
        self.assertEqual(obj["canonical"], list(settings_mod.CANONICAL_MODELS))
        self.assertEqual(obj["source"], {"dispatch": "default", "pipeline": "default"})
        self.assertEqual(obj["warnings"], [])

    def test_layering_override_over_config_over_default(self):
        write_text(self.home / "config.yaml", "models:\n  dispatch: claude-sonnet-5\n")
        write_text(self.overrides_path, json.dumps({"models_pipeline": "claude-fable-5-1[1m]"}))
        _s, obj = get_json(self.port, "/api/settings/models")
        self.assertEqual(obj["dispatch"], "claude-sonnet-5")
        self.assertEqual(obj["pipeline"], "claude-fable-5-1[1m]")
        self.assertEqual(obj["source"], {"dispatch": "config", "pipeline": "override"})
        self.assertEqual(len(obj["warnings"]), 1)
        self.assertIn("claude-fable-5-1[1m]", obj["warnings"][0])

    def test_get_is_token_light(self):
        status, _h, _d = http_request(self.port, "GET", "/api/settings/models")
        self.assertEqual(status, 200)


class ModelsPutTestCase(_ServerCase):
    def test_put_writes_override_and_preserves_other_keys(self):
        write_text(self.overrides_path, json.dumps({"language": "en", "features": {"digest": False}}))
        status, obj = put_json(self.port, "/api/settings/models", {"dispatch": OPUS})
        self.assertEqual(status, 200)
        self.assertEqual(obj["dispatch"], OPUS)
        self.assertEqual(obj["source"]["dispatch"], "override")
        doc = self._overrides()
        self.assertEqual(doc, {"language": "en", "features": {"digest": False},
                               "models_dispatch": OPUS})

    def test_put_follow_deletes_the_key_diff_write(self):
        write_text(self.overrides_path, json.dumps({"models_dispatch": OPUS, "language": "zh"}))
        status, obj = put_json(self.port, "/api/settings/models", {"dispatch": "follow"})
        self.assertEqual(status, 200)
        self.assertEqual(obj["dispatch"], "follow")
        self.assertEqual(self._overrides(), {"language": "zh"})

    def test_put_equal_to_config_yaml_value_deletes_the_key(self):
        write_text(self.home / "config.yaml", "models:\n  pipeline: claude-sonnet-5\n")
        write_text(self.overrides_path, json.dumps({"models_pipeline": OPUS}))
        _s, obj = put_json(self.port, "/api/settings/models", {"pipeline": "claude-sonnet-5"})
        self.assertEqual(obj["pipeline"], "claude-sonnet-5")
        self.assertEqual(obj["source"]["pipeline"], "config")
        self.assertEqual(self._overrides(), {})

    def test_put_both_knobs_at_once(self):
        _s, obj = put_json(self.port, "/api/settings/models",
                           {"dispatch": OPUS, "pipeline": "claude-haiku-4-5-20251001"})
        self.assertEqual((obj["dispatch"], obj["pipeline"]), (OPUS, "claude-haiku-4-5-20251001"))
        self.assertEqual(self._overrides(),
                         {"models_dispatch": OPUS, "models_pipeline": "claude-haiku-4-5-20251001"})

    def test_free_text_is_accepted_with_a_warning(self):
        status, obj = put_json(self.port, "/api/settings/models", {"dispatch": "claude-opus-5-eap"})
        self.assertEqual(status, 200)
        self.assertEqual(obj["dispatch"], "claude-opus-5-eap")
        self.assertEqual(len(obj["warnings"]), 1)
        self.assertIn("claude-opus-5-eap", obj["warnings"][0])

    def test_unknown_field_is_400(self):
        status, obj = put_json(self.port, "/api/settings/models", {"dispatch": OPUS, "brain": "x"})
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
        status, obj = put_json(self.port, "/api/settings/models", {"dispatch": OPUS})
        self.assertEqual(status, 409)
        assert_envelope(self, obj, "CONFLICT")
        self.assertEqual(self.overrides_path.read_text(encoding="utf-8"), "{not json")

    def test_put_without_token_is_401(self):
        headers = auth_headers(self.port)
        headers.pop("X-Zai-Token")
        status, obj = put_json(self.port, "/api/settings/models", {"dispatch": OPUS}, headers)
        self.assertEqual(status, 401)
        assert_envelope(self, obj, "UNAUTHORIZED")
        self.assertFalse(self.overrides_path.exists())

    def test_put_cross_origin_is_403(self):
        headers = auth_headers(self.port)
        headers["Origin"] = "https://evil.example"
        status, obj = put_json(self.port, "/api/settings/models", {"dispatch": OPUS}, headers)
        self.assertEqual(status, 403)
        assert_envelope(self, obj, "FORBIDDEN")

    def test_put_text_plain_is_415(self):
        headers = auth_headers(self.port, content_type="text/plain")
        status, obj = put_json(self.port, "/api/settings/models", {"dispatch": OPUS}, headers)
        self.assertEqual(status, 415)
        assert_envelope(self, obj, "INVALID_FIELD")

    def test_put_unknown_path_is_404(self):
        status, obj = put_json(self.port, "/api/settings/nope", {"x": 1})
        self.assertEqual(status, 404)
        assert_envelope(self, obj, "NOT_FOUND")

    def test_pipeline_reads_what_the_web_wrote(self):
        """End to end: the daemon's config layer sees the PUT."""
        put_json(self.port, "/api/settings/models", {"dispatch": OPUS})
        from act.lib import config
        with mock.patch.object(config, "SETTINGS_OVERRIDES_PATH", self.overrides_path):
            cfg = config.load_config()
        self.assertEqual(cfg.models_dispatch, OPUS)
        self.assertEqual(cfg.models_pipeline, "follow")


class ClaudeCodeDefaultTestCase(_ServerCase):
    def _write_cc(self, doc, mode=None):
        self.cc_path.parent.mkdir(parents=True, exist_ok=True)
        self.cc_path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
        if mode is not None:
            os.chmod(self.cc_path, mode)

    def test_get_missing_file(self):
        status, obj = get_json(self.port, "/api/claude-code/default-model")
        self.assertEqual(status, 200)
        self.assertEqual(obj["model"], None)
        self.assertFalse(obj["exists"])
        self.assertFalse(obj["parseable"])
        self.assertEqual(obj["path"], str(self.cc_path))

    def test_get_reads_model_and_canonical_flag(self):
        self._write_cc({"model": "claude-fable-5-1[1m]", "theme": "dark"})
        _s, obj = get_json(self.port, "/api/claude-code/default-model")
        self.assertEqual(obj["model"], "claude-fable-5-1[1m]")
        self.assertTrue(obj["exists"] and obj["parseable"])
        self.assertFalse(obj["canonical"])
        self._write_cc({"model": OPUS})
        _s, obj = get_json(self.port, "/api/claude-code/default-model")
        self.assertTrue(obj["canonical"])

    def test_get_unparsable_is_honest_not_500(self):
        self.cc_path.parent.mkdir(parents=True)
        self.cc_path.write_text("{broken", encoding="utf-8")
        status, obj = get_json(self.port, "/api/claude-code/default-model")
        self.assertEqual(status, 200)
        self.assertTrue(obj["exists"])
        self.assertFalse(obj["parseable"])

    def test_post_edits_only_model_and_backs_up_first(self):
        original = {"theme": "dark", "model": "claude-fable-5-1[1m]",
                    "permissions": {"allow": ["Bash(ls:*)"]}, "env": {"A": "1"}}
        self._write_cc(original, mode=0o600)
        status, obj = post_json(self.port, "/api/claude-code/default-model", {"model": OPUS})
        self.assertEqual(status, 200)
        self.assertEqual(obj["model"], OPUS)
        self.assertEqual(obj["previous"], "claude-fable-5-1[1m]")
        backup = Path(obj["backup"])
        self.assertTrue(backup.exists())
        self.assertTrue(backup.name.startswith("settings.json.bak-"))
        self.assertEqual(json.loads(backup.read_text(encoding="utf-8")), original)
        after = json.loads(self.cc_path.read_text(encoding="utf-8"))
        self.assertEqual(after, {**original, "model": OPUS})
        self.assertEqual(list(after.keys()), list(original.keys()))   # key order kept
        self.assertEqual(stat.S_IMODE(self.cc_path.stat().st_mode), 0o600)
        # the file is still what GET reads
        _s, got = get_json(self.port, "/api/claude-code/default-model")
        self.assertEqual(got["model"], OPUS)

    def test_post_creates_the_file_when_absent(self):
        status, obj = post_json(self.port, "/api/claude-code/default-model", {"model": OPUS})
        self.assertEqual(status, 200)
        self.assertIsNone(obj["backup"])
        self.assertIsNone(obj["previous"])
        self.assertEqual(json.loads(self.cc_path.read_text(encoding="utf-8")), {"model": OPUS})

    def test_post_refuses_unparsable_file(self):
        self.cc_path.parent.mkdir(parents=True)
        self.cc_path.write_text("{broken", encoding="utf-8")
        status, obj = post_json(self.port, "/api/claude-code/default-model", {"model": OPUS})
        self.assertEqual(status, 409)
        assert_envelope(self, obj, "CONFLICT")
        self.assertEqual(self.cc_path.read_text(encoding="utf-8"), "{broken")
        self.assertEqual(list(self.cc_path.parent.glob("*.bak-*")), [])

    def test_post_rejects_follow_and_malformed(self):
        for bad in ("follow", "", "has space", None):
            with self.subTest(bad=bad):
                status, obj = post_json(self.port, "/api/claude-code/default-model",
                                        {"model": bad})
                self.assertEqual(status, 400)
                assert_envelope(self, obj, "INVALID_FIELD")
        self.assertFalse(self.cc_path.exists())

    def test_post_unknown_field_is_400(self):
        status, obj = post_json(self.port, "/api/claude-code/default-model",
                                {"model": OPUS, "theme": "light"})
        self.assertEqual(status, 400)
        assert_envelope(self, obj, "UNKNOWN_FIELD")

    def test_post_without_token_is_401(self):
        headers = auth_headers(self.port)
        headers.pop("X-Zai-Token")
        body = json.dumps({"model": OPUS}).encode("utf-8")
        status, _h, data = http_request(self.port, "POST", "/api/claude-code/default-model",
                                        body=body, headers=headers)
        self.assertEqual(status, 401)
        assert_envelope(self, json.loads(data), "UNAUTHORIZED")
        self.assertFalse(self.cc_path.exists())

    def test_two_posts_keep_two_backups(self):
        self._write_cc({"model": "a1"})
        _s, first = post_json(self.port, "/api/claude-code/default-model", {"model": OPUS})
        _s, second = post_json(self.port, "/api/claude-code/default-model", {"model": "claude-sonnet-5"})
        self.assertNotEqual(first["backup"], second["backup"])
        self.assertEqual(len(list(self.cc_path.parent.glob("settings.json.bak-*"))), 2)


if __name__ == "__main__":
    unittest.main()
