"""server/screenpipe_disk.py — 录制数据磁盘占用快照（CONTRACT §71.1；路由 §49 ``GET /api/screenpipe/disk``；issue #28）。

判例钉住：GET 路径永不扫目录 / 开 sqlite（首次回 computing 空壳、后台算完才 ready；同一时刻最多一个后台算；
``?refresh=1`` 只在没在算时再起一个）；扫描按类归并（db / backup / log / media / other）且不跟符号链接；db 只读问
freelist 与首末 frame，坏库进 ``db_error`` 不炸；增长估算样本优先（≥ 1 天跨度）、全程平均兜底、都没有 = null 并说明；
样本文件带帽；``retention_days`` 走目录 effective、``last_prune`` 原样投影；回执文件名与 act 侧逐字镜像。
真 server 随机端口（tests/test_server_common.py）；小目录 + 小 sqlite 全在临时目录里。
"""
import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first
from tests.test_server_common import auth_headers, get_json, http_request, start_server

from act.lib import screenpipe_retention as act_ret
from server import paths
from server import screenpipe_disk as disk

NOW = 1_800_000_000.0
DAY = 86400.0


def make_root(root: Path, *, db_rows: int = 3) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "data").mkdir()
    (root / "data" / "a.mp4").write_bytes(b"m" * 1000)
    (root / "engine.log").write_bytes(b"l" * 300)
    (root / "db.sqlite.bak-20260604").write_bytes(b"b" * 5000)
    (root / "notes.txt").write_bytes(b"o" * 10)
    conn = sqlite3.connect(str(root / "db.sqlite"))
    conn.execute("CREATE TABLE frames (id INTEGER PRIMARY KEY, timestamp TIMESTAMP NOT NULL)")
    for i in range(db_rows):
        conn.execute("INSERT INTO frames(timestamp) VALUES (?)", ("2026-01-%02dT00:00:00.000000+00:00" % (i + 1),))
    conn.commit()
    conn.close()


class ScanTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-disk-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / ".screenpipe"
        make_root(self.root)

    def test_classify(self):
        self.assertEqual(disk.classify("", "db.sqlite"), "db")
        self.assertEqual(disk.classify("", "db.sqlite-wal"), "db")
        self.assertEqual(disk.classify("", "db.sqlite.bak-20260604"), "backup")
        self.assertEqual(disk.classify("", "old.bak"), "backup")
        self.assertEqual(disk.classify("", "screenpipe.2026-04-16.0.log"), "log")
        self.assertEqual(disk.classify("data/2026", "x.mp4"), "media")
        self.assertEqual(disk.classify("pipes", "y.json"), "other")

    def test_scan_sums_by_kind_and_skips_symlinks(self):
        (self.root / "link.log").symlink_to(self.root / "engine.log")
        out = disk.scan(self.root)
        sizes = out["sizes"]
        self.assertEqual(sizes["media"], 1000)
        self.assertEqual(sizes["log"], 300)       # 符号链接不计
        self.assertEqual(sizes["backup"], 5000)
        self.assertEqual(sizes["other"], 10)
        self.assertGreater(sizes["db"], 0)
        self.assertEqual(out["total_bytes"], sum(sizes.values()))
        self.assertEqual(out["backups"], [{"name": "db.sqlite.bak-20260604", "bytes": 5000}])
        self.assertEqual(out["file_count"], 5)

    def test_db_stats_reads_freelist_and_frame_span(self):
        out = disk.db_stats(self.root / "db.sqlite")
        self.assertIsNone(out["db_error"])
        self.assertEqual(out["oldest_frame_ts"], "2026-01-01T00:00:00.000000+00:00")
        self.assertEqual(out["newest_frame_ts"], "2026-01-03T00:00:00.000000+00:00")
        self.assertIsInstance(out["db_reclaimable_bytes"], int)

    def test_db_stats_is_honest_about_missing_or_broken_db(self):
        self.assertEqual(disk.db_stats(self.root / "nope.sqlite")["db_error"], "no_db")
        bad = self.root / "bad.sqlite"
        bad.write_text("not a db", encoding="utf-8")
        out = disk.db_stats(bad)
        self.assertIsNotNone(out["db_error"])
        self.assertIsNone(out["db_reclaimable_bytes"])


class EstimateTestCase(unittest.TestCase):
    def test_samples_slope_wins_when_span_is_at_least_a_day(self):
        samples = [[NOW - 2 * DAY, 1_000], [NOW - DAY, 1_500], [NOW, 2_000]]
        out = disk.estimate(samples, NOW, db_bytes=10, oldest_ts="2020-01-01T00:00:00+00:00")
        self.assertEqual(out["basis"], "samples")
        self.assertEqual(out["bytes_per_month"], 15_000)    # 1000 B / 2 d × 30 d
        self.assertEqual(out["span_days"], 2.0)
        self.assertEqual(out["samples"], 3)

    def test_short_span_falls_back_to_lifetime_average(self):
        samples = [[NOW - 3600, 1_000], [NOW, 2_000]]
        oldest = "2026-01-01T00:00:00.000000+00:00"
        import datetime as dt
        since = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc).timestamp()
        now = since + 10 * DAY
        out = disk.estimate(samples, now, db_bytes=3_000, oldest_ts=oldest)
        self.assertEqual(out["basis"], "lifetime")
        self.assertEqual(out["bytes_per_month"], 9_000)      # 3000 B / 10 d × 30 d
        self.assertEqual(out["span_days"], 10.0)

    def test_nothing_to_go_on_is_null_not_zero(self):
        out = disk.estimate([], NOW, db_bytes=0, oldest_ts=None)
        self.assertEqual(out, {"bytes_per_month": None, "basis": None, "span_days": None, "samples": 0})
        out = disk.estimate([[NOW, 5]], NOW, db_bytes=5, oldest_ts="garbage")
        self.assertIsNone(out["bytes_per_month"])

    def test_old_samples_fall_out_of_the_window(self):
        samples = [[NOW - 60 * DAY, 0], [NOW - 40 * DAY, 100], [NOW, 100]]
        out = disk.estimate(samples, NOW, db_bytes=0, oldest_ts=None)
        self.assertEqual(out["samples"], 1)
        self.assertIsNone(out["bytes_per_month"])

    def test_append_sample_gap_and_cap(self):
        samples = disk.append_sample([], NOW, 10)
        self.assertEqual(samples, [[NOW, 10]])
        self.assertIs(disk.append_sample(samples, NOW + 60, 20), samples)   # 间隔不足不记
        grown = disk.append_sample(samples, NOW + disk.SAMPLE_MIN_GAP_S, 20)
        self.assertEqual(len(grown), 2)
        many = [[NOW - (disk.SAMPLE_CAP + 5 - i) * DAY, i] for i in range(disk.SAMPLE_CAP + 5)]
        capped = disk.append_sample(many, NOW, 1)
        self.assertEqual(len(capped), disk.SAMPLE_CAP)
        self.assertEqual(capped[-1], [NOW, 1])

    def test_samples_file_round_trip_tolerates_garbage(self):
        with tempfile.TemporaryDirectory(prefix="zai-disk-s-") as tmp:
            p = Path(tmp) / "state" / "s.json"
            disk.save_samples(p, [[1.0, 2], [3.0, 4]])
            self.assertEqual(disk.load_samples(p), [[1.0, 2], [3.0, 4]])
            p.write_text('{"not": "a list"}', encoding="utf-8")
            self.assertEqual(disk.load_samples(p), [])
            p.write_text("[[1, 2], [3], \"x\", [5, 6]]", encoding="utf-8")
            self.assertEqual(disk.load_samples(p), [[1.0, 2], [5.0, 6]])
            self.assertEqual(disk.load_samples(p.parent / "missing.json"), [])


class SnapshotTestCase(unittest.TestCase):
    """snapshot()：GET 路径零扫描；后台 job 注入缝 ``spawn``。"""

    def setUp(self):
        disk.reset_cache_for_tests()
        self.addCleanup(disk.reset_cache_for_tests)
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-disk-snap-")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        (self.home / "state").mkdir(parents=True)
        self.root = Path(self.tmp.name) / ".screenpipe"
        make_root(self.root)
        patcher = mock.patch.object(paths, "screenpipe_dir", return_value=self.root)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_first_call_is_a_computing_placeholder_and_schedules_exactly_one_job(self):
        jobs = []
        with mock.patch.object(disk, "scan", side_effect=AssertionError("GET path must not scan")):
            first = disk.snapshot(self.home, now=NOW, spawn=jobs.append)
            second = disk.snapshot(self.home, now=NOW + 1, spawn=jobs.append)
        self.assertEqual(first["state"], "computing")
        self.assertTrue(first["refreshing"])
        self.assertIsNone(first["total_bytes"])
        self.assertEqual(first["retention_days"], 0)
        self.assertIsNone(first["last_prune"])
        self.assertEqual(second["state"], "computing")
        self.assertEqual(len(jobs), 1)               # 在算就不再起第二个
        jobs[0]()                                    # 后台算完
        ready = disk.snapshot(self.home, now=NOW + 2, spawn=jobs.append)
        self.assertEqual(ready["state"], "ready")
        self.assertFalse(ready["refreshing"])
        self.assertEqual(ready["backup_bytes"], 5000)
        self.assertEqual(ready["media_bytes"], 1000)
        self.assertEqual(ready["total_bytes"], ready["db_bytes"] + 5000 + 300 + 1000 + 10)
        self.assertEqual(ready["oldest_frame_ts"], "2026-01-01T00:00:00.000000+00:00")
        self.assertEqual(ready["growth"]["samples"], 1)
        self.assertEqual(len(jobs), 1)               # 新鲜缓存不再起

    def test_stale_or_refresh_schedules_again_but_never_twice_in_flight(self):
        jobs = []
        disk.snapshot(self.home, now=NOW, spawn=jobs.append)
        jobs[0]()
        disk.snapshot(self.home, now=NOW + disk.CACHE_TTL_S + 1, spawn=jobs.append)
        self.assertEqual(len(jobs), 2)
        got = disk.snapshot(self.home, now=NOW + disk.CACHE_TTL_S + 2, refresh=True, spawn=jobs.append)
        self.assertEqual(len(jobs), 2)               # 已在算，refresh 不叠加
        self.assertEqual(got["state"], "ready")      # 旧快照照旧可读
        self.assertTrue(got["refreshing"])
        jobs[1]()
        disk.snapshot(self.home, now=NOW + disk.CACHE_TTL_S + 3, refresh=True, spawn=jobs.append)
        self.assertEqual(len(jobs), 3)

    def test_background_failure_becomes_state_error_and_releases_inflight(self):
        jobs = []
        disk.snapshot(self.home, now=NOW, spawn=jobs.append)
        with mock.patch.object(disk, "compute", side_effect=RuntimeError("boom")):
            jobs[0]()
        got = disk.snapshot(self.home, now=NOW + 1, spawn=jobs.append)
        self.assertEqual(got["state"], "error")
        self.assertIn("boom", got["error"])
        self.assertFalse(got["refreshing"])

    def test_missing_root_is_ready_with_zeros(self):
        with mock.patch.object(paths, "screenpipe_dir", return_value=self.root / "absent"):
            snap = disk.compute(self.home, now=NOW)
        self.assertEqual(snap["state"], "ready")
        self.assertFalse(snap["root_exists"])
        self.assertEqual(snap["total_bytes"], 0)
        self.assertEqual(snap["db_error"], "no_db")

    def test_retention_days_and_last_prune_come_from_settings_and_receipt(self):
        (self.home / "state" / "settings_overrides.json").write_text(json.dumps({"screenpipe_retention_days": 14}),
                                                                      encoding="utf-8")
        (self.home / "state" / disk.RECEIPT_NAME).write_text(json.dumps({"ran_at": "x", "deleted_frames": 7}),
                                                             encoding="utf-8")
        got = disk.snapshot(self.home, now=NOW, spawn=lambda fn: None)
        self.assertEqual(got["retention_days"], 14)
        self.assertEqual(got["last_prune"], {"ran_at": "x", "deleted_frames": 7})

    def test_receipt_name_mirrors_act(self):
        self.assertEqual(disk.RECEIPT_NAME, act_ret.RECEIPT_NAME)


class EndpointTestCase(unittest.TestCase):
    def setUp(self):
        disk.reset_cache_for_tests()
        self.addCleanup(disk.reset_cache_for_tests)
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-disk-http-")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        (self.home / "state").mkdir(parents=True)
        self.root = Path(self.tmp.name) / ".screenpipe"
        make_root(self.root)
        patcher = mock.patch.object(paths, "screenpipe_dir", return_value=self.root)
        patcher.start()
        self.addCleanup(patcher.stop)
        _httpd, self.port = start_server(self, self.home)

    def test_get_returns_immediately_then_becomes_ready(self):
        status, first = get_json(self.port, "/api/screenpipe/disk")
        self.assertEqual(status, 200)
        self.assertIn(first["state"], ("computing", "ready"))
        deadline = time.time() + 10
        got = first
        while got["state"] != "ready" and time.time() < deadline:
            time.sleep(0.05)
            _s, got = get_json(self.port, "/api/screenpipe/disk")
        self.assertEqual(got["state"], "ready")
        self.assertEqual(got["backup_bytes"], 5000)
        self.assertEqual(got["backups"][0]["name"], "db.sqlite.bak-20260604")
        self.assertIn("growth", got)
        self.assertEqual(got["retention_days"], 0)
        self.assertTrue((self.home / "state" / disk.SAMPLES_NAME).exists())

    def test_get_is_token_light_and_write_methods_are_rejected(self):
        status, _h, _b = http_request(self.port, "GET", "/api/screenpipe/disk")
        self.assertEqual(status, 200)
        status, _h, _b = http_request(self.port, "POST", "/api/screenpipe/disk", body=b"{}", headers=auth_headers(self.port))
        self.assertIn(status, (404, 405))

    def test_retention_field_is_in_the_catalog_and_round_trips(self):
        status, section = get_json(self.port, "/api/settings/storage")
        self.assertEqual(status, 200)
        field = {f["key"]: f for f in section["fields"]}["screenpipe_retention_days"]
        self.assertEqual((field["kind"], field["default"], field["effective"], field["source"]), ("int", 0, 0, "default"))
        body = json.dumps({"screenpipe_retention_days": 30}).encode("utf-8")
        status, _h, _b = http_request(self.port, "PUT", "/api/settings/storage", body=body, headers=auth_headers(self.port))
        self.assertEqual(status, 200)
        overrides = json.loads((self.home / "state" / "settings_overrides.json").read_text(encoding="utf-8"))
        self.assertEqual(overrides["screenpipe_retention_days"], 30)
        _s, snap = get_json(self.port, "/api/screenpipe/disk")
        self.assertEqual(snap["retention_days"], 30)
        status, _h, _b = http_request(self.port, "PUT", "/api/settings/storage",
                                      body=json.dumps({"screenpipe_retention_days": -1}).encode("utf-8"),
                                      headers=auth_headers(self.port))
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()
