"""actd best-effort / short-circuit paths (宪法第 11 条：一条坏记录、一个缺席的
协作者、一次失败的探测都不许崩 pass) — CONTRACT §4 / §9 / §11 / §21 / §34bis / §44.3-S.

Each judgment walks one of the guarded exits the P3b CRAP round found
unwalked: dispatch with no executor (logged, nothing launched), archive_stale's
three gates (disabled / already swept / one card raising), a corrupt or
foreign-status merge job in the TTL sweep, worktree inference through
``executor.transcript_cwd`` (found / raising), the registry-guard comparison
itself failing, ``rework`` refused by the executor, and the steer flush when
the roster window closed or ``stop_session`` raised.
"""
import json
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env before act imports

from act import actd, merge_review
from act.lib import config, registry, steer
from act.lib.registry import Requirement, State


class NeverRaiseBase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        for p in list(config.REGISTRY_DIR.glob("*.yaml")) + list(registry.ARCHIVE_DIR.glob("*.yaml")):
            p.unlink()
        merge_review.MERGE_DIR.mkdir(parents=True, exist_ok=True)
        for p in merge_review.MERGE_DIR.glob("*.json"):
            p.unlink()
        self.notify = mock.patch.object(actd.notify, "notify").start()
        self.log = mock.patch.object(actd, "_log").start()
        self.addCleanup(mock.patch.stopall)

    def _logged(self, needle):
        return any(needle in str(c.args[0]) for c in self.log.call_args_list)


class DispatchWithoutExecutorTest(NeverRaiseBase):
    def test_approved_card_is_left_alone_and_logged(self):
        registry.save(Requirement(id="R-1", title="t", status=State.APPROVED.value))
        with mock.patch.object(actd, "executor", None):
            self.assertEqual(actd.dispatch_approved(config.Config()), 0)
        self.assertTrue(self._logged("dispatch: executor unavailable, cannot dispatch R-1"))
        self.assertEqual(registry.load("R-1").status, State.APPROVED.value)


class ArchiveStaleGatesTest(NeverRaiseBase):
    def _cold_delivered(self, rid="R-2"):
        registry.save(Requirement(id=rid, title="cold", status=State.DELIVERED.value,
                                  execution={"accepted_at": "2020-01-01T00:00:00Z"}))

    def test_disabled_and_already_swept_return_zero(self):
        self._cold_delivered()
        cfg = config.Config()
        cfg.archive_after_days = 0
        self.assertEqual(actd.archive_stale(cfg), 0)
        cfg.archive_after_days = 30
        (config.STATE_DIR / actd._ARCHIVE_SWEEP_MARKER).write_text("now", encoding="utf-8")
        self.addCleanup(lambda: (config.STATE_DIR / actd._ARCHIVE_SWEEP_MARKER).unlink(missing_ok=True))
        self.assertEqual(actd.archive_stale(cfg), 0)
        self.assertEqual(registry.load("R-2").status, State.DELIVERED.value)

    def test_one_card_raising_does_not_stop_the_sweep(self):
        self._cold_delivered("R-3")
        self._cold_delivered("R-4")
        (config.STATE_DIR / actd._ARCHIVE_SWEEP_MARKER).unlink(missing_ok=True)
        cfg = config.Config()
        cfg.archive_after_days = 30
        real = registry.archive

        def flaky(req, reason):
            if req.id == "R-3":
                raise RuntimeError("disk full")
            return real(req, reason=reason)

        with mock.patch.object(registry, "archive", side_effect=flaky):
            n = actd.archive_stale(cfg)
        self.assertEqual(n, 1)
        self.assertTrue(self._logged("archive: auto-archive failed for R-3"))
        self.assertTrue((config.STATE_DIR / actd._ARCHIVE_SWEEP_MARKER).exists())
        (config.STATE_DIR / actd._ARCHIVE_SWEEP_MARKER).unlink(missing_ok=True)


class MergeJobSweepTest(NeverRaiseBase):
    def test_corrupt_and_foreign_status_jobs(self):
        (merge_review.MERGE_DIR / "MS-bad.json").write_text("{nope", encoding="utf-8")
        (merge_review.MERGE_DIR / "MS-list.json").write_text("[1, 2]", encoding="utf-8")
        (merge_review.MERGE_DIR / "MS-odd.json").write_text(
            json.dumps({"id": "MS-odd", "status": "weird", "expires_at": "2000-01-01T00:00:00Z"}),
            encoding="utf-8")
        self.assertEqual(actd.cleanup_merge_jobs(), 2)
        self.assertEqual(sorted(p.name for p in merge_review.MERGE_DIR.glob("*.json")), ["MS-odd.json"])
        self.assertTrue(self._logged("merge: corrupt job file MS-bad.json — removed"))
        with mock.patch.object(actd, "merge_review", None):
            self.assertEqual(actd.cleanup_merge_jobs(), 0)


class SecondaryWorktreeTest(NeverRaiseBase):
    def _pair(self):
        registry.save(Requirement(id="R-1", title="主", status=State.REVIEW.value))
        registry.save(Requirement(id="R-2", title="副", status=State.CARD_SENT.value,
                                  target_repo="~/fallback",
                                  execution={"session_id": "sid-2", "delivered_summary": "s"}))

    def test_transcript_cwd_feeds_the_rework_material(self):
        self._pair()
        fake = mock.Mock()
        fake.rework = mock.Mock(return_value=True)
        fake.transcript_cwd = mock.Mock(return_value=Path("/wt/R-2"))
        fake.stop_session_confirmed = mock.Mock(return_value=(True, True, "stopped"))
        with mock.patch.object(actd, "executor", fake):
            actd._merge_into_primary("R-1", ["R-2"])
        self.assertIn("worktree：/wt/R-2；", fake.rework.call_args.args[1])

    def test_transcript_cwd_failure_falls_back_to_the_target_repo(self):
        self._pair()
        fake = mock.Mock()
        fake.rework = mock.Mock(return_value=True)
        fake.transcript_cwd = mock.Mock(side_effect=OSError("gone"))
        fake.stop_session_confirmed = mock.Mock(return_value=(True, True, "stopped"))
        with mock.patch.object(actd, "executor", fake):
            actd._merge_into_primary("R-1", ["R-2"])
        self.assertIn("worktree：~/fallback；", fake.rework.call_args.args[1])


class GuardComparisonFailureTest(NeverRaiseBase):
    def test_writes_since_raising_is_logged_and_the_harvest_proceeds(self):
        card = Requirement(id="R-g", title="清理", status=State.EXECUTING.value,
                           preset=actd.PROPOSALS_TRIAGE_PRESET)
        registry.save(card)
        snap = actd._triage_snapshot_path("R-g")
        snap.parent.mkdir(parents=True, exist_ok=True)
        snap.write_text(json.dumps({"at": "2026-09-02T00:00:00Z", "files": {"R-x.yaml": "1:1"}}),
                        encoding="utf-8")
        ex = {"registry_snapshot_ref": str(snap)}
        with mock.patch.object(registry, "writes_since", side_effect=RuntimeError("ledger locked")):
            actd._check_triage_registry_guard(card, ex)
        self.assertTrue(self._logged("guard: registry snapshot check failed for R-g"))
        self.notify.assert_not_called()
        self.assertNotIn("registry_snapshot_ref", ex)


class ReworkRefusedTest(NeverRaiseBase):
    def test_executor_refusal_and_missing_executor_ack_noop(self):
        req = Requirement(id="R-r", title="t", status=State.REVIEW.value,
                          execution={"session_id": "sid-r"})
        registry.save(req)
        fake = mock.Mock()
        fake.rework = mock.Mock(return_value=False)
        with mock.patch.object(actd, "executor", fake):
            self.assertEqual(actd._apply_decision(req, "rework", "再改"), "noop")
        self.assertTrue(self._logged("rework NOT sent (ok=False)"))
        with mock.patch.object(actd, "executor", None):
            self.assertEqual(actd._apply_decision(req, "rework", "再改"), "noop")
        self.assertTrue(self._logged("rework requested but executor unavailable"))
        with mock.patch.object(actd, "executor", fake):
            self.assertEqual(actd._apply_decision(req, "rework", "   "), "noop")
        self.assertTrue(self._logged("rework with empty feedback"))


class SteerWindowTest(NeverRaiseBase):
    def _queued(self):
        req = Requirement(id="R-s", title="t", status=State.EXECUTING.value,
                          execution={"session_id": "sid-s"})
        steer.enqueue_steer(req, "换方案", ts="t1")
        registry.save(req)
        return registry.load("R-s")

    def test_closed_window_leaves_the_queue_for_the_next_pass(self):
        req = self._queued()
        fake = mock.Mock()
        fake.briefing_window_open = mock.Mock(return_value=False)
        with mock.patch.object(actd, "executor", fake):
            actd._flush_steers(req, config.Config())
        fake.stop_session.assert_not_called()
        fake.resume.assert_not_called()
        self.assertTrue(self._logged("窗口已关"))
        self.assertEqual(len(steer.pending_steers(registry.load("R-s"))), 1)

    def test_stop_failure_records_an_attempt_and_keeps_the_queue(self):
        req = self._queued()
        fake = mock.Mock()
        fake.briefing_window_open = mock.Mock(side_effect=RuntimeError("roster down"))  # → treated as open
        fake.stop_session = mock.Mock(side_effect=OSError("no claude"))
        with mock.patch.object(actd, "executor", fake):
            actd._flush_steers(req, config.Config())
        fake.resume.assert_not_called()
        self.assertTrue(self._logged("stop_session failed（下 pass 重试）"))
        after = registry.load("R-s")
        self.assertEqual(len(steer.pending_steers(after)), 1)
        self.assertEqual(int((after.execution or {}).get("steer_attempts", 0) or 0), 1)


if __name__ == "__main__":
    unittest.main()
