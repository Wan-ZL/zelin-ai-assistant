"""变异 runner 的杀伤判定判例（CONTRACT §57）—— 真 python 子进程，住
integration/（防腐 #7）。

剧本：一个 10-site 的小模块 + 一强一弱两份测试。强测试钉边界值 → 10/10 全歼；
弱测试只断言「非 None」→ 恰好 1 杀 9 存（唯一杀得死的是 return X → return None）
——这就是变异测试要暴露的「测试跑了但什么都没钉」的洞。随后同一 state 再跑
一轮 = 零执行（断点台账生效）；把弱测试改强再跑 = 旧账作废、全部重判
（B3：存活体名单必须跟着测试网走）。CLI 主入口全程写出 JSON + markdown 报告。

时间预算：BUDGET_SECONDS 兜底（~34 个子进程 unittest，每个亚秒级）。
"""
import importlib.util
import json
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

_SPEC = importlib.util.spec_from_file_location(
    "qa_mutate_integration",
    Path(__file__).resolve().parent.parent.parent
    / "scripts" / "qa" / "mutate.py",
)
mutate = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mutate)

BUDGET_SECONDS = 120

# 10 个 site：>=(1)、10±1(2)、return n+1 → None(1)、+(1)、1±1(2)、
# return 0 → None(1)、0±1(2)
_MODULE = ("def bonus(n):\n"
           "    if n >= 10:\n"
           "        return n + 1\n"
           "    return 0\n")

_STRONG = ("import unittest\n"
           "from mymod import bonus\n\n\n"
           "class StrongTestCase(unittest.TestCase):\n"
           "    def test_boundary_and_values(self):\n"
           "        self.assertEqual(bonus(10), 11)\n"
           "        self.assertEqual(bonus(9), 0)\n"
           "        self.assertEqual(bonus(11), 12)\n")

_WEAK = ("import unittest\n"
         "from mymod import bonus\n\n\n"
         "class WeakTestCase(unittest.TestCase):\n"
         "    def test_weak(self):\n"
         "        self.assertIsNotNone(bonus(50))\n")


def _make_project(root):
    root = Path(root)
    (root / "mymod.py").write_text(_MODULE, encoding="utf-8")
    tdir = root / "t"
    tdir.mkdir()
    (tdir / "__init__.py").write_text("", encoding="utf-8")
    (tdir / "test_strong.py").write_text(_STRONG, encoding="utf-8")
    (tdir / "test_weak.py").write_text(_WEAK, encoding="utf-8")


def _fresh_state():
    return {"schema": mutate.SCHEMA,
            "runner_version": mutate.RUNNER_VERSION, "modules": {}}


class KillDetectionTestCase(unittest.TestCase):
    def setUp(self):
        self.start = time.monotonic()

    def tearDown(self):
        self.assertLess(time.monotonic() - self.start, BUDGET_SECONDS)

    def test_strong_tests_kill_every_mutant(self):
        with TemporaryDirectory(prefix="mutate-int-") as root:
            _make_project(root)
            report, _state = mutate.run_targets(
                root, {"mymod.py": ["t/test_strong.py"]},
                budget_seconds=BUDGET_SECONDS, mutant_timeout=30,
                state=_fresh_state(), log=lambda *_: None)
        module = report["modules"]["mymod.py"]
        self.assertEqual(module["sites_total"], 10)
        self.assertEqual((module["killed"], module["survived"],
                          module["timeout"], module["score"]),
                         (10, 0, 0, 1.0))
        self.assertTrue(report["complete"])

    def test_weak_test_lets_mutants_survive_and_resume_reruns_nothing(self):
        with TemporaryDirectory(prefix="mutate-int-") as root:
            _make_project(root)
            state = _fresh_state()
            report, state = mutate.run_targets(
                root, {"mymod.py": ["t/test_weak.py"]},
                budget_seconds=BUDGET_SECONDS, mutant_timeout=30,
                state=state, log=lambda *_: None)
            module = report["modules"]["mymod.py"]
            # 弱测试只杀得死 return n+1 -> return None；其余 9 个全存活
            self.assertEqual((module["killed"], module["survived"]), (1, 9))
            ops = sorted(s["op"] for s in module["survivors"])
            self.assertIn("cmp_gte", ops)       # 边界没被钉
            self.assertIn("arith_add", ops)     # 数值没被钉
            for survivor in module["survivors"]:
                self.assertRegex(survivor["location"], r"^mymod\.py:\d+$")

            # 同一 state 再跑一轮：断点台账让第二轮零执行、零 spawn 成本
            report2, _state = mutate.run_targets(
                root, {"mymod.py": ["t/test_weak.py"]},
                budget_seconds=BUDGET_SECONDS, mutant_timeout=30,
                state=state, log=lambda *_: None)
            self.assertEqual(report2["executed_this_run"], 0)
            self.assertEqual(report2["modules"]["mymod.py"]["survived"], 9)
            self.assertTrue(report2["complete"])

    def test_strengthening_the_mapped_test_rejudges_old_survivors(self):
        # B3（v0.48.13 审查，empirically reproduced）：作废键只挂模块内容时，
        # 把弱测试改强永远不重跑（executed_this_run=0），夜报把 9 个早已
        # 杀得死的变异体继续当「测试网的洞」发布——P3/P5 修好了也摘不掉。
        # 修复后：映射测试文件的内容折进作废键，测试变强 = 该模块全部重判。
        with TemporaryDirectory(prefix="mutate-int-") as root:
            _make_project(root)
            state = _fresh_state()
            report, state = mutate.run_targets(
                root, {"mymod.py": ["t/test_weak.py"]},
                budget_seconds=BUDGET_SECONDS, mutant_timeout=30,
                state=state, log=lambda *_: None)
            self.assertEqual(report["modules"]["mymod.py"]["survived"], 9)

            # 夜里有人按 P3 的单子把弱测试补强（模块本身一字未动）
            (Path(root) / "t" / "test_weak.py").write_text(
                _STRONG.replace("StrongTestCase", "StrengthenedTestCase"),
                encoding="utf-8")
            report2, _state = mutate.run_targets(
                root, {"mymod.py": ["t/test_weak.py"]},
                budget_seconds=BUDGET_SECONDS, mutant_timeout=30,
                state=state, log=lambda *_: None)
        module = report2["modules"]["mymod.py"]
        self.assertEqual(report2["executed_this_run"], 10)  # 旧账作废全部重判
        self.assertEqual((module["killed"], module["survived"]), (10, 0))
        self.assertTrue(report2["complete"])

    def test_cli_main_writes_reports_and_state(self):
        with TemporaryDirectory(prefix="mutate-int-") as root:
            _make_project(root)
            qa = Path(root) / "qa"
            qa.mkdir()
            (qa / "mutation_targets.toml").write_text(
                '[config]\ntime_budget_seconds = 300\n'
                'per_mutant_timeout_seconds = 30\n'
                '[targets]\n"mymod.py" = ["t/test_weak.py"]\n',
                encoding="utf-8")
            rc = mutate.main(["--all", "--repo-root", root])
            self.assertEqual(rc, 0)  # 存活体不是失败（D5：报告型工具）
            report = json.loads((Path(root) / ".qa" / "mutation"
                                 / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["modules"]["mymod.py"]["survived"], 9)
            body = (Path(root) / ".qa" / "mutation"
                    / "report.md").read_text(encoding="utf-8")
            self.assertIn("Never a PR gate", body)
            state = json.loads((Path(root) / ".qa" / "mutation"
                                / "state.json").read_text(encoding="utf-8"))
            self.assertIn("mymod.py", state["modules"])


if __name__ == "__main__":
    unittest.main()
