"""act.report.build_report — the usage report text pinned byte-for-byte.

A synthetic event log exercising every section (feature frequency 7d/30d,
hour/weekday heat, rework > 1, resume failures, exhausted resumes, the
approval funnel with a >50% rejection rate, dispatch→review durations, a
repetition storm) is rendered with a frozen clock and a fixed local timezone;
the expected text lives in ``tests/fixtures/report/usage.golden.txt`` and was
captured from the pre-P3b implementation. Any drift in ordering, formatting or
thresholds flips this test.
"""
import datetime as _dt
import os
import time
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first

from act import report

GOLDEN = Path(__file__).parent / "fixtures" / "report" / "usage.golden.txt"
NOW = _dt.datetime(2026, 9, 2, 12, 0, 0, tzinfo=_dt.timezone.utc)


def _ev(day_offset, hour, event, **props):
    ts = (NOW - _dt.timedelta(days=day_offset)).replace(hour=hour, minute=0, second=0)
    d = {"ts": ts.strftime("%Y-%m-%dT%H:%M:%SZ"), "event": event}
    d.update(props)
    return d


def synthetic_events():
    events = [
        _ev(1, 9, "card_sent", req="R-1"), _ev(1, 9, "card_sent", req="R-2"),
        _ev(1, 10, "inbox_approve", req="R-1"), _ev(1, 10, "inbox_reject", req="R-2"),
        _ev(2, 10, "inbox_reject", req="R-3"), _ev(2, 11, "inbox_trash", req="R-4"),
        _ev(20, 15, "inbox_accept", req="R-1"),
        _ev(3, 8, "inbox_rework", req="R-5"), _ev(3, 9, "inbox_rework", req="R-5"),
        _ev(3, 9, "inbox_rework", req="R-6"),
        _ev(4, 8, "auto_resume", req="R-7", ok=False), _ev(4, 8, "resume_launch", req="R-7", ok=False),
        _ev(4, 9, "auto_resume", req="R-8", ok=True),
        _ev(5, 8, "auto_resume_exhausted", req="R-9"),
        _ev(6, 8, "dispatch", req="R-1"),
        {"ts": (NOW - _dt.timedelta(days=6)).replace(hour=9, minute=30).strftime("%Y-%m-%dT%H:%M:%SZ"),
         "event": "review_promoted", "req": "R-1"},
        _ev(6, 8, "review_promoted", req="R-never-dispatched"),
        _ev(25, 22, "dispatch", req="R-old"),
        {"ts": "not-a-timestamp", "event": "junk"},
        {"event": "no_ts"},
    ]
    storm_base = (NOW - _dt.timedelta(days=2)).replace(hour=14, minute=0, second=0)
    for k in range(5):
        events.append({"ts": (storm_base + _dt.timedelta(minutes=10 * k)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                       "event": "resume_launch", "req": "R-storm"})
    return events


class _Clock(_dt.datetime):
    @classmethod
    def now(cls, tz=None):
        if tz is None:
            return NOW.astimezone().replace(tzinfo=None)
        return NOW.astimezone(tz)


@unittest.skipUnless(hasattr(time, "tzset"), "fixed local timezone needs time.tzset (POSIX)")
class ReportGoldenTestCase(unittest.TestCase):
    def setUp(self):
        self._tz = os.environ.get("TZ")
        os.environ["TZ"] = "Asia/Shanghai"
        time.tzset()

    def tearDown(self):
        if self._tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = self._tz
        time.tzset()

    def render(self, days=30):
        with mock.patch.object(report, "read_events", return_value=iter(synthetic_events())), \
                mock.patch.object(report._dt, "datetime", _Clock):
            return report.build_report(days=days)

    def test_matches_golden(self):
        text = self.render()
        self.assertEqual(text, GOLDEN.read_text(encoding="utf-8"))

    def test_report_covers_every_section(self):
        text = self.render()
        for marker in ("## 1 · 功能使用频次", "## 2 · 时段热力", "## 3 · 健康信号", "多次打回",
                       "自动恢复失败", "恢复放弃", "拒绝率 >50%", "执行时长", "## 4 · 重复风暴",
                       "R-storm · resume_launch × 5"):
            self.assertIn(marker, text)

    def test_empty_log_is_healthy(self):
        with mock.patch.object(report, "read_events", return_value=iter([])), \
                mock.patch.object(report._dt, "datetime", _Clock):
            text = report.build_report(days=7)
        self.assertIn("事件数 0", text)
        self.assertIn("（无 —— 健康）", text)
        self.assertNotIn("拒绝率", text)


class ReportCliTestCase(unittest.TestCase):
    def test_main_prints_the_report_for_the_requested_window(self):
        with mock.patch.object(report, "build_report", return_value="REPORT") as br, \
                mock.patch("builtins.print") as pr:
            self.assertEqual(report.main(["--days", "7"]), 0)
        br.assert_called_once_with(days=7)
        pr.assert_called_once_with("REPORT")

    def test_helpers_on_edge_inputs(self):
        self.assertEqual(report._bar(0, 0), "")
        self.assertEqual(report._bar(0, 5), "")
        self.assertEqual(report._bar(1, 100), report._BAR)
        self.assertIsNone(report._parse_ts("bad"))
        self.assertIsNone(report._parse_ts(None))
        self.assertIsNone(report._first_storm([]))
        t0 = NOW
        four = [t0 + _dt.timedelta(minutes=m) for m in (0, 10, 20, 30)]
        self.assertEqual(report._first_storm(list(reversed(four))), (4, t0))
        spread = [t0 + _dt.timedelta(hours=h) for h in range(5)]
        self.assertIsNone(report._first_storm(spread))
        self.assertEqual(report._multi_rework([{"event": "inbox_rework", "req": None}]), {})
        self.assertFalse(report._is_resume_failure({"event": "auto_resume", "ok": True, "req": "r"}))
        self.assertTrue(report._is_resume_failure({"event": "resume_launch", "ok": False, "req": "r"}))
        self.assertEqual(report._funnel_lines(report.Counter()), ["审批漏斗: 发卡 0 → 批准 0 · 拒绝 0 · 删除 0 · 验收 0"])
        self.assertEqual(report._duration_lines([]), [])


if __name__ == "__main__":
    unittest.main()
