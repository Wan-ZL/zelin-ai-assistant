"""``POST /api/analytics`` — the web board's minimal analytics ingress (CONTRACT §16 / §15 / §49; D48).

Only two metadata-only events survive from the native UI funnel, and the
server owns the whitelist — the client cannot mint event names or fields:

- ``wizard_complete`` (no fields) and ``pipeline_repair_result{ok: bool}``;
  anything else → 400 (INVALID_FIELD for an unlisted event / bad type,
  UNKNOWN_FIELD for a stray top-level key or an unlisted field);
- field types are checked from a closed table (``bool``, ``int`` — metadata
  scalars only); a whitelist entry with a type outside the table fails loud
  (TypeError at import / request) instead of forwarding the raw client value;
- the record goes through ``act.lib.analytics.log_event`` — same file, same
  §16 ``features.analytics`` gate (flag off ⇒ nothing written, receipt says
  ``logged: false`` and the HTTP status is still 200: analytics never breaks
  the UI), same writer-level ``v`` stamp — plus a constant ``via: "web"``;
- the four write gates apply (no token → 401).
Unit level uses the ``log`` injection seam; route level patches the writer
(no real subprocess, no network).
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first
from tests.test_server_common import (assert_envelope, http_request, post_json,
                                      start_server)

from act.lib import analytics, config
from server import analytics_ingest
from server.errors import InvalidFieldError, UnknownFieldError

ROUTE = "/api/analytics"


class _Recorder:
    def __init__(self, result=True):
        self.calls = []
        self.result = result

    def __call__(self, event, **fields):
        self.calls.append((event, fields))
        return self.result


class IngestWhitelistTestCase(unittest.TestCase):
    def test_whitelist_is_exactly_the_two_native_events(self):
        self.assertEqual(sorted(analytics_ingest.EVENTS), ["pipeline_repair_result", "wizard_complete"])
        self.assertEqual(analytics_ingest.EVENTS["wizard_complete"], {})
        self.assertEqual(analytics_ingest.EVENTS["pipeline_repair_result"], {"ok": bool})

    def test_wizard_complete_logs_with_via_web_and_no_fields(self):
        rec = _Recorder()
        receipt = analytics_ingest.ingest({"event": "wizard_complete"}, log=rec)
        self.assertEqual(receipt, {"ok": True, "event": "wizard_complete", "logged": True})
        self.assertEqual(rec.calls, [("wizard_complete", {"via": "web"})])

    def test_pipeline_repair_result_carries_only_the_bool(self):
        rec = _Recorder()
        analytics_ingest.ingest({"event": "pipeline_repair_result", "fields": {"ok": False}}, log=rec)
        self.assertEqual(rec.calls, [("pipeline_repair_result", {"via": "web", "ok": False})])
        rec.calls.clear()
        analytics_ingest.ingest({"event": "pipeline_repair_result", "fields": {}}, log=rec)
        self.assertEqual(rec.calls, [("pipeline_repair_result", {"via": "web"})])
        rec.calls.clear()
        analytics_ingest.ingest({"event": "pipeline_repair_result", "fields": None}, log=rec)
        self.assertEqual(rec.calls, [("pipeline_repair_result", {"via": "web"})])

    def test_unlisted_event_is_400_with_the_allowed_list(self):
        rec = _Recorder()
        for event in ("mw_section_dwell", "", None, 3, ["wizard_complete"]):
            with self.assertRaises(InvalidFieldError) as cm:
                analytics_ingest.ingest({"event": event}, log=rec)
            self.assertEqual(cm.exception.details, {"allowed": ["pipeline_repair_result", "wizard_complete"]})
        with self.assertRaises(InvalidFieldError):
            analytics_ingest.ingest({}, log=rec)
        self.assertEqual(rec.calls, [])

    def test_stray_keys_and_unlisted_fields_are_400_unknown_field(self):
        rec = _Recorder()
        with self.assertRaises(UnknownFieldError) as cm:
            analytics_ingest.ingest({"event": "wizard_complete", "ts": "x"}, log=rec)
        self.assertEqual(cm.exception.details, {"fields": ["ts"]})
        with self.assertRaises(UnknownFieldError) as cm:
            analytics_ingest.ingest({"event": "wizard_complete", "fields": {"text": "hello"}}, log=rec)
        self.assertEqual(cm.exception.details, {"fields": ["text"]})
        with self.assertRaises(UnknownFieldError) as cm:
            analytics_ingest.ingest({"event": "pipeline_repair_result", "fields": {"ok": True, "error": "boom"}}, log=rec)
        self.assertEqual(cm.exception.details, {"fields": ["error"]})
        self.assertEqual(rec.calls, [])

    def test_field_types_are_strict(self):
        rec = _Recorder()
        for bad in (1, 0, "true", "false", None, [True]):
            with self.assertRaises(InvalidFieldError):
                analytics_ingest.ingest({"event": "pipeline_repair_result", "fields": {"ok": bad}}, log=rec)
        for bad in ([], "ok", 1):
            with self.assertRaises(InvalidFieldError):
                analytics_ingest.ingest({"event": "pipeline_repair_result", "fields": bad}, log=rec)
        self.assertEqual(rec.calls, [])

    def test_writer_refusal_is_reported_as_logged_false(self):
        rec = _Recorder(result=False)
        receipt = analytics_ingest.ingest({"event": "wizard_complete"}, log=rec)
        self.assertEqual(receipt["logged"], False)
        self.assertEqual(receipt["ok"], True)


class FieldTypeTableTestCase(unittest.TestCase):
    """The whitelist's field types are enforced generically from ``_FIELD_TYPES`` — a
    future ``{"count": int}`` entry is checked, and a type outside the table (``str``:
    free text) fails loud instead of passing the client value straight through."""

    def _with_events(self, events):
        p = mock.patch.object(analytics_ingest, "EVENTS", events)
        p.start()
        self.addCleanup(p.stop)

    def test_current_whitelist_passes_the_self_check(self):
        analytics_ingest._check_spec(analytics_ingest.EVENTS)   # must not raise

    def test_table_holds_only_metadata_scalars(self):
        self.assertEqual(set(analytics_ingest._FIELD_TYPES), {bool, int})

    def test_int_spec_is_enforced_not_passed_through(self):
        self._with_events({"wizard_step": {"index": int}})
        rec = _Recorder()
        for bad in ("3", 3.0, True, False, None, [3]):
            with self.assertRaises(InvalidFieldError):
                analytics_ingest.ingest({"event": "wizard_step", "fields": {"index": bad}}, log=rec)
        self.assertEqual(rec.calls, [])
        analytics_ingest.ingest({"event": "wizard_step", "fields": {"index": 3}}, log=rec)
        self.assertEqual(rec.calls, [("wizard_step", {"via": "web", "index": 3})])

    def test_unsupported_spec_type_fails_loud_at_import_and_at_request(self):
        with self.assertRaises(TypeError):
            analytics_ingest._check_spec({"note": {"text": str}})
        # a whitelist that slipped past the import-time check still never forwards the value
        self._with_events({"note": {"text": str}})
        rec = _Recorder()
        with self.assertRaises(TypeError):
            analytics_ingest.ingest({"event": "note", "fields": {"text": "free text"}}, log=rec)
        self.assertEqual(rec.calls, [])


class IngestThroughTheRealWriterTestCase(unittest.TestCase):
    """The default writer is act.lib.analytics.log_event — same file, same §16 gate, same ``v`` stamp."""

    def setUp(self):
        analytics.reset_feature_gate_cache()
        self.addCleanup(analytics.reset_feature_gate_cache)
        self.addCleanup(lambda: config.CONFIG_PATH.unlink(missing_ok=True))
        self.addCleanup(lambda: config.SETTINGS_OVERRIDES_PATH.unlink(missing_ok=True))

    def _patch_features(self, **flags):
        cfg = config.Config()
        cfg.features.update(flags)
        p = mock.patch.object(config, "load_config", return_value=cfg)
        p.start()
        self.addCleanup(p.stop)

    def _events(self, name):
        return [e for e in analytics.read_events() if e.get("event") == name]

    def test_flag_on_appends_one_line_to_events_jsonl(self):
        self._patch_features(analytics=True)
        before = len(self._events("pipeline_repair_result"))
        receipt = analytics_ingest.ingest({"event": "pipeline_repair_result", "fields": {"ok": True}})
        self.assertEqual(receipt["logged"], True)
        rows = self._events("pipeline_repair_result")
        self.assertEqual(len(rows), before + 1)
        last = rows[-1]
        self.assertEqual(last["ok"], True)
        self.assertEqual(last["via"], "web")
        self.assertIn("v", last)   # writer-level version stamp
        self.assertIn("ts", last)

    def test_flag_off_is_a_no_op_with_logged_false(self):
        self._patch_features(analytics=False)
        before = len(self._events("wizard_complete"))
        receipt = analytics_ingest.ingest({"event": "wizard_complete"})
        self.assertEqual(receipt, {"ok": True, "event": "wizard_complete", "logged": False})
        self.assertEqual(len(self._events("wizard_complete")), before)


class IngestRouteTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-analytics-")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        (self.home / "state").mkdir(parents=True)
        _httpd, self.port = start_server(self, self.home)
        self.rec = _Recorder()
        p = mock.patch.object(analytics, "log_event", self.rec)
        p.start()
        self.addCleanup(p.stop)

    def test_route_logs_whitelisted_event(self):
        status, receipt = post_json(self.port, ROUTE, {"event": "pipeline_repair_result", "fields": {"ok": False}})
        self.assertEqual(status, 200)
        self.assertEqual(receipt, {"ok": True, "event": "pipeline_repair_result", "logged": True})
        self.assertEqual(self.rec.calls, [("pipeline_repair_result", {"via": "web", "ok": False})])

    def test_route_rejects_unlisted_event_and_unknown_field(self):
        status, obj = post_json(self.port, ROUTE, {"event": "board_search", "fields": {"chars": 3}})
        self.assertEqual(status, 400)
        assert_envelope(self, obj, "INVALID_FIELD")
        status, obj = post_json(self.port, ROUTE, {"event": "wizard_complete", "query": "secret"})
        self.assertEqual(status, 400)
        assert_envelope(self, obj, "UNKNOWN_FIELD")
        self.assertEqual(self.rec.calls, [])

    def test_route_requires_the_write_gates(self):
        body = json.dumps({"event": "wizard_complete"}).encode("utf-8")
        status, _h, data = http_request(self.port, "POST", ROUTE, body=body,
                                        headers={"Content-Type": "application/json"})
        self.assertEqual(status, 401)
        assert_envelope(self, json.loads(data.decode("utf-8")), "UNAUTHORIZED")
        self.assertEqual(self.rec.calls, [])


if __name__ == "__main__":
    unittest.main()
