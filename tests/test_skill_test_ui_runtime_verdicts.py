"""test-ui skill · runtime 层判决的负控制（每个 check_<id> 的红路径都要真能红）：没有 runtime bundle 的每个 runtime 层都是
UNAVAILABLE 而不是崩；tier 4 / 5 的层是 UNAVAILABLE 带 id；theme_default_observed 首帧 dark ≠ 参照 light → fail；reflow 横向
溢出 → fail；screens_capture 零截图 → fail；tokens_runtime 颜色漂移 → fail；keyboard_reach 不可达 → fail；a11y_rules 有 axe =
真仪器（pass 不降级；axe violation → fail）；matrix 双主题 → pass；tier ≥ 3 才跑全矩阵；structure_source 读不到文件 / 零条目 →
fail；surface_detect 无面 → na；probe 源文件的排除规则。零子进程。

法典：docs/CONTRACT.md §58（fail closed）/ §66；设计 vnext2-plan R2.8。
"""
import unittest

from tests import skill_test_ui_testkit as kit

import checks_ui  # noqa: E402
import sensors  # noqa: E402

tc = kit.tc
RUNTIME_CHECKS = ("check_pair_runtime", "check_tokens_runtime", "check_theme_default_observed", "check_a11y_rules",
                  "check_screens_capture", "check_visual_diff", "check_matrix_themes_viewports", "check_keyboard_reach",
                  "check_focus_order", "check_reflow", "check_app_launch")
TIER45_CHECKS = ("check_inventory_stability", "check_visual_stability", "check_states_matrix", "check_cross_engine",
                 "check_reference_runtime", "check_matrix_all_routes", "check_all_references", "check_clean_machine_ui",
                 "check_golden_review_sheet")


def _ctx(**state):
    ctx = checks_ui.make_ctx("/r", kit.fake_det(["board.html"]), sel={"tier": 2})
    ctx["state"].update(state)
    return ctx


def _bundle(items=(), **inventory_extra):
    inventory = kit.make_inventory(list(items), mode="runtime")
    inventory.update(inventory_extra)
    return {"inventory": inventory, "tokens_observed": {}, "geometry": {}, "axe": [], "observed_theme": {}}


class NoRuntimeTestCase(unittest.TestCase):
    def test_every_runtime_check_is_unavailable_without_a_bundle(self):
        for name in RUNTIME_CHECKS:
            res = getattr(sensors, name)(_ctx())
            self.assertEqual(res["status"], "unavailable", name)
            self.assertIn("runtime", res["summary"], name)

    def test_tier_4_and_5_rows_are_unavailable_and_named(self):
        for name in TIER45_CHECKS:
            fn = getattr(sensors, name)
            res = fn(_ctx(runtime=_bundle()))
            self.assertEqual(res["status"], "unavailable", name)
            self.assertIn(name[len("check_"):], res["summary"], name)
            self.assertIn(tc.SKILL_VERSION, res["summary"])
            self.assertEqual(fn.__name__, name)  # run_ui prints internal:<fn name> in the plan preview


class RedPathsTestCase(unittest.TestCase):
    def test_observed_dark_first_frame_is_red(self):
        ref = kit.make_tokens({"light": {}}, declared={"mode": "fixed", "fallback": "light", "evidence": []})
        ctx = _ctx(runtime=dict(_bundle(), observed_theme={"light": "light", "dark": "dark"}), subject_tokens=ref, reference_tokens=ref)
        res = sensors.check_theme_default_observed(ctx)
        self.assertEqual(res["status"], "fail")
        self.assertIn("first frame observed", res["summary"])
        ctx["state"]["runtime"]["observed_theme"] = {"light": "light", "dark": "light"}
        self.assertEqual(sensors.check_theme_default_observed(ctx)["status"], "pass")

    def test_reflow_overflow_is_red(self):
        ctx = _ctx(runtime=_bundle(overflow={"board::light::narrow::zh::rest": {"scrollWidth": 1200, "clientWidth": 960},
                                            "board::light::desktop::zh::rest": {"scrollWidth": 1440, "clientWidth": 1440}}))
        res = sensors.check_reflow(ctx)
        self.assertEqual((res["status"], sorted(res["details"]["overflow"])), ("fail", ["board::light::narrow::zh::rest"]))
        ctx["state"]["runtime"]["inventory"]["overflow"] = {"k": {"scrollWidth": 1, "clientWidth": 1}, "empty": None}
        self.assertEqual(sensors.check_reflow(ctx)["status"], "pass")

    def test_no_screenshots_is_red(self):
        self.assertEqual(sensors.check_screens_capture(_ctx(runtime=_bundle(shots=[])))["status"], "fail")
        res = sensors.check_screens_capture(_ctx(runtime=_bundle(shots=[{"id": "shot:board:initial:light:desktop:zh", "path": "/x.png"}])))
        self.assertEqual((res["status"], res["details"]["shots"]), ("pass", ["shot:board:initial:light:desktop:zh"]))

    def test_computed_color_drift_is_red(self):
        declared = kit.make_tokens({"light": {"color.bg": "#fafbfc", "layout.lane.width": "400px"}})
        ctx = _ctx(runtime=dict(_bundle(), tokens_observed={"light": {"--bg": "rgb(0, 0, 0)", "--layout-lane-width": "320px"}}), subject_tokens=declared)
        res = sensors.check_tokens_runtime(ctx)
        self.assertEqual(res["status"], "fail")
        self.assertEqual([d["var"] for d in res["details"]["drift"]], ["--bg"])  # only colours are judged here (tiers.md)
        ctx["state"]["runtime"]["tokens_observed"] = {"light": {"--bg": "rgb(250, 251, 252)"}}
        self.assertEqual(sensors.check_tokens_runtime(ctx)["status"], "pass")

    def test_unreachable_interactive_item_is_red(self):
        items = [kit.make_item("board", "button", "Approve"), kit.make_item("board", "button", "Later")]
        ctx = _ctx(runtime=_bundle(items, focus_walk={"board::light::desktop::zh::rest": ["control:board:button:approve"]}))
        res = sensors.check_keyboard_reach(ctx)
        self.assertEqual(res["status"], "fail")
        self.assertEqual([h["id"] for h in res["details"]["hits"]], ["control:board:button:later"])
        ctx["state"]["runtime"]["inventory"]["focus_walk"]["board::light::desktop::zh::rest"].append("control:board:button:later")
        self.assertEqual(sensors.check_keyboard_reach(ctx)["status"], "pass")

    def test_a11y_rules_with_axe_is_a_real_instrument(self):
        items = [kit.make_item("board", "button", "Approve")]
        without_axe = _ctx(runtime=_bundle(items))
        self.assertEqual(sensors.check_a11y_rules(without_axe)["status"], "substituted")
        with_axe = _ctx(runtime=_bundle(items))
        with_axe["det"]["tools"]["axe"] = "/w/axe.min.js"
        self.assertEqual(sensors.check_a11y_rules(with_axe)["status"], "pass")
        with_axe["state"]["runtime"]["axe"] = [{"id": "button-name", "impact": "critical", "help": "Buttons must have discernible text", "target": "button.x"}]
        res = sensors.check_a11y_rules(with_axe)
        self.assertEqual(res["status"], "fail")
        self.assertEqual(res["details"]["hits"][0]["rule_id"], "axe:button-name")

    def test_matrix_needs_two_themes(self):
        one = _ctx(runtime=_bundle(dims={"themes": ["light"], "viewports": [{"name": "desktop"}], "languages": ["zh"]}))
        self.assertEqual(sensors.check_matrix_themes_viewports(one)["status"], "substituted")
        two = _ctx(runtime=_bundle(dims={"themes": ["light", "dark"], "viewports": [{"name": "desktop"}, {"name": "narrow"}], "languages": ["zh", "en"]}))
        res = sensors.check_matrix_themes_viewports(two)
        self.assertEqual(res["status"], "pass")
        self.assertIn("['desktop', 'narrow']", res["summary"])

    def test_driver_dims_follow_the_tier(self):
        dims = {"themes": ["light", "dark"], "default_theme": "dark", "viewports": [{"name": "a"}, {"name": "b"}], "languages": ["zh", "en"]}
        self.assertEqual(sensors._dims_for_tier(dims, False), (["dark"], [{"name": "a"}], ["zh"]))
        self.assertEqual(sensors._dims_for_tier(dims, True), (["light", "dark"], [{"name": "a"}, {"name": "b"}], ["zh", "en"]))
        self.assertEqual(sensors._dims_for_tier({}, False), (["light"], [{"name": "desktop", "w": 1440, "h": 900}], ["zh"]))
        recipe = {"url": "http://127.0.0.1:1", "ready": "/api/health", "flags_all_on": None}
        ctx = _ctx()
        ctx["det"]["dims"] = dims
        self.assertEqual(sensors._driver_config(ctx, recipe, 3)["themes"], ["light", "dark"])
        self.assertEqual(sensors._driver_config(ctx, recipe, 2)["themes"], ["dark"])


class SourceRedPathsTestCase(unittest.TestCase):
    def test_structure_source_fails_closed_on_errors_or_zero_items(self):
        ctx = _ctx(subject_source=dict(kit.make_inventory([]), errors=["a.html: Permission denied"], screens=[], landmarks=[]))
        res = sensors.check_structure_source(ctx)
        self.assertEqual((res["status"], res["details"]["errors"]), ("fail", ["a.html: Permission denied"]))
        ctx = _ctx(subject_source=dict(kit.make_inventory([]), errors=[], screens=[], landmarks=[]))
        res = sensors.check_structure_source(ctx)
        self.assertEqual(res["status"], "fail")
        self.assertIn("0 items", res["summary"])

    def test_surface_detect_without_surface_is_na(self):
        ctx = checks_ui.make_ctx("/r", kit.fake_det(["x.py"]))
        self.assertEqual(sensors.check_surface_detect(ctx)["status"], "na")
        res = sensors.check_surface_detect(_ctx())
        self.assertEqual(res["status"], "pass")
        self.assertIn("static-html", res["summary"])

    def test_probe_source_exclusions(self):
        self.assertTrue(sensors._is_probe_source("web/src/App.tsx"))
        self.assertTrue(sensors._is_probe_source("server/lanes.py"))
        for rel in ("tests/test_x.py", "ui/parity/pending.txt", "ui/tokens/native-tokens.json", "web/src/fixtures/a.tsx",
                    "web/src/App.test.tsx", "docs/x.md", "mac/Sources/App.swift"):
            self.assertFalse(sensors._is_probe_source(rel), rel)


if __name__ == "__main__":
    unittest.main()
