"""Inbox field-type poison immunity — nightly audit 2026-07-15.

The v0.31 fix closed the non-dict-JSON poison class; this closes it one field
deeper: webui/syncd forward ``comment`` (and future fields) verbatim from the
wire, so a non-string value used to AttributeError AFTER _apply_decision had
already saved — the file survived and re-crashed every mtime-ordered pass,
wedging the whole inbox and re-folding the comment into notes/plan each time.

Two layers under test:
- parse-time coercion: a non-string comment becomes None before any use;
- the per-file try/except wrapper: ANY crash while applying one decision file
  acks bad_json + deletes it, so the loop and later files survive.

Runs entirely inside the sandbox AIASSISTANT_HOME (tests/__init__.py).
"""
import json
import os
import time
import unittest
import uuid
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import actd
from act.lib import config, registry
from act.lib.registry import Requirement, State

_APPLIED = config.STATE_DIR / "sync" / "applied.jsonl"


def _mk_req(req_id, status=State.CARD_SENT.value, execution=None, notes=""):
    req = Requirement(id=req_id, title=f"poison test {req_id}", status=status,
                      execution=execution, notes=notes)
    registry.save(req)
    return req


def _drop(body: dict, mtime=None) -> str:
    config.ensure_state_dirs()
    aid = str(uuid.uuid4())
    path = config.INBOX_DIR / f"{aid}.json"
    path.write_text(json.dumps(body), encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return aid


def _acks() -> dict:
    if not _APPLIED.exists():
        return {}
    out = {}
    for ln in _APPLIED.read_text(encoding="utf-8").splitlines():
        if ln.strip():
            rec = json.loads(ln)
            out[rec.get("action_id")] = rec.get("result_status")
    return out


class PoisonBase(unittest.TestCase):
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
        (config.STATE_DIR / "sync.json").write_text(
            json.dumps({"mode": "cloud", "device_id": "dev-test"}),
            encoding="utf-8")
        actd._SYNC_ACTIVE_CACHE = None


class NonStringCommentTestCase(PoisonBase):
    def test_non_string_comment_never_wedges_the_inbox(self):
        # the finding-0 scenario verbatim: {"action":"comment","comment":5}
        # lands FIRST in mtime order, a legitimate approve behind it.
        _mk_req("R-820", notes="原有备注")
        _mk_req("R-821")
        now = time.time()
        _drop({"id": "R-820", "action": "comment", "comment": 5}, mtime=now - 60)
        _drop({"id": "R-821", "action": "approve"}, mtime=now)

        actd.process_inbox()

        # both files consumed — nothing left to re-crash the next pass
        self.assertEqual(list(config.INBOX_DIR.glob("*.json")), [])
        # the decision BEHIND the poison file still applied
        self.assertEqual(registry.load("R-821").status, State.APPROVED.value)
        # coerced to None => no tag folded, no unbounded notes/plan growth
        poisoned = registry.load("R-820")
        self.assertEqual(poisoned.notes, "原有备注")
        self.assertNotIn("修改方向", poisoned.notes)

    def test_second_pass_does_not_refold_anything(self):
        _mk_req("R-820")
        _drop({"id": "R-820", "action": "comment", "comment": {"x": 1}})
        actd.process_inbox()
        notes_after_first = registry.load("R-820").notes
        actd.process_inbox()  # poison gone — a second pass must be a no-op
        self.assertEqual(registry.load("R-820").notes, notes_after_first)

    def test_non_string_rework_comment_is_a_noop_not_a_crash(self):
        # the same unguarded .strip() lived in the rework branch
        _mk_req("R-822", status=State.REVIEW.value,
                execution={"session_id": "sess-1"})
        aid = _drop({"id": "R-822", "action": "rework", "comment": ["x"]})
        actd.process_inbox()  # must not raise
        self.assertEqual(list(config.INBOX_DIR.glob("*.json")), [])
        self.assertEqual(registry.load("R-822").status, State.REVIEW.value)
        self.assertEqual(_acks().get(aid), "noop")


class PerFileCrashWrapperTestCase(PoisonBase):
    def test_apply_crash_is_terminal_for_that_file_only(self):
        # simulate a FUTURE field-type poison: _apply_decision blows up on the
        # first file; the second must still be processed in the same pass.
        _mk_req("R-830")
        _mk_req("R-831")
        now = time.time()
        bad = _drop({"id": "R-830", "action": "approve"}, mtime=now - 60)
        good = _drop({"id": "R-831", "action": "approve"}, mtime=now)

        real_apply = actd._apply_decision

        def exploding(req, action, comment, expected_status=None, board_seq=None):
            if req.id == "R-830":
                raise AttributeError("simulated field-type poison")
            return real_apply(req, action, comment, expected_status, board_seq)

        with mock.patch.object(actd, "_apply_decision", side_effect=exploding):
            actd.process_inbox()  # must not raise

        self.assertEqual(list(config.INBOX_DIR.glob("*.json")), [])
        self.assertEqual(registry.load("R-831").status, State.APPROVED.value)
        # §5.4: the poisoned action still reaches a terminal ack, never a
        # stuck 'delivered' retry loop on the phone
        self.assertEqual(_acks().get(bad), "bad_json")
        self.assertEqual(_acks().get(good), "running")

    def test_crash_in_suggestion_level_branch_is_contained_too(self):
        aid = _drop({"action": "capture", "text": "boom"})
        with mock.patch.object(actd, "_apply_capture",
                               side_effect=RuntimeError("boom")):
            actd.process_inbox()  # must not raise
        self.assertEqual(list(config.INBOX_DIR.glob("*.json")), [])
        self.assertEqual(_acks().get(aid), "bad_json")


if __name__ == "__main__":
    unittest.main()
