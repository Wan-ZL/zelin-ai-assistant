"""act/lib/screenpipe_retention.py 的变异残存补杀（CONTRACT §72.2；§18 cron 链 cleanup 步）。

夜间变异跑（§57）在这个模块上留了一批活口，全都落在「清理是一把会删用户数据的刀」
的安全面上：关（0）与开（1）的边界、导出标记的下限钳位、单笔事务的锁窗上限、
时间预算「到点即停」的那一下、以及回执自身的诚实（零字段齐全、错误只多一句、
UTF-8 原文 + 键有序落盘）。本文件逐条把它们钉成判例，与
tests/test_screenpipe_retention.py 的既有断言互不重叠。

判据一律写字面量而不是读模块常量——读常量的断言会跟着变异体一起挪，等于没钉。
"""
import datetime as _dt
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

NOW = 1_757_200_000.0
DAY = 86400.0


def _ts(offset_days: float) -> str:
    stamp = _dt.datetime.fromtimestamp(NOW - offset_days * DAY, _dt.timezone.utc)
    return stamp.strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")


def _db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.executescript("""
        CREATE TABLE frames (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TIMESTAMP NOT NULL);
        CREATE TABLE ocr_text (frame_id INTEGER NOT NULL, text TEXT NOT NULL);
        CREATE TABLE audio_transcriptions (id INTEGER PRIMARY KEY AUTOINCREMENT,
                                           timestamp TIMESTAMP NOT NULL, transcription TEXT NOT NULL);
    """)
    return conn


def _seed(conn: sqlite3.Connection, frame_days=(), audio_days=()) -> None:
    for days in frame_days:
        cur = conn.execute("INSERT INTO frames(timestamp) VALUES (?)", (_ts(days),))
        conn.execute("INSERT INTO ocr_text(frame_id, text) VALUES (?, 'x')", (cur.lastrowid,))
    for days in audio_days:
        conn.execute("INSERT INTO audio_transcriptions(timestamp, transcription) VALUES (?, 'x')",
                     (_ts(days),))
    conn.commit()


class TempDirCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-ret-mk-")
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)


# --------------------------------------------------------------------------- #
# 刀的边界：开 / 关、标记钳位、锁窗、时间预算
# --------------------------------------------------------------------------- #
class DeleteBoundaryTestCase(TempDirCase):
    def _conn(self, frame_days=(), audio_days=()):
        conn = _db(self.dir / "db.sqlite")
        self.addCleanup(conn.close)
        _seed(conn, frame_days, audio_days)
        return conn

    def test_a_single_transaction_never_locks_more_than_two_thousand_frames(self):
        """§72.2 锁窗：引擎同时在写，一笔事务最多 2000 条 —— 多一条都要下一笔。"""
        conn = self._conn(frame_days=[9] * 2001)
        cutoff = ret.cutoff_iso(NOW, 1)
        self.assertEqual(ret.delete_frames_batch(conn, cutoff, 2001), 2000)
        self.assertEqual(ret.delete_frames_batch(conn, cutoff, 2001), 1)
        self.assertEqual(ret.delete_frames_batch(conn, cutoff, 2001), 0)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM ocr_text").fetchone()[0], 0)

    def test_prune_asks_the_engines_db_for_a_thirty_second_busy_timeout(self):
        """§72.2：引擎在写，删的这一侧必须肯等 30 秒再放弃（默认 5 秒不够）。"""
        conn = self._conn(frame_days=[9], audio_days=[9])
        self.assertEqual(conn.execute("PRAGMA busy_timeout").fetchone()[0], 5000)
        ret.prune(conn, retention_days=1, now=NOW, frame_marker=9, audio_marker=9)
        self.assertEqual(conn.execute("PRAGMA busy_timeout").fetchone()[0], 30000)

    def test_one_day_is_already_on_and_zero_is_still_off(self):
        """§72.2 的开关边界：``0`` = 永久保留（出厂默认），``1`` = 已经在删。"""
        conn = self._conn(frame_days=[3, 0.1])
        off = ret.prune(conn, retention_days=0, now=NOW, frame_marker=9, audio_marker=9)
        self.assertEqual(off["skipped"], "retention_off")
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM frames").fetchone()[0], 2)
        on = ret.prune(conn, retention_days=1, now=NOW, frame_marker=9, audio_marker=9)
        self.assertIsNone(on["skipped"])
        self.assertEqual(on["deleted_frames"], 1)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM frames").fetchone()[0], 1)

    def test_retention_off_receipt_is_all_zeros_and_writes_nothing(self):
        conn = self._conn(frame_days=[9])
        self.assertEqual(
            ret.prune(conn, retention_days=0, now=NOW, frame_marker=7, audio_marker=8),
            {"retention_days": 0, "frame_marker": 7, "audio_marker": 8, "dry_run": False,
             "deleted_frames": 0, "deleted_audio": 0, "batches": 0,
             "budget_exhausted": False, "cutoff": None, "skipped": "retention_off"})

    def test_nothing_eligible_counts_as_zero_not_as_a_placeholder(self):
        """``plan`` 的数字直接进回执与 ``--dry-run``：没有就是 0，不是 -1 / 1。"""
        conn = self._conn(frame_days=[0.1], audio_days=[0.1])
        self.assertEqual(ret.plan(conn, ret.cutoff_iso(NOW, 1), 9, 9),
                         {"eligible_frames": 0, "eligible_audio": 0})

    def test_the_budget_stops_the_drain_the_moment_it_is_gone(self):
        """§72.2 时间预算：到点即停 —— 不再开新事务，且回执照实说预算用尽。"""
        conn = self._conn(frame_days=[9, 9], audio_days=[9])
        with mock.patch.object(ret.time, "monotonic", return_value=500.0):
            receipt = ret.prune(conn, retention_days=1, now=NOW, frame_marker=9,
                                audio_marker=9, max_seconds=0.0)
        self.assertEqual(receipt["deleted_frames"], 0)
        self.assertEqual(receipt["deleted_audio"], 0)
        self.assertEqual(receipt["batches"], 0)
        self.assertTrue(receipt["budget_exhausted"])
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM frames").fetchone()[0], 2)

    def test_either_half_running_out_of_budget_is_reported_as_exhausted(self):
        """帧删完了但音频没轮到 = 这一轮**没干完**，下一轮 cron 得接着删（§0 第 3 条）。"""
        conn = self._conn(audio_days=[9])
        clock = mock.Mock(side_effect=[100.0, 100.0, 300.0])
        with mock.patch.object(ret.time, "monotonic", clock):
            receipt = ret.prune(conn, retention_days=1, now=NOW, frame_marker=0,
                                audio_marker=9, max_seconds=10.0)
        self.assertTrue(receipt["budget_exhausted"])
        self.assertEqual(receipt["deleted_audio"], 0)

    def test_an_absent_or_broken_export_marker_deletes_nothing(self):
        """§72.2 的回程票：``id <= 导出标记``；标记缺席 / 坏形 / 负数一律钳到 0 = 一行不删。"""
        markers = self.dir / "export_markers"
        markers.mkdir()
        self.assertEqual(ret.read_marker(markers, "missing"), 0)
        for raw in ("", "   ", "abc", "-5", "0"):
            (markers / "last_frame_id").write_text(raw, encoding="utf-8")
            self.assertEqual(ret.read_marker(markers, "last_frame_id"), 0, raw)
        conn = self._conn(frame_days=[9])
        receipt = ret.prune(conn, retention_days=1, now=NOW, frame_marker=0, audio_marker=0)
        self.assertEqual(receipt["deleted_frames"], 0)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM frames").fetchone()[0], 1)


# --------------------------------------------------------------------------- #
# 旋钮：config 层与 --days 的钳位（负数 / 缺席都不许变成「删一切」或「偷偷开启」）
# --------------------------------------------------------------------------- #
class KnobTestCase(TempDirCase):
    def test_config_knob_clamps_to_zero_and_zero_stays_off(self):
        self.assertEqual(ret.retention_days_from_config(config.Config(screenpipe_retention_days=0)), 0)
        self.assertEqual(ret.retention_days_from_config(config.Config(screenpipe_retention_days=-5)), 0)
        self.assertEqual(ret.retention_days_from_config(object()), 0)   # 没有这一属性的假 cfg
        self.assertEqual(ret.retention_days_from_config(config.Config(screenpipe_retention_days=7)), 7)

    def test_days_override_zero_is_off_and_negative_clamps_to_zero(self):
        db = self.dir / "db.sqlite"
        conn = _db(db)
        _seed(conn, frame_days=[9])
        conn.close()
        (db.parent / "export_markers").mkdir()
        (db.parent / "export_markers" / "last_frame_id").write_text("9", encoding="utf-8")
        off = ret.run(db_path=db, state_dir=self.dir / "state", days=0, now=NOW)
        self.assertEqual(off["skipped"], "retention_off")
        self.assertEqual(off["retention_days"], 0)
        self.assertEqual(off["deleted_frames"], 0)
        negative = ret.run(db_path=db, state_dir=self.dir / "state", days=-3, now=NOW)
        self.assertEqual(negative["retention_days"], 0)
        self.assertEqual(negative["skipped"], "retention_off")
        conn = sqlite3.connect(str(db))
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM frames").fetchone()[0], 1)
        conn.close()

    def test_default_db_path_is_the_engines_own_database(self):
        self.assertEqual(ret.default_db_path(), Path.home() / ".screenpipe" / "db.sqlite")

    def test_skipped_receipt_carries_the_whole_zero_shape(self):
        """server ``GET /api/screenpipe/disk`` 原样投影这份回执——字段一个都不许缺。"""
        self.assertEqual(ret.prune_skipped(4, "no_db"),
                         {"retention_days": 4, "skipped": "no_db", "deleted_frames": 0,
                          "deleted_audio": 0, "batches": 0, "budget_exhausted": False,
                          "cutoff": None, "dry_run": False})


# --------------------------------------------------------------------------- #
# 回执：落盘形制与失败时的诚实
# --------------------------------------------------------------------------- #
class ReceiptTestCase(TempDirCase):
    def test_receipt_write_creates_missing_dirs_and_overwrites_in_place(self):
        path = self.dir / "state" / "deep" / ret.RECEIPT_NAME
        ret.write_receipt(path, {"deleted_frames": 1})
        ret.write_receipt(path, {"deleted_frames": 2})          # 目录已在 → 照样覆盖写
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"deleted_frames": 2})
        self.assertFalse((path.parent / (ret.RECEIPT_NAME + ".tmp")).exists())

    def test_receipt_lands_as_sorted_utf8_text_a_human_can_diff(self):
        path = self.dir / "state" / ret.RECEIPT_NAME
        receipt = {"skipped": None, "error": "打不开：权限", "deleted_frames": 0}
        ret.write_receipt(path, receipt)
        text = path.read_text(encoding="utf-8")
        self.assertEqual(text, json.dumps(receipt, ensure_ascii=False, indent=1, sort_keys=True))
        self.assertIn("打不开：权限", text)                      # 原文落盘，不是 \uXXXX
        self.assertLess(text.index('"deleted_frames"'), text.index('"error"'))
        self.assertIn('\n "deleted_frames": 0,', text)          # 一键一行、缩进一格

    def test_a_receipt_that_cannot_be_written_only_adds_one_sentence(self):
        """写不进去是**多一句**，不是把 error 变成字符串 "None"（回执仍要能被读懂）。"""
        (self.dir / "blocker").write_text("not a dir", encoding="utf-8")
        receipt = ret.run(db_path=self.dir / "nope.sqlite", days=0,
                          state_dir=self.dir / "blocker" / "state", now=NOW)
        self.assertEqual(receipt["skipped"], "no_db")
        self.assertFalse(receipt["error"].startswith("None"))
        self.assertTrue(receipt["error"].strip().startswith("receipt_write_failed:"))

    def test_duration_counts_forward_and_is_rounded_to_milliseconds(self):
        clock = mock.Mock(side_effect=[1000.0, 1000.0625])
        with mock.patch.object(ret.time, "monotonic", clock):
            receipt = ret.run(db_path=self.dir / "nope.sqlite", state_dir=self.dir / "state",
                              days=0, now=NOW)
        self.assertEqual(receipt["duration_s"], 0.062)

    def test_cli_prints_one_sorted_utf8_json_line(self):
        db = self.dir / "录制" / "db.sqlite"
        db.parent.mkdir()
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(ret.main(["--db", str(db), "--days", "0"]), 0)
        line = buf.getvalue()
        self.assertTrue(line.endswith("\n"))
        self.assertEqual(line.count("\n"), 1)
        self.assertIn("录制", line)                              # UTF-8 原文，不转义
        doc = json.loads(line)
        self.assertEqual(line.strip(), json.dumps(doc, ensure_ascii=False, sort_keys=True))
