"""Audit regression test — the radar failed-note retry ledger must be written
atomically. The pre-fix in-place ``write_text`` truncates first, so a
crash/ENOSPC mid-write destroyed the whole ledger — every queued failed note
silently dropped out of the retry gate (the radar's worst failure mode).
"""
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import radar


class FailedQueueAtomicWriteTestCase(unittest.TestCase):
    def test_failed_write_leaves_previous_ledger_intact(self):
        radar._save_failed_queue({"note-a": {"mtime": 1.0, "attempts": 2}})
        before = radar._load_failed_queue()
        self.assertIn("note-a", before)

        real_write_text = Path.write_text

        def enospc_after_truncate(self, data, *args, **kwargs):
            # ENOSPC mid-write: some bytes land, then the write fails. With
            # the pre-fix truncating in-place write this corrupts the real
            # ledger; the atomic tmp+replace must leave it untouched.
            real_write_text(self, str(data)[:5], *args, **kwargs)
            raise OSError(28, "No space left on device")

        with mock.patch.object(Path, "write_text", enospc_after_truncate):
            radar._save_failed_queue({"note-b": {"mtime": 2.0, "attempts": 1}})

        self.assertEqual(radar._load_failed_queue(), before)

    def test_successful_write_replaces_ledger(self):
        radar._save_failed_queue({"note-a": {"mtime": 1.0, "attempts": 2}})
        radar._save_failed_queue({"note-b": {"mtime": 2.0, "attempts": 1}})
        self.assertEqual(list(radar._load_failed_queue()), ["note-b"])


if __name__ == "__main__":
    unittest.main()
