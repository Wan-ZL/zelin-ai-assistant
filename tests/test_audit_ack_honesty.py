"""§5.4 ack honesty for suggestion-level actions + rework (audit 2026-07-15).

CONTRACT §5.4/§32.1: result_status=running means "applied a real change" (已生效
on the phone). capture/feedback/merge_* used to be acked "running" BEFORE their
outcome was known, and rework was acked "running" even when executor.rework
returned False — the phone's durable ledger showed 已生效 for actions that had
zero effect. The _apply_* helpers now return the real disposition.

Runs entirely inside the sandbox AIASSISTANT_HOME (tests/__init__.py).
"""
import json
import unittest
import uuid
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import actd, merge_review
from act.lib import config, registry
from act.lib.registry import Requirement, State

_APPLIED = config.STATE_DIR / "sync" / "applied.jsonl"


def _activate_sync():
    config.ensure_state_dirs()
    (config.STATE_DIR / "sync.json").write_text(
        json.dumps({"mode": "cloud", "device_id": "dev-test"}), encoding="utf-8")
    actd._SYNC_ACTIVE_CACHE = None


def _mk_req(req_id, status=State.CARD_SENT.value, execution=None):
    req = Requirement(id=req_id, title=f"ack test {req_id}", status=status,
                      execution=execution)
    registry.save(req)
    return req


def _drop(body: dict) -> str:
    config.ensure_state_dirs()
    aid = str(uuid.uuid4())
    (config.INBOX_DIR / f"{aid}.json").write_text(
        json.dumps(body), encoding="utf-8")
    return aid


def _result_for(aid: str):
    if not _APPLIED.exists():
        return None
    for ln in _APPLIED.read_text(encoding="utf-8").splitlines():
        if ln.strip():
            rec = json.loads(ln)
            if rec.get("action_id") == aid:
                return rec.get("result_status")
    return None


class AckHonestyBase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()
        for p in config.INBOX_DIR.glob("*.json"):
            p.unlink()
        merge_review.MERGE_DIR.mkdir(parents=True, exist_ok=True)
        for p in merge_review.MERGE_DIR.glob("*.json"):
            p.unlink()
        try:
            _APPLIED.unlink()
        except OSError:
            pass
        _activate_sync()


class ReworkAckTestCase(AckHonestyBase):
    def test_failed_rework_acks_noop_and_leaves_card_in_review(self):
        _mk_req("R-701", status=State.REVIEW.value)
        aid = _drop({"id": "R-701", "action": "rework", "comment": "再补一段"})
        fake_exec = mock.Mock()
        fake_exec.rework.return_value = False   # transcript gone / no session
        with mock.patch.object(actd, "executor", fake_exec):
            actd.process_inbox()
        # honest: the 打回 did NOT land, so the ledger must not show 已生效
        self.assertEqual(_result_for(aid), "noop")
        self.assertEqual(registry.load("R-701").status, State.REVIEW.value)

    def test_successful_rework_still_acks_running(self):
        _mk_req("R-702", status=State.REVIEW.value)
        aid = _drop({"id": "R-702", "action": "rework", "comment": "改这里"})
        fake_exec = mock.Mock()
        fake_exec.rework.return_value = True
        with mock.patch.object(actd, "executor", fake_exec):
            actd.process_inbox()
        self.assertEqual(_result_for(aid), "running")


class SuggestionLevelAckTestCase(AckHonestyBase):
    def test_capture_with_whitespace_text_acks_noop(self):
        aid = _drop({"action": "capture", "text": "   \n  "})
        actd.process_inbox()
        self.assertEqual(_result_for(aid), "noop")

    def test_capture_with_text_acks_running(self):
        aid = _drop({"action": "capture", "text": "跟进 carving 板调研"})
        actd.process_inbox()
        self.assertEqual(_result_for(aid), "running")

    def test_feedback_with_empty_text_acks_noop(self):
        aid = _drop({"action": "feedback", "ids": [], "text": ""})
        actd.process_inbox()
        self.assertEqual(_result_for(aid), "noop")

    def test_merge_review_with_unknown_ids_acks_noop(self):
        aid = _drop({"action": "merge_review", "ids": ["R-991", "R-992"]})
        actd.process_inbox()
        self.assertEqual(_result_for(aid), "noop")

    def test_merge_apply_on_unknown_suggestion_acks_unknown(self):
        aid = _drop({"action": "merge_apply", "id": "MS-nosuch"})
        actd.process_inbox()
        self.assertEqual(_result_for(aid), "unknown")

    def test_merge_apply_on_analyzing_job_acks_noop(self):
        merge_review.write_job({"id": "MS-a1", "ids": ["R-1", "R-2"],
                                "status": "analyzing",
                                "requested_at": "2026-07-15T00:00:00Z"})
        aid = _drop({"action": "merge_apply", "id": "MS-a1"})
        actd.process_inbox()
        self.assertEqual(_result_for(aid), "noop")

    def test_merge_apply_whose_apply_crashes_acks_noop(self):
        _mk_req("R-711")
        _mk_req("R-712")
        merge_review.write_job({"id": "MS-c1", "ids": ["R-711", "R-712"],
                                "primary": "R-711", "verdict": "merge",
                                "status": "done",
                                "requested_at": "2026-07-15T00:00:00Z"})
        aid = _drop({"action": "merge_apply", "id": "MS-c1"})
        with mock.patch.object(actd, "_apply_merge_verdict",
                               side_effect=RuntimeError("apply exploded")):
            actd.process_inbox()
        self.assertEqual(_result_for(aid), "noop")
        # job stays done so the user can retry/dismiss
        self.assertEqual(merge_review.load_job("MS-c1").get("status"), "done")

    def test_merge_force_with_unknown_ids_acks_noop(self):
        aid = _drop({"action": "merge_force", "ids": ["R-981", "R-982"],
                     "primary": "R-981"})
        actd.process_inbox()
        self.assertEqual(_result_for(aid), "noop")


if __name__ == "__main__":
    unittest.main()
