"""test-ui skill · 规则引擎 + opinion 隔离判例：规则表从 references/rules/*.json 读、项目覆盖生效；
wcag.name.interactive（无名图标按钮）、wcag.target.size（20×20 目标）、wcag.contrast.text（runtime + 源色对）、
wcag.keyboard（不在 Tab 走位里）、wcag.lang（html 无 lang）、wcag.heading.order（h1 → h3）、tokens.off_literal；
waiver 让 hit 变 WAIVED；只有 critical/serious 判红；opinion 只能写 report.opinion，试图写 checks/items/fix_first 的键被丢弃并记录。

法典：docs/CONTRACT.md §UI-parity；设计 vnext2-plan R2.8。零子进程。
"""
import unittest

from tests import skill_test_ui_testkit as kit

import checks_ui  # noqa: E402
import parity  # noqa: E402
import run_ui  # noqa: E402
import sensors  # noqa: E402

THR = dict(parity.DEFAULT_THRESHOLDS)
RULES = parity.load_rules()


def _with_state(item, **state):
    item["states"] = {"light::desktop::zh::rest": dict({"visible": True, "focusable": True}, **state)}
    return item


class RuleTableTestCase(unittest.TestCase):
    def test_skill_default_thresholds_are_the_documented_strict_values(self):
        """SKILL.md / tiers.md / pairing.md / visual.py 写明的 skill 默认值（strict = WCAG 2.2 AA，视觉 0 %）——项目没给
        阈值时用的就是这一份，改一位就是改了尺子。"""
        self.assertEqual({k: v for k, v in parity.DEFAULT_THRESHOLDS.items() if k not in ("source", "note")},
                         {"max_changed_pct": 0.0, "pixel_tolerance": 0, "max_mask_ratio": 0.2, "contrast_text": 4.5,
                          "contrast_large": 3.0, "target_min_px": 24, "geometry_tolerance_px": 1.0,
                          "token_required_families": ["layout"], "similarity_floor": 0.8, "reruns": 3})
        self.assertEqual(parity.DEFAULT_THRESHOLDS["source"], "skill-defaults")
        self.assertIn("WCAG 2.2 AA", parity.DEFAULT_THRESHOLDS["note"])

    def test_rules_loaded_and_overridable(self):
        self.assertIn("wcag.contrast.text", RULES)
        self.assertEqual(RULES["tokens.off_literal"]["severity"], "minor")
        patched = parity.load_rules({"wcag.target.size": {"severity": "serious"}, "custom.rule": {"severity": "minor"}})
        self.assertEqual(patched["wcag.target.size"]["severity"], "serious")
        self.assertIn("custom.rule", patched)


class InventoryRulesTestCase(unittest.TestCase):
    def test_name_interactive_negative_control(self):
        inv = kit.make_inventory([kit.make_item("board", "button", ""), kit.make_item("board", "button", "Go"),
                                  kit.make_item("board", "button", "", visible=False, hidden_by="hidden")])
        hits = parity.rule_name_interactive(inv, RULES, THR)
        self.assertEqual([h["id"] for h in hits], ["control:board:button:unnamed"])
        self.assertEqual(hits[0]["severity"], "serious")

    def test_target_size_boundary(self):
        """parity._small_target: `min(w, h) < floor` — 24 放行、23 判中（< → <= 会误红）。"""
        small = _with_state(kit.make_item("board", "button", "X"), bbox=[0, 0, 20, 20])
        edge = _with_state(kit.make_item("board", "button", "Y"), bbox=[0, 0, 24, 40])
        under = _with_state(kit.make_item("board", "button", "Z"), bbox=[0, 0, 23, 40])
        hits = parity.rule_target_size(kit.make_inventory([small, edge, under]), RULES, THR)
        self.assertEqual(sorted(h["id"] for h in hits), ["control:board:button:x", "control:board:button:z"])
        self.assertEqual(hits[0]["theme"], "light")

    def test_contrast_runtime_boundary(self):
        """parity._low_contrast: `ratio < floor` — 4.5 放行、4.49 判中；大字用 3.0。"""
        ok = _with_state(kit.make_item("board", "static", "A"), contrast={"ratio": 4.5, "large": False})
        low = _with_state(kit.make_item("board", "static", "B"), contrast={"ratio": 4.49, "large": False})
        large = _with_state(kit.make_item("board", "heading", "C"), contrast={"ratio": 3.2, "large": True})
        hits = parity.rule_contrast_runtime(kit.make_inventory([ok, low, large]), RULES, THR)
        self.assertEqual([h["id"] for h in hits], ["control:board:static:b"])

    def test_contrast_pairs_from_tokens(self):
        doc = kit.make_tokens({"light": {"color.text-tertiary": "#8a8f99", "color.bg": "#ffffff", "color.text-primary": "#1a1c22"}})
        pairs = [["color.text-tertiary", "color.bg"], ["color.text-primary", "color.bg"], ["color.nope", "color.bg"], ["color.text-tertiary", "color.bg", True]]
        hits = parity.rule_contrast_pairs(doc, RULES, THR, pairs)
        self.assertEqual(len(hits), 1)  # tertiary fails normal text; passes as large (≥ 3.0); missing token skipped
        self.assertEqual((hits[0]["id"], hits[0]["theme"]), ("color.text-tertiary/color.bg", "light"))
        self.assertLess(hits[0]["measured"], 4.5)

    def test_keyboard_lang_heading(self):
        inv = kit.make_inventory([kit.make_item("board", "button", "Go"), kit.make_item("board", "button", "Stay"),
                                  kit.make_item("board", "heading", "H1", level=1), kit.make_item("board", "heading", "H3", level=3)])
        inv["focus_walk"] = {"board::light": ["control:board:button:go"]}
        inv["lang"] = ""
        hits = {h["rule_id"]: h for h in parity.run_rules(inv, None, THR, RULES)}
        self.assertEqual(hits["wcag.keyboard"]["id"], "control:board:button:stay")
        self.assertEqual(hits["wcag.lang"]["id"], "document")
        self.assertEqual(hits["wcag.heading.order"]["measured"], "h3 after h1")
        inv["focus_walk"], inv["lang"] = {}, None
        self.assertEqual([h["rule_id"] for h in parity.run_rules(inv, None, THR, RULES)], ["wcag.heading.order"])

    def test_waivers_turn_hits_into_waived(self):
        inv = kit.make_inventory([kit.make_item("board", "button", "")])
        hits = parity.run_rules(inv, None, THR, RULES, waivers={"wcag.name.interactive::control:board:button:unnamed": "icon documented elsewhere"})
        self.assertEqual((hits[0]["status"], hits[0]["reason"]), ("WAIVED", "icon documented elsewhere"))
        reasonless = parity.run_rules(inv, None, THR, RULES, waivers={"wcag.name.interactive::control:board:button:unnamed": ""})
        self.assertEqual(reasonless[0]["status"], "hit")

    def test_rules_verdict_severity(self):
        minor = [{"status": "hit", "severity": "minor", "rule_id": "tokens.off_literal", "id": "x"}]
        self.assertEqual(sensors._rules_verdict(minor, "t")["status"], "pass")
        serious = minor + [{"status": "hit", "severity": "serious", "rule_id": "wcag.lang", "id": "d"}]
        self.assertEqual(sensors._rules_verdict(serious, "t")["status"], "fail")
        waived = [dict(serious[1], status="WAIVED")]
        self.assertEqual(sensors._rules_verdict(waived, "t")["status"], "pass")

    def test_off_token_literals_check_cap(self):
        det = kit.fake_det(["b.html"])
        ctx = checks_ui.make_ctx("/r", det)
        ctx["state"]["subject_tokens"] = kit.make_tokens({"light": {}}, literals=[{"file": "a.css", "line": 1, "property": "color", "value": "#fff", "family": "color"}])
        self.assertEqual(sensors.check_off_token_literals(ctx)["status"], "pass")  # advisory without cap
        det["thresholds"] = dict(THR, max_off_token_literals=0)
        self.assertEqual(sensors.check_off_token_literals(ctx)["status"], "fail")


class KeyboardRuleTestCase(unittest.TestCase):
    def test_disabled_items_are_not_unreachable(self):
        """composer 的「捕获」在没输入时 disabled（不可聚焦）→ 不在 Tab 序里是设计，不是 wcag.keyboard 命中；可聚焦却没走到才是。"""
        go = kit.make_item("board", "button", "Go")
        disabled = kit.make_item("board", "button", "Capture", focusable=False)
        inv = kit.make_inventory([go, disabled], focus_walk={"board::light": []})
        hits = parity.rule_keyboard(inv, parity.load_rules(), {})
        self.assertEqual([h["id"] for h in hits], ["control:board:button:go"])


class HitDedupeTestCase(unittest.TestCase):
    def test_same_defect_across_viewports_and_languages_counts_once(self):
        """同一 (rule, id, theme) 在 4 个 viewport × language 状态各命中一次 = 一个缺陷（occurrences 4），不是四个。"""
        base = {"rule_id": "wcag.target.size", "id": "control:board:link:trash", "measured": 17, "threshold": 24, "severity": "moderate", "status": "hit"}
        hits = [dict(base, theme="light")] * 4 + [dict(base, theme="dark")] * 4 + [dict(base, id="control:board:link:x", theme="light", severity="serious")]
        res = sensors._rules_verdict(hits, "t")
        self.assertEqual((res["details"]["minor"], res["details"]["serious"]), (2, 1))
        by = {(h["id"], h["theme"]): h["occurrences"] for h in res["details"]["hits"]}
        self.assertEqual(by[("control:board:link:trash", "light")], 4)


class OpinionIsolationTestCase(unittest.TestCase):
    def test_apply_opinion_drops_measurement_keys(self):
        result = {"items": [{"id": "x", "status": "MISSING"}], "checks": []}
        out = parity.apply_opinion(result, {"text": "the rail feels crowded", "items": [], "fix_first": ["hack"], "status": "pass"})
        self.assertEqual(out["items"], [{"id": "x", "status": "MISSING"}])
        self.assertEqual(out["opinion"]["dropped_keys"], ["fix_first", "items", "status"])
        self.assertEqual(out["opinion"]["banner"], "Nothing below changes a status or a rank.")
        # catalog.md: any key other than `text` is dropped AND listed — not just the six well-known measurement keys
        smuggled = parity.apply_opinion({}, {"text": "ok", "score": 9, "sections": ["a"], "verdict": "green"})
        self.assertEqual(smuggled["opinion"]["dropped_keys"], ["score", "sections", "verdict"])
        self.assertEqual(sorted(smuggled["opinion"]), ["banner", "dropped_keys", "text"])

    def test_opinion_check_and_report_section(self):
        det = kit.fake_det(["b.html"])
        ctx = checks_ui.make_ctx("/r", det, sel={"opinion": {"text": "crowded", "checks": [{"status": "pass"}]}})
        res = sensors.check_opinion(ctx)
        self.assertEqual(res["status"], "pass")
        results = [run_ui._result("opinion", {"kind": "internal", "fn": sensors.check_opinion, "tool": "internal", "steps": []}, "pass", res["summary"], res["details"])]
        opinion = run_ui._opinion(results)
        self.assertEqual(opinion["text"], "crowded")
        self.assertEqual(run_ui.verdict(results), ("green", 0))  # opinion never moves the verdict
        self.assertEqual(sensors.check_opinion(checks_ui.make_ctx("/r", det, sel={}))["status"], "na")


if __name__ == "__main__":
    unittest.main()
