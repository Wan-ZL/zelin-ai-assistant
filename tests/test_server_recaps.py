"""server/ recap face (CONTRACT §63 / §63.9; §49 routes): GET/PUT
/api/settings/recap, POST /api/recaps/mark, GET /api/recaps/history and the
three inbox special forms through POST /api/actions.

- settings: effective values layered overrides → config.yaml → default;
  ``slack_draft_enabled`` is false out of the box; PUT diff-writes the flat
  keys (equal-to-effective deletes the key, other keys preserved), unknown
  field / bad value → 400.
- marks: the server-owned filing axis (§63.5 追记 2026-09-15, issue #301 —
  the old 「无控制流读它」clause is retired: ``sent_at`` / ``dismissed_at``
  decide the projection budget and the dismissed retention window in
  recap_store, and nothing else); key shape and mark vocabulary fail closed;
  ``on: false`` clears the stamp (「恢复」).
- history (§63.9, issue #300): the stored versions **with their text** — the
  one place the earlier text is readable (the board projection carries only
  scalar handles). Read-only (the server never writes a recap file, §63.6),
  fail-open: absent / corrupt / oversize = 200 empty layer, never 500 / 404;
  a bad key is the one 400 — the client never names a path.
- inbox forms: meeting_key shape, note ≤ 500, partial only ``true``,
  channel_id shape, ``recap_revert`` version = a real integer ≥ 1, §63.11
  ``answers`` = 1..12 distinct ``id=value`` strings (a list of strings, because
  the byte serializer has no nested-object branch); unknown fields 400; files
  land with ``via: web``.
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
        # §63.10 只读的第四格：出厂 = 快速五行（面板拿它当形状选择器的初值）
        self.assertEqual(snap["default_shape"], "lines")
        self.assertEqual(snap["source"], {"enabled": "default", "default_language": "default",
                                          "slack_draft_enabled": "default",
                                          "default_shape": "default"})

    def test_config_yaml_layer(self):
        write_text(self.home / "config.yaml",
                   "recap:\n  enabled: false\n  default_language: zh\n  default_shape: sections\n"
                   "  slack_draft:\n    enabled: 'true'\n")
        _s, snap = get_json(self.port, "/api/settings/recap")
        self.assertEqual((snap["enabled"], snap["default_language"], snap["slack_draft_enabled"]),
                         (False, "zh", True))
        # §63.10：形状也是 config.yaml 层的一格（PUT 不收它——管线没有对应的 overrides 扁平键）
        self.assertEqual(snap["default_shape"], "sections")
        self.assertEqual(set(snap["source"].values()), {"config"})
        write_text(self.home / "config.yaml", "recap:\n  default_shape: klingon\n")
        _s, snap = get_json(self.port, "/api/settings/recap")
        self.assertEqual(snap["default_shape"], "lines")   # 认不出的值回落到出厂形，不 500
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
        # §63.10：`default_shape` GET 得到、PUT 写不进——它只住 config.yaml，
        # 写进 settings_overrides.json 的话管线根本不读，那个开关会是一颗假按钮
        status, body = put_json(self.port, "/api/settings/recap", {"default_shape": "sections"})
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

    def test_dismiss_then_restore(self):
        """§63.5 追记（issue #301）：忽略 = 第三个 mark，回执带三个时间戳，`on: false` 恢复。"""
        status, body = post_json(self.port, "/api/recaps/mark", {"key": KEY, "mark": "dismissed"})
        self.assertEqual(status, 200)
        self.assertTrue(body["dismissed_at"].endswith("Z"))
        self.assertIsNone(body["sent_at"])
        self.assertEqual(set(self.marks()[KEY]), {"dismissed_at"})
        # 忽略与已发送互不覆盖（两个原因各自一个戳）
        status, body = post_json(self.port, "/api/recaps/mark", {"key": KEY, "mark": "sent"})
        self.assertTrue(body["sent_at"])
        self.assertTrue(body["dismissed_at"])
        status, body = post_json(self.port, "/api/recaps/mark", {"key": KEY, "mark": "dismissed", "on": False})
        self.assertIsNone(body["dismissed_at"])
        self.assertTrue(body["sent_at"])
        self.assertEqual(set(self.marks()[KEY]), {"dismissed_at", "sent_at"})

    def test_validation(self):
        for payload, code in (
            ({"key": "R-101", "mark": "copied"}, "INVALID_FIELD"),
            ({"key": KEY, "mark": "forwarded"}, "INVALID_FIELD"),
            ({"key": KEY, "mark": "archived"}, "INVALID_FIELD"),   # 归档是 sent 派生的，不是一个 mark
            ({"key": KEY, "mark": "dismissed", "on": "yes"}, "INVALID_FIELD"),
            ({"key": KEY, "mark": "dismissed", "reason": "no notes"}, "UNKNOWN_FIELD"),
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


class HistoryTestCase(_Case):
    """§63.9（issue #300）GET /api/recaps/history?key=：存着的每一版 + 正文，只读、fail-open。"""

    def _write_recap(self, doc: dict, key: str = KEY) -> Path:
        path = self.home / "state" / "recap" / "recaps" / (key.replace(":", "_") + ".json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        return path

    def _doc(self) -> dict:
        return {"key": KEY, "version": 2, "generated_at": "2026-08-31T20:40:00Z", "partial": False,
                "quality": "ok", "en": ["Decided: b"], "zh": ["定了：b"],
                "history": [{"version": 1, "generated_at": "2026-08-31T20:20:00Z", "partial": False,
                             "quality": "needs_review", "en": ["Decided: a"], "zh": ["定了：a"]}]}

    def test_current_and_entries_carry_the_text(self):
        self._write_recap(self._doc())
        status, body = get_json(self.port, "/api/recaps/history?key=" + KEY)
        self.assertEqual(status, 200)
        self.assertEqual(body["key"], KEY)
        # §63.10 追记（issue #303）add-only：shape 与两语言粘出去的正文 copy_*
        # （老 daemon 写的记录没有这三个键 → 五行形 + null，键恒在）
        self.assertEqual(body["current"], {"version": 2, "generated_at": "2026-08-31T20:40:00Z",
                                           "partial": False, "quality": "ok",
                                           "en": ["Decided: b"], "zh": ["定了：b"],
                                           "shape": "lines", "copy_en": None, "copy_zh": None})
        self.assertEqual(len(body["entries"]), 1)
        self.assertEqual(body["entries"][0]["version"], 1)
        self.assertEqual(body["entries"][0]["quality"], "needs_review")
        self.assertEqual(body["entries"][0]["en"], ["Decided: a"])
        self.assertEqual((body["history_cap"], body["truncated"]), (5, False))

    def test_entries_are_newest_first_and_textless_ones_are_dropped(self):
        doc = self._doc()
        doc["history"] = [
            {"version": 1, "en": ["Decided: a"], "zh": []},
            {"version": 2, "en": None},                                   # 无正文 = 回退不了，不列
            {"version": 3, "en": ["Decided: c"], "generated_at": 7, "quality": 9, "partial": 1},
        ]
        self._write_recap(doc)
        _status, body = get_json(self.port, "/api/recaps/history?key=" + KEY)
        self.assertEqual([e["version"] for e in body["entries"]], [3, 1])
        # 手改坏的值逐字段消毒（数字 generated_at / quality、非 bool partial 都真实出现过）
        self.assertEqual((body["entries"][0]["generated_at"], body["entries"][0]["quality"]), (None, None))
        self.assertIs(body["entries"][0]["partial"], True)

    def test_an_absent_file_is_an_empty_layer_never_404(self):
        # 新装机 / 还没出过稿 / 已过保留期：页面对这三者一条路 = 没有上一版可看
        status, body = get_json(self.port, "/api/recaps/history?key=" + KEY)
        self.assertEqual(status, 200)
        self.assertEqual(body, {"key": KEY, "current": None, "entries": [],
                                "history_cap": 5, "truncated": False})

    def test_a_corrupt_or_non_object_file_is_an_empty_layer_never_500(self):
        path = self._write_recap({"key": KEY})
        path.write_text("{oops", encoding="utf-8")
        status, body = get_json(self.port, "/api/recaps/history?key=" + KEY)
        self.assertEqual((status, body["current"], body["entries"]), (200, None, []))
        path.write_text("[1, 2]", encoding="utf-8")
        status, body = get_json(self.port, "/api/recaps/history?key=" + KEY)
        self.assertEqual((status, body["current"], body["entries"]), (200, None, []))

    def test_an_oversize_file_is_not_read_at_all(self):
        self._write_recap({"key": KEY, "en": ["x" * 40], "history": [], "pad": "y" * 3_000_000})
        status, body = get_json(self.port, "/api/recaps/history?key=" + KEY)
        self.assertEqual((status, body["current"], body["truncated"]), (200, None, True))

    def test_the_key_is_the_one_400_and_the_client_never_names_a_path(self):
        for query in ("", "?key=", "?key=R-101", "?key=meeting:../../etc/passwd",
                      "?key=" + KEY + "/x"):
            with self.subTest(query=query):
                status, body = get_json(self.port, "/api/recaps/history" + query)
                self.assertEqual(status, 400)
                assert_envelope(self, body, "INVALID_FIELD")

    def test_a_recap_file_is_never_written_by_the_server(self):
        """§63.6 写者分工：这条路只读——回退走 inbox recap_revert → actd → act.recap。"""
        path = self._write_recap(self._doc())
        before = path.read_bytes()
        get_json(self.port, "/api/recaps/history?key=" + KEY)
        self.assertEqual(path.read_bytes(), before)


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

    def test_recap_generate_carries_the_intent_answers(self):
        """§63.11（issue #302）：答案是**字符串列表**（golden `recap_generate-intent` 钉字节形）。"""
        answers = ["split1=drop", "aud=send"]
        status, _body = post_json(self.port, "/api/actions",
                                  {"action": "recap_generate", "meeting_key": KEY,
                                   "answers": answers})
        self.assertEqual(status, 200)
        raw = self._files()[0].read_bytes().decode("utf-8")
        self.assertIn('"answers" : [\n    "split1=drop",\n    "aud=send"\n  ]', raw)
        rec = json.loads(raw)
        self.assertEqual((rec["answers"], rec["via"]), (answers, "web"))

    def test_recap_generate_answers_fail_closed(self):
        for answers in ([], "split1=drop", [1], ["split1"], ["Split1=drop"], ["aud=send too"],
                        ["split1=drop", "split1=keep"], ["aud=send"] * 13, [["split1=drop"]],
                        [{"split1": "drop"}]):
            with self.subTest(answers=answers):
                status, body = post_json(self.port, "/api/actions",
                                         {"action": "recap_generate", "meeting_key": KEY,
                                          "answers": answers})
                self.assertEqual(status, 400)
                assert_envelope(self, body, "INVALID_FIELD")
        self.assertEqual(self._files(), [])

    def test_recap_slack_draft_lands(self):
        status, _body = post_json(self.port, "/api/actions",
                                  {"action": "recap_slack_draft", "meeting_key": KEY, "channel_id": "D0ABCDEF12"})
        self.assertEqual(status, 200)
        rec = json.loads(self._files()[0].read_text(encoding="utf-8"))
        self.assertEqual(rec["channel_id"], "D0ABCDEF12")

    def test_recap_revert_lands_with_an_integer_version(self):
        """§63.9（issue #300）：第一个带整数值的 inbox 动作——字节形多一支值类型（golden 钉死）。"""
        status, body = post_json(self.port, "/api/actions",
                                 {"action": "recap_revert", "meeting_key": KEY, "version": 2})
        self.assertEqual(status, 200)
        self.assertEqual(body["action"], "recap_revert")
        raw = self._files()[0].read_bytes().decode("utf-8")
        self.assertIn('"version" : 2', raw)          # 裸十进制，不是 "2"
        rec = json.loads(raw)
        self.assertEqual((rec["meeting_key"], rec["version"], rec["via"]), (KEY, 2, "web"))

    def test_recap_revert_validation_fails_closed(self):
        for payload, code in (
            ({"action": "recap_revert", "meeting_key": KEY}, "INVALID_FIELD"),
            ({"action": "recap_revert", "meeting_key": KEY, "version": 0}, "INVALID_FIELD"),
            ({"action": "recap_revert", "meeting_key": KEY, "version": -1}, "INVALID_FIELD"),
            ({"action": "recap_revert", "meeting_key": KEY, "version": True}, "INVALID_FIELD"),
            ({"action": "recap_revert", "meeting_key": KEY, "version": "2"}, "INVALID_FIELD"),
            ({"action": "recap_revert", "meeting_key": KEY, "version": 2.0}, "INVALID_FIELD"),
            ({"action": "recap_revert", "meeting_key": KEY, "version": 10 ** 9}, "INVALID_FIELD"),
            ({"action": "recap_revert", "meeting_key": "R-1", "version": 2}, "INVALID_FIELD"),
            ({"action": "recap_revert", "meeting_key": KEY, "version": 2, "note": "x"}, "UNKNOWN_FIELD"),
        ):
            with self.subTest(payload=payload):
                status, body = post_json(self.port, "/api/actions", payload)
                self.assertEqual(status, 400)
                assert_envelope(self, body, code)
        self.assertEqual(self._files(), [])

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
