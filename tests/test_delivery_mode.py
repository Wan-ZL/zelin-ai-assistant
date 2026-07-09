"""registry delivery_mode — CONTRACT §20 (v0.10).

- missing / unknown / null values normalize to "repo" on load (never None);
- "chat" survives a save -> load_all round-trip;
- "repo" is the default and is NOT serialized (missing == repo keeps YAML clean).

Registry files live under the sandbox AIASSISTANT_HOME (tests/__init__.py).
"""
import unittest

from tests import TMP_HOME  # noqa: F401 - ensures the sandbox env is set first

from act.lib import config, registry
from act.lib.registry import Requirement


class DeliveryModeTestCase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        if config.REGISTRY_DIR.exists():
            for p in config.REGISTRY_DIR.glob("*.yaml"):
                p.unlink()

    # -- load tolerance (§20) -------------------------------------------------- #
    def test_missing_delivery_mode_defaults_to_repo(self):
        req = Requirement.from_dict({"id": "R-900", "title": "老条目"})
        self.assertEqual(req.delivery_mode, "repo")

    def test_invalid_values_normalize_to_repo(self):
        for bad in ("banana", "", None, 42):
            req = Requirement.from_dict(
                {"id": "R-901", "title": "x", "delivery_mode": bad})
            self.assertEqual(req.delivery_mode, "repo", msg=repr(bad))

    def test_case_and_whitespace_tolerant(self):
        for raw in ("Chat", "CHAT", "  chat  "):
            req = Requirement.from_dict(
                {"id": "R-902", "title": "x", "delivery_mode": raw})
            self.assertEqual(req.delivery_mode, "chat", msg=repr(raw))
        req = Requirement.from_dict(
            {"id": "R-903", "title": "x", "delivery_mode": "REPO"})
        self.assertEqual(req.delivery_mode, "repo")

    # -- round-trip through save/load ------------------------------------------ #
    def test_chat_survives_save_load_roundtrip(self):
        req = Requirement.from_dict(
            {"id": "R-910", "title": "聊天交付任务", "delivery_mode": "chat"})
        registry.save(req)
        loaded = [r for r in registry.load_all() if r.id == "R-910"]
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].delivery_mode, "chat")

    def test_repo_is_not_serialized_and_loads_back_as_repo(self):
        req = Requirement.from_dict({"id": "R-911", "title": "分支交付任务"})
        registry.save(req)
        text = (config.REGISTRY_DIR / "R-911.yaml").read_text(encoding="utf-8")
        self.assertNotIn("delivery_mode", text)  # missing == repo (§20)
        loaded = [r for r in registry.load_all() if r.id == "R-911"][0]
        self.assertEqual(loaded.delivery_mode, "repo")

    def test_to_dict_serializes_only_chat(self):
        chat = Requirement.from_dict(
            {"id": "R-912", "title": "x", "delivery_mode": "chat"})
        self.assertEqual(chat.to_dict().get("delivery_mode"), "chat")
        repo = Requirement.from_dict({"id": "R-913", "title": "x"})
        self.assertNotIn("delivery_mode", repo.to_dict())


if __name__ == "__main__":
    unittest.main()
