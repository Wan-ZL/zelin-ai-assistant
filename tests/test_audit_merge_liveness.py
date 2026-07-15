"""Merge apply/force liveness guard (audit 2026-07-15).

A done merge suggestion stays actionable for its 24h TTL while the board moves
underneath it: the user can trash/merge/archive the primary and THEN tap 采纳
from a stale surface. Applying used to fold live secondaries into the dead
primary — terminal MERGED (no undo, rendered in no lane), deliverables later
hard-deleted with the primary at trash purge. Now the job fails visibly and
nothing is absorbed.

Runs entirely inside the sandbox AIASSISTANT_HOME (tests/__init__.py).
"""
import unittest

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import actd, merge_review
from act.lib import config, registry
from act.lib.registry import Requirement, State


def _mk_req(req_id, status=State.CARD_SENT.value, **kw):
    kw.setdefault("title", f"Task {req_id}")
    kw.setdefault("sources", [{"who": "zelin", "channel": "meeting",
                               "date": "2026-07-01", "quote": f"quote {req_id}"}])
    req = Requirement(id=req_id, status=status, **kw)
    registry.save(req)
    return req


def _mk_job(job_id, ids, primary, status="done", verdict="merge"):
    job = {"id": job_id, "ids": ids, "primary": primary,
           "status": status, "verdict": verdict,
           "requested_at": "2026-07-15T00:00:00Z"}
    merge_review.write_job(job)
    return job


class MergeLivenessBase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()
        merge_review.MERGE_DIR.mkdir(parents=True, exist_ok=True)
        for p in merge_review.MERGE_DIR.glob("*.json"):
            p.unlink()


class MergeApplyDeadPrimaryTestCase(MergeLivenessBase):
    def test_apply_on_trashed_primary_fails_job_and_spares_secondary(self):
        _mk_req("R-601", status=State.TRASHED.value,
                trashed_at="2026-07-14T00:00:00Z")
        _mk_req("R-602")
        _mk_job("MS-dead01", ["R-601", "R-602"], "R-601")

        ret = actd._apply_merge_decision("merge_apply", "MS-dead01")

        self.assertEqual(ret, "noop")
        job = merge_review.load_job("MS-dead01")
        self.assertEqual(job.get("status"), "failed")   # dies visibly, not silently
        self.assertIn("失效", job.get("error") or "")
        # the live secondary was NOT buried in terminal MERGED
        sec = registry.load("R-602")
        self.assertEqual(str(sec.status), State.CARD_SENT.value)
        self.assertIsNone(sec.merged_into)

    def test_apply_on_merged_primary_fails_job(self):
        _mk_req("R-603", status=State.MERGED.value, merged_into="R-000")
        _mk_req("R-604")
        _mk_job("MS-dead02", ["R-603", "R-604"], "R-603")
        ret = actd._apply_merge_decision("merge_apply", "MS-dead02")
        self.assertEqual(ret, "noop")
        self.assertEqual(merge_review.load_job("MS-dead02").get("status"), "failed")
        self.assertEqual(str(registry.load("R-604").status), State.CARD_SENT.value)

    def test_apply_on_live_primary_still_merges(self):
        _mk_req("R-605")
        _mk_req("R-606")
        _mk_job("MS-live01", ["R-605", "R-606"], "R-605")
        ret = actd._apply_merge_decision("merge_apply", "MS-live01")
        self.assertEqual(ret, "running")
        self.assertEqual(str(registry.load("R-606").status), State.MERGED.value)
        self.assertEqual(merge_review.load_job("MS-live01").get("status"),
                         "dismissed")


class MergeForceDeadPrimaryTestCase(MergeLivenessBase):
    def test_force_on_trashed_primary_is_dropped(self):
        _mk_req("R-611", status=State.TRASHED.value,
                trashed_at="2026-07-14T00:00:00Z")
        _mk_req("R-612")
        ret = actd._apply_merge_force(["R-611", "R-612"], "R-611")
        self.assertEqual(ret, "noop")
        sec = registry.load("R-612")
        self.assertEqual(str(sec.status), State.CARD_SENT.value)
        self.assertIsNone(sec.merged_into)


class MergeIntoPrimaryBackstopTestCase(MergeLivenessBase):
    def test_merge_into_dead_primary_raises(self):
        _mk_req("R-621", status=State.TRASHED.value,
                trashed_at="2026-07-14T00:00:00Z")
        _mk_req("R-622")
        with self.assertRaises(ValueError):
            actd._merge_into_primary("R-621", ["R-622"])
        self.assertEqual(str(registry.load("R-622").status),
                         State.CARD_SENT.value)

    def test_trashed_secondary_is_skipped_not_absorbed(self):
        _mk_req("R-631")
        _mk_req("R-632", status=State.TRASHED.value,
                trashed_at="2026-07-14T00:00:00Z")
        actd._merge_into_primary("R-631", ["R-632"])
        sec = registry.load("R-632")
        # stays restorable in the recycle bin — never flipped to terminal MERGED
        self.assertEqual(str(sec.status), State.TRASHED.value)
        self.assertIsNone(sec.merged_into)
        self.assertEqual(int(registry.load("R-631").repeated_mentions), 1)


if __name__ == "__main__":
    unittest.main()
