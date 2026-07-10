"""act/radar.py — Obsidian radar prompts pass through sanitize.scrub (P1-7).

The two ``claude -p`` call sites (requirement extraction and manager
action-items drafting) used to send note_text verbatim; both must now scrub
the outbound prompt. subprocess.run is monkeypatched so no real claude runs,
and the prompt handed to it is inspected directly. config.yaml lives in the
sandbox AIASSISTANT_HOME set in tests/__init__.py and is removed in tearDown.
"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - ensures the sandbox env is set first

from act import radar
from act.lib import config, sanitize

TERM = "ProjectPhoenix"
SECRET = "sk-ant-api03-abcdefghijklmnop"


def _fake_run(*args, **kwargs):
    return mock.Mock(returncode=0, stdout="[]", stderr="")


class RadarScrubTestCase(unittest.TestCase):
    def setUp(self):
        self._cleanup()
        self.tmp = tempfile.TemporaryDirectory(prefix="radar-scrub-")
        terms_file = Path(self.tmp.name) / "terms.txt"
        terms_file.write_text(f"{TERM}\n", encoding="utf-8")
        config.CONFIG_PATH.write_text(
            "sources:\n"
            '  watch_people: ["boss.man"]\n'
            "features:\n"
            "  manager_pack: true\n"  # explicit-enable only (post-2026-07-08)
            "execution:\n"
            f'  default_target_repo: "{self.tmp.name}/workbench"\n'
            "redaction:\n"
            "  enabled: true\n"
            f'  terms_file: "{terms_file}"\n',
            encoding="utf-8",
        )

    def tearDown(self):
        self._cleanup()
        self.tmp.cleanup()

    @staticmethod
    def _cleanup():
        for p in (config.CONFIG_PATH, config.SETTINGS_OVERRIDES_PATH):
            if p.exists():
                p.unlink()

    # -- extraction call site --------------------------------------------- #
    def test_extract_prompt_masks_redaction_terms_and_secrets(self):
        note = f"会上说要跟进 {TERM}，另外屏幕上闪过 {SECRET}"
        with mock.patch.object(radar.subprocess, "run", side_effect=_fake_run) as run:
            radar._run_extract(note)
        prompt = run.call_args[0][0][-1]
        self.assertNotIn(TERM, prompt)
        self.assertNotIn(SECRET, prompt)
        self.assertIn(sanitize.MASK, prompt)
        # the static instruction header survives the scrub untouched
        self.assertIn("requirement radar", prompt)

    def test_injected_runner_bypasses_subprocess(self):
        """Injectable-runner contract unchanged: runner receives the raw note."""
        seen = {}

        def runner(note_text):
            seen["note"] = note_text
            return "[]"

        radar._run_extract(f"note about {TERM}", runner=runner)
        self.assertEqual(seen["note"], f"note about {TERM}")

    # -- manager action-items call site ------------------------------------ #
    def test_action_items_prompt_masks_redaction_terms(self):
        cfg = config.load_config()  # explicit target repo -> tmp workbench
        note = Path(self.tmp.name) / "2026-07-08-sync.md"
        text = f"boss 让我把 {TERM} 的评审安排一下"
        with mock.patch.object(radar.notify, "notify"), \
                mock.patch.object(radar.subprocess, "run", side_effect=_fake_run) as run:
            radar.manager_action_items(note, text, cfg)
        prompt = run.call_args[0][0][-1]
        self.assertNotIn(TERM, prompt)
        self.assertIn(sanitize.MASK, prompt)


if __name__ == "__main__":
    unittest.main()
