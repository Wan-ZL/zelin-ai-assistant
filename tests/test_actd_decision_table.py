"""_apply_decision verb table — characterization golden (CONTRACT §5.4 / §10 / §11 / §32.2 / §44.3-S).

Every inbox verb (the 16 in the whitelist plus an unknown one) applied to a
fresh card in every on-disk status (11, four of them also with a live session),
with and without a comment (approve also on external sources — W17) — and a second table
for the verbs that read ``expected_status`` / ``via`` (comment / raise / accept /
rework) across matching, aliased and stale pins from owner and agent ingress.
For each case the §5.4 ack, the status after, the execution keys added and
removed, the notes tail (dates masked) and whether the plan changed are pinned
in ``tests/fixtures/actd_decision_table.json``.

The executor / analyze collaborators are cooperative fakes (rework succeeds,
stop confirms, harvest returns a FINAL DRAFT) so every verb reaches its real
write — the table is about actd's own verb logic, not the executor's. Minted
from the pre-refactor ``act/actd.py`` (P3b); regenerate ONLY with an
intentional verb-semantics change in the same PR (``REGEN_DECISION_TABLE=1``)
and say so in CONTRACT §10.
"""
import itertools
import json
import os
import re
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env before act imports

from act import actd
from act.lib import config, registry
from act.lib.registry import Requirement, State

FIXTURE = Path(__file__).parent / "fixtures" / "actd_decision_table.json"

VERBS = ["approve", "reject", "comment", "raise", "trash", "restore", "pin", "accept",
         "rework", "done_external", "abort_execution", "stop_to_review", "revert_review",
         "defer", "archive", "unarchive", "bogus"]
STATUSES = ["detected", "raising", "card_sent", "approved", "executing", "review",
            "delivered", "trashed", "merged", "rejected", "archived"]
WITH_SESSION = ["approved", "executing", "review", "delivered"]
SOURCES = {
    "hand": [{"who": "zelin", "channel": "quick", "date": "2026-09-01", "quote": "手打"}],
    "external": [{"who": "boss", "channel": "slack", "date": "2026-09-01", "quote": "外来"}],
}
COMMENT = {"none": None, "text": "改一下方向"}
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


class _FakeExecutor:
    """Cooperative executor: every call succeeds and touches nothing."""
    DISPATCH_STREAK_KEYS = ("dispatch_attempts", "dispatch_streak")

    class DispatchError(Exception):
        pass

    @staticmethod
    def rework(req, comment):
        return True

    @staticmethod
    def stop_session_confirmed(sid):
        return True, True, "stopped"

    @staticmethod
    def harvest_delivery(sid):
        return {"delivered_summary": "摘要", "final_draft": "FINAL DRAFT:\n正文"}


def _variants():
    """(label, status, with_session) — 11 statuses + 4 session-bearing twins."""
    for status in STATUSES:
        yield status, status, False
    for status in WITH_SESSION:
        yield f"{status}+sid", status, True


def _mint_card(rid, status, with_session, sources_key):
    ex = {"session_id": f"sid-{rid}", "dispatch_attempts": 2} if with_session else None
    req = Requirement(id=rid, title=f"表格卡 {rid}", status=State.CARD_SENT.value,
                      sources=list(SOURCES[sources_key]), execution=ex,
                      plan=None, definition_of_done=None)
    registry.save(req)
    if status == "trashed":
        registry.trash(req, "deleted")
    elif status == "archived":
        req.status = State.DELIVERED.value
        registry.save(req)
        registry.archive(req, reason="user")
    else:
        req.status = status
        registry.save(req)
    return registry.resolve(rid)


def _snapshot(req):
    ex = dict(req.execution or {})
    return {"status": str(req.status), "ex": ex, "notes": req.notes or "", "plan": req.plan}


def _row(before, after, ack):
    added = sorted(k for k in after["ex"] if k not in before["ex"])
    removed = sorted(k for k in before["ex"] if k not in after["ex"])
    tail = after["notes"][len(before["notes"]):] if after["notes"].startswith(before["notes"]) \
        else after["notes"]
    return {"ack": ack, "status": after["status"], "ex_added": added, "ex_removed": removed,
            "notes_tail": _DATE.sub("<date>", tail).strip(), "plan_changed": after["plan"] != before["plan"]}


def _wipe_registry():
    for p in list(config.REGISTRY_DIR.glob("*.yaml")) + list(registry.ARCHIVE_DIR.glob("*.yaml")):
        p.unlink()


def _apply(rid, status, with_session, sources_key, verb, comment, **kw):
    req = _mint_card(rid, status, with_session, sources_key)
    before = _snapshot(req)
    ack = actd._apply_decision(req, verb, comment, **kw)
    after = registry.resolve(rid) or req
    _wipe_registry()   # one card per case — resolve() scans the registry
    return _row(before, _snapshot(after), ack)


def build_table():
    rows = {}
    n = 0
    for (label, status, sess), (ck, comment), verb in itertools.product(
            list(_variants()), COMMENT.items(), VERBS):
        # external sources only matter to approve (W17 forced expansion) — the
        # other verbs never read the origin, so they run on the hand card only
        for src in (SOURCES if verb == "approve" else ["hand"]):
            n += 1
            key = f"verb={verb}|status={label}|sources={src}|comment={ck}"
            rows[key] = _apply(f"R-{n}", status, sess, src, verb, comment)
    for (label, status, sess), verb, pin, via in itertools.product(
            list(_variants()), ["comment", "raise", "accept", "rework"],
            ["same", "review", "card_sent"], [None, "agent"]):
        n += 1
        expected = status if pin == "same" else pin
        key = f"verb={verb}|status={label}|expected={pin}|via={via}"
        rows[key] = _apply(f"R-{n}", status, sess, "hand", verb, "改一下方向",
                           expected_status=expected, via=via, ts="2026-09-02T00:00:00Z",
                           stem=f"stem-{n}")
    return rows


class DecisionTableTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        config.ensure_state_dirs()
        _wipe_registry()
        patches = [mock.patch.object(actd, "executor", _FakeExecutor()),
                   mock.patch.object(actd, "analyze", object()),
                   mock.patch.object(actd.notify, "notify")]
        for p in patches:
            p.start()
        try:
            cls.table = build_table()
        finally:
            for p in patches:
                p.stop()
            _wipe_registry()
        if os.environ.get("REGEN_DECISION_TABLE") == "1":
            FIXTURE.write_text(json.dumps(cls.table, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                               encoding="utf-8")
        assert FIXTURE.exists(), "golden missing — REGEN_DECISION_TABLE=1 to mint"
        cls.expected = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def test_every_case_matches_the_golden(self):
        self.assertEqual(set(self.table), set(self.expected))
        diffs = {k: (self.table[k], self.expected[k]) for k in self.expected
                 if self.table[k] != self.expected[k]}
        self.assertEqual(diffs, {}, f"{len(diffs)} case(s) changed — intentional? REGEN_DECISION_TABLE=1 "
                                    "only with a CONTRACT §10 amendment")

    def test_ack_vocabulary_and_unknown_verb(self):
        acks = {row["ack"] for row in self.table.values()}
        self.assertEqual(acks, {"running", "noop", "unknown"})
        for key, row in self.table.items():
            if key.startswith("verb=bogus|") and "status=archived" not in key:
                self.assertEqual(row["ack"], "unknown", key)
                self.assertEqual(row["ex_added"], [], key)

    def test_archived_cards_only_answer_unarchive(self):
        for key, row in self.table.items():
            if "|status=archived|" in key and not key.startswith("verb=unarchive|"):
                self.assertEqual(row["ack"], "noop", key)
                self.assertEqual(row["status"], "archived", key)


if __name__ == "__main__":
    unittest.main()
