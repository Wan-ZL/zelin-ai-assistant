"""test-ui skill · GEOMETRY + 默认主题判例：`layout.lane.width` 声明 400 而渲染 320（今日漏项「列更窄」）
→ CHANGED geometry 并注明「declared matches, rendered does not」；源模式替代物只看 token 是否被 CSS 消费
且永远 substituted；默认主题 light → dark（漏项「暗色默认」）→ CHANGED declared；首帧观察值不等 → observed。

法典：docs/CONTRACT.md §UI-parity.3；设计 vnext2-plan R2.8。零子进程。
"""
import unittest

from tests import skill_test_ui_testkit as kit

import checks_ui  # noqa: E402
import parity  # noqa: E402
import sensors  # noqa: E402

THR = dict(parity.DEFAULT_THRESHOLDS)
GEOMETRY = {"layout.lane.width": {"screen": "board", "role": "list", "measure": "width"},
            "layout.rail.default_width": {"screen": "*", "role": "navigation", "measure": "width"}}


class GeometryTestCase(unittest.TestCase):
    def test_narrower_lanes_are_changed_with_declared_note(self):
        """负控制（漏项 #3）：参照 400px，被测也声明 400px，但渲染出 320 → CHANGED，note 说明声明没变。"""
        ref = kit.make_tokens({"light": {"layout.lane.width": "400px", "layout.rail.default_width": "200px"}})
        sub = kit.make_tokens({"light": {"layout.lane.width": "400px", "layout.rail.default_width": "200px"}})
        rows = parity.compare_geometry(GEOMETRY, ref, {"layout.lane.width": [320, 320, 320], "layout.rail.default_width": [200]}, THR, sub)
        by = {r["location"]: r for r in rows}
        self.assertEqual(by["layout.lane.width"]["status"], "CHANGED")
        self.assertEqual(by["layout.lane.width"]["note"], "declared matches (400.0px), rendered does not")
        self.assertEqual(by["layout.rail.default_width"]["status"], "PRESENT")

    def test_tolerance_boundary(self):
        """parity._geometry_row: `abs(v - declared) > tolerance` — 正好 1px 放行，1.5px 判变（> → >= 会误红）。"""
        ref = kit.make_tokens({"light": {"layout.lane.width": "400px"}})
        geo = {"layout.lane.width": GEOMETRY["layout.lane.width"]}
        self.assertEqual(parity.compare_geometry(geo, ref, {"layout.lane.width": [401]}, THR)[0]["status"], "PRESENT")
        self.assertEqual(parity.compare_geometry(geo, ref, {"layout.lane.width": [401.5]}, THR)[0]["status"], "CHANGED")

    def test_na_and_unavailable_rows(self):
        ref = kit.make_tokens({"light": {"layout.lane.width": "400px"}})
        rows = {r["location"]: r for r in parity.compare_geometry(GEOMETRY, ref, {"layout.lane.width": []}, THR)}
        self.assertEqual(rows["layout.lane.width"]["status"], "UNAVAILABLE")
        self.assertEqual(rows["layout.rail.default_width"]["status"], "N-A")

    def test_source_substitute_looks_for_var_consumption(self):
        css_ok = [".lane { width: var(--native-layout-lane-width); }", ".rail { width: var(--native-layout-rail-default-width); }"]
        css_bad = [".lane { width: 320px; }", ".rail { width: var(--native-layout-rail-default-width); }"]
        ok = {r["location"]: r for r in parity.geometry_source_substitute(GEOMETRY, css_ok)}
        bad = {r["location"]: r for r in parity.geometry_source_substitute(GEOMETRY, css_bad)}
        self.assertEqual(ok["layout.lane.width"]["status"], "PRESENT")
        self.assertTrue(ok["layout.lane.width"]["substituted"])
        self.assertEqual(bad["layout.lane.width"]["status"], "MISSING")

    def test_check_geometry_runtime_paths(self):
        det = kit.fake_det(["board.html"], config={"geometry": GEOMETRY}, tokens_files={"css": [], "component_dirs": []})
        ctx = checks_ui.make_ctx("/r", det)
        ctx["state"]["reference_tokens"] = kit.make_tokens({"light": {"layout.lane.width": "400px", "layout.rail.default_width": "200px"}})
        self.assertEqual(sensors.check_geometry_runtime(ctx)["status"], "fail")  # source substitute: nothing consumed
        ctx["state"]["runtime"] = {"inventory": kit.make_inventory([]), "geometry": {"layout.lane.width": [320], "layout.rail.default_width": [200]}}
        res = sensors.check_geometry_runtime(ctx)
        self.assertEqual(res["status"], "fail")
        self.assertIn("layout.lane.width 400.0→[320]", res["summary"])
        ctx["state"]["runtime"]["geometry"]["layout.lane.width"] = [400]
        self.assertEqual(sensors.check_geometry_runtime(ctx)["status"], "pass")
        self.assertEqual(sensors.check_geometry_runtime(checks_ui.make_ctx("/r", kit.fake_det(["b.html"])))["status"], "unavailable")


class DefaultThemeTestCase(unittest.TestCase):
    def test_dark_default_is_changed(self):
        """负控制（漏项 #2）：参照 light、被测声明 dark → CHANGED declared。"""
        ref = kit.make_tokens({"light": {}}, declared={"mode": "system", "fallback": "light", "evidence": []})
        sub = kit.make_tokens({"light": {}}, declared={"mode": "system", "fallback": "dark", "evidence": []})
        row = parity.compare_default_theme(sub, ref)
        self.assertEqual((row["status"], row["fields_changed"]), ("CHANGED", ["declared"]))
        same = parity.compare_default_theme(ref, ref)
        self.assertEqual(same["status"], "PRESENT")

    def test_observed_first_frame(self):
        ref = kit.make_tokens({"light": {}}, declared={"mode": "system", "fallback": "light", "evidence": []})
        row = parity.compare_default_theme(ref, ref, observed={"light": "light", "dark": "dark"})
        self.assertEqual(row["fields_changed"], ["observed"])
        row = parity.compare_default_theme(ref, ref, observed={"light": "light", "dark": "light"})
        self.assertEqual(row["status"], "PRESENT")
        self.assertEqual(parity.compare_default_theme({}, {}, observed={"light": "dark"})["status"], "PRESENT")  # no reference fallback → not judged

    def test_check_theme_default_declared(self):
        det = kit.fake_det(["board.html"])
        ctx = checks_ui.make_ctx("/r", det)
        ctx["state"]["subject_tokens"] = kit.make_tokens({"light": {}}, declared={"mode": "fixed", "fallback": "dark", "evidence": []})
        ctx["state"]["reference_tokens"] = kit.make_tokens({"light": {}}, declared={"mode": "system", "fallback": "light", "evidence": []})
        res = sensors.check_theme_default_declared(ctx)
        self.assertEqual(res["status"], "fail")
        self.assertIn("light → dark", res["summary"])
        ctx["state"]["reference_tokens"] = None
        self.assertEqual(sensors.check_theme_default_declared(ctx)["status"], "pass")
        ctx["state"]["subject_tokens"] = None
        self.assertEqual(sensors.check_theme_default_declared(ctx)["status"], "unavailable")


if __name__ == "__main__":
    unittest.main()
