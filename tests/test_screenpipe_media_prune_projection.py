"""媒体清理回执的投影与「停了」的判定（CONTRACT §72.4；server/screenpipe_disk.py）。

issue #28 的验收标准里那一条：清理**要可观测**，不只是可配——「清理停了」与「跑了但没东西可删」
从外面看一模一样，而前一种会永久地让盘涨。所以 `ingest/screenpipe-cleanup.sh` 每轮写一份回执，
`GET /api/screenpipe/disk` 把它原样带出来并加两个算出来的字段：

- `media_prune.age_seconds`（上一次尝试的岁数）/ `ok_age_seconds` / `stale`（> `PRUNE_STALE_S`；链本该 30 分钟一轮）；
- **`stale` 按 `last_ok_ts`（上次干净跑完）算，不按上一次尝试算**：每 30 分钟失败一次的清理会把 `ts`
  一直刷新，按 `ts` 算就永远「刚跑过」，而一个文件都没被删掉——那正是这一行要报出来的情形；
- 回执缺席 / 坏 JSON / 坏时间戳 → `state: "never"` + `stale: true`（宁可报「没跑过」也不报一次干净的空转）；
- 脚本的 `unreadable`（目录在但进不去）/ `partial`（没扫完）原样保留，不与「删了 0 个」混为一谈；
- 升级前写的回执没有 `last_ok_ts`：它自己是 `ok` 的话，它的 `ts` 就算一次成功。

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
        self.assertIsNone(got["ok_age_seconds"])
        self.assertIsNone(got["last_ok_ts"])
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
        # 上一轮还成功过（脚本把 last_ok_ts 带了下来）：这一轮的毛病是权限，不是「久没跑」
        self.write_receipt({"ts": iso(NOW - 60), "state": "unreadable", "retention_minutes": 60,
                            "deleted_files": 0, "deleted_bytes": 0, "last_ok_ts": iso(NOW - 1800)})
        got = disk.media_prune(self.home, NOW)
        self.assertEqual(got["state"], "unreadable")
        self.assertEqual(got["ok_age_seconds"], 1800.0)
        self.assertFalse(got["stale"])

    def test_a_prune_that_fails_every_round_forever_is_stale_not_fresh(self):
        """每 30 分钟失败一次：`ts` 一直是「刚刚」，但一个文件都没删过——必须报 stale
        并说出距上次成功多久（按 `ts` 算新鲜度的话这台机器的盘会一直涨而界面一片绿）。"""
        self.write_receipt({"ts": iso(NOW - 60), "state": "unreadable", "retention_minutes": 60,
                            "deleted_files": 0, "deleted_bytes": 0, "last_ok_ts": iso(NOW - 6 * 3600)})
        got = disk.media_prune(self.home, NOW)
        self.assertEqual(got["age_seconds"], 60.0)              # 上一次**尝试**是一分钟前
        self.assertEqual(got["ok_age_seconds"], 21600.0)        # 上一次**成功**是六小时前
        self.assertTrue(got["stale"])
        # 一次都没成功过（脚本写 null）：不知道就别报新鲜
        self.write_receipt({"ts": iso(NOW - 60), "state": "unreadable", "retention_minutes": 60,
                            "deleted_files": 0, "deleted_bytes": 0, "last_ok_ts": None})
        got = disk.media_prune(self.home, NOW)
        self.assertIsNone(got["ok_age_seconds"])
        self.assertIsNone(got["last_ok_ts"])
        self.assertTrue(got["stale"])

    def test_a_partial_round_is_its_own_state_and_never_counts_as_a_clean_one(self):
        """`partial` = find 没枚举完（读不到的子目录 / 文件在扫的过程中变动）：删掉的那些是真的，
        但这一轮不算干净——`last_ok_ts` 不许被它刷新，所以连着几小时 partial 会变 stale。"""
        self.write_receipt({"ts": iso(NOW - 60), "state": "partial", "retention_minutes": 60,
                            "deleted_files": 3, "deleted_bytes": 900, "last_ok_ts": iso(NOW - 600)})
        got = disk.media_prune(self.home, NOW)
        self.assertEqual((got["state"], got["deleted_files"]), ("partial", 3))
        self.assertFalse(got["stale"])          # 十分钟前才有一轮干净的——一次撞车不是毛病
        self.write_receipt({"ts": iso(NOW - 60), "state": "partial", "retention_minutes": 60,
                            "deleted_files": 3, "deleted_bytes": 900,
                            "last_ok_ts": iso(NOW - disk.PRUNE_STALE_S - 60)})
        self.assertTrue(disk.media_prune(self.home, NOW)["stale"])

    def test_a_legacy_receipt_without_last_ok_ts_uses_its_own_ok_timestamp(self):
        # 升级前那一轮写的回执（没有这一键）：state 是 ok 就说明那一刻真跑完了
        self.write_receipt({"ts": iso(NOW - 600), "state": "ok", "deleted_files": 1})
        got = disk.media_prune(self.home, NOW)
        self.assertEqual(got["last_ok_ts"], iso(NOW - 600))
        self.assertFalse(got["stale"])
        # 失败态的老回执无从得知上次成功是什么时候 —— 按「没成功过」算
        self.write_receipt({"ts": iso(NOW - 600), "state": "no_data_dir", "deleted_files": 0})
        got = disk.media_prune(self.home, NOW)
        self.assertIsNone(got["last_ok_ts"])
        self.assertTrue(got["stale"])

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
        # 脚本写的两个新东西（键名 + partial 这个 state）与本投影逐字对上
        self.assertIn('"last_ok_ts":%s', script)
        self.assertIn("write_receipt partial", script)


if __name__ == "__main__":
    unittest.main()
