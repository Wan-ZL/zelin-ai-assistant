"""act/lib/feedback.py + actd feedback inbox action (CONTRACT §29).

Covers the contract's four required behaviors:
(a) the inbox action lands a local record in state/feedback/ (and the inbox
    file is consumed) — FeedbackInboxActionTestCase;
(b) per-id snapshots carry type + title (+ status) at report time, with MS-
    merge-suggestion and unknown ids degrading instead of failing —
    SnapshotTestCase;
(c) the upload works with telemetry disabled and without any consent marker
    (feedback is an explicit user action) but still respects the empty-URL
    hard-off switch — TelemetryOffTestCase;
(d) bad ids never lose the report text — BadIdsTestCase;
plus the upload lifecycle: initial failure leaves uploaded:null pending,
retry_pending retries exactly ONCE then marks uploaded:false terminal — and
NEVER in the same pass that created the record (MIN_RETRY_AGE_SECONDS gives
the §29 "retry next pass" contract real time separation) —
RetryLifecycleTestCase.

Transports are injected/stubbed — no test ever touches the network.
Everything lives under the sandbox AIASSISTANT_HOME (tests/__init__.py).
"""
import json
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - ensures the sandbox env is set first

from act import __version__ as act_version
from act import actd, merge_review
from act.lib import analytics, config, feedback, registry
from act.lib.registry import Requirement


def _cfg(enabled: bool = True, url: str = "https://example.supabase.co") -> config.Config:
    c = config.Config()
    c.telemetry_enabled = enabled
    c.telemetry_supabase_url = url
    return c


def _clear_dir(path) -> None:
    if path.exists():
        for p in path.glob("*"):
            p.unlink()


def _records() -> list:
    return [json.loads(p.read_text(encoding="utf-8"))
            for p in sorted(feedback.FEEDBACK_DIR.glob("*.json"))]


class _StubTransportMixin:
    """self.rows collects uploaded rows; self._transport is a Transport.
    Registry entries / merge jobs a test creates are removed in tearDown so
    this module leaves no state behind for other suites."""

    def setUp(self):
        config.ensure_state_dirs()
        _clear_dir(config.INBOX_DIR)
        feedback.FEEDBACK_DIR.mkdir(parents=True, exist_ok=True)
        _clear_dir(feedback.FEEDBACK_DIR)
        if analytics.EVENTS_PATH.exists():
            analytics.EVENTS_PATH.unlink()
        self.rows: list = []
        self._made_reqs: list = []
        self._made_jobs: list = []

    def tearDown(self):
        for req in self._made_reqs:
            registry.delete(req)
        for sid in self._made_jobs:
            path = merge_review.job_path(sid)
            if path.exists():
                path.unlink()

    def _transport(self, row):
        self.rows.append(dict(row))

    def _mk_req(self, title: str = "修 Slack 权限", rtype: str = "ops",
                status: str = "delivered") -> Requirement:
        req = Requirement(id=registry.next_id(), title=title, type=rtype,
                          status=status)
        registry.save(req)
        self._made_reqs.append(req)
        return req

    def _mk_job(self, ids: list) -> dict:
        job = merge_review.create_job(ids)
        self._made_jobs.append(str(job["id"]))
        return job


class FeedbackInboxActionTestCase(_StubTransportMixin, unittest.TestCase):
    """(a) 动作落盘: the inbox action produces a state/feedback record."""

    def _drop_inbox(self, payload: dict) -> None:
        (config.INBOX_DIR / "feedback-test.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def test_action_writes_record_and_consumes_inbox_file(self):
        req = self._mk_req(title="Quinton 权限卡")
        self._drop_inbox({"action": "feedback", "ids": [req.id],
                          "text": "这张卡重复了", "ts": "2026-07-10T01:02:03Z"})
        # stub the network away: actd calls record_feedback with no transport
        with mock.patch.object(feedback, "_default_transport",
                               lambda cfg: self._transport):
            n = actd.process_inbox()
        self.assertEqual(n, 1)
        self.assertEqual(list(config.INBOX_DIR.glob("*.json")), [])  # consumed

        (rec,) = _records()
        self.assertEqual(rec["text"], "这张卡重复了")
        self.assertEqual(rec["ids"], [req.id])
        self.assertEqual(rec["app_version"], act_version)
        self.assertTrue(rec["ts"])
        self.assertTrue(rec["uploaded"])  # stub transport succeeded
        # the uploaded row rides the analytics_events shape, event="feedback"
        (row,) = self.rows
        self.assertEqual(row["event"], "feedback")
        self.assertEqual(row["source"], "feedback")
        self.assertEqual(row["props"]["text"], "这张卡重复了")
        self.assertEqual(row["client_ts"], rec["ts"])
        self.assertTrue(row["device_id"])
        # upload bookkeeping never leaks into props
        self.assertNotIn("uploaded", row["props"])

    def test_empty_text_is_dropped_without_a_record(self):
        self._drop_inbox({"action": "feedback", "ids": [], "text": "   "})
        with mock.patch.object(feedback, "_default_transport",
                               lambda cfg: self._transport):
            actd.process_inbox()
        self.assertEqual(_records(), [])
        self.assertEqual(list(config.INBOX_DIR.glob("*.json")), [])  # consumed
        self.assertEqual(self.rows, [])

    def test_ids_may_be_an_empty_array(self):
        self._drop_inbox({"action": "feedback", "ids": [],
                          "text": "整体建议：提案门槛太低"})
        with mock.patch.object(feedback, "_default_transport",
                               lambda cfg: self._transport):
            actd.process_inbox()
        (rec,) = _records()
        self.assertEqual(rec["ids"], [])
        self.assertEqual(rec["cards"], [])
        self.assertTrue(rec["uploaded"])

    def test_report_text_never_reaches_local_analytics_events(self):
        # CONTRACT §29: inbox_feedback logs METADATA only — the report text
        # travels solely inside the feedback record/upload.
        self._drop_inbox({"action": "feedback", "ids": [],
                          "text": "敏感内容只走 feedback 通道"})
        with mock.patch.object(feedback, "_default_transport",
                               lambda cfg: self._transport):
            actd.process_inbox()
        events = [e for e in analytics.read_events()
                  if e.get("event") == "inbox_feedback"]
        self.assertEqual(len(events), 1)
        self.assertNotIn("text", events[0])
        self.assertNotIn("敏感内容", json.dumps(events[0], ensure_ascii=False))


class SnapshotTestCase(_StubTransportMixin, unittest.TestCase):
    """(b) 快照字段: per-id type + title (+ status) at report time."""

    def test_requirement_snapshot_has_type_title_status(self):
        req = self._mk_req(title="停车 Q&A 误导入", rtype="research",
                           status="delivered")
        rec = feedback.record_feedback([req.id], "这卡不该存在",
                                       cfg=_cfg(), transport=self._transport)
        (card,) = rec["cards"]
        self.assertEqual(card, {"id": req.id, "kind": "requirement",
                                "type": "research", "title": "停车 Q&A 误导入",
                                "status": "delivered"})

    def test_merge_suggestion_snapshot(self):
        r1, r2 = self._mk_req(title="卡一"), self._mk_req(title="卡二")
        job = self._mk_job([r1.id, r2.id])
        rec = feedback.record_feedback([job["id"]], "这条合并建议不对",
                                       cfg=_cfg(), transport=self._transport)
        (card,) = rec["cards"]
        self.assertEqual(card["kind"], "merge_suggestion")
        self.assertEqual(card["type"], "merge_suggestion")
        self.assertEqual(card["status"], "analyzing")
        self.assertIn(r1.id, card["title"])
        self.assertIn(r2.id, card["title"])

    def test_unknown_id_degrades_to_unknown_kind(self):
        rec = feedback.record_feedback(["R-9999", "MS-deadbeef"], "查无此卡",
                                       cfg=_cfg(), transport=self._transport)
        self.assertEqual([c["kind"] for c in rec["cards"]],
                         ["unknown", "unknown"])
        self.assertEqual([c["title"] for c in rec["cards"]], [None, None])
        self.assertTrue(rec["uploaded"])  # unknown ids still upload fine


class TelemetryOffTestCase(_StubTransportMixin, unittest.TestCase):
    """(c) telemetry off 时仍工作: feedback ignores telemetry.enabled and the
    consent gate (explicit user action), but respects the empty-URL hard-off."""

    def test_records_and_uploads_with_telemetry_disabled(self):
        # no consent marker exists either (mixin wipes nothing that sets one)
        rec = feedback.record_feedback([], "关了统计也要能上报",
                                       cfg=_cfg(enabled=False),
                                       transport=self._transport)
        self.assertTrue(rec["uploaded"])
        self.assertEqual(len(self.rows), 1)
        self.assertEqual(self.rows[0]["props"]["text"], "关了统计也要能上报")

    def test_default_transport_ignores_telemetry_enabled(self):
        # enabled=False still yields a live transport (URL + key present)
        self.assertIsNotNone(feedback._default_transport(_cfg(enabled=False)))

    def test_empty_supabase_url_keeps_record_local_and_gives_up(self):
        rec = feedback.record_feedback([], "fork 上没有后端",
                                       cfg=_cfg(url=""), transport=None)
        self.assertIs(rec["uploaded"], False)  # terminal, never retried
        self.assertIn("disabled", rec["upload_error"])
        (on_disk,) = _records()
        self.assertIs(on_disk["uploaded"], False)
        # a later retry sweep skips it entirely
        n = feedback.retry_pending(cfg=_cfg(url=""),
                                   transport=self._transport)
        self.assertEqual(n, 0)
        self.assertEqual(self.rows, [])


class BadIdsTestCase(_StubTransportMixin, unittest.TestCase):
    """(d) 坏 ids 容错: garbage ids never lose the report text."""

    def test_non_list_ids_are_treated_as_empty(self):
        for bad in ("R-001", 42, {"id": "R-1"}, None):
            self.assertEqual(feedback.clean_ids(bad), [])

    def test_garbage_entries_are_coerced_deduped_or_skipped(self):
        self.assertEqual(feedback.clean_ids([None, "", "  ", 42, "R-1", "R-1"]),
                         ["42", "R-1"])

    def test_record_survives_garbage_ids(self):
        rec = feedback.record_feedback([None, 42, "R-nope"], "text 不能丢",
                                       cfg=_cfg(), transport=self._transport)
        self.assertIsNotNone(rec)
        self.assertEqual(rec["text"], "text 不能丢")
        self.assertEqual(rec["ids"], ["42", "R-nope"])
        self.assertTrue(all(c["kind"] == "unknown" for c in rec["cards"]))
        self.assertTrue(rec["uploaded"])

    def test_inbox_action_with_non_list_ids_still_records(self):
        (config.INBOX_DIR / "feedback-bad-ids.json").write_text(
            json.dumps({"action": "feedback", "ids": "R-001",
                        "text": "ids 是字符串也不能崩"}), encoding="utf-8")
        with mock.patch.object(feedback, "_default_transport",
                               lambda cfg: self._transport):
            actd.process_inbox()
        (rec,) = _records()
        self.assertEqual(rec["ids"], [])
        self.assertEqual(rec["text"], "ids 是字符串也不能崩")


class RetryLifecycleTestCase(_StubTransportMixin, unittest.TestCase):
    """Upload lifecycle: fail -> pending (null) -> one LATER retry -> true|false."""

    def _failing(self, row):
        raise OSError("supabase unreachable")

    @staticmethod
    def _age_pending(seconds: int = 120) -> None:
        """Backdate every pending record's ts, simulating a later actd pass
        (the sweep age-gates records created in the current pass)."""
        import datetime as _dt
        old = (_dt.datetime.now(_dt.timezone.utc)
               - _dt.timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")
        for p in feedback.FEEDBACK_DIR.glob("*.json"):
            try:
                rec = json.loads(p.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict) and rec.get("uploaded") is None:
                rec["ts"] = old
                p.write_text(json.dumps(rec, ensure_ascii=False),
                             encoding="utf-8")

    def test_same_pass_retry_is_deferred(self):
        # §29 regression: the sweep runs in the SAME run_once() pass that just
        # did the inline attempt — a fresh record must stay pending (null),
        # not burn its single retry seconds into the same outage.
        feedback.record_feedback([], "同一轮不重试", cfg=_cfg(),
                                 transport=self._failing)
        n = feedback.retry_pending(cfg=_cfg(), transport=self._failing)
        self.assertEqual(n, 0)
        (on_disk,) = _records()
        self.assertIsNone(on_disk["uploaded"])           # still pending
        self.assertEqual(on_disk["upload_attempts"], 1)  # only the inline try
        # a genuinely later pass performs the final retry
        self._age_pending()
        self.assertEqual(feedback.retry_pending(cfg=_cfg(),
                                                transport=self._transport), 1)
        (on_disk,) = _records()
        self.assertIs(on_disk["uploaded"], True)

    def test_initial_failure_leaves_pending_record(self):
        rec = feedback.record_feedback([], "第一次就断网",
                                       cfg=_cfg(), transport=self._failing)
        self.assertIsNotNone(rec)  # the report itself never fails
        (on_disk,) = _records()
        self.assertIsNone(on_disk["uploaded"])          # pending
        self.assertEqual(on_disk["upload_attempts"], 1)
        self.assertIn("unreachable", on_disk["upload_error"])
        self.assertEqual(on_disk["text"], "第一次就断网")

    def test_retry_success_marks_uploaded_true(self):
        feedback.record_feedback([], "重试成功", cfg=_cfg(),
                                 transport=self._failing)
        self._age_pending()   # simulate a later pass (age gate)
        n = feedback.retry_pending(cfg=_cfg(), transport=self._transport)
        self.assertEqual(n, 1)
        (on_disk,) = _records()
        self.assertIs(on_disk["uploaded"], True)
        self.assertTrue(on_disk["uploaded_at"])
        self.assertNotIn("upload_error", on_disk)
        self.assertEqual(self.rows[0]["props"]["text"], "重试成功")

    def test_retry_failure_gives_up_terminally(self):
        feedback.record_feedback([], "两次都失败", cfg=_cfg(),
                                 transport=self._failing)
        self._age_pending()   # simulate a later pass (age gate)
        n = feedback.retry_pending(cfg=_cfg(), transport=self._failing)
        self.assertEqual(n, 1)
        (on_disk,) = _records()
        self.assertIs(on_disk["uploaded"], False)       # gave up
        self.assertEqual(on_disk["upload_attempts"], 2)
        # terminal: further sweeps never touch it again
        self.assertEqual(feedback.retry_pending(cfg=_cfg(),
                                                transport=self._transport), 0)
        self.assertEqual(self.rows, [])

    def test_uploaded_records_are_skipped_by_the_sweep(self):
        feedback.record_feedback([], "一次成功", cfg=_cfg(),
                                 transport=self._transport)
        self.rows.clear()
        self.assertEqual(feedback.retry_pending(cfg=_cfg(),
                                                transport=self._transport), 0)
        self.assertEqual(self.rows, [])

    def test_corrupt_record_file_never_breaks_the_sweep(self):
        (feedback.FEEDBACK_DIR / "torn.json").write_text("{{{ torn",
                                                         encoding="utf-8")
        feedback.record_feedback([], "好记录照常重试", cfg=_cfg(),
                                 transport=self._failing)
        self._age_pending()   # simulate a later pass (age gate)
        n = feedback.retry_pending(cfg=_cfg(), transport=self._transport)
        self.assertEqual(n, 1)  # the good record was retried, corrupt skipped


if __name__ == "__main__":
    unittest.main()
