"""§58.3 hygiene 门的 `mkdtemp:` 规则判例（issue #436）：tests/ 里的裸 mkdtemp 记账。

fixture 仓自带迷你 qa/gates.toml（与 test_qa_hygiene_caps 同款），判例不依赖真仓库
的数字。规则：白名单外的 tests/** 文件每处 `mkdtemp(...)`（`tempfile.mkdtemp` 与裸
`mkdtemp` 两形）计 1 分，键 = `mkdtemp:<文件>`；TemporaryDirectory 不算；act/ 等目录
不在本规则范围。最后一条对真仓库判：账本出生即零条，之后也只能是零。
"""
import os
import sys
import tempfile
import unittest

_QA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "qa")
if _QA_DIR not in sys.path:
    sys.path.insert(0, _QA_DIR)

import hygiene  # noqa: E402
import qa_common  # noqa: E402

_GATES = """\
[hygiene]
max_file_lines_py = 500
max_file_lines_swift = 500
max_function_lines = 500
max_class_lines = 500
"""


def _write(root, relpath, source):
    path = os.path.join(root, relpath)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(source)


def _scan(files):
    with tempfile.TemporaryDirectory() as root:
        _write(root, "qa/gates.toml", _GATES)
        for relpath, source in files.items():
            _write(root, relpath, source)
        return hygiene.scan(root)


class RawMkdtempTestCase(unittest.TestCase):
    def test_each_raw_call_counts_and_both_spellings_are_seen(self):
        source = ("import tempfile\nfrom tempfile import mkdtemp\n"
                  "a = tempfile.mkdtemp(prefix='x-')\n"
                  "b = mkdtemp()\n"
                  "def f(self):\n    return tempfile.mkdtemp()\n")
        scores = _scan({"tests/test_leaky.py": source})
        self.assertEqual(scores, {"mkdtemp:tests/test_leaky.py": 3.0})

    def test_subdirectories_of_tests_are_in_scope(self):
        source = "import tempfile\nx = tempfile.mkdtemp()\n"
        scores = _scan({"tests/integration/test_real.py": source})
        self.assertEqual(scores, {"mkdtemp:tests/integration/test_real.py": 1.0})

    def test_the_two_sanctioned_files_are_exempt(self):
        source = "import tempfile\nROOT = tempfile.mkdtemp(prefix='home-')\n"
        scores = _scan({"tests/__init__.py": source, "tests/scratch_testkit.py": source})
        self.assertEqual(scores, {})

    def test_temporary_directory_and_prose_mentions_are_not_violations(self):
        source = ('"""mkdtemp is banned here; use scratch_dir."""\n'
                  "import tempfile\n"
                  "with tempfile.TemporaryDirectory() as d:\n    pass\n"
                  "note = 'mkdtemp'\n")
        scores = _scan({"tests/test_clean.py": source})
        self.assertEqual(scores, {})

    def test_runtime_packages_are_outside_this_rule(self):
        source = '"""§1 管我。"""\nimport tempfile\nx = tempfile.mkdtemp()\n'
        scores = _scan({"act/lib/tool.py": source, "scripts/qa/tool.py": source})
        self.assertEqual(scores, {})

    def test_unparseable_test_file_does_not_crash_the_gate(self):
        scores = _scan({"tests/test_broken.py": "def (:\n  mkdtemp(\n"})
        self.assertEqual(scores, {})


class LiveRepoTestCase(unittest.TestCase):
    def test_the_live_tests_tree_has_no_raw_mkdtemp(self):
        scores = hygiene.scan(qa_common.REPO_ROOT)
        raw = sorted(k for k in scores if k.startswith("mkdtemp:"))
        self.assertEqual(raw, [], "use tests.scratch_testkit.scratch_dir (CONTRACT §58.3)")


if __name__ == "__main__":
    unittest.main()
