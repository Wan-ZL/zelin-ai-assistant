"""Audit regressions — registry list-file save/delete branches (findings 4/23/49).

A registry file may hold a YAML LIST of cards (e.g. the R-002..R-006 debt
batch); save() on one member rewrites the WHOLE file and delete() drops one
entry. Until now these branches had zero coverage. Covered here:

  - finding 49: save() on a member preserves every sibling; delete() removes
    only the target entry and unlinks the file once it is empty;
  - finding 4: save() REFUSES to rewrite a list file whose content became
    unreadable/corrupt — treating it as empty used to silently destroy every
    sibling AND the still-recoverable corrupt bytes;
  - finding 23: hand-written unquoted numeric ids (`id: 4` — int on disk,
    str-normalized in memory) replace/delete the RIGHT entry instead of
    appending a duplicate row / dropping nothing.

Runs entirely inside the sandbox AIASSISTANT_HOME (tests/__init__.py).
"""
import unittest

import yaml

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act.lib import config, registry
from act.lib.registry import State


def _clear_registry():
    config.ensure_state_dirs()
    for p in config.REGISTRY_DIR.glob("*.yaml"):
        p.unlink()
    if registry.ARCHIVE_DIR.exists():
        for p in registry.ARCHIVE_DIR.glob("*.yaml"):
            p.unlink()


class ListFileBase(unittest.TestCase):
    def setUp(self):
        _clear_registry()
        self.path = config.REGISTRY_DIR / "debt-batch.yaml"
        docs = [{"id": i, "title": f"card {i}", "status": "detected"}
                for i in ("R-002", "R-003", "R-004")]
        self.path.write_text(
            yaml.safe_dump(docs, allow_unicode=True, sort_keys=False),
            encoding="utf-8")

    def _member(self, rid):
        return next(r for r in registry.load_all() if r.id == rid)


# --------------------------------------------------------------------------- #
# finding 49: baseline round-trip coverage for the _in_list branches
# --------------------------------------------------------------------------- #
class ListFileSaveTestCase(ListFileBase):
    def test_save_list_member_preserves_siblings(self):
        mid = self._member("R-003")
        mid.set_status(State.CARD_SENT)
        registry.save(mid)
        reloaded = {r.id: r for r in registry.load_all()}
        self.assertEqual(sorted(reloaded), ["R-002", "R-003", "R-004"])
        self.assertEqual(reloaded["R-003"].status, "card_sent")
        self.assertEqual(reloaded["R-002"].status, "detected")
        self.assertEqual(reloaded["R-004"].status, "detected")
        self.assertEqual(reloaded["R-002"].title, "card R-002")
        self.assertEqual(reloaded["R-004"].title, "card R-004")
        # list membership itself survives the round-trip: a second save on the
        # reloaded member must edit the SAME file in place, not fork a copy.
        self.assertTrue(reloaded["R-003"]._in_list)
        self.assertEqual(reloaded["R-003"]._file, str(self.path))

    def test_save_second_round_trip_still_in_place(self):
        mid = self._member("R-003")
        mid.set_status(State.CARD_SENT)
        registry.save(mid)
        again = self._member("R-003")
        again.set_status(State.APPROVED)
        registry.save(again)
        self.assertEqual(sorted(r.id for r in registry.load_all()),
                         ["R-002", "R-003", "R-004"])
        self.assertEqual(self._member("R-003").status, "approved")


class ListFileDeleteTestCase(ListFileBase):
    def test_delete_list_member_removes_only_that_entry(self):
        self.assertTrue(registry.delete(self._member("R-003")))
        self.assertEqual(sorted(r.id for r in registry.load_all()),
                         ["R-002", "R-004"])
        self.assertTrue(self.path.exists())

    def test_delete_last_entry_unlinks_file(self):
        for rid in ("R-002", "R-003", "R-004"):
            self.assertTrue(registry.delete(self._member(rid)))
        self.assertFalse(self.path.exists())
        self.assertEqual(registry.load_all(), [])

    def test_delete_missing_member_returns_false(self):
        stub = self._member("R-003")
        self.assertTrue(registry.delete(stub))
        self.assertFalse(registry.delete(stub))  # already gone — nothing removed


# --------------------------------------------------------------------------- #
# finding 4: an unreadable list file must never be treated as empty
# --------------------------------------------------------------------------- #
class CorruptListFileFailClosedTestCase(ListFileBase):
    CORRUPT = "{broken yaml: [\n"

    def test_save_refuses_unreadable_list_file(self):
        mid = self._member("R-003")          # held in memory (actd/executor do this)
        self.path.write_text(self.CORRUPT, encoding="utf-8")
        mid.set_status(State.CARD_SENT)
        with self.assertRaises(yaml.YAMLError):
            registry.save(mid)
        # fail-closed: the corrupt (hand-recoverable) bytes were NOT replaced
        self.assertEqual(self.path.read_text(encoding="utf-8"), self.CORRUPT)

    def test_delete_still_refuses_unreadable_list_file(self):
        mid = self._member("R-003")
        self.path.write_text(self.CORRUPT, encoding="utf-8")
        self.assertFalse(registry.delete(mid))
        self.assertEqual(self.path.read_text(encoding="utf-8"), self.CORRUPT)


# --------------------------------------------------------------------------- #
# finding 23: int id on disk vs str-normalized id in memory
# --------------------------------------------------------------------------- #
class NumericIdListEntryTestCase(unittest.TestCase):
    RAW = ("- id: 4\n  title: numeric id card\n  status: detected\n"
           "- id: R-005\n  title: sibling\n  status: detected\n")

    def setUp(self):
        _clear_registry()
        self.path = config.REGISTRY_DIR / "hand-batch.yaml"
        self.path.write_text(self.RAW, encoding="utf-8")

    def test_save_numeric_id_replaces_in_place_not_duplicates(self):
        card = next(r for r in registry.load_all() if r.id == "4")
        registry.trash(card, reason="deleted")
        data = yaml.safe_load(self.path.read_text(encoding="utf-8"))
        # one row per card — the trash used to APPEND a second "4" row while
        # the int-id original kept status detected (card shown twice on board)
        self.assertEqual(len(data), 2)
        entry = next(d for d in data if str(d["id"]) == "4")
        self.assertEqual(entry["status"], "trashed")
        sibling = next(d for d in data if str(d["id"]) == "R-005")
        self.assertEqual(sibling["status"], "detected")

    def test_delete_numeric_id_removes_the_right_entry(self):
        card = next(r for r in registry.load_all() if r.id == "4")
        self.assertTrue(registry.delete(card))
        data = yaml.safe_load(self.path.read_text(encoding="utf-8"))
        self.assertEqual([str(d["id"]) for d in data], ["R-005"])


if __name__ == "__main__":
    unittest.main()
