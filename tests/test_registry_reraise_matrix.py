"""registry — the re-raise / follow-up / merge_or_new decision matrix
(卡片生命周期 §3.3–§3.5, §45 cap, §50 stamps, §60 ids).

Characterization net for the P3b split of ``reraise_or_followup`` and
``merge_or_new_with_kind``: every exit (dead-end, live-canonical fold, pure
restatement fold, open-follow-up fold, re-raise, follow-up child), the
``actionable`` override vs ``_carries_increment``, the §45 cap on both births,
thread_key vs title parent selection, the increment child's inherited fields,
and the brand-new card's birth status table.
"""
import unittest

from tests import TMP_HOME  # noqa: F401 - sandbox env first
from tests import store2_testkit

from act.lib import registry
from act.lib.registry import Requirement, State


def _card(rid, title, status, **kw):
    req = Requirement(id=rid, title=title, status=status, **kw)
    registry.save(req)
    return req


class ReraiseMatrixTestCase(unittest.TestCase):
    def setUp(self):
        store2_testkit.use_backend(self, "yaml")

    def test_dead_end_states_return_none(self):
        for st in (State.REJECTED.value, State.TRASHED.value, State.ARCHIVED.value):
            parent = _card(f"P-{st[:3]}", "dead", st)
            self.assertEqual(registry.reraise_or_followup(parent, Requirement(id="", title="x"),
                                                          same_task=True), (None, None))

    def test_merged_duplicate_hops_to_live_primary_and_folds(self):
        primary = _card("P-100", "primary task", State.EXECUTING.value)
        dup = _card("P-101", "primary task", State.MERGED.value, merged_into="P-100")
        cand = Requirement(id="", title="primary task", summary="more",
                           sources=[{"channel": "slack", "date": "2026-09-02", "quote": "q"}])
        kind, saved = registry.reraise_or_followup(dup, cand, same_task=True, note="progress")
        self.assertEqual((kind, saved.id, saved.status), ("folded", "P-100", "executing"))
        self.assertEqual(saved.repeated_mentions, 2)
        self.assertIn("[radar] progress", saved.notes)
        self.assertEqual(saved.thread_id, "P-100")
        self.assertEqual(saved.origin_trust, "external")
        self.assertEqual(registry.load("P-100").repeated_mentions, 2)
        self.assertIsNotNone(primary)

    def test_pure_restatement_of_resolved_bumps_by_one_without_flip(self):
        parent = _card("P-110", "done task", State.DELIVERED.value,
                       sources=[{"channel": "quick", "date": "d", "quote": "a"}])
        cand = Requirement(id="", title="done task",
                           sources=[{"channel": "slack", "date": "d", "quote": "b"},
                                    {"channel": "slack", "date": "d", "quote": "c"}])
        kind, saved = registry.reraise_or_followup(parent, cand, same_task=True, actionable=False)
        self.assertEqual((kind, saved.status, saved.repeated_mentions), ("folded", "delivered", 2))
        self.assertEqual(len(saved.sources), 3)
        self.assertEqual(registry.load("P-110").repeated_mentions, 2)

    def test_deterministic_actionable_uses_carries_increment(self):
        parent = _card("P-120", "shipped", State.DELIVERED.value)
        plain = Requirement(id="", title="shipped")
        kind, _ = registry.reraise_or_followup(parent, plain, same_task=True)
        self.assertEqual(kind, "folded")
        parent = registry.load("P-120")
        harder = Requirement(id="", title="shipped", hardness="hard")
        kind, saved = registry.reraise_or_followup(parent, harder, same_task=True, note="again")
        self.assertEqual((kind, saved.status), ("reraised", "card_sent"))
        self.assertIn("[re-raised] again", saved.notes)
        self.assertIn("· 新增:again", saved.summary)
        self.assertEqual(saved.execution["reraised_note"], "again")

    def test_open_follow_up_absorbs_the_second_source(self):
        parent = _card("P-130", "closed thread", State.DELIVERED.value)
        child = _card("P-131", "follow", State.CARD_SENT.value, improvement_of="P-130")
        cand = Requirement(id="", title="closed thread",
                           sources=[{"channel": "gmail", "date": "d", "quote": "z"}])
        kind, saved = registry.reraise_or_followup(parent, cand, same_task=True, actionable=True,
                                                   note="second source")
        self.assertEqual((kind, saved.id), ("folded", "P-131"))
        self.assertEqual(saved.repeated_mentions, 2)
        self.assertIn("second source", saved.notes)
        self.assertIsNotNone(child)

    def test_different_task_opens_follow_up_with_lineage(self):
        parent = _card("P-140", "thread root", State.DELIVERED.value, type="code", tier="T2",
                       thread_key="gmail:abc", thread_id="P-140")
        cand = Requirement(id="", title="", type="", tier="", summary="", hardness="",
                           sources=[{"channel": "gmail", "date": "d", "quote": "q"}])
        kind, child = registry.reraise_or_followup(parent, cand, same_task=False,
                                                   actionable=True, note="new ask")
        self.assertEqual(kind, "follow_up")
        self.assertEqual((child.title, child.type, child.tier, child.hardness),
                         ("new ask", "code", "T2", "soft"))
        self.assertEqual((child.improvement_of, child.thread_id, child.thread_key),
                         ("P-140", "P-140", "gmail:abc"))
        self.assertEqual(child.summary, "既往卡 P-140 的后续：new ask")
        self.assertEqual(child.notes, "[radar] new ask")
        self.assertEqual(child.status, "card_sent")
        self.assertEqual(child.plan, [])
        self.assertTrue(child.id.startswith("P-"))

    def test_cap_detected_caps_both_births(self):
        parent = _card("P-150", "capped", State.DELIVERED.value)
        cand = Requirement(id="", title="capped")
        kind, saved = registry.reraise_or_followup(parent, cand, same_task=True, actionable=True,
                                                   cap_detected=True)
        self.assertEqual((kind, saved.status), ("reraised", "detected"))
        parent2 = _card("P-151", "other root", State.DELIVERED.value)
        kind, child = registry.reraise_or_followup(parent2, Requirement(id="", title="diff"),
                                                   same_task=False, actionable=True,
                                                   cap_detected=True)
        self.assertEqual((kind, child.status), ("follow_up", "detected"))
        self.assertEqual(registry._birth_state(True), "detected")
        self.assertEqual(registry._birth_state(False), "card_sent")

    def test_reraise_without_note_leaves_text_alone(self):
        parent = _card("P-160", "quiet", State.DELIVERED.value, notes="", summary="s",
                       execution={"session_id": "sid-1", "done": True, "accepted_at": "t"})
        kind, saved = registry.reraise_or_followup(parent, Requirement(id="", title="quiet"),
                                                   same_task=True, actionable=True, note="")
        self.assertEqual(kind, "reraised")
        self.assertEqual((saved.notes, saved.summary), ("", "s"))
        ex = saved.execution
        self.assertEqual((ex["reraised_session_id"], ex["reraised_note"], ex["accepted_at"]),
                         ("sid-1", "", "t"))
        self.assertNotIn("session_id", ex)
        self.assertNotIn("done", ex)

    def test_reraised_execution_without_session(self):
        ex = registry._reraised_execution(None, "n")
        self.assertEqual(ex["reraised_note"], "n")
        self.assertNotIn("reraised_session_id", ex)
        self.assertTrue(ex["reraised_at"])


class CarriesIncrementTestCase(unittest.TestCase):
    def test_each_rule(self):
        parent = Requirement(id="p", deadline="2026-09-10", cost_estimate_usd=None, hardness="soft")
        self.assertTrue(registry._earlier_deadline(parent, Requirement(id="n", deadline="2026-09-01")))
        self.assertFalse(registry._earlier_deadline(parent, Requirement(id="n", deadline="2026-09-10")))
        self.assertFalse(registry._earlier_deadline(parent, Requirement(id="n")))
        self.assertTrue(registry._earlier_deadline(Requirement(id="p"), Requirement(id="n", deadline="x")))
        self.assertTrue(registry._adds_cost(parent, Requirement(id="n", cost_estimate_usd=0.0)))
        self.assertFalse(registry._adds_cost(Requirement(id="p", cost_estimate_usd=1.0),
                                             Requirement(id="n", cost_estimate_usd=2.0)))
        self.assertTrue(registry._escalates(parent, Requirement(id="n", hardness="hard")))
        self.assertFalse(registry._escalates(Requirement(id="p", hardness="hard"),
                                             Requirement(id="n", hardness="hard")))
        self.assertTrue(registry._carries_increment(parent, Requirement(id="n", improvement_of="p")))
        self.assertFalse(registry._carries_increment(parent, Requirement(id="n")))
        self.assertIs(registry._carries_increment(parent, Requirement(id="n", deadline="2026-01-01")), True)


class MergeOrNewMatrixTestCase(unittest.TestCase):
    def setUp(self):
        store2_testkit.use_backend(self, "yaml")

    def test_thread_key_match_without_title_match_is_a_different_task(self):
        _card("P-200", "invoice for March", State.DELIVERED.value, thread_key="gmail:t1")
        cand = Requirement(id="", title="unrelated ask", sources=[{"gmail_thread_id": "t1",
                                                                  "channel": "gmail",
                                                                  "date": "d", "quote": "q"}])
        kind, saved = registry.merge_or_new_with_kind(cand)
        self.assertEqual(kind, "follow_up")
        self.assertEqual(saved.improvement_of, "P-200")
        self.assertEqual(saved.thread_key, "gmail:t1")

    def test_thread_key_is_derived_from_the_first_source(self):
        req = Requirement(id="", title="a", sources=[{"slack_thread_ts": "9.1"}, {"gmail_thread_id": "g"}])
        registry._ensure_thread_key(req)
        self.assertEqual(req.thread_key, "slack:9.1")
        keep = Requirement(id="", title="a", thread_key="keep", sources=[{"gmail_thread_id": "g"}])
        registry._ensure_thread_key(keep)
        self.assertEqual(keep.thread_key, "keep")
        none = Requirement(id="", title="a")
        registry._ensure_thread_key(none)
        self.assertIsNone(none.thread_key)

    def test_match_helpers(self):
        a = Requirement(id="A", title="alpha task here", status="card_sent", thread_key="k")
        b = Requirement(id="B", title="beta", status="trashed", thread_key="k")
        self.assertTrue(registry._matchable(a))
        self.assertFalse(registry._matchable(b))
        self.assertFalse(registry._matchable(Requirement(id="c", status="merged_into:A")))
        self.assertTrue(registry._matchable(Requirement(id="d", status="merged")))
        self.assertEqual(registry._match_by_thread([b, a], Requirement(id="", title="x", thread_key="k")),
                         (a, False))
        self.assertEqual(registry._match_by_thread([a], Requirement(id="", title="Alpha Task Here",
                                                                    thread_key="k")), (a, True))
        self.assertEqual(registry._match_by_thread([a], Requirement(id="", title="x")), (None, False))
        self.assertEqual(registry._match_by_title([b, a], Requirement(id="", title="alpha task here")),
                         (a, True))
        self.assertEqual(registry._match_by_title([a], Requirement(id="", title="zzz")), (None, False))
        self.assertEqual(registry._match_parent([a], Requirement(id="", title="alpha task here",
                                                                 thread_key="other")), (a, True))

    def test_increment_child_inherits_from_parent(self):
        parent = _card("P-210", "parent title", State.CARD_SENT.value, type="comms", tier="T0",
                       hardness="soft", deadline="2026-12-01", plan=["p1"], thread_key="slack:1")
        cand = Requirement(id="", title="PARENT  title", type="", tier="", hardness="",
                           deadline="2026-10-01", plan=None, display_title="disp", notes="",
                           sources=[])
        kind, child = registry.merge_or_new_with_kind(cand, high_confidence=True)
        self.assertEqual(kind, "proposed")
        self.assertEqual((child.title, child.type, child.tier, child.hardness, child.deadline,
                          child.plan), ("PARENT  title", "comms", "T0", "soft", "2026-10-01", ["p1"]))
        self.assertEqual(registry._increment_fields(parent, Requirement(id="", title="")),
                         {"title": "parent title", "hardness": "soft", "deadline": "2026-12-01",
                          "plan": ["p1"]})
        self.assertEqual((child.improvement_of, child.thread_id, child.thread_key, child.status,
                          child.display_title, child.notes),
                         ("P-210", "P-210", "slack:1", "card_sent", "disp", ""))
        # the open parent itself is not rewritten on the increment path
        self.assertIsNone(registry.load("P-210").thread_id)

    def test_open_restatement_stamps_and_bumps_by_new_rows(self):
        _card("P-220", "same ask", State.CARD_SENT.value,
              sources=[{"channel": "quick", "date": "d", "quote": "a"}])
        cand = Requirement(id="", title="same ask",
                           sources=[{"channel": "slack", "date": "d", "quote": "b"},
                                    {"channel": "slack", "date": "d", "quote": "c"}])
        kind, saved = registry.merge_or_new_with_kind(cand)
        self.assertEqual((kind, saved.id, saved.repeated_mentions), ("folded", "P-220", 3))
        self.assertEqual(saved.origin_trust, "external")

    def test_brand_new_birth_status_table(self):
        self.assertEqual(registry._brand_new_status(Requirement(id="", hardness="hard", deadline="d"), True),
                         "card_sent")
        self.assertEqual(registry._brand_new_status(Requirement(id="", hardness="hard", deadline="d"), False),
                         "detected")
        self.assertEqual(registry._brand_new_status(Requirement(id="", hardness="soft", deadline="d"), True),
                         "detected")
        self.assertEqual(registry._brand_new_status(Requirement(id="", hardness="hard"), True), "detected")
        self.assertTrue(registry._needs_birth_status(Requirement(id="", status="")))
        self.assertTrue(registry._needs_birth_status(Requirement(id="", status="detected")))
        self.assertFalse(registry._needs_birth_status(Requirement(id="", status="card_sent")))

    def test_file_new_keeps_preset_status_and_normalises_mentions(self):
        kind, saved = registry.merge_or_new_with_kind(
            Requirement(id="", title="fresh", status="card_sent", repeated_mentions=0))
        self.assertEqual((kind, saved.status, saved.repeated_mentions, saved.thread_id),
                         ("proposed", "card_sent", 1, saved.id))
        self.assertEqual(saved.origin_trust, "proposed")
        kept = registry.merge_or_new_with_kind(Requirement(id="P-777", title="explicit id"))[1]
        self.assertEqual((kept.id, kept.thread_id), ("P-777", "P-777"))

    def test_dead_end_parent_files_a_fresh_card(self):
        _card("P-230", "archived one", State.ARCHIVED.value, prev_status="delivered")
        merged = _card("P-231", "archived one", State.MERGED.value, merged_into="P-230")
        kind, saved = registry.merge_or_new_with_kind(Requirement(id="", title="archived one"))
        self.assertEqual(kind, "proposed")
        self.assertNotIn(saved.id, ("P-230", "P-231"))
        self.assertIsNotNone(merged)

    def test_merge_or_new_delegates(self):
        saved = registry.merge_or_new({"title": "via dict"}, high_confidence=True)
        self.assertEqual(saved.title, "via dict")
        self.assertEqual(saved.status, "detected")


class FoldHelpersTestCase(unittest.TestCase):
    def test_pick_sources_and_bump(self):
        cand = Requirement(id="c", sources=[{"a": 1}])
        self.assertEqual(registry._pick_sources(cand, None), [{"a": 1}])
        self.assertEqual(registry._pick_sources(cand, []), [])
        self.assertIsNone(registry._pick_sources(None, None))
        req = Requirement(id="r", repeated_mentions=None)
        registry._bump_mentions(req, 0)
        self.assertIsNone(req.repeated_mentions)
        registry._bump_mentions(req, 2)
        self.assertEqual(req.repeated_mentions, 3)

    def test_absorb_sources_dedupes_and_stamps(self):
        req = Requirement(id="r", sources=[{"channel": "quick", "date": "d", "quote": "a"}])
        registry._absorb_sources(req, [{"channel": "Quick", "date": "d", "quote": "A"},
                                       {"channel": "slack", "date": "d", "quote": "b"}])
        self.assertEqual((len(req.sources), req.repeated_mentions, req.origin_trust), (2, 2, "external"))
        registry._absorb_sources(req, None)
        self.assertEqual(req.repeated_mentions, 2)

    def test_fold_note_helpers(self):
        existing = registry.parse_fold_notes("[radar] a [@2026-01-01T00:00:00Z]\n[quick] b")
        self.assertTrue(registry._has_fold_note(existing, "radar", "a"))
        self.assertFalse(registry._has_fold_note(existing, "quick", "a"))
        self.assertTrue(registry._has_fold_note(existing, "quick", "b"))
        with unittest.mock.patch.object(registry, "_iso_now", return_value="2026-01-01T00:00:00Z"):
            self.assertEqual(registry._unique_fold_ts(existing), "2026-01-01T00:00:00Z#2")
            existing.append({"kind": "radar", "text": "c", "ts": "2026-01-01T00:00:00Z#2",
                             "split_into": None})
            self.assertEqual(registry._unique_fold_ts(existing), "2026-01-01T00:00:00Z#3")
        self.assertTrue(registry._splittable_fold_line("[radar] a [@t1]", "t1"))
        self.assertFalse(registry._splittable_fold_line("[radar] a [@t1] [已拆出 R-1]", "t1"))
        self.assertFalse(registry._splittable_fold_line("plain [@t1]", "t1"))
        self.assertFalse(registry._splittable_fold_line("[radar] a [@t2]", "t1"))
        self.assertEqual(registry._note_lines(Requirement(id="x", notes=None)), [""])

    def test_open_child_and_containment(self):
        self.assertTrue(registry._is_open_child(Requirement(id="c", improvement_of="p", status="card_sent")))
        self.assertFalse(registry._is_open_child(Requirement(id="c", status="card_sent")))
        self.assertFalse(registry._is_open_child(Requirement(id="c", improvement_of="p", status="delivered")))
        self.assertFalse(registry._is_open_child(Requirement(id="c", improvement_of="p", status="rejected")))
        self.assertFalse(registry._is_open_child(Requirement(id="c", improvement_of="p", status="trashed")))
        self.assertTrue(registry._contains_either("abcdefghijkl", "xx abcdefghijkl yy"))
        self.assertFalse(registry._contains_either("short", "short and more"))
        self.assertFalse(registry._contains_either("abcdefghijkl", "zzzzzzzzzzzzzz"))


import unittest.mock  # noqa: E402  (used above via unittest.mock)

if __name__ == "__main__":
    unittest.main()
