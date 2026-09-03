"""write-better skill · its own linter suite runs in CI (CONTRACT §65; skill absorbed from PR #102).

`skills/write-better/scripts/test_style_check.py` is the skill's self-contained
62-case regression suite for `style_check.py` (contractions, slash dates, hedges,
passive voice, serial comma, LaTeX stripping, exit codes …). Eight of its cases
run the linter as a real subprocess (CLI exit codes / stdin / multi-file), so the
whole suite lives behind this integration/ wrapper (防腐 #7) instead of being
copied into tests/. Zero network, zero claude; BUDGET_SECONDS bounds it.

Wall-clock cases (``test_perf_*`` — "2 MB lints in < 3 s") are left out here: under
the QA gates job the suite runs with coverage tracing (~2× slower) and the first
CI run flaked at 3.17 s. They are performance claims about the linter, not
correctness; `python3 skills/write-better/scripts/test_style_check.py` still runs
them when the skill itself is worked on.
"""
import importlib.util
import sys
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SUITE = REPO / "skills" / "write-better" / "scripts" / "test_style_check.py"
BUDGET_SECONDS = 60
_T0 = [time.monotonic()]


def setUpModule():
    _T0[0] = time.monotonic()


def tearDownModule():
    elapsed = time.monotonic() - _T0[0]
    if elapsed > BUDGET_SECONDS:
        raise AssertionError("tests/integration/test_skill_write_better_style_check.py took %.0fs > %ds"
                             % (elapsed, BUDGET_SECONDS))


def load_tests(loader, standard_tests, pattern):
    """unittest load_tests protocol: import the skill's suite by path and hand its
    cases to the runner (its `sys.path.insert(0, HERE)` makes `style_check` importable)."""
    spec = importlib.util.spec_from_file_location("_write_better_style_suite", SUITE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    for test in _iter_cases(loader.loadTestsFromModule(module)):
        if "test_perf" not in test.id():
            standard_tests.addTest(test)
    return standard_tests


def _iter_cases(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from _iter_cases(item)
        else:
            yield item


class SuitePresentTestCase(unittest.TestCase):
    def test_suite_file_ships_with_the_skill(self):
        self.assertTrue(SUITE.is_file())
        self.assertTrue((SUITE.parent / "style_check.py").is_file())
