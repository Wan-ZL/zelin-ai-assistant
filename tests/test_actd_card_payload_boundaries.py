"""Card-level payload boundaries the P3b mutation round found unpinned
(CONTRACT §22 session import / §37 set_title + CARD TITLE / §38 split_note).

Covered: the split card inherits the origin's tier/type with defaults, caps
title at 80 and summary at 120, and quotes the full note; set_title accepts
exactly 64 chars and rejects 65; the session import window defaults to 7 for
absent / garbage values and honours an explicit one, explicit ids win over the
window; a harvested title that is absent or ``None`` never calls
``set_display_title``.
"""
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env before act imports

from act import actd
from act.lib import config, registry
from act.lib.registry import Requirement, State


class PayloadBase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()
        mock.patch.object(actd.notify, "notify").start()
        self.addCleanup(mock.patch.stopall)


class SplitNoteBoundaryTest(PayloadBase):
    def test_split_card_defaults_and_caps(self):
        long_note = "备" * 150
        req = Requirement(id="R-1", title="origin", status=State.CARD_SENT.value,
                          tier=None, type=None)
        ts = registry.append_fold_note(req, long_note, "quick")
        registry.save(req)
        self.assertTrue(ts)
        self.assertEqual(actd._apply_split_note("R-1", ts), "running")
        new = next(r for r in registry.load_all() if r.split_from == "R-1")
        self.assertEqual(new.tier, "T1")
        self.assertEqual(new.type, "other")
        self.assertEqual(len(new.title), 80)
        self.assertEqual(len(new.summary), 120)
        self.assertEqual(new.sources[0]["quote"], long_note)
        self.assertEqual(new.sources[0]["channel"], "split")
        self.assertEqual(new.status, State.RAISING.value)


class SetTitleBoundaryTest(PayloadBase):
    def test_sixty_four_chars_accepted_sixty_five_rejected(self):
        req = Requirement(id="R-2", title="frozen", status=State.CARD_SENT.value)
        registry.save(req)
        self.assertEqual(actd._apply_set_title(req, "x" * 64), "running")
        self.assertEqual(registry.load("R-2").display_title, "x" * 64)
        self.assertEqual(actd._apply_set_title(req, "y" * 65), "noop")
        self.assertEqual(registry.load("R-2").display_title, "x" * 64)
        self.assertEqual(actd._apply_set_title(req, "   "), "noop")
        self.assertEqual(actd._apply_set_title(req, 42), "noop")


class SessionImportWindowTest(PayloadBase):
    def _run(self, decision):
        fake = mock.Mock()
        fake.run_once = mock.Mock(return_value=1)
        fake.import_by_ids = mock.Mock(return_value=2)
        with mock.patch.object(actd, "radar_claude_sessions", fake):
            result = actd._apply_claude_import(decision)
        return result, fake

    def test_window_defaults_to_seven_for_absent_and_garbage(self):
        for decision in ({"action": "import_claude_sessions"},
                         {"action": "import_claude_sessions", "window_days": "abc"},
                         {"action": "import_claude_sessions", "window_days": None},
                         {"action": "import_claude_sessions", "window_days": 0}):
            result, fake = self._run(decision)
            self.assertEqual(result, "running", decision)
            fake.run_once.assert_called_once_with(window_days=7)
            fake.import_by_ids.assert_not_called()

    def test_explicit_window_and_ids(self):
        result, fake = self._run({"action": "import_claude_sessions", "window_days": "3"})
        fake.run_once.assert_called_once_with(window_days=3)
        result, fake = self._run({"action": "import_claude_sessions",
                                  "session_ids": ["s1", "", None, 7]})
        self.assertEqual(result, "running")
        fake.import_by_ids.assert_called_once_with(["s1", "7"])
        fake.run_once.assert_not_called()

    def test_import_failure_is_noop_and_missing_module_is_noop(self):
        fake = mock.Mock()
        fake.run_once = mock.Mock(side_effect=RuntimeError("boom"))
        with mock.patch.object(actd, "radar_claude_sessions", fake):
            self.assertEqual(actd._apply_claude_import({"action": "import_claude_sessions"}), "noop")
        with mock.patch.object(actd, "radar_claude_sessions", None):
            self.assertEqual(actd._apply_claude_import({"action": "import_claude_sessions"}), "noop")


class HarvestTitleGuardTest(PayloadBase):
    def test_absent_or_none_title_never_touches_the_display_title(self):
        req = Requirement(id="R-3", title="t", status=State.EXECUTING.value)
        with mock.patch.object(registry, "set_display_title") as sdt:
            actd._apply_harvest_title(req, {})
            actd._apply_harvest_title(req, None)
            actd._apply_harvest_title(req, {"card_title": ""})
        sdt.assert_not_called()
        with mock.patch.object(registry, "set_display_title", return_value=True) as sdt:
            actd._apply_harvest_title(req, {"card_title": "新名字"})
        sdt.assert_called_once_with(req, "新名字")


if __name__ == "__main__":
    unittest.main()
