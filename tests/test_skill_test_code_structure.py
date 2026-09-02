"""test-code skill · 结构门判例（skills/test-code/scripts/structure_check.py + checks.check_structure）。

设计 = docs/design/vnext2-plan.md R2.8（第 1 档核心圈的确定性结构指标）；含负控制：
每条规则都用一个已知坏样本证明它会红，再用干净样本证明它会绿。零子进程。
"""

import os
import sys
import tempfile
import unittest

_SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "skills", "test-code", "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import checks  # noqa: E402
import ladder_common as lc  # noqa: E402
import structure_check as sc  # noqa: E402
from tests import skill_test_code_testkit as kit  # noqa: E402

CLEAN = {
    "pkg/__init__.py": "",
    "pkg/core.py": "import pkg.util\n",
    "pkg/util.py": "X = 1\n",
    "scripts/run.py": "import pkg.core\n",
    "tests/test_core.py": "def test_x():\n    assert True\n",
    "tests/test_util.py": "def test_y():\n    assert True\n",
}
CAPS = {"max_dir_depth": 3, "max_files_per_dir": 3}


def _reader(files):
    return lambda rel: files.get(rel, "")


class PlacementRulesTestCase(unittest.TestCase):
    def test_clean_layout_has_no_violations(self):
        violations, details, errors = sc.measure(sorted(CLEAN), _reader(CLEAN), "tests", CAPS)
        self.assertEqual((violations, errors), ({}, []))
        self.assertEqual(details["mirror_ratio"], 1.0)
        self.assertEqual(details["python_modules"], 6)

    def test_negative_control_test_outside_tests_dir(self):
        files = dict(CLEAN, **{"pkg/test_inline.py": "def test_z():\n    assert True\n"})
        violations, _, _ = sc.measure(sorted(files), _reader(files), "tests", CAPS)
        self.assertIn("tests-outside:pkg/test_inline.py", violations)
        self.assertEqual(sc.tests_outside(sorted(files), None), {}, "no tests dir declared = rule not applicable")

    def test_negative_control_duplicate_basename(self):
        files = dict(CLEAN, **{"other/util.py": "Y = 2\n", "other/__init__.py": "", "pkg/core.py": "import other.util\n"})
        violations, _, _ = sc.measure(sorted(files), _reader(files), "tests", CAPS)
        self.assertEqual(violations.get("dup-basename:util.py"), 2.0)
        self.assertNotIn("dup-basename:__init__.py", violations)

    def test_negative_control_depth_and_crowding(self):
        files = dict(CLEAN)
        files["a/b/c/d/deep.py"] = ""
        for i in range(4):
            files["pkg/f%d.py" % i] = "import pkg.core\n"
        violations, _, _ = sc.measure(sorted(files), _reader(files), "tests", CAPS)
        self.assertEqual(violations.get("depth:a/b/c/d/deep.py"), 4.0)
        self.assertGreater(violations.get("crowded-dir:pkg", 0), 3)
        # 测试目录根免检：平铺 10 个测试文件不算拥挤
        many = dict(CLEAN, **{"tests/test_%d.py" % i: "def test_a():\n    assert 1\n" for i in range(10)})
        v2, _, _ = sc.measure(sorted(many), _reader(many), "tests", CAPS)
        self.assertNotIn("crowded-dir:tests", v2)


class ImportGraphTestCase(unittest.TestCase):
    def test_module_names_and_resolution(self):
        self.assertEqual(sc.module_name("act/lib/x.py"), "act.lib.x")
        self.assertEqual(sc.module_name("pkg/__init__.py"), "pkg")
        known = {"pkg": 1, "pkg.util": 1}
        self.assertEqual(sc._resolve("pkg.util.deep", known), "pkg.util")
        self.assertIsNone(sc._resolve("json", known))

    def test_negative_control_cycle_detected(self):
        files = dict(CLEAN, **{"pkg/util.py": "import pkg.core\n"})
        violations, _, _ = sc.measure(sorted(files), _reader(files), "tests", CAPS)
        self.assertEqual(violations.get("cycle:pkg.core>pkg.util"), 2.0)
        graph, _ = sc.import_graph(_reader(CLEAN), [f for f in sorted(CLEAN) if f.endswith(".py")])
        self.assertEqual(sc.cycles(graph), {})

    def test_three_node_cycle_is_one_component(self):
        files = {"a.py": "import b\n", "b.py": "import c\n", "c.py": "import a\n", "d.py": "import a\n"}
        graph, errors = sc.import_graph(_reader(files), sorted(files))
        self.assertEqual(errors, [])
        self.assertEqual(sc.cycles(graph), {"cycle:a>b>c": 3.0})

    def test_negative_control_orphan_and_entrypoint_exemptions(self):
        files = dict(CLEAN, **{"pkg/lonely.py": "Z = 3\n", "pkg/cli.py": "if __name__ == '__main__':\n    pass\n"})
        violations, _, _ = sc.measure(sorted(files), _reader(files), "tests", CAPS)
        self.assertIn("orphan:pkg/lonely.py", violations)
        self.assertNotIn("orphan:pkg/cli.py", violations, "__main__ guard = entrypoint")
        self.assertNotIn("orphan:scripts/run.py", violations, "scripts/ = entrypoint dir")
        self.assertNotIn("orphan:pkg/__init__.py", violations)

    def test_relative_imports_resolve_so_submodules_are_not_orphans(self):
        """跨项目实跑（itsdangerous）：`from ._json import x` 让 _json.py 被误报孤儿；包 __init__ 的
        `.sub` 与模块里的 `..sibling` 都要解析到绝对名。docs/conf.py（Sphinx）免检。"""
        files = {
            "pkg/__init__.py": "from ._json import dumps\nfrom .core import run\n",
            "pkg/_json.py": "def dumps():\n    return 1\n",
            "pkg/core.py": "from . import _json\nfrom ..top import T\n",
            "top.py": "T = 1\n",
            "docs/conf.py": "project = 'x'\n",
            "tests/test_core.py": "import pkg\n",
        }
        graph, stems, errors = sc.scan_imports(_reader(files), sorted(f for f in files if f.endswith(".py")))
        self.assertEqual(errors, [])
        self.assertEqual(graph["pkg"], {"pkg._json", "pkg.core"})
        self.assertEqual(graph["pkg.core"], {"pkg", "pkg._json", "top"}, "`from . import _json` also imports the package")
        violations, _, _ = sc.measure(sorted(files), _reader(files), "tests", CAPS)
        self.assertEqual([k for k in violations if k.startswith("orphan:")], [])
        self.assertEqual(sc._relative_base("pkg.sub.mod", False, 1), "pkg.sub")
        self.assertEqual(sc._relative_base("pkg.sub.mod", False, 2), "pkg")
        self.assertEqual(sc._relative_base("pkg.sub", True, 1), "pkg.sub")

    def test_syntax_error_is_an_error_not_a_pass(self):
        files = dict(CLEAN, **{"pkg/broken.py": "def (:\n"})
        _, _, errors = sc.measure(sorted(files), _reader(files), "tests", CAPS)
        self.assertEqual(len(errors), 1)
        self.assertIn("pkg/broken.py", errors[0])

    def test_mirror_ratio(self):
        files = dict(CLEAN)
        del files["tests/test_util.py"]
        self.assertEqual(sc.mirror_ratio(sorted(f for f in files if f.endswith(".py")), "tests"), 0.5,
                         "scripts/ entrypoints are not expected to be mirrored")
        self.assertIsNone(sc.mirror_ratio(["a.py"], None))


class CheckStructureTestCase(unittest.TestCase):
    def _run(self, files, init=False, caps=None):
        tmp = tempfile.mkdtemp()
        kit.make_repo(tmp, files)
        det = kit.fake_det(sorted(files))
        det["thresholds"]["structure"] = caps or CAPS
        ctx = checks.make_ctx(tmp, det, {}, os.path.join(tmp, "out"), init_baselines=init)
        return checks.check_structure(ctx), tmp

    def test_clean_repo_passes_and_reports_mirror_ratio(self):
        res, _ = self._run(CLEAN)
        self.assertEqual(res["status"], "pass")
        self.assertEqual(res["details"]["mirror_ratio"], 1.0)
        self.assertEqual(res["details"]["caps"], CAPS)

    def test_negative_control_cycle_fails_then_baseline_grandfathers(self):
        files = dict(CLEAN, **{"pkg/util.py": "import pkg.core\n"})
        res, tmp = self._run(files)
        self.assertEqual(res["status"], "fail")
        self.assertIn("cycle:pkg.core>pkg.util", res["details"]["new"])
        # --init-baselines 把今天的账记下来 → 第二次跑同一份代码 = pass（只许缩小）
        det = kit.fake_det(sorted(files))
        det["thresholds"]["structure"] = CAPS
        ctx = checks.make_ctx(tmp, det, {}, os.path.join(tmp, "out"), init_baselines=True)
        self.assertEqual(checks.check_structure(ctx)["status"], "pass")
        ledger = lc.load_ledger(os.path.join(tmp, ".test-code", "baselines", "structure.txt"))
        self.assertIn("cycle:pkg.core>pkg.util", ledger)
        ctx = checks.make_ctx(tmp, det, {}, os.path.join(tmp, "out"))
        self.assertEqual(checks.check_structure(ctx)["status"], "pass")

    def test_unparseable_file_fails_closed(self):
        res, _ = self._run(dict(CLEAN, **{"pkg/broken.py": "def (:\n"}))
        self.assertEqual(res["status"], "fail")
        self.assertIn("fail closed", res["summary"])

    def test_structure_is_a_core_tier1_check_and_menu_carries_circle(self):
        entry = checks.BY_ID["structure"]
        self.assertEqual((entry["tier"], entry["circle"]), (1, "core"))
        self.assertIn("structure", checks.default_checks(kit.fake_det(sorted(CLEAN)), 1))
        self.assertNotIn("type_coverage", checks.default_checks(kit.fake_det(sorted(CLEAN)), 5),
                         "extended circle is never pre-selected")
        menu = checks.build_menu(checks.make_ctx("/r", kit.fake_det(sorted(CLEAN))))
        self.assertEqual({row["circle"] for row in menu}, {"core", "extended"})


class ExtendedBuildersTestCase(unittest.TestCase):
    """扩展圈 builder：探不到表面 = na，工具缺 = unavailable，都在 = cmd/substituted。"""

    def _ctx(self, files, tools=None, pkgs=None):
        det = kit.fake_det(sorted(files))
        det["tools"] = tools or {}
        if pkgs is not None:
            det["layout"]["js_packages"] = pkgs
        return checks.make_ctx("/r", det)

    def test_type_coverage_kinds(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit.make_repo(tmp, {"a.py": "", "pyproject.toml": "[tool.mypy]\nstrict = true\n"})
            det = kit.fake_det(["a.py", "pyproject.toml"])
            ctx = checks.make_ctx(tmp, det)
            self.assertEqual(checks._b_type_coverage(ctx)["kind"], "unavailable")
            det["tools"] = {"mypy": "/bin/mypy"}
            self.assertEqual(checks._b_type_coverage(ctx)["kind"], "cmd")
            kit.make_repo(tmp, {"pyproject.toml": "[tool.ruff]\n"})
            self.assertEqual(checks._b_type_coverage(ctx)["kind"], "na")

    def test_duplication_api_bundle_license_doc_kinds(self):
        ctx = self._ctx(["a.py"])
        self.assertEqual(checks._b_duplication(ctx)["kind"], "unavailable")
        self.assertEqual(checks._b_duplication(self._ctx(["a.py"], tools={"pylint": "/bin/pylint"}))["kind"], "cmd")
        self.assertEqual(checks._b_api_breaking(ctx)["kind"], "na")
        self.assertEqual(checks._b_bundle_size(ctx)["kind"], "na")
        pkg = {"dir": "web", "bins": ["size-limit", "jscpd"], "scripts": {}}
        self.assertEqual(checks._b_bundle_size(self._ctx(["web/package.json"], pkgs=[pkg]))["kind"], "cmd")
        self.assertEqual(checks._b_duplication(self._ctx(["web/package.json"], pkgs=[pkg]))["kind"], "cmd")
        self.assertEqual(checks._b_license_check(ctx)["kind"], "unavailable")
        lic = checks._b_license_check(self._ctx(["a.py"], tools={"pip-licenses": "/bin/pl"}))
        self.assertEqual(lic["kind"], "substituted", "inventory without an allowlist is never a pass")
        self.assertEqual(checks._b_doc_coverage(ctx)["kind"], "unavailable")
        self.assertEqual(checks._b_doc_coverage(self._ctx(["a.py"], tools={"interrogate": "/bin/i"}))["kind"], "cmd")

    def test_clean_install_and_feedback_channel(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit.make_repo(tmp, {"a.py": ""})
            ctx = checks.make_ctx(tmp, kit.fake_det(["a.py"]))
            self.assertEqual(checks._b_clean_install(ctx)["kind"], "unavailable")
            kit.make_repo(tmp, {"scripts/clean-vm-install.sh": "#!/bin/bash\n"})
            self.assertEqual(checks._b_clean_install(ctx)["kind"], "cmd")
            res = checks.check_feedback_channel(ctx)
            self.assertEqual(res["status"], "pass", "informational: never red")
            self.assertTrue(res["details"]["blind_spots"])
            ctx2 = checks.make_ctx(tmp, kit.fake_det(["a.py", ".github/ISSUE_TEMPLATE/bug.md"]))
            self.assertEqual(checks.check_feedback_channel(ctx2)["details"]["blind_spots"], []) if "blind_spots" in \
                checks.check_feedback_channel(ctx2)["details"] else None
            self.assertTrue(checks.check_feedback_channel(ctx2)["details"]["found"])


if __name__ == "__main__":
    unittest.main()
