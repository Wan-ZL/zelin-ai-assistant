"""actd — judgments that pin the logical survivors of the P3b round-2 mutation run
(``scripts/qa/mutate.py`` on the split ``act/lib/actd/`` modules).

Tunable constants (batch threshold, wake grace, resume window, probe interval,
24 h sweep gate) and log-only truncations were left as equivalent; every
survivor below changed an observable answer and is now pinned:

  alerts       digest-sourced cards are skipped only for dict sources with the exact
               channel; the new-card loop continues past an already-known card; the
               batch collapses at 3 fresh cards and not at 2; a reraised card without
               a note; suspended time = wall advance MINUS mono advance.
  inbox        a skipped preset capture acks ``running``; ``is_owner_ingress`` is
               None-or-"web" (web comments fold, agent comments only record).
  session      stop_session_tracked's (stopped, issued) answers drive whether the
               session id survives; no stop on a card_sent card; execution None is fine;
               a harvest failure is reported to the caller.
  decisions    W17 forced expansion needs BOTH plan and DoD empty; analyze missing
               blocks approve / raise with ``noop``; notes None gets the W17 tag.
  merge        the detached launch's success is what emits ``merge_review_requested``;
               merge_force acks by outcome.
  dispatch     auto-dispatch continues past non-proposal cards; an explicit external
               stamp on a hand card blocks; the live count is one per executing
               session and one per launch; two approved cards both log without an
               executor; a halted card does not stop the next one; stale last_error is
               cleared after a successful launch.
  housekeeping purge_trash disabled → 0; a deadline of today protects; improvement_of
               lineage in both directions protects; a sibling on the card's thread
               protects; archive_after_days=1 is enabled.
  reconcile    the FINAL DRAFT probe throttles per session and clears after the
               interval; a probe failure is a clean False; a non-preset review card
               never gets a snapshot ref; harvest_to_review without an executor still
               lands review; a failed harvest is logged, a good one is not.
"""
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env before act imports

from act import actd
from act.lib import analytics, config, registry
from act.lib.registry import Requirement, State


def _clean():
    config.ensure_state_dirs()
    for p in list(config.REGISTRY_DIR.glob("*.yaml")) + list(registry.ARCHIVE_DIR.glob("*.yaml")):
        p.unlink()


class _Exec:
    """Cooperative executor whose dispatch stamps a session like the real one."""
    DISPATCH_STREAK_KEYS = ("dispatch_attempts",)

    class DispatchError(Exception):
        pass

    def __init__(self):
        self.dispatched = []

    def dispatch(self, req, cfg):
        self.dispatched.append(req.id)
        ex = dict(req.execution or {})
        ex["session_id"] = f"sid-{req.id}"
        req.execution = ex
        registry.save(req)


class Base(unittest.TestCase):
    def setUp(self):
        _clean()
        self.notify = mock.patch.object(actd.notify, "notify").start()
        self.log = mock.patch.object(actd, "_log").start()
        self.addCleanup(mock.patch.stopall)

    def _logged(self, needle):
        return any(needle in str(c.args[0]) for c in self.log.call_args_list)


# --------------------------------------------------------------------------- #
# alerts
# --------------------------------------------------------------------------- #
def _na(*items):
    return {"needs_approval": list(items), "running": [], "review": []}


class AlertsKillsTest(Base):
    def test_digest_skip_needs_a_dict_source_with_the_exact_channel(self):
        prev = _na()
        curr = _na({"id": "R-1", "title": "digest 卡", "sources": [{"channel": "weekly-digest"}]},
                   {"id": "R-2", "title": "非 dict 来源", "sources": ["weekly-digest"]},
                   {"id": "R-3", "title": "别的渠道", "sources": [{"channel": "digest"}]})
        msgs = actd.detect_transitions(prev, curr)
        self.assertEqual([m[2] for m in msgs], ["R-2", "R-3"])

    def test_known_card_first_does_not_stop_the_scan(self):
        prev = _na({"id": "R-1", "title": "老卡"})
        curr = _na({"id": "R-1", "title": "老卡"}, {"id": "R-2", "title": "新卡"})
        msgs = actd.detect_transitions(prev, curr)
        self.assertEqual([m[2] for m in msgs], ["R-2"])

    def test_batch_threshold_is_more_than_two(self):
        two = _na({"id": "R-1", "title": "a"}, {"id": "R-2", "title": "b"})
        three = _na({"id": "R-1", "title": "a"}, {"id": "R-2", "title": "b"}, {"id": "R-3", "title": "c"})
        self.assertEqual([m[2] for m in actd.detect_transitions(_na(), two)], ["R-1", "R-2"])
        batched = actd.detect_transitions(_na(), three)
        self.assertEqual(len(batched), 1)
        self.assertEqual(batched[0][2:], (None, None))
        self.assertIn("3", batched[0][0] + batched[0][1])

    def test_reraised_card_without_a_note(self):
        with mock.patch.object(actd.notify, "msg_reraised", return_value=("t", "b")) as msg:
            msgs = actd.detect_transitions(_na(), _na({"id": "R-9", "title": "回锅", "reraised": True}))
        msg.assert_called_once_with("回锅", "")
        self.assertEqual(msgs, [("t", "b", "R-9", None)])

    def test_suspended_time_is_wall_minus_mono(self):
        cfg = config.Config()
        cfg.poll_interval_seconds = 10
        actd._wake_state.update({"last_pass": None, "last_mono": None, "grace_until": 0.0})
        self.addCleanup(actd._wake_state.update, {"last_pass": None, "last_mono": None, "grace_until": 0.0})
        self.assertTrue(actd._wake_grace(cfg, wall=1000.0, mono=100.0))      # first pass: grace
        # jump past the grace window with BOTH clocks advancing equally: a long
        # pass, not a sleep — no new grace
        self.assertFalse(actd._wake_grace(cfg, wall=1000.0 + 40 * 60, mono=100.0 + 40 * 60))
        # wall jumps 20 min while mono only 10 s: real suspension → grace again
        self.assertTrue(actd._wake_grace(cfg, wall=1000.0 + 60 * 60, mono=100.0 + 40 * 60 + 10))


# --------------------------------------------------------------------------- #
# inbox
# --------------------------------------------------------------------------- #
class InboxKillsTest(Base):
    def test_skipped_preset_capture_acks_running(self):
        registry.save(Requirement(id="R-t", title="清理", status=State.APPROVED.value,
                                  preset=actd.PROPOSALS_TRIAGE_PRESET))
        import json
        import uuid
        aid = str(uuid.uuid4())
        (config.INBOX_DIR / f"{aid}.json").write_text(json.dumps(
            {"action": "capture", "text": "清理", "mode": "run", "preset": actd.PROPOSALS_TRIAGE_PRESET}),
            encoding="utf-8")
        with mock.patch.object(actd, "_write_applied_ack") as ack, \
                mock.patch.object(actd, "_apply_capture") as cap:
            self.assertEqual(actd.process_inbox(), 1)
        ack.assert_called_once_with(aid, "running")
        cap.assert_not_called()

    def test_owner_ingress_is_none_or_web(self):
        self.assertTrue(actd._is_owner_ingress(None))
        self.assertTrue(actd._is_owner_ingress("web"))
        self.assertFalse(actd._is_owner_ingress("agent"))
        self.assertFalse(actd._is_owner_ingress("remote"))
        self.assertFalse(actd._is_owner_ingress("WEB"))
        for via, folded in (("web", True), ("agent", False)):
            req = Requirement(id=f"R-{via}", title="t", status=State.CARD_SENT.value, plan=["p"])
            registry.save(req)
            self.assertEqual(actd._apply_decision(req, "comment", "改", via=via), "running")
            after = registry.load(req.id)
            self.assertEqual("修改方向] 改" in (after.plan[-1] if isinstance(after.plan, list) else ""), folded, via)
            self.assertEqual("agent 备注" in (after.notes or ""), not folded, via)


# --------------------------------------------------------------------------- #
# session
# --------------------------------------------------------------------------- #
class SessionKillsTest(Base):
    def _fake(self, confirmed):
        fake = mock.Mock()
        fake.stop_session_confirmed = mock.Mock(return_value=confirmed)
        return fake

    def test_stop_session_tracked_answers(self):
        req = Requirement(id="R-s", title="t", status=State.EXECUTING.value)
        ex = {"stop_failed_at": "old", "stop_failed_error": "old"}
        with mock.patch.object(actd, "executor", self._fake((True, True, "stopped"))):
            self.assertEqual(actd._stop_session_tracked(req, ex, "sid", "why"), (True, True))
        self.assertNotIn("stop_failed_at", ex)
        with mock.patch.object(actd, "executor", self._fake((False, True, "still alive"))):
            self.assertEqual(actd._stop_session_tracked(req, ex, "sid", "why"), (False, True))
        self.assertEqual(ex["stop_failed_error"], "still alive")
        boom = mock.Mock()
        boom.stop_session_confirmed = mock.Mock(side_effect=RuntimeError("no roster"))
        with mock.patch.object(actd, "executor", boom):
            self.assertEqual(actd._stop_session_tracked(req, ex, "sid", "why"), (False, False))
        self.assertEqual(ex["stop_failed_error"], "RuntimeError: no roster")
        self.assertEqual(self.notify.call_count, 2)

    def test_stop_live_session_keeps_the_id_unless_a_real_stop_was_confirmed(self):
        for confirmed, kept in (((True, True, "ok"), False), ((False, True, "alive"), True),
                                ((True, False, "was dead"), True)):
            req = Requirement(id="R-l", title="t", status=State.EXECUTING.value,
                              execution={"session_id": "sid-l"})
            with mock.patch.object(actd, "executor", self._fake(confirmed)):
                actd._stop_live_session(req, "reject")
            self.assertEqual("session_id" in req.execution, kept, confirmed)
            self.assertEqual(req.execution["aborted_session_id"], "sid-l")

    def test_no_stop_for_a_proposal_card_and_execution_none_is_fine(self):
        fake = self._fake((True, True, "ok"))
        req = Requirement(id="R-p", title="t", status=State.CARD_SENT.value,
                          execution={"session_id": "sid-p"})
        with mock.patch.object(actd, "executor", fake):
            actd._stop_live_session(req, "trash")
        fake.stop_session_confirmed.assert_not_called()
        self.assertEqual(req.execution, {"session_id": "sid-p"})
        bare = Requirement(id="R-b", title="t", status=State.APPROVED.value, execution=None)
        with mock.patch.object(actd, "executor", fake):
            actd._stop_live_session(bare, "trash")
        fake.stop_session_confirmed.assert_not_called()

    def test_harvest_failure_is_reported_on_done_external(self):
        req = Requirement(id="R-h", title="t", status=State.EXECUTING.value,
                          execution={"session_id": "sid-h"})
        registry.save(req)
        fake = self._fake((True, True, "ok"))
        fake.harvest_delivery = mock.Mock(side_effect=RuntimeError("transcript gone"))
        with mock.patch.object(actd, "executor", fake):
            self.assertEqual(actd._apply_decision(req, "done_external", None), "running")
        self.assertTrue(self._logged("done_external — harvest_delivery(sid-h) failed (ignored): transcript gone"))
        self.assertEqual(registry.load("R-h").status, State.DELIVERED.value)


# --------------------------------------------------------------------------- #
# decisions
# --------------------------------------------------------------------------- #
_EXTERNAL = [{"who": "boss", "channel": "slack", "date": "2026-09-01", "quote": "x"}]


class DecisionsKillsTest(Base):
    def test_w17_forced_expansion_needs_both_plan_and_dod_empty(self):
        with_dod = Requirement(id="R-d", title="t", status=State.CARD_SENT.value,
                               sources=list(_EXTERNAL), plan=None, definition_of_done=["done"])
        registry.save(with_dod)
        self.assertEqual(actd._apply_decision(with_dod, "approve", None), "running")
        self.assertEqual(registry.load("R-d").status, State.APPROVED.value)
        bare = Requirement(id="R-e", title="t", status=State.CARD_SENT.value,
                           sources=list(_EXTERNAL), plan=None, definition_of_done=None, notes=None)
        registry.save(bare)
        self.assertEqual(actd._apply_decision(bare, "approve", None), "running")
        after = registry.load("R-e")
        self.assertEqual(after.status, State.RAISING.value)
        self.assertTrue(after.notes.startswith("[W17]"), after.notes)

    def test_analyze_missing_blocks_approve_and_raise_with_noop(self):
        ext = Requirement(id="R-f", title="t", status=State.CARD_SENT.value, sources=list(_EXTERNAL))
        registry.save(ext)
        debt = Requirement(id="R-g", title="t", status=State.DETECTED.value)
        registry.save(debt)
        with mock.patch.object(actd, "analyze", None):
            self.assertEqual(actd._apply_decision(ext, "approve", None), "noop")
            self.assertEqual(actd._apply_decision(debt, "raise", None), "noop")
        self.assertEqual(registry.load("R-f").status, State.CARD_SENT.value)
        self.assertEqual(registry.load("R-g").status, State.DETECTED.value)


# --------------------------------------------------------------------------- #
# merge
# --------------------------------------------------------------------------- #
class MergeKillsTest(Base):
    def setUp(self):
        super().setUp()
        from act import merge_review
        merge_review.MERGE_DIR.mkdir(parents=True, exist_ok=True)
        for p in merge_review.MERGE_DIR.glob("*.json"):
            p.unlink()
        registry.save(Requirement(id="R-1", title="a", status=State.CARD_SENT.value))
        registry.save(Requirement(id="R-2", title="b", status=State.CARD_SENT.value))

    def test_requested_event_only_when_the_launch_succeeded(self):
        import subprocess
        with mock.patch.object(subprocess, "Popen"), mock.patch.object(analytics, "log_event") as ev:
            actd._apply_merge_review(["R-1", "R-2"])
        self.assertIn("merge_review_requested", [c.args[0] for c in ev.call_args_list])
        with mock.patch.object(subprocess, "Popen", side_effect=OSError("x")), \
                mock.patch.object(analytics, "log_event") as ev:
            actd._apply_merge_review(["R-1", "R-2"])
        self.assertNotIn("merge_review_requested", [c.args[0] for c in ev.call_args_list])

    def test_merge_force_acks_by_outcome(self):
        with mock.patch.object(actd, "_merge_into_primary", side_effect=RuntimeError("boom")):
            self.assertEqual(actd._apply_merge_force(["R-1", "R-2"], "R-1"), "noop")
        self.assertEqual(actd._apply_merge_force(["R-1", "R-2"], "R-1"), "running")
        self.assertEqual(registry.load("R-2").status, State.MERGED.value)


# --------------------------------------------------------------------------- #
# dispatch
# --------------------------------------------------------------------------- #
def _hand(rid, status=State.CARD_SENT.value, **kw):
    kw.setdefault("sources", [{"who": "zelin", "channel": "quick", "date": "2026-09-01", "quote": "q"}])
    kw.setdefault("cost_estimate_usd", 1.0)
    kw.setdefault("target_repo", TMP_HOME)
    kw.setdefault("target_kind", "existing")
    req = Requirement(id=rid, title=f"t {rid}", status=status, **kw)
    registry.save(req)
    return req


class DispatchKillsTest(Base):
    def test_auto_dispatch_continues_past_non_proposal_cards(self):
        _hand("R-1", status=State.APPROVED.value)
        _hand("R-2")
        self.assertEqual(actd.auto_dispatch_pass(config.Config()), 1)
        self.assertEqual(registry.load("R-2").status, State.APPROVED.value)

    def test_explicit_external_stamp_blocks_a_hand_card(self):
        _hand("R-3", origin_trust="external")
        self.assertEqual(actd.auto_dispatch_pass(config.Config()), 0)
        self.assertEqual(registry.load("R-3").status, State.CARD_SENT.value)

    def test_live_count_and_cap_within_one_pass(self):
        cfg = config.Config(raw={"autodispatch": {"max_concurrent": 2}})
        _hand("R-x", status=State.EXECUTING.value, execution={"session_id": "sid-x"})
        for rid in ("R-a", "R-b", "R-c"):
            _hand(rid, status=State.APPROVED.value)
        ex = _Exec()
        with mock.patch.object(actd, "executor", ex):
            self.assertEqual(actd.dispatch_approved(cfg), 1)    # live 1 + 1 launch = cap
        self.assertEqual(ex.dispatched, ["R-a"])
        registry.delete(registry.load("R-x"))
        registry.delete(registry.load("R-a"))
        ex = _Exec()
        with mock.patch.object(actd, "executor", ex):
            self.assertEqual(actd.dispatch_approved(cfg), 2)    # both slots free
        self.assertEqual(ex.dispatched, ["R-b", "R-c"])

    def test_no_executor_logs_every_approved_card(self):
        _hand("R-a", status=State.APPROVED.value)
        _hand("R-b", status=State.APPROVED.value)
        with mock.patch.object(actd, "executor", None):
            actd.dispatch_approved(config.Config())
        self.assertTrue(self._logged("cannot dispatch R-a"))
        self.assertTrue(self._logged("cannot dispatch R-b"))

    def test_a_halted_card_does_not_stop_the_next_one(self):
        _hand("R-a", status=State.APPROVED.value, execution={"dispatch_halted": True})
        _hand("R-b", status=State.APPROVED.value)
        ex = _Exec()
        with mock.patch.object(actd, "executor", ex):
            self.assertEqual(actd.dispatch_approved(config.Config()), 1)
        self.assertEqual(ex.dispatched, ["R-b"])

    def test_stale_last_error_is_cleared_after_a_launch(self):
        _hand("R-a", status=State.APPROVED.value, execution={"last_error": "old"})
        _hand("R-b", status=State.APPROVED.value, execution={"last_error_at": "old"})
        with mock.patch.object(actd, "executor", _Exec()):
            actd.dispatch_approved(config.Config())
        for rid in ("R-a", "R-b"):
            ex = registry.load(rid).execution
            self.assertNotIn("last_error", ex, rid)
            self.assertNotIn("last_error_at", ex, rid)
            self.assertEqual(ex["session_id"], f"sid-{rid}")


# --------------------------------------------------------------------------- #
# housekeeping
# --------------------------------------------------------------------------- #
class HousekeepingKillsTest(Base):
    def setUp(self):
        super().setUp()
        (config.STATE_DIR / actd._ARCHIVE_SWEEP_MARKER).unlink(missing_ok=True)
        self.addCleanup(lambda: (config.STATE_DIR / actd._ARCHIVE_SWEEP_MARKER).unlink(missing_ok=True))

    def _cold(self, rid, **kw):
        kw.setdefault("execution", {"accepted_at": "2020-01-01T00:00:00Z"})
        registry.save(Requirement(id=rid, title="cold", status=State.DELIVERED.value, **kw))

    def _sweep(self, days=30):
        (config.STATE_DIR / actd._ARCHIVE_SWEEP_MARKER).unlink(missing_ok=True)
        cfg = config.Config()
        cfg.archive_after_days = days
        return actd.archive_stale(cfg)

    def test_purge_trash_disabled_returns_zero(self):
        cfg = config.Config()
        cfg.trash_retention_days = 0
        self.assertEqual(actd.purge_trash(cfg), 0)

    def test_deadline_today_protects(self):
        import datetime as _dt
        self._cold("R-1", deadline=_dt.date.today().isoformat())
        self._cold("R-2", deadline=(_dt.date.today() - _dt.timedelta(days=1)).isoformat())
        self.assertEqual(self._sweep(), 1)
        self.assertEqual(registry.load("R-1").status, State.DELIVERED.value)
        self.assertEqual(registry.resolve("R-2").status, State.ARCHIVED.value)

    def test_lineage_and_thread_siblings_protect(self):
        self._cold("R-1")                                                   # improvement target
        registry.save(Requirement(id="R-1i", title="改进", status=State.CARD_SENT.value, improvement_of="R-1"))
        self._cold("R-2", improvement_of="R-2b")                             # improves an open card
        registry.save(Requirement(id="R-2b", title="base", status=State.DETECTED.value))
        self._cold("R-3")                                                   # thread sibling open
        registry.save(Requirement(id="R-3s", title="sib", status=State.APPROVED.value, thread_id="R-3"))
        self._cold("R-4")                                                   # nothing protects
        self.assertEqual(self._sweep(), 1)
        for rid in ("R-1", "R-2", "R-3"):
            self.assertEqual(registry.load(rid).status, State.DELIVERED.value, rid)
        self.assertEqual(registry.resolve("R-4").status, State.ARCHIVED.value)

    def test_one_day_threshold_is_enabled(self):
        self._cold("R-5")
        self.assertEqual(self._sweep(days=1), 1)


# --------------------------------------------------------------------------- #
# reconcile
# --------------------------------------------------------------------------- #
class ReconcileKillsTest(Base):
    def setUp(self):
        super().setUp()
        actd._HARVEST_PROBE_AT.clear()
        self.addCleanup(actd._HARVEST_PROBE_AT.clear)

    def test_final_draft_probe_throttles_per_session(self):
        req = Requirement(id="R-p", title="t", status=State.EXECUTING.value)
        registry.save(req)
        fake = mock.Mock()
        fake.harvest_delivery = mock.Mock(return_value={})
        clock = [1000.0]
        with mock.patch.object(actd, "executor", fake), \
                mock.patch.object(actd.time, "monotonic", side_effect=lambda: clock[0]):
            self.assertFalse(actd._promote_if_delivered(req, {}, "sid-p"))
            clock[0] += actd._HARVEST_PROBE_INTERVAL_S - 1
            self.assertFalse(actd._promote_if_delivered(req, {}, "sid-p"))
            self.assertEqual(fake.harvest_delivery.call_count, 1)      # throttled
            clock[0] += 2
            self.assertFalse(actd._promote_if_delivered(req, {}, "sid-p"))
            self.assertEqual(fake.harvest_delivery.call_count, 2)      # interval passed
            self.assertFalse(actd._promote_if_delivered(req, {}, "sid-other"))
            self.assertEqual(fake.harvest_delivery.call_count, 3)      # per session

    def test_probe_failure_is_a_clean_false(self):
        req = Requirement(id="R-q", title="t", status=State.EXECUTING.value)
        fake = mock.Mock()
        fake.harvest_delivery = mock.Mock(side_effect=RuntimeError("gone"))
        with mock.patch.object(actd, "executor", fake):
            self.assertIs(actd._promote_if_delivered(req, {}, "sid-q"), False)

    def test_non_preset_review_card_never_gets_a_snapshot_ref(self):
        req = Requirement(id="R-r", title="t", status=State.REVIEW.value,
                          execution={"session_id": "sid-r"})
        registry.save(req)
        actd._reconcile_review_attach(req, {"sid-r": {"state": "working"}})
        ex = registry.load("R-r").execution
        self.assertTrue(ex.get("_review_active"))
        self.assertNotIn("registry_snapshot_ref", ex)

    def test_harvest_to_review_without_executor_still_lands_review(self):
        req = Requirement(id="R-n", title="t", status=State.EXECUTING.value,
                          execution={"session_id": "sid-n"})
        registry.save(req)
        ex = dict(req.execution)
        with mock.patch.object(actd, "executor", None):
            actd._harvest_to_review(req, ex, "sid-n", "[tag]", "why", interrupted_reason="blocked")
        after = registry.load("R-n")
        self.assertEqual(after.status, State.REVIEW.value)
        self.assertEqual(after.execution["interrupted_reason"], "blocked")
        self.assertIn("[tag]", after.notes)

    def test_harvest_failure_is_logged_and_success_is_not(self):
        fake = mock.Mock()
        fake.harvest_delivery = mock.Mock(side_effect=RuntimeError("gone"))
        req = Requirement(id="R-m", title="t", status=State.EXECUTING.value,
                          execution={"session_id": "sid-m"})
        registry.save(req)
        with mock.patch.object(actd, "executor", fake):
            actd._harvest_to_review(req, dict(req.execution), "sid-m", "[tag]", "why")
        self.assertTrue(self._logged("reconcile: R-m harvest_delivery(sid-m) failed (ignored): gone"))
        self.log.reset_mock()
        fake.harvest_delivery = mock.Mock(return_value={"delivered_summary": "ok"})
        req2 = Requirement(id="R-o", title="t", status=State.EXECUTING.value,
                           execution={"session_id": "sid-o"})
        registry.save(req2)
        with mock.patch.object(actd, "executor", fake):
            actd._harvest_to_review(req2, dict(req2.execution), "sid-o", "[tag]", "why")
        self.assertFalse(self._logged("failed (ignored)"))
        self.assertEqual(registry.load("R-o").execution["delivered_summary"], "ok")


if __name__ == "__main__":
    unittest.main()


# --------------------------------------------------------------------------- #
# round-1 stragglers (original actd.py run) not yet pinned above
# --------------------------------------------------------------------------- #
class DispatchLoopKillsTest(Base):
    def test_already_dispatched_and_backing_off_cards_do_not_stop_the_pass(self):
        _hand("R-a", status=State.APPROVED.value, execution={"session_id": "sid-a"})   # already dispatched
        _hand("R-b", status=State.APPROVED.value)
        _hand("R-c", status=State.APPROVED.value)
        ex = _Exec()

        class BackingOff(_Exec.DispatchError):
            pass
        ex.DispatchBackingOff = BackingOff
        real = ex.dispatch

        def flaky(req, cfg):
            if req.id == "R-b":
                raise BackingOff("in the window")
            return real(req, cfg)
        ex.dispatch = flaky
        with mock.patch.object(actd, "executor", ex):
            self.assertEqual(actd.dispatch_approved(config.Config()), 1)
        self.assertEqual(ex.dispatched, ["R-c"])
        self.assertNotIn("last_error", registry.load("R-b").execution or {})   # backoff: no trace written


class MergeSweepKillsTest(Base):
    def setUp(self):
        super().setUp()
        from act import merge_review
        self.mr = merge_review
        merge_review.MERGE_DIR.mkdir(parents=True, exist_ok=True)
        for p in merge_review.MERGE_DIR.glob("*.json"):
            p.unlink()

    def _job(self, name, **fields):
        import json
        path = self.mr.MERGE_DIR / f"{name}.json"
        path.write_text(json.dumps(fields), encoding="utf-8")
        return path

    def test_analyzing_job_without_id_is_failed_by_stem(self):
        import datetime as _dt
        old = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._job("MS-noid", status="analyzing", requested_at=old)
        with mock.patch.object(self.mr, "mark_failed") as failed:
            actd.cleanup_merge_jobs()
        failed.assert_called_once_with("MS-noid", "analysis timed out")

    def test_expiry_falls_back_to_requested_at_then_mtime(self):
        import datetime as _dt
        import os
        now = _dt.datetime.now(_dt.timezone.utc)
        stale = (now - _dt.timedelta(hours=self.mr.TTL_HOURS + 1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        fresh = (now - _dt.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._job("MS-old", status="done", requested_at=stale)           # requested_at fallback → expired
        self._job("MS-new", status="done", requested_at=fresh)           # requested_at fallback → kept
        p = self._job("MS-mtime", status="dismissed")                    # mtime fallback → expired
        t = (now - _dt.timedelta(hours=self.mr.TTL_HOURS + 1)).timestamp()
        os.utime(p, (t, t))
        self.assertEqual(actd.cleanup_merge_jobs(), 2)
        self.assertEqual([q.name for q in sorted(self.mr.MERGE_DIR.glob("*.json"))], ["MS-new.json"])


class AlertsLoopKillsTest(Base):
    def test_review_ready_scan_continues_past_a_from_review_rerun(self):
        prev = {"needs_approval": [], "running": [{"id": "R-1", "from_review": True}, {"id": "R-2"}],
                "review": []}
        curr = {"needs_approval": [], "running": [],
                "review": [{"id": "R-1", "name": "回流"}, {"id": "R-2", "name": "新交付"}]}
        msgs = actd.detect_transitions(prev, curr)
        self.assertEqual([(m[2], m[3]) for m in msgs], [("R-2", "review_ready")])


class ReconcileBookkeepingKillsTest(Base):
    def setUp(self):
        super().setUp()
        actd._HARVEST_PROBE_AT.clear()
        self.addCleanup(actd._HARVEST_PROBE_AT.clear)

    def test_exec_seconds_measures_dispatch_to_promotion(self):
        import datetime as _dt
        disp = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(seconds=120)).strftime("%Y-%m-%dT%H:%M:%SZ")
        req = Requirement(id="R-x", title="t", status=State.EXECUTING.value,
                          execution={"session_id": "sid-x", "dispatched_at": disp})
        registry.save(req)
        fake = mock.Mock()
        fake.harvest_delivery = mock.Mock(return_value={"final_draft": "FINAL DRAFT:\n正文"})
        with mock.patch.object(actd, "executor", fake), mock.patch.object(analytics, "log_event") as ev:
            self.assertTrue(actd._promote_if_delivered(req, dict(req.execution), "sid-x"))
        promoted = [c for c in ev.call_args_list if c.args[0] == "review_promoted"]
        self.assertEqual(len(promoted), 1)
        self.assertTrue(119 <= promoted[0].kwargs["exec_s"] <= 130, promoted[0].kwargs)

    def test_resume_window_boundaries(self):
        import datetime as _dt
        now = _dt.datetime(2026, 9, 2, 12, 0, 0, tzinfo=_dt.timezone.utc)

        def iso(delta_s):
            return (now - _dt.timedelta(seconds=delta_s)).strftime("%Y-%m-%dT%H:%M:%SZ")
        hist = [iso(0), iso(actd.RESUME_STORM_WINDOW_S), iso(actd.RESUME_STORM_WINDOW_S + 1), iso(-1),
                "garbage", None]
        self.assertEqual(actd._recent_resume_count({"resume_history": hist}, now=now), 2)
        self.assertEqual(actd._recent_resume_count({"resume_history": "nope"}, now=now), 0)

    def test_resuming_notice_fires_on_the_third_attempt_only(self):
        fake = mock.Mock()
        fake.harvest_delivery = mock.Mock(return_value={})
        fake.resume = mock.Mock(return_value=True)
        for attempts, expect in ((1, False), (2, True)):
            _clean()   # one executing card per round
            req = Requirement(id=f"R-n{attempts}", title="t", status=State.EXECUTING.value,
                              execution={"session_id": f"sid-n{attempts}", "resume_attempts": attempts})
            registry.save(req)
            notified = set()
            self.notify.reset_mock()
            with mock.patch.object(actd, "executor", fake), \
                    mock.patch.object(actd, "_run_claude_agents", return_value=[]):
                self.assertEqual(actd.reconcile_executing(config.Config(), notified), 1)
            self.assertEqual(req.id in notified, expect, attempts)


class RaisingAndLoopKillsTest(Base):
    def test_raising_failure_note_survives_notes_none(self):
        req = Requirement(id="R-r", title="t", status=State.RAISING.value, notes=None)
        registry.save(req)
        with mock.patch.object(actd.analyze, "expand_debt", side_effect=RuntimeError("boom")):
            self.assertEqual(actd.process_raising(config.Config()), 1)
        after = registry.load("R-r")
        self.assertEqual(after.status, State.DETECTED.value)
        self.assertEqual(after.notes, "(raise 展开失败，退回欠账)")

    def test_loop_health_inherits_a_count_of_one(self):
        import json
        path = config.STATE_DIR / actd.LOOP_HEALTH_NAME
        path.write_text(json.dumps({"consecutive_failures": 1}), encoding="utf-8")
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        self.assertEqual(actd.LoopHealthTracker().consecutive_failures, 1)
        path.write_text(json.dumps({"consecutive_failures": True}), encoding="utf-8")
        self.assertEqual(actd.LoopHealthTracker().consecutive_failures, 0)

    def test_early_dashboard_write_needs_any_activity_and_anti_nag_sets_keep_identity(self):
        resume_set, radar_set = set(), set()
        with mock.patch.object(actd, "process_inbox", return_value=1), \
                mock.patch.object(actd, "auto_dispatch_pass", return_value=0), \
                mock.patch.object(actd, "dispatch_approved", return_value=0), \
                mock.patch.object(actd, "reconcile_executing", return_value=0) as rec, \
                mock.patch.object(actd, "_housekeeping_phase"), \
                mock.patch.object(actd, "_store2_tick"), \
                mock.patch.object(actd, "_refresh_model_knobs"), \
                mock.patch.object(actd, "build_dashboard", return_value={}), \
                mock.patch.object(actd, "write_dashboard") as write, \
                mock.patch.object(actd, "update_check", None), \
                mock.patch.object(actd, "detect_transitions", return_value=[]), \
                mock.patch.object(actd, "_check_auth_failures", return_value=[]), \
                mock.patch.object(actd, "_check_radar_liveness", return_value=[]) as live, \
                mock.patch.object(actd.heartbeat, "beat"):
            actd.run_once(config.Config(), None, set(), resume_set, radar_set, interval=5)
            self.assertEqual(write.call_count, 2)          # early + final
            self.assertIs(rec.call_args.args[1], resume_set)
            self.assertIs(live.call_args.args[0], radar_set)
            write.reset_mock()
            with mock.patch.object(actd, "process_inbox", return_value=0):
                actd.run_once(config.Config(), None, set(), None, None, interval=5)
            self.assertEqual(write.call_count, 1)          # idle pass: final only


class AttachmentRefsKillsTest(Base):
    def test_non_string_attachment_entries_are_ignored_and_every_orphan_is_swept(self):
        import os
        import time as _time
        att = config.STATE_DIR / "attachments"
        att.mkdir(parents=True, exist_ok=True)
        for f in att.iterdir():
            f.unlink()
        keep = att / "keep.png"
        for name in ("keep.png", "orphan-a.png", "orphan-b.png"):
            (att / name).write_bytes(b"x")
            old = _time.time() - actd._ATTACH_GC_MAX_AGE_S - 60
            os.utime(att / name, (old, old))
        registry.save(Requirement(id="R-1", title="t", status=State.CARD_SENT.value,
                                  execution={"attachments": [123, None, f" {keep} "]}))
        from pathlib import Path
        order = [keep, att / "orphan-a.png", att / "orphan-b.png"]
        with mock.patch.object(Path, "iterdir", lambda self: iter(order) if self == att else iter([])), \
                mock.patch.object(actd, "feedback", None):
            self.assertEqual(actd._sweep_attachment_dirs(), 2)
        self.assertTrue(keep.exists())
        self.assertFalse((att / "orphan-a.png").exists())


class PurgeCountKillsTest(Base):
    def test_purge_counts_only_due_cards(self):
        cfg = config.Config()
        cfg.trash_retention_days = 7
        fresh = Requirement(id="R-f", title="t", status=State.CARD_SENT.value)
        registry.save(fresh)
        registry.trash(fresh, "deleted")
        old = Requirement(id="R-o", title="t", status=State.CARD_SENT.value)
        registry.save(old)
        registry.trash(old, "deleted")
        old = registry.load("R-o")
        old.trashed_at = "2020-01-01T00:00:00Z"
        registry.save(old)
        self.assertEqual(actd.purge_trash(cfg), 1)
        self.assertIsNone(registry.load("R-o"))
        self.assertIsNotNone(registry.load("R-f"))


# --------------------------------------------------------------------------- #
# round-2 stragglers（second look at the split modules）
# --------------------------------------------------------------------------- #
class StragglerKillsTest(Base):
    def test_steer_ts_of_the_wrong_type_is_not_stringified(self):
        from act.lib import steer
        req = Requirement(id="R-ts", title="t", status=State.EXECUTING.value,
                          execution={"session_id": "sid-ts"})
        registry.save(req)
        self.assertEqual(actd._apply_decision(req, "comment", "转向", ts=["not", "a", "ts"]), "running")
        pend = steer.pending_steers(registry.load("R-ts"))
        self.assertEqual(len(pend), 1)
        self.assertNotIn("[", str(pend[0].get("ts")))

    def test_fold_appends_to_a_string_plan(self):
        req = Requirement(id="R-sp", title="t", status=State.CARD_SENT.value, plan="第一步")
        registry.save(req)
        self.assertEqual(actd._apply_decision(req, "comment", "补一步"), "running")
        plan = registry.load("R-sp").plan
        self.assertTrue(plan.startswith("第一步\n["), plan)
        self.assertTrue(plan.endswith("修改方向] 补一步"), plan)

    def test_run_once_returns_the_dashboard_it_wrote(self):
        dash = {"needs_approval": [], "marker": 1}
        with mock.patch.object(actd, "process_inbox", return_value=0), \
                mock.patch.object(actd, "auto_dispatch_pass", return_value=0), \
                mock.patch.object(actd, "dispatch_approved", return_value=0), \
                mock.patch.object(actd, "reconcile_executing", return_value=0), \
                mock.patch.object(actd, "_housekeeping_phase"), \
                mock.patch.object(actd, "_store2_tick"), \
                mock.patch.object(actd, "_refresh_model_knobs"), \
                mock.patch.object(actd, "build_dashboard", return_value=dash), \
                mock.patch.object(actd, "write_dashboard"), \
                mock.patch.object(actd, "update_check", None), \
                mock.patch.object(actd, "detect_transitions", return_value=[]), \
                mock.patch.object(actd, "_check_auth_failures", return_value=[]), \
                mock.patch.object(actd, "_check_radar_liveness", return_value=[]), \
                mock.patch.object(actd.heartbeat, "beat"):
            self.assertIs(actd.run_once(config.Config(), None, set()), dash)

    def test_raising_expands_the_oldest_card_first(self):
        registry.save(Requirement(id="R-20", title="new", status=State.RAISING.value))
        registry.save(Requirement(id="R-3", title="old", status=State.RAISING.value))
        seen = []
        with mock.patch.object(actd.analyze, "expand_debt", side_effect=lambda r: seen.append(r.id)):
            actd.process_raising(config.Config())
        self.assertEqual(seen, ["R-3"])

    def test_feedback_refs_continue_past_a_corrupt_record(self):
        import json
        import os
        import time as _time
        from act.lib import feedback
        fdir = feedback.FEEDBACK_DIR
        fdir.mkdir(parents=True, exist_ok=True)
        for p in fdir.glob("*.json"):
            p.unlink()
        att = config.STATE_DIR / "attachments"
        att.mkdir(parents=True, exist_ok=True)
        for f in att.iterdir():
            f.unlink()
        keep = att / "fb-keep.png"
        keep.write_bytes(b"x")
        old = _time.time() - actd._ATTACH_GC_MAX_AGE_S - 60
        os.utime(keep, (old, old))
        (fdir / "a-corrupt.json").write_text("{nope", encoding="utf-8")
        (fdir / "b-good.json").write_text(json.dumps({"id": "fb", "images": [str(keep)]}), encoding="utf-8")
        self.assertEqual(actd._sweep_attachment_dirs(), 0)   # referenced by the good record → kept
        self.assertTrue(keep.exists())
        self.assertTrue(self._logged("unreadable feedback record"))

    def test_gc_runs_when_no_marker_exists(self):
        from act.lib.actd import housekeeping
        actd._ATTACH_GC_MARKER.unlink(missing_ok=True)
        with mock.patch.object(housekeeping, "sweep_attachment_dirs", return_value=0) as sweep:
            actd.gc_attachments()
        sweep.assert_called_once()
        self.assertTrue(actd._ATTACH_GC_MARKER.exists())
        actd._ATTACH_GC_MARKER.unlink(missing_ok=True)


class ReconcileCountKillsTest(Base):
    def setUp(self):
        super().setUp()
        actd._HARVEST_PROBE_AT.clear()
        self.addCleanup(actd._HARVEST_PROBE_AT.clear)

    def _run(self, fake, roster=()):
        with mock.patch.object(actd, "executor", fake), \
                mock.patch.object(actd, "_run_claude_agents", return_value=list(roster)):
            return actd.reconcile_executing(config.Config(), set())

    def test_no_session_and_promotion_count_zero(self):
        registry.save(Requirement(id="R-a", title="t", status=State.EXECUTING.value, execution={}))
        registry.save(Requirement(id="R-b", title="t", status=State.EXECUTING.value,
                                  execution={"session_id": "sid-b"}))
        fake = mock.Mock()
        fake.harvest_delivery = mock.Mock(return_value={"final_draft": "FINAL DRAFT:\n正文"})
        self.assertEqual(self._run(fake), 0)
        self.assertEqual(registry.load("R-a").status, State.EXECUTING.value)
        self.assertEqual(registry.load("R-b").status, State.REVIEW.value)
        fake.resume.assert_not_called()

    def test_garbage_last_resume_at_does_not_block_the_resume(self):
        registry.save(Requirement(id="R-g", title="t", status=State.EXECUTING.value,
                                  execution={"session_id": "sid-g", "last_resume_at": "garbage",
                                             "resume_attempts": 1}))
        fake = mock.Mock()
        fake.harvest_delivery = mock.Mock(return_value={})
        fake.resume = mock.Mock(return_value=True)
        self.assertEqual(self._run(fake), 1)
        fake.resume.assert_called_once()

    def test_reload_failure_after_brief_counts_nothing(self):
        registry.save(Requirement(id="R-bf", title="t", status=State.EXECUTING.value,
                                  execution={"session_id": "sid-bf", "pending_briefings": ["b"]}))
        fake = mock.Mock()
        fake.harvest_delivery = mock.Mock(return_value={})
        fake.brief = mock.Mock(return_value=True)
        with mock.patch.object(registry, "load", return_value=None):
            self.assertEqual(self._run(fake), 0)
        fake.brief.assert_called_once()
        self.assertTrue(self._logged("reload after brief failed"))
