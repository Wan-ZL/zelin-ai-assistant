"""Voice-profile two-level fallback (docs/VOICE.md).

Resolution order in act/executor.py::resolve_voice_profile:
- state/voice-profile.md (private) exists  -> use it, default ignored;
- only config/voice-profile.default.md     -> use the repo default;
- neither                                  -> None, build_prompt injects nothing.

Paths derive from config.HOME (AIASSISTANT_HOME), which tests/__init__.py points
at a throwaway tmp dir before any act.* import — so both files here live in the
sandbox and the real repo/state is never touched.
"""
import unittest
from pathlib import Path

from tests import TMP_HOME  # noqa: F401 - ensures the sandbox env is set first

from act import executor
from act.lib import config
from act.lib.registry import Requirement

PRIVATE = config.STATE_DIR / "voice-profile.md"
DEFAULT = config.HOME / "config" / "voice-profile.default.md"


def _build_prompt() -> str:
    """build_prompt against a sandbox target with hermetic cfg (no memory read,
    no git remote probing outside the sandbox)."""
    cfg = config.Config()
    cfg.memory_inject = False
    req = Requirement.from_dict({"id": "R-970", "title": "voice fallback test"})
    return executor.build_prompt(req, cfg, target=Path(TMP_HOME) / "voice-target")


class VoiceProfileFallbackTestCase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        DEFAULT.parent.mkdir(parents=True, exist_ok=True)
        self._cleanup()

    def tearDown(self):
        # the sandbox HOME is shared by the whole suite — leave no voice files
        # behind for other test modules.
        self._cleanup()

    @staticmethod
    def _cleanup():
        for p in (PRIVATE, DEFAULT):
            if p.exists():
                p.unlink()

    # -- resolution order ------------------------------------------------------ #
    def test_private_wins_when_both_exist(self):
        PRIVATE.write_text("# private profile\n", encoding="utf-8")
        DEFAULT.write_text("# default profile\n", encoding="utf-8")
        self.assertEqual(executor.resolve_voice_profile(), PRIVATE)

    def test_default_used_when_private_missing(self):
        DEFAULT.write_text("# default profile\n", encoding="utf-8")
        self.assertEqual(executor.resolve_voice_profile(), DEFAULT)

    def test_none_when_neither_exists(self):
        self.assertIsNone(executor.resolve_voice_profile())

    # -- prompt injection ------------------------------------------------------ #
    def test_prompt_points_at_private_not_default(self):
        PRIVATE.write_text("# private profile\n", encoding="utf-8")
        DEFAULT.write_text("# default profile\n", encoding="utf-8")
        prompt = _build_prompt()
        self.assertIn("VOICE PROFILE", prompt)
        self.assertIn(str(PRIVATE), prompt)
        self.assertNotIn(str(DEFAULT), prompt)

    def test_prompt_points_at_default_when_no_private(self):
        DEFAULT.write_text("# default profile\n", encoding="utf-8")
        prompt = _build_prompt()
        self.assertIn("VOICE PROFILE", prompt)
        self.assertIn(str(DEFAULT), prompt)

    def test_prompt_has_no_voice_block_when_neither(self):
        prompt = _build_prompt()
        self.assertNotIn("VOICE PROFILE", prompt)
        self.assertNotIn("voice-profile", prompt)

    def test_injection_text_says_owner_not_zelin(self):
        DEFAULT.write_text("# default profile\n", encoding="utf-8")
        prompt = _build_prompt()
        self.assertIn("以 owner 名义", prompt)
        block = prompt[prompt.index("VOICE PROFILE"):]
        self.assertNotIn("Zelin", block.split("\n## ")[0])

    # -- repo ships a real default (checked against the actual checkout, not the
    # sandbox: the file must exist for fresh clones to get any injection) ------ #
    def test_repo_default_profile_exists_and_is_sanitized_shape(self):
        repo_default = Path(__file__).resolve().parents[1] / "config" / "voice-profile.default.md"
        self.assertTrue(repo_default.exists(), "config/voice-profile.default.md missing from repo")
        text = repo_default.read_text(encoding="utf-8")
        for section in ("全局铁律", "桶 A", "桶 B", "桶 C", "桶 D", "桶 E", "反面清单"):
            self.assertIn(section, text)
        self.assertIn("state/voice-profile.md", text)  # header points users at the override


if __name__ == "__main__":
    unittest.main()
