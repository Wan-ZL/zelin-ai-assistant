"""§57 变异 runner 的子进程草稿不泄漏（issue #436 第二条期望；§58.3 追记 2026-09-20）。

`mutate.run_subset` 给每个测试子进程一个 `mutate-home-*` 目录当 AIASSISTANT_HOME，并把
TMPDIR/TEMP/TMP 一起指进去，`finally` 里整树 rmtree——所以 killpg 收割的超时子进程也
漏不出一个目录。真 python 子进程，住 integration/（防腐 #7）。时间预算：BUDGET_SECONDS
兜底（一个亚秒级子进程 + 一个 3 秒超时被杀的子进程）。
"""
import importlib.util
import json
import os
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

_SPEC = importlib.util.spec_from_file_location(
    "qa_mutate_scratch_home",
    Path(__file__).resolve().parent.parent.parent / "scripts" / "qa" / "mutate.py",
)
mutate = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mutate)

BUDGET_SECONDS = 60

# 子进程里的判例：把它看到的临时目录根写回工作区，（可选）再睡到被收割。
_PROBE = (
    "import json, os, tempfile, unittest\n"
    "class Probe(unittest.TestCase):\n"
    "    def test_probe(self):\n"
    "        seen = {'tmpdir': os.environ.get('TMPDIR'), 'gettempdir': tempfile.gettempdir(),\n"
    "                'home': os.environ.get('AIASSISTANT_HOME')}\n"
    "        with open('seen.json', 'w', encoding='utf-8') as fh:\n"
    "            json.dump(seen, fh)\n"
    "        if os.environ.get('PROBE_SLEEP'):\n"
    "            import time; time.sleep(30)\n"
)


def _workspace(root):
    root = Path(root)
    (root / "t").mkdir()
    (root / "t" / "__init__.py").write_text("", encoding="utf-8")
    (root / "t" / "test_probe.py").write_text(_PROBE, encoding="utf-8")
    return root


def _wait_for(path, seconds=20):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        time.sleep(0.05)
    raise AssertionError("probe never wrote seen.json")


class ScratchHomeTestCase(unittest.TestCase):
    def setUp(self):
        self.start = time.monotonic()

    def tearDown(self):
        self.assertLess(time.monotonic() - self.start, BUDGET_SECONDS)

    def test_child_temp_root_is_the_per_run_home_and_it_is_removed(self):
        with TemporaryDirectory(prefix="mutate-ws-") as ws:
            root = _workspace(ws)
            status, _ = mutate.run_subset(root, [Path("t/test_probe.py")], timeout=60)
            self.assertEqual(status, "pass")
            seen = _wait_for(root / "seen.json")
        self.assertEqual(seen["tmpdir"], seen["home"])
        self.assertTrue(os.path.basename(seen["home"]).startswith("mutate-home-"), seen)
        self.assertEqual(os.path.abspath(seen["gettempdir"]), os.path.abspath(seen["home"]))
        self.assertFalse(os.path.exists(seen["home"]), "run_subset removed the per-run home")

    def test_a_killed_child_leaves_no_home_behind(self):
        with TemporaryDirectory(prefix="mutate-ws-") as ws:
            root = _workspace(ws)
            os.environ["PROBE_SLEEP"] = "1"
            self.addCleanup(os.environ.pop, "PROBE_SLEEP", None)
            status, _ = mutate.run_subset(root, [Path("t/test_probe.py")], timeout=3)
            self.assertEqual(status, "timeout")
            seen = _wait_for(root / "seen.json")
        self.assertTrue(os.path.basename(seen["home"]).startswith("mutate-home-"), seen)
        self.assertFalse(os.path.exists(seen["home"]), "the killed child's home was still removed")


if __name__ == "__main__":
    unittest.main()
