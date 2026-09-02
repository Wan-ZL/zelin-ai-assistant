"""§58.3 hygiene 门的判例：行数上限（防腐 #1）+ 模块 docstring 引 §（防腐 #5）。

fixture 仓自带迷你 qa/gates.toml（hygiene.scan 从被扫仓读阈值），所以
判例不依赖真仓库的数字。
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

_GATES = """\
[hygiene]
max_file_lines_py = 10
max_file_lines_swift = 3
max_function_lines = 3
max_class_lines = 5
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


class LineCapTestCase(unittest.TestCase):
    def test_an_oversized_python_file_is_flagged_with_its_size(self):
        scores = _scan({"act/big.py": '"""§1 判例。"""\n' + "x = 1\n" * 11})
        self.assertEqual(scores, {"file-lines:act/big.py": 12.0})

    def test_an_oversized_function_is_flagged(self):
        source = '"""§1 判例。"""\ndef f(a):\n    a += 1\n    a += 2\n    return a\n'
        scores = _scan({"act/mod.py": source})
        self.assertEqual(scores, {"func-lines:act/mod.py::f": 4.0})

    def test_an_oversized_class_is_flagged(self):
        body = "".join("    x%d = %d\n" % (i, i) for i in range(5))
        scores = _scan({"act/mod.py": '"""§1 判例。"""\nclass C:\n' + body})
        self.assertEqual(scores, {"class-lines:act/mod.py::C": 6.0})

    def test_swift_caps_apply_to_shell_only_mac_is_exempt(self):
        swift = "let a = 1\nlet b = 2\nlet c = 3\nlet d = 4\n"
        scores = _scan({"shell/App.swift": swift, "mac/Huge.swift": swift})
        self.assertEqual(scores, {"file-lines:shell/App.swift": 4.0})


class DocstringSectionTestCase(unittest.TestCase):
    def test_a_module_without_a_contract_section_is_flagged(self):
        scores = _scan({"act/mod.py": '"""无法典指针的模块。"""\n'})
        self.assertEqual(scores, {"docstring:act/mod.py": 1.0})

    def test_citing_a_section_satisfies_the_rule(self):
        scores = _scan({"act/mod.py": '"""管我的法条：§47.4。"""\n'})
        self.assertEqual(scores, {})

    def test_dunder_init_and_scripts_are_exempt(self):
        scores = _scan({
            "act/__init__.py": '__version__ = "0.0.0"\n',
            "scripts/tool.py": '"""无 § 也行——docstring 门只管 act/ 与 server/。"""\n',
        })
        self.assertEqual(scores, {})

    def test_server_modules_are_in_scope(self):
        scores = _scan({"server/app.py": '"""没有指针。"""\n'})
        self.assertEqual(scores, {"docstring:server/app.py": 1.0})


if __name__ == "__main__":
    unittest.main()
