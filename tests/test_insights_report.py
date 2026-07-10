"""scripts/insights_report.py — aggregate/render/build_body over fixture rows.

The insights GitHub Action must publish AGGREGATES ONLY: these tests pin that
the aggregate step reduces rows to counts (device ids survive only as a
distinct count), the renderer emits the tables the workflow posts, and the
body degrades gracefully with no Supabase key / no AI insights / fetch errors.

scripts/ is not a package — the module is loaded from its file path.
"""
import importlib.util
import json
import unittest
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "insights_report",
    Path(__file__).resolve().parent.parent / "scripts" / "insights_report.py",
)
insights_report = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(insights_report)

FIXTURE_ROWS = json.loads("""
[
  {"device_id": "dev-a", "event": "radar_scan", "app_version": "0.13.0",
   "inserted_at": "2026-07-01T09:00:00+00:00", "props": {"event": "radar_scan"}},
  {"device_id": "dev-a", "event": "radar_scan", "app_version": "0.13.0",
   "inserted_at": "2026-07-01T10:00:00+00:00", "props": {}},
  {"device_id": "dev-b", "event": "inbox_approve", "app_version": "0.12.0",
   "inserted_at": "2026-07-02T09:00:00+00:00", "props": {"ok": true}},
  {"device_id": "dev-b", "event": "auto_resume", "app_version": "0.12.0",
   "inserted_at": "2026-07-02T11:00:00+00:00", "props": {"ok": false}},
  {"device_id": "dev-b", "event": "auto_resume", "app_version": "0.12.0",
   "inserted_at": "2026-07-03T11:00:00+00:00", "props": {"ok": true}},
  {"device_id": "dev-c", "event": "dispatch", "app_version": "0.13.0",
   "inserted_at": "2026-07-03T12:00:00+00:00", "props": {"level": "detailed"}}
]
""")


class AggregateTestCase(unittest.TestCase):
    def setUp(self):
        self.agg = insights_report.aggregate(FIXTURE_ROWS)

    def test_counts_by_event_day_version(self):
        self.assertEqual(self.agg["total"], 6)
        self.assertEqual(self.agg["devices"], 3)
        self.assertEqual(self.agg["by_event"]["radar_scan"], 2)
        self.assertEqual(self.agg["by_event"]["auto_resume"], 2)
        self.assertEqual(self.agg["by_day"]["2026-07-01"], 2)
        self.assertEqual(self.agg["by_day"]["2026-07-03"], 2)
        self.assertEqual(self.agg["by_version"]["0.13.0"], 3)
        self.assertEqual(self.agg["by_level"], {"detailed": 1})

    def test_error_rates_only_for_rows_with_ok_flag(self):
        rates = self.agg["error_rates"]
        self.assertEqual(rates["auto_resume"], {"fail": 1, "total": 2})
        self.assertEqual(rates["inbox_approve"], {"fail": 0, "total": 1})
        self.assertNotIn("radar_scan", rates)  # no ok flag -> no error row

    def test_malformed_rows_are_skipped(self):
        agg = insights_report.aggregate(["not a dict", None, {}])
        self.assertEqual(agg["total"], 1)  # {} counts as one (unknown) event
        self.assertEqual(agg["devices"], 0)


class RenderTestCase(unittest.TestCase):
    def test_tables_carry_aggregates_but_no_raw_row_fields(self):
        agg = insights_report.aggregate(FIXTURE_ROWS)
        md = insights_report.render_tables(agg)
        self.assertIn("6 events from 3 devices", md)
        self.assertIn("| radar_scan | 2 |", md)
        self.assertIn("| 2026-07-01 | 2 |", md)
        self.assertIn("| auto_resume | 1 | 2 | 50.0% |", md)
        # aggregates only: device ids never appear in the output
        self.assertNotIn("dev-a", md)
        self.assertNotIn("dev-b", md)


class BodyTestCase(unittest.TestCase):
    def test_body_with_insights_section(self):
        agg = insights_report.aggregate(FIXTURE_ROWS)
        body = insights_report.build_body(agg, "- something [confidence: high]",
                                          days=30)
        self.assertIn("## Insights", body)
        self.assertIn("[confidence: high]", body)
        self.assertIn("## Aggregates", body)

    def test_body_without_anthropic_key_keeps_tables(self):
        agg = insights_report.aggregate(FIXTURE_ROWS)
        body = insights_report.build_body(agg, None, days=30)
        self.assertNotIn("## Insights", body)
        self.assertIn("No AI analysis this run", body)
        self.assertIn("## Aggregates", body)

    def test_body_missing_supabase_key_explains_setup(self):
        body = insights_report.build_body(None, None, days=30, missing_key=True)
        self.assertIn("SUPABASE_INSIGHTS_KEY", body)
        self.assertIn("gh secret set", body)

    def test_body_fetch_error_is_reported(self):
        body = insights_report.build_body(None, None, days=30,
                                          error="URLError: boom")
        self.assertIn("This run failed", body)
        self.assertIn("URLError: boom", body)


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
            return FakeResp([{"event": "e", "inserted_at": "2026-07-01T00:00:00Z"}] * size)

        rows = insights_report.fetch_rows("https://x.supabase.co", "key",
                                          "2026-06-01T00:00:00Z", opener=opener)
        self.assertEqual(len(rows), insights_report.PAGE_SIZE + 3)
        self.assertEqual(len(calls), 2)  # stopped after the short batch
        self.assertEqual(calls[0], f"0-{insights_report.PAGE_SIZE - 1}")


if __name__ == "__main__":
    unittest.main()
