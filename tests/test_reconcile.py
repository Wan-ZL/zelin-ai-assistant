"""actd.reconcile_executing — the auto-resume/promotion state machine (P1-11).

Decision chain per executing item, against a FAKE `claude agents` roster
(actd._run_claude_agents patched; the real _index_agents join is exercised):

  live (running or idle) -> reset resume backoff, never resume
  blocked                -> waiting for the USER, never resume
  done                   -> mark done + review_at, harvest transcript,
                            promote to REVIEW (once)
  vanished, done earlier -> promote to REVIEW (missed-promotion catch-up)
  vanished, not done     -> executor.resume with exponential backoff,
                            notify at attempt 3, give up for good at 5
  no session_id          -> skip (cannot safely resume)

Runs entirely inside the sandbox AIASSISTANT_HOME (tests/__init__.py).
"""
import datetime as _dt
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import actd
from act.lib import analytics, config, registry
from act.lib.registry import Requirement, State

SID = "aaaa1111-0000-4000-8000-000000000001"  # short id = aaaa1111


def _agent(state, sid=SID, pid=None):
    """One roster entry shaped like `claude agents --json --all` output."""
    a = {"id": str(sid).split("-")[0], "sessionId": sid, "state": state,
         "cwd": "/tmp/wt", "name": "bg agent", "startedAt": "2026-07-08T00:00:00Z"}
    if pid is not None:
        a["pid"] = pid
    return a


def _iso_ago(seconds):
    t = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(seconds=seconds)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


class ReconcileBase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()
        self.cfg = config.Config()
        # never fire a real macOS notification from the suite
        p = mock.patch.object(actd.notify, "notify", mock.Mock(return_value=True))
        self.notify = p.start()
        self.addCleanup(p.stop)

    def _mk_req(self, req_id="R-900", status=State.EXECUTING.value, execution=None):
        # dispatch stores the SHORT id; the roster is dual-keyed (short + full)
        if execution is None:
            execution = {"session_id": "aaaa1111"}
        req = Requirement(id=req_id, title="状态机测试", status=status,
                          execution=execution)
        registry.save(req)
        return req

    def _reconcile(self, agents, notified=None, resume=None):
        """Run one reconcile pass against a fake roster; returns (result, resume_mock)."""
        resume = resume if resume is not None else mock.Mock(return_value=True)
        with mock.patch.object(actd, "_run_claude_agents", return_value=agents), \
             mock.patch.object(actd.executor, "resume", resume):
            n = actd.reconcile_executing(
                self.cfg, notified if notified is not None else set())
        return n, resume


# --------------------------------------------------------------------------- #
# live / blocked — never resume
# --------------------------------------------------------------------------- #
class LiveAndBlockedTestCase(ReconcileBase):
    def test_live_agent_resets_backoff_and_clears_notified(self):
        self._mk_req(execution={"session_id": "aaaa1111", "resume_attempts": 3})
        notified = {"R-900"}
        n, resume = self._reconcile([_agent("working", pid=42)], notified=notified)
        self.assertEqual(n, 0)
        resume.assert_not_called()
        req = registry.load("R-900")
        self.assertEqual(req.status, State.EXECUTING.value)
        self.assertEqual((req.execution or {}).get("resume_attempts"), 0)
        self.assertNotIn("R-900", notified)  # recovered — re-arm the notice

    def test_idle_agent_counts_as_live_not_resumed(self):
        # THE drift regression (P1-13): idle = process alive = do NOT resume,
        # even though the dashboard does not count idle as running.
        self._mk_req(execution={"session_id": "aaaa1111", "resume_attempts": 2})
        n, resume = self._reconcile([_agent("idle", pid=42)])
        self.assertEqual(n, 0)
        resume.assert_not_called()
        req = registry.load("R-900")
        self.assertEqual(req.status, State.EXECUTING.value)
        self.assertEqual((req.execution or {}).get("resume_attempts"), 0)

    def test_blocked_agent_not_resumed(self):
        # waiting for the USER — resuming a blocked agent spawns duplicates
        self._mk_req(execution={"session_id": "aaaa1111", "resume_attempts": 2})
        n, resume = self._reconcile([_agent("blocked")])
        self.assertEqual(n, 0)
        resume.assert_not_called()
        req = registry.load("R-900")
        self.assertEqual(req.status, State.EXECUTING.value)
        self.assertEqual((req.execution or {}).get("resume_attempts"), 0)


# --------------------------------------------------------------------------- #
# done -> review promotion (+ harvest)
# --------------------------------------------------------------------------- #
class DonePromotionTestCase(ReconcileBase):
    def _reconcile_done(self, harvested, agents=None):
        harvest = (mock.Mock(side_effect=harvested) if isinstance(harvested, Exception)
                   else mock.Mock(return_value=harvested))
        with mock.patch.object(actd.executor, "harvest_delivery", harvest):
            n, resume = self._reconcile(
                agents if agents is not None else [_agent("done")])
        return harvest, resume

    def test_done_promotes_to_review_with_harvest(self):
        self._mk_req()
        harvest, resume = self._reconcile_done(
            {"delivered_summary": "改动在 feat/x", "final_draft": "全文"})
        resume.assert_not_called()
        harvest.assert_called_once_with("aaaa1111")
        req = registry.load("R-900")
        self.assertEqual(req.status, State.REVIEW.value)
        ex = req.execution or {}
        self.assertTrue(ex.get("done"))
        self.assertTrue(ex.get("review_at"))
        self.assertEqual(ex.get("delivered_summary"), "改动在 feat/x")
        self.assertEqual(ex.get("final_draft"), "全文")
        events = [e.get("event") for e in analytics.read_events()
                  if e.get("req") == "R-900"]
        self.assertIn("review_promoted", events)

    def test_harvest_failure_never_blocks_promotion(self):
        self._mk_req()
        self._reconcile_done(RuntimeError("transcript exploded"))
        req = registry.load("R-900")
        self.assertEqual(req.status, State.REVIEW.value)  # promoted anyway
        ex = req.execution or {}
        self.assertTrue(ex.get("done"))
        self.assertNotIn("delivered_summary", ex)

    def test_empty_harvest_promotes_without_summary_fields(self):
        self._mk_req()
        self._reconcile_done({"delivered_summary": None, "final_draft": None})
        req = registry.load("R-900")
        self.assertEqual(req.status, State.REVIEW.value)
        ex = req.execution or {}
        self.assertNotIn("delivered_summary", ex)
        self.assertNotIn("final_draft", ex)

    def test_second_pass_after_promotion_is_noop(self):
        self._mk_req()
        harvest, _ = self._reconcile_done({"delivered_summary": "x", "final_draft": None})
        # roster still lists the agent as done; status is review now
        harvest2 = mock.Mock(return_value={"delivered_summary": "y", "final_draft": None})
        with mock.patch.object(actd.executor, "harvest_delivery", harvest2):
            self._reconcile([_agent("done")])
        req = registry.load("R-900")
        self.assertEqual(req.status, State.REVIEW.value)
        self.assertEqual((req.execution or {}).get("delivered_summary"), "x")

    def test_done_earlier_but_agent_purged_promotes_missed(self):
        # finished in a previous pass; the roster no longer lists the agent
        self._mk_req(execution={"session_id": "aaaa1111", "done": True})
        harvest, resume = self._reconcile_done({"delivered_summary": "x"}, agents=[])
        resume.assert_not_called()   # done ≠ crashed — never mistaken for dead
        harvest.assert_not_called()  # promotion only; no re-harvest
        req = registry.load("R-900")
        self.assertEqual(req.status, State.REVIEW.value)


# --------------------------------------------------------------------------- #
# vanished (dead) -> resume with exponential backoff, give up at 5
# --------------------------------------------------------------------------- #
class ResumeBackoffTestCase(ReconcileBase):
    def test_vanished_agent_is_resumed(self):
        req = self._mk_req()
        n, resume = self._reconcile([])
        self.assertEqual(n, 1)
        resume.assert_called_once()
        self.assertEqual(resume.call_args[0][0].id, req.id)
        events = [e.get("event") for e in analytics.read_events()
                  if e.get("req") == "R-900"]
        self.assertIn("auto_resume", events)

    def test_backoff_window_defers_resume(self):
        # attempts=2 -> backoff 120s; a 60s-old attempt is still cooling down
        self._mk_req(execution={"session_id": "aaaa1111", "resume_attempts": 2,
                                "last_resume_at": _iso_ago(60)})
        n, resume = self._reconcile([])
        self.assertEqual(n, 0)
        resume.assert_not_called()

    def test_backoff_elapsed_resumes_again(self):
        self._mk_req(execution={"session_id": "aaaa1111", "resume_attempts": 2,
                                "last_resume_at": _iso_ago(130)})
        n, resume = self._reconcile([])
        self.assertEqual(n, 1)
        resume.assert_called_once()

    def test_third_attempt_notifies_once(self):
        self._mk_req(execution={"session_id": "aaaa1111", "resume_attempts": 2,
                                "last_resume_at": _iso_ago(9999)})
        notified = set()
        self._reconcile([], notified=notified)
        self.assertIn("R-900", notified)
        titles = [c.args[0] for c in self.notify.call_args_list]
        self.assertIn("任务疑似中断，正在自动恢复", titles)
        # same pass shape again: already in notified -> no second notification
        self.notify.reset_mock()
        registry.save(Requirement(id="R-900", title="状态机测试",
                                  status=State.EXECUTING.value,
                                  execution={"session_id": "aaaa1111",
                                             "resume_attempts": 3,
                                             "last_resume_at": _iso_ago(9999)}))
        self._reconcile([], notified=notified)
        self.notify.assert_not_called()

    def test_first_attempt_does_not_notify(self):
        self._mk_req()
        notified = set()
        self._reconcile([], notified=notified)
        self.assertNotIn("R-900", notified)
        self.notify.assert_not_called()

    def test_five_failures_gives_up_terminally(self):
        self._mk_req(execution={"session_id": "aaaa1111", "resume_attempts": 5})
        n, resume = self._reconcile([])
        self.assertEqual(n, 0)
        resume.assert_not_called()
        req = registry.load("R-900")
        self.assertTrue((req.execution or {}).get("resume_exhausted"))
        self.assertEqual(req.status, State.EXECUTING.value)  # 状态机不动，仅停恢复
        titles = [c.args[0] for c in self.notify.call_args_list]
        self.assertTrue(any("自动恢复已放弃" in t for t in titles))
        events = [e.get("event") for e in analytics.read_events()
                  if e.get("req") == "R-900"]
        self.assertIn("auto_resume_exhausted", events)
        # terminal: the next pass skips it entirely (no repeat notification)
        self.notify.reset_mock()
        n, resume = self._reconcile([])
        self.assertEqual(n, 0)
        resume.assert_not_called()
        self.notify.assert_not_called()

    def test_resume_crash_never_kills_the_pass(self):
        self._mk_req(req_id="R-900")
        self._mk_req(req_id="R-901", execution={"session_id": "bbbb2222"})
        boom = mock.Mock(side_effect=RuntimeError("launch exploded"))
        n, resume = self._reconcile([], resume=boom)
        self.assertEqual(n, 0)              # neither counted as resumed…
        self.assertEqual(resume.call_count, 2)  # …but BOTH were attempted


# --------------------------------------------------------------------------- #
# skips and guards
# --------------------------------------------------------------------------- #
class GuardsTestCase(ReconcileBase):
    def test_no_session_id_is_skipped(self):
        self._mk_req(execution={})
        registry.save(Requirement(id="R-901", title="无 execution",
                                  status=State.EXECUTING.value, execution=None))
        n, resume = self._reconcile([])
        self.assertEqual(n, 0)
        resume.assert_not_called()

    def test_auto_resume_off_disables_resume(self):
        self._mk_req()
        self.cfg.auto_resume = False
        n, resume = self._reconcile([])
        self.assertEqual(n, 0)
        resume.assert_not_called()

    def test_non_executing_statuses_ignored(self):
        for i, st in enumerate((State.CARD_SENT.value, State.APPROVED.value,
                                State.DELIVERED.value, State.TRASHED.value)):
            self._mk_req(req_id=f"R-91{i}", status=st,
                         execution={"session_id": f"cccc000{i}"})
        n, resume = self._reconcile([])
        self.assertEqual(n, 0)
        resume.assert_not_called()

    def test_roster_failure_returns_zero(self):
        self._mk_req()
        resume = mock.Mock()
        with mock.patch.object(actd, "_run_claude_agents",
                               side_effect=OSError("claude missing")), \
             mock.patch.object(actd.executor, "resume", resume):
            n = actd.reconcile_executing(self.cfg, set())
        self.assertEqual(n, 0)
        resume.assert_not_called()


# --------------------------------------------------------------------------- #
# review attach reflow (决策 8 / §30) — attach activity is NOT a rework round;
# the registry stays review and deliverables are re-harvested on settle
# --------------------------------------------------------------------------- #
class ReviewAttachReflowTestCase(ReconcileBase):
    def test_working_again_marks_review_active(self):
        self._mk_req(status=State.REVIEW.value,
                     execution={"session_id": "aaaa1111", "done": True,
                                "delivered_summary": "旧摘要"})
        self._reconcile([_agent("working", pid=42)])
        req = registry.load("R-900")
        self.assertEqual(req.status, State.REVIEW.value)  # 状态机不动
        self.assertTrue((req.execution or {}).get("_review_active"))

    def test_settle_reharvests_and_clears_flag(self):
        self._mk_req(status=State.REVIEW.value,
                     execution={"session_id": "aaaa1111", "done": True,
                                "_review_active": True,
                                "delivered_summary": "旧摘要"})
        harvest = mock.Mock(return_value={"delivered_summary": "attach 对话后的新摘要",
                                          "final_draft": "新全文"})
        with mock.patch.object(actd.executor, "harvest_delivery", harvest):
            self._reconcile([_agent("done")])
        req = registry.load("R-900")
        ex = req.execution or {}
        self.assertNotIn("_review_active", ex)
        self.assertEqual(ex.get("delivered_summary"), "attach 对话后的新摘要")
        self.assertEqual(ex.get("final_draft"), "新全文")

    def test_settle_with_empty_harvest_keeps_old_values(self):
        self._mk_req(status=State.REVIEW.value,
                     execution={"session_id": "aaaa1111", "done": True,
                                "_review_active": True,
                                "delivered_summary": "旧摘要"})
        harvest = mock.Mock(return_value={"delivered_summary": None,
                                          "final_draft": None})
        with mock.patch.object(actd.executor, "harvest_delivery", harvest):
            self._reconcile([])  # agent purged from roster = settled too
        req = registry.load("R-900")
        ex = req.execution or {}
        self.assertNotIn("_review_active", ex)
        self.assertEqual(ex.get("delivered_summary"), "旧摘要")  # 空收割不覆盖

    def test_blocked_mid_activity_keeps_flag(self):
        self._mk_req(status=State.REVIEW.value,
                     execution={"session_id": "aaaa1111", "done": True,
                                "_review_active": True})
        harvest = mock.Mock()
        with mock.patch.object(actd.executor, "harvest_delivery", harvest):
            self._reconcile([_agent("blocked")])
        harvest.assert_not_called()  # 会话中途等输入，还没收工
        req = registry.load("R-900")
        self.assertTrue((req.execution or {}).get("_review_active"))


# --------------------------------------------------------------------------- #
# transcript-delivery promotion — FINAL DRAFT beats waiting/resume
# (2026-07-14 R-041: the finished brief sat in 需输入 for hours; the session
# was later purged from the roster, where a resume would have spawned a
# confused duplicate instead of shipping the delivery.)
# --------------------------------------------------------------------------- #
class DeliveredTranscriptPromotionTestCase(ReconcileBase):
    # Isolation, belt AND suspenders: the throttle memo is module-global and
    # OTHER suites also drive reconcile_executing over blocked/vanished
    # agents (which probes and records their sid). patch.dict scopes the memo
    # per test, and every test here uses its OWN sid so no ordering — local
    # or CI's — can leave a fresh throttle entry behind (2026-07-14: passed
    # locally on 3.13, failed on CI 3.14 through exactly that interaction).
    def setUp(self):
        super().setUp()
        p = mock.patch.dict(actd._HARVEST_PROBE_AT, clear=True)
        p.start()
        self.addCleanup(p.stop)

    def _harvest(self, final_draft, summary="last words"):
        return mock.patch.object(
            actd.executor, "harvest_delivery",
            mock.Mock(return_value={"delivered_summary": summary,
                                    "final_draft": final_draft}))

    def test_blocked_agent_with_final_draft_promotes_to_review(self):
        # a chat-mode agent that printed FINAL DRAFT settles in waiting-input
        # (a bg session never exits on its own) — that is a delivery, not a
        # question for the user.
        self._mk_req(execution={"session_id": "d1a10001"})
        with self._harvest(final_draft="成稿全文"):
            _, resume = self._reconcile([_agent("blocked", sid="d1a10001")])
        resume.assert_not_called()
        req = registry.load("R-900")
        self.assertEqual(req.status, State.REVIEW.value)
        ex = req.execution or {}
        self.assertTrue(ex.get("done"))
        self.assertEqual(ex.get("final_draft"), "成稿全文")
        self.assertEqual(ex.get("delivered_summary"), "last words")

    def test_vanished_session_with_final_draft_promotes_instead_of_resume(self):
        self._mk_req(execution={"session_id": "d1a10002"})
        with self._harvest(final_draft="成稿全文"):
            _, resume = self._reconcile([])  # roster empty — session purged
        resume.assert_not_called()
        self.assertEqual(registry.load("R-900").status, State.REVIEW.value)

    def test_vanished_session_without_final_draft_still_resumes(self):
        # delivered_summary alone is any dead session's last words — never
        # proof of delivery; the resume path must stay intact.
        self._mk_req(execution={"session_id": "d1a10003"})
        with self._harvest(final_draft=None):
            _, resume = self._reconcile([])
        resume.assert_called_once()
        self.assertEqual(registry.load("R-900").status, State.EXECUTING.value)

    def test_first_probe_survives_young_uptime(self):
        # 0.0-sentinel regression (CI runners, just-rebooted Macs): monotonic()
        # counts from boot, so with a 0.0 "never probed" default the very
        # first probe was throttled away whenever uptime < interval.
        self._mk_req(execution={"session_id": "d1a10005"})
        with self._harvest(final_draft="成稿全文"), \
                mock.patch.object(actd.time, "monotonic", return_value=5.0):
            _, resume = self._reconcile([_agent("blocked", sid="d1a10005")])
        resume.assert_not_called()
        self.assertEqual(registry.load("R-900").status, State.REVIEW.value)

    def test_blocked_probe_is_throttled_between_passes(self):
        # a genuinely blocked agent must not get its transcript re-read on
        # every 10 s daemon pass.
        self._mk_req(execution={"session_id": "d1a10004"})
        with self._harvest(final_draft=None) as harvest:
            self._reconcile([_agent("blocked", sid="d1a10004")])
            self._reconcile([_agent("blocked", sid="d1a10004")])
        self.assertEqual(harvest.call_count, 1)


if __name__ == "__main__":
    unittest.main()
