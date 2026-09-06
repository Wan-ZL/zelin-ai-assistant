"""scripts/insights_report — the derived views and the issue body pinned
byte-for-byte (§16 Usage Insights).

A synthetic row set exercising every view (funnel with a device that only
dispatched, install fallback off, ingest scans/skips with reasons, ok-flag
events, dispatch failures, configured-but-cardless devices, used-once events,
D2/D7 retention, legacy aggregate incl. non-dict rows and short timestamps) is
rendered with a frozen clock; ``tests/fixtures/insights/views.golden.json``
holds the view dicts, ``body.golden.md`` the assembled body. Captured from the
pre-P3b script, so any drift in counting, ordering or wording flips this test.
"""
import datetime as dt
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import insights_report as ir  # noqa: E402

FIXTURES = REPO / "tests" / "fixtures" / "insights"
NOW = dt.datetime(2026, 9, 2, 12, 0, 0, tzinfo=dt.timezone.utc)


def _row(device, event, day, hour=10, version="1.0.0", props=None, client_ts=None):
    ts = (NOW - dt.timedelta(days=day)).replace(hour=hour, minute=0, second=0)
    row = {"device_id": device, "event": event, "app_version": version,
           "inserted_at": ts.strftime("%Y-%m-%dT%H:%M:%SZ"), "props": props or {}}
    if client_ts:
        row["client_ts"] = client_ts
    return row


def synthetic_rows():
    rows = [
        _row("d1", "feature_first_reach", 20, props={"feature": "app_launch"}),
        _row("d1", "feature_first_reach", 19, props={"feature": "ingest_configured"}),
        _row("d1", "milestone_first_card", 18),
        _row("d1", "inbox_approve", 17, props={"ok": True}),
        _row("d1", "dispatch", 16),
        _row("d1", "radar_scan", 15, props={"source": "gmail"}),
        _row("d1", "radar_skip", 15, props={"source": "gmail", "reason": "no_credentials"}),
        _row("d1", "radar_skip", 14, props={"source": "gmail", "reason": "no_credentials"}),
        _row("d1", "radar_skip", 14, props={"source": "gmail", "reason": "disabled"}),
        _row("d1", "radar_skip", 13, props={"reason": ""}),
        _row("d1", "dispatch_failed", 12, props={"ok": False}),
        _row("d1", "resume_launch", 11, props={"ok": False, "level": "detailed"}),
        _row("d1", "resume_launch", 10, props={"ok": True, "level": "basic"}),
        _row("d2", "feature_first_reach", 9, props={"feature": "app_launch"}),
        _row("d2", "feature_first_reach", 9, props={"feature": "ingest_configured"}),
        _row("d2", "ask_answered", 8, props={"ok": True}),
        _row("d3", "dispatch", 7),                                   # only a dispatch row
        _row("d4", "card_sent", 30, client_ts="2026-07-01T00:00:00Z"),  # client_ts wins
        _row("d4", "card_sent", 1),
        _row("", "card_sent", 1),                                    # no device → skipped
        {"event": "orphan", "inserted_at": "2026-09"},              # short ts, no device
        "junk",
        _row("d5", "feature_first_reach", 2, props={"feature": "ingest_configured"}, version=None),
    ]
    return rows


class _Clock(dt.datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW if tz else NOW.replace(tzinfo=None)


class ViewsGoldenTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.golden = json.loads((FIXTURES / "views.golden.json").read_text(encoding="utf-8"))
        cls.rows = synthetic_rows()

    def test_views(self):
        self.assertEqual(json.loads(json.dumps(ir.funnel(self.rows))), self.golden["funnel"])
        self.assertEqual(json.loads(json.dumps(ir.path_failures(self.rows))), self.golden["path_failures"])
        self.assertEqual(json.loads(json.dumps(ir.abandonment(self.rows))), self.golden["abandonment"])
        self.assertEqual(json.loads(json.dumps(ir.retention(self.rows))), self.golden["retention"])
        self.assertEqual(json.loads(json.dumps(ir.aggregate(self.rows))), self.golden["aggregate"])

    def test_install_fallback_when_no_app_launch(self):
        rows = [r for r in self.rows if not (isinstance(r, dict) and r.get("props", {}).get("feature") == "app_launch")]
        self.assertEqual(json.loads(json.dumps(ir.funnel(rows))), self.golden["funnel_fallback"])

    def test_empty_views(self):
        self.assertEqual(json.loads(json.dumps(ir.retention([]))), self.golden["retention_empty"])
        self.assertEqual(json.loads(json.dumps(ir.path_failures([]))), self.golden["path_failures_empty"])
        self.assertEqual(ir.render_reliability(ir.path_failures([])), self.golden["render_reliability_empty"])


class BodyGoldenTestCase(unittest.TestCase):
    def test_full_body(self):
        rows = synthetic_rows()
        with mock.patch.object(ir.dt, "datetime", _Clock):
            body = ir.build_body(ir.aggregate(rows), "- fix one\n- fix two", 30,
                                 funnel_v=ir.funnel(rows), failures_v=ir.path_failures(rows),
                                 abandon_v=ir.abandonment(rows), retention_v=ir.retention(rows))
        self.assertEqual(body, (FIXTURES / "body.golden.md").read_text(encoding="utf-8"))

    def test_notice_bodies(self):
        golden = json.loads((FIXTURES / "views.golden.json").read_text(encoding="utf-8"))
        with mock.patch.object(ir.dt, "datetime", _Clock):
            self.assertEqual(ir.build_body(None, None, 7, missing_key=True), golden["body_missing_key"])
            self.assertEqual(ir.build_body(None, None, 7, error="URLError: boom"), golden["body_error"])
            agg = ir.aggregate([])
            self.assertEqual(ir.build_body(agg, None, 7), golden["body_no_views"])


class MainStagesTestCase(unittest.TestCase):
    """main(): missing key notice / fetch failure / no-change gate / full run."""

    def setUp(self):
        import tempfile
        self.out = Path(tempfile.mkdtemp(prefix="insights-main-")) / "body.md"

    def _env(self, **extra):
        base = {"SUPABASE_INSIGHTS_KEY": "k", "ANTHROPIC_API_KEY": "", "INSIGHTS_PREV_TOTAL": "",
                "INSIGHTS_DAYS": "7"}
        base.update(extra)
        return mock.patch.dict("os.environ", base, clear=False)

    def test_missing_key_writes_notice(self):
        with self._env(SUPABASE_INSIGHTS_KEY=""), mock.patch("builtins.print"):
            self.assertEqual(ir.main(["--out", str(self.out)]), 0)
        self.assertIn("Not configured", self.out.read_text(encoding="utf-8"))

    def test_fetch_failure_writes_error_and_exits_2(self):
        with self._env(), mock.patch.object(ir, "fetch_rows", side_effect=RuntimeError("boom")), \
                mock.patch("builtins.print"):
            self.assertEqual(ir.main(["--out", str(self.out)]), 2)
        self.assertIn("RuntimeError: boom", self.out.read_text(encoding="utf-8"))

    def test_no_change_gate_writes_nothing(self):
        rows = synthetic_rows()
        total = ir.aggregate(rows)["total"]
        with self._env(INSIGHTS_PREV_TOTAL=str(total)), \
                mock.patch.object(ir, "fetch_rows", return_value=rows), mock.patch("builtins.print"):
            self.assertEqual(ir.main(["--out", str(self.out)]), 0)
        self.assertFalse(self.out.exists())

    def test_full_run_with_insights(self):
        rows = synthetic_rows()
        with self._env(ANTHROPIC_API_KEY="a"), mock.patch.object(ir, "fetch_rows", return_value=rows), \
                mock.patch.object(ir, "analyze", return_value="- do this") as an, mock.patch("builtins.print"):
            self.assertEqual(ir.main(["--out", str(self.out)]), 0)
        an.assert_called_once()
        body = self.out.read_text(encoding="utf-8")
        self.assertIn("- do this", body)
        self.assertIn(f"**Totals:** {ir.aggregate(rows)['total']} events", body)
        self.assertEqual(ir._env_settings()[0], 30)     # default window without INSIGHTS_DAYS

    def test_helpers(self):
        self.assertIsNone(ir._maybe_insights({"total": 0}, "key", ()))
        self.assertIsNone(ir._maybe_insights({"total": 5}, "", ()))
        with mock.patch.dict("os.environ", {"INSIGHTS_PREV_TOTAL": "x"}):
            self.assertFalse(ir._unchanged_since_last({"total": 1}))
        self.assertIsNone(ir._message_text({"stop_reason": "refusal", "content": [{"type": "text", "text": "x"}]}))
        self.assertIsNone(ir._message_text({"content": [{"type": "tool_use"}, "junk", {"type": "text", "text": ""}]}))
        self.assertEqual(ir._message_text({"content": [{"type": "text", "text": " a "}, {"type": "text", "text": "b"}]}),
                         "a \nb")


if __name__ == "__main__":
    unittest.main()
