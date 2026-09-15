"""§47.5 — an iCloud-evicted (dataless) note is "not local yet", not poison.

Live case (2026-08-16/18/20): three screenpipe notes in the iCloud-hosted
vault were evicted to dataless placeholders; ``read_text`` under the cron
chain raised ``[Errno 11] Resource deadlock avoided`` (EDEADLK), the radar
charged the retry budget five times and gave up on each. Pinned here:

- dataless probe / EDEADLK on read → one ``brctl download`` nudge → re-read
  → filed normally (the materialisation is what actually cures it: re-opening
  the same placeholder in the same no-download context deadlocks again);
- the probe never vetoes the read: ``brctl download`` returns asynchronously,
  so SF_DATALESS often lingers on a file that reads fine — the nudge is
  followed by a real read attempt either way;
- a note that stays dataless is booked ``deferred``: the wider
  ``FAILED_MAX_ATTEMPTS_DEFERRED`` budget instead of the poison one, no §40
  card and ``radar_health`` stays ok while it waits, the marker advances, a
  lone deferred note is not a systemic failure (it burns its whole budget
  alone and still ends in one §40 card, never in a voided pass), the next
  pass retries it;
- the wait is bounded — a note that never comes back still gives up with the
  §40 trace the constitution (#11) requires, and health says so;
- the per-pass iCloud budget bounds both halves (the whole pass holds
  state/radar.lock): only ``DATALESS_WAIT_MAX_NOTES`` notes may sleep on the
  download, and a wedged download daemon cannot burn more than
  ``DATALESS_PASS_BUDGET_S`` on ``brctl`` calls;
- the ``brctl`` call is best-effort (missing binary swallowed) and the
  dataless probe answers False for ordinary / missing files.

The ledger re-arm gate that reopens a stuck give-up lives in
tests/test_radar_ledger_rearm.py.

No real ``brctl`` runs: the download seam is patched (unit layer, no
subprocess).
"""
import errno
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports
from tests.test_radar import BASE, RadarScanBase, _item

from act import radar
from act.lib import radar_health, registry


class _DatalessBase(RadarScanBase):
    """驱逐态是两件事，判例里也分开：``dataless`` = SF_DATALESS 位挂着，
    ``deadlocked`` = 读它就 EDEADLK。真·cron 语境下的驱逐 note 两样都占
    （``_evict``），但位挂着却读得动是常态——`brctl download` 异步返回，位
    往往还没清。"""

    def setUp(self):
        super().setUp()
        self.downloads: list = []
        self.dataless: set = set()     # SF_DATALESS 位挂着的 note
        self.deadlocked: set = set()   # 读它就 EDEADLK（cron 语境的驱逐态）
        self._patch(radar, "DATALESS_DOWNLOAD_WAIT_S", 0)
        self._patch(radar, "_brctl_download",
                    lambda note: self.downloads.append(str(note)))
        self._patch(radar, "_is_dataless", lambda note: str(note) in self.dataless)
        real_read = Path.read_text

        def read_text(this, *a, **kw):
            if str(this) in self.deadlocked:
                raise OSError(errno.EDEADLK, "Resource deadlock avoided")
            return real_read(this, *a, **kw)

        self._patch(Path, "read_text", read_text)

    def _patch(self, target, name, value):
        patcher = mock.patch.object(target, name, value)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _evict(self, note):
        """iCloud 把这篇 note 驱逐了：位挂着，而且 cron 语境下读不出来。"""
        self.dataless.add(str(note))
        self.deadlocked.add(str(note))

    def _restore(self):
        """iCloud 把文件放回来了。"""
        self.dataless.clear()
        self.deadlocked.clear()

    def _companion(self, i):
        return self._note(f"ok-{i}.md", f"ok {i}", BASE + 10 * (i + 1))

    def _queue(self):
        return radar._load_failed_queue()

    def _gave_up_cards(self):
        return [r for r in registry.load_all()
                if (r.sources or [{}])[0].get("channel") == radar.GIVE_UP_CHANNEL]

    def _obsidian_health(self):
        """radar_health 只有 cron 语境才写（_owns_health）——这几条判例要看
        账，所以自己戴上那顶帽子。key 探针也钉住：没有 key 的机器（CI）会把
        失败报成 `no_api_key`，那是 §15 的另一条法条，不是本节要钉的。"""
        os.environ["AIASSISTANT_CRON"] = "1"
        self.addCleanup(lambda: os.environ.pop("AIASSISTANT_CRON", None))
        self._patch(radar, "_has_anthropic_key", lambda: True)
        self.addCleanup(lambda: radar_health.HEALTH_PATH.exists()
                        and radar_health.HEALTH_PATH.unlink())
        return lambda: json.loads(
            radar_health.HEALTH_PATH.read_text(encoding="utf-8"))["obsidian"]


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

    def test_a_dataless_note_is_materialized_before_the_read(self):
        """生产路径：stat 报驱逐 → brctl 把文件拉回来 → 这一轮就读到了。
        （#305 只重开 open()：cron 语境下物化是关的，重开还是 EDEADLK。）"""
        note = self._note("evicted.md", "Boss: decide the venue", BASE)
        self._evict(note)
        self._patch(radar, "_brctl_download", self._downloader_that_delivers())

        summary = radar.scan(
            runner=lambda t: json.dumps([_item("Decide the venue")]))

        self.assertEqual(self.downloads, [str(note)])
        self.assertEqual(summary["extracted"], 1)
        self.assertEqual(self._queue(), {})             # 读到了，没留案底

    def _downloader_that_delivers(self):
        def download(note):
            self.downloads.append(str(note))
            self.dataless.discard(str(note))   # iCloud 把文件放回来了
            self.deadlocked.discard(str(note))
        return download

    def test_a_lingering_dataless_flag_never_vetoes_the_read(self):
        """`brctl download` 异步返回：SF_DATALESS 位常常还挂着，文件却已经
        读得动了。探针只决定催不催，**不替 read 投否决票**——旧代码在这里
        一次 read_text 都不发，把一篇读得到的 note 直接判成 deferred。"""
        note = self._note("evicted.md", "Boss: decide the venue", BASE)
        self.dataless.add(str(note))    # 位一直挂着，读却没问题

        summary = radar.scan(
            runner=lambda t: json.dumps([_item("Decide the venue")]))

        self.assertEqual(self.downloads, [str(note)])   # 催了一把
        self.assertEqual(summary["extracted"], 1)       # 也真的读了
        self.assertEqual(self._queue(), {})
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

    def test_non_utf8_semantics_unchanged(self):
        note = self.raw / "binary.md"
        note.write_bytes(b"\xff\xfe not utf8")
        os.utime(note, (BASE, BASE))
        summary = radar.scan(runner=lambda t: "[]")

        error = self._queue()[str(note)]["last_error"]
        self.assertTrue(error.startswith("unreadable note binary.md:"))
        self.assertFalse(radar._is_deferred_error(error))   # 毒 note，不是等云端
        self.assertEqual(self._queue()[str(note)]["attempts"], 1)
        self.assertEqual(self.downloads, [])
        self.assertTrue(any("unreadable note" in s for s in summary["skipped"]))


class WaitBudgetTestCase(_DatalessBase):
    def test_only_a_few_notes_per_pass_may_sleep_on_the_download(self):
        """整轮 pass 持着 state/radar.lock：等下载的 note 数有上限，超额的
        只催一把 brctl 就进 deferred，下轮再来。"""
        self._patch(radar, "DATALESS_WAIT_MAX_NOTES", 2)
        waited: list = []

        def materialize(note, waits=None):
            waited.append(radar._may_wait(waits))

        self._patch(radar, "_materialize", materialize)
        for i in range(4):
            self._evict(self._note(f"evicted-{i}.md", "content", BASE + i))

        radar.scan(runner=lambda t: "[]")

        self.assertEqual(waited, [True, True, False, False])

    def test_a_wedged_download_daemon_cannot_hold_the_pass_lock(self):
        """催 brctl 也进同一本预算：bird 卡死时每篇要烧满超时，而整轮 pass
        攥着 state/radar.lock（§17）——预算花完的 note 这轮既不催也不等，
        直接 deferred，下轮 cron 再来。只封篇数封不住这一头。"""
        clock = {"now": 0.0}
        real_budget = radar._WaitBudget
        self._patch(radar, "_WaitBudget",
                    lambda notes: real_budget(notes, seconds=20.0,
                                              clock=lambda: clock["now"]))

        def wedged(note):                       # 每篇烧满 brctl 的超时
            self.downloads.append(str(note))
            clock["now"] += radar.DATALESS_BRCTL_TIMEOUT_S

        self._patch(radar, "_brctl_download", wedged)
        for i in range(10):
            self._evict(self._note(f"evicted-{i}.md", "content", BASE + i))

        summary = radar.scan(runner=lambda t: "[]")

        self.assertEqual(len(self.downloads), 4)    # 20s / 5s，其余一把也不催
        self.assertEqual(len(self._queue()), 10)    # 但十篇都留了 deferred 案底
        self.assertEqual(sum(s.startswith(radar.DEFERRED_PREFIX)
                             for s in summary["skipped"]), 10)

    def test_the_budget_hands_out_exactly_its_allowance(self):
        budget = radar._WaitBudget(2)
        self.assertEqual([budget.take() for _ in range(4)],
                         [True, True, False, False])
        self.assertTrue(radar._may_wait(None))    # 没有预算对象时不受限
        self.assertTrue(radar._may_nudge(None))
        spent = radar._WaitBudget(5, seconds=0)   # 时间预算先用完
        self.assertTrue(spent.exhausted())
        self.assertFalse(spent.take())            # 篇数还剩，照样不给
        self.assertFalse(radar._may_nudge(spent))


class DeferredLedgerTestCase(_DatalessBase):
    def test_a_waiting_note_keeps_the_wider_budget_and_healthy_books(self):
        note = self._note("evicted.md", "content", BASE)
        self._evict(note)
        calls = []
        obsidian = self._obsidian_health()

        def runner(text):
            calls.append(text)
            return "[]"

        # 第一轮独自成 pass：deferred 不算 systemic，marker 照常前进
        summary = radar.scan(runner=runner)
        self.assertEqual(radar._read_marker(), BASE)
        self.assertTrue(any(s.startswith(radar.DEFERRED_PREFIX)
                            for s in summary["skipped"]))
        self.assertTrue(obsidian()["last_ok"])          # 等 iCloud ≠ extract_failed
        self.assertIsNone(obsidian()["skip_reason"])
        # 远超毒 note 的 5 次额度
        for i in range(radar.FAILED_MAX_ATTEMPTS + 2):
            self._companion(i)
            summary = radar.scan(runner=runner)
            self.assertEqual(summary["files_scanned"], 2, f"pass {i}")

        entry = self._queue()[str(note)]
        self.assertTrue(entry["deferred"])
        self.assertFalse(entry["gave_up"])              # 旧代码在第 5 轮判死
        self.assertGreater(entry["attempts"], radar.FAILED_MAX_ATTEMPTS)
        self.assertTrue(entry["last_error"].startswith(radar.DEFERRED_PREFIX))
        self.assertFalse(any("giving up" in s for s in summary["skipped"]))
        self.assertEqual(len(self.downloads), radar.FAILED_MAX_ATTEMPTS + 3)
        self.assertFalse(any("content" in c for c in calls))   # 没读到就不烧 claude
        self.assertEqual(self._gave_up_cards(), [])
        self.assertIsNone(obsidian()["skip_reason"])

    def test_a_note_that_never_comes_back_still_gives_up_with_a_trace(self):
        """宪法第 11 条：放弃要留痕。等 iCloud 的额度宽，但**有终点**——
        烧完了照常 gave_up + §40 卡 + health 说话。"""
        note = self._note("evicted.md", "content", BASE)
        self._evict(note)
        obsidian = self._obsidian_health()

        for i in range(radar.FAILED_MAX_ATTEMPTS_DEFERRED):
            self._companion(i)              # 部分失败 -> 不判 systemic
            summary = radar.scan(runner=lambda t: "[]")

        entry = self._queue()[str(note)]
        self.assertEqual(entry["attempts"], radar.FAILED_MAX_ATTEMPTS_DEFERRED)
        self.assertTrue(entry["gave_up"])
        self.assertTrue(any("giving up" in s for s in summary["skipped"]))
        self.assertEqual(len(self._gave_up_cards()), 1)
        self.assertEqual(obsidian()["skip_reason"], "extract_failed")

    def test_a_lone_waiting_note_gives_up_instead_of_voiding_the_pass(self):
        """夜里只有这一篇 note 的 pass：它烧完自己的额度，照常 gave_up + 一张
        §40 卡。**不许**被判 systemic——那会把 attempts 整轮回滚（`failed_before`
        还原），这篇 note 就永远到不了留痕那一步（宪法第 11 条）。
        `_is_note_level_error` 的 DEFERRED_PREFIX 分支钉在这里。"""
        note = self._note("evicted.md", "content", BASE)
        self._evict(note)                       # 全程只有这一篇，没有陪跑的

        for i in range(radar.FAILED_MAX_ATTEMPTS_DEFERRED):
            summary = radar.scan(runner=lambda t: "[]")
            self.assertEqual(summary["files_scanned"], 1, f"pass {i}")
            self.assertFalse(any("systemic extraction failure" in s
                                 for s in summary["skipped"]), f"pass {i}")

        entry = self._queue()[str(note)]
        self.assertEqual(entry["attempts"], radar.FAILED_MAX_ATTEMPTS_DEFERRED)
        self.assertTrue(entry["gave_up"])       # 账没被 systemic 回滚掉
        self.assertEqual(len(self._gave_up_cards()), 1)

    def test_the_attempt_cap_is_per_class(self):
        deferred = f"{radar.DEFERRED_PREFIX}: a.md: still dataless"
        legacy = ("unreadable note a.md: [Errno 11] Resource deadlock avoided")
        self.assertEqual(radar._max_attempts_for(deferred),
                         radar.FAILED_MAX_ATTEMPTS_DEFERRED)
        self.assertEqual(radar._max_attempts_for(legacy),
                         radar.FAILED_MAX_ATTEMPTS_DEFERRED)
        self.assertEqual(radar._max_attempts_for("unparseable extraction on a.md"),
                         radar.FAILED_MAX_ATTEMPTS)
        self.assertGreater(radar.FAILED_MAX_ATTEMPTS_DEFERRED,
                           radar.FAILED_MAX_ATTEMPTS)

    def test_a_partial_ledger_entry_never_crashes_the_pass(self):
        """台账被截断/手改成没有 attempts 的形状时也只影响这一篇 note
        （_record_failure 绝不 KeyError——模块 docstring 承诺扛得住）。"""
        note = self._note("evicted.md", "content", BASE)
        queue = {str(note): {"mtime": BASE}}
        entry = radar._record_failure(
            queue, note, BASE, f"{radar.DEFERRED_PREFIX}: evicted.md: x")
        self.assertEqual(entry["attempts"], 1)
        self.assertFalse(entry["gave_up"])

    def test_deferred_note_is_retried_and_cleared_once_local(self):
        note = self._note("evicted.md", "Boss: decide the venue", BASE)
        self._evict(note)
        radar.scan(runner=lambda t: "[]")
        self.assertIn(str(note), self._queue())

        self._restore()                                # iCloud 拉回来了
        summary = radar.scan(runner=lambda t: json.dumps([_item("Decide the venue")]))
        self.assertEqual(summary["files_scanned"], 1)
        self.assertEqual(summary["extracted"], 1)
        self.assertEqual(self._queue(), {})

    def test_a_real_failure_after_the_wait_is_capped_by_the_poison_budget(self):
        """同一条案底的 attempts 是共用的，上限按**当前**错误的类别算：
        等云端等久了的 note 一旦真坏，立刻留痕而不是再宽限 5 轮。"""
        note = self._note("evicted.md", "poison", BASE)
        self._evict(note)
        radar.scan(runner=lambda t: "[]")
        self._restore()
        self._companion(0)

        def runner(text):
            if "poison" in text:
                raise RuntimeError("permanent failure")
            return "[]"

        radar.scan(runner=runner)
        entry = self._queue()[str(note)]
        self.assertEqual(entry["attempts"], 2)
        self.assertFalse(entry["deferred"])
        self.assertFalse(entry["gave_up"])
        self.assertEqual(radar._max_attempts_for(entry["last_error"]),
                         radar.FAILED_MAX_ATTEMPTS)


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
