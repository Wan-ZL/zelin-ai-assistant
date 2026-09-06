"""registry — the write/read paths behind save / load / delete / archive on
both backends, with every edge the P3b split exposed (CONTRACT §1 / §4 / §9 /
§34bis / §53 / §60).

Characterization net: backend selection (env / config memo / marker / missing
db), guard_snapshot on both backends incl. OSError degradation, the writes
journal (boundary ``>=``, junk lines, in-process fallback), list-file loading
with junk members and unreadable files, load()'s archive-residue precedence,
delete() on every file shape (single, list member, last member, unreadable,
unlinkable, sqlite tombstone), the agent wall's birth rules, and the
display-title rename rules.
"""
import json
import os
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first
from tests import store2_testkit

from act.lib import config, registry, sanitize
from act.lib.registry import Requirement, State


class BackendSelectionTestCase(unittest.TestCase):
    def setUp(self):
        store2_testkit.use_backend(self, "yaml")

    def test_env_forces_and_junk_env_falls_through_to_config_memo(self):
        with mock.patch.dict(os.environ, {"ZAI_REGISTRY_BACKEND": " SQLite "}):
            self.assertEqual(registry.backend_forced(), "sqlite")
        registry.reset_store_cache()
        with mock.patch.dict(os.environ, {"ZAI_REGISTRY_BACKEND": "bogus"}), \
                mock.patch.object(config, "registry_backend_setting", return_value="yaml") as rbs:
            self.assertEqual(registry.backend_forced(), "yaml")
            self.assertEqual(registry.backend_forced(), "yaml")   # memoised
        rbs.assert_called_once()
        registry.reset_store_cache()
        with mock.patch.dict(os.environ, {"ZAI_REGISTRY_BACKEND": ""}), \
                mock.patch.object(config, "registry_backend_setting", return_value="auto"):
            self.assertIsNone(registry.backend_forced())
            self.assertEqual(registry.backend(), registry.BACKEND_YAML)
            registry.store2_truth_path().parent.mkdir(parents=True, exist_ok=True)
            registry.store2_truth_path().write_text("{}", encoding="utf-8")
            try:
                self.assertEqual(registry.backend(), registry.BACKEND_SQLITE)
            finally:
                registry.store2_truth_path().unlink()
        registry.reset_store_cache()

    def test_store_refuses_a_missing_db_and_resets_on_path_change(self):
        with mock.patch.dict(os.environ, {"ZAI_REGISTRY_BACKEND": "sqlite"}):
            registry.reset_store_cache()
            if registry.store2_db_path().exists():
                registry.store2_db_path().unlink()
            with self.assertRaises(RuntimeError):
                registry._store()
            from act.lib.store2.store import Store
            Store(registry.store2_db_path()).close()
            st = registry._store()
            self.assertIs(registry._store(), st)
            # a different db path invalidates the cached singleton
            with mock.patch.object(registry, "store2_db_path",
                                   return_value=registry.store2_db_path().with_name("other.db")):
                with self.assertRaises(RuntimeError):
                    registry._store()
            self.assertIsNone(registry._STORE)
        registry.reset_store_cache()

    def test_path_helpers(self):
        self.assertEqual(registry.store2_db_path(), config.STATE_DIR / "store2.db")
        self.assertEqual(registry.store2_truth_path(), config.STATE_DIR / "store2_truth.json")
        self.assertEqual(registry.store2_activation_path(), config.STATE_DIR / "store2_activation.json")
        self.assertEqual(registry.registry_backups_dir(), config.STATE_DIR / "backups")
        self.assertEqual(registry.registry_export_dir(), config.STATE_DIR / "registry-export")


class GuardSnapshotTestCase(unittest.TestCase):
    def test_yaml_snapshot_shape_and_oserror_degradation(self):
        store2_testkit.use_backend(self, "yaml")
        registry.save(Requirement(id="P-001", title="a"))
        snap = registry.guard_snapshot()
        self.assertEqual(list(snap), ["P-001.yaml"])
        size, mtime = snap["P-001.yaml"].split(":")
        self.assertTrue(int(size) > 0 and int(mtime) > 0)
        with mock.patch.object(Path, "stat", side_effect=OSError("gone")):
            self.assertEqual(registry.guard_snapshot(), {})
        with mock.patch.object(Path, "glob", side_effect=OSError("denied")):
            self.assertEqual(registry.guard_snapshot(), {})

    def test_sqlite_snapshot_uses_version_and_includes_tombstones(self):
        store2_testkit.use_backend(self, "sqlite")
        registry.save(Requirement(id="P-001", title="a"))
        self.assertEqual(registry.guard_snapshot(), {"P-001.yaml": "v1"})
        req = registry.load("P-001")
        req.title = "b"
        registry.save(req)
        self.assertEqual(registry.guard_snapshot(), {"P-001.yaml": "v2"})
        registry.trash(req, "deleted")
        self.assertTrue(registry.delete(req))
        self.assertIn("P-001.yaml", registry.guard_snapshot())   # tombstone row still counts


class WritesJournalTestCase(unittest.TestCase):
    def setUp(self):
        store2_testkit.use_backend(self, "yaml")
        registry._PROC_WRITES.clear()

    def test_boundary_is_inclusive_and_junk_lines_skip(self):
        path = registry._writes_journal_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join([
            json.dumps({"f": "old.yaml", "ts": "2026-01-01T00:00:00Z"}),
            json.dumps({"f": "at.yaml", "ts": "2026-01-02T00:00:00Z"}),
            json.dumps({"f": "new.yaml", "ts": "2026-01-03T00:00:00Z"}),
            json.dumps({"ts": "2026-01-03T00:00:00Z"}),          # no f
            json.dumps(["not", "a", "dict"]),
            "{broken",
        ]) + "\n", encoding="utf-8")
        self.assertEqual(registry.writes_since("2026-01-02T00:00:00Z"),
                         frozenset({"at.yaml", "new.yaml"}))
        self.assertEqual(registry._journal_entry_name("{broken", "x"), None)
        self.assertEqual(registry._journal_entry_name(json.dumps({"f": "", "ts": "9"}), "1"), None)

    def test_missing_journal_falls_back_to_process_map(self):
        path = registry._writes_journal_path()
        if path.exists():
            path.unlink()
        registry._PROC_WRITES.update({"a.yaml": "2026-01-02T00:00:00Z",
                                      "b.yaml": "2026-01-01T00:00:00Z"})
        self.assertEqual(registry.writes_since("2026-01-02T00:00:00Z"), frozenset({"a.yaml"}))
        self.assertEqual(registry._journal_names_since("2026"), set())

    def test_journal_write_records_and_compacts(self):
        path = registry._writes_journal_path()
        if path.exists():
            path.unlink()
        registry._journal_write("x.yaml")
        self.assertIn("x.yaml", registry._PROC_WRITES)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8").splitlines()[0])["f"], "x.yaml")
        with mock.patch.object(registry, "_WRITES_JOURNAL_MAX_BYTES", 10):
            registry._journal_write("y.yaml")
            registry._journal_write("z.yaml")
        lines = path.read_text(encoding="utf-8").splitlines()
        self.assertLessEqual(len(lines), 2)
        with mock.patch.object(Path, "open", side_effect=OSError("ro")):
            registry._journal_write("w.yaml")     # never raises
        self.assertIn("w.yaml", registry._PROC_WRITES)


class LoadShapesTestCase(unittest.TestCase):
    def setUp(self):
        store2_testkit.use_backend(self, "yaml")

    def test_list_file_junk_members_and_unreadable_file(self):
        config.REGISTRY_DIR.mkdir(parents=True, exist_ok=True)
        (config.REGISTRY_DIR / "batch.yaml").write_text(
            "- id: R-001\n  title: one\n- 42\n- id: 2\n  title: 456\n", encoding="utf-8")
        (config.REGISTRY_DIR / "bad.yaml").write_text("id: [unclosed", encoding="utf-8")
        (config.REGISTRY_DIR / "empty.yaml").write_text("", encoding="utf-8")
        (config.REGISTRY_DIR / "scalar.yaml").write_text("just text\n", encoding="utf-8")
        with mock.patch("sys.stderr"):
            reqs = registry.load_all()
        self.assertEqual([(r.id, r.title, r._in_list) for r in reqs],
                         [("R-001", "one", True), ("2", "456", True)])
        self.assertEqual(registry._cards_from_file(config.REGISTRY_DIR / "scalar.yaml"), [])
        self.assertEqual(registry._cards_from_file(config.REGISTRY_DIR / "empty.yaml"), [])

    def test_archive_copy_wins_over_active_residue(self):
        registry.save(Requirement(id="P-007", title="active", status=State.DELIVERED.value))
        registry.ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
        (registry.ARCHIVE_DIR / "P-007.yaml").write_text(
            "id: P-007\ntitle: sealed\nstatus: archived\nprev_status: delivered\n",
            encoding="utf-8")
        found = registry.load("P-007")
        self.assertEqual((found.title, found.status), ("sealed", "archived"))
        self.assertTrue(registry._in_archive_dir(found))
        self.assertIsNone(registry.load("P-999"))
        self.assertFalse(registry._in_archive_dir(Requirement(id="x")))

    def test_sqlite_load_returns_none_for_missing_and_tombstone(self):
        store2_testkit.use_backend(self, "sqlite")
        self.assertIsNone(registry.load("P-404"))
        req = Requirement(id="P-001", title="t", status=State.TRASHED.value)
        registry.save(req)
        registry.delete(req)
        self.assertIsNone(registry.load("P-001"))
        self.assertEqual(registry.load_all(), [])


class DeleteShapesTestCase(unittest.TestCase):
    def setUp(self):
        store2_testkit.use_backend(self, "yaml")
        config.REGISTRY_DIR.mkdir(parents=True, exist_ok=True)

    def test_fresh_object_inherits_location_and_missing_card_is_false(self):
        registry.save(Requirement(id="P-010", title="a"))
        self.assertTrue(registry.delete(Requirement(id="P-010")))
        self.assertFalse((config.REGISTRY_DIR / "P-010.yaml").exists())
        self.assertFalse(registry.delete(Requirement(id="P-011")))

    def test_list_member_paths(self):
        path = config.REGISTRY_DIR / "batch.yaml"
        path.write_text("- id: 1\n  title: one\n- id: R-002\n  title: two\n", encoding="utf-8")
        self.assertTrue(registry.delete(registry.load("1")))
        self.assertEqual([r.id for r in registry.load_all()], ["R-002"])
        stub = Requirement(id="R-999")
        stub._file, stub._in_list = str(path), True
        self.assertFalse(registry.delete(stub))                 # not in file
        self.assertTrue(registry.delete(registry.load("R-002")))
        self.assertFalse(path.exists())                         # last member → file gone

    def test_unreadable_list_file_refuses(self):
        path = config.REGISTRY_DIR / "batch.yaml"
        path.write_text("- id: R-005\n  title: x\n", encoding="utf-8")
        req = registry.load("R-005")
        path.write_text("- id: [broken", encoding="utf-8")
        self.assertFalse(registry.delete(req))
        self.assertIsNone(registry._load_list_file(path))
        path.write_text("id: R-005\n", encoding="utf-8")
        self.assertEqual(registry._load_list_file(path), [{"id": "R-005"}])

    def test_unlink_failure_is_false(self):
        registry.save(Requirement(id="P-012", title="a"))
        req = registry.load("P-012")
        with mock.patch.object(Path, "unlink", side_effect=OSError("busy")):
            self.assertFalse(registry.delete(req))
        self.assertTrue(registry.delete(req))

    def test_sqlite_delete_paths(self):
        store2_testkit.use_backend(self, "sqlite")
        self.assertFalse(registry.delete(Requirement(id="P-404")))
        live = Requirement(id="P-020", title="live", status=State.CARD_SENT.value)
        registry.save(live)
        self.assertFalse(registry.delete(live))       # only trashed cards purge
        registry.trash(live, "deleted")
        self.assertTrue(registry.delete(live))
        self.assertFalse(registry.delete(live))       # tombstone → idempotent False


class AgentWallTestCase(unittest.TestCase):
    def setUp(self):
        store2_testkit.use_backend(self, "yaml")

    def test_forbidden_birth_rules(self):
        self.assertTrue(registry._forbidden_birth(Requirement(id="a"), "approved"))
        self.assertTrue(registry._forbidden_birth(Requirement(id="a", prev_status="review"), "trashed"))
        self.assertFalse(registry._forbidden_birth(Requirement(id="a"), "detected"))
        self.assertFalse(registry._forbidden_birth(Requirement(id="a", prev_status="detected"), "trashed"))

    def test_wall_only_for_agent_and_only_on_change(self):
        from act.lib.store2.store import TransitionDenied
        req = Requirement(id="P-030", title="t", status=State.CARD_SENT.value)
        registry._agent_wall(req, "approved")            # system actor: no-op
        with registry.acting_as("agent"):
            registry._agent_wall(req, "card_sent")       # same status: allowed
            with self.assertRaises(TransitionDenied):
                registry._agent_wall(req, "approved")
            with self.assertRaises(TransitionDenied):
                registry._agent_wall(Requirement(id="x", status="executing"), None)
            registry._agent_wall(Requirement(id="x", status="detected"), None)
            with self.assertRaises(ValueError):
                with registry.acting_as("robot"):
                    pass
        self.assertEqual(registry.current_actor(), "system")


class DisplayTitleRulesTestCase(unittest.TestCase):
    def test_accepted_title(self):
        req = Requirement(id="a")
        self.assertIsNone(registry._accepted_title(req, None, False))
        self.assertIsNone(registry._accepted_title(req, "  ", True))
        self.assertIsNone(registry._accepted_title(req, f"x {sanitize.MASK} y", True))
        self.assertEqual(registry._accepted_title(req, "  new  name ", False), "new name")
        req.user_titled = True
        self.assertIsNone(registry._accepted_title(req, "llm name", False))
        self.assertEqual(registry._accepted_title(req, "user name", True), "user name")

    def test_is_rename_normalises_only_for_llm_writes(self):
        self.assertFalse(registry._is_rename("same", "same", False))
        self.assertFalse(registry._is_rename("same", "same", True))
        # stored value is the un-normalised long form; the clip form is not a rename for LLM
        long_prev = "整理\n合同"
        clipped = registry._is_rename("整理 合同", long_prev, False)
        self.assertFalse(clipped)
        self.assertTrue(registry._is_rename("整理 合同", long_prev, True))
        self.assertTrue(registry._is_rename("other", "", False))

    def test_apply_rename_and_former_titles_cap(self):
        req = Requirement(id="a", former_titles=["", "  ", "old1", "old2"])
        registry._apply_rename(req, "new", "old3")
        self.assertEqual((req.display_title, req.former_titles), ("new", ["old1", "old2", "old3"]))
        registry._apply_rename(req, "newer", "old1")     # dedupe moves old1 to the end
        self.assertEqual(req.former_titles, ["old2", "old3", "old1"])
        registry._apply_rename(req, "x", "")
        self.assertEqual(req.former_titles, ["old2", "old3", "old1"])   # no prev → untouched

    def test_pin_user_title(self):
        req = Requirement(id="a")
        self.assertFalse(registry._pin_user_title(req, False))
        self.assertTrue(registry._pin_user_title(req, True))
        self.assertFalse(registry._pin_user_title(req, True))
        self.assertTrue(req.user_titled)

    def test_set_display_title_pin_only_change(self):
        req = Requirement(id="a", display_title="same")
        self.assertTrue(registry.set_display_title(req, "same", by_user=True))
        self.assertTrue(req.user_titled)
        self.assertFalse(registry.set_display_title(req, "same", by_user=True))


class GuardSnapshotStatTailTestCase(unittest.TestCase):
    def test_one_unstatable_file_is_skipped_not_fatal(self):
        store2_testkit.use_backend(self, "yaml")
        registry.save(Requirement(id="P-001", title="a"))
        registry.save(Requirement(id="P-002", title="b"))
        real_stat = Path.stat

        def flaky(self_path, *a, **kw):
            if self_path.name == "P-001.yaml":
                raise OSError("vanished")
            return real_stat(self_path, *a, **kw)

        with mock.patch.object(Path, "stat", flaky):
            self.assertEqual(list(registry.guard_snapshot()), ["P-002.yaml"])

    def test_duplicate_fold_note_returns_none(self):
        req = Requirement(id="x", notes="[radar] same [@t1]")
        self.assertIsNone(registry.append_fold_note(req, " same ", "radar"))
        self.assertEqual(req.notes, "[radar] same [@t1]")


if __name__ == "__main__":
    unittest.main()
