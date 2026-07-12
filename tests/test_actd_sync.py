"""actd §5.4 sync-safety changes (plan of record §5.4):

  * comment/raise/accept/rework gain status guards — a stale action whose
    expected_status (pinned by the phone) no longer matches, or whose intrinsic
    precondition is wrong, is an idempotent no-op (never rips a running/moved
    card back);
  * process_inbox writes a state/sync/applied.jsonl ack line on EVERY terminal
    disposition — success (running), guarded no-op (noop), unknown/gone req
    (unknown) and bad-JSON (bad_json) — so the phone's "did it land?" is a
    durable truth, not inferred from the always-deleted inbox file.

Runs entirely inside the sandbox AIASSISTANT_HOME (tests/__init__.py) and on
macOS/Linux unchanged.
"""
import json
import unittest
import uuid
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import actd
from act.lib import config, registry
from act.lib.registry import Requirement, State

_APPLIED = config.STATE_DIR / "sync" / "applied.jsonl"


def _mk_req(req_id="R-700", status=State.CARD_SENT.value, execution=None, notes=""):
    req = Requirement(id=req_id, title="sync guard test", status=status,
                      execution=execution, notes=notes)
    registry.save(req)
    return req


def _drop(action, req_id=None, comment=None, expected_status=None,
          board_seq=None, raw=None):
    """Write one inbox file; return its action_id (= filename stem)."""
    config.ensure_state_dirs()
    aid = str(uuid.uuid4())
    path = config.INBOX_DIR / f"{aid}.json"
    if raw is not None:
        path.write_text(raw, encoding="utf-8")
        return aid
    body = {"id": req_id, "action": action, "comment": comment,
            "ts": "2026-07-08T00:00:00Z"}
    if expected_status is not None:
        body["expected_status"] = expected_status
    if board_seq is not None:
        body["board_seq"] = board_seq
    path.write_text(json.dumps(body), encoding="utf-8")
    return aid


def _acks() -> list:
    if not _APPLIED.exists():
        return []
    return [json.loads(ln) for ln in _APPLIED.read_text(encoding="utf-8").splitlines()
            if ln.strip()]


def _result_for(aid: str):
    for rec in _acks():
        if rec.get("action_id") == aid:
            return rec.get("result_status")
    return None


class SyncGuardBase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()
        for p in config.INBOX_DIR.glob("*.json"):
            p.unlink()
        try:
            _APPLIED.unlink()
        except OSError:
            pass


# --------------------------------------------------------------------------- #
# status guards — stale / wrong-precondition actions are no-ops
# --------------------------------------------------------------------------- #
class StatusGuardTestCase(SyncGuardBase):
    def test_comment_stale_expected_status_is_noop(self):
        _mk_req(status=State.EXECUTING.value)
        aid = _drop("comment", "R-700", comment="改方向", expected_status="card_sent")
        actd.process_inbox()
        self.assertEqual(registry.load("R-700").status, State.EXECUTING.value)
        self.assertEqual(_result_for(aid), "noop")

    def test_comment_on_running_card_is_noop_even_without_expected(self):
        # intrinsic guard: comment only folds on card_sent/detected — a stale
        # comment must not rip an executing card back to card_sent.
        _mk_req(status=State.EXECUTING.value)
        aid = _drop("comment", "R-700", comment="改方向")
        actd.process_inbox()
        self.assertEqual(registry.load("R-700").status, State.EXECUTING.value)
        self.assertEqual(_result_for(aid), "noop")

    def test_comment_on_card_sent_applies(self):
        _mk_req(status=State.CARD_SENT.value)
        aid = _drop("comment", "R-700", comment="加个测试")
        actd.process_inbox()
        req = registry.load("R-700")
        self.assertEqual(req.status, State.CARD_SENT.value)
        self.assertIn("加个测试", req.notes or "")
        self.assertEqual(_result_for(aid), "running")

    def test_raise_only_from_detected(self):
        _mk_req(status=State.CARD_SENT.value)
        aid = _drop("raise", "R-700")
        with mock.patch.object(actd, "analyze", mock.Mock()):
            actd.process_inbox()
        self.assertEqual(registry.load("R-700").status, State.CARD_SENT.value)
        self.assertEqual(_result_for(aid), "noop")

    def test_raise_from_detected_applies(self):
        _mk_req(status=State.DETECTED.value)
        aid = _drop("raise", "R-700")
        with mock.patch.object(actd, "analyze", mock.Mock()):
            actd.process_inbox()
        self.assertEqual(registry.load("R-700").status, State.RAISING.value)
        self.assertEqual(_result_for(aid), "running")

    def test_accept_only_from_review(self):
        _mk_req(status=State.CARD_SENT.value)
        aid = _drop("accept", "R-700")
        actd.process_inbox()
        self.assertEqual(registry.load("R-700").status, State.CARD_SENT.value)
        self.assertEqual(_result_for(aid), "noop")

    def test_accept_expected_status_mismatch_is_noop(self):
        # even though the card IS in review (intrinsic ok), a phone that saw it
        # 'delivered' must not re-accept it — expected_status guards that.
        _mk_req(status=State.REVIEW.value)
        aid = _drop("accept", "R-700", expected_status="delivered")
        actd.process_inbox()
        self.assertEqual(registry.load("R-700").status, State.REVIEW.value)
        self.assertEqual(_result_for(aid), "noop")

    def test_accept_from_review_applies(self):
        _mk_req(status=State.REVIEW.value)
        aid = _drop("accept", "R-700", expected_status="review")
        actd.process_inbox()
        self.assertEqual(registry.load("R-700").status, State.DELIVERED.value)
        self.assertEqual(_result_for(aid), "running")

    def test_rework_only_from_review(self):
        _mk_req(status=State.EXECUTING.value)
        aid = _drop("rework", "R-700", comment="打回重做")
        fake_exec = mock.Mock()
        with mock.patch.object(actd, "executor", fake_exec):
            actd.process_inbox()
        self.assertEqual(registry.load("R-700").status, State.EXECUTING.value)
        fake_exec.rework.assert_not_called()   # guard fired before executor
        self.assertEqual(_result_for(aid), "noop")

    def test_rework_from_review_applies(self):
        _mk_req(status=State.REVIEW.value)
        aid = _drop("rework", "R-700", comment="补一段")
        fake_exec = mock.Mock()
        fake_exec.rework.return_value = True
        with mock.patch.object(actd, "executor", fake_exec):
            actd.process_inbox()
        fake_exec.rework.assert_called_once()
        self.assertEqual(_result_for(aid), "running")


# --------------------------------------------------------------------------- #
# applied.jsonl ack — one line for EVERY terminal disposition
# --------------------------------------------------------------------------- #
class AppliedAckTestCase(SyncGuardBase):
    def test_success_writes_running_ack(self):
        _mk_req(status=State.CARD_SENT.value)
        aid = _drop("approve", "R-700")
        actd.process_inbox()
        self.assertEqual(_result_for(aid), "running")

    def test_guarded_noop_writes_noop_ack(self):
        _mk_req(status=State.CARD_SENT.value)   # accept only from review
        aid = _drop("accept", "R-700")
        actd.process_inbox()
        self.assertEqual(_result_for(aid), "noop")

    def test_unknown_req_writes_unknown_ack(self):
        aid = _drop("approve", "R-DOES-NOT-EXIST")
        actd.process_inbox()
        self.assertEqual(_result_for(aid), "unknown")

    def test_bad_json_writes_bad_json_ack(self):
        aid = _drop("approve", raw="{ this is not valid json")
        actd.process_inbox()
        self.assertEqual(_result_for(aid), "bad_json")

    def test_unknown_action_writes_unknown_ack(self):
        _mk_req(status=State.CARD_SENT.value)
        aid = _drop("frobnicate", "R-700")
        actd.process_inbox()
        self.assertEqual(_result_for(aid), "unknown")


if __name__ == "__main__":
    unittest.main()
