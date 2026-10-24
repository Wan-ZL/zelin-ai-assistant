"""ingest/screenpipe-cleanup.sh 第三步：每天一次的 a11y 瘦身（CONTRACT §72.5 / §18 / §78）——真 bash、真 sqlite3。

住在 tests/integration/（防腐 #7：真 IO 只许住这里；时间预算 BUDGET_SECONDS）。库是临时建的最小 schema
（frames / elements / ui_events 三张表里本步碰到的列），`ZAI_SCREENPIPE_DATA_DIR` 指到临时目录（库 =
它的上一级的 db.sqlite），`ZAI_SCREENPIPE_A11Y_RECEIPT` 指到临时回执——绝不打开这台机器真实的
~/.screenpipe/db.sqlite。

钉住的行为（SQL 原样来自 v2.0.0 前仓外的 ~/.local/bin/screenpipe-prune-a11y.sh，其 LaunchAgent
com.zelin.screenpipe-prune 随 §78 封闭足迹退役）：
  - 早于 1 天的帧：accessibility_tree_json / accessibility_text 置 NULL，它们的 source='accessibility'
    elements 删掉；OCR elements 与帧本身一行不动；1 天内的帧原样；早于 1 天的 ui_events 删掉；
  - 回执 state/screenpipe_a11y_prune.json：ts / date（本地日期）/ state=ok / rc / db / 前后计数；
  - **一天只试一次**：回执的 date 是今天就跳过（回执原样），是昨天就再跑；
  - 库不在 = `no_db`；表不对（另一个引擎版本）= `error` + 非零 rc，计数 null；
  - 无论哪种都 **exit 0**（cron 链是 `&&` 串的），媒体清理那一步照常出回执。
"""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from tests import TMP_HOME  # noqa: F401 - sandbox env first

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "ingest" / "screenpipe-cleanup.sh"
BUDGET_SECONDS = 60
_T0 = [time.monotonic()]

SCHEMA = """
CREATE TABLE frames (id INTEGER PRIMARY KEY, timestamp TEXT, accessibility_tree_json TEXT,
                     accessibility_text TEXT, ocr_marker TEXT);
CREATE TABLE elements (id INTEGER PRIMARY KEY, frame_id INTEGER, source TEXT, text TEXT);
CREATE TABLE ui_events (id INTEGER PRIMARY KEY, timestamp TEXT, kind TEXT);
"""


def setUpModule():
    _T0[0] = time.monotonic()


def tearDownModule():
    elapsed = time.monotonic() - _T0[0]
    if elapsed > BUDGET_SECONDS:
        raise AssertionError("tests/integration/test_cleanup_a11y_prune.py took %.0fs > %ds budget"
                             % (elapsed, BUDGET_SECONDS))


@unittest.skipIf(sys.platform.startswith("win"), "bash scripts are POSIX-only")
class CleanupA11yPruneTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="a11y-prune-")
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.home = base / "home"
        (self.home / "state").mkdir(parents=True)
        self.data = base / "screenpipe" / "data"
        self.data.mkdir(parents=True)
        self.db = base / "screenpipe" / "db.sqlite"
        self.receipt = self.home / "state" / "screenpipe_a11y_prune.json"

    def make_db(self, schema=SCHEMA):
        con = sqlite3.connect(str(self.db))
        con.executescript(schema)
        if schema is SCHEMA:
            old = "datetime('now','-2 days')"
            new = "datetime('now','-1 hour')"
            con.executescript(f"""
                INSERT INTO frames VALUES (1, {old}, '{{"tree":1}}', 'old a11y', 'ocr-1');
                INSERT INTO frames VALUES (2, {new}, '{{"tree":2}}', 'new a11y', 'ocr-2');
                INSERT INTO elements VALUES (1, 1, 'accessibility', 'old a11y el');
                INSERT INTO elements VALUES (2, 1, 'ocr', 'old ocr el');
                INSERT INTO elements VALUES (3, 2, 'accessibility', 'new a11y el');
                INSERT INTO ui_events VALUES (1, {old}, 'click');
                INSERT INTO ui_events VALUES (2, {new}, 'key');
            """)
        con.commit()
        con.close()

    def run_script(self):
        env = dict(os.environ)
        env.update({"AIASSISTANT_HOME": str(self.home),
                    "ZAI_SCREENPIPE_DATA_DIR": str(self.data),
                    "ZAI_SCREENPIPE_PRUNE_RECEIPT": str(self.home / "state" / "screenpipe_prune.json"),
                    "ZAI_SCREENPIPE_A11Y_RECEIPT": str(self.receipt)})
        return subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=60)

    def query(self, sql):
        con = sqlite3.connect(str(self.db))
        try:
            return con.execute(sql).fetchall()
        finally:
            con.close()

    def receipt_doc(self):
        return json.loads(self.receipt.read_text(encoding="utf-8"))

    def test_prunes_old_accessibility_data_and_keeps_ocr_and_recent_rows(self):
        self.make_db()
        proc = self.run_script()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.query("SELECT id, accessibility_tree_json, accessibility_text, ocr_marker "
                                    "FROM frames ORDER BY id"),
                         [(1, None, None, "ocr-1"), (2, '{"tree":2}', "new a11y", "ocr-2")])
        self.assertEqual(self.query("SELECT id FROM elements ORDER BY id"), [(2,), (3,)])
        self.assertEqual(self.query("SELECT id FROM ui_events ORDER BY id"), [(2,)])
        doc = self.receipt_doc()
        self.assertEqual(doc["state"], "ok")
        self.assertEqual(doc["rc"], 0)
        self.assertEqual(doc["date"], time.strftime("%Y-%m-%d"))
        self.assertEqual(doc["db"], str(self.db))
        self.assertEqual(doc["before"], {"a11y_frames": 2, "a11y_elements": 2, "ui_events": 2})
        self.assertEqual(doc["after"], {"a11y_frames": 1, "a11y_elements": 1, "ui_events": 1})
        self.assertTrue((self.home / "state" / "screenpipe_prune.json").is_file(), "media step still ran")

    def test_runs_once_per_local_day(self):
        self.make_db()
        self.run_script()
        first = self.receipt.read_text(encoding="utf-8")
        con = sqlite3.connect(str(self.db))
        con.execute("INSERT INTO ui_events VALUES (3, datetime('now','-3 days'), 'late')")
        con.commit()
        con.close()
        self.assertEqual(self.run_script().returncode, 0)
        self.assertEqual(self.receipt.read_text(encoding="utf-8"), first, "same day: skipped")
        self.assertEqual(self.query("SELECT id FROM ui_events ORDER BY id"), [(2,), (3,)])
        doc = json.loads(first)
        doc["date"] = "2000-01-01"
        self.receipt.write_text(json.dumps(doc, separators=(",", ":")) + "\n", encoding="utf-8")
        self.assertEqual(self.run_script().returncode, 0)
        self.assertEqual(self.query("SELECT id FROM ui_events ORDER BY id"), [(2,)], "a new day: ran again")
        self.assertEqual(self.receipt_doc()["date"], time.strftime("%Y-%m-%d"))

    def test_no_db_is_recorded_not_a_failure(self):
        proc = self.run_script()
        self.assertEqual(proc.returncode, 0)
        doc = self.receipt_doc()
        self.assertEqual(doc["state"], "no_db")
        self.assertIsNone(doc["before"])
        self.assertFalse(self.db.exists(), "the step never creates the engine's database")

    def test_an_unexpected_schema_is_an_error_receipt_and_still_exit_0(self):
        self.make_db("CREATE TABLE frames (id INTEGER PRIMARY KEY, timestamp TEXT);")
        proc = self.run_script()
        self.assertEqual(proc.returncode, 0)
        doc = self.receipt_doc()
        self.assertEqual(doc["state"], "error")
        self.assertNotEqual(doc["rc"], 0)
        self.assertIsNone(doc["before"])
        self.assertIsNone(doc["after"])


if __name__ == "__main__":
    unittest.main()
