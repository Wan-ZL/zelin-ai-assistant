"""search_index — the never-raises edges of update_card / prune (§37).

Pins the P3b split: blank card id, transcript reader crash, registry crash
during prune, empty index file, and the gone-shapes table (legacy
``merged_into:`` status + legacy bare ``rejected`` drop; everything else stays).
"""
import json
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first

from act import executor
from act.lib import registry, search_index
from act.lib.registry import Requirement, State

SID = "dddd4444-0000-4000-8000-000000000004"


class UpdateCardEdgesTestCase(unittest.TestCase):
    def setUp(self):
        search_index.INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
        if search_index.INDEX_PATH.exists():
            search_index.INDEX_PATH.unlink()

    def test_blank_card_id_is_a_noop(self):
        with mock.patch.object(executor, "transcript_plain_text") as tpt:
            self.assertFalse(search_index.update_card("   ", SID))
            self.assertFalse(search_index.update_card(None, SID))
        tpt.assert_not_called()

    def test_reader_crash_is_swallowed(self):
        with mock.patch.object(executor, "transcript_plain_text",
                               side_effect=RuntimeError("boom")):
            self.assertFalse(search_index.update_card("R-970", SID))
        self.assertFalse(search_index.INDEX_PATH.exists())

    def test_changed_text_rewrites_entry(self):
        with mock.patch.object(executor, "transcript_plain_text", return_value="v1"):
            self.assertTrue(search_index.update_card("R-971", SID))
        with mock.patch.object(executor, "transcript_plain_text", return_value="v2"):
            self.assertTrue(search_index.update_card("R-971", SID))
        self.assertEqual(search_index.load_index()["R-971"]["text"], "v2")

    def test_same_text_helper(self):
        self.assertTrue(search_index._same_text({"text": "a"}, "a"))
        self.assertFalse(search_index._same_text({"text": "b"}, "a"))
        self.assertFalse(search_index._same_text("not-a-dict", "a"))
        self.assertFalse(search_index._same_text(None, "a"))


class PruneEdgesTestCase(unittest.TestCase):
    def setUp(self):
        search_index.INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)

    def _index(self, data: dict) -> None:
        search_index.INDEX_PATH.write_text(json.dumps(data), encoding="utf-8")

    def test_empty_index_file_is_zero_without_registry_scan(self):
        self._index({})
        with mock.patch.object(registry, "load_all") as la:
            self.assertEqual(search_index.prune(), 0)
        la.assert_not_called()

    def test_registry_crash_returns_zero_and_keeps_file(self):
        self._index({"R-980": {"updated_at": "x", "text": "t"}})
        with mock.patch.object(registry, "load_all", side_effect=OSError("disk")):
            self.assertEqual(search_index.prune(), 0)
        self.assertEqual(list(search_index.load_index()), ["R-980"])

    def test_legacy_merged_into_and_rejected_drop(self):
        rows = [
            Requirement(id="R-981", title="legacy merged", status="merged_into:R-1"),
            Requirement(id="R-982", title="legacy rejected", status=State.REJECTED.value),
            Requirement(id="R-983", title="detected stays", status=State.DETECTED.value),
        ]
        self._index({r.id: {"updated_at": "x", "text": "t"} for r in rows})
        with mock.patch.object(registry, "load_all", return_value=rows):
            self.assertEqual(search_index.prune(), 2)
        self.assertEqual(list(search_index.load_index()), ["R-983"])

    def test_nothing_stale_does_not_rewrite(self):
        rows = [Requirement(id="R-984", title="live", status=State.CARD_SENT.value)]
        self._index({"R-984": {"updated_at": "x", "text": "t"}})
        with mock.patch.object(registry, "load_all", return_value=rows), \
                mock.patch.object(search_index, "_write") as w:
            self.assertEqual(search_index.prune(), 0)
        w.assert_not_called()

    def test_gone_table(self):
        self.assertTrue(search_index._gone(Requirement(id="a", status="merged_into:b"), registry))
        self.assertTrue(search_index._gone(Requirement(id="a", status="merged"), registry))
        self.assertTrue(search_index._gone(Requirement(id="a", status="rejected"), registry))
        self.assertFalse(search_index._gone(Requirement(id="a", status="trashed"), registry))
        self.assertFalse(search_index._gone(Requirement(id="a", status="archived"), registry))


if __name__ == "__main__":
    unittest.main()
