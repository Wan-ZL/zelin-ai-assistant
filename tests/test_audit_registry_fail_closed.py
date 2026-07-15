"""Audit regressions — registry fail-closed guards (findings 3/22).

  - finding 3: next_id() must never re-allocate the id of a card whose FILE
    exists but whose CONTENT is unreadable (hand-edit YAML typo, transient
    OSError): load_all() skips such files, so id allocation additionally
    counts R-<n> FILENAMES in both the active and archive dirs. Belt and
    braces: save() refuses to overwrite an existing card file it did not
    load when that file is unreadable (its bytes are still recoverable by
    hand — an overwrite would make the loss permanent).
  - finding 22: crash-mid-move archive residue (BOTH archive/R-X.yaml and
    the active R-X.yaml on disk): load() prefers the archive copy — matching
    the dashboard's archived_ids dedup — so the user's unarchive click works
    and repairs the residue instead of silently no-oping forever.

Runs entirely inside the sandbox AIASSISTANT_HOME (tests/__init__.py).
"""
import unittest
from pathlib import Path
from unittest import mock

import yaml

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act.lib import config, registry
from act.lib.registry import Requirement, State

CORRUPT = "{broken yaml: [\n"


def _clear_registry():
    config.ensure_state_dirs()
    for p in config.REGISTRY_DIR.glob("*.yaml"):
        p.unlink()
    if registry.ARCHIVE_DIR.exists():
        for p in registry.ARCHIVE_DIR.glob("*.yaml"):
            p.unlink()


# --------------------------------------------------------------------------- #
# finding 3: unreadable card files must keep their id allocated
# --------------------------------------------------------------------------- #
class NextIdFailClosedTestCase(unittest.TestCase):
    def setUp(self):
        _clear_registry()

    def test_next_id_counts_unreadable_active_file(self):
        registry.save(Requirement(id="R-001", title="readable card"))
        # highest id on disk is a corrupt file load_all() skips
        (config.REGISTRY_DIR / "R-042.yaml").write_text(CORRUPT, encoding="utf-8")
        # 曾经返回 R-002 —— 下一次 save 就会覆盖仍可手工修复的 R-042.yaml
        self.assertEqual(registry.next_id(), "R-043")

    def test_next_id_counts_unreadable_archive_file(self):
        registry.ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
        (registry.ARCHIVE_DIR / "R-050.yaml").write_text(CORRUPT, encoding="utf-8")
        self.assertEqual(registry.next_id(), "R-051")

    def test_next_id_unchanged_for_readable_files(self):
        registry.save(Requirement(id="R-007", title="readable card"))
        self.assertEqual(registry.next_id(), "R-008")


class SaveOverwriteGuardTestCase(unittest.TestCase):
    def setUp(self):
        _clear_registry()

    def test_save_refuses_overwriting_unreadable_file_it_did_not_load(self):
        p = config.REGISTRY_DIR / "R-042.yaml"
        p.write_text(CORRUPT, encoding="utf-8")
        with self.assertRaises(yaml.YAMLError):
            registry.save(Requirement(id="R-042", title="unrelated new card"))
        # the recoverable corrupt bytes survived
        self.assertEqual(p.read_text(encoding="utf-8"), CORRUPT)

    def test_save_still_overwrites_readable_file_via_fresh_object(self):
        registry.save(Requirement(id="R-001", title="v1"))
        registry.save(Requirement(id="R-001", title="v2"))  # fresh object, _file unset
        self.assertEqual(registry.load("R-001").title, "v2")

    def test_save_of_loaded_card_is_untouched_by_the_guard(self):
        registry.save(Requirement(id="R-001", title="v1"))
        req = registry.load("R-001")
        req.title = "v2"
        registry.save(req)  # _file is bound — normal update path
        self.assertEqual(registry.load("R-001").title, "v2")


# --------------------------------------------------------------------------- #
# finding 22: archive crash-mid-move residue must stay recoverable
# --------------------------------------------------------------------------- #
class ArchiveResidueTestCase(unittest.TestCase):
    def setUp(self):
        _clear_registry()

    def _make_residue(self) -> tuple[Path, Path]:
        """Reproduce archive()'s crash window: the archive copy is written
        (first file op) but the active original was never deleted (second)."""
        req = Requirement(id="R-010", title="done card",
                          status=State.DELIVERED.value)
        registry.save(req)
        active_path = Path(req._file)
        with mock.patch.object(registry, "_delete_original"):
            registry.archive(req, "user")
        archive_path = registry.ARCHIVE_DIR / "R-010.yaml"
        self.assertTrue(active_path.exists())   # residue
        self.assertTrue(archive_path.exists())  # authoritative copy
        return active_path, archive_path

    def test_load_prefers_archive_copy_over_active_residue(self):
        _active, archive_path = self._make_residue()
        got = registry.load("R-010")
        # 曾经返回 active 残件（status=delivered）——actd 的 unarchive guard
        # 因 status != archived 而静默 no-op，卡片永远卡在归档视图里。
        self.assertEqual(got.status, State.ARCHIVED.value)
        self.assertEqual(Path(got._file), archive_path)

    def test_unarchive_on_residue_restores_and_repairs(self):
        active_path, archive_path = self._make_residue()
        registry.unarchive(registry.load("R-010"))
        # single authoritative copy again: active restored, archive gone
        self.assertFalse(archive_path.exists())
        got = registry.load("R-010")
        self.assertEqual(got.status, State.DELIVERED.value)
        self.assertEqual(Path(got._file), active_path)
        copies = [r for r in registry.load_all(include_archived=True)
                  if r.id == "R-010"]
        self.assertEqual(len(copies), 1)

    def test_load_without_residue_still_returns_the_active_card(self):
        registry.save(Requirement(id="R-011", title="live card",
                                  status=State.CARD_SENT.value))
        got = registry.load("R-011")
        self.assertEqual(got.status, State.CARD_SENT.value)
        self.assertEqual(Path(got._file).parent, config.REGISTRY_DIR)


if __name__ == "__main__":
    unittest.main()
