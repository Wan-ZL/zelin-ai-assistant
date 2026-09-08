"""§47.5 — an iCloud-evicted (dataless) note is "not local yet", not poison.

Live case (2026-08-16/18/20): three screenpipe notes in the iCloud-hosted
vault were evicted to dataless placeholders; ``read_text`` under the cron
chain raised ``[Errno 11] Resource deadlock avoided`` (EDEADLK), the radar
charged the retry budget five times and gave up on each. Pinned here:

- EDEADLK on read → one ``brctl download`` nudge → re-read → filed normally;
- a note that stays dataless is booked ``deferred`` (attempts untouched,
  never ``gave_up``, no §40 card), the marker still advances, a lone
  deferred note is not a systemic failure, and the next pass retries it;
- legacy gave_up entries carrying the EDEADLK text are migrated to deferred
  on load so they get retried and cleared;
- the ``brctl`` call is best-effort (missing binary swallowed) and the
  dataless probe answers False for ordinary / missing files.

No real ``brctl`` runs: the download seam is patched (unit layer, no
subprocess).
"""
import errno
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports
from tests.test_radar import BASE, RadarScanBase, _item

from act import radar
from act.lib import registry


class _DatalessBase(RadarScanBase):
    def setUp(self):
        super().setUp()
        self.downloads: list = []
        self.dataless: set = set()   # note paths currently "evicted"
        self._patch(radar, "DATALESS_DOWNLOAD_WAIT_S", 0)
        self._patch(radar, "_brctl_download",
                    lambda note: self.downloads.append(str(note)))
        self._patch(radar, "_is_dataless", lambda note: str(note) in self.dataless)

    def _patch(self, target, name, value):
        patcher = mock.patch.object(target, name, value)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _companion(self, i):
        return self._note(f"ok-{i}.md", f"ok {i}", BASE + 10 * (i + 1))

    def _queue(self):
        return radar._load_failed_queue()


class DeadlockReadTestCase(_DatalessBase):
    def test_edeadlk_read_is_nudged_then_filed_normally(self):
        note = self._note("evicted.md", "Boss: ship the report by July 20", BASE)
        real = Path.read_text
        state = {"failed": False}

        def read_text(self, *a, **kw):
            if self.name == "evicted.md" and not state["failed"]:
                state["failed"] = True
                raise OSError(errno.EDEADLK, "Resource deadlock avoided")
            return real(self, *a, **kw)

        self._patch(Path, "read_text", read_text)
        runner = lambda text: json.dumps(  # noqa: E731
            [_item("Ship the report", hardness="hard", deadline="2026-07-20")])
        summary = radar.scan(runner=runner)

        self.assertEqual(self.downloads, [str(note)])   # nudged exactly once
        self.assertEqual(summary["cards"], 1)
        self.assertEqual(self._queue(), {})
        self.assertEqual(radar._read_marker(), BASE)
        self.assertFalse(any(radar.DEFERRED_PREFIX in s for s in summary["skipped"]))

    def test_edeadlk_that_persists_after_the_nudge_is_deferred(self):
        note = self._note("evicted.md", "content", BASE)

        def read_text(self, *a, **kw):
            raise OSError(errno.EDEADLK, "Resource deadlock avoided")

        self._patch(Path, "read_text", read_text)  # every read deadlocks
        with self.assertRaises(radar._NotLocalYet):
            radar._read_note_text(note)
        self.assertEqual(self.downloads, [str(note)])

    def test_other_oserrors_still_surface_as_unreadable(self):
        note = self._note("locked.md", "content", BASE)

        def read_text(self, *a, **kw):
            raise OSError(errno.EACCES, "Permission denied")

        self._patch(Path, "read_text", read_text)
        with self.assertRaises(OSError) as ctx:
            radar._read_note_text(note)
        self.assertEqual(ctx.exception.errno, errno.EACCES)
        self.assertEqual(self.downloads, [])            # no pointless nudge


class DeferredLedgerTestCase(_DatalessBase):
    def test_note_that_stays_dataless_never_burns_budget_or_gives_up(self):
        note = self._note("evicted.md", "content", BASE)
        self.dataless.add(str(note))
        calls = []

        def runner(text):
            calls.append(text)
            return "[]"

        # 第一轮独自成 pass：deferred 不算 systemic，marker 照常前进
        summary = radar.scan(runner=runner)
        self.assertEqual(radar._read_marker(), BASE)
        self.assertTrue(any(s.startswith(radar.DEFERRED_PREFIX)
                            for s in summary["skipped"]))
        # 远超单 note 额度的轮数
        for i in range(radar.FAILED_MAX_ATTEMPTS + 2):
            self._companion(i)
            summary = radar.scan(runner=runner)
            self.assertEqual(summary["files_scanned"], 2, f"pass {i}")

        entry = self._queue()[str(note)]
        self.assertTrue(entry["deferred"])
        self.assertFalse(entry["gave_up"])
        self.assertEqual(entry["attempts"], 0)
        self.assertTrue(entry["last_error"].startswith(radar.DEFERRED_PREFIX))
        self.assertFalse(any("giving up" in s for s in summary["skipped"]))
        self.assertEqual(len(self.downloads), radar.FAILED_MAX_ATTEMPTS + 3)
        self.assertFalse(any("content" in c for c in calls))   # 没读到就不烧 claude
        # 没有 §40 放弃诊断卡
        self.assertFalse(any(
            (r.sources or [{}])[0].get("channel") == radar.GIVE_UP_CHANNEL
            for r in registry.load_all()))

    def test_deferred_note_is_retried_and_cleared_once_local(self):
        note = self._note("evicted.md", "Boss: decide the venue", BASE)
        self.dataless.add(str(note))
        radar.scan(runner=lambda t: "[]")
        self.assertIn(str(note), self._queue())

        self.dataless.clear()                          # iCloud 拉回来了
        summary = radar.scan(runner=lambda t: json.dumps([_item("Decide the venue")]))
        self.assertEqual(summary["files_scanned"], 1)
        self.assertEqual(summary["extracted"], 1)
        self.assertEqual(self._queue(), {})

    def test_deferred_then_real_failure_charges_budget_from_zero(self):
        note = self._note("evicted.md", "poison", BASE)
        self.dataless.add(str(note))
        radar.scan(runner=lambda t: "[]")
        self.dataless.clear()
        self._companion(0)

        def runner(text):
            if "poison" in text:
                raise RuntimeError("permanent failure")
            return "[]"

        radar.scan(runner=runner)
        entry = self._queue()[str(note)]
        self.assertEqual(entry["attempts"], 1)
        self.assertFalse(entry["deferred"])
        self.assertFalse(entry["gave_up"])


class LegacyMigrationTestCase(_DatalessBase):
    def test_legacy_edeadlk_gave_up_entry_is_reopened_and_cleared(self):
        # 老代码留下的案底形状（live radar_failed.json 2026-09-08 原样）
        note = self._note("2026-08-16-screenpipe-1252.md", "Boss: fix the map", BASE)
        radar._write_marker(BASE + 100)               # 早已扫过它
        radar._save_failed_queue({str(note): {
            "mtime": BASE, "attempts": 5, "gave_up": True,
            "last_error": ("unreadable note 2026-08-16-screenpipe-1252.md: "
                           "[Errno 11] Resource deadlock avoided")}})

        loaded = radar._load_failed_queue()[str(note)]
        self.assertTrue(loaded["deferred"])
        self.assertFalse(loaded["gave_up"])
        self.assertEqual(loaded["attempts"], 0)

        summary = radar.scan(runner=lambda t: json.dumps([_item("Fix the map")]))
        self.assertEqual(summary["files_scanned"], 1)   # 重新排上队
        self.assertEqual(self._queue(), {})              # 读到了 → 销案

    def test_unrelated_gave_up_entries_are_left_alone(self):
        radar._save_failed_queue({"gmail:uid:7": {
            "mtime": 7.0, "attempts": 4, "gave_up": True,
            "last_error": "poison message (unparseable headers)"}})
        entry = radar._load_failed_queue()["gmail:uid:7"]
        self.assertTrue(entry["gave_up"])
        self.assertNotIn("deferred", entry)


class ProbeAndNudgeTestCase(unittest.TestCase):
    def test_dataless_probe_is_false_for_plain_and_missing_files(self):
        with tempfile.TemporaryDirectory() as d:
            plain = Path(d) / "a.md"
            plain.write_text("x", encoding="utf-8")
            self.assertFalse(radar._is_dataless(plain))
            self.assertFalse(radar._is_dataless(Path(d) / "missing.md"))

    def test_dataless_probe_reads_the_sf_dataless_bit(self):
        st = mock.Mock(st_flags=radar.SF_DATALESS | 0x40)
        with mock.patch.object(Path, "stat", lambda self: st):
            self.assertTrue(radar._is_dataless(Path("/x.md")))

    def test_brctl_nudge_swallows_a_missing_binary_and_timeouts(self):
        for exc in (FileNotFoundError("brctl"),
                    subprocess.TimeoutExpired("brctl", 30)):
            with mock.patch.object(radar.subprocess, "run", side_effect=exc) as run:
                radar._brctl_download(Path("/x.md"))   # must not raise
                self.assertEqual(run.call_args[0][0][:2], ["brctl", "download"])


if __name__ == "__main__":
    unittest.main()
