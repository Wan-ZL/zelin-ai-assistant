"""store2.store — the pure helpers split out of create_card / put_card /
transition / update_card_fields in P3b, plus get_activities (CONTRACT §53).

Pins: key checks (unknown / missing / agent prev_status), payload shape,
row-argument assembly incl. the origin_trust fail-closed default and the
created/updated fallbacks, the verb spec lookup + parent-arg checks, target
status for both verb families, transition audit rows, per-field assignment
collection (no-op payload, unchanged hot column), put_card's source projection
rewrite + no-op short-circuit, and get_activities' tolerant JSON read.
"""
import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from tests import TMP_HOME  # noqa: F401 - sandbox env first

from act.lib.store2 import store as st
from act.lib.store2 import IntegrityViolation, NotFound, Store, StoreError, TransitionDenied

NOW = "2026-09-02T12:00:00Z"


class PureHelpersTestCase(unittest.TestCase):
    def test_check_create_keys(self):
        st._check_create_keys({"id": "a", "status": "detected", "title": "t"}, "system")
        with self.assertRaises(StoreError) as cm:
            st._check_create_keys({"id": "a", "status": "x", "title": "t", "bogus": 1}, "system")
        self.assertEqual((cm.exception.code, cm.exception.details["fields"]), ("UNKNOWN_FIELD", ["bogus"]))
        with self.assertRaises(StoreError) as cm:
            st._check_create_keys({"id": "a", "status": "", "title": "t"}, "system")
        self.assertEqual((cm.exception.code, cm.exception.details["fields"]), ("INVALID_FIELD", ["status"]))
        # agent may not pass prev_status at birth
        st._check_create_keys({"id": "a", "status": "detected", "title": "t", "prev_status": "x"}, "system")
        with self.assertRaises(StoreError) as cm:
            st._check_create_keys({"id": "a", "status": "detected", "title": "t", "prev_status": "x"}, "agent")
        self.assertEqual(cm.exception.details["fields"], ["prev_status"])

    def test_payload_dict(self):
        self.assertEqual(st._payload_dict({}), {})
        self.assertEqual(st._payload_dict({"payload": None}), {})
        self.assertEqual(st._payload_dict({"payload": {"k": 1}}), {"k": 1})
        with self.assertRaises(StoreError):
            st._payload_dict({"payload": [1]})
        with self.assertRaises(StoreError):
            st._require_payload_dict("x")
        st._require_payload_dict({})

    def test_create_row_args_defaults(self):
        args = st._create_row_args({"id": "a", "status": "detected", "title": "t"}, 7, "user", {}, NOW)
        self.assertEqual(args[:6], ("a", "detected", None, "T1", "", "t"))
        self.assertEqual(args[6], "external")
        self.assertEqual(args[9:11], (NOW, NOW))
        self.assertEqual(args[12:14], (7, "user"))
        self.assertEqual(args[14], "{}")
        self.assertIsNone(args[15])
        explicit = st._create_row_args({"id": "a", "status": "s", "title": "t", "created": "c",
                                        "updated": "u", "origin_trust": "hand", "work_id": "R-1"},
                                       1, "system", {"x": 1}, NOW)
        self.assertEqual((explicit[6], explicit[9], explicit[10], explicit[15]), ("hand", "c", "u", "R-1"))

    def test_put_row_args_and_update_args(self):
        ins = st._put_row_args("a", {"origin_trust": None}, 3, "system", {}, NOW)
        self.assertEqual((ins[0], ins[3], ins[4], ins[5], ins[6], ins[9], ins[10], ins[12]),
                         ("a", "T1", "", "", "external", NOW, NOW, 3))
        upd = st._put_update_args("a", {"origin_trust": "", "status": "review"}, 4, "user", {"k": 2}, NOW)
        self.assertEqual((upd[0], upd[5], upd[10], upd[11], upd[12], upd[13], upd[14]),
                         ("review", "external", '{"k": 2}', "user", NOW, 4, "a"))

    def test_payload_changes_diff(self):
        self.assertEqual(st._payload_changes({"a": 1, "b": 2}, {"a": 1, "b": 3, "c": 4}),
                         [{"field": "payload.b", "before": 2, "after": 3},
                          {"field": "payload.c", "before": None, "after": 4}])
        self.assertEqual(st._payload_changes({}, {}), [])

    def test_verb_spec_and_parent_arg(self):
        self.assertIs(st._verb_spec("approve", "a", None), st.VERBS["approve"])
        with self.assertRaises(StoreError) as cm:
            st._verb_spec("teleport", "a", None)
        self.assertEqual(cm.exception.code, "UNKNOWN_VERB")
        with self.assertRaises(StoreError) as cm:
            st._verb_spec("merge", "a", None)
        self.assertIn("requires merged_into_id", cm.exception.message)
        with self.assertRaises(StoreError) as cm:
            st._verb_spec("merge", "a", "a")
        self.assertIn("into itself", cm.exception.message)
        self.assertIs(st._verb_spec("merge", "a", "b"), st.VERBS["merge"])

    def test_target_status_families(self):
        self.assertEqual(st._target_status(st.VERBS["restore"], "trashed", "approved"), ("approved", None))
        self.assertEqual(st._target_status(st.VERBS["restore"], "trashed", None), ("detected", None))
        self.assertEqual(st._target_status(st.VERBS["restore"], "trashed", "archived"),
                         ("archived", "delivered"))
        self.assertEqual(st._target_status(st.VERBS["unarchive"], "archived", None), ("delivered", None))
        self.assertEqual(st._target_status(st.VERBS["trash"], "card_sent", None), ("trashed", "card_sent"))
        self.assertEqual(st._target_status(st.VERBS["trash"], "trashed", "review"), ("trashed", "review"))
        self.assertEqual(st._target_status(st.VERBS["approve"], "card_sent", "x"), ("approved", "x"))

    def test_transition_changes(self):
        self.assertEqual(st._transition_changes(("a", None, None), ("b", None, None)),
                         [{"field": "status", "before": "a", "after": "b"}])
        rows = st._transition_changes(("a", None, None), ("b", "a", "P-1"))
        self.assertEqual([r["field"] for r in rows], ["status", "prev_status", "merged_into_id"])


class AssignmentsTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="store2-helpers-"))
        self.store = Store(self.tmp / "s.db", now_fn=lambda: NOW)
        self.store.create_card({"id": "R-1", "status": "card_sent", "title": "t",
                                "payload": {"k": 1}}, actor_type="system")
        self.row = self.store._get_row(self.store._conn(), "R-1")

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_check_update_fields(self):
        st._check_update_fields({"tier": "T2"}, "system")
        with self.assertRaises(StoreError) as cm:
            st._check_update_fields({"status": "x"}, "user")
        self.assertEqual(cm.exception.code, "UNKNOWN_FIELD")
        with self.assertRaises(TransitionDenied):
            st._check_update_fields({"origin_trust": "hand"}, "agent")
        st._check_update_fields({"origin_trust": "hand"}, "user")

    def test_payload_and_field_assignment(self):
        self.assertIsNone(st._payload_assignment(self.row, {"k": 1}))
        changes, sql, arg = st._payload_assignment(self.row, {"k": 2})
        self.assertEqual((sql, arg), ("payload = ?", '{"k": 2}'))
        self.assertEqual(changes, [{"field": "payload.k", "before": 1, "after": 2}])
        with self.assertRaises(StoreError):
            st._payload_assignment(self.row, "nope")
        self.assertIsNone(st._field_assignment(self.row, "tier", "T1"))
        self.assertEqual(st._field_assignment(self.row, "tier", "T2"),
                         ([{"field": "tier", "before": "T1", "after": "T2"}], "tier = ?", "T2"))

    def test_collect_assignments_is_sorted_and_skips_unchanged(self):
        changes, assigns, args = st._collect_assignments(
            self.row, {"type": "", "tier": "T2", "payload": {"k": 1}, "deadline": "d"})
        self.assertEqual(assigns, ["deadline = ?", "tier = ?"])
        self.assertEqual(args, ["d", "T2"])
        self.assertEqual([c["field"] for c in changes], ["deadline", "tier"])
        self.assertEqual(st._collect_assignments(self.row, {}), ([], [], []))


class WritePathsTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="store2-writes-"))
        self.store = Store(self.tmp / "s.db", now_fn=lambda: NOW)

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _sources(self, card_id):
        return [tuple(r) for r in self.store._conn().execute(
            "SELECT channel, who, quote FROM sources WHERE card_id = ? ORDER BY id", (card_id,))]

    HOT = {"status": "detected", "prev_status": None, "tier": "T1", "type": "", "title": "t",
           "origin_trust": "hand", "target_repo": None, "deadline": None,
           "merged_into_id": None, "work_id": None}

    def test_put_card_source_projection_and_noop(self):
        first = self.store.put_card("P-1", {"id": "P-1"}, self.HOT,
                                    [{"channel": "quick", "who": "z", "quote": "a"}, None])
        self.assertEqual(first["version"], 1)
        # a None source row projects as an empty channel (s[0] or "")
        self.assertEqual(self._sources("P-1"), [("quick", "z", "a"), ("", None, None)])
        again = self.store.put_card("P-1", {"id": "P-1"}, self.HOT,
                                    [{"channel": "quick", "who": "z", "quote": "a"},
                                     {"channel": ""}])
        self.assertEqual(again["version"], 1)                         # no-op
        self.assertEqual(len(self.store.get_activities("P-1")), 1)
        third = self.store.put_card("P-1", {"id": "P-1"}, self.HOT,
                                    [{"channel": "slack", "who": "b", "quote": "c"}])
        self.assertEqual(third["version"], 2)
        self.assertEqual(self._sources("P-1"), [("slack", "b", "c")])
        acts = self.store.get_activities("P-1")
        self.assertEqual(len(acts), 1)      # sources-only rewrite: no field changes → no activity row
        self.assertEqual(self.store._src_rows(None), [])
        self.assertEqual(self.store._src_rows([None]), [(None, None, None, None, None)])

    def test_put_card_tombstone_is_not_found_and_payload_shape(self):
        trashed = dict(self.HOT, status="trashed", prev_status="detected")
        self.store.put_card("P-2", {"id": "P-2"}, trashed, [])
        self.store.purge_trashed("P-2")
        with self.assertRaises(NotFound):
            self.store.put_card("P-2", {"id": "P-2"}, trashed, [])
        with self.assertRaises(StoreError):
            self.store.put_card("P-3", "junk", self.HOT, [])

    def test_put_card_hot_and_payload_changes_are_audited(self):
        self.store.put_card("P-4", {"id": "P-4", "a": 1}, self.HOT, [])
        self.store.put_card("P-4", {"id": "P-4", "a": 2}, dict(self.HOT, tier="T2"), [])
        fields = [c["field"] for c in self.store.get_activities("P-4")[-1]["changes"]]
        self.assertEqual(fields, ["tier", "payload.a"])
        row = self.store._get_row(self.store._conn(), "P-4")
        # a hot dict missing keys reads as None ≠ stored "" / "external" → counted as a change
        self.assertEqual([c["field"] for c in self.store._hot_changes(row, {"status": "detected"})],
                         ["tier", "type", "title", "origin_trust"])

    def test_transition_paths(self):
        self.store.create_card({"id": "R-1", "status": "card_sent", "title": "t"}, actor_type="system")
        self.store.create_card({"id": "R-2", "status": "card_sent", "title": "t"}, actor_type="system")
        merged = self.store.transition("R-2", "merge", "user", None, merged_into_id="R-1")
        self.assertEqual((merged["status"], merged["merged_into_id"]), ("merged", "R-1"))
        with self.assertRaises(NotFound):
            self.store.transition("R-1", "merge", "user", None, merged_into_id="R-404")
        same = self.store.transition("R-1", "promote", "system", None)   # card_sent → card_sent no-op
        self.assertEqual(same["version"], 1)
        self.store._require_parent_row(self.store._conn(), st.VERBS["approve"], None)  # no parent needed

    def test_update_card_fields_noop_and_change(self):
        self.store.create_card({"id": "R-1", "status": "card_sent", "title": "t",
                                "payload": {"k": 1}}, actor_type="system")
        same = self.store.update_card_fields("R-1", None, {"payload": {"k": 1}, "tier": "T1"}, "system")
        self.assertEqual(same["version"], 1)
        changed = self.store.update_card_fields("R-1", None, {"deadline": "2026-10-01"}, "system")
        self.assertEqual((changed["version"], changed["deadline"]), (2, "2026-10-01"))

    def test_get_activities_tolerates_bad_json(self):
        self.store.create_card({"id": "R-9", "status": "detected", "title": "t"}, actor_type="system")
        acts = self.store.get_activities("R-9")
        self.assertEqual(acts[0]["changes"], [{"field": "status", "before": None, "after": "detected"}])
        conn = self.store._conn()
        try:
            conn.execute("UPDATE activities SET changes = 'not json' WHERE card_id = 'R-9'")
            tolerated = True
        except sqlite3.IntegrityError:
            tolerated = False      # json_valid CHECK refuses — the read-side guard is belt-and-braces
        if tolerated:
            self.assertEqual(self.store.get_activities("R-9")[0]["changes"], [])
        self.assertEqual(self.store.get_activities("R-none"), [])
        self.assertIsInstance(json.dumps(acts[0]["changes"]), str)
        self.assertTrue(isinstance(IntegrityViolation("x", "y"), StoreError))


if __name__ == "__main__":
    unittest.main()
