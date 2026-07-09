"""registry.merge_or_new — the dedup heart of the radar (P1-12).

Three documented paths (registry.py docstring):
  restatement (same title, no increment) -> merge sources into the parent,
    bump repeated_mentions, STATUS UNCHANGED, no new card;
  increment (new/earlier deadline, first cost estimate, soft->hard) ->
    improvement card with improvement_of=<parent-id>;
  no match -> brand-new entry; high_confidence + hard + deadline routes
    straight to card_sent, everything else lands as detected debt.

Plus the two matching guards: fuzzy title containment only above 12 chars,
and trashed/rejected/merged entries never match (决策 6: 拒绝 ≠ 已办完 —
a rejected requirement must produce a fresh card when restated).

Runs entirely inside the sandbox AIASSISTANT_HOME (tests/__init__.py).
"""
import unittest

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act.lib import config, registry
from act.lib.registry import Requirement, State

TITLE = "Prepare quarterly OKR report"


def _src(channel="meeting", date="2026-07-01", quote="prepare the OKR report"):
    return {"who": "manager", "channel": channel, "date": date, "quote": quote}


def _incoming(title=TITLE, **kw):
    kw.setdefault("sources", [_src(channel="slack", date="2026-07-02",
                                   quote="don't forget the OKR report")])
    return Requirement(id="", title=title, **kw)


class MergeBase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()

    def _parent(self, title=TITLE, status=State.CARD_SENT.value, **kw):
        kw.setdefault("sources", [_src()])
        req = Requirement(id="R-100", title=title, status=status, **kw)
        registry.save(req)
        return req

    def _all_ids(self):
        return sorted(r.id for r in registry.load_all())


# --------------------------------------------------------------------------- #
# path 1: restatement -> merge, no new card
# --------------------------------------------------------------------------- #
class RestatementTestCase(MergeBase):
    def test_restatement_merges_into_parent_no_new_card(self):
        self._parent()
        got = registry.merge_or_new(_incoming())
        self.assertEqual(got.id, "R-100")
        self.assertEqual(self._all_ids(), ["R-100"])       # 不新建卡
        self.assertEqual(got.status, State.CARD_SENT.value)  # 状态不动
        self.assertEqual(got.repeated_mentions, 2)
        self.assertEqual(len(got.sources), 2)              # slack 来源并入

    def test_identical_source_does_not_bump_mentions(self):
        self._parent()
        got = registry.merge_or_new(
            _incoming(sources=[_src()]))  # same channel/date/quote
        self.assertEqual(got.id, "R-100")
        self.assertEqual(got.repeated_mentions, 1)  # dedupe: 不重复计数
        self.assertEqual(len(got.sources), 1)

    def test_title_matching_ignores_case_and_whitespace(self):
        self._parent()
        got = registry.merge_or_new(
            _incoming(title="  prepare   Quarterly okr REPORT "))
        self.assertEqual(got.id, "R-100")
        self.assertEqual(self._all_ids(), ["R-100"])

    def test_dict_input_is_accepted(self):
        self._parent()
        got = registry.merge_or_new(
            {"title": TITLE,
             "sources": [_src(channel="slack", date="2026-07-03", quote="again")]})
        self.assertEqual(got.id, "R-100")
        self.assertEqual(got.repeated_mentions, 2)


# --------------------------------------------------------------------------- #
# fuzzy containment — only above the 12-char guard
# --------------------------------------------------------------------------- #
class FuzzyContainmentTestCase(MergeBase):
    def test_containment_merges_long_titles(self):
        self._parent()  # 28 chars normalized
        got = registry.merge_or_new(
            _incoming(title="Prepare quarterly OKR report for the exec team"))
        self.assertEqual(got.id, "R-100")
        self.assertEqual(self._all_ids(), ["R-100"])

    def test_short_containment_does_not_merge(self):
        self._parent(title="fix ci")  # 6 chars < 12 — too ambiguous to merge
        got = registry.merge_or_new(_incoming(title="fix ci today please"))
        self.assertNotEqual(got.id, "R-100")
        self.assertEqual(len(self._all_ids()), 2)

    def test_containment_boundary_at_12_chars(self):
        self._parent(title="abcdefghijkl")  # exactly 12 -> merges
        got = registry.merge_or_new(_incoming(title="abcdefghijkl now"))
        self.assertEqual(got.id, "R-100")

        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()
        self._parent(title="abcdefghijk")  # 11 -> new entry
        got = registry.merge_or_new(_incoming(title="abcdefghijk now"))
        self.assertNotEqual(got.id, "R-100")

    def test_unrelated_title_is_a_new_entry(self):
        self._parent()
        got = registry.merge_or_new(_incoming(title="Set up the training cluster"))
        self.assertNotEqual(got.id, "R-100")
        self.assertEqual(len(self._all_ids()), 2)


# --------------------------------------------------------------------------- #
# path 2: increment -> improvement card
# --------------------------------------------------------------------------- #
class IncrementTestCase(MergeBase):
    def test_earlier_deadline_creates_improvement_card(self):
        self._parent(deadline="2026-08-01")
        got = registry.merge_or_new(_incoming(deadline="2026-07-15"))
        self.assertNotEqual(got.id, "R-100")
        self.assertEqual(got.improvement_of, "R-100")
        self.assertEqual(got.deadline, "2026-07-15")
        self.assertEqual(got.status, State.DETECTED.value)  # 无 hc -> 欠账
        # parent untouched — the increment lives on the child
        parent = registry.load("R-100")
        self.assertEqual(parent.deadline, "2026-08-01")
        self.assertEqual(parent.repeated_mentions, 1)

    def test_new_deadline_on_deadlineless_parent_is_an_increment(self):
        self._parent(deadline=None)
        got = registry.merge_or_new(_incoming(deadline="2026-07-20"))
        self.assertEqual(got.improvement_of, "R-100")

    def test_later_deadline_is_a_restatement_not_increment(self):
        self._parent(deadline="2026-07-15")
        got = registry.merge_or_new(_incoming(deadline="2026-08-01"))
        self.assertEqual(got.id, "R-100")  # 更晚的 deadline 不算增量
        self.assertIsNone(got.improvement_of)

    def test_first_cost_estimate_is_an_increment(self):
        self._parent(cost_estimate_usd=None)
        got = registry.merge_or_new(_incoming(cost_estimate_usd=120.0))
        self.assertEqual(got.improvement_of, "R-100")
        self.assertEqual(got.cost_estimate_usd, 120.0)

    def test_hardness_escalation_is_an_increment(self):
        self._parent(hardness="soft")
        got = registry.merge_or_new(_incoming(hardness="hard"))
        self.assertEqual(got.improvement_of, "R-100")
        self.assertEqual(got.hardness, "hard")

    def test_high_confidence_increment_routes_to_card_sent(self):
        self._parent(deadline="2026-08-01")
        got = registry.merge_or_new(_incoming(deadline="2026-07-15", hardness="hard"),
                                    high_confidence=True)
        self.assertEqual(got.improvement_of, "R-100")
        self.assertEqual(got.status, State.CARD_SENT.value)


# --------------------------------------------------------------------------- #
# path 3: no match -> new entry, high-confidence routing
# --------------------------------------------------------------------------- #
class NewEntryRoutingTestCase(MergeBase):
    def test_new_entry_defaults_to_detected_debt(self):
        got = registry.merge_or_new(_incoming())
        self.assertTrue(got.id)  # id assigned
        self.assertEqual(got.status, State.DETECTED.value)
        self.assertEqual(got.repeated_mentions, 1)

    def test_high_confidence_hard_deadline_goes_straight_to_card(self):
        got = registry.merge_or_new(
            _incoming(hardness="hard", deadline="2026-07-20"),
            high_confidence=True)
        self.assertEqual(got.status, State.CARD_SENT.value)

    def test_high_confidence_without_deadline_stays_detected(self):
        got = registry.merge_or_new(_incoming(hardness="hard"),
                                    high_confidence=True)
        self.assertEqual(got.status, State.DETECTED.value)

    def test_high_confidence_soft_stays_detected(self):
        got = registry.merge_or_new(
            _incoming(hardness="soft", deadline="2026-07-20"),
            high_confidence=True)
        self.assertEqual(got.status, State.DETECTED.value)


# --------------------------------------------------------------------------- #
# matching guards — trash / rejected / merged never match (决策 6)
# --------------------------------------------------------------------------- #
class ExclusionTestCase(MergeBase):
    def test_trashed_parent_never_matches_new_card_created(self):
        # 决策 6: 拒绝 ≠ 已办完 — a trashed ask restated MUST re-card
        parent = self._parent()
        registry.trash(parent, "rejected")
        got = registry.merge_or_new(_incoming())
        self.assertNotEqual(got.id, "R-100")
        self.assertEqual(got.status, State.DETECTED.value)
        trashed = registry.load("R-100")
        self.assertEqual(trashed.status, State.TRASHED.value)  # 回收站不动
        self.assertEqual(trashed.repeated_mentions, 1)

    def test_rejected_parent_never_matches(self):
        self._parent(status=State.REJECTED.value)
        got = registry.merge_or_new(_incoming())
        self.assertNotEqual(got.id, "R-100")

    def test_merged_parent_never_matches(self):
        self._parent(status="merged_into:R-050")
        got = registry.merge_or_new(_incoming())
        self.assertNotEqual(got.id, "R-100")

    def test_delivered_parent_still_matches_and_absorbs_restatement(self):
        # the other half of 决策 6: done_external/accept (-> delivered) is what
        # silences later restatements, unlike a trashed reject
        self._parent(status=State.DELIVERED.value)
        got = registry.merge_or_new(_incoming())
        self.assertEqual(got.id, "R-100")
        self.assertEqual(got.status, State.DELIVERED.value)  # 静默合并，不复活
        self.assertEqual(self._all_ids(), ["R-100"])


if __name__ == "__main__":
    unittest.main()
