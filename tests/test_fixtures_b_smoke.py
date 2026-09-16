"""B 档场景 fixture 的冒烟判例（CONTRACT §58 QA 闸门；§77.3 B 档 fixture）。

清单真源 = ``qa/coverage_fixtures_b.json``（proof ``fixture:B-<nn>-<slug>``，
runner 按 ``python3 scripts/qa/fixtures_b/<slug>.py`` 的退出码判 PRESENT）。这里
把同一批 ``main()`` 在**进程内**跑一遍（不起子进程，防腐 #7）：

- 清单与 ``scripts/qa/fixtures_b/`` 下的脚本一一对应，谁也不多谁也不少；
- 每个 ``main()`` 回 0，且 stdout 的最后一行是一句 ≤200 字符的证据、以自己的
  id 开头、含 PASS；
- 跑完之后套件自己的沙箱 ``AIASSISTANT_HOME`` 与 ``act.lib.config`` 的路径常量
  逐字复原（夹具借走的世界必须还回来），临时 home 不留。
"""
import contextlib
import importlib
import io
import json
import os
import unittest
from pathlib import Path

from tests import TMP_HOME  # noqa: F401 - sandbox env before act imports

from act.lib import config

REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST = REPO_ROOT / "qa" / "coverage_fixtures_b.json"
FIXTURE_DIR = REPO_ROOT / "scripts" / "qa" / "fixtures_b"


def _rows() -> list:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def _module_name(row: dict) -> str:
    return "scripts.qa.fixtures_b." + row["id"].split("-", 2)[2].replace("-", "_")


class ManifestTestCase(unittest.TestCase):
    def test_manifest_shape_is_the_brief_schema(self):
        rows = _rows()
        self.assertGreaterEqual(len(rows), 12)
        self.assertEqual([r["id"] for r in rows], sorted(r["id"] for r in rows))
        for row in rows:
            self.assertEqual(set(row), {"id", "source", "scenario", "proof", "tier",
                                        "status", "waive_reason", "note"})
            self.assertEqual((row["source"], row["tier"], row["status"]),
                             ("fixtures_b", "B", "todo"))
            self.assertEqual(row["proof"], "fixture:" + row["id"])
            self.assertTrue(row["scenario"].strip())
            self.assertIsNone(row["waive_reason"])

    def test_every_script_is_declared_and_every_row_has_its_script(self):
        rows = _rows()
        declared = {Path(r["note"]).name for r in rows}
        on_disk = {p.name for p in FIXTURE_DIR.glob("*.py")
                   if not p.name.startswith("_")}
        self.assertEqual(declared, on_disk)
        for row in rows:
            self.assertTrue((REPO_ROOT / row["note"]).is_file(), row["id"])


class RunEveryFixtureTestCase(unittest.TestCase):
    """15 个场景在进程内各跑一次——绝不 spawn 子进程、绝不出网。"""

    def _run_one(self, row: dict) -> str:
        module = importlib.import_module(_module_name(row))
        self.assertEqual(module.FIXTURE_ID, row["id"])
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = module.main()
        line = buf.getvalue().strip().splitlines()[-1]
        self.assertEqual(code, 0, line)
        return line

    def test_every_fixture_passes_and_prints_one_evidence_line(self):
        home_before = os.environ.get("AIASSISTANT_HOME")
        paths_before = (config.HOME, config.STATE_DIR, config.REGISTRY_DIR)
        for row in _rows():
            with self.subTest(fixture=row["id"]):
                line = self._run_one(row)
                self.assertLessEqual(len(line), 200)
                self.assertTrue(line.startswith(row["id"] + " PASS "), line)
        # 夹具借走的世界还回来了吗（跑完之后套件其余判例还得在同一个沙箱里跑）
        self.assertEqual(os.environ.get("AIASSISTANT_HOME"), home_before)
        self.assertEqual((config.HOME, config.STATE_DIR, config.REGISTRY_DIR), paths_before)

    def test_no_temp_home_is_left_behind(self):
        leftovers_before = set(Path("/tmp").glob("zaa-cov-*"))
        self._run_one(_rows()[0])
        self.assertEqual(set(Path("/tmp").glob("zaa-cov-*")), leftovers_before)
