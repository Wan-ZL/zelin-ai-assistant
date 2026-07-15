"""Audit fixes — act/digest.py + act/oneonone.py.

* Output paths: the Monday digest and the 1:1 prep page used to write to the
  hardcoded placeholder ``~/Projects/your-workbench`` regardless of
  ``execution.default_target_repo``. They now resolve the output root at call
  time: the configured repo when set, ``STATE_DIR`` otherwise — the literal
  example placeholder path is never created (config.default_target_repo_configured).
* Stable suggestion titles: 进化建议 embedded live counts in the card title,
  defeating merge_or_new's title dedup — a near-duplicate self-improvement
  card stacked every Monday the number moved. Titles are now stable; volatile
  counts live only in summary/sources.quote.

Everything runs inside the sandbox AIASSISTANT_HOME (tests/__init__.py).
"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act.* import

from act import digest, oneonone
from act.lib import config, registry


class DigestOutputPathTestCase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        if config.CONFIG_PATH.exists():
            config.CONFIG_PATH.unlink()
        self.addCleanup(lambda: config.CONFIG_PATH.unlink(missing_ok=True))
        # quiet + deterministic: no notification queue writes from these tests
        patcher = mock.patch.object(digest.notify, "notify", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_unconfigured_falls_back_to_state_dir(self):
        path = digest.write_digest()
        self.assertEqual(path.parent, config.STATE_DIR / "digests")
        self.assertTrue(path.exists())
        # the placeholder path is never part of any output location
        self.assertNotIn("your-workbench", str(path))
        preps = list((config.STATE_DIR / "oneonone").glob("prep-*.md"))
        self.assertTrue(preps, "1:1 prep page must land under state/ too")

    def test_configured_repo_receives_digest_and_prep(self):
        tmp = tempfile.TemporaryDirectory(prefix="workbench-")
        self.addCleanup(tmp.cleanup)
        config.CONFIG_PATH.write_text(
            f"execution:\n  default_target_repo: {tmp.name}\n",
            encoding="utf-8")
        path = digest.write_digest()
        self.assertEqual(path.parent, Path(tmp.name) / "digests")
        self.assertTrue(path.exists())
        self.assertTrue((Path(tmp.name) / "oneonone").is_dir())

    def test_output_root_reads_config_at_call_time(self):
        self.assertEqual(oneonone.output_root(), config.STATE_DIR)
        tmp = tempfile.TemporaryDirectory(prefix="workbench-")
        self.addCleanup(tmp.cleanup)
        config.CONFIG_PATH.write_text(
            f"execution:\n  default_target_repo: {tmp.name}\n",
            encoding="utf-8")
        self.assertEqual(oneonone.output_root(), Path(tmp.name))


class SuggestionTitleStabilityTestCase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()
        self.cfg = config.load_config()

    @staticmethod
    def _events(resume_fails: int, rejects: int = 0, approves: int = 0) -> list:
        return ([{"event": "auto_resume", "ok": False}] * resume_fails
                + [{"event": "inbox_reject"}] * rejects
                + [{"event": "inbox_approve"}] * approves)

    def test_titles_stable_while_counts_move_to_detail(self):
        s1 = digest.build_suggestions(self.cfg, events=self._events(12, 3, 1))
        s2 = digest.build_suggestions(self.cfg, events=self._events(17, 6, 2))
        self.assertEqual(len(s1), 2)
        self.assertEqual([t for t, _ in s1], [t for t, _ in s2])
        # no live count leaks into a title
        self.assertNotIn("12", s1[0][0])
        self.assertNotIn("3/4", s1[1][0])
        # the live counts still surface — in the volatile detail
        self.assertIn("12", s1[0][1])
        self.assertIn("17", s2[0][1])
        self.assertIn("3/4", s1[1][1])
        self.assertIn("6/8", s2[1][1])

    def test_repeat_mondays_merge_into_one_card(self):
        filed1 = digest.file_suggestion_cards(
            digest.build_suggestions(self.cfg, events=self._events(12)))
        filed2 = digest.file_suggestion_cards(
            digest.build_suggestions(self.cfg, events=self._events(17)))
        self.assertEqual(len(filed1), 1)
        self.assertEqual(len(filed2), 1)
        self.assertEqual(filed1[0].id, filed2[0].id,
                         "week 2 must merge into week 1's card, not stack")
        cards = [r for r in registry.load_all() if r.type == "self-improvement"]
        self.assertEqual(len(cards), 1)
        # counts stayed out of the title but landed in the card content
        self.assertNotIn("12", cards[0].title)
        self.assertIn("12", cards[0].summary)


if __name__ == "__main__":
    unittest.main()
