"""scripts/insights_report.py — the four derived views + graceful degradation.

The insights GitHub Action must publish AGGREGATES ONLY. These tests pin that:
  * the activation FUNNEL counts distinct devices per stage, stays monotonic,
    unions the legacy producer events, and reports drop-off %;
  * per-path FAILURE rates come out as fail/total fractions;
  * ABANDONMENT surfaces configured-but-uncarded + used-exactly-once devices;
  * RETENTION reports cohort/return counts by client event time;
  * NO device id ever appears in any rendered output (they survive only as a
    distinct COUNT), and the ``**Totals:** N events`` line the workflow greps
    for is preserved;
  * ``analytics.log_first`` emits its milestone at most once per install.

scripts/ is not a package — the module is loaded from its file path.
"""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from act.lib import analytics

_SPEC = importlib.util.spec_from_file_location(
    "insights_report",
    Path(__file__).resolve().parent.parent / "scripts" / "insights_report.py",
)
insights_report = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(insights_report)


def _row(device, event, props=None, client_ts=None, inserted_at=None,
         app_version="0.19.0"):
    return {
        "device_id": device,
        "event": event,
        "app_version": app_version,
        "inserted_at": inserted_at or "2026-07-01T00:00:00+00:00",
        "client_ts": client_ts,
        "props": props or {},
    }


# Activation-shaped fixture: 4 devices at descending funnel depth.
#   dev-a: full funnel (via v0.19 milestone events)
#   dev-b: install -> configure -> first card (via LEGACY card_sent)
#   dev-c: install -> configure  (never carded)
#   dev-d: install only
FUNNEL_ROWS = [
    _row("dev-a", "feature_first_reach", {"feature": "app_launch"}),
    _row("dev-a", "feature_first_reach", {"feature": "ingest_configured"}),
    _row("dev-a", "milestone_first_card", {"req": "R-1"}),
    _row("dev-a", "milestone_first_approval", {"req": "R-1"}),
    _row("dev-a", "milestone_first_delivery", {"req": "R-1"}),
    _row("dev-b", "feature_first_reach", {"feature": "app_launch"}),
    _row("dev-b", "feature_first_reach", {"feature": "ingest_configured"}),
    _row("dev-b", "card_sent", {"req": "R-2", "via": "raise"}),  # legacy
    _row("dev-c", "feature_first_reach", {"feature": "app_launch"}),
    _row("dev-c", "feature_first_reach", {"feature": "ingest_configured"}),
    _row("dev-d", "feature_first_reach", {"feature": "app_launch"}),
]


class FunnelTestCase(unittest.TestCase):
    def setUp(self):
        self.f = insights_report.funnel(FUNNEL_ROWS)
        self.by_key = {s["key"]: s for s in self.f["stages"]}

    def test_distinct_devices_per_stage(self):
        self.assertEqual(self.by_key["installed"]["devices"], 4)
        self.assertEqual(self.by_key["configured"]["devices"], 3)
        self.assertEqual(self.by_key["first_card"]["devices"], 2)  # incl legacy
        self.assertEqual(self.by_key["first_approval"]["devices"], 1)
        self.assertEqual(self.by_key["first_delivery"]["devices"], 1)
        self.assertEqual(self.f["install_base"], 4)

    def test_monotonic_non_increasing(self):
        counts = [s["devices"] for s in self.f["stages"]]
        self.assertEqual(counts, sorted(counts, reverse=True))

    def test_drop_off_and_pct(self):
        self.assertIsNone(self.by_key["installed"]["drop_from_prev_pct"])
        self.assertAlmostEqual(
            self.by_key["configured"]["drop_from_prev_pct"], 25.0)
        self.assertAlmostEqual(
            self.by_key["first_approval"]["drop_from_prev_pct"], 50.0)
        self.assertAlmostEqual(self.by_key["configured"]["pct_of_install"], 75.0)

    def test_install_fallback_to_all_devices_when_no_app_launch(self):
        rows = [_row("d1", "dispatch"), _row("d2", "card_sent"),
                _row("d2", "milestone_first_delivery")]
        f = insights_report.funnel(rows)
        by_key = {s["key"]: s for s in f["stages"]}
        # no app_launch marker -> installed falls back to all distinct devices
        self.assertEqual(by_key["installed"]["devices"], 2)


class PathFailuresTestCase(unittest.TestCase):
    def setUp(self):
        self.rows = [
            _row("dev-a", "radar_scan", {"source": "gmail"}),
            _row("dev-a", "radar_scan", {"source": "gmail"}),
            _row("dev-b", "radar_skip",
                 {"source": "gmail", "reason": "no_credentials"}),
            _row("dev-a", "radar_scan", {"source": "slack"}),
            _row("dev-a", "dispatch", {"req": "R-1"}),
            _row("dev-b", "dispatch_failed",
                 {"req": "R-2", "reason": "launch_failed"}),
            _row("dev-b", "auto_resume", {"ok": False}),
            _row("dev-b", "auto_resume", {"ok": True}),
        ]
        self.pf = insights_report.path_failures(self.rows)

    def test_ingest_skip_rate_and_top_reason(self):
        gmail = self.pf["ingest"]["gmail"]
        self.assertEqual(gmail["scans"], 2)
        self.assertEqual(gmail["skips"], 1)
        self.assertAlmostEqual(gmail["skip_rate_pct"], 100.0 / 3)
        self.assertEqual(gmail["top_reason"], ("no_credentials", 1))
        self.assertEqual(self.pf["ingest"]["slack"]["skips"], 0)

    def test_dispatch_failure_rate(self):
        d = self.pf["dispatch"]
        self.assertEqual((d["ok"], d["failed"], d["total"]), (1, 1, 2))
        self.assertAlmostEqual(d["fail_rate_pct"], 50.0)

    def test_ok_flag_events(self):
        self.assertEqual(self.pf["ok_events"]["auto_resume"],
                         {"fail": 1, "total": 2, "rate_pct": 50.0})
        # events without an ok flag never appear here
        self.assertNotIn("radar_scan", self.pf["ok_events"])


class AbandonmentTestCase(unittest.TestCase):
    def test_configured_no_card_and_used_once(self):
        ab = insights_report.abandonment(FUNNEL_ROWS)
        self.assertEqual(ab["configured"], 3)          # a, b, c
        self.assertEqual(ab["configured_no_card"], 1)  # only dev-c
        self.assertAlmostEqual(ab["configured_no_card_pct"], 100.0 / 3)
        # every event above is emitted exactly once per device
        self.assertEqual(ab["used_once"]["milestone_first_delivery"], 1)
        self.assertEqual(ab["used_once"]["card_sent"], 1)


class RetentionTestCase(unittest.TestCase):
    def setUp(self):
        self.rows = [
            # dev-r1: first-seen 07-01, returns 07-03 (d2) and 07-09 (d7)
            _row("dev-r1", "app", client_ts="2026-07-01T09:00:00Z"),
            _row("dev-r1", "app", client_ts="2026-07-03T09:00:00Z"),
            _row("dev-r1", "app", client_ts="2026-07-09T09:00:00Z"),
            # dev-r2: only 07-01 (via inserted_at fallback), never returns
            _row("dev-r2", "app", inserted_at="2026-07-01T09:00:00+00:00"),
            # dev-r3: only 07-10 == window end -> excluded from both cohorts
            _row("dev-r3", "app", client_ts="2026-07-10T09:00:00Z"),
        ]
        self.r = insights_report.retention(self.rows)

    def test_cohort_and_return_counts(self):
        self.assertEqual(self.r["devices"], 3)
        self.assertEqual(self.r["d2"], {"cohort": 2, "returned": 1,
                                        "rate_pct": 50.0})
        self.assertEqual(self.r["d7"], {"cohort": 2, "returned": 1,
                                        "rate_pct": 50.0})

    def test_empty_rows(self):
        r = insights_report.retention([])
        self.assertEqual(r["devices"], 0)
        self.assertEqual(r["d2"]["cohort"], 0)


class NoIdLeakTestCase(unittest.TestCase):
    """The single most important privacy invariant: an opaque device id that
    is present in the rows must NEVER surface in the rendered report."""

    def test_device_ids_absent_from_full_body(self):
        secret = "dev-SECRET-0xdeadbeef"
        rows = [
            _row(secret, "feature_first_reach", {"feature": "app_launch"}),
            _row(secret, "feature_first_reach",
                 {"feature": "ingest_configured"}),
            _row(secret, "card_sent", {"req": "R-9"},
                 client_ts="2026-07-01T00:00:00Z"),
            _row(secret, "radar_skip", {"source": "gmail",
                                        "reason": "no_credentials"}),
        ]
        agg = insights_report.aggregate(rows)
        body = insights_report.build_body(
            agg, None, days=30,
            funnel_v=insights_report.funnel(rows),
            failures_v=insights_report.path_failures(rows),
            abandon_v=insights_report.abandonment(rows),
            retention_v=insights_report.retention(rows))
        self.assertNotIn(secret, body)
        # the derived-view sections and the load-bearing Totals line all show
        self.assertIn("### 1. Activation funnel", body)
        self.assertIn("### 2. Reliability", body)
        self.assertIn("### 3. Feature abandonment", body)
        self.assertIn("### 4. Retention", body)
        self.assertIn(f"**Totals:** {agg['total']} events", body)
        self.assertIn("no_credentials", body)  # skip reason surfaces


class BodyTestCase(unittest.TestCase):
    def setUp(self):
        self.agg = insights_report.aggregate(FUNNEL_ROWS)
        self.views = dict(
            funnel_v=insights_report.funnel(FUNNEL_ROWS),
            failures_v=insights_report.path_failures(FUNNEL_ROWS),
            abandon_v=insights_report.abandonment(FUNNEL_ROWS),
            retention_v=insights_report.retention(FUNNEL_ROWS))

    def test_totals_line_is_verbatim_for_the_no_change_gate(self):
        body = insights_report.build_body(self.agg, None, days=30, **self.views)
        # matches insights.yml's sed 's/.*\*\*Totals:\*\* \([0-9]*\) events.*/'
        self.assertIn(f"**Totals:** {self.agg['total']} events from", body)

    def test_body_with_insights_section(self):
        body = insights_report.build_body(
            self.agg, "- **Fix:** X — because 25.0%. [confidence: high]",
            days=30, **self.views)
        self.assertIn("## Insights", body)
        self.assertIn("[confidence: high]", body)
        self.assertIn("Appendix", body)

    def test_body_without_anthropic_key_keeps_views(self):
        body = insights_report.build_body(self.agg, None, days=30, **self.views)
        self.assertNotIn("## Insights", body)
        self.assertIn("No AI analysis this run", body)
        self.assertIn("### 1. Activation funnel", body)

    def test_tenant_caveat_is_present(self):
        body = insights_report.build_body(self.agg, None, days=30, **self.views)
        self.assertIn("commingle", body)

    def test_body_missing_supabase_key_explains_setup(self):
        body = insights_report.build_body(None, None, days=30, missing_key=True)
        self.assertIn("SUPABASE_INSIGHTS_KEY", body)
        self.assertIn("gh secret set", body)

    def test_body_fetch_error_is_reported(self):
        body = insights_report.build_body(None, None, days=30,
                                          error="URLError: boom")
        self.assertIn("This run failed", body)
        self.assertIn("URLError: boom", body)


class LegacyAggregateTestCase(unittest.TestCase):
    """The appendix aggregate/render_tables are unchanged — the Totals line and
    per-event error rates still work for the collapsed raw-counts appendix."""

    def test_aggregate_and_render_tables(self):
        rows = [
            _row("dev-a", "radar_scan", {}),
            _row("dev-b", "auto_resume", {"ok": False}),
            _row("dev-b", "auto_resume", {"ok": True}),
        ]
        agg = insights_report.aggregate(rows)
        self.assertEqual(agg["total"], 3)
        self.assertEqual(agg["devices"], 2)
        md = insights_report.render_tables(agg)
        self.assertIn("3 events from 2 devices", md)
        self.assertIn("| auto_resume | 1 | 2 | 50.0% |", md)
        self.assertNotIn("dev-a", md)


class FetchPagingTestCase(unittest.TestCase):
    def test_fetch_pages_until_short_batch(self):
        calls = []

        class FakeResp:
            def __init__(self, payload):
                self.payload = payload

            def read(self):
                return json.dumps(self.payload).encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def opener(req, timeout=0):
            calls.append(req.headers.get("Range"))
            page = len(calls) - 1
            size = insights_report.PAGE_SIZE if page == 0 else 3
            return FakeResp([{"event": "e",
                              "inserted_at": "2026-07-01T00:00:00Z"}] * size)

        rows = insights_report.fetch_rows("https://x.supabase.co", "key",
                                          "2026-06-01T00:00:00Z", opener=opener)
        self.assertEqual(len(rows), insights_report.PAGE_SIZE + 3)
        self.assertEqual(len(calls), 2)  # stopped after the short batch
        self.assertEqual(calls[0], f"0-{insights_report.PAGE_SIZE - 1}")

    def test_select_includes_client_ts(self):
        seen = {}

        class FakeResp:
            def read(self):
                return b"[]"

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def opener(req, timeout=0):
            seen["url"] = req.full_url
            return FakeResp()

        insights_report.fetch_rows("https://x.supabase.co", "key",
                                   "2026-06-01T00:00:00Z", opener=opener)
        self.assertIn("client_ts", seen["url"])  # retention needs client time


class LogFirstTestCase(unittest.TestCase):
    """analytics.log_first — once-per-install milestone with a persistent
    marker. Emitting twice must produce exactly one event line."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self._saved = (analytics.ANALYTICS_DIR, analytics.EVENTS_PATH,
                       analytics.FIRST_DIR)
        analytics.ANALYTICS_DIR = base
        analytics.EVENTS_PATH = base / "events.jsonl"
        analytics.FIRST_DIR = base / "first"

    def tearDown(self):
        (analytics.ANALYTICS_DIR, analytics.EVENTS_PATH,
         analytics.FIRST_DIR) = self._saved
        self._tmp.cleanup()

    def _events(self):
        if not analytics.EVENTS_PATH.exists():
            return []
        return [json.loads(ln) for ln in
                analytics.EVENTS_PATH.read_text().splitlines() if ln.strip()]

    def test_fires_once_and_marks(self):
        analytics.log_first("milestone_first_card", req="R-1")
        analytics.log_first("milestone_first_card", req="R-2")  # suppressed
        evs = self._events()
        self.assertEqual(len(evs), 1)
        self.assertEqual(evs[0]["event"], "milestone_first_card")
        self.assertEqual(evs[0]["req"], "R-1")
        self.assertTrue((analytics.FIRST_DIR / "milestone_first_card").exists())

    def test_distinct_milestones_are_independent(self):
        analytics.log_first("milestone_first_card", req="R-1")
        analytics.log_first("milestone_first_approval", req="R-1")
        self.assertEqual(len(self._events()), 2)


if __name__ == "__main__":
    unittest.main()
