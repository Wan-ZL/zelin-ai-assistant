"""§58.3 依赖方向规则的判例（fixture 包，不扫真仓库）。

规则：act/lib 只准 stdlib+yaml+act.lib；act 顶层（entrypoint 层）互相不
import；server 只准 act.lib + server；任何模块不准跨模块引 `_私名`
（from X import _y 与 X._y 属性链两形）。
"""
import os
import sys
import tempfile
import unittest

_QA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "qa")
if _QA_DIR not in sys.path:
    sys.path.insert(0, _QA_DIR)

import depgraph  # noqa: E402


def _write(root, relpath, source):
    path = os.path.join(root, relpath)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(source)


def _fixture(files):
    tmp = tempfile.TemporaryDirectory()
    base = {
        "act/__init__.py": "",
        "act/lib/__init__.py": "",
        "act/lib/other.py": "",
        "act/executor.py": "",
        "act/analyze.py": "",
    }
    base.update(files)
    for relpath, source in base.items():
        _write(tmp.name, relpath, source)
    return tmp


class DirectionRuleTestCase(unittest.TestCase):
    def _scan(self, files):
        with _fixture(files) as root:
            return set(depgraph.scan(root))

    def test_lib_importing_stdlib_yaml_and_lib_siblings_is_clean(self):
        keys = self._scan({"act/lib/good.py":
                           "import os\nimport yaml\nfrom act.lib import other\n"})
        self.assertEqual(keys, set())

    def test_lib_importing_an_entrypoint_is_flagged(self):
        keys = self._scan({"act/lib/bad.py": "import act.executor\n"})
        self.assertEqual(keys, {"lib-import:act/lib/bad.py->act.executor"})

    def test_relative_import_escaping_lib_is_flagged(self):
        keys = self._scan({"act/lib/rel.py": "from ..executor import dispatch\n"})
        self.assertEqual(keys, {"lib-import:act/lib/rel.py->act.executor"})

    @unittest.skipUnless(getattr(sys, "stdlib_module_names", None),
                         "第三方判定需要 sys.stdlib_module_names（3.10+）")
    def test_lib_importing_an_unknown_third_party_is_flagged(self):
        keys = self._scan({"act/lib/third.py": "import requests\n"})
        self.assertEqual(keys, {"lib-thirdparty:act/lib/third.py->requests"})

    def test_entrypoints_may_use_lib_but_not_each_other(self):
        keys = self._scan({"act/actd.py":
                           "from act.lib import other\nimport act.analyze\n"})
        self.assertEqual(keys, {"entry-pair:act/actd.py->act.analyze"})

    def test_lazy_import_inside_a_function_still_counts(self):
        keys = self._scan({"act/actd.py":
                           "def go():\n    import act.analyze\n    return act.analyze\n"})
        self.assertEqual(keys, {"entry-pair:act/actd.py->act.analyze"})

    def test_entrypoints_may_import_the_llm_boundary(self):
        """§58.3 规则 2 的法定例外（§59 / 防腐 #3）：act.llm 是所有带 prompt 的
        claude 调用必经的边界——entrypoint 层 import 它不是互引；lib 层仍不许。"""
        self.assertEqual(depgraph.BOUNDARY_MODULES, frozenset({"act.llm"}))
        keys = self._scan({"act/llm.py": "from act.lib import other\n",
                           "act/recap.py": "from act import llm\nimport act.llm\n",
                           "act/lib/bad.py": "from act import llm\n"})
        self.assertEqual(keys, {"lib-import:act/lib/bad.py->act.llm"})

    def test_the_llm_boundary_itself_may_not_import_other_entrypoints(self):
        keys = self._scan({"act/llm.py": "import act.executor\n"})
        self.assertEqual(keys, {"entry-pair:act/llm.py->act.executor"})

    def test_server_may_import_lib_and_itself_but_not_entrypoints(self):
        keys = self._scan({
            "server/paths.py": "",
            "server/app.py": ("import os\nfrom act.lib import other\n"
                              "from server import paths\nimport act.executor\n"),
        })
        self.assertEqual(keys, {"server-import:server/app.py->act.executor"})


class PrivateNameRuleTestCase(unittest.TestCase):
    def _scan(self, files):
        with _fixture(files) as root:
            return set(depgraph.scan(root))

    def test_from_import_of_a_private_name_is_flagged(self):
        keys = self._scan({"act/lib/bad.py":
                           "from act.executor import _runner_env\n"})
        self.assertEqual(keys, {
            "lib-import:act/lib/bad.py->act.executor",
            "private:act/lib/bad.py->act.executor._runner_env",
        })

    def test_attribute_chain_private_access_is_flagged(self):
        keys = self._scan({"act/digest.py":
                           "from act.lib import other\nvalue = other._secret\n"})
        self.assertEqual(keys, {"private:act/digest.py->act.lib.other._secret"})

    def test_dotted_module_attribute_private_access_is_flagged(self):
        keys = self._scan({"act/lib/peek.py":
                           "import act.lib.other\nvalue = act.lib.other._x\n"})
        self.assertEqual(keys, {"private:act/lib/peek.py->act.lib.other._x"})

    def test_dunder_names_and_object_attributes_are_not_private(self):
        keys = self._scan({"act/digest.py": (
            "from act.lib.other import __doc__ as d\n"
            "class C:\n"
            "    def go(self):\n"
            "        return self._own\n")})
        self.assertEqual(keys, set())


if __name__ == "__main__":
    unittest.main()
