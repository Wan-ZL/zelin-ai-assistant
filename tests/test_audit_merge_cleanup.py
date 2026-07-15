"""cleanup_merge_jobs — stuck-'analyzing' timeout sweep (audit 2026-07-15).

An analysis subprocess killed mid-run (power loss, sleep, kill -9) leaves the
job file at status='analyzing' forever; the ONLY exit is cleanup_merge_jobs'
timeout branch (the TTL sweep deliberately skips 'analyzing'). Untested until
now — a regression left a permanent '分析中' zombie spinner on the dashboard.

Runs entirely inside the sandbox AIASSISTANT_HOME (tests/__init__.py).
"""
import datetime as _dt
import os
import unittest

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import actd, merge_review
from act.lib import config


def _iso_ago(seconds: int) -> str:
    dt = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(seconds=seconds)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_analyzing(job_id, requested_at, mtime_ago=None):
    merge_review.write_job({"id": job_id, "ids": ["R-1", "R-2"],
                            "status": "analyzing",
                            "requested_at": requested_at})
    if mtime_ago is not None:
        path = merge_review.job_path(job_id)
        ts = _dt.datetime.now().timestamp() - mtime_ago
        os.utime(path, (ts, ts))


class CleanupAnalyzingTimeoutTestCase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        merge_review.MERGE_DIR.mkdir(parents=True, exist_ok=True)
        for p in merge_review.MERGE_DIR.glob("*.json"):
            p.unlink()

    def test_stuck_analyzing_fails_after_timeout(self):
        _write_analyzing("MS-stuck1", _iso_ago(30 * 60))  # 30 min > 20 min cap
        actd.cleanup_merge_jobs()
        job = merge_review.load_job("MS-stuck1")
        self.assertEqual(job.get("status"), "failed")
        self.assertEqual(job.get("error"), "analysis timed out")
        self.assertTrue(job.get("expires_at"))  # now on the normal TTL track

    def test_fresh_analyzing_is_untouched(self):
        _write_analyzing("MS-fresh1", _iso_ago(5 * 60))
        actd.cleanup_merge_jobs()
        self.assertEqual(merge_review.load_job("MS-fresh1").get("status"),
                         "analyzing")

    def test_corrupt_requested_at_times_out_via_file_mtime(self):
        # requested_at unparseable -> the _mtime_dt fallback must still free
        # the zombie once the FILE is old enough
        _write_analyzing("MS-badts1", "not-a-timestamp", mtime_ago=30 * 60)
        actd.cleanup_merge_jobs()
        job = merge_review.load_job("MS-badts1")
        self.assertEqual(job.get("status"), "failed")
        self.assertEqual(job.get("error"), "analysis timed out")

    def test_analyzing_never_swept_by_ttl_even_with_expired_expires_at(self):
        # the TTL branch must keep skipping analyzing files — only the timeout
        # branch may end them, as a visible 'failed', never a silent unlink
        merge_review.write_job({"id": "MS-keep1", "ids": ["R-1", "R-2"],
                                "status": "analyzing",
                                "requested_at": _iso_ago(60),
                                "expires_at": _iso_ago(60)})
        removed = actd.cleanup_merge_jobs()
        self.assertEqual(removed, 0)
        self.assertIsNotNone(merge_review.load_job("MS-keep1"))


if __name__ == "__main__":
    unittest.main()
