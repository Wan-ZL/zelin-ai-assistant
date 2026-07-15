"""actd 强制合并 merge_force (契约 §21bis, v0.31) — user-chosen primary, no AI.

merge_force is the §21 ``merge`` verdict executed deterministically with a
user-picked primary and NO ``claude -p`` / no MS- job file. It MUST reuse the
exact same _merge_into_primary path as the AI verdict, so this suite asserts:

- happy path: primary absorbs sources / repeated_mentions / notes, each
  secondary lands terminal ``merged`` + ``merged_into``; analytics logs
  merge_force{n,outcome=ok};
- multi-secondary in one shot;
- illegal payloads are dropped as no-ops (nothing merged, nothing logged):
  <2 distinct ids, primary ∉ ids, an id missing from the registry;
- an execution failure lands outcome=fail and NEVER propagates (the poll must
  not hang; the user can retry).

Runs entirely inside the sandbox AIASSISTANT_HOME (tests/__init__.py).
"""
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import actd
from act.lib import analytics, config, registry
from act.lib.registry import Requirement, State


def _src(quote, channel="meeting", date="2026-07-01"):
    return {"who": "manager", "channel": channel, "date": date, "quote": quote}


class MergeForceBase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()
        if analytics.EVENTS_PATH.exists():
            analytics.EVENTS_PATH.unlink()

    def _save(self, rid, quote, status=State.CARD_SENT.value, **kw):
        kw.setdefault("sources", [_src(quote)])
        req = Requirement(id=rid, title=f"Task {rid}", status=status, **kw)
        registry.save(req)
        return req

    def _events(self):
        return [e for e in analytics.read_events()
                if e.get("event") == "merge_force"]


# --------------------------------------------------------------------------- #
# happy path — same deterministic semantics as the AI 'merge' verdict
# --------------------------------------------------------------------------- #
class MergeForceHappyPathTestCase(MergeForceBase):
    def test_absorbs_secondary_into_chosen_primary(self):
        self._save("R-1", "prepare the OKR report")
        self._save("R-2", "don't forget the OKR report", repeated_mentions=2)

        actd._apply_merge_force(["R-1", "R-2"], "R-1")

        sec = registry.load("R-2")
        self.assertEqual(str(sec.status), State.MERGED.value)
        self.assertEqual(sec.merged_into, "R-1")

        primary = registry.load("R-1")
        self.assertEqual(str(primary.status), State.CARD_SENT.value)  # untouched
        self.assertEqual(int(primary.repeated_mentions), 3)  # 1 + 2
        quotes = [s.get("quote") for s in primary.sources]
        self.assertIn("prepare the OKR report", quotes)
        self.assertIn("don't forget the OKR report", quotes)
        self.assertIn("[merged] R-2", primary.notes)

        (ev,) = self._events()
        self.assertEqual(ev["outcome"], "ok")
        self.assertEqual(ev["n"], 2)

    def test_user_can_pick_a_non_first_primary(self):
        # The user钦定 R-2 as primary even though R-1 was listed first.
        self._save("R-1", "alpha")
        self._save("R-2", "beta")

        actd._apply_merge_force(["R-1", "R-2"], "R-2")

        self.assertEqual(str(registry.load("R-1").status), State.MERGED.value)
        self.assertEqual(registry.load("R-1").merged_into, "R-2")
        self.assertEqual(str(registry.load("R-2").status), State.CARD_SENT.value)

    def test_multiple_secondaries_in_one_shot(self):
        self._save("R-1", "keep me")
        self._save("R-2", "fold a")
        self._save("R-3", "fold b")

        actd._apply_merge_force(["R-1", "R-2", "R-3"], "R-1")

        self.assertEqual(str(registry.load("R-2").status), State.MERGED.value)
        self.assertEqual(str(registry.load("R-3").status), State.MERGED.value)
        self.assertEqual(str(registry.load("R-1").status), State.CARD_SENT.value)
        self.assertEqual(int(registry.load("R-1").repeated_mentions), 3)  # 1+1+1
        (ev,) = self._events()
        self.assertEqual(ev["n"], 3)


# --------------------------------------------------------------------------- #
# illegal payloads — dropped as no-ops (nothing merged, nothing logged)
# --------------------------------------------------------------------------- #
class MergeForceValidationTestCase(MergeForceBase):
    def test_fewer_than_two_distinct_ids_dropped(self):
        self._save("R-1", "a")
        # duplicate collapses to 1 distinct id
        actd._apply_merge_force(["R-1", "R-1"], "R-1")
        self.assertEqual(str(registry.load("R-1").status), State.CARD_SENT.value)
        self.assertEqual(self._events(), [])

    def test_primary_not_in_ids_dropped(self):
        self._save("R-1", "a")
        self._save("R-2", "b")
        actd._apply_merge_force(["R-1", "R-2"], "R-9")
        # nothing merged
        self.assertEqual(str(registry.load("R-1").status), State.CARD_SENT.value)
        self.assertEqual(str(registry.load("R-2").status), State.CARD_SENT.value)
        self.assertEqual(self._events(), [])

    def test_unknown_secondary_id_dropped_wholesale(self):
        # A single missing id fails the whole request — no partial merge.
        self._save("R-1", "a")
        actd._apply_merge_force(["R-1", "R-2"], "R-1")
        self.assertEqual(str(registry.load("R-1").status), State.CARD_SENT.value)
        self.assertEqual(self._events(), [])

    def test_non_list_ids_dropped(self):
        actd._apply_merge_force("R-1", "R-1")
        self.assertEqual(self._events(), [])


# --------------------------------------------------------------------------- #
# execution failure — outcome=fail, never propagates
# --------------------------------------------------------------------------- #
class MergeForceFailureTestCase(MergeForceBase):
    def test_execution_failure_logs_fail_and_does_not_raise(self):
        self._save("R-1", "a")
        self._save("R-2", "b")
        with mock.patch.object(actd, "_merge_into_primary",
                               side_effect=RuntimeError("boom")):
            # must NOT raise — a failed apply cannot hang the inbox poll
            actd._apply_merge_force(["R-1", "R-2"], "R-1")
        (ev,) = self._events()
        self.assertEqual(ev["outcome"], "fail")
        self.assertEqual(ev["n"], 2)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
