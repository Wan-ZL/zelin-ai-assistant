"""§58.1 圈复杂度计数口径的判例：已知代码片段必须得到精确分数。

口径（qa/gates.toml 的门用同一实现）：1 + if/elif/for/while/except/assert/
三元/and-or（n−1）/comprehension-if/match-case；with-as 不算；嵌套 def
不计入外层（各自成账）。
"""
import ast
import os
import sys
import textwrap
import unittest

_QA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "qa")
if _QA_DIR not in sys.path:
    sys.path.insert(0, _QA_DIR)

import qa_common  # noqa: E402


def _scores(source):
    tree = ast.parse(textwrap.dedent(source))
    return {qual: qa_common.cyclomatic_complexity(node)
            for qual, node in qa_common.collect_functions(tree)}


class DecisionPointTestCase(unittest.TestCase):
    def test_straight_line_function_scores_one(self):
        self.assertEqual(_scores("def f(a):\n    return a"), {"f": 1})

    def test_if_scores_two_and_else_is_free(self):
        src = """
        def f(a):
            if a:
                return 1
            else:
                return 2
        """
        self.assertEqual(_scores(src), {"f": 2})

    def test_elif_chain_counts_each_branch(self):
        src = """
        def f(a):
            if a == 1:
                return 1
            elif a == 2:
                return 2
            elif a == 3:
                return 3
            return 0
        """
        self.assertEqual(_scores(src), {"f": 4})

    def test_boolop_counts_n_minus_one(self):
        # a and b or c = Or(And(a,b), c)：and 贡献 1、or 贡献 1、if 贡献 1
        src = """
        def f(a, b, c):
            if a and b or c:
                return 1
            return 0
        """
        self.assertEqual(_scores(src), {"f": 4})

    def test_loops_except_assert_ternary(self):
        src = """
        def f(items):
            total = 0
            for x in items:            # +1
                while x > 0:           # +1
                    x -= 1
            try:
                total += 1
            except ValueError:         # +1
                pass
            assert total >= 0          # +1
            return 1 if total else 0   # +1
        """
        self.assertEqual(_scores(src), {"f": 6})

    def test_with_as_is_deliberately_free(self):
        src = """
        def f(path):
            with open(path) as fh:
                return fh.read()
        """
        self.assertEqual(_scores(src), {"f": 1})

    def test_comprehension_counts_ifs_only(self):
        # 两个 if 子句 +2；for 生成子句本体不算
        src = """
        def f(items):
            return [x for x in items if x if x > 1]
        """
        self.assertEqual(_scores(src), {"f": 3})

    def test_lambda_body_counts_into_the_enclosing_function(self):
        src = """
        def f(a):
            g = lambda x: 1 if x else 2
            return g(a)
        """
        self.assertEqual(_scores(src), {"f": 2})

    @unittest.skipUnless(sys.version_info >= (3, 10), "match 语法要 3.10+")
    def test_match_counts_each_case(self):
        src = """
        def f(a):
            match a:
                case 1:
                    return 1
                case 2:
                    return 2
                case _:
                    return 0
        """
        self.assertEqual(_scores(src), {"f": 4})


class FunctionAccountingTestCase(unittest.TestCase):
    def test_nested_def_is_scored_separately_not_into_the_parent(self):
        src = """
        def outer(a):
            def inner(b):
                if b:
                    return 1
                return 0
            return inner(a)
        """
        self.assertEqual(_scores(src), {"outer": 1, "outer.inner": 2})

    def test_method_qualnames_use_the_class_prefix(self):
        src = """
        class C:
            def m(self, a):
                if a:
                    return 1
                return 0
        """
        self.assertEqual(_scores(src), {"C.m": 2})

    def test_duplicate_definitions_get_stable_suffixes(self):
        src = """
        def f():
            return 1

        def f():
            if True:
                return 2
        """
        self.assertEqual(_scores(src), {"f": 1, "f#2": 2})

    def test_async_functions_are_counted_like_sync_ones(self):
        src = """
        async def f(a):
            if a:
                return 1
            return 0
        """
        self.assertEqual(_scores(src), {"f": 2})


if __name__ == "__main__":
    unittest.main()
