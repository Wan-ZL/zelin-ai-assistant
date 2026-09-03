"""server/ recap face (CONTRACT §63; §49 routes): GET/PUT /api/settings/recap,
POST /api/recaps/mark, and the two inbox special forms through POST /api/actions.

- settings: effective values layered overrides → config.yaml → default;
  ``slack_draft_enabled`` is false out of the box; PUT diff-writes the flat
  keys (equal-to-effective deletes the key, other keys preserved), unknown
  field / bad value → 400.
- marks: a server-owned file no control flow reads; key shape and mark
  vocabulary fail closed; ``on: false`` clears the stamp.
- inbox forms: meeting_key shape, note ≤ 500, partial only ``true``,
  channel_id shape; unknown fields 400; files land with ``via: web``.
Real server on a random port (tests/test_server_common.py); stdlib client.
"""
import json
import tempfile
import unittest
from pathlib import Path

from tests import TMP_HOME  # noqa: F401 - sandbox env first
from tests.test_server_common import (assert_envelope, auth_headers, get_json,
                                      http_request, post_json, start_server, write_text)

KEY = "meeting:2026-08-31T1256-zoom"


def put_json(port, path, payload):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    status, _h, data = http_request(port, "PUT", path, body=body, headers=auth_headers(port))
    return status, json.loads(data.decode("utf-8"))


class _Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-recaps-")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        (self.home / "state").mkdir(parents=True)
        _httpd, self.port = start_server(self, self.home)

    def overrides(self) -> dict:
        p = self.home / "state" / "settings_overrides.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


class SettingsTestCase(_Case):
    def test_defaults(self):
        status, snap = get_json(self.port, "/api/settings/recap")
        self.assertEqual(status, 200)
        self.assertEqual(snap["enabled"], True)
        self.assertEqual(snap["default_language"], "auto")
        self.assertEqual(snap["slack_draft_enabled"], False)
        self.assertEqual(snap["languages"], ["auto", "zh", "en"])
        self.assertEqual(snap["source"], {"enabled": "default", "default_language": "default",
                                          "slack_draft_enabled": "default"})

    def test_config_yaml_layer(self):
        write_text(self.home / "config.yaml",
                   "recap:\n  enabled: false\n  default_language: zh\n  slack_draft:\n    enabled: 'true'\n")
        _s, snap = get_json(self.port, "/api/settings/recap")
        self.assertEqual((snap["enabled"], snap["default_language"], snap["slack_draft_enabled"]),
                         (False, "zh", True))
        self.assertEqual(set(snap["source"].values()), {"config"})
        write_text(self.home / "config.yaml", "recap: [not, a, map]\n")
        _s, snap = get_json(self.port, "/api/settings/recap")
        self.assertEqual(snap["slack_draft_enabled"], False)

    def test_put_diff_writes_and_preserves_other_keys(self):
        write_text(self.home / "state" / "settings_overrides.json",
                   json.dumps({"language": "en", "models_pipeline": "claude-opus-5"}))
        status, snap = put_json(self.port, "/api/settings/recap",
                                {"slack_draft_enabled": True, "default_language": "EN"})
        self.assertEqual(status, 200)
        self.assertEqual((snap["slack_draft_enabled"], snap["default_language"]), (True, "en"))
        self.assertEqual(snap["source"]["slack_draft_enabled"], "override")
        ov = self.overrides()
        self.assertEqual(ov["recap_slack_draft_enabled"], True)
        self.assertEqual(ov["recap_default_language"], "en")
        self.assertEqual((ov["language"], ov["models_pipeline"]), ("en", "claude-opus-5"))
        # back to the default → the key is deleted, not written as false
        status, snap = put_json(self.port, "/api/settings/recap", {"slack_draft_enabled": "false"})
        self.assertEqual(snap["slack_draft_enabled"], False)
        self.assertNotIn("recap_slack_draft_enabled", self.overrides())
        self.assertEqual(snap["source"]["slack_draft_enabled"], "default")

    def test_bad_override_entry_is_skipped(self):
        write_text(self.home / "state" / "settings_overrides.json",
                   json.dumps({"recap_default_language": "klingon", "recap_enabled": "no"}))
        _s, snap = get_json(self.port, "/api/settings/recap")
        self.assertEqual(snap["default_language"], "auto")
        self.assertEqual(snap["enabled"], False)

    def test_put_rejects_unknown_fields_bad_values_and_empty(self):
        status, body = put_json(self.port, "/api/settings/recap", {"targets": {}})
        self.assertEqual(status, 400)
        assert_envelope(self, body, "UNKNOWN_FIELD")
        status, body = put_json(self.port, "/api/settings/recap", {"default_language": "fr"})
        self.assertEqual(status, 400)
        assert_envelope(self, body, "INVALID_FIELD")
        status, body = put_json(self.port, "/api/settings/recap", {"enabled": "maybe"})
        self.assertEqual(status, 400)
        assert_envelope(self, body, "INVALID_FIELD")
        status, body = put_json(self.port, "/api/settings/recap", {})
        self.assertEqual(status, 400)
        assert_envelope(self, body, "INVALID_FIELD")

    def test_unreadable_or_non_object_overrides_are_a_409_never_overwritten(self):
        p = self.home / "state" / "settings_overrides.json"
        write_text(p, "[1, 2]")
        status, body = get_json(self.port, "/api/settings/recap")
        self.assertEqual(status, 409)
        assert_envelope(self, body, "CONFLICT")
        status, body = put_json(self.port, "/api/settings/recap", {"enabled": False})
        self.assertEqual(status, 409)
        self.assertEqual(p.read_text(encoding="utf-8"), "[1, 2]")
        p.unlink()
        p.mkdir()                     # a directory: read_text raises IsADirectoryError (OSError)
        status, body = get_json(self.port, "/api/settings/recap")
        self.assertEqual(status, 409)
        assert_envelope(self, body, "CONFLICT")

    def test_put_needs_the_write_gates(self):
        body = json.dumps({"enabled": False}).encode("utf-8")
        status, _h, _d = http_request(self.port, "PUT", "/api/settings/recap", body=body,
                                      headers={"Content-Type": "application/json"})
        self.assertEqual(status, 401)
        self.assertEqual(self.overrides(), {})


class MarksTestCase(_Case):
    def marks(self) -> dict:
        p = self.home / "state" / "recap" / "marks.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}

    def test_copy_then_sent_then_clear(self):
        status, body = post_json(self.port, "/api/recaps/mark", {"key": KEY, "mark": "copied"})
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertTrue(body["copied_at"].endswith("Z"))
        self.assertIsNone(body["sent_at"])
        status, body = post_json(self.port, "/api/recaps/mark", {"key": KEY, "mark": "sent", "on": True})
        self.assertTrue(body["sent_at"])
        self.assertEqual(set(self.marks()[KEY]), {"copied_at", "sent_at"})
        status, body = post_json(self.port, "/api/recaps/mark", {"key": KEY, "mark": "sent", "on": False})
        self.assertIsNone(body["sent_at"])
        self.assertTrue(body["copied_at"])

    def test_validation(self):
        for payload, code in (
            ({"key": "R-101", "mark": "copied"}, "INVALID_FIELD"),
            ({"key": KEY, "mark": "forwarded"}, "INVALID_FIELD"),
            ({"key": KEY, "mark": "sent", "on": "yes"}, "INVALID_FIELD"),
            ({"key": KEY, "mark": "sent", "channel": "C1"}, "UNKNOWN_FIELD"),
            ({"mark": "sent"}, "INVALID_FIELD"),
        ):
            with self.subTest(payload=payload):
                status, body = post_json(self.port, "/api/recaps/mark", payload)
                self.assertEqual(status, 400)
                assert_envelope(self, body, code)
        self.assertEqual(self.marks(), {})

    def test_corrupt_marks_file_is_replaced_not_crashed(self):
        p = self.home / "state" / "recap" / "marks.json"
        p.parent.mkdir(parents=True)
        p.write_text("{oops", encoding="utf-8")
        status, body = post_json(self.port, "/api/recaps/mark", {"key": KEY, "mark": "copied"})
        self.assertEqual(status, 200)
        self.assertIn(KEY, self.marks())


class InboxFormsTestCase(_Case):
    def _files(self):
        return sorted((self.home / "state" / "inbox").glob("*.json"))

    def test_recap_generate_lands_with_via_web(self):
        status, body = post_json(self.port, "/api/actions",
                                 {"action": "recap_generate", "meeting_key": KEY, "note": "fix", "partial": True})
        self.assertEqual(status, 200)
        self.assertEqual(body["action"], "recap_generate")
        rec = json.loads(self._files()[0].read_text(encoding="utf-8"))
        self.assertEqual(rec["meeting_key"], KEY)
        self.assertEqual((rec["note"], rec["partial"], rec["via"]), ("fix", True, "web"))
        self.assertNotIn("channel_id", rec)

    def test_recap_slack_draft_lands(self):
        status, _body = post_json(self.port, "/api/actions",
                                  {"action": "recap_slack_draft", "meeting_key": KEY, "channel_id": "D0ABCDEF12"})
        self.assertEqual(status, 200)
        rec = json.loads(self._files()[0].read_text(encoding="utf-8"))
        self.assertEqual(rec["channel_id"], "D0ABCDEF12")

    def test_validation_fails_closed(self):
        cases = (
            ({"action": "recap_generate"}, "INVALID_FIELD"),
            ({"action": "recap_generate", "meeting_key": "R-101"}, "INVALID_FIELD"),
            ({"action": "recap_generate", "meeting_key": KEY, "note": "n" * 501}, "INVALID_FIELD"),
            ({"action": "recap_generate", "meeting_key": KEY, "note": ""}, "INVALID_FIELD"),
            ({"action": "recap_generate", "meeting_key": KEY, "partial": False}, "INVALID_FIELD"),
            ({"action": "recap_generate", "meeting_key": KEY, "channel_id": "C123"}, "UNKNOWN_FIELD"),
            ({"action": "recap_generate", "meeting_key": KEY, "id": "R-1"}, "UNKNOWN_FIELD"),
            ({"action": "recap_slack_draft", "meeting_key": KEY}, "INVALID_FIELD"),
            ({"action": "recap_slack_draft", "meeting_key": KEY, "channel_id": "general"}, "INVALID_FIELD"),
            ({"action": "recap_slack_draft", "meeting_key": KEY, "channel_id": "C0123456789", "note": "x"},
             "UNKNOWN_FIELD"),
        )
        for payload, code in cases:
            with self.subTest(payload=payload):
                status, body = post_json(self.port, "/api/actions", payload)
                self.assertEqual(status, 400)
                assert_envelope(self, body, code)
        self.assertEqual(self._files(), [])


if __name__ == "__main__":
    unittest.main()
