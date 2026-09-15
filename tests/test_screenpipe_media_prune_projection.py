"""媒体清理回执的投影与「停了」的判定（CONTRACT §72.4；server/screenpipe_disk.py）。

issue #28 的验收标准里那一条：清理**要可观测**，不只是可配——「清理停了」与「跑了但没东西可删」
从外面看一模一样，而前一种会永久地让盘涨。所以 `ingest/screenpipe-cleanup.sh` 每轮写一份回执，
`GET /api/screenpipe/disk` 把它原样带出来并加两个算出来的字段：

- `media_prune.age_seconds` / `stale`（> `PRUNE_STALE_S`；链本该 30 分钟一轮）；
- 回执缺席 / 坏 JSON / 坏时间戳 → `state: "never"` + `stale: true`（宁可报「没跑过」也不报一次干净的空转）；
- 脚本的 `unreadable`（目录在但进不去）原样保留，不与「删了 0 个」混为一谈。

另钉住这条投影**在 GET 路径上**：把 `scan` 换成会抛的桩，快照照样出得来（渲染路径零阻塞 IO，§72.1）。
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first

from server import screenpipe_disk as disk

NOW = 1_800_000_000.0


def iso(epoch: float) -> str:
    import datetime as dt
    return dt.datetime.fromtimestamp(epoch, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class MediaPruneProjectionTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-media-prune-")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        (self.home / "state").mkdir(parents=True)

    def write_receipt(self, doc) -> None:
        path = self.home / "state" / disk.PRUNE_RECEIPT_NAME
        path.write_text(doc if isinstance(doc, str) else json.dumps(doc), encoding="utf-8")

    def test_missing_receipt_is_never_and_stale(self):
        got = disk.media_prune(self.home, NOW)
        self.assertEqual(got["state"], "never")
        self.assertTrue(got["stale"])
        self.assertIsNone(got["age_seconds"])
        self.assertIsNone(got["deleted_files"])

    def test_fresh_ok_receipt_is_projected_verbatim_and_not_stale(self):
        self.write_receipt({"ts": iso(NOW - 600), "state": "ok", "retention_minutes": 60,
                            "deleted_files": 12, "deleted_bytes": 345, "data_dir": "/x/.screenpipe/data"})
        got = disk.media_prune(self.home, NOW)
        self.assertEqual((got["state"], got["deleted_files"], got["deleted_bytes"], got["retention_minutes"]),
                         ("ok", 12, 345, 60))
        self.assertEqual(got["data_dir"], "/x/.screenpipe/data")
        self.assertEqual(got["age_seconds"], 600.0)
        self.assertFalse(got["stale"])

    def test_a_clean_but_old_run_is_stale(self):
        self.write_receipt({"ts": iso(NOW - disk.PRUNE_STALE_S - 60), "state": "ok",
                            "retention_minutes": 60, "deleted_files": 0, "deleted_bytes": 0})
        got = disk.media_prune(self.home, NOW)
        self.assertEqual((got["state"], got["deleted_files"]), ("ok", 0))   # 删 0 个不是错
        self.assertTrue(got["stale"])                                       # 但三小时没动静是

    def test_unreadable_keeps_its_own_state(self):
        self.write_receipt({"ts": iso(NOW - 60), "state": "unreadable", "retention_minutes": 60,
                            "deleted_files": 0, "deleted_bytes": 0})
        got = disk.media_prune(self.home, NOW)
        self.assertEqual(got["state"], "unreadable")
        self.assertFalse(got["stale"])          # 刚跑过——问题是权限，不是没跑

    def test_broken_receipt_or_timestamp_reads_as_never_or_stale(self):
        self.write_receipt("{not json")
        self.assertEqual(disk.media_prune(self.home, NOW)["state"], "never")
        self.write_receipt([1, 2])
        self.assertEqual(disk.media_prune(self.home, NOW)["state"], "never")
        self.write_receipt({"ts": "whenever", "state": "ok", "deleted_files": 3})
        got = disk.media_prune(self.home, NOW)
        self.assertEqual(got["state"], "ok")
        self.assertIsNone(got["age_seconds"])
        self.assertTrue(got["stale"])           # 时间戳读不出来 = 不敢说它新鲜
        self.write_receipt({"state": "", "ts": iso(NOW)})
        self.assertEqual(disk.media_prune(self.home, NOW)["state"], "never")

    def test_a_future_timestamp_is_not_negative_age(self):
        self.write_receipt({"ts": iso(NOW + 300), "state": "ok", "deleted_files": 1})
        got = disk.media_prune(self.home, NOW)
        self.assertEqual(got["age_seconds"], 0.0)
        self.assertFalse(got["stale"])

    def test_snapshot_carries_the_receipt_and_the_knob_without_touching_the_disk(self):
        self.write_receipt({"ts": iso(NOW - 60), "state": "ok", "retention_minutes": 30,
                            "deleted_files": 2, "deleted_bytes": 8})
        (self.home / "state" / "settings_overrides.json").write_text(
            json.dumps({"screenpipe_media_retention_minutes": 30}), encoding="utf-8")
        with mock.patch.object(disk, "scan", side_effect=AssertionError("GET path must never walk the tree")), \
             mock.patch.object(disk, "db_stats", side_effect=AssertionError("GET path must never open sqlite")):
            snap = disk.snapshot(self.home, now=NOW, spawn=lambda fn: None)
        self.assertEqual(snap["media_retention_minutes"], 30)
        self.assertEqual(snap["media_prune"]["deleted_files"], 2)
        self.assertFalse(snap["media_prune"]["stale"])
        self.assertEqual(snap["state"], "computing")     # 后台还没算，数字仍是 null

    def test_knob_falls_back_to_the_factory_default_when_the_catalog_blows_up(self):
        with mock.patch.object(disk.settings_catalog, "effective_value", side_effect=RuntimeError("boom")):
            self.assertEqual(disk.media_retention_minutes(self.home), disk.MEDIA_RETENTION_DEFAULT)

    def test_receipt_name_mirrors_the_cleanup_script(self):
        script = (Path(__file__).resolve().parents[1] / "ingest" / "screenpipe-cleanup.sh").read_text(encoding="utf-8")
        self.assertIn("state/%s" % disk.PRUNE_RECEIPT_NAME, script)
        self.assertIn("--print-value screenpipe_media_retention_minutes", script)


if __name__ == "__main__":
    unittest.main()
