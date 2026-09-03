"""test-ui skill · 账本判例（shrink-only 语义）：pending 记账 vs stale；waivers 必须带理由，`<rule>::<id>[::theme]`
匹配优先序；pending 对 merge-base 只许缩（grew = 问题）；waivers 新增要 acknowledged；aliases 悬空；
parse_ledger 与 parity 契约同口径（`# 注释` / `<id>  <rest>`）。零子进程。

法典：docs/CONTRACT.md §58.4（三态）/ §UI-parity.2（pending / waivers）；设计 vnext2-plan R2.8。
负控制：pending 长了 → pending_grew；无理由 waiver → reasonless_waiver（不 WAIVED）。
"""
import os
import tempfile
import unittest

from tests import skill_test_ui_testkit as kit

import parity  # noqa: E402

THR = dict(parity.DEFAULT_THRESHOLDS)


class ParseTestCase(unittest.TestCase):
    def test_parse_ledger_and_aliases(self):
        text = "# comment\n\ncontrol:a:button:x\ncontrol:a:button:y  reason here  #119\n"
        self.assertEqual(parity.parse_ledger(text), {"control:a:button:x": "", "control:a:button:y": "reason here  #119"})
        aliases = parity.parse_aliases("control:a:button:x  control:a:button:z  renamed in #3\ncontrol:a:button:q\n")
        self.assertEqual(aliases["control:a:button:x"], {"subject": "control:a:button:z", "reason": "renamed in #3"})
        self.assertEqual(aliases["control:a:button:q"], {"subject": "", "reason": ""})

    def test_load_ledgers_from_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit.make_repo(tmp, {"pending.txt": "a:b:c:d\n", "waivers.txt": "x:y:z:w  why\n"})
            ledgers = parity.load_ledgers(tmp)
            self.assertEqual((ledgers["pending"], ledgers["waivers"], ledgers["aliases"]), ({"a:b:c:d": ""}, {"x:y:z:w": "why"}, {}))
            self.assertEqual(ledgers["dir"], tmp)
        self.assertEqual(parity.load_ledgers(None)["dir"], None)

    def test_waiver_lookup_order(self):
        waivers = {"wcag.contrast.text::control:a:button:x::dark": "d", "wcag.contrast.text::control:a:button:x": "r",
                   "control:a:button:x": "i", "*": "all"}
        self.assertEqual(parity.waiver_for(waivers, "control:a:button:x", "wcag.contrast.text", "dark")[1], "d")
        self.assertEqual(parity.waiver_for(waivers, "control:a:button:x", "wcag.contrast.text", "light")[1], "r")
        self.assertEqual(parity.waiver_for(waivers, "control:a:button:x")[1], "i")
        self.assertEqual(parity.waiver_for(waivers, "other")[1], "all")
        self.assertIsNone(parity.waiver_for({}, "other"))


class ApplyLedgerTestCase(unittest.TestCase):
    def _row(self, status, item_id="control:board:button:approve"):
        return {"id": item_id, "status": status, "ledger": None, "detail": {}, "fields_changed": []}

    def test_pending_records_missing_and_flags_stale(self):
        problems = []
        ledgers = dict(parity.load_ledgers(None), pending={"control:board:button:approve": ""})
        row = parity.apply_ledger(self._row("MISSING"), ledgers, problems)
        self.assertEqual((row["status"], row["ledger"], problems), ("MISSING", "pending", []))
        row = parity.apply_ledger(self._row("PRESENT"), ledgers, problems)
        self.assertEqual((row["ledger"], problems[0]["kind"]), ("stale", "stale_pending"))

    def test_waiver_needs_reason(self):
        problems = []
        ledgers = dict(parity.load_ledgers(None), waivers={"control:board:button:approve": "kept out on purpose"})
        row = parity.apply_ledger(self._row("MISSING"), ledgers, problems)
        self.assertEqual((row["status"], row["ledger"], row["detail"]["reason"]), ("WAIVED", "waived", "kept out on purpose"))
        ledgers["waivers"] = {"control:board:button:approve": ""}
        row = parity.apply_ledger(self._row("CHANGED"), ledgers, problems)
        self.assertEqual(row["status"], "CHANGED")
        self.assertEqual(problems[0]["kind"], "reasonless_waiver")
        self.assertEqual(row["detail"]["invalid_waiver"], "control:board:button:approve")

    def test_present_is_never_waived(self):
        ledgers = dict(parity.load_ledgers(None), waivers={"*": "everything"})
        row = parity.apply_ledger(self._row("PRESENT"), ledgers, [])
        self.assertEqual(row["status"], "PRESENT")


class ShrinkOnlyTestCase(unittest.TestCase):
    def test_pending_grew_is_a_problem(self):
        ledgers = dict(parity.load_ledgers(None), pending={"a:b:c:d": "", "a:b:c:e": ""})
        problems = parity.ledger_shrink_check(ledgers, {"pending": "a:b:c:d\n", "waivers": ""})
        self.assertEqual(problems, [{"kind": "pending_grew", "line": "a:b:c:e"}])
        self.assertEqual(parity.ledger_shrink_check(ledgers, {"pending": "a:b:c:d\na:b:c:e\na:b:c:f\n", "waivers": ""}), [])

    def test_waiver_growth_needs_reason_and_acknowledgement(self):
        ledgers = dict(parity.load_ledgers(None), waivers={"x:y:z:w": "because", "x:y:z:v": ""})
        base = {"pending": "", "waivers": ""}
        kinds = [p["line"] for p in parity.ledger_shrink_check(ledgers, base)]
        self.assertEqual(sorted(kinds), ["x:y:z:v", "x:y:z:w"])
        acked = parity.ledger_shrink_check(ledgers, base, acknowledged=["x:y:z:w"])
        self.assertEqual([p["line"] for p in acked], ["x:y:z:v"])  # reasonless stays a problem even if acked
        self.assertEqual(parity.ledger_shrink_check(dict(ledgers, waivers={"x:y:z:w": "because"}), {"pending": "", "waivers": "x:y:z:w  because\n"}), [])

    def test_ledger_lint(self):
        ledgers = dict(parity.load_ledgers(None), waivers={"a": ""}, aliases={"b": {"subject": "", "reason": ""}})
        kinds = sorted(p["kind"] for p in parity.ledger_lint(ledgers))
        self.assertEqual(kinds, ["dangling_alias", "reasonless_waiver"])


class ThresholdsUnmovedTestCase(unittest.TestCase):
    def test_loosening_detected_by_direction(self):
        import sensors
        self.assertTrue(sensors._loosened(0.02, 0.0, 1))       # max_changed_pct up = loosened
        self.assertFalse(sensors._loosened(0.0, 0.02, 1))      # stricter
        self.assertTrue(sensors._loosened(4.0, 4.5, -1))       # contrast floor down = loosened
        self.assertFalse(sensors._loosened(None, 4.5, -1))
        self.assertEqual(sensors._mask_area({"masks": {"board": [[0, 0, 10, 10], [0, 0, 1, 1, 9]]}}), 100)

    def test_check_thresholds_unmoved_verdicts(self):
        import checks_ui
        import sensors
        det = kit.fake_det(["board.html"], thresholds=dict(THR, max_changed_pct=0.05), thresholds_base=dict(THR),
                           config={"masks": {"board": [[0, 0, 100, 100]]}}, config_base={"masks": {}})
        res = sensors.check_thresholds_unmoved(checks_ui.make_ctx("/r", det))
        self.assertEqual(res["status"], "fail")
        self.assertIn("max_changed_pct", res["details"]["loosened"])
        self.assertTrue(any(k.startswith("masks") for k in res["details"]["loosened"]))
        det["thresholds"], det["config"] = dict(THR), {}
        self.assertEqual(sensors.check_thresholds_unmoved(checks_ui.make_ctx("/r", det))["status"], "pass")
        det["thresholds_base"] = None
        self.assertEqual(sensors.check_thresholds_unmoved(checks_ui.make_ctx("/r", det))["status"], "na")


class LedgerLintCheckTestCase(unittest.TestCase):
    def test_check_ledger_lint_with_base_texts(self):
        import checks_ui
        import sensors
        with tempfile.TemporaryDirectory() as tmp:
            kit.make_repo(tmp, {"pending.txt": "a:b:c:d\na:b:c:e\n", "waivers.txt": "x:y:z:w\n"})
            parsed = parity.load_ledgers(tmp)
            det = kit.fake_det(["board.html"], ledgers={"dir": tmp, "parsed": parsed, "base_texts": {"pending": "a:b:c:d\n", "waivers": "", "aliases": ""}})
            res = sensors.check_ledger_lint(checks_ui.make_ctx("/r", det, sel={}))
            self.assertEqual(res["status"], "fail")
            self.assertEqual(sorted({p["kind"] for p in res["details"]["problems"]}), ["pending_grew", "reasonless_waiver", "waiver_grew"])
            det["ledgers"] = {"dir": None, "parsed": parity.load_ledgers(None), "base_texts": None}
            self.assertEqual(sensors.check_ledger_lint(checks_ui.make_ctx("/r", det, sel={}))["status"], "na")
            self.assertFalse(os.path.exists(os.path.join(tmp, "aliases.txt")))  # the skill wrote nothing


if __name__ == "__main__":
    unittest.main()
