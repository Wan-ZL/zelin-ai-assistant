"""oneonone — first_seen / last_activity dating and the prep page buckets (§40).

Characterization net for the P3b split: date extraction from source rows
(junk rows skipped), card.sent_at and the three execution stamps, min/max
selection, the ready/in-flight/not-ready bucketing incl. the 7-day delivered
window (undated delivered counts as recent; older delivered cards drop out of
every bucket), the rendered page sections, and write_prep's path.
"""
import datetime as _dt
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first
from tests import store2_testkit

from act import oneonone
from act.lib import registry
from act.lib.registry import Requirement, State

TODAY = _dt.date(2026, 9, 2)


class DatingTestCase(unittest.TestCase):
    def test_source_and_field_dates(self):
        req = Requirement(id="a", sources=[{"date": "2026-01-05"}, "junk", {"date": "nope"}, {"date": None}],
                          card={"sent_at": "2026-02-01T10:00:00Z"},
                          execution={"dispatched_at": "2026-03-01", "last_resume_at": "bad",
                                     "last_rework_at": "2026-04-01"})
        self.assertEqual(oneonone._source_dates(req), [_dt.date(2026, 1, 5)])
        self.assertEqual(oneonone._field_dates(req.card, ("sent_at",)), [_dt.date(2026, 2, 1)])
        self.assertEqual(oneonone._field_dates(None, ("sent_at",)), [])
        self.assertEqual(oneonone.first_seen(req), _dt.date(2026, 1, 5))
        self.assertEqual(oneonone.last_activity(req), _dt.date(2026, 4, 1))

    def test_first_seen_ignores_resume_and_rework(self):
        req = Requirement(id="a", execution={"last_resume_at": "2026-01-01", "dispatched_at": "2026-05-01"})
        self.assertEqual(oneonone.first_seen(req), _dt.date(2026, 5, 1))
        self.assertEqual(oneonone.last_activity(req), _dt.date(2026, 5, 1))

    def test_undated_card(self):
        req = Requirement(id="a")
        self.assertIsNone(oneonone.first_seen(req))
        self.assertIsNone(oneonone.last_activity(req))
        self.assertEqual(oneonone._age_str(req, TODAY), "")
        aged = Requirement(id="b", sources=[{"date": "2026-09-05"}])
        self.assertEqual(oneonone._age_str(aged, TODAY), "")       # future date → blank
        aged = Requirement(id="b", sources=[{"date": "2026-08-30"}])
        self.assertEqual(oneonone._age_str(aged, TODAY), "3 天")


class BucketTestCase(unittest.TestCase):
    def test_bucket_rules(self):
        review = Requirement(id="r", status=State.REVIEW.value)
        fresh = Requirement(id="f", status=State.DELIVERED.value, sources=[{"date": "2026-08-27"}])
        undated = Requirement(id="u", status=State.DELIVERED.value)
        old = Requirement(id="o", status=State.DELIVERED.value, sources=[{"date": "2026-08-01"}])
        running = Requirement(id="x", status=State.EXECUTING.value)
        approved = Requirement(id="ap", status=State.APPROVED.value)
        proposal = Requirement(id="p", status=State.CARD_SENT.value)
        debt = Requirement(id="d", status=State.DETECTED.value)
        raising = Requirement(id="ra", status=State.RAISING.value)
        ready, in_flight, not_ready = oneonone._bucket(
            [review, fresh, undated, old, running, approved, proposal, debt, raising], TODAY)
        self.assertEqual([r.id for r in ready], ["r", "f", "u"])
        self.assertEqual([r.id for r in in_flight], ["x", "ap", "p"])
        self.assertEqual([r.id for r in not_ready], ["d"])
        self.assertTrue(oneonone._delivered_this_week(fresh, TODAY))
        self.assertFalse(oneonone._delivered_this_week(old, TODAY))
        boundary = Requirement(id="b", status=State.DELIVERED.value, sources=[{"date": "2026-08-26"}])
        self.assertTrue(oneonone._is_ready(boundary, TODAY))      # exactly 7 days
        self.assertFalse(oneonone._is_ready(debt, TODAY))


class PageTestCase(unittest.TestCase):
    def setUp(self):
        store2_testkit.use_backend(self, "yaml")

    def test_build_prep_sections_and_ledger(self):
        registry.save(Requirement(id="P-1", title="review me", status=State.REVIEW.value,
                                  notes="[MANAGER-OWES] send the doc"))
        registry.save(Requirement(id="P-2", title="binned", status=State.TRASHED.value))
        registry.save(Requirement(id="P-3", title="dup", status="merged_into:P-1"))
        registry.save(Requirement(id="P-4", title="", status=State.DETECTED.value))
        with mock.patch.object(oneonone.failures, "ui_lang", return_value="zh"):
            page = oneonone.build_prep(TODAY)
        self.assertIn("# 1:1 prep · 2026-09-02", page)
        self.assertIn("## ✅ Ready（可汇报：待验收 + 本周交付，1）", page)
        self.assertIn("- 🔍 P-1 · review me （待验收）", page)
        self.assertIn("## 🏃 In-flight（进行中，0）\n- （无）", page)
        self.assertIn("- 📡 P-4 · (untitled) （潜在任务）", page)
        self.assertIn("- P-1 · [MANAGER-OWES] send the doc", page)
        self.assertNotIn("binned", page)
        self.assertNotIn("dup", page)

    def test_write_prep_and_main(self):
        with mock.patch.object(oneonone.analytics, "log_event") as le:
            path = oneonone.write_prep(TODAY)
        self.assertEqual(path.name, "prep-2026-09-02.md")
        self.assertTrue(path.read_text(encoding="utf-8").startswith("# 1:1 prep"))
        le.assert_called_once_with("oneonone_prep", path=str(path))
        with mock.patch.object(oneonone, "write_prep", return_value=path), \
                mock.patch("builtins.print") as pr:
            self.assertEqual(oneonone.main([]), 0)
        pr.assert_called_once_with(str(path))


if __name__ == "__main__":
    unittest.main()
