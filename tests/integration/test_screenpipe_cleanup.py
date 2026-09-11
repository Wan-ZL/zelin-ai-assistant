"""ingest/screenpipe-cleanup.sh 行为判例（CONTRACT §71）——真 bash、真文件。

住在 tests/integration/（防腐 #7：真 IO 只许住这里；时间预算 BUDGET_SECONDS）。
脚本本人是真的；两个 env 缝（`ZAI_SCREENPIPE_DATA_DIR` / `ZAI_SCREENPIPE_PRUNE_RECEIPT`）
让它对着临时目录跑，`AIASSISTANT_HOME` 指到临时 home。不出网、不碰真 ~/.screenpipe。

钉住的行为：
  - 超过保留期的 `*.jpg` / `*.mp4` 删掉，年轻的与 `db.sqlite` 一根汗毛不动；
  - 保留期读 config 层（settings_overrides.json 的扁平键压过 config.yaml）；
  - 每轮留一条回执（ts / state / retention_minutes / 删了几个几字节）；
  - 数据目录不在 = `no_data_dir`、进不去 = `unreadable`（**与「删了 0 个」分开**——
    停掉的 prune 与没东西可删的 prune 从外面看一模一样，前一种会把盘吃满）；
  - **永远 exit 0**：cron 链是 `&&` 串的，prune 的毛病不许吞掉这一轮 ingest。
"""
import json
import os
import shutil
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


def setUpModule():
    _T0[0] = time.monotonic()


def tearDownModule():
    elapsed = time.monotonic() - _T0[0]
    if elapsed > BUDGET_SECONDS:
        raise AssertionError("tests/integration/test_screenpipe_cleanup.py took %.0fs > %ds budget"
                             % (elapsed, BUDGET_SECONDS))


@unittest.skipIf(sys.platform.startswith("win"), "bash scripts are POSIX-only")
class ScreenpipeCleanupTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="prune-")
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.home = base / "home"
        (self.home / "state").mkdir(parents=True)
        self.data = base / "screenpipe" / "data"
        self.data.mkdir(parents=True)
        self.receipt = self.home / "state" / "screenpipe_prune.json"

    def aged(self, name: str, minutes: int, size: int = 32) -> Path:
        p = self.data / name
        p.write_bytes(b"x" * size)
        when = time.time() - minutes * 60
        os.utime(p, (when, when))
        return p

    def run_script(self, *, data=None, env_extra=None):
        env = dict(os.environ)
        env.update({"AIASSISTANT_HOME": str(self.home),
                    "ZAI_SCREENPIPE_DATA_DIR": str(self.data if data is None else data),
                    "ZAI_SCREENPIPE_PRUNE_RECEIPT": str(self.receipt)})
        env.update(env_extra or {})
        proc = subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True,
                              text=True, timeout=30)
        return proc

    def receipt_doc(self) -> dict:
        return json.loads(self.receipt.read_text(encoding="utf-8"))

    def test_deletes_old_media_and_keeps_everything_else(self):
        old_jpg = self.aged("old.jpg", 240, 100)
        old_mp4 = self.aged("old.mp4", 240, 200)
        young = self.aged("young.jpg", 5, 50)
        db = self.aged("db.sqlite", 10_000, 4096)
        proc = self.run_script()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertFalse(old_jpg.exists())
        self.assertFalse(old_mp4.exists())
        self.assertTrue(young.exists())
        self.assertTrue(db.exists())        # 文本索引永不被这把旋钮碰
        doc = self.receipt_doc()
        self.assertEqual(doc["state"], "ok")
        self.assertEqual(doc["deleted_files"], 2)
        self.assertEqual(doc["deleted_bytes"], 300)
        self.assertEqual(doc["retention_minutes"], 60)
        self.assertTrue(doc["ts"].endswith("Z"))

    def test_retention_comes_from_the_config_layer(self):
        # override（web 那把旋钮写的扁平键）压过 config.yaml
        (self.home / "config.yaml").write_text(
            "recording:\n  media_retention_minutes: 600\n", encoding="utf-8")
        (self.home / "state" / "settings_overrides.json").write_text(
            json.dumps({"recording_media_retention_minutes": 120}), encoding="utf-8")
        keep = self.aged("keep.jpg", 90)     # 比 120 分钟年轻
        drop = self.aged("drop.jpg", 300)
        self.run_script()
        self.assertTrue(keep.exists())
        self.assertFalse(drop.exists())
        self.assertEqual(self.receipt_doc()["retention_minutes"], 120)

    def test_nothing_to_delete_still_leaves_a_receipt(self):
        self.aged("young.jpg", 1)
        self.run_script()
        doc = self.receipt_doc()
        self.assertEqual((doc["state"], doc["deleted_files"]), ("ok", 0))

    def test_missing_data_dir_is_its_own_state(self):
        proc = self.run_script(data=Path(self.tmp.name) / "nope")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(self.receipt_doc()["state"], "no_data_dir")

    # getattr：Windows 上没有 geteuid，而装饰器在 class 定义时就求值（整类的
    # skipIf 拦不住它）
    @unittest.skipIf(getattr(os, "geteuid", lambda: 1)() == 0,
                     "root reads everything; the mode gate is meaningless")
    def test_unreadable_data_dir_is_not_reported_as_a_clean_run(self):
        locked = Path(self.tmp.name) / "locked"
        locked.mkdir()
        (locked / "old.jpg").write_bytes(b"x")
        os.chmod(locked, 0o000)
        self.addCleanup(lambda: (os.chmod(locked, 0o700), shutil.rmtree(locked, ignore_errors=True)))
        proc = self.run_script(data=locked)
        self.assertEqual(proc.returncode, 0)   # 链子不许断
        self.assertEqual(self.receipt_doc()["state"], "unreadable")

    def test_receipt_is_rewritten_each_round(self):
        self.aged("a.jpg", 240)
        self.run_script()
        first = self.receipt_doc()
        self.aged("b.jpg", 240)
        self.run_script()
        second = self.receipt_doc()
        self.assertEqual(second["deleted_files"], 1)
        self.assertGreaterEqual(second["ts"], first["ts"])


if __name__ == "__main__":
    unittest.main()
