"""server/inbox_writer.py 字段闸门的逐条判例（CONTRACT §49；wire 真源
docs/design/inbox-actions.md）。

test_server_actions 走真 server + golden；这里直接调 write_action / 小件，
把此前没有判例的 fail-closed 分支逐个钉死：comment 非字符串、merge_review
不足两条、ids 重复、feedback publish 非 bool / 双空 / images 非绝对路径、
capture images 超上限 / preset 无 mode、set_title 归一后越界、
import_claude_sessions 空表，以及 Mac 字节形序列化的每种值类型。
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests import TMP_HOME  # noqa: F401

from server import inbox_writer as iw
from server.errors import InvalidFieldError, UnknownFieldError


class _Home(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="zai-inbox-gate-"))

    def _write(self, payload: dict) -> dict:
        return iw.write_action(payload, home=self.home)

    def _inbox_files(self) -> list:
        inbox = self.home / "state" / "inbox"
        return sorted(inbox.glob("*.json")) if inbox.is_dir() else []

    def _rejects(self, payload: dict, needle: str, exc=InvalidFieldError):
        before = self._inbox_files()
        with self.assertRaises(exc) as cm:
            self._write(payload)
        self.assertIn(needle, cm.exception.message)
        self.assertEqual(self._inbox_files(), before)   # 毒值绝不落盘


class CardVerbGateTestCase(_Home):
    def test_comment_must_be_string_or_null(self):
        self._rejects({"action": "approve", "id": "R-1", "comment": {"x": 1}},
                      "string or null")

    def test_comment_action_needs_text(self):
        self._rejects({"action": "comment", "id": "R-1", "comment": "   "},
                      "requires text")
        self._rejects({"action": "comment", "id": "R-1"}, "requires text")

    def test_rework_may_carry_empty_comment(self):
        res = self._write({"action": "rework", "id": "R-1", "comment": ""})
        self.assertEqual(res["action"], "rework")

    def test_unsafe_id_rejected(self):
        self._rejects({"action": "approve", "id": "../x"}, "safe id")

    def test_unknown_action_and_non_string_action(self):
        self._rejects({"action": "launch_nukes"}, "unknown action")
        self._rejects({"action": 7}, "unknown action")


class IdListGateTestCase(_Home):
    def test_merge_review_needs_two_ids(self):
        self._rejects({"action": "merge_review", "ids": ["R-1"]}, "at least 2")

    def test_merge_review_rejects_duplicates(self):
        self._rejects({"action": "merge_review", "ids": ["R-1", "R-1"]}, "duplicates")

    def test_merge_review_rejects_non_list_and_unsafe_members(self):
        self._rejects({"action": "merge_review", "ids": "R-1"}, "list of safe ids")
        self._rejects({"action": "merge_review", "ids": ["R-1", "../x"]}, "list of safe ids")

    def test_merge_force_dedups_and_requires_primary_in_ids(self):
        res = self._write({"action": "merge_force", "ids": ["R-1", "R-1", "R-2"],
                           "primary": "R-2"})
        self.assertEqual(res["action"], "merge_force")
        self._rejects({"action": "merge_force", "ids": ["R-1", "R-2"], "primary": "R-9"},
                      "primary among them")

    def test_import_sessions_needs_one(self):
        self._rejects({"action": "import_claude_sessions", "session_ids": []},
                      "at least 1")


class FeedbackGateTestCase(_Home):
    def test_publish_must_be_bool(self):
        self._rejects({"action": "feedback", "text": "x", "publish": "yes"},
                      "publish must be a boolean")

    def test_text_and_images_both_empty(self):
        self._rejects({"action": "feedback", "text": "  "}, "needs text or images")
        self._rejects({"action": "feedback"}, "needs text or images")

    def test_images_must_be_absolute_and_distinct(self):
        self._rejects({"action": "feedback", "images": ["rel.png"]}, "absolute paths")
        self._rejects({"action": "feedback", "images": ["/a.png", "/a.png"]}, "duplicates")

    def test_images_alone_are_enough(self):
        res = self._write({"action": "feedback", "images": ["/a.png"]})
        self.assertEqual(res["action"], "feedback")


class CaptureGateTestCase(_Home):
    def test_images_capped(self):
        imgs = ["/p%d.png" % i for i in range(iw._CAPTURE_IMAGES_MAX + 1)]
        self._rejects({"action": "capture", "text": "t", "images": imgs}, "at most")

    def test_preset_requires_run_mode(self):
        self._rejects({"action": "capture", "text": "t", "preset": "proposals_triage"},
                      'with mode:"run"')
        self._rejects({"action": "capture", "text": "t", "mode": "run", "preset": "other"},
                      'with mode:"run"')
        res = self._write({"action": "capture", "text": "t", "mode": "run",
                           "preset": "proposals_triage"})
        self.assertEqual(res["action"], "capture")

    def test_mode_other_than_run_rejected(self):
        self._rejects({"action": "capture", "text": "t", "mode": "walk"}, 'mode:"run"')

    def test_empty_text_rejected(self):
        self._rejects({"action": "capture", "text": " "}, "must not be empty")


class TitleAndSplitTestCase(_Home):
    def test_title_normalized_and_bounded(self):
        self._rejects({"action": "set_title", "id": "R-1", "title": "x" * 65}, "1..64")
        self._rejects({"action": "set_title", "id": "R-1", "title": "   "}, "must not be empty")
        res = self._write({"action": "set_title", "id": "R-1", "title": " a　 b "})
        self.assertEqual(res["action"], "set_title")

    def test_split_note_requires_note_ts_string(self):
        self._rejects({"action": "split_note", "id": "R-1", "note_ts": 5}, "must be a string")


class AllowedFieldsTestCase(unittest.TestCase):
    def test_card_verbs_allow_exactly_three_plus_actor_for_comment(self):
        self.assertEqual(iw._allowed_fields("approve"), {"action", "id", "comment"})
        self.assertEqual(iw._allowed_fields("comment"), {"action", "id", "comment", "actor"})

    def test_special_verbs_union_required_and_optional(self):
        self.assertEqual(iw._allowed_fields("capture"),
                         {"action", "text", "mode", "images", "preset", "actor"})
        self.assertEqual(iw._allowed_fields("weekly_digest_now"), {"action"})

    def test_unknown_field_lists_sorted_names(self):
        with self.assertRaises(UnknownFieldError) as cm:
            iw._reject_unknown_fields("approve", {"action": "approve", "z": 1, "a": 2})
        self.assertEqual(cm.exception.details, {"fields": ["a", "z"]})


class MacJsonBytesTestCase(unittest.TestCase):
    def test_every_value_type_renders_the_mac_shape(self):
        rec = {"b": True, "f": False, "n": None, "s": "a/b", "e": [], "l": ["x", ["y"]]}
        out = iw.mac_json_bytes(rec).decode("utf-8")
        self.assertIn('"b" : true', out)
        self.assertIn('"f" : false', out)
        self.assertIn('"n" : null', out)
        self.assertIn('"s" : "a\\/b"', out)
        self.assertIn('"e" : [\n\n  ]', out)
        self.assertIn('"l" : [\n    "x",\n    [\n      "y"\n    ]\n  ]', out)
        self.assertFalse(out.endswith("\n"))

    def test_unsupported_type_is_loud(self):
        with self.assertRaises(TypeError):
            iw.mac_json_bytes({"d": {"nested": 1}})


if __name__ == "__main__":
    unittest.main()
