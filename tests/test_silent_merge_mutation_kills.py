"""silent_merge (CONTRACT §44) — 夜间变异存活体逐个钉死（R-287，2026-09-06 夜报
act/lib/silent_merge.py run=62 killed=24 survived=38）。

每条判例的 docstring 写明它杀的是哪些变异体（file:line + operator），夜报再报
同一 site 时不用重判。判例只钉 §44 的 job 文件 / sweep / 判官派生这一层——
fold+trash 的可逆执行由 tests/test_silent_merge.py 钉。

**故意放过的等价变异体**（夜报会继续列出，不补判例）：
- ``act/lib/silent_merge.py:84`` ``json.dumps(..., indent=2)`` 的 ``2 -> 1`` /
  ``2 -> 3``：pretty-print 缩进宽度，没有任何读者解析空白（``_load_job`` 走
  ``json.loads``），纯排版常量。

零 spawn：Popen 全部注入 MagicMock；零真 registry 写：job 目录与日志目录都在
tempfile 里。
"""
import datetime as _dt
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env before act imports

from act.lib import config, silent_merge


def _iso(dt: _dt.datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


NOW = _dt.datetime(2026, 9, 6, 12, 0, 0, tzinfo=_dt.timezone.utc)


class _IsolatedJobsTestCase(unittest.TestCase):
    """SILENT_DIR / LOG_DIR 都指向一次性 tmp 目录，测试之间互不看见。"""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="sm-kills-jobs-"))
        self.logs = Path(tempfile.mkdtemp(prefix="sm-kills-logs-"))
        for target, attr, val in ((silent_merge, "SILENT_DIR", self.dir),
                                  (config, "LOG_DIR", self.logs)):
            p = mock.patch.object(target, attr, val)
            p.start()
            self.addCleanup(p.stop)

    def _job(self, name, **fields):
        path = self.dir / f"{name}.json"
        path.write_text(json.dumps(fields), encoding="utf-8")
        return path

    def _status(self, name):
        return json.loads((self.dir / f"{name}.json").read_text(encoding="utf-8"))["status"]


class JobFileTestCase(_IsolatedJobsTestCase):
    def test_write_job_creates_missing_parent_dirs(self):
        """kills const_bool@81 (mkdir parents=True -> False): a fresh
        AIASSISTANT_HOME has no state/silent_merge/ yet — the first request()
        of the daemon's life must be able to create the whole chain."""
        nested = self.dir / "state" / "deeper" / "silent_merge"
        with mock.patch.object(silent_merge, "SILENT_DIR", nested):
            silent_merge._write_job({"id": "SM-nested", "status": "pending"})
        self.assertEqual(json.loads((nested / "SM-nested.json").read_text(
            encoding="utf-8"))["status"], "pending")

    def test_job_file_keeps_chinese_readable(self):
        """kills const_bool@84 (ensure_ascii=False -> True): the judge's brief
        is Chinese prose and the job file is what the owner opens when a
        merge looks wrong — it must not be a wall of \\uXXXX escapes."""
        silent_merge._write_job({"id": "SM-cn", "status": "judged",
                                 "brief": "副卡补充了 deadline"})
        raw = (self.dir / "SM-cn.json").read_text(encoding="utf-8")
        self.assertIn("副卡补充了 deadline", raw)
        self.assertNotIn("\\u", raw)

    def test_new_job_id_shape(self):
        """kills int_minus1/int_plus1@98 (hex[:8] -> [:7] / [:9]): the id is the
        job file stem AND the per-job log name (config.LOG_DIR/<id>.log) —
        its width is part of the on-disk contract."""
        pat = re.compile(r"^SM-[0-9a-f]{8}$")
        ids = {silent_merge.new_job_id() for _ in range(32)}
        self.assertEqual(len(ids), 32)
        for sid in ids:
            self.assertRegex(sid, pat)


class PendingCountTestCase(_IsolatedJobsTestCase):
    def test_empty_dir_is_zero(self):
        """kills int_minus1/int_plus1@103 (n = 0 -> -1 / 1) and
        return_none@114: an empty budget must read as exactly 0."""
        self.assertEqual(silent_merge.pending_count(), 0)

    def test_counts_only_pending_jobs(self):
        """kills cmp_eq@107 (== "pending" -> !=) and int_minus1/int_plus1@109
        (n += 1 -> += 0 / += 2): 2 pending among 3 done + 1 judged = 2."""
        self._job("SM-p1", status="pending")
        self._job("SM-p2", status="pending")
        for i in range(3):
            self._job(f"SM-d{i}", status="done")
        self._job("SM-j", status="judged")
        self.assertEqual(silent_merge.pending_count(), 2)

    def test_corrupt_file_does_not_stop_the_count(self):
        """kills loop_flow@111 (continue -> break): a corrupt job file that
        happens to sort first must not hide the pending jobs behind it (the
        throttle would then think the budget is free)."""
        bad = self._job("SM-bad", status="pending")
        bad.write_text("{nope", encoding="utf-8")
        good = [self._job("SM-g1", status="pending"),
                self._job("SM-g2", status="pending")]
        with mock.patch.object(silent_merge.Path, "glob",
                               return_value=[bad] + good):
            self.assertEqual(silent_merge.pending_count(), 2)


class RequestSpawnTestCase(_IsolatedJobsTestCase):
    def test_judge_is_spawned_detached(self):
        """kills const_bool@144 (start_new_session=True -> False): the judge
        must live in its own session — the 10 s daemon pass never waits on
        an LLM and a daemon restart must not take the judge down with it."""
        popen = mock.MagicMock()
        with mock.patch.object(silent_merge.subprocess, "Popen", popen):
            sid = silent_merge.request("R-001", "R-002")
        self.assertEqual(self._status(sid), "pending")
        self.assertEqual(popen.call_count, 1)
        argv = popen.call_args.args[0]
        kwargs = popen.call_args.kwargs
        self.assertEqual(argv, [sys.executable, "-m", "act.lib.silent_merge", sid])
        self.assertIs(kwargs["start_new_session"], True)
        self.assertIs(kwargs["stdin"], subprocess.DEVNULL)
        self.assertEqual(kwargs["cwd"], str(config.HOME))


class UnlinkQuietTestCase(_IsolatedJobsTestCase):
    def test_one_when_removed_zero_when_missing(self):
        """kills int_minus1/int_plus1@166 (return 1 -> 0 / 2),
        int_minus1/int_plus1@168 (return 0 -> -1 / 1) and return_none@168:
        sweep() sums these — anything but exact 0/1 corrupts its count."""
        p = self._job("SM-x", status="done")
        self.assertEqual(silent_merge._unlink_quiet(p), 1)
        self.assertFalse(p.exists())
        self.assertEqual(silent_merge._unlink_quiet(p), 0)


class SweepBoundaryTestCase(_IsolatedJobsTestCase):
    def test_corrupt_job_file_is_removed_and_counted(self):
        """kills return_none@205 (return _unlink_quiet(p) -> None): a corrupt
        job file is dropped and counts as exactly one removal — sweep() sums
        the per-file results, so None there is a TypeError in the daemon."""
        bad = self.dir / "SM-bad.json"
        bad.write_text("{nope", encoding="utf-8")
        self.assertEqual(silent_merge._sweep_one(bad, NOW), 1)
        self.assertFalse(bad.exists())

    def test_pending_timeout_is_strictly_more_than_twenty_minutes(self):
        """kills int_minus1/int_plus1@64 (PENDING_TIMEOUT_MIN 20 -> 19 / 21)
        and cmp_gt@180 (> -> >=): exactly 20 min is still waiting for the
        judge; 20 min 30 s is stuck and gets failed."""
        self._job("SM-edge", id="SM-edge", status="pending",
                  requested_at=_iso(NOW - _dt.timedelta(minutes=20)))
        self._job("SM-over", id="SM-over", status="pending",
                  requested_at=_iso(NOW - _dt.timedelta(minutes=20, seconds=30)))
        self.assertEqual(silent_merge.sweep(NOW), 0)   # failing is not removing
        self.assertEqual(self._status("SM-edge"), "pending")
        self.assertEqual(self._status("SM-over"), "failed")

    def test_ttl_is_strictly_more_than_twenty_four_hours(self):
        """kills int_minus1/int_plus1@65 (TTL_HOURS 24 -> 23 / 25),
        cmp_gt@184 (> -> >=) and int_minus1/int_plus1@184 (60 -> 59 / 61):
        a done job finished exactly 24 h ago stays; 24 h 1 min is purged."""
        keep = self._job("SM-keep", id="SM-keep", status="done",
                         finished_at=_iso(NOW - _dt.timedelta(hours=24)))
        gone = self._job("SM-gone", id="SM-gone", status="failed",
                         finished_at=_iso(NOW - _dt.timedelta(hours=24, minutes=1)))
        self.assertEqual(silent_merge.sweep(NOW), 1)
        self.assertTrue(keep.exists())
        self.assertFalse(gone.exists())

    def test_only_terminal_statuses_expire(self):
        """kills bool_and@184 (status in (done, failed) AND age -> OR): a
        'judged' job actd has not consumed yet is never purged however old
        (it carries an unexecuted verdict), and a fresh done job is kept."""
        judged = self._job("SM-judged", id="SM-judged", status="judged",
                           finished_at=_iso(NOW - _dt.timedelta(hours=30)))
        fresh = self._job("SM-fresh", id="SM-fresh", status="done",
                          finished_at=_iso(NOW - _dt.timedelta(hours=1)))
        self.assertEqual(silent_merge.sweep(NOW), 0)
        self.assertTrue(judged.exists())
        self.assertTrue(fresh.exists())
        self.assertEqual(self._status("SM-judged"), "judged")

    def test_stuck_job_without_id_is_failed_under_its_file_stem(self):
        """kills bool_or@197 (job.get("id") OR p.stem -> AND): a job record
        missing its id falls back to the file stem, so the timeout verdict
        lands in the same file — never in a stray None.json."""
        self._job("SM-noid", status="pending",
                  requested_at=_iso(NOW - _dt.timedelta(minutes=45)))
        self.assertEqual(silent_merge.sweep(NOW), 0)
        self.assertEqual(self._status("SM-noid"), "failed")
        self.assertEqual(sorted(q.name for q in self.dir.iterdir()), ["SM-noid.json"])

    def test_sweep_one_returns_exact_zero_for_kept_jobs(self):
        """kills int_minus1/int_plus1/return_none@208 (undated -> 0) and
        int_minus1/int_plus1@213 (kept -> 0): sweep() sums per-file results,
        so a kept job must contribute exactly 0."""
        undated = self._job("SM-undated", id="SM-undated", status="pending")
        fresh = self._job("SM-fresh", id="SM-fresh", status="pending",
                          requested_at=_iso(NOW - _dt.timedelta(minutes=1)))
        self.assertEqual(silent_merge._sweep_one(undated, NOW), 0)
        self.assertEqual(silent_merge._sweep_one(fresh, NOW), 0)
        self.assertEqual(silent_merge.sweep(NOW), 0)

    def test_unlistable_dir_sweeps_exactly_zero(self):
        """kills int_minus1/int_plus1/return_none@223: an unreadable job dir
        is 'nothing removed' = 0, never -1 / 1 / None."""
        with mock.patch.object(silent_merge.Path, "glob", side_effect=OSError("io")):
            self.assertEqual(silent_merge.sweep(NOW), 0)


if __name__ == "__main__":
    unittest.main()
