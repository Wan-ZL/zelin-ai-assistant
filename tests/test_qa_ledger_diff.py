"""§58.4「账本对 base 只许缩」的判例（scripts/qa/ledger_diff.py）。

三态判决看不见「账本自己长了」：一个 PR 新增债务并同 PR 自记账，
compare_with_ledger 照样 ok=True（f2a54c1 审查 blocker 1 的活演示）。
本门把 head 的 qa/ 文件与 merge-base 的版本逐键比较：账本加键/抬分、
地板下调、gates.toml 放宽/删键、文件整个消失——任一即 FAIL；base 上
不存在的文件不比（账本出生的 PR 免比）。判例全部走纯文本比较器与注入
缝 collect_findings，不 spawn git。
"""
import os
import sys
import unittest

_QA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "qa")
if _QA_DIR not in sys.path:
    sys.path.insert(0, _QA_DIR)

import ledger_diff  # noqa: E402
import qa_common  # noqa: E402

_BASELINE = "qa/complexity_baseline.txt"


class BaselineGrowthTestCase(unittest.TestCase):
    def test_self_enrolled_new_debt_is_caught(self):
        # blocker 1 的活演示：CC-40 新函数 + 同 PR 自记账 → 三态全绿，
        # 但 base 差分必须抓到这条加进账本的键。
        base = "act/x.py::f 8\n"
        head = "act/evil_new.py::monster 40\nact/x.py::f 8\n"
        findings = ledger_diff.diff_baseline(_BASELINE, base, head)
        self.assertEqual(len(findings), 1)
        self.assertIn("added key act/evil_new.py::monster", findings[0])

    def test_three_state_verdict_alone_misses_the_same_bypass(self):
        # 钉住动机本身：同一形状喂给三态判决是绿的——所以才需要本门。
        result = qa_common.compare_with_ledger(
            {"act/evil_new.py::monster": 40.0}, {"act/evil_new.py::monster": 40.0}, 6.0)
        self.assertTrue(result["ok"])

    def test_raised_score_is_caught(self):
        findings = ledger_diff.diff_baseline(
            _BASELINE, "act/x.py::f 8\n", "act/x.py::f 12\n")
        self.assertEqual(len(findings), 1)
        self.assertIn("raised act/x.py::f 8 -> 12", findings[0])

    def test_shrinking_is_the_only_allowed_edit(self):
        base = "act/x.py::f 8\nact/y.py::g 22.5\n"
        self.assertEqual(ledger_diff.diff_baseline(_BASELINE, base, "act/x.py::f 7\n"), [])
        self.assertEqual(ledger_diff.diff_baseline(_BASELINE, base, ""), [])
        self.assertEqual(ledger_diff.diff_baseline(_BASELINE, base, base), [])

    def test_ledger_born_in_this_pr_is_exempt(self):
        findings = ledger_diff.diff_baseline(_BASELINE, None, "act/x.py::f 8\n")
        self.assertEqual(findings, [])

    def test_deleting_a_ledger_file_fails(self):
        findings = ledger_diff.diff_baseline(_BASELINE, "act/x.py::f 8\n", None)
        self.assertEqual(len(findings), 1)
        self.assertIn("deleted", findings[0])

    def test_nan_score_cannot_slip_past_the_raise_check(self):
        # nan > 84 与 nan < 84 都是 False：nan 一旦被解析成登记分，抬分
        # 检测就永久失明。解析层必须 fail-loud（qa_common._parse_score），
        # 差分门跟着炸红——不是静默零 findings。
        with self.assertRaises(ValueError):
            ledger_diff.diff_baseline(_BASELINE, "act/x.py::f 84\n",
                                      "act/x.py::f nan\n")

    def test_comments_and_scoreless_keys_parse_like_the_gate(self):
        # 解析与门同源（qa_common.parse_ledger_text）：注释/裸键语义一致。
        base = "# header\nedge-a\n"
        self.assertEqual(ledger_diff.diff_baseline(_BASELINE, base, "edge-a\n"), [])
        findings = ledger_diff.diff_baseline(_BASELINE, base, "edge-a\nedge-b\n")
        self.assertEqual(len(findings), 1)
        self.assertIn("added key edge-b", findings[0])


class FloorDiffTestCase(unittest.TestCase):
    def test_lowering_the_floor_fails(self):
        findings = ledger_diff.diff_floor("82.8\n", "# note\n80.0\n")
        self.assertEqual(len(findings), 1)
        self.assertIn("lowered 82.8 -> 80", findings[0])

    def test_raising_or_holding_the_floor_passes(self):
        self.assertEqual(ledger_diff.diff_floor("82.8\n", "83.1\n"), [])
        self.assertEqual(ledger_diff.diff_floor("82.8\n", "82.8\n"), [])

    def test_nan_floor_cannot_slip_past_the_lower_check(self):
        # nan < 83.2 是 False：nan 地板既不算 lowered、又让 coverage_floor
        # 的 percent < floor 永远 False——覆盖率跌到 1% 门照样绿。拒收。
        with self.assertRaises(ValueError):
            ledger_diff.diff_floor("83.2\n", "nan\n")

    def test_floor_birth_is_exempt_and_deletion_fails(self):
        self.assertEqual(ledger_diff.diff_floor(None, "82.8\n"), [])
        findings = ledger_diff.diff_floor("82.8\n", None)
        self.assertEqual(len(findings), 1)
        self.assertIn("deleted", findings[0])


class GatesLoosenTestCase(unittest.TestCase):
    _BASE = "[crap]\nmax = 6.0\ntolerance = 0.5\n"

    def test_raising_a_threshold_fails(self):
        head = "[crap]\nmax = 10.0\ntolerance = 0.5\n"
        findings = ledger_diff.diff_gates(self._BASE, head)
        self.assertEqual(len(findings), 1)
        self.assertIn("[crap].max raised 6.0 -> 10.0", findings[0])

    def test_widening_a_tolerance_fails(self):
        head = "[crap]\nmax = 6.0\ntolerance = 1.5\n"
        findings = ledger_diff.diff_gates(self._BASE, head)
        self.assertEqual(len(findings), 1)
        self.assertIn("[crap].tolerance raised 0.5 -> 1.5", findings[0])

    def test_tightening_passes(self):
        head = "[crap]\nmax = 5.0\ntolerance = 0.2\n"
        self.assertEqual(ledger_diff.diff_gates(self._BASE, head), [])

    def test_removing_a_threshold_key_fails(self):
        head = "[crap]\nmax = 6.0\n"
        findings = ledger_diff.diff_gates(self._BASE, head)
        self.assertEqual(len(findings), 1)
        self.assertIn("[crap].tolerance removed", findings[0])

    def test_adding_a_new_knob_is_allowed(self):
        head = self._BASE + "[mutation]\nnightly = true\n"
        self.assertEqual(ledger_diff.diff_gates(self._BASE, head), [])

    def test_changing_an_undeclared_key_fails_closed(self):
        # 方向表外的键改动一律 fail-closed：新旋钮必须先声明方向。
        base = "[mutation]\nbudget = 5\n"
        head = "[mutation]\nbudget = 6\n"
        findings = ledger_diff.diff_gates(base, head)
        self.assertEqual(len(findings), 1)
        self.assertIn("direction not declared", findings[0])

    def test_gates_birth_is_exempt_and_deletion_fails(self):
        self.assertEqual(ledger_diff.diff_gates(None, self._BASE), [])
        findings = ledger_diff.diff_gates(self._BASE, None)
        self.assertEqual(len(findings), 1)
        self.assertIn("deleted", findings[0])

    def test_every_committed_knob_has_a_declared_direction(self):
        # 自维护钉：qa/gates.toml 每个数字键都必须在 _LOOSEN_UP 里声明
        # 方向——否则表外改动 fail-closed 会把合法收紧也拦下。
        for section, keys in qa_common.load_gates().items():
            for key, value in keys.items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    self.assertIn((section, key), ledger_diff._LOOSEN_UP,
                                  "declare direction for [%s].%s" % (section, key))


class ParityLedgerTestCase(unittest.TestCase):
    """§65.2：ui/parity/pending.txt 与 waivers.txt 同样只许缩（按 id 集合比，备注列不是分数）。"""

    def test_added_id_is_caught_and_remarks_are_ignored(self):
        base = "# head\ncontrol:a:b:c\ncontrol:x:y:z  reason #119\n"
        head = "control:a:b:c\ncontrol:x:y:z  reworded reason\ncontrol:new:one\n"
        findings = ledger_diff.diff_parity_ledger("ui/parity/pending.txt", base, head)
        self.assertEqual(findings, ["GROW: ui/parity/pending.txt added control:new:one"])

    def test_striking_lines_birth_and_deletion(self):
        base = "control:a:b:c\nlane:debt\n"
        self.assertEqual(ledger_diff.diff_parity_ledger("ui/parity/waivers.txt", base, "lane:debt\n"), [])
        self.assertEqual(ledger_diff.diff_parity_ledger("ui/parity/waivers.txt", None, base), [])
        self.assertIn("deleted", ledger_diff.diff_parity_ledger("ui/parity/waivers.txt", base, None)[0])

    def test_collect_findings_covers_both_parity_ledgers(self):
        base_files = {"ui/parity/pending.txt": "a:b\n", "ui/parity/waivers.txt": "c:d  why\n"}
        head_files = {"ui/parity/pending.txt": "a:b\ne:f\n", "ui/parity/waivers.txt": "c:d  why\ng:h  why2 D3\n"}
        findings = ledger_diff.collect_findings(base_files.get, head_files.get, set())
        self.assertEqual(sorted(findings), ["GROW: ui/parity/pending.txt added e:f",
                                            "GROW: ui/parity/waivers.txt added g:h"])


class CollectFindingsTestCase(unittest.TestCase):
    def test_all_guarded_files_are_compared_and_findings_aggregate(self):
        base_files = {
            "qa/complexity_baseline.txt": "act/x.py::f 8\n",
            "qa/coverage_floor.txt": "82.8\n",
            "qa/gates.toml": "[complexity]\nmax = 6\n",
        }
        head_files = {
            "qa/complexity_baseline.txt": "act/evil.py::m 40\nact/x.py::f 8\n",
            "qa/coverage_floor.txt": "80.0\n",
            "qa/gates.toml": "[complexity]\nmax = 8\n",
        }
        findings = ledger_diff.collect_findings(
            base_files.get, head_files.get, {"qa/complexity_baseline.txt"})
        self.assertEqual(len(findings), 3)
        kinds = sorted(f.split(":", 1)[0] for f in findings)
        self.assertEqual(kinds, ["FLOOR", "GROW", "LOOSEN"])

    def test_clean_pr_produces_no_findings(self):
        files = {
            "qa/complexity_baseline.txt": "act/x.py::f 8\n",
            "qa/coverage_floor.txt": "82.8\n",
            "qa/gates.toml": "[complexity]\nmax = 6\n",
        }
        findings = ledger_diff.collect_findings(
            files.get, files.get, {"qa/complexity_baseline.txt"})
        self.assertEqual(findings, [])

    def test_birth_pr_with_no_qa_at_base_is_green(self):
        # 本 PR 自己的形状：base（main）上没有任何 qa/ 文件 → 全部免比。
        head_files = {
            "qa/complexity_baseline.txt": "act/x.py::f 8\n",
            "qa/coverage_floor.txt": "82.8\n",
            "qa/gates.toml": "[complexity]\nmax = 6\n",
        }
        findings = ledger_diff.collect_findings(
            lambda rel: None, head_files.get, {"qa/complexity_baseline.txt"})
        self.assertEqual(findings, [])


if __name__ == "__main__":
    unittest.main()
