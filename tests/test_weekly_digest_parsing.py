"""act/weekly_digest — reply parsing, note collection budget and CLI exit
codes (§24).

Pinned (P3 mutation net):
- ``parse_output``: ```json fence stripped; a JSON object inside prose is
  found; blank / non-object / empty-digest replies are None (the caller
  aborts instead of filing an empty card);
- ``collect_notes``: only ``*.md`` modified inside the 7-day window, newest
  first, exactly-at-cutoff included, unstattable entries skipped;
- ``_notes_material``: newest-first excerpts, per-file head cap, and the
  TOTAL_CHARS budget stops adding files;
- ``due``: wrong weekday / too early / ran <6 days ago -> not due; an
  unparseable ``last_run`` is treated as never run;
- ``main``: not_due -> 0, ok -> 0, failure -> 1.
"""
import datetime as _dt
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import weekly_digest as wd
from act.lib import config


class ParseOutputTestCase(unittest.TestCase):
    def test_fenced_json_is_accepted(self):
        raw = '```json\n{"digest": "本周…"}\n```'
        self.assertEqual(wd.parse_output(raw), {"digest": "本周…"})

    def test_object_inside_prose_is_found(self):
        raw = 'Here you go: {"digest": "recap", "suggestions": []} thanks'
        self.assertEqual(wd.parse_output(raw)["digest"], "recap")

    def test_rejects_blank_non_object_and_empty_digest(self):
        self.assertIsNone(wd.parse_output(""))
        self.assertIsNone(wd.parse_output("   "))
        self.assertIsNone(wd.parse_output(None))
        self.assertIsNone(wd.parse_output("just words"))
        self.assertIsNone(wd.parse_output('["digest"]'))
        self.assertIsNone(wd.parse_output('{"digest": "   "}'))
        self.assertIsNone(wd.parse_output('{"other": 1}'))
        self.assertIsNone(wd.parse_output("prose {not: json} prose"))


class CollectNotesTestCase(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="wd-notes-"))
        self.cfg = config.Config()
        self.now = _dt.datetime(2026, 7, 13, 12, 0, 0)
        patcher = mock.patch.object(config, "effective_obsidian_raw",
                                    return_value=self.root)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _note(self, name, age_days, text="x"):
        p = self.root / name
        p.write_text(text, encoding="utf-8")
        ts = self.now.timestamp() - age_days * 86400
        os.utime(p, (ts, ts))
        return p

    def test_window_newest_first_and_cutoff_inclusive(self):
        self._note("old.md", 8)
        at = self._note("at-cutoff.md", wd.WINDOW_DAYS)
        newer = self._note("newer.md", 1)
        (self.root / "not-a-note.txt").write_text("skip", encoding="utf-8")
        notes = wd.collect_notes(self.cfg, self.now)
        self.assertEqual([p for p, _ in notes], [newer, at])

    def test_missing_dir_is_empty(self):
        with mock.patch.object(config, "effective_obsidian_raw", return_value=None):
            self.assertEqual(wd.collect_notes(self.cfg, self.now), [])
        with mock.patch.object(config, "effective_obsidian_raw",
                               return_value=self.root / "nope"):
            self.assertEqual(wd.collect_notes(self.cfg, self.now), [])

    def test_unstattable_entry_is_skipped(self):
        good = self._note("good.md", 1)
        real_stat = Path.stat

        def flaky(self_, *a, **kw):
            if self_.name == "ghost.md":
                raise OSError("vanished")
            return real_stat(self_, *a, **kw)

        self._note("ghost.md", 1)
        with mock.patch.object(Path, "stat", flaky):
            notes = wd.collect_notes(self.cfg, self.now)
        self.assertEqual([p for p, _ in notes], [good])

    def test_material_budget(self):
        a = self._note("a.md", 1, "A" * 10)
        b = self._note("b.md", 2, "B" * (wd.PER_FILE_CHARS + 50))
        material = wd._notes_material([(a, 2.0), (b, 1.0)])
        self.assertIn("### a.md\n" + "A" * 10, material)
        self.assertIn("### b.md\n" + "B" * wd.PER_FILE_CHARS + "\n", material)
        self.assertNotIn("B" * (wd.PER_FILE_CHARS + 1), material)
        with mock.patch.object(wd, "TOTAL_CHARS", 30):
            capped = wd._notes_material([(a, 2.0), (b, 1.0)])
        self.assertIn("### a.md", capped)
        self.assertNotIn("### b.md", capped)     # budget hit -> stop adding

    def test_unreadable_note_is_skipped_in_material(self):
        a = self._note("a.md", 1, "AAA")
        material = wd._notes_material([(self.root / "missing.md", 3.0), (a, 2.0)])
        self.assertEqual(material, "### a.md\nAAA\n")


class DueTestCase(unittest.TestCase):
    def setUp(self):
        self.cfg = config.Config()
        self.cfg.weekly_digest_day = 0     # Monday
        self.cfg.weekly_digest_hour = 9

    def test_weekday_hour_and_recent_run(self):
        monday_10 = _dt.datetime(2026, 7, 13, 10, 0)
        self.assertTrue(wd.due(self.cfg, {}, monday_10))
        self.assertFalse(wd.due(self.cfg, {}, _dt.datetime(2026, 7, 14, 10, 0)))  # Tuesday
        self.assertFalse(wd.due(self.cfg, {}, _dt.datetime(2026, 7, 13, 8, 59)))  # too early
        self.assertTrue(wd.due(self.cfg, {}, _dt.datetime(2026, 7, 13, 9, 0)))    # at the hour
        self.assertFalse(wd.due(self.cfg, {"last_run": "2026-07-08"}, monday_10))  # 5 days
        self.assertTrue(wd.due(self.cfg, {"last_run": "2026-07-07"}, monday_10))   # 6 days
        self.assertTrue(wd.due(self.cfg, {"last_run": "garbage"}, monday_10))


class MainTestCase(unittest.TestCase):
    def _rc(self, summary):
        with mock.patch.object(wd, "run", return_value=summary):
            return wd.main([])

    def test_exit_codes(self):
        self.assertEqual(self._rc({"ok": True, "skipped": "not_due"}), 0)
        self.assertEqual(self._rc({"ok": True, "skipped": None, "digest_id": "P-1"}), 0)
        self.assertEqual(self._rc({"ok": False, "skipped": None, "error": "x"}), 1)
        self.assertEqual(self._rc({"ok": False, "skipped": "disabled"}), 1)

    def test_now_flag_forces(self):
        with mock.patch.object(wd, "run", return_value={"ok": True, "skipped": None}) as run:
            wd.main(["--now"])
        run.assert_called_once_with(force=True)


if __name__ == "__main__":
    unittest.main()
