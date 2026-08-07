"""actd 'capture' inbox action (CONTRACT §10) — one-liner -> RAISING card,
and the v0.34.0 mode="run" variant (CONTRACT §34) — one-liner -> APPROVED card
that dispatch_approved picks up on the next pass.

Runs entirely inside the sandbox AIASSISTANT_HOME (tests/__init__.py); no LLM
is invoked (process_raising is NOT called here; dispatch is stubbed).
"""
import json
import subprocess
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

    # ------------------------------------------------------------------ #
    # §34.1 (v0.47, 2026-08-07 拍板): mode:"run" NEVER dedup-merges — every
    # run-box line files a FRESH approved card. The old disposition table
    # (promote / fold / re-raise) is abolished; these tests pin the new law.
    # ------------------------------------------------------------------ #

    def test_run_mode_same_text_twice_files_two_cards(self):
        # §34.1: repeating a line in the run box is the user's explicit intent
        # to run it again — two cards, two runs (the old single-card fold hid
        # the second ask entirely).
        text = "同一句话连发两次各开一跑"
        self._write_capture(text)
        actd.process_inbox()
        self._write_capture(text)
        actd.process_inbox()
        entries = [r for r in registry.load_all() if r.title == text]
        self.assertEqual(len(entries), 2)
        for req in entries:
            self.assertEqual(req.status, State.APPROVED.value)
            self.assertEqual(req.delivery_mode, "chat")

    def test_run_mode_same_inbox_file_replay_files_one_card(self):
        # §34.1 crash-replay 幂等：process_inbox 先 apply 后删文件（at-least-
        # once）——apply 与 unlink 之间 crash，同一 inbox 文件重放绝不铸第二张
        # approved 卡（否则一次 crash = 起两个 agent）。幂等键 = 文件 stem
        # （execution.inbox_stem）。两个不同文件 = 两张卡的判例在上面
        # （test_run_mode_same_text_twice_files_two_cards）。
        _activate_sync()
        text = "crash 后同一文件重放只此一卡"
        aid = self._write_capture(text)
        payload = (config.INBOX_DIR / f"{aid}.json").read_text(encoding="utf-8")
        actd.process_inbox()
        entries = [r for r in registry.load_all() if r.title == text]
        self.assertEqual(len(entries), 1)
        self.assertEqual((entries[0].execution or {}).get("inbox_stem"), aid)
        # 模拟 crash-replay：同名同内容的文件再次出现在 inbox
        (config.INBOX_DIR / f"{aid}.json").write_text(payload, encoding="utf-8")
        actd.process_inbox()
        entries = [r for r in registry.load_all() if r.title == text]
        self.assertEqual(len(entries), 1)          # 没有第二张卡
        self.assertEqual(_ack_for(aid), "running")  # 诚实 ack：这单确实在队里
        self.assertEqual(list(config.INBOX_DIR.glob("*.json")), [])

    def test_run_mode_unlink_failure_replay_after_dispatch_files_one_card(self):
        # 终审 P1 判例：_safe_unlink 吞 OSError——unlink 持续失败时同一 inbox
        # 文件跨 pass 存活，且 pass 间卡片已被派发（dispatch 整体重建
        # execution）。幂等键必须活过派发，否则每 pass 铸一张新卡、起一个
        # 新 agent（旧标题判重兜底已随 §34.1 拆除，重放闸是唯一防线）。
        _activate_sync()
        text = "unlink 失败跨 pass 重放不铸第二张卡"
        aid = self._write_capture(text)
        with mock.patch.object(actd, "_safe_unlink", new=lambda p: None):
            actd.process_inbox()
        self.assertTrue((config.INBOX_DIR / f"{aid}.json").exists())
        req = [r for r in registry.load_all() if r.title == text][0]
        # 真 executor.dispatch（runner 注入，绝不 spawn claude）——execution
        # 被重建为 {session_id, dispatched_at, log, inbox_stem}
        cfg = config.Config()
        cfg.memory_inject = False
        with mock.patch.object(executor, "has_remote", return_value=False), \
             mock.patch.object(executor.notify, "notify",
                               new=mock.Mock(return_value=True)):
            runner = mock.Mock(return_value=subprocess.CompletedProcess(
                ["claude"], 0, stdout="backgrounded · e88561e5\n", stderr=""))
            executor.dispatch(req, cfg, runner=runner)
        dispatched = registry.load(req.id)
        self.assertEqual(dispatched.status, State.EXECUTING.value)
        self.assertEqual((dispatched.execution or {}).get("inbox_stem"), aid)
        # 下一 pass：同一文件重放——重放闸认出 stem，不铸第二张卡
        actd.process_inbox()
        entries = [r for r in registry.load_all() if r.title == text]
        self.assertEqual(len(entries), 1)
        self.assertEqual(list(config.INBOX_DIR.glob("*.json")), [])

    def test_run_mode_never_touches_a_matching_open_proposal(self):
        # §34.1: a title hit on an open proposal no longer promotes that card —
        # the proposal keeps its state AND its LLM-chosen repo routing; the
        # run-box line files its own fresh chat-delivery card.
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
        entries = {r.id: r for r in registry.load_all() if r.title == text}
        self.assertEqual(len(entries), 2)
        untouched = entries[existing.id]
        self.assertEqual(untouched.status, State.CARD_SENT.value)
        self.assertEqual(untouched.delivery_mode, "repo")
        self.assertEqual(untouched.target_repo, "/tmp/llm-routed-repo")
        fresh = next(r for rid, r in entries.items() if rid != existing.id)
        self.assertEqual(fresh.status, State.APPROVED.value)
        self.assertEqual(fresh.delivery_mode, "chat")
        self.assertIsNone(fresh.target_repo)
        self.assertIn("[direct-run]", fresh.notes or "")

    def test_run_mode_leaves_raising_card_alone_and_files_new(self):
        # §34.1: a plain capture mid-expansion is NOT hijacked by a direct-run
        # of the same text — the raising card keeps expanding toward a
        # proposal; the run gets its own fresh approved chat card.
        text = "先普通捕获再直接开跑的同一句话"
        self._write_capture(text, mode=None)
        actd.process_inbox()
        req = [r for r in registry.load_all() if r.title == text][0]
        self.assertEqual(req.status, State.RAISING.value)
        self.assertEqual(req.delivery_mode, "repo")

        self._write_capture(text)
        actd.process_inbox()
        entries = {r.id: r for r in registry.load_all() if r.title == text}
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[req.id].status, State.RAISING.value)
        fresh = next(r for rid, r in entries.items() if rid != req.id)
        self.assertEqual(fresh.status, State.APPROVED.value)
        self.assertEqual(fresh.delivery_mode, "chat")
        self.assertIsNone(fresh.target_repo)

    def test_run_mode_title_hit_on_executing_card_files_new_card(self):
        # THE 2026-08-07 incident, pinned: two run-box messages led by the same
        # URL title-matched the EXECUTING card and were silently folded — the
        # new text never reached the session, the board showed nothing. §34.1:
        # the running card is untouched and the line files a fresh approved
        # card (a new run genuinely queues → "running" stays the honest ack).
        _activate_sync()
        text = "正在跑的卡再发一次开新跑"
        running = Requirement(id=registry.next_id(), title=text,
                              status=State.EXECUTING.value,
                              execution={"session_id": "live1234"},
                              sources=[{"who": "zelin", "channel": "quick_capture",
                                        "date": "2026-07-14", "quote": text}])
        registry.save(running)

        aid = self._write_capture(text)
        actd.process_inbox()
        entries = {r.id: r for r in registry.load_all() if r.title == text}
        self.assertEqual(len(entries), 2)
        untouched = entries[running.id]
        self.assertEqual(untouched.status, State.EXECUTING.value)
        self.assertEqual((untouched.execution or {}).get("session_id"), "live1234")
        fresh = next(r for rid, r in entries.items() if rid != running.id)
        self.assertEqual(fresh.status, State.APPROVED.value)
        self.assertEqual(fresh.delivery_mode, "chat")
        self.assertTrue((fresh.execution or {}).get("approved_at"))
        self.assertEqual(_ack_for(aid), "running")

    def test_run_mode_title_hit_on_delivered_card_files_new_card(self):
        # §34.1: re-running a finished task = a FRESH card, not a re-raise of
        # the old one — the delivered card keeps its history/acceptance intact
        # and the new round dispatches from the new card on the next pass.
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
        entries = {r.id: r for r in registry.load_all() if r.title == text}
        self.assertEqual(len(entries), 2)
        untouched = entries[delivered.id]
        self.assertEqual(untouched.status, State.DELIVERED.value)
        ex = untouched.execution or {}
        self.assertEqual(ex.get("session_id"), "oldround1")   # history intact
        self.assertTrue(ex.get("done"))
        fresh = next(r for rid, r in entries.items() if rid != delivered.id)
        self.assertEqual(fresh.status, State.APPROVED.value)
        self.assertEqual(fresh.delivery_mode, "chat")
        self.assertIsNone(fresh.target_repo)
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
        self.assertEqual(registry.load(fresh.id).status, State.EXECUTING.value)
        # the OLD card never re-enters the pipeline
        self.assertEqual(registry.load(delivered.id).status, State.DELIVERED.value)

    def test_run_mode_title_hit_on_review_card_files_new_card(self):
        # §34.1: a 待验收 match no longer folds-and-noops — the review card is
        # untouched and a fresh run queues, so "running" is now the honest ack
        # (something genuinely started; the old noop covered the fold-only case).
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
        entries = {r.id: r for r in registry.load_all() if r.title == text}
        self.assertEqual(len(entries), 2)
        untouched = entries[review.id]
        self.assertEqual(untouched.status, State.REVIEW.value)
        self.assertEqual((untouched.execution or {}).get("session_id"), "rev1")
        fresh = next(r for rid, r in entries.items() if rid != review.id)
        self.assertEqual(fresh.status, State.APPROVED.value)
        self.assertEqual(_ack_for(aid), "running")


if __name__ == "__main__":
    unittest.main()
