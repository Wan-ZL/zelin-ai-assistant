"""Ingest shell chain audit fixes — fail-closed export markers & PID locks.

Runs the REAL ingest scripts against a sandboxed HOME (fake screenpipe
sqlite db, fake vault) + sandboxed AIASSISTANT_HOME, the same way cron does.

Covered audit findings:
  - export markers must come from the rows actually exported, never a fresh
    MAX(id) (rows arriving during the write loop stayed covered-but-lost);
  - markers must NOT advance when writing the dump file failed;
  - screenpipe-export.sh double-run lock (exit 3, lock kept for the holder);
  - vault_sync_processing_live must honor every PID line in the lock (script
    + headless-claude child), so an orphaned claude still holds the guard.

Hermeticity: all writes land in per-test tmp dirs (fake HOME + fake
AIASSISTANT_HOME) in BOTH vault-sync modes — direct (no helper / vault
unreadable, the CI path) and mirror (a machine with the app installed while
the real chain is mid-round: the pull early-returns and the mirror is the
tmp AIASSISTANT_HOME's). Assertions are mode-agnostic. The one global the
script touches is its own /tmp lock; tests skip when a real export holds it.
"""
import os
import shutil
import sqlite3
import subprocess
import tempfile
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
EXPORT_SH = REPO_ROOT / "ingest" / "screenpipe-export.sh"
VAULT_SYNC_SH = REPO_ROOT / "ingest" / "vault-sync.sh"
EXPORT_LOCK = Path("/tmp/screenpipe-export.lock")


class ExportScriptTestCase(unittest.TestCase):
    def setUp(self):
        if shutil.which("sqlite3") is None:
            self.skipTest("sqlite3 CLI not available")
        if EXPORT_LOCK.exists():
            self.skipTest("a real screenpipe export is running on this machine")
        self.tmp = tempfile.TemporaryDirectory(prefix="ingest-audit-")
        base = Path(self.tmp.name)
        self.fake_home = base / "home"
        self.ai_home = base / "aihome"
        self.fake_home.mkdir()
        self.ai_home.mkdir()
        # Shell fallback and the config-layer default both resolve to
        # $HOME/Documents/Obsidian Vault (drift-guarded by test_config_cli).
        self.vault = self.fake_home / "Documents" / "Obsidian Vault"
        self.inbox = self.vault / "1 - unprocessed"
        # mirror-mode inbox (the pull early-returns to this when the real
        # chain is mid-round on a machine with the app installed)
        self.mirror_inbox = self.ai_home / "state" / "vault-mirror" / "1 - unprocessed"
        self.markers = self.fake_home / ".screenpipe" / "export_markers"
        self._make_db()

    def tearDown(self):
        self.tmp.cleanup()

    def _make_db(self):
        db_dir = self.fake_home / ".screenpipe"
        db_dir.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(db_dir / "db.sqlite")
        con.executescript(
            """
            CREATE TABLE frames (
                id INTEGER PRIMARY KEY, timestamp TEXT,
                app_name TEXT, window_name TEXT, full_text TEXT);
            CREATE TABLE audio_chunks (id INTEGER PRIMARY KEY, timestamp TEXT);
            CREATE TABLE audio_transcriptions (
                id INTEGER PRIMARY KEY, audio_chunk_id INTEGER,
                transcription TEXT, device TEXT);
            """
        )
        ts = "2026-07-15T10:00:00.000Z"
        con.executemany(
            "INSERT INTO frames VALUES (?, ?, ?, ?, ?)",
            [
                (1, ts, "Safari", "Docs", "frame-text-one"),
                (2, ts, "Safari", "Docs", "frame-text-two"),
                (3, ts, "Safari", "Docs", "frame-text-three"),
                # excluded by the sensitive-app filter, id ABOVE all exported
                # rows: the old post-write MAX(id) marker (no exclusion
                # filter) would jump to 9.
                (9, ts, "1Password 8", "Vault", "secret-vault-text"),
            ],
        )
        con.executemany("INSERT INTO audio_chunks VALUES (?, ?)", [(1, ts), (2, ts)])
        con.executemany(
            "INSERT INTO audio_transcriptions VALUES (?, ?, ?, ?)",
            [
                (1, 1, "hello line one\nhello line two", "mic"),
                (2, 2, "goodbye", "mic"),
                # no matching chunk → never exported by the JOIN; the old
                # unfiltered MAX(id) marker would jump to 7 and bury it.
                (7, None, "orphan transcription", "mic"),
            ],
        )
        con.commit()
        con.close()

    def _run_export(self):
        env = dict(
            os.environ,
            HOME=str(self.fake_home),
            AIASSISTANT_HOME=str(self.ai_home),
        )
        env.pop("AIASSISTANT_CRON", None)
        return subprocess.run(
            ["bash", str(EXPORT_SH)],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )

    def _marker(self, name):
        p = self.markers / name
        return p.read_text().strip() if p.exists() else None

    def _dumps(self):
        # whichever inbox the mode decision picked, both are ours. Dedupe by
        # basename: in mirror mode the SAME dump legitimately exists in both
        # the mirror and (after the immediate/retried push) the vault inbox —
        # one logical export, two rsync'd copies.
        seen = {}
        for d in (self.inbox, self.mirror_inbox):
            if d.is_dir():
                for p in d.glob("screenpipe_*.md"):
                    seen.setdefault(p.name, p)
        return list(seen.values())

    def test_markers_come_from_exported_rows_not_max_id(self):
        proc = self._run_export()
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        # markers = last id actually written to the dump, NOT MAX(id): the
        # excluded frame (9) and the unjoinable transcription (7) stay ABOVE
        # the markers instead of being covered without ever being exported.
        self.assertEqual(self._marker("last_frame_id"), "3")
        self.assertEqual(self._marker("last_audio_id"), "2")
        dumps = self._dumps()
        self.assertEqual(len(dumps), 1, proc.stdout)
        body = dumps[0].read_text()
        for expected in ("frame-text-one", "frame-text-three", "goodbye"):
            self.assertIn(expected, body)
        # newline-flattened transcription parses as ONE record
        self.assertIn("hello line one hello line two", body)
        self.assertNotIn("secret-vault-text", body)

    def test_second_run_exports_nothing_new(self):
        first = self._run_export()
        self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
        second = self._run_export()
        # rows 9 (excluded) and 7 (unjoinable) sit above the markers but must
        # neither produce a dump nor move the markers.
        self.assertEqual(second.returncode, 0, second.stderr + second.stdout)
        self.assertIn("No new data", second.stdout)
        self.assertEqual(len(self._dumps()), 1)
        self.assertEqual(self._marker("last_frame_id"), "3")
        self.assertEqual(self._marker("last_audio_id"), "2")

    def test_write_failure_keeps_markers_and_fails_the_chain(self):
        # OUT_DIR exists as a regular FILE → the dump redirect cannot open
        # (the same failure shape as a missing FDA grant / ENOSPC). The old
        # code advanced the markers anyway and exited 0 — the whole pending
        # window silently lost while reporting success. Plant the file at
        # BOTH candidate OUT_DIRs so the failure fires in either sync mode
        # (a mirror pull rsyncs the vault-side file into the mirror anyway).
        self.vault.mkdir(parents=True)
        self.inbox.write_text("placeholder\n")
        self.mirror_inbox.parent.mkdir(parents=True)
        self.mirror_inbox.write_text("placeholder\n")
        proc = self._run_export()
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("markers not advanced", proc.stderr)
        self.assertIsNone(self._marker("last_frame_id"))
        self.assertIsNone(self._marker("last_audio_id"))
        self.assertEqual(self.inbox.read_text(), "placeholder\n")

    def test_export_lock_holder_alive_second_run_exits_3(self):
        # dummy holder whose command line matches the liveness grep (the
        # trailing `true` stops bash from exec-replacing itself with sleep,
        # which would drop the marker argv from the command line)
        holder = subprocess.Popen(["bash", "-c", "sleep 60; true", "screenpipe-export-dummy"])
        try:
            EXPORT_LOCK.write_text(f"{holder.pid}\n")
            proc = self._run_export()
            self.assertEqual(proc.returncode, 3, proc.stderr + proc.stdout)
            self.assertIn("already running", proc.stdout)
            # the skipped run must NOT steal or clear the live holder's lock
            self.assertEqual(EXPORT_LOCK.read_text(), f"{holder.pid}\n")
            self.assertEqual(len(self._dumps()), 0)
        finally:
            holder.kill()
            holder.wait()
            if EXPORT_LOCK.exists() and EXPORT_LOCK.read_text().strip() == str(holder.pid):
                EXPORT_LOCK.unlink()


class ProcessingLivenessTestCase(unittest.TestCase):
    """vault_sync_processing_live over the multi-PID lock format."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="ingest-lock-")
        self.lock = Path(self.tmp.name) / "lock"

    def tearDown(self):
        self.tmp.cleanup()

    def _live(self):
        proc = subprocess.run(
            [
                "bash",
                "-c",
                '. "$1"; VAULT_SYNC_PROCESS_LOCK="$2"; vault_sync_processing_live',
                "bash",
                str(VAULT_SYNC_SH),
                str(self.lock),
            ],
            env=dict(os.environ, AIASSISTANT_HOME=self.tmp.name),
            capture_output=True,
            text=True,
        )
        return proc.returncode == 0

    def _dead_pid(self):
        p = subprocess.Popen(["bash", "-c", "exit 0"])
        p.wait()
        return p.pid

    @unittest.skipIf(sys.platform.startswith("win"),
                     "POSIX ps -p liveness semantics (git-bash ps differs)")
    def test_live_claude_child_on_second_line_holds_the_lock(self):
        # killed parent (dead pid, line 1) + orphaned claude child whose
        # command line carries the skill path (line 2) → still live. The
        # trailing `true` stops bash from exec-replacing itself with sleep.
        child = subprocess.Popen(["bash", "-c", "sleep 60; true", "unprocessed-ingest-dummy"])
        try:
            self.lock.write_text(f"{self._dead_pid()}\n{child.pid}\n")
            self.assertTrue(self._live())
        finally:
            child.kill()
            child.wait()

    def test_all_holders_dead_means_not_live(self):
        self.lock.write_text(f"{self._dead_pid()}\n{self._dead_pid()}\n")
        self.assertFalse(self._live())

    def test_alive_but_unrelated_pid_means_not_live(self):
        # our own pytest process is alive but its command line is not the
        # ingest chain — PID reuse must not count as a live holder.
        self.lock.write_text(f"{os.getpid()}\n")
        self.assertFalse(self._live())

    def test_missing_or_pidless_lock_means_not_live(self):
        self.assertFalse(self._live())
        self.lock.write_text("legacy lock, no pid\n")
        self.assertFalse(self._live())


if __name__ == "__main__":
    unittest.main()
