"""screenpipe DB 保留期清理（CONTRACT §72.2；§18 cron 链 cleanup 步；issue #28）。

判例钉住：0 = 关（零写入）；只删「早于截止点 ∧ id ≤ 导出标记」的行（未导出的一行不碰、标记缺席 = 一行不删）；
ocr_text / elements 随 frame 走、音频转写按自己的标记；分批 + 时间预算（用尽即停、下一轮接着）；dry-run 只数不删；
回执落 state/screenpipe_retention.json 且任何失败只进回执（永不抛）；config 层三层读到同一个值；CLI 一行 JSON。
真 sqlite（stdlib）建一张缩小版 schema；不碰 ~/.screenpipe。
"""
import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first

from act.lib import config
from act.lib import screenpipe_retention as ret

NOW = 1_757_200_000.0   # 2025-09-06T23:06:40Z（固定时钟；日期本身不重要，判例只比相对天数）
DAY = 86400.0


def _ts(offset_days: float) -> str:
    import datetime as dt
    stamp = dt.datetime.fromtimestamp(NOW - offset_days * DAY, dt.timezone.utc)
    return stamp.strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")


def make_db(path: Path, *, with_elements: bool = True) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.executescript("""
        CREATE TABLE frames (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TIMESTAMP NOT NULL, app_name TEXT);
        CREATE INDEX idx_frames_timestamp ON frames(timestamp);
        CREATE TABLE ocr_text (frame_id INTEGER NOT NULL, text TEXT NOT NULL);
        CREATE TABLE audio_transcriptions (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TIMESTAMP NOT NULL,
                                           transcription TEXT NOT NULL);
    """)
    if with_elements:
        conn.execute("CREATE TABLE elements (id INTEGER PRIMARY KEY, frame_id INTEGER NOT NULL, source TEXT)")
    return conn


def seed(conn: sqlite3.Connection, frame_days: list, audio_days: list) -> None:
    """frames id 1..n 按给定「几天前」；每帧一行 ocr_text + 一行 elements；音频同理。"""
    for days in frame_days:
        cur = conn.execute("INSERT INTO frames(timestamp, app_name) VALUES (?, 'X')", (_ts(days),))
        conn.execute("INSERT INTO ocr_text(frame_id, text) VALUES (?, 'ocr')", (cur.lastrowid,))
        if ret.table_exists(conn, "elements"):
            conn.execute("INSERT INTO elements(frame_id, source) VALUES (?, 'ocr')", (cur.lastrowid,))
    for days in audio_days:
        conn.execute("INSERT INTO audio_transcriptions(timestamp, transcription) VALUES (?, 'hi')", (_ts(days),))
    conn.commit()


def counts(conn: sqlite3.Connection) -> dict:
    out = {}
    for table in ("frames", "ocr_text", "audio_transcriptions"):
        out[table] = conn.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0]
    if ret.table_exists(conn, "elements"):
        out["elements"] = conn.execute("SELECT COUNT(*) FROM elements").fetchone()[0]
    return out


class PruneTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-ret-")
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.conn = make_db(self.dir / "db.sqlite")
        self.addCleanup(self.conn.close)

    def test_zero_days_is_off_and_writes_nothing(self):
        seed(self.conn, [40, 30, 1], [40])
        before = counts(self.conn)
        receipt = ret.prune(self.conn, retention_days=0, now=NOW, frame_marker=99, audio_marker=99)
        self.assertEqual(receipt["skipped"], "retention_off")
        self.assertIsNone(receipt["cutoff"])
        self.assertEqual(counts(self.conn), before)

    def test_deletes_only_exported_rows_older_than_cutoff(self):
        # frames: id1 40d, id2 30d, id3 20d (not exported), id4 1d ; audio: id1 40d, id2 2d
        seed(self.conn, [40, 30, 20, 1], [40, 2])
        receipt = ret.prune(self.conn, retention_days=7, now=NOW, frame_marker=2, audio_marker=1)
        self.assertEqual(receipt["deleted_frames"], 2)
        self.assertEqual(receipt["deleted_audio"], 1)
        self.assertEqual(receipt["eligible_frames"], 2)
        self.assertEqual(receipt["eligible_audio"], 1)
        self.assertFalse(receipt["budget_exhausted"])
        self.assertEqual(counts(self.conn), {"frames": 2, "ocr_text": 2, "audio_transcriptions": 1, "elements": 2})
        left = [r[0] for r in self.conn.execute("SELECT id FROM frames ORDER BY id")]
        self.assertEqual(left, [3, 4])   # id3 早于截止点但未导出 —— 一行不碰
        self.assertEqual(receipt["cutoff"], ret.cutoff_iso(NOW, 7))

    def test_missing_export_marker_deletes_nothing(self):
        seed(self.conn, [40, 30], [40])
        receipt = ret.prune(self.conn, retention_days=7, now=NOW, frame_marker=0, audio_marker=0)
        self.assertEqual((receipt["deleted_frames"], receipt["deleted_audio"]), (0, 0))
        self.assertEqual(counts(self.conn)["frames"], 2)

    def test_dry_run_counts_but_keeps_everything(self):
        seed(self.conn, [40, 30, 1], [40])
        receipt = ret.prune(self.conn, retention_days=7, now=NOW, frame_marker=9, audio_marker=9, dry_run=True)
        self.assertTrue(receipt["dry_run"])
        self.assertEqual((receipt["eligible_frames"], receipt["eligible_audio"]), (2, 1))
        self.assertEqual((receipt["deleted_frames"], receipt["deleted_audio"]), (0, 0))
        self.assertEqual(counts(self.conn)["frames"], 3)

    def test_batches_and_budget(self):
        seed(self.conn, [40] * 7, [40] * 3)
        receipt = ret.prune(self.conn, retention_days=7, now=NOW, frame_marker=99, audio_marker=99, batch=3)
        self.assertEqual(receipt["deleted_frames"], 7)
        self.assertEqual(receipt["deleted_audio"], 3)
        self.assertEqual(receipt["batches"], 3 + 1)      # frames 3+3+1, audio 3
        # 预算 0 秒：一笔都不删、如实报 budget_exhausted，下一轮接着
        seed(self.conn, [40], [])
        again = ret.prune(self.conn, retention_days=7, now=NOW, frame_marker=99, audio_marker=99, max_seconds=0.0)
        self.assertTrue(again["budget_exhausted"])
        self.assertEqual(again["deleted_frames"], 0)
        self.assertEqual(again["eligible_frames"], 1)

    def test_elements_table_optional(self):
        conn = make_db(self.dir / "plain.sqlite", with_elements=False)
        self.addCleanup(conn.close)
        seed(conn, [40, 1], [])
        receipt = ret.prune(conn, retention_days=7, now=NOW, frame_marker=9, audio_marker=9)
        self.assertEqual(receipt["deleted_frames"], 1)
        self.assertEqual(counts(conn), {"frames": 1, "ocr_text": 1, "audio_transcriptions": 0})

    def test_cutoff_is_utc_iso_prefix(self):
        self.assertEqual(ret.cutoff_iso(NOW, 7), "2025-08-30T23:06:40")

    def test_read_marker_tolerates_absence_and_garbage(self):
        self.assertEqual(ret.read_marker(self.dir, "nope"), 0)
        (self.dir / "last_frame_id").write_text("  42\n", encoding="utf-8")
        self.assertEqual(ret.read_marker(self.dir, "last_frame_id"), 42)
        (self.dir / "last_audio_id").write_text("abc", encoding="utf-8")
        self.assertEqual(ret.read_marker(self.dir, "last_audio_id"), 0)
        (self.dir / "neg").write_text("-5", encoding="utf-8")
        self.assertEqual(ret.read_marker(self.dir, "neg"), 0)


class RunTestCase(unittest.TestCase):
    """run()：旋钮 → 标记 → 清理 → 回执；永不抛。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-ret-run-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = self.root / "sp" / "db.sqlite"
        self.db.parent.mkdir()
        (self.db.parent / "export_markers").mkdir()
        self.state = self.root / "state"
        conn = make_db(self.db)
        seed(conn, [40, 30, 1], [40, 1])
        conn.close()

    def _marker(self, name: str, value: str) -> None:
        (self.db.parent / "export_markers" / name).write_text(value, encoding="utf-8")

    def test_run_prunes_with_markers_and_writes_receipt(self):
        self._marker("last_frame_id", "3")
        self._marker("last_audio_id", "2")
        receipt = ret.run(db_path=self.db, state_dir=self.state, days=7, now=NOW)
        self.assertIsNone(receipt["error"])
        self.assertEqual(receipt["deleted_frames"], 2)
        self.assertEqual(receipt["deleted_audio"], 1)
        self.assertEqual(receipt["frame_marker"], 3)
        self.assertEqual(receipt["ran_at"], "2025-09-06T23:06:40Z")
        self.assertIsInstance(receipt["db_bytes_after"], int)
        on_disk = json.loads((self.state / ret.RECEIPT_NAME).read_text(encoding="utf-8"))
        self.assertEqual(on_disk["deleted_frames"], 2)
        self.assertFalse((self.state / (ret.RECEIPT_NAME + ".tmp")).exists())

    def test_run_without_db_is_a_skip_not_an_error(self):
        receipt = ret.run(db_path=self.root / "missing.sqlite", state_dir=self.state, days=7, now=NOW)
        self.assertEqual(receipt["skipped"], "no_db")
        self.assertIsNone(receipt["error"])
        self.assertTrue((self.state / ret.RECEIPT_NAME).exists())

    def test_run_reads_retention_from_config_layer(self):
        self._marker("last_frame_id", "3")
        cfg = config.Config()
        cfg.screenpipe_retention_days = 7
        receipt = ret.run(db_path=self.db, state_dir=self.state, now=NOW, cfg=cfg)
        self.assertEqual(receipt["retention_days"], 7)
        self.assertEqual(receipt["deleted_frames"], 2)
        off = ret.run(db_path=self.db, state_dir=self.state, now=NOW, cfg=config.Config())
        self.assertEqual(off["skipped"], "retention_off")

    def test_run_never_raises(self):
        bad = self.root / "bad.sqlite"
        bad.write_text("this is not a database", encoding="utf-8")
        receipt = ret.run(db_path=bad, state_dir=self.state, days=7, now=NOW)
        self.assertIsNotNone(receipt["error"])
        self.assertIn("DatabaseError", receipt["error"])
        self.assertTrue((self.state / ret.RECEIPT_NAME).exists())

    def test_the_default_db_path_is_the_engine_s_own(self):
        """`--db` 缺席时（cron 那一形）指向引擎自己的库。**只读这一个纯函数**——
        判例绝不拿默认路径真跑一轮：owner 机器上那个库是真的（本文件的纪律）。"""
        self.assertEqual(ret.default_db_path(), Path.home() / ".screenpipe" / "db.sqlite")

    def test_a_receipt_that_cannot_be_written_only_adds_a_line_to_itself(self):
        """回执落不下去（state/ 的位置被占成了文件、只读卷、盘满）= 回执里多一句，
        清理本身照旧算完、stdout 那份仍然完整（§0 第 11 条）。"""
        self._marker("last_frame_id", "3")
        blocked = self.root / "state-is-a-file"
        blocked.write_text("x", encoding="utf-8")
        receipt = ret.run(db_path=self.db, state_dir=blocked, days=7, now=NOW)
        self.assertEqual(receipt["deleted_frames"], 2)        # 删照删
        self.assertIn("receipt_write_failed", receipt["error"])

    def test_a_db_that_cannot_be_stat_ed_after_the_prune_reports_a_null_size(self):
        """清理跑完那一下 `stat` 失败（库被引擎挪走、外置卷掉线——一轮最长 120 s，
        这是真会发生的窗口）= `db_bytes_after` 诚实为 null，而不是 0，也不是崩。"""
        self._marker("last_frame_id", "3")
        db, real_stat, seen = self.db, Path.stat, []

        def flaky_stat(self, *args, **kwargs):
            """同一个库文件的第二次 stat（prune 之后那一次）失败。"""
            if str(self) == str(db):
                seen.append(1)
                if len(seen) > 1:
                    raise OSError("EIO")
            return real_stat(self, *args, **kwargs)

        with mock.patch.object(Path, "stat", flaky_stat):
            receipt = ret.run(db_path=db, state_dir=self.state, days=7, now=NOW)
        self.assertIsNone(receipt["error"])
        self.assertEqual(receipt["deleted_frames"], 2)
        self.assertIsNone(receipt["db_bytes_after"])

    def test_cli_prints_one_json_line(self):
        self._marker("last_frame_id", "3")
        buf = io.StringIO()
        with redirect_stdout(buf), mock.patch.object(ret, "receipt_path", lambda _s=None: self.state / ret.RECEIPT_NAME), \
                mock.patch.object(ret.time, "time", lambda: NOW):
            rc = ret.main(["--dry-run", "--db", str(self.db), "--days", "7"])
        self.assertEqual(rc, 0)
        lines = buf.getvalue().splitlines()
        self.assertEqual(len(lines), 1)
        doc = json.loads(lines[0])
        self.assertTrue(doc["dry_run"])
        self.assertEqual(doc["eligible_frames"], 2)


class ConfigLayerTestCase(unittest.TestCase):
    """recording.retention_days ← settings_overrides.json 扁平键 screenpipe_retention_days；坏值宽容回默认。"""

    def test_default_is_keep_forever(self):
        self.assertEqual(config.Config().screenpipe_retention_days, 0)
        self.assertIn("screenpipe_retention_days", config._OVERRIDE_FIELDS)

    def test_yaml_and_override_layers(self):
        cfg = config.Config()
        config._apply_recording(cfg, {"recording": {"retention_days": "30"}})
        self.assertEqual(cfg.screenpipe_retention_days, 30)
        config._apply_recording(cfg, {"recording": {"retention_days": -3}})
        self.assertEqual(cfg.screenpipe_retention_days, 30)     # 负数 = 坏值，保留原值
        config._apply_recording(cfg, {"recording": {"retention_days": "lots"}})
        self.assertEqual(cfg.screenpipe_retention_days, 30)
        config._override_scalar(cfg, "screenpipe_retention_days", 14)
        self.assertEqual(cfg.screenpipe_retention_days, 14)
        self.assertEqual(ret.retention_days_from_config(cfg), 14)


if __name__ == "__main__":
    unittest.main()
