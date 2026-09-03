"""card_model — Requirement.from_dict / to_dict shapes (CONTRACT §1 / §2 / §20).

The model moved out of registry.py in P3b; this pins the round-trip contract
independently of the facade: the ``repo`` alias, delivery_mode tolerance,
numeric-scalar normalisation (id / title / tier / work_id), unknown keys
dropped, the to_dict skip table (None / "" / [] / False / 0 / repo), the CORE
block always present, the legacy ``merged_into:`` status helpers, and that the
registry facade re-exports the very same objects.
"""
import unittest

from act.lib import card_model, registry
from act.lib.card_model import Requirement, State


class FromDictTestCase(unittest.TestCase):
    def test_repo_alias_only_when_target_repo_absent(self):
        self.assertEqual(Requirement.from_dict({"id": "a", "repo": "/r"}).target_repo, "/r")
        self.assertEqual(Requirement.from_dict({"id": "a", "repo": "/r", "target_repo": "/t"}).target_repo,
                         "/t")

    def test_delivery_mode_tolerance(self):
        for raw, want in ((None, "repo"), ("", "repo"), (" CHAT ", "chat"), ("Repo", "repo"),
                          ("email", "repo"), (7, "repo")):
            self.assertEqual(Requirement.from_dict({"id": "a", "delivery_mode": raw}).delivery_mode, want,
                             raw)
        self.assertEqual(card_model._coerce_delivery_mode("chat"), "chat")

    def test_numeric_scalars_become_strings(self):
        req = Requirement.from_dict({"id": 4, "title": 456, "tier": 7, "work_id": 12})
        self.assertEqual((req.id, req.title, req.tier, req.work_id), ("4", "456", "7", "12"))
        req = Requirement.from_dict({"id": "x", "work_id": None})
        self.assertIsNone(req.work_id)

    def test_missing_id_and_unknown_keys(self):
        req = Requirement.from_dict({"title": "t", "bogus": 1, "_file": "/never"})
        self.assertEqual(req.id, "")
        self.assertIsNone(req._file)
        self.assertEqual(Requirement.from_dict(None).id, "")
        self.assertEqual(card_model._known_kwargs({"id": 1, "nope": 2, "_in_list": True}), {"id": 1})

    def test_stringify_scalars_leaves_other_types(self):
        kwargs = {"id": 1, "title": None, "plan": ["x"]}
        card_model._stringify_scalars(kwargs)
        self.assertEqual(kwargs, {"id": "1", "title": None, "plan": ["x"]})


class ToDictTestCase(unittest.TestCase):
    def test_core_block_always_present_in_order(self):
        out = Requirement(id="a").to_dict()
        self.assertEqual(list(out)[:len(card_model.CORE_ORDER)], card_model.CORE_ORDER)
        self.assertEqual(out["green_sign_required"], False)
        self.assertIsNone(out["deadline"])
        self.assertEqual(out["sources"], [])
        self.assertEqual(out["repeated_mentions"], 1)

    def test_skip_table(self):
        self.assertTrue(card_model._skip_optional("notes", ""))
        self.assertTrue(card_model._skip_optional("permanent", False))
        self.assertTrue(card_model._skip_optional("silent_merge_count", 0))
        self.assertTrue(card_model._skip_optional("former_titles", []))
        self.assertTrue(card_model._skip_optional("delivery_mode", "repo"))
        self.assertFalse(card_model._skip_optional("delivery_mode", "chat"))
        self.assertFalse(card_model._skip_optional("permanent", True))
        self.assertFalse(card_model._skip_optional("silent_merge_count", 2))
        self.assertFalse(card_model._skip_optional("notes", "x"))

    def test_optionals_only_when_set_and_in_vocabulary_order(self):
        req = Requirement(id="a", delivery_mode="chat", silent_merge_count=3, needs_mcp=True,
                          summary="s", work_id="R-9", merged_from=["P-1"], permanent=True)
        out = req.to_dict()
        optional_keys = list(out)[len(card_model.CORE_ORDER):]
        self.assertEqual(optional_keys, ["summary", "delivery_mode", "permanent",
                                         "silent_merge_count", "work_id", "needs_mcp", "merged_from"])
        self.assertNotIn("notes", out)
        self.assertNotIn("_file", out)

    def test_round_trip_is_stable(self):
        req = Requirement(id="a", title="t", delivery_mode="chat", former_titles=["x"],
                          execution={"k": 1}, needs_mcp=True)
        again = Requirement.from_dict(req.to_dict())
        self.assertEqual(again, req)
        self.assertEqual(again.to_dict(), req.to_dict())


class StatusHelpersTestCase(unittest.TestCase):
    def test_legacy_merged_status(self):
        req = Requirement(id="a", status="merged_into:P-7", merged_into="P-9")
        self.assertTrue(req.is_merged)
        self.assertEqual(req.merged_parent, "P-7")
        req = Requirement(id="a", status=State.MERGED.value, merged_into="P-9")
        self.assertFalse(req.is_merged)
        self.assertEqual(req.merged_parent, "P-9")
        req.status = None
        self.assertFalse(req.is_merged)

    def test_set_status_stores_the_bare_value(self):
        req = Requirement(id="a")
        req.set_status(State.REVIEW)
        self.assertEqual(req.status, "review")
        self.assertEqual(str(State.REVIEW), "review")
        self.assertEqual(f"{State.TRASHED}", "trashed")


class FacadeReexportTestCase(unittest.TestCase):
    def test_registry_reexports_the_same_objects(self):
        self.assertIs(registry.Requirement, card_model.Requirement)
        self.assertIs(registry.State, card_model.State)
        self.assertIs(registry.CORE_ORDER, card_model.CORE_ORDER)
        self.assertIs(registry.OPTIONAL_ORDER, card_model.OPTIONAL_ORDER)
        self.assertEqual(registry.MERGED_PREFIX, card_model.MERGED_PREFIX)

    def test_vocabulary_matches_the_dataclass_public_fields(self):
        public = {f for f in Requirement.__dataclass_fields__ if not f.startswith("_")}
        self.assertEqual(set(card_model.CORE_ORDER) | set(card_model.OPTIONAL_ORDER), public)
        self.assertEqual(len(set(card_model.CORE_ORDER) & set(card_model.OPTIONAL_ORDER)), 0)


if __name__ == "__main__":
    unittest.main()
