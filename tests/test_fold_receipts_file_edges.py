"""fold_receipts — the per-file helpers behind record / load_recent / sweep (§44.6).

Pins: entry shape (None req/channel → "", explicit now), the atomic write's
tmp cleanup on failure, the row parser's four rejection reasons (unreadable,
non-dict, non-numeric ``at``, expired, no ``req``), the ``id`` fallback to the
file stem, stale unlink accounting (stat error = not removed), and that
record() swallows a mkdir failure.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first

from act.lib import fold_receipts as fr


class EntryAndWriteTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="fold-rcpt-")
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_entry_shape(self):
        self.assertEqual(fr._entry("rid", None, None, 12.9),
                         {"id": "rid", "req": "", "channel": "", "at": 12})
        with mock.patch.object(fr.time, "time", return_value=77.7):
            self.assertEqual(fr._entry("rid", "R-1", "quick", None)["at"], 77)

    def test_write_entry_is_atomic_and_cleans_tmp(self):
        fr._write_entry(self.dir, "abc", {"id": "abc"})
        self.assertEqual(json.loads((self.dir / "abc.json").read_text()), {"id": "abc"})
        self.assertFalse((self.dir / "abc.json.tmp").exists())
        with mock.patch.object(fr.os, "replace", side_effect=OSError("cross-device")):
            with self.assertRaises(OSError):
                fr._write_entry(self.dir, "def", {"id": "def"})
        self.assertFalse((self.dir / "def.json.tmp").exists())
        self.assertFalse((self.dir / "def.json").exists())

    def test_record_swallows_directory_failure(self):
        with mock.patch.object(fr, "_dir", return_value=self.dir / "file-not-dir"):
            (self.dir / "file-not-dir").write_text("x", encoding="utf-8")
            self.assertIsNone(fr.record("R-1", "quick", "note"))


class RowParserTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="fold-rows-")
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _file(self, name: str, body) -> Path:
        p = self.dir / name
        text = body if isinstance(body, str) else json.dumps(body)
        p.write_text(text, encoding="utf-8")
        return p

    def test_read_entry_rejections(self):
        self.assertIsNone(fr._read_entry(self.dir / "missing.json"))
        self.assertIsNone(fr._read_entry(self._file("bad.json", "{nope")))
        self.assertIsNone(fr._read_entry(self._file("list.json", [1, 2])))
        self.assertEqual(fr._read_entry(self._file("ok.json", {"a": 1})), {"a": 1})

    def test_entry_at(self):
        self.assertIsNone(fr._entry_at(None))
        self.assertIsNone(fr._entry_at({"at": "soon"}))
        self.assertEqual(fr._entry_at({}), 0)
        self.assertEqual(fr._entry_at({"at": "15"}), 15)
        self.assertEqual(fr._entry_at({"at": 9.9}), 9)

    def test_recent_row_rejections_and_projection(self):
        cutoff = 1000
        self.assertIsNone(fr._recent_row(self._file("a.json", "junk"), cutoff))
        self.assertIsNone(fr._recent_row(self._file("b.json", {"req": "R", "at": "x"}), cutoff))
        self.assertIsNone(fr._recent_row(self._file("c.json", {"req": "R", "at": 999}), cutoff))
        self.assertIsNone(fr._recent_row(self._file("d.json", {"req": "", "at": 2000}), cutoff))
        self.assertIsNone(fr._recent_row(self._file("e.json", {"at": 2000}), cutoff))
        row = fr._recent_row(self._file("stem.json", {"req": "R-9", "at": 1000,
                                                      "text": "never projected"}), cutoff)
        self.assertEqual(row, {"id": "stem", "req": "R-9", "channel": "", "at": 1000})
        row = fr._recent_row(self._file("f.json", {"id": "given", "req": "R", "channel": "c",
                                                   "at": 3000}), cutoff)
        self.assertEqual((row["id"], row["channel"]), ("given", "c"))

    def test_load_recent_unreadable_dir_is_empty(self):
        with mock.patch.object(fr, "_dir", return_value=self.dir / "nope"):
            self.assertEqual(fr.load_recent(), [])
        with mock.patch.object(Path, "glob", side_effect=OSError("denied")):
            self.assertEqual(fr.load_recent(), [])


class SweepTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="fold-sweep-")
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_unlink_if_stale(self):
        old = self.dir / "old.json"
        old.write_text("{}", encoding="utf-8")
        os.utime(old, (100, 100))
        fresh = self.dir / "fresh.json"
        fresh.write_text("{}", encoding="utf-8")
        self.assertEqual(fr._unlink_if_stale(old, 200), 1)
        self.assertFalse(old.exists())
        self.assertEqual(fr._unlink_if_stale(fresh, 0), 0)
        self.assertTrue(fresh.exists())
        self.assertEqual(fr._unlink_if_stale(self.dir / "gone.json", 10**12), 0)

    def test_sweep_counts_and_tolerates_missing_dir(self):
        for n in ("a", "b"):
            p = self.dir / f"{n}.json"
            p.write_text("{}", encoding="utf-8")
            os.utime(p, (100, 100))
        self.assertEqual(fr._sweep_stale(self.dir, now=10**6), 2)
        self.assertEqual(fr._sweep_stale(self.dir / "missing", now=10**6), 0)


if __name__ == "__main__":
    unittest.main()
