"""§58.2 CRAP 公式与行段覆盖率映射的判例。

CRAP(f) = CC(f)² × (1 − cov(f))³ + CC(f)（owner D4：上限 6）；cov(f) =
函数行段内 coverage 认识的语句行里被执行的比例（qa_common.span_coverage），
crap.py 把 coverage JSON 按 AST 行段映射到函数。
"""
import ast
import json
import os
import sys
import tempfile
import textwrap
import unittest

_QA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "qa")
if _QA_DIR not in sys.path:
    sys.path.insert(0, _QA_DIR)

import crap  # noqa: E402
import qa_common  # noqa: E402


class CrapFormulaTestCase(unittest.TestCase):
    def test_fully_covered_function_scores_its_cc(self):
        # cov=1 → (1−cov)³ = 0 → CRAP = CC
        self.assertEqual(qa_common.crap_score(6, 1.0), 6)

    def test_uncovered_trivial_function(self):
        # CC=1, cov=0 → 1×1 + 1 = 2
        self.assertEqual(qa_common.crap_score(1, 0.0), 2)

    def test_half_covered_cc10(self):
        # 100 × 0.125 + 10 = 22.5
        self.assertEqual(qa_common.crap_score(10, 0.5), 22.5)

    def test_score_rounds_to_one_decimal(self):
        # 196 × (0.964)³ + 14 = 189.58… → 189.6（账本的可读粒度）
        self.assertEqual(qa_common.crap_score(14, 0.036), 189.6)

    def test_less_coverage_never_lowers_the_score(self):
        scores = [qa_common.crap_score(8, cov) for cov in (1.0, 0.75, 0.5, 0.25, 0.0)]
        self.assertEqual(scores, sorted(scores))


class SpanCoverageTestCase(unittest.TestCase):
    def _node(self, lineno, end_lineno):
        node = ast.parse("def f():\n    pass").body[0]
        node.lineno, node.end_lineno = lineno, end_lineno
        return node

    def test_half_executed_span(self):
        node = self._node(1, 5)
        self.assertEqual(qa_common.span_coverage(node, {1, 2}, {3, 4}), 0.5)

    def test_lines_outside_the_span_are_ignored(self):
        node = self._node(10, 12)
        self.assertEqual(
            qa_common.span_coverage(node, {1, 2, 10, 11, 12}, {3, 4}), 1.0)

    def test_span_without_known_statements_counts_as_covered(self):
        # coverage 不认识任何行（stub/装饰器工件）→ 没有欠账
        node = self._node(50, 51)
        self.assertEqual(qa_common.span_coverage(node, {1}, {2}), 1.0)


class CoverageJsonMappingTestCase(unittest.TestCase):
    """crap.scan：coverage JSON ∩ AST 函数行段 → 每函数 CRAP。"""

    def test_scan_maps_file_coverage_onto_functions(self):
        source = textwrap.dedent("""\
            def covered(a):
                if a:
                    return 1
                return 0


            def uncovered(a):
                if a:
                    return 1
                return 0
            """)
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "act"))
            with open(os.path.join(root, "act", "m.py"), "w", encoding="utf-8") as fh:
                fh.write(source)
            cov = {"files": {"act/m.py": {
                "executed_lines": [1, 2, 3, 4, 7],
                "missing_lines": [8, 9, 10],
            }}}
            cov_path = os.path.join(root, "coverage.json")
            with open(cov_path, "w", encoding="utf-8") as fh:
                json.dump(cov, fh)
            scores, details = crap.scan(cov_path, root=root)
        # covered: CC 2, cov 1.0 → CRAP 2；uncovered: CC 2, cov 1/4 → 4×0.421875+2 ≈ 3.7
        self.assertEqual(scores["act/m.py::covered"], 2)
        self.assertEqual(scores["act/m.py::uncovered"], 3.7)
        self.assertEqual(details["act/m.py::covered"], (2, 1.0))

    def test_files_absent_from_coverage_json_are_not_scored(self):
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "act"))
            with open(os.path.join(root, "act", "m.py"), "w", encoding="utf-8") as fh:
                fh.write("def f():\n    return 1\n")
            cov_path = os.path.join(root, "coverage.json")
            with open(cov_path, "w", encoding="utf-8") as fh:
                json.dump({"files": {}}, fh)
            scores, _ = crap.scan(cov_path, root=root)
        self.assertEqual(scores, {})


if __name__ == "__main__":
    unittest.main()
