"""registry / card_model — judgments for the mutants that survived the P3b
mutation round (CONTRACT §57: a survivor is a test gap, not a shrug).

Each test names the operator flip it kills: identity returns (``return req`` /
``return False`` must be the object / False, not None), the journal compaction
boundary, the archive-dir scans in next_id / next_work_id / load_by_work_id,
the ``or`` fallbacks that a ``and`` flip turns into TypeErrors, defaults on the
merge_or_new signature, the 80-char title clip, the merge-lineage cycle guard,
and the dataclass bookkeeping fields staying out of equality / repr.
"""
import json
import os
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first
from tests import store2_testkit

from act.lib import analytics, config, registry
from act.lib.registry import Requirement, State


class IdentityReturnsTestCase(unittest.TestCase):
    def setUp(self):
        store2_testkit.use_backend(self, "yaml")

    def test_trash_restore_pin_archive_return_the_card(self):
        req = Requirement(id="P-1", title="t", status=State.DELIVERED.value)
        registry.save(req)
        self.assertIs(registry.trash(req, "deleted"), req)
        self.assertIs(registry.restore(req), req)
        self.assertIs(registry.pin(req), req)
        self.assertIs(registry.archive(req, "user"), req)
        self.assertIs(registry.unarchive(req), req)
        self.assertEqual(req.status, "delivered")

    def test_unarchive_without_prev_status_falls_back_to_delivered(self):
        req = Requirement(id="P-2", title="t", status=State.ARCHIVED.value, prev_status=None)
        registry.save(req)
        registry.unarchive(req)
        self.assertEqual(req.status, "delivered")

    def test_archive_of_a_never_saved_card_does_not_trip_on_missing_file(self):
        req = Requirement(id="P-3", title="fresh", status=State.DELIVERED.value)
        self.assertIs(registry.archive(req, "auto"), req)
        self.assertTrue((registry.ARCHIVE_DIR / "P-3.yaml").exists())
        again = registry.load("P-3")
        self.assertIs(registry.unarchive(again), again)
        self.assertTrue((config.REGISTRY_DIR / "P-3.yaml").exists())

    def test_delete_paths_return_real_false(self):
        self.assertIs(registry.delete(Requirement(id="P-404")), False)
        stub = Requirement(id="P-5")
        stub._file, stub._in_list = str(config.REGISTRY_DIR / "nope.yaml"), True
        self.assertIs(registry.delete(stub), False)
        single = Requirement(id="P-6")
        single._file, single._in_list = str(config.REGISTRY_DIR / "nope.yaml"), False
        self.assertIs(registry.delete(single), False)
        self.assertIs(registry.mark_note_split(Requirement(id="x"), "", "P-9"), False)
        self.assertIs(registry.mark_note_split(Requirement(id="x", notes="[radar] a [@t1]"), "t9", "P-9"),
                      False)

    def test_sqlite_delete_of_tombstone_is_real_false(self):
        store2_testkit.use_backend(self, "sqlite")
        req = Requirement(id="P-7", title="t", status=State.TRASHED.value)
        registry.save(req)
        self.assertIs(registry.delete(req), True)
        self.assertIs(registry.delete(req), False)


class FileShapesTestCase(unittest.TestCase):
    def setUp(self):
        store2_testkit.use_backend(self, "yaml")

    def test_missing_registry_dir_loads_nothing(self):
        with mock.patch.object(config, "REGISTRY_DIR", config.REGISTRY_DIR / "does-not-exist"):
            self.assertEqual(registry.load_all(), [])
            self.assertEqual(registry.registry_yaml_files(), [])
            self.assertIsNone(registry.load("P-1"))

    def test_replace_list_member_appends_when_absent(self):
        out = registry._replace_list_member([{"id": "a"}], Requirement(id="b", title="t"))
        self.assertEqual([x["id"] for x in out], ["a", "b"])
        out = registry._replace_list_member([{"id": 4}], Requirement(id="4", title="t"))
        self.assertEqual(out[0]["title"], "t")
        self.assertEqual(len(out), 1)

    def test_single_save_marks_not_in_list(self):
        req = Requirement(id="P-8", title="t")
        registry.save(req)
        self.assertIs(req._in_list, False)
        self.assertEqual(req._file, str(config.REGISTRY_DIR / "P-8.yaml"))

    def test_first_card_milestone_only_for_card_sent(self):
        with mock.patch.object(analytics, "log_first") as lf:
            registry.save(Requirement(id="P-9", title="t", status=State.DETECTED.value))
            lf.assert_not_called()
            registry.save(Requirement(id="P-10", title="t", status=State.CARD_SENT.value))
        lf.assert_called_once_with("milestone_first_card", req="P-10")

    def test_journal_compaction_boundary_is_strict(self):
        path = registry._writes_journal_path()
        if path.exists():
            path.unlink()
        registry._journal_write("a.yaml")
        one = path.stat().st_size
        with mock.patch.object(registry, "_WRITES_JOURNAL_MAX_BYTES", one * 2):
            registry._journal_write("b.yaml")          # size == cap → no compaction
        self.assertEqual(len(path.read_text(encoding="utf-8").splitlines()), 2)
        with mock.patch.object(registry, "_WRITES_JOURNAL_MAX_BYTES", one * 2):
            registry._journal_write("c.yaml")          # size > cap → keep the newer half (2 of 3)
        kept = [json.loads(ln)["f"] for ln in path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(kept, ["b.yaml", "c.yaml"])

    def test_agent_wall_uses_the_stored_status(self):
        from act.lib.store2.store import TransitionDenied
        req = Requirement(id="P-11", title="t", status=State.CARD_SENT.value)
        registry.save(req)
        req.set_status(State.DETECTED)
        with registry.acting_as("agent"):
            with self.assertRaises(TransitionDenied) as cm:
                registry.save(req)
        self.assertIn("may not move card P-11 'card_sent' -> 'detected'", str(cm.exception))


class UnrepresentableTestCase(unittest.TestCase):
    def test_sqlite_save_names_the_errors(self):
        store2_testkit.use_backend(self, "sqlite")
        from act.lib.store2.store import StoreError
        with self.assertRaises(StoreError) as cm:
            registry.save(Requirement(id="P-12", title="t", status="flying"))
        self.assertEqual(cm.exception.code, "UNREPRESENTABLE")
        self.assertIn("card P-12: status 'flying' 不在 schema 词表内", cm.exception.message)


class IdHelpersTestCase(unittest.TestCase):
    def setUp(self):
        store2_testkit.use_backend(self, "yaml")

    def test_id_number_both_namespaces(self):
        self.assertEqual(registry.id_number("R-042"), 42)
        self.assertEqual(registry.id_number("P-007"), 7)
        self.assertIsNone(registry.id_number("MS-1"))
        self.assertIsNone(registry.id_number(None))

    def test_next_id_and_work_id_scan_the_archive(self):
        sealed = Requirement(id="P-050", title="sealed", status=State.DELIVERED.value, work_id="R-300")
        registry.save(sealed)
        registry.archive(sealed, "user")
        registry.save(Requirement(id="P-003", title="live"))
        self.assertEqual(registry.next_id(), "P-051")
        self.assertEqual(registry.next_work_id(), "R-301")
        self.assertEqual(registry.load_by_work_id("R-300").id, "P-050")
        self.assertIsNone(registry.load_by_work_id("R-999"))

    def test_distinct_strs_drops_blanks_and_dupes(self):
        self.assertEqual(registry._distinct_strs(["", None, " a ", "a", "b"]), ["a", "b"])

    def test_canonical_id_cycle_and_ghost(self):
        a = Requirement(id="A", status=State.MERGED.value, merged_into="B")
        b = Requirement(id="B", status=State.MERGED.value, merged_into="A")
        self.assertEqual(registry._canonical_id("A", {"A": a, "B": b}), "A")
        self.assertEqual(registry._canonical_id("ghost", {}), "ghost")
        self.assertEqual(registry._canonical_id("", {}), "")
        dangling = Requirement(id="C", status="merged_into:")
        self.assertEqual(registry._canonical_id("C", {"C": dangling}), "C")


class MatchingTestCase(unittest.TestCase):
    def setUp(self):
        store2_testkit.use_backend(self, "yaml")

    def test_empty_titles_never_match(self):
        self.assertIs(registry._same_source_and_title(Requirement(id="a", title=""),
                                                      Requirement(id="b", title="x")), False)
        self.assertIs(registry._same_source_and_title(Requirement(id="a", title="x"),
                                                      Requirement(id="b", title="  ")), False)

    def test_dedupe_sources_tolerates_missing_keys_and_junk(self):
        merged, added = registry.dedupe_sources([{"quote": "a"}],
                                                ["junk", {"quote": "a"}, {"channel": None, "date": None,
                                                                          "ref": "r"}])
        self.assertEqual(added, 1)
        self.assertEqual(len(merged), 2)

    def test_delivered_follow_up_is_not_open(self):
        registry.save(Requirement(id="P-20", title="root", status=State.DELIVERED.value))
        registry.save(Requirement(id="P-21", title="child", status=State.DELIVERED.value,
                                  improvement_of="P-20"))
        self.assertIsNone(registry.find_open_follow_up("P-20"))
        registry.save(Requirement(id="P-22", title="child2", status=State.CARD_SENT.value,
                                  improvement_of="P-20"))
        self.assertEqual(registry.find_open_follow_up("P-20").id, "P-22")


class MergeDefaultsTestCase(unittest.TestCase):
    def setUp(self):
        store2_testkit.use_backend(self, "yaml")

    def test_signature_defaults(self):
        kind, saved = registry.merge_or_new_with_kind(Requirement(id="", title="hard one",
                                                                  hardness="hard", deadline="2026-12-01"))
        self.assertEqual((kind, saved.status), ("proposed", "detected"))     # high_confidence=False
        registry.save(Requirement(id="P-30", title="done thing", status=State.DELIVERED.value,
                                  repeated_mentions=None, notes="old note"))
        kind, saved = registry.merge_or_new_with_kind(Requirement(id="", title="done thing",
                                                                  summary="", hardness="hard"))
        self.assertEqual((kind, saved.status), ("reraised", "card_sent"))    # cap_detected=False
        self.assertEqual(saved.repeated_mentions, 2)                          # None or 1 → +1
        self.assertIn("[re-raised] done thing", saved.notes)                 # note = summary or title
        self.assertIn("old note", saved.notes)

    def test_follow_up_title_clip_and_child_defaults(self):
        registry.save(Requirement(id="P-31", title="root", status=State.DELIVERED.value,
                                  thread_key="gmail:t"))
        cand = Requirement(id="", title="x" * 100, sources=None,
                           thread_key="gmail:t")
        kind, child = registry.merge_or_new_with_kind(cand)
        self.assertEqual(kind, "follow_up")
        self.assertEqual(len(child.title), 80)
        self.assertEqual(child.repeated_mentions, 1)
        self.assertEqual(child.sources, [])

    def test_increment_child_keeps_notes_and_open_fold_roots_thread(self):
        registry.save(Requirement(id="P-032", title="open parent", status=State.CARD_SENT.value,
                                  thread_id=None))
        kind, child = registry.merge_or_new_with_kind(
            Requirement(id="", title="open parent", hardness="hard", notes="keep me", sources=None))
        self.assertEqual((kind, child.notes, child.repeated_mentions, child.sources),
                         ("proposed", "keep me", 1, []))
        kind, parent = registry.merge_or_new_with_kind(Requirement(id="", title="open parent"))
        self.assertEqual((kind, parent.id, parent.thread_id), ("folded", "P-032", "P-032"))
        self.assertEqual(registry.load("P-032").thread_id, "P-032")


class CardModelBookkeepingTestCase(unittest.TestCase):
    def test_file_fields_are_outside_equality_and_repr(self):
        a = Requirement(id="x", title="t")
        b = Requirement(id="x", title="t")
        b._file, b._in_list = "/tmp/x.yaml", True
        self.assertEqual(a, b)
        self.assertNotIn("_file", repr(b))
        self.assertNotIn("_in_list", repr(b))
        self.assertEqual(json.dumps(a.to_dict()), json.dumps(b.to_dict()))
        self.assertTrue(os.path.basename(b._file))


if __name__ == "__main__":
    unittest.main()
