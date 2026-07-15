"""actd 'capture' inbox action (CONTRACT §10) — one-liner -> RAISING card,
and the v0.34.0 mode="run" variant (CONTRACT §34) — one-liner -> APPROVED card
that dispatch_approved picks up on the next pass.

Runs entirely inside the sandbox AIASSISTANT_HOME (tests/__init__.py); no LLM
is invoked (process_raising is NOT called here; dispatch is stubbed).
"""
import json
import unittest
import uuid
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - ensures the sandbox env is set first

from act import actd, executor
from act.lib import config, registry
from act.lib.registry import Requirement, State


class CaptureActionTestCase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        if config.REGISTRY_DIR.exists():
            for p in config.REGISTRY_DIR.glob("*.yaml"):
                p.unlink()
        for p in config.INBOX_DIR.glob("*.json"):
            p.unlink()

    def _write_capture(self, text: str):
        payload = {"action": "capture", "text": text,
                   "ts": "2026-07-07T00:00:00Z"}
        path = config.INBOX_DIR / f"capture-{uuid.uuid4()}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False),
                        encoding="utf-8")
        return path

    def test_capture_creates_raising_entry_with_title_eq_text(self):
        text = "给 my-bench 加一个一键导出报告按钮"
        self._write_capture(text)
        processed = actd.process_inbox()
        self.assertEqual(processed, 1)

        entries = [r for r in registry.load_all() if r.title == text]
        self.assertEqual(len(entries), 1)
        req = entries[0]
        self.assertEqual(req.status, registry.State.RAISING.value)
        # 原话保留在 sources，channel=quick_capture（契约 §10）
        self.assertEqual(req.sources[0]["channel"], "quick_capture")
        self.assertEqual(req.sources[0]["quote"], text)
        # inbox 文件读后即删
        self.assertEqual(list(config.INBOX_DIR.glob("*.json")), [])

    def test_capture_same_text_twice_is_idempotent(self):
        text = "把 phase I 的任务生成脚本整理进 repo"
        self._write_capture(text)
        actd.process_inbox()
        self._write_capture(text)
        actd.process_inbox()

        entries = [r for r in registry.load_all() if r.title == text]
        self.assertEqual(len(entries), 1)          # merge_or_new 按 title 合并
        self.assertEqual(entries[0].status, registry.State.RAISING.value)

    def test_capture_does_not_downgrade_already_expanded_card(self):
        text = "整理 secrets 契约文档"
        self._write_capture(text)
        actd.process_inbox()
        req = [r for r in registry.load_all() if r.title == text][0]
        req.set_status(registry.State.CARD_SENT)   # 模拟 process_raising 已扩写完
        registry.save(req)

        self._write_capture(text)
        actd.process_inbox()
        entries = [r for r in registry.load_all() if r.title == text]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].status, registry.State.CARD_SENT.value)

    def test_capture_with_empty_text_creates_nothing(self):
        self._write_capture("   ")
        actd.process_inbox()
        self.assertEqual(registry.load_all(), [])
        self.assertEqual(list(config.INBOX_DIR.glob("*.json")), [])


_APPLIED = config.STATE_DIR / "sync" / "applied.jsonl"


def _activate_sync():
    """§5.4 ack ledger only exists for cloud-synced installs (same pattern as
    tests/test_audit_ack_honesty.py)."""
    config.ensure_state_dirs()
    (config.STATE_DIR / "sync.json").write_text(
        json.dumps({"mode": "cloud", "device_id": "dev-test"}), encoding="utf-8")
    actd._SYNC_ACTIVE_CACHE = None


def _ack_for(action_id: str):
    if not _APPLIED.exists():
        return None
    for ln in _APPLIED.read_text(encoding="utf-8").splitlines():
        if ln.strip():
            rec = json.loads(ln)
            if rec.get("action_id") == action_id:
                return rec.get("result_status")
    return None


class DirectRunCaptureTestCase(unittest.TestCase):
    """CONTRACT §34 (v0.34.0): capture with mode="run" skips the proposal gate."""

    def setUp(self):
        config.ensure_state_dirs()
        if config.REGISTRY_DIR.exists():
            for p in config.REGISTRY_DIR.glob("*.yaml"):
                p.unlink()
        for p in config.INBOX_DIR.glob("*.json"):
            p.unlink()
        if _APPLIED.exists():
            _APPLIED.unlink()
        (config.STATE_DIR / "sync.json").unlink(missing_ok=True)
        actd._SYNC_ACTIVE_CACHE = None

    def _write_capture(self, text, mode="run"):
        # exact client shape (Mac AppDelegate / shared InboxAction.capture):
        # sorted keys, mode only present when the run box was used.
        payload = {"action": "capture", "text": text,
                   "ts": "2026-07-15T00:00:00Z"}
        if mode is not None:
            payload["mode"] = mode
        aid = f"capture-{uuid.uuid4()}"
        (config.INBOX_DIR / f"{aid}.json").write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
            encoding="utf-8")
        return aid

    def test_run_mode_lands_approved_with_direct_run_bookkeeping(self):
        text = "把 my-bench 的周报数据整理成一页摘要"
        self._write_capture(text)
        self.assertEqual(actd.process_inbox(), 1)

        entries = [r for r in registry.load_all() if r.title == text]
        self.assertEqual(len(entries), 1)
        req = entries[0]
        self.assertEqual(req.status, State.APPROVED.value)
        # same minimal card as a plain capture: 原话进 sources
        self.assertEqual(req.sources[0]["channel"], "quick_capture")
        self.assertEqual(req.sources[0]["quote"], text)
        # origin tag + approve-parity bookkeeping + no-preview-safe delivery
        self.assertIn("[direct-run]", req.notes or "")
        self.assertTrue((req.execution or {}).get("approved_at"))
        self.assertEqual(req.delivery_mode, "chat")
        self.assertIsNone(req.target_repo)  # dispatch falls back to the workbench
        self.assertEqual(list(config.INBOX_DIR.glob("*.json")), [])

    def test_run_mode_card_gets_dispatched_on_next_pass(self):
        text = "查一下上周的 crash 日志并总结原因"
        self._write_capture(text)
        actd.process_inbox()

        fake = mock.Mock()
        fake.DispatchError = executor.DispatchError

        def _dispatch(req, cfg):
            req.set_status(State.EXECUTING)
            req.execution = {"session_id": "e88561e5"}
            registry.save(req)
            return req

        fake.dispatch.side_effect = _dispatch
        with mock.patch.object(actd, "executor", fake):
            n = actd.dispatch_approved(config.Config())
        self.assertEqual(n, 1)
        req = [r for r in registry.load_all() if r.title == text][0]
        self.assertEqual(req.status, State.EXECUTING.value)
        self.assertEqual((req.execution or {}).get("session_id"), "e88561e5")

    def test_run_mode_empty_text_acked_noop(self):
        _activate_sync()
        aid = self._write_capture("   ")
        actd.process_inbox()
        self.assertEqual(registry.load_all(), [])
        self.assertEqual(_ack_for(aid), "noop")
        self.assertEqual(list(config.INBOX_DIR.glob("*.json")), [])

    def test_run_mode_non_string_text_acked_noop(self):
        _activate_sync()
        aid = self._write_capture(["not", "a", "string"])
        actd.process_inbox()
        self.assertEqual(registry.load_all(), [])
        self.assertEqual(_ack_for(aid), "noop")

    def test_run_mode_happy_path_acked_running(self):
        _activate_sync()
        aid = self._write_capture("跑一个小任务")
        actd.process_inbox()
        self.assertEqual(_ack_for(aid), "running")

    def test_absent_mode_keeps_todays_raising_behavior(self):
        text = "老路径不受影响"
        self._write_capture(text, mode=None)
        actd.process_inbox()
        req = [r for r in registry.load_all() if r.title == text][0]
        self.assertEqual(req.status, State.RAISING.value)
        self.assertEqual(req.notes, "from app quick capture")
        self.assertEqual(req.delivery_mode, "repo")

    def test_unknown_mode_fail_safes_to_proposal_path(self):
        # junk must never silently start an agent — anything but "run" behaves
        # exactly like today's capture.
        text = "垃圾 mode 走提案路径"
        self._write_capture(text, mode="yolo")
        actd.process_inbox()
        req = [r for r in registry.load_all() if r.title == text][0]
        self.assertEqual(req.status, State.RAISING.value)

    def test_run_mode_same_text_twice_never_double_cards(self):
        text = "同一句话不重复开跑"
        self._write_capture(text)
        actd.process_inbox()
        self._write_capture(text)
        actd.process_inbox()
        entries = [r for r in registry.load_all() if r.title == text]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].status, State.APPROVED.value)

    def test_run_mode_promotes_matching_open_proposal_instead_of_twin(self):
        # the matched proposal carries LLM-chosen REPO routing — promotion must
        # strip it (direct-run skipped the preview, so no branch/PR may land in
        # that repo) and leave an honest notes tag about the reroute.
        text = "已有提案卡的同一句话"
        existing = Requirement(id=registry.next_id(), title=text,
                               status=State.CARD_SENT.value,
                               delivery_mode="repo",
                               target_repo="/tmp/llm-routed-repo",
                               sources=[{"who": "zelin", "channel": "quick_capture",
                                         "date": "2026-07-14", "quote": text}])
        registry.save(existing)

        self._write_capture(text)
        actd.process_inbox()
        entries = [r for r in registry.load_all() if r.title == text]
        self.assertEqual(len(entries), 1)
        got = entries[0]
        self.assertEqual(got.id, existing.id)
        self.assertEqual(got.status, State.APPROVED.value)
        self.assertEqual(got.delivery_mode, "chat")
        self.assertIsNone(got.target_repo)
        self.assertIn("[direct-run] 交付改为 chat", got.notes or "")

    def test_run_mode_promoted_raising_card_forced_to_chat(self):
        # a plain-capture card mid-expansion defaults to delivery_mode="repo";
        # a direct-run of the same text must promote it AND force chat.
        text = "先普通捕获再直接开跑的同一句话"
        self._write_capture(text, mode=None)
        actd.process_inbox()
        req = [r for r in registry.load_all() if r.title == text][0]
        self.assertEqual(req.status, State.RAISING.value)
        self.assertEqual(req.delivery_mode, "repo")

        self._write_capture(text)
        actd.process_inbox()
        entries = [r for r in registry.load_all() if r.title == text]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].status, State.APPROVED.value)
        self.assertEqual(entries[0].delivery_mode, "chat")
        self.assertIsNone(entries[0].target_repo)

    def test_run_mode_never_requeues_a_card_already_running(self):
        _activate_sync()
        text = "正在跑的卡不再重复排队"
        running = Requirement(id=registry.next_id(), title=text,
                              status=State.EXECUTING.value,
                              execution={"session_id": "live1234"},
                              sources=[{"who": "zelin", "channel": "quick_capture",
                                        "date": "2026-07-14", "quote": text}])
        registry.save(running)

        aid = self._write_capture(text)
        actd.process_inbox()
        entries = [r for r in registry.load_all() if r.title == text]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].status, State.EXECUTING.value)
        self.assertEqual((entries[0].execution or {}).get("session_id"), "live1234")
        # the ask genuinely is in motion — "running" is the honest ack here
        self.assertEqual(_ack_for(aid), "running")

    def test_run_mode_reraises_delivered_card_as_new_queued_round(self):
        # CONTRACT §34: the run box must be able to re-run a finished task —
        # the resolved match forces the §3.5 re-raise (merge_or_new's increment
        # gate alone would silently fold) and the new round actually queues:
        # the finished round's session_id is archived so dispatch_approved
        # does not skip the card as "already dispatched".
        _activate_sync()
        text = "重跑上次已交付的那个任务"
        delivered = Requirement(id=registry.next_id(), title=text,
                                status=State.DELIVERED.value,
                                delivery_mode="repo",
                                target_repo="/tmp/llm-routed-repo",
                                execution={"session_id": "oldround1", "done": True,
                                           "accepted_at": "2026-07-10T00:00:00Z"},
                                sources=[{"who": "zelin", "channel": "quick_capture",
                                          "date": "2026-07-10", "quote": text}])
        registry.save(delivered)

        aid = self._write_capture(text)
        actd.process_inbox()
        entries = [r for r in registry.load_all() if r.title == text]
        self.assertEqual(len(entries), 1)
        got = entries[0]
        self.assertEqual(got.id, delivered.id)          # same card, new round
        self.assertEqual(got.status, State.APPROVED.value)
        self.assertEqual(got.delivery_mode, "chat")
        self.assertIsNone(got.target_repo)
        ex = got.execution or {}
        self.assertTrue(ex.get("reraised_at"))
        self.assertEqual(ex.get("reraised_session_id"), "oldround1")
        self.assertNotIn("session_id", ex)
        self.assertNotIn("done", ex)
        self.assertEqual(_ack_for(aid), "running")

        fake = mock.Mock()
        fake.DispatchError = executor.DispatchError

        def _dispatch(req, cfg):
            req.set_status(State.EXECUTING)
            req.execution = {"session_id": "newround2"}
            registry.save(req)
            return req

        fake.dispatch.side_effect = _dispatch
        with mock.patch.object(actd, "executor", fake):
            self.assertEqual(actd.dispatch_approved(config.Config()), 1)
        self.assertEqual(registry.load(delivered.id).status, State.EXECUTING.value)

    def test_run_mode_fold_into_review_card_is_acked_noop(self):
        # a 待验收 match files no run: sources fold, the card stays in review,
        # and the ack must say so — "running" here would be a silent fake
        # success (the Mac placeholder deliberately does not clear against
        # review rows for the same reason).
        _activate_sync()
        text = "命中一张待验收卡的同一句话"
        review = Requirement(id=registry.next_id(), title=text,
                             status=State.REVIEW.value,
                             execution={"session_id": "rev1", "done": True},
                             sources=[{"who": "zelin", "channel": "quick_capture",
                                       "date": "2026-07-12", "quote": text}])
        registry.save(review)

        aid = self._write_capture(text)
        actd.process_inbox()
        entries = [r for r in registry.load_all() if r.title == text]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].status, State.REVIEW.value)
        self.assertEqual((entries[0].execution or {}).get("session_id"), "rev1")
        self.assertEqual(_ack_for(aid), "noop")


if __name__ == "__main__":
    unittest.main()
