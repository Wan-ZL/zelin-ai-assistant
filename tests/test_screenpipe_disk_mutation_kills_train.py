"""server/screenpipe_disk.py 的变异残存补杀（CONTRACT §72.1 / §72.4；路由 §49）。

夜间变异跑（§57）在这张磁盘快照上留了一批活口，集中在三处「数字本身就是契约」的
地方：样本台账的两道帽（间隔 6 小时、容量 240 条、窗口 30 天）、增长估算「跨度不够
就不给数字」的那条线（§0 第 3 条：不虚报），以及 §72.4 媒体清理的新鲜度阈值
（3 小时，按**上次干净跑完**算）。另有几处是「目录读不出来不该炸掉整张快照」的兜底。

既有判例（tests/test_server_screenpipe_disk.py）在这些点上读模块常量断言——读常量的
断言会跟着变异体一起挪，等于没钉；本文件一律写字面量，且只补既有判例没覆盖的边界。
"""
import datetime as _dt
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first

from server import paths
from server import screenpipe_disk as disk

NOW = 1_800_000_000.0
DAY = 86400.0
HOUR = 3600.0


def _iso(epoch: float) -> str:
    return _dt.datetime.fromtimestamp(epoch, _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _make_db(path: Path, stamps=("2026-01-01T00:00:00.000000+00:00",)) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE frames (id INTEGER PRIMARY KEY, timestamp TIMESTAMP NOT NULL)")
    for s in stamps:
        conn.execute("INSERT INTO frames(timestamp) VALUES (?)", (s,))
    conn.commit()
    conn.close()


class TempDirCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-disk-mk-")
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.home = self.dir / "home"
        (self.home / "state").mkdir(parents=True)


# --------------------------------------------------------------------------- #
# 样本台账的三道帽（防腐 #4：出生即带帽）
# --------------------------------------------------------------------------- #
class SampleLedgerTestCase(TempDirCase):
    def test_a_sample_is_taken_exactly_six_hours_apart_not_a_second_sooner(self):
        samples = [[NOW, 10]]
        self.assertIs(disk.append_sample(samples, NOW + 6 * HOUR - 1, 20), samples)
        grown = disk.append_sample(samples, NOW + 6 * HOUR, 20)
        self.assertEqual(grown, [[NOW, 10], [NOW + 6 * HOUR, 20]])

    def test_the_gap_is_measured_from_the_newest_sample_not_the_oldest(self):
        samples = [[NOW - 10 * HOUR, 1], [NOW, 2]]
        self.assertIs(disk.append_sample(samples, NOW + HOUR, 3), samples)

    def test_the_ledger_never_grows_past_two_hundred_and_forty_entries(self):
        many = [[float(i), i] for i in range(300)]
        capped = disk.append_sample(many, NOW, 7)
        self.assertEqual(len(capped), 240)
        self.assertEqual(capped[-1], [NOW, 7])

    def test_saving_samples_creates_the_state_dir_on_the_way(self):
        path = self.dir / "fresh" / "state" / "s.json"
        disk.save_samples(path, [[1.0, 2]])
        self.assertEqual(disk.load_samples(path), [[1.0, 2]])


# --------------------------------------------------------------------------- #
# 增长估算：跨度不够就不给数字（§0 第 3 条）
# --------------------------------------------------------------------------- #
class EstimateBoundaryTestCase(unittest.TestCase):
    def test_the_window_is_exactly_thirty_days_wide(self):
        edge = disk.estimate([[NOW - 30 * DAY, 100], [NOW, 200]], NOW, 0, None)
        self.assertEqual(edge["samples"], 2)
        outside = disk.estimate([[NOW - 30 * DAY - 1, 100], [NOW, 200]], NOW, 0, None)
        self.assertEqual(outside["samples"], 1)

    def test_two_samples_a_full_day_apart_are_already_a_trustworthy_slope(self):
        out = disk.estimate([[NOW - DAY, 1_000], [NOW, 2_000]], NOW, 0, None)
        self.assertEqual(out["basis"], "samples")
        self.assertEqual(out["span_days"], 1.0)
        self.assertEqual(out["bytes_per_month"], 30_000)

    def test_span_days_keeps_one_decimal(self):
        slope = disk.estimate([[NOW - 1.25 * DAY, 0], [NOW, 1_000]], NOW, 0, None)
        self.assertEqual(slope["span_days"], 1.2)
        lifetime = disk.estimate([], NOW, 1_000, _iso(NOW - 1.25 * DAY))
        self.assertEqual(lifetime["basis"], "lifetime")
        self.assertEqual(lifetime["span_days"], 1.2)

    def test_lifetime_average_needs_a_full_day_and_at_least_one_byte(self):
        day_old = _iso(NOW - DAY)
        self.assertEqual(disk.estimate([], NOW, 100, day_old)["basis"], "lifetime")
        self.assertEqual(disk.estimate([], NOW, 1, _iso(NOW - 2 * DAY))["basis"], "lifetime")
        empty = disk.estimate([], NOW, 0, _iso(NOW - 2 * DAY))
        self.assertIsNone(empty["basis"])
        self.assertIsNone(empty["bytes_per_month"])


# --------------------------------------------------------------------------- #
# 扫描与 db 问询
# --------------------------------------------------------------------------- #
class ScanTestCase(TempDirCase):
    def test_an_empty_root_scans_to_honest_zeros(self):
        root = self.dir / "empty"
        root.mkdir()
        out = disk.scan(root)
        self.assertEqual(out["sizes"], {"db": 0, "backup": 0, "log": 0, "media": 0, "other": 0})
        self.assertEqual(out["total_bytes"], 0)
        self.assertEqual(out["file_count"], 0)
        self.assertEqual(out["backups"], [])

    def test_only_the_five_biggest_backups_are_listed(self):
        root = self.dir / ".screenpipe"
        root.mkdir()
        for i in range(6):
            (root / ("db.sqlite.bak-%d" % i)).write_bytes(b"b" * (100 + i))
        out = disk.scan(root)
        self.assertEqual(len(out["backups"]), 5)
        self.assertEqual([b["bytes"] for b in out["backups"]], [105, 104, 103, 102, 101])

    def test_db_stats_opens_the_engine_db_and_a_connect_failure_stays_a_dict(self):
        root = self.dir / ".screenpipe"
        root.mkdir()
        _make_db(root / "db.sqlite")
        ok = disk.db_stats(root / "db.sqlite")
        self.assertIsNone(ok["db_error"])
        self.assertEqual(ok["oldest_frame_ts"], "2026-01-01T00:00:00.000000+00:00")
        self.assertIsInstance(ok["db_reclaimable_bytes"], int)
        with mock.patch.object(disk.sqlite3, "connect",
                               side_effect=sqlite3.OperationalError("locked")):
            broken = disk.db_stats(root / "db.sqlite")
        self.assertEqual(broken["db_error"], "locked")          # 是一份回执，不是 None
        self.assertIsNone(broken["db_reclaimable_bytes"])

    def test_a_missing_root_computes_to_zeros_at_the_clock_it_was_given(self):
        snap = disk.compute(self.home, root=self.dir / "gone", now=NOW)
        self.assertEqual(snap["state"], "ready")
        self.assertEqual(snap["computed_at"], _iso(NOW))
        self.assertFalse(snap["root_exists"])
        for key in ("total_bytes", "db_bytes", "backup_bytes", "log_bytes",
                    "media_bytes", "other_bytes", "file_count"):
            self.assertEqual(snap[key], 0, key)
        self.assertEqual(snap["backups"], [])
        self.assertEqual(snap["db_error"], "no_db")


# --------------------------------------------------------------------------- #
# 目录读不出来 / 媒体清理回执（§72.4）
# --------------------------------------------------------------------------- #
class CatalogAndMediaTestCase(TempDirCase):
    def _prune_receipt(self, doc: dict) -> None:
        (self.home / "state" / "screenpipe_prune.json").write_text(
            json.dumps(doc), encoding="utf-8")

    def test_an_unreadable_settings_catalog_falls_back_to_off_and_the_factory_minutes(self):
        """目录坏了是设置页的事，不该让整张磁盘快照失败（§72.1 兜底）。"""
        (self.home / "state" / "settings_overrides.json").write_text("{ not json",
                                                                     encoding="utf-8")
        self.assertEqual(disk.retention_days(self.home), 0)
        self.assertEqual(disk.media_retention_minutes(self.home), 60)

    def test_media_prune_goes_stale_exactly_three_hours_after_the_last_clean_run(self):
        ts = "2027-01-15T08:00:00+00:00"
        epoch = _dt.datetime(2027, 1, 15, 8, tzinfo=_dt.timezone.utc).timestamp()
        self._prune_receipt({"state": "ok", "ts": ts, "last_ok_ts": ts,
                             "retention_minutes": 60, "deleted_files": 2,
                             "deleted_bytes": 10, "data_dir": "/x"})
        fresh = disk.media_prune(self.home, epoch + 3 * HOUR)
        self.assertEqual(fresh["ok_age_seconds"], 10800.0)
        self.assertFalse(fresh["stale"])
        gone = disk.media_prune(self.home, epoch + 3 * HOUR + 1)
        self.assertTrue(gone["stale"])

    def test_media_prune_ages_are_rounded_to_milliseconds(self):
        ts = "2027-01-15T08:00:00+00:00"
        epoch = _dt.datetime(2027, 1, 15, 8, tzinfo=_dt.timezone.utc).timestamp()
        self._prune_receipt({"state": "ok", "ts": ts, "last_ok_ts": ts})
        out = disk.media_prune(self.home, epoch + 0.0625)
        self.assertEqual(out["age_seconds"], 0.062)
        self.assertEqual(out["ok_age_seconds"], 0.062)


# --------------------------------------------------------------------------- #
# GET 路径的缓存
# --------------------------------------------------------------------------- #
class SnapshotCacheTestCase(TempDirCase):
    def setUp(self):
        super().setUp()
        disk.reset_cache_for_tests()
        self.root = self.dir / ".screenpipe"
        self.root.mkdir()
        _make_db(self.root / "db.sqlite")
        patcher = mock.patch.object(paths, "screenpipe_dir", return_value=self.root)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(disk.reset_cache_for_tests)     # LIFO：先 join 再拆临时目录

    def test_the_first_placeholder_admits_it_has_no_samples_yet(self):
        first = disk.snapshot(self.home, now=NOW, spawn=lambda fn: None)
        self.assertEqual(first["state"], "computing")
        self.assertEqual(first["growth"], {"bytes_per_month": None, "basis": None,
                                           "span_days": None, "samples": 0})

    def test_the_cache_is_recomputed_the_moment_the_ttl_is_reached(self):
        jobs = []
        disk.snapshot(self.home, now=NOW, spawn=jobs.append)
        jobs[0]()                                        # 后台算完，缓存时刻 = NOW
        disk.snapshot(self.home, now=NOW + 599, spawn=jobs.append)
        self.assertEqual(len(jobs), 1)                   # 还新鲜
        disk.snapshot(self.home, now=NOW + 600, spawn=jobs.append)
        self.assertEqual(len(jobs), 2)                   # 正好到点就重算
