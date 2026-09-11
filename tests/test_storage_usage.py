"""磁盘占用 / 增长估计 / prune 回执的判例（CONTRACT §71，issue #28）。

- walk：按类分（media / index / other），符号链接不跟，帽住 + truncated。
- usage_snapshot：**请求线程里永不走目录树**（spawn 注入缝，没人替它跑就一直
  scanning）、TTL 内不重扫、过期先回旧值再后台刷、?refresh 强制重扫。
- growth：采样够长按首尾净增、净增为负算 0、样本不够退回 lifetime、都不够 → None。
- 采样台账：≤ SAMPLES_CAP 条、不足 1 小时改写最后一条（帽 = 出生自带，防腐 #4）。
- prune 投影：ok / no_data_dir / unreadable / 缺席 / 坏 JSON / 坏时间戳、stale 阈值。
- 保留期三层读 + 夹取 + diff-write。
"""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from tests import TMP_HOME  # noqa: F401 - sandbox env first

from server import storage
from server.errors import InvalidFieldError, UnknownFieldError


def inline(fn):
    """spawn 注入缝：同线程立即跑（判例绝不起真线程）。"""
    fn()


def never(_fn):
    """spawn 注入缝：什么都不做——扫描永远出不来结果。"""


class _Case(unittest.TestCase):
    def setUp(self):
        storage.reset_cache()
        self.addCleanup(storage.reset_cache)
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-storage-")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        (self.home / "state").mkdir(parents=True)
        self.root = Path(self.tmp.name) / "screenpipe"
        self._prev = os.environ.get("ZAI_SCREENPIPE_DIR")
        os.environ["ZAI_SCREENPIPE_DIR"] = str(self.root)
        self.addCleanup(self._restore_env)

    def _restore_env(self):
        if self._prev is None:
            os.environ.pop("ZAI_SCREENPIPE_DIR", None)
        else:
            os.environ["ZAI_SCREENPIPE_DIR"] = self._prev

    def write(self, rel: str, size: int) -> Path:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x" * size)
        return p


class WalkTestCase(_Case):
    def test_splits_media_index_and_other(self):
        self.write("data/a.jpg", 100)
        self.write("data/b.mp4", 200)
        self.write("data/c.JPG", 50)          # 后缀大小写不敏感
        self.write("db.sqlite", 10)
        self.write("db.sqlite-wal", 5)
        self.write("engine.log", 7)
        out = storage.walk(self.root)
        self.assertEqual(out["state"], "ready")
        self.assertEqual(out["bytes"], {"media": 350, "index": 15, "other": 7, "total": 372})
        self.assertEqual(out["files"], {"media": 3, "index": 2, "other": 1})
        self.assertFalse(out["truncated"])

    def test_symlink_is_not_followed_and_counts_as_its_own_tiny_entry(self):
        target = self.write("data/real.mp4", 1000)
        link = self.root / "data" / "link.mp4"
        link.symlink_to(target)
        out = storage.walk(self.root)
        # lstat：软链本身几十字节，绝不把同一份数据算两遍
        self.assertLess(out["bytes"]["media"], 2000)
        self.assertEqual(out["files"]["media"], 2)

    def test_cap_truncates_and_says_so(self):
        for i in range(5):
            self.write("data/f%d.jpg" % i, 10)
        out = storage.walk(self.root, cap=3)
        self.assertTrue(out["truncated"])
        self.assertEqual(out["files"]["media"], 3)

    def test_missing_dir_is_missing_not_an_error(self):
        snap = storage.usage_snapshot(self.home, spawn=inline)
        self.assertEqual(snap["state"], "missing")
        self.assertIsNone(snap["bytes"])


class UsageSnapshotTestCase(_Case):
    def test_first_read_never_walks_the_tree_in_the_request_thread(self):
        self.write("data/a.jpg", 100)
        snap = storage.usage_snapshot(self.home, spawn=never)
        self.assertEqual(snap["state"], "scanning")   # 没人替它跑 = 没有结果
        self.assertIsNone(snap["bytes"])
        self.assertTrue(snap["scanning"])

    def test_second_read_inside_ttl_does_not_rescan(self):
        self.write("data/a.jpg", 100)
        first = storage.usage_snapshot(self.home, spawn=inline)
        self.assertEqual(first["state"], "ready")
        self.write("data/b.jpg", 900)
        again = storage.usage_snapshot(self.home, spawn=inline)
        self.assertEqual(again["bytes"]["total"], 100)   # 缓存里的旧值
        self.assertFalse(again["stale"])

    def test_refresh_forces_a_rescan(self):
        self.write("data/a.jpg", 100)
        storage.usage_snapshot(self.home, spawn=inline)
        self.write("data/b.jpg", 900)
        fresh = storage.usage_snapshot(self.home, refresh=True, spawn=inline)
        self.assertEqual(fresh["bytes"]["total"], 1000)

    def test_expired_cache_serves_the_old_value_and_refreshes_behind_it(self):
        self.write("data/a.jpg", 100)
        storage.usage_snapshot(self.home, spawn=inline)
        storage._cache["at"] = time.time() - storage.SCAN_TTL_S - 1
        self.write("data/b.jpg", 900)
        held = []
        out = storage.usage_snapshot(self.home, spawn=lambda fn: held.append(fn))
        self.assertEqual(out["bytes"]["total"], 100)     # 先回旧值，不让用户盯 spinner
        self.assertTrue(out["stale"])
        held[0]()                                        # 后台那一轮跑完
        self.assertEqual(storage._cache["usage"]["bytes"]["total"], 1000)

    def test_a_dead_spawn_does_not_wedge_the_scanning_flag(self):
        def boom(_fn):
            raise RuntimeError("no threads today")
        with self.assertRaises(RuntimeError):
            storage.usage_snapshot(self.home, spawn=boom)
        self.assertFalse(storage._cache.get("scanning"))


class GrowthTestCase(_Case):
    def _samples(self, rows):
        (self.home / "state" / "storage_samples.json").write_text(
            json.dumps({"samples": rows}), encoding="utf-8")

    def test_samples_basis_uses_the_two_ends(self):
        now = time.time()
        self._samples([[now - 10 * 86400, 0], [now - 5 * 86400, 10 ** 9], [now, 10 ** 10]])
        usage = {"state": "ready", "bytes": {"total": 10 ** 10}}
        out = storage.growth(self.home, usage, now=now)
        self.assertEqual(out["basis"], "samples")
        self.assertEqual(out["days"], 10.0)
        self.assertEqual(out["bytes_per_month"], int(10 ** 10 / 10 * 30))

    def test_negative_net_growth_reports_zero_not_a_scare(self):
        now = time.time()
        self._samples([[now - 10 * 86400, 10 ** 10], [now, 10 ** 8]])
        out = storage.growth(self.home, {"state": "ready", "bytes": {"total": 10 ** 8}}, now=now)
        self.assertEqual(out["bytes_per_month"], 0)

    def test_short_span_falls_back_to_lifetime(self):
        now = time.time()
        self._samples([[now - 60, 1], [now, 2]])
        self.root.mkdir(parents=True)
        out = storage.growth(self.home, {"state": "ready", "bytes": {"total": 10 ** 9}}, now=now)
        # 目录刚建（不足一天）→ 连 lifetime 都说不出来
        self.assertIsNone(out)

    def test_nothing_to_say_when_not_ready(self):
        self.assertIsNone(storage.growth(self.home, {"state": "scanning", "bytes": None}))


class SamplesLedgerTestCase(_Case):
    def test_capped_and_hourly(self):
        now = time.time()
        for i in range(storage.SAMPLES_CAP + 10):
            storage._record_sample(self.home, i, now + i * storage.SAMPLE_MIN_GAP_S)
        rows = storage.read_samples(self.home)
        self.assertEqual(len(rows), storage.SAMPLES_CAP)
        self.assertEqual(rows[-1][1], storage.SAMPLES_CAP + 9)

    def test_a_second_read_within_the_hour_rewrites_the_last_row(self):
        now = time.time()
        storage._record_sample(self.home, 100, now)
        storage._record_sample(self.home, 200, now + 60)
        rows = storage.read_samples(self.home)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1], 200)

    def test_junk_file_reads_as_empty(self):
        (self.home / "state" / "storage_samples.json").write_text("nope", encoding="utf-8")
        self.assertEqual(storage.read_samples(self.home), [])


class PruneReceiptTestCase(_Case):
    def _receipt(self, doc):
        storage.prune_receipt_path(self.home).write_text(
            json.dumps(doc) if isinstance(doc, dict) else doc, encoding="utf-8")

    def test_absent_receipt_is_never_and_stale(self):
        out = storage._prune(self.home, time.time())
        self.assertEqual(out["state"], "never")
        self.assertTrue(out["stale"])

    def test_fresh_ok_receipt(self):
        now = time.time()
        self._receipt({"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 60)),
                       "state": "ok", "retention_minutes": 60,
                       "deleted_files": 3, "deleted_bytes": 999})
        out = storage._prune(self.home, now)
        self.assertEqual((out["state"], out["deleted_files"], out["stale"]), ("ok", 3, False))

    def test_old_receipt_is_stale(self):
        now = time.time()
        self._receipt({"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                           time.gmtime(now - storage.PRUNE_STALE_S - 60)),
                       "state": "ok", "deleted_files": 0, "deleted_bytes": 0})
        self.assertTrue(storage._prune(self.home, now)["stale"])

    def test_unreadable_is_carried_through_verbatim(self):
        now = time.time()
        self._receipt({"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
                       "state": "unreadable", "deleted_files": 0, "deleted_bytes": 0})
        self.assertEqual(storage._prune(self.home, now)["state"], "unreadable")

    def test_junk_shapes_never_raise(self):
        now = time.time()
        for doc in ("not json", "[]", {"ts": 5}, {"ts": "yesterday"}, {}):
            self._receipt(doc)
            self.assertEqual(storage._prune(self.home, now)["state"], "never")

    def test_unknown_state_word_is_not_echoed_as_a_status(self):
        now = time.time()
        self._receipt({"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
                       "state": "whatever"})
        self.assertEqual(storage._prune(self.home, now)["state"], "unknown")


class RetentionKnobTestCase(_Case):
    def test_coerce_clamps_instead_of_refusing(self):
        self.assertEqual(storage.coerce_retention(1), storage.MIN_RETENTION_MINUTES)
        self.assertEqual(storage.coerce_retention(10 ** 9), storage.MAX_RETENTION_MINUTES)
        self.assertEqual(storage.coerce_retention("90"), 90)
        for junk in (True, None, "soon", [], {}):
            with self.assertRaises(ValueError):
                storage.coerce_retention(junk)

    def test_layers(self):
        self.assertEqual(storage._retention(self.home), (60, "default"))
        (self.home / "config.yaml").write_text(
            "recording:\n  media_retention_minutes: 120\n", encoding="utf-8")
        self.assertEqual(storage._retention(self.home), (120, "config"))
        (self.home / "state" / "settings_overrides.json").write_text(
            json.dumps({"recording_media_retention_minutes": 240}), encoding="utf-8")
        self.assertEqual(storage._retention(self.home), (240, "override"))

    def test_bad_override_keeps_the_effective_value(self):
        (self.home / "state" / "settings_overrides.json").write_text(
            json.dumps({"recording_media_retention_minutes": "soon"}), encoding="utf-8")
        self.assertEqual(storage._retention(self.home), (60, "default"))

    def test_update_diff_writes_and_deletes_on_equal(self):
        storage.update(self.home, {"media_retention_minutes": 180})
        doc = json.loads((self.home / "state" / "settings_overrides.json").read_text())
        self.assertEqual(doc["recording_media_retention_minutes"], 180)
        storage.update(self.home, {"media_retention_minutes": 60})   # 等于默认 = 删键
        doc = json.loads((self.home / "state" / "settings_overrides.json").read_text())
        self.assertNotIn("recording_media_retention_minutes", doc)

    def test_update_preserves_other_keys(self):
        (self.home / "state" / "settings_overrides.json").write_text(
            json.dumps({"language": "en"}), encoding="utf-8")
        storage.update(self.home, {"media_retention_minutes": 180})
        doc = json.loads((self.home / "state" / "settings_overrides.json").read_text())
        self.assertEqual(doc["language"], "en")

    def test_update_gates(self):
        with self.assertRaises(UnknownFieldError):
            storage.update(self.home, {"nope": 1})
        with self.assertRaises(InvalidFieldError):
            storage.update(self.home, {})
        with self.assertRaises(InvalidFieldError):
            storage.update(self.home, {"media_retention_minutes": "soon"})


class SnapshotShapeTestCase(_Case):
    def test_wire_shape(self):
        snap = storage.snapshot(self.home, spawn=never)
        self.assertEqual(set(snap), {"media_retention_minutes", "source", "bounds",
                                     "usage", "growth", "prune"})
        self.assertEqual(snap["bounds"], {"min": storage.MIN_RETENTION_MINUTES,
                                          "max": storage.MAX_RETENTION_MINUTES,
                                          "default": storage.DEFAULT_RETENTION_MINUTES})
        self.assertEqual(snap["usage"]["dir"], str(self.root))
        self.assertIsNone(snap["growth"])
        self.assertEqual(snap["prune"]["state"], "never")


if __name__ == "__main__":
    unittest.main()
