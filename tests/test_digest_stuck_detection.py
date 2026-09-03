"""act/digest 「卡住」判定 (§17 sections 3): why an executing card is listed
as stuck, and the ISO timestamp parser under it.

Pinned (P3 mutation net — the reasons had no killing test):
- resume_exhausted wins; last_resume_ok is False (and only ``False``) next;
- dispatched more than STUCK_AFTER_HOURS ago -> "已执行 Nh 未交付" with the
  whole hours; exactly at the threshold is NOT stuck (strict >);
- no execution / unparseable dispatched_at -> None;
- ``_parse_iso`` accepts only the canonical Z form.
"""
import datetime as _dt
import unittest

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import digest
from act.lib.registry import Requirement, State

_NOW = _dt.datetime(2026, 7, 13, 12, 0, 0, tzinfo=_dt.timezone.utc)


def _req(**execution) -> Requirement:
    return Requirement(id="R-1", title="t", status=State.EXECUTING.value,
                       execution=execution or None)


class ParseIsoTestCase(unittest.TestCase):
    def test_canonical_form_parses_as_utc(self):
        ts = digest._parse_iso("2026-07-13T10:00:00Z")
        self.assertEqual(ts, _dt.datetime(2026, 7, 13, 10, tzinfo=_dt.timezone.utc))

    def test_empty_and_malformed_are_none(self):
        self.assertIsNone(digest._parse_iso(None))
        self.assertIsNone(digest._parse_iso(""))
        self.assertIsNone(digest._parse_iso("2026-07-13 10:00"))
        self.assertIsNone(digest._parse_iso("yesterday"))


class IsStuckTestCase(unittest.TestCase):
    def test_no_execution_is_not_stuck(self):
        self.assertIsNone(digest._is_stuck(_req(), _NOW))

    def test_resume_exhausted_first(self):
        req = _req(resume_exhausted=True, last_resume_ok=False,
                   dispatched_at="2026-07-01T00:00:00Z")
        self.assertEqual(digest._is_stuck(req, _NOW), "自动恢复已放弃，需人工")

    def test_last_resume_failed(self):
        self.assertEqual(digest._is_stuck(_req(last_resume_ok=False), _NOW),
                         "上次自动恢复失败")
        # only a literal False counts — None/0 do not
        self.assertIsNone(digest._is_stuck(_req(last_resume_ok=None), _NOW))
        self.assertIsNone(digest._is_stuck(_req(last_resume_ok=0), _NOW))

    def test_overdue_dispatch_reports_whole_hours(self):
        dispatched = (_NOW - _dt.timedelta(hours=30, minutes=40)).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
        self.assertEqual(digest._is_stuck(_req(dispatched_at=dispatched), _NOW),
                         "已执行 30h 未交付")

    def test_threshold_is_strict(self):
        at = (_NOW - _dt.timedelta(hours=digest.STUCK_AFTER_HOURS)).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
        self.assertIsNone(digest._is_stuck(_req(dispatched_at=at), _NOW))
        just_over = (_NOW - _dt.timedelta(hours=digest.STUCK_AFTER_HOURS, seconds=1)
                     ).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.assertEqual(digest._is_stuck(_req(dispatched_at=just_over), _NOW),
                         f"已执行 {digest.STUCK_AFTER_HOURS}h 未交付")

    def test_unparseable_dispatched_at_is_not_stuck(self):
        self.assertIsNone(digest._is_stuck(_req(dispatched_at="soon"), _NOW))


if __name__ == "__main__":
    unittest.main()
