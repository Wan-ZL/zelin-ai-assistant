"""merge-review actd side — the edges the P3b mutation round found unpinned (CONTRACT §21).

Covered: merge_review request validation (module unavailable / exactly two ids /
the detached Popen shape / a launch failure marks the job failed but still acks
running); merge_dismiss idempotency; verdict validity (each clause of the
unusable-job check raises); link_improvement / close_secondary loops continue
past a missing secondary; partition keeps going after an independent group and
truncates group errors to 200 chars; merge_into_primary bookkeeping edges
(repeated_mentions default 1+1, summary falls back to the title, blank former
titles filtered, deliverable carried when only ONE of final_draft /
delivered_summary is present, rework injected only for a REVIEW primary with an
executor, a session-bearing secondary is not stopped without an executor);
stop_session_tracked without an executor answers ``(False, False)``.
"""
import subprocess
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env before act imports

from act import actd, merge_review
from act.lib import config, registry
from act.lib.registry import Requirement, State


def _src(quote, channel="meeting"):
    return {"who": "manager", "channel": channel, "date": "2026-07-01", "quote": quote}


class MergeEdgeBase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()
        merge_review.MERGE_DIR.mkdir(parents=True, exist_ok=True)
        for p in merge_review.MERGE_DIR.glob("*.json"):
            p.unlink()
        mock.patch.object(actd.notify, "notify").start()
        self.addCleanup(mock.patch.stopall)

    def _save(self, rid, quote="q", status=State.CARD_SENT.value, **kw):
        kw.setdefault("sources", [_src(quote)])
        kw.setdefault("title", f"Task {rid}")
        req = Requirement(id=rid, status=status, **kw)
        registry.save(req)
        return req


class MergeReviewRequestTest(MergeEdgeBase):
    def test_module_unavailable_is_noop(self):
        self._save("R-1")
        self._save("R-2")
        with mock.patch.object(actd, "merge_review", None):
            self.assertEqual(actd._apply_merge_review(["R-1", "R-2"]), "noop")
        self.assertEqual(list(merge_review.MERGE_DIR.glob("*.json")), [])

    def test_exactly_two_distinct_cards_is_the_floor(self):
        self._save("R-1")
        self._save("R-2")
        self._save("R-3")
        with mock.patch.object(subprocess, "Popen") as popen:
            self.assertEqual(actd._apply_merge_review(["R-1", "R-1"]), "noop")
            self.assertEqual(actd._apply_merge_review(["R-1"]), "noop")
            popen.assert_not_called()
            self.assertEqual(actd._apply_merge_review(["R-1", "R-2"]), "running")
            self.assertEqual(actd._apply_merge_review(["R-1", "R-2", "R-3"]), "running")
        self.assertEqual(popen.call_count, 2)
        argv = popen.call_args_list[0].args[0]
        self.assertEqual(argv[:3], [popen.call_args_list[0].args[0][0], "-m", "act.merge_review"])
        kwargs = popen.call_args_list[0].kwargs
        self.assertIs(kwargs["start_new_session"], True)   # detached: outlives the pass
        self.assertEqual(kwargs["cwd"], str(config.HOME))
        self.assertIs(kwargs["stdin"], subprocess.DEVNULL)
        jobs = sorted(merge_review.MERGE_DIR.glob("*.json"))
        self.assertEqual(len(jobs), 2)

    def test_launch_failure_marks_the_job_failed_but_the_change_is_real(self):
        self._save("R-1")
        self._save("R-2")
        with mock.patch.object(subprocess, "Popen", side_effect=OSError("no fork")):
            self.assertEqual(actd._apply_merge_review(["R-1", "R-2"]), "running")
        job = merge_review.load_job(sorted(merge_review.MERGE_DIR.glob("*.json"))[0].stem)
        self.assertEqual(job["status"], "failed")
        self.assertIn("analysis launch failed", job.get("error", ""))


class MergeDismissTest(MergeEdgeBase):
    def test_dismiss_is_idempotent_and_records_the_previous_status(self):
        merge_review.write_job({"id": "MS-1", "ids": ["R-1", "R-2"], "primary": "R-1",
                                "status": "done", "verdict": "merge"})
        self.assertEqual(actd._apply_merge_decision("merge_dismiss", "MS-1"), "running")
        self.assertEqual(merge_review.load_job("MS-1")["status"], "dismissed")
        with mock.patch.object(merge_review, "dismiss_job") as dismiss:
            self.assertEqual(actd._apply_merge_decision("merge_dismiss", "MS-1"), "noop")
        dismiss.assert_not_called()
        self.assertEqual(actd._apply_merge_decision("merge_dismiss", ""), "unknown")
        self.assertEqual(actd._apply_merge_decision("merge_dismiss", None), "unknown")
        with mock.patch.object(actd, "merge_review", None):
            self.assertEqual(actd._apply_merge_decision("merge_dismiss", "MS-1"), "noop")


class VerdictValidityTest(MergeEdgeBase):
    def test_each_unusable_shape_raises(self):
        self._save("R-1")
        self._save("R-2")
        bad = [
            {"verdict": "teleport", "ids": ["R-1", "R-2"], "primary": "R-1"},   # unknown verdict
            {"verdict": "merge", "ids": ["R-1", "R-2"], "primary": "R-9"},      # primary not in ids
            {"verdict": "merge", "ids": ["R-1"], "primary": "R-1"},             # no secondaries
        ]
        for job in bad:
            with self.assertRaises(ValueError, msg=job):
                actd._apply_merge_verdict(job)
        actd._apply_merge_verdict({"verdict": "keep_separate", "ids": ["R-1", "R-2"], "primary": "R-9"})
        self.assertEqual(registry.load("R-2").status, State.CARD_SENT.value)

    def test_link_and_close_loops_continue_past_a_missing_secondary(self):
        self._save("R-1")
        self._save("R-3")
        actd._apply_merge_verdict({"verdict": "link_improvement",
                                   "ids": ["R-1", "R-404", "R-3"], "primary": "R-1"})
        self.assertEqual(registry.load("R-3").improvement_of, "R-1")
        self._save("R-4")
        actd._apply_merge_verdict({"verdict": "close_secondary",
                                   "ids": ["R-1", "R-404", "R-4"], "primary": "R-1"})
        self.assertEqual(registry.load("R-4").status, State.TRASHED.value)
        self.assertEqual(registry.load("R-4").trash_reason, "merged-review: 不再需要")


class PartitionEdgeTest(MergeEdgeBase):
    def test_independent_group_first_does_not_stop_the_rest(self):
        self._save("R-1")
        self._save("R-2")
        self._save("R-3")
        job = {"id": "MS-p", "verdict": "partition", "ids": ["R-1", "R-2", "R-3"],
               "primary": "R-1", "status": "done",
               "groups": [{"primary": "R-1", "ids": ["R-1"]},
                          {"primary": "R-2", "ids": ["R-2", "R-3"]}]}
        merge_review.write_job(job)
        actd._apply_merge_partition(job)
        self.assertEqual(registry.load("R-3").status, State.MERGED.value)
        self.assertEqual(registry.load("R-3").merged_into, "R-2")
        outcomes = [r["outcome"] for r in job["group_results"]]
        self.assertEqual(outcomes, ["independent", "ok"])

    def test_group_errors_are_capped_at_200_chars_and_the_job_fails_visibly(self):
        self._save("R-1")
        self._save("R-2")
        stale = [f"R-{9000 + i}" for i in range(40)]    # all missing → a very long reason
        job = {"id": "MS-q", "verdict": "partition", "ids": ["R-1", "R-2", "R-8999"] + stale,
               "primary": "R-1", "status": "done",
               "groups": [{"primary": "R-1", "ids": ["R-1"] + stale},
                          {"primary": "R-2", "ids": ["R-2", "R-8999"]}]}
        merge_review.write_job(job)
        with self.assertRaises(RuntimeError):
            actd._apply_merge_partition(job)
        results = job["group_results"]
        self.assertEqual([r["outcome"] for r in results], ["skipped", "skipped"])
        self.assertEqual(len(results[0]["error"]), 200)
        self.assertEqual(merge_review.load_job("MS-q")["status"], "failed")
        summary = actd._partition_results_summary(results)
        self.assertIn("组1（主卡 R-1）跳过：", summary)
        self.assertIn("组2（主卡 R-2）跳过：", summary)


class MergeIntoPrimaryEdgeTest(MergeEdgeBase):
    def test_mentions_default_to_one_each_and_summary_falls_back_to_the_title(self):
        self._save("R-1", repeated_mentions=None)
        self._save("R-2", repeated_mentions=None, title="副卡的标题",
                   display_title="用户改的名", former_titles=["旧名", " ", None])
        actd._merge_into_primary("R-1", ["R-2"])
        prim = registry.load("R-1")
        self.assertEqual(int(prim.repeated_mentions), 2)
        self.assertIn("[merged] R-2 并入：副卡的标题（曾用名：用户改的名 · 旧名）", prim.notes)
        self.assertNotIn("merged_deliverables", prim.execution or {})   # no deliverable to carry

    def test_empty_summary_shows_the_placeholder(self):
        self._save("R-1")
        self._save("R-2", title="", execution={"delivered_summary": ""})
        actd._merge_into_primary("R-1", ["R-2"])
        self.assertIn("[merged] R-2 并入：(无摘要)", registry.load("R-1").notes)

    def test_deliverable_is_carried_when_only_one_field_is_present(self):
        self._save("R-1")
        self._save("R-2", title=None, execution={"final_draft": "只有正文"})
        self._save("R-3", execution={"delivered_summary": "只有摘要"})
        actd._merge_into_primary("R-1", ["R-2", "R-3"])
        carried = registry.load("R-1").execution["merged_deliverables"]
        self.assertEqual([c["id"] for c in carried], ["R-2", "R-3"])
        self.assertEqual(carried[0]["title"], "")
        self.assertIsNone(carried[0]["former_titles"])
        self.assertEqual(carried[0]["final_draft"], "只有正文")
        self.assertEqual(carried[1]["delivered_summary"], "只有摘要")

    def test_rework_is_injected_only_for_a_review_primary_with_an_executor(self):
        self._save("R-1", status=State.REVIEW.value)
        self._save("R-2", execution={"delivered_summary": "s"})
        self._save("R-3", status=State.EXECUTING.value)
        self._save("R-4", execution={"delivered_summary": "s"})
        fake = mock.Mock()
        fake.rework = mock.Mock(return_value=True)
        fake.transcript_cwd = mock.Mock(return_value=None)
        with mock.patch.object(actd, "executor", fake):
            actd._merge_into_primary("R-1", ["R-2"])
            actd._merge_into_primary("R-3", ["R-4"])
        fake.rework.assert_called_once()
        self.assertEqual(fake.rework.call_args.args[0].id, "R-1")
        self.assertIn("R-2 已并入", fake.rework.call_args.args[1])

    def test_no_executor_means_no_stop_no_rework_and_still_merged(self):
        self._save("R-1", status=State.REVIEW.value)
        self._save("R-2", execution={"session_id": "sid-2", "delivered_summary": "s"})
        with mock.patch.object(actd, "executor", None), \
                mock.patch.object(actd, "_stop_session_tracked") as stop:
            actd._merge_into_primary("R-1", ["R-2"])
        stop.assert_not_called()
        self.assertEqual(registry.load("R-2").status, State.MERGED.value)

    def test_stop_session_tracked_without_executor_is_false_false(self):
        req = Requirement(id="R-9", title="t", status=State.EXECUTING.value)
        ex = {"session_id": "sid"}
        with mock.patch.object(actd, "executor", None):
            self.assertEqual(actd._stop_session_tracked(req, ex, "sid", "why"), (False, False))
        self.assertEqual(ex, {"session_id": "sid"})   # no ledger written


if __name__ == "__main__":
    unittest.main()
