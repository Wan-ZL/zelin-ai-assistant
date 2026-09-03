"""feedback — snapshot builders and the retry gate split out in P3b (§29).

Pins: the ``str or None`` field shape, MS- snapshots (non-MS id, missing job,
member title synthesis, empty status), the requirement snapshot, a crashing
registry degrading to kind=unknown, the pending/too-young gate, the due-record
reader on corrupt / terminal / young / old files, and record_feedback's text
coercion.
"""
import datetime as _dt
import json
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first

from act.lib import feedback, registry
from act.lib.registry import Requirement


class SnapshotHelpersTestCase(unittest.TestCase):
    def test_nonempty(self):
        self.assertIsNone(feedback._nonempty(None))
        self.assertIsNone(feedback._nonempty(""))
        self.assertEqual(feedback._nonempty(0), None)
        self.assertEqual(feedback._nonempty("x"), "x")
        self.assertEqual(feedback._nonempty(7), "7")

    def test_merge_snapshot_shapes(self):
        self.assertIsNone(feedback._merge_snapshot("R-1"))
        with mock.patch.object(feedback.merge_review, "load_job", return_value=None):
            self.assertIsNone(feedback._merge_snapshot("MS-9"))
        with mock.patch.object(feedback.merge_review, "load_job",
                               return_value={"ids": ["R-1", 2], "status": ""}):
            snap = feedback._merge_snapshot("MS-9")
        self.assertEqual(snap, {"id": "MS-9", "kind": "merge_suggestion",
                                "type": "merge_suggestion",
                                "title": "merge suggestion: R-1 + 2", "status": None})
        with mock.patch.object(feedback.merge_review, "load_job",
                               return_value={"status": "pending"}):
            snap = feedback._merge_snapshot("MS-9")
        self.assertEqual((snap["title"], snap["status"]), ("merge suggestion: ", "pending"))

    def test_merge_review_absent_means_no_merge_snapshot(self):
        with mock.patch.object(feedback, "merge_review", None):
            self.assertIsNone(feedback._merge_snapshot("MS-1"))

    def test_req_snapshot(self):
        req = Requirement(id="R-5", title="", type="code", status="review")
        self.assertEqual(feedback._req_snapshot("R-5", req),
                         {"id": "R-5", "kind": "requirement", "type": "code",
                          "title": None, "status": "review"})

    def test_snapshot_survives_a_crashing_registry(self):
        with mock.patch.object(registry, "load", side_effect=RuntimeError("db")):
            self.assertEqual(feedback._snapshot("R-404")["kind"], "unknown")


class RetryGateTestCase(unittest.TestCase):
    NOW = _dt.datetime(2026, 9, 2, 12, 0, tzinfo=_dt.timezone.utc)

    def test_is_pending_record(self):
        self.assertTrue(feedback._is_pending_record({"id": "a", "uploaded": None}))
        self.assertTrue(feedback._is_pending_record({"id": "a"}))
        self.assertFalse(feedback._is_pending_record({"id": "a", "uploaded": False}))
        self.assertFalse(feedback._is_pending_record({"id": "a", "uploaded": True}))
        self.assertFalse(feedback._is_pending_record({"uploaded": None}))
        self.assertFalse(feedback._is_pending_record(["not", "dict"]))

    def test_too_young(self):
        young = (self.NOW - _dt.timedelta(seconds=feedback.MIN_RETRY_AGE_SECONDS - 1))
        old = (self.NOW - _dt.timedelta(seconds=feedback.MIN_RETRY_AGE_SECONDS))
        fmt = "%Y-%m-%dT%H:%M:%SZ"
        self.assertTrue(feedback._too_young({"ts": young.strftime(fmt)}, self.NOW))
        self.assertFalse(feedback._too_young({"ts": old.strftime(fmt)}, self.NOW))
        self.assertFalse(feedback._too_young({"ts": "garbage"}, self.NOW))
        self.assertFalse(feedback._too_young({}, self.NOW))

    def test_due_record_reader(self):
        feedback.FEEDBACK_DIR.mkdir(parents=True, exist_ok=True)
        corrupt = feedback.FEEDBACK_DIR / "corrupt.json"
        corrupt.write_text("{", encoding="utf-8")
        self.assertIsNone(feedback._due_record(corrupt, self.NOW))
        terminal = feedback.FEEDBACK_DIR / "terminal.json"
        terminal.write_text(json.dumps({"id": "t", "uploaded": True}), encoding="utf-8")
        self.assertIsNone(feedback._due_record(terminal, self.NOW))
        due = feedback.FEEDBACK_DIR / "due.json"
        due.write_text(json.dumps({"id": "d", "ts": "2020-01-01T00:00:00Z"}), encoding="utf-8")
        self.assertEqual(feedback._due_record(due, self.NOW)["id"], "d")
        self.assertIsNone(feedback._due_record(feedback.FEEDBACK_DIR / "missing.json", self.NOW))
        for p in (corrupt, terminal, due):
            p.unlink()

    def test_clean_text_and_cfg_or_load(self):
        self.assertEqual(feedback._clean_text(None), "")
        self.assertEqual(feedback._clean_text("  hi "), "hi")
        sentinel = object()
        self.assertIs(feedback._cfg_or_load(sentinel), sentinel)
        with mock.patch.object(feedback.config, "load_config", return_value="loaded"):
            self.assertEqual(feedback._cfg_or_load(None), "loaded")


class RetryPendingTailsTestCase(unittest.TestCase):
    def test_unreadable_dir_and_upload_crash(self):
        with mock.patch.object(type(feedback.FEEDBACK_DIR), "glob", side_effect=OSError("denied")):
            self.assertEqual(feedback.retry_pending(cfg=object()), 0)
        feedback.FEEDBACK_DIR.mkdir(parents=True, exist_ok=True)
        due = feedback.FEEDBACK_DIR / "crash.json"
        due.write_text(json.dumps({"id": "c", "ts": "2020-01-01T00:00:00Z"}), encoding="utf-8")
        try:
            with mock.patch.object(feedback, "_attempt_upload", side_effect=RuntimeError("boom")):
                self.assertEqual(feedback.retry_pending(cfg=object()), 0)
        finally:
            due.unlink()


if __name__ == "__main__":
    unittest.main()
