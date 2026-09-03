"""test-ui skill · TOKENS 传感器判例：CSS 变量三种主题作用域、家族与点路径（含 --native-layout-* 还原成
layout.group.name）、font 简写拆解、W3C JSON 双值拆主题、typeScale.ts 表、默认主题声明三态、字面量普查、
token 比较（MISSING 只对 required 家族、dimension 容差、颜色归一）、theme parity。零子进程。

法典：docs/CONTRACT.md §UI-parity.3；设计 vnext2-plan R2.8。负控制：dark 少一个 token；组件 CSS 里 `color: #fff`。
"""
import os
import tempfile
import unittest

from tests import skill_test_ui_testkit as kit

import parity  # noqa: E402
import tokens as tk  # noqa: E402

CSS = """/* header */
:root { color-scheme: light; --bg: #fafbfc; --text-primary: #1a1c22; --radius-card: 8px; --space-2: 8px;
  --type-title: 600 15px/1.4 var(--font-sans); --font-sans: -apple-system, sans-serif;
  --native-layout-lane-width: 400px; --native-layout-rail-default-width: 200px; --native-color-green-light: #28cd41;
  --accent-soft: rgba(18, 117, 140, 0.12); --z-top: 10; }
:root[data-theme="dark"] { color-scheme: dark; --bg: #1b1d23; --text-primary: #e6e8ee; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { --bg: #1b1d23; --text-primary: #e6e8ee; --surface: #23262e; } }
.card { color: #fff; border-radius: 6px; padding: 4px; background: var(--surface); font-size: 12.5px; }
"""


class CssParseTestCase(unittest.TestCase):
    def test_scopes(self):
        themes = tk.parse_css_variables(CSS, "t.css")
        self.assertEqual(sorted(themes), ["dark", "light"])
        self.assertEqual(themes["light"]["--bg"]["value"], "#fafbfc")
        self.assertEqual(themes["dark"]["--bg"]["value"], "#1b1d23")
        self.assertEqual(themes["dark"]["--surface"]["value"], "#23262e")  # prefers-dark media scope merges into dark
        self.assertEqual(themes["light"]["--bg"]["source"], "t.css:2")
        self.assertNotIn(".card", str(themes))

    def test_family_and_path(self):
        self.assertEqual(tk.family_of("--bg", "#fff"), "color")
        self.assertEqual(tk.family_of("--type-x", "600 1px"), "typography")
        self.assertEqual(tk.family_of("--z-top", "10"), "other")
        self.assertEqual(tk.family_of("--custom-x", "1", {"--custom-": "spacing"}), "spacing")
        self.assertEqual(tk.token_path("--native-layout-rail-default-width", "layout"), "layout.rail.default_width")
        self.assertEqual(tk.token_path("--native-layout-lane-width", "layout"), "layout.lane.width")
        self.assertEqual(tk.token_path("--radius-card", "radius"), "radius.card")
        self.assertEqual(tk.token_path("--bg", "color"), "color.bg")

    def test_font_shorthand(self):
        self.assertEqual(tk.parse_font_shorthand("600 15px/1.4 var(--font-sans)"), {"weight": 600, "size": 15.0, "line": 1.4, "family": "sans"})
        self.assertEqual(tk.parse_font_shorthand("400 10px/16px var(--font-mono)"), {"weight": 400, "size": 10.0, "line": 1.6, "family": "mono"})
        self.assertEqual(tk.parse_font_shorthand("bold 12px x")["weight"], 700)
        self.assertEqual(tk.parse_font_shorthand("12px x")["weight"], 400)
        self.assertIsNone(tk.parse_font_shorthand("inherit"))

    def test_css_to_tokens_types(self):
        doc = tk.css_to_tokens(tk.parse_css_variables(CSS))
        light = doc["light"]
        self.assertEqual(light["color.bg"]["$value"], "#fafbfcff")
        self.assertEqual(light["typography.title"]["$value"]["size"], 15.0)
        self.assertEqual(light["layout.lane.width"], {"$type": "dimension", "$value": "400px", "source": "tokens.css:4", "var": "--native-layout-lane-width"})
        self.assertEqual(light["color.accent-soft"]["$value"], "#12758c1f")
        self.assertEqual(light["other.z-top"]["$type"], "string")


class DeclaredThemeTestCase(unittest.TestCase):
    def test_three_shapes(self):
        self.assertEqual(tk.declared_default_theme('dataset.theme = "light"', "")["mode"], "fixed")
        system = tk.declared_default_theme("", CSS)
        self.assertEqual((system["mode"], system["fallback"]), ("system", "light"))
        fixed = tk.declared_default_theme("", ":root { color-scheme: dark; }")
        self.assertEqual((fixed["mode"], fixed["fallback"]), ("fixed", "dark"))
        none = tk.declared_default_theme("", "")
        self.assertEqual((none["mode"], none["fallback"]), ("fixed", None))


class LiteralCensusTestCase(unittest.TestCase):
    def test_component_literals_negative_control(self):
        hits = tk.literal_census(CSS, "board.css")
        found = {(h["property"], h["value"]) for h in hits}
        self.assertIn(("color", "#fff"), found)
        self.assertIn(("border-radius", "6px"), found)
        self.assertIn(("font-size", "12.5px"), found)
        self.assertNotIn(("background", "var(--surface)"), found)
        self.assertFalse(any(h["property"] == "padding" for h in hits))  # spacing not in default families
        self.assertEqual(tk.literal_census(":root { --x: #fff; } .a { color: var(--x); }", "x.css"), [])


class DesignTokensTestCase(unittest.TestCase):
    def test_dual_value_split_and_default(self):
        doc = {"color": {"semantic": {"green": {"$type": "color", "$value": {"light": "#28cd41", "dark": "#32d74b"}}}},
               "layout": {"lane": {"width": {"$type": "dimension", "$value": "400px"}}},
               "theme": {"default": {"$type": "string", "$value": "light"}, "follows_system": {"$type": "boolean", "$value": True}}}
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "native-tokens.json")
            kit.make_repo(tmp, {"native-tokens.json": kit.tc.dump_json(doc)})
            loaded = tk.load_design_tokens(path)
        self.assertEqual(loaded["producer"]["mode"], "frozen")
        self.assertEqual(loaded["themes"]["light"]["color.semantic.green"]["$value"], "#28cd41ff")
        self.assertEqual(loaded["themes"]["dark"]["color.semantic.green"]["$value"], "#32d74bff")
        self.assertEqual(loaded["themes"]["dark"]["layout.lane.width"]["$value"], "400px")
        # theme.default 给了值 = 固定默认（follows_system 只是备注）——web 跟随系统就是 CHANGED mode（owner (b)）
        declared = loaded["default_theme"]["declared"]
        self.assertEqual((declared["mode"], declared["fallback"]), ("fixed", "light"))
        self.assertEqual(declared["evidence"], ["%s: theme.default = light" % path, "%s: theme.follows_system = true (informational)" % path])
        doc["theme"].pop("default")
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "native-tokens.json")
            kit.make_repo(tmp, {"native-tokens.json": kit.tc.dump_json(doc)})
            self.assertEqual(tk.load_design_tokens(path)["default_theme"]["declared"]["mode"], "system")
        self.assertEqual(loaded["families"], {"color": 1, "layout": 1, "theme": 2})

    def test_type_scale_ts(self):
        text = 'export const TYPE_SCALE = [\n  { token: "--type-card-title", font: "500 12px/1.4 var(--font-sans)", zh: "x" },\n];'
        scale = tk.parse_type_scale_ts(text)
        self.assertEqual(scale["typography.card-title"]["$value"]["weight"], 500)


class ExtractAndFindTestCase(unittest.TestCase):
    def test_extract_css_tokens_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit.make_repo(tmp, {"web/src/styles/tokens.css": CSS, "web/src/components/board/board.css": ".x { color: #000; }",
                                "web/index.html": '<html lang="en"><script>localStorage.getItem("zai.theme")</script></html>',
                                "web/src/styles/typeScale.ts": 'token: "--type-title", font: "600 15px/1.4 var(--font-sans)"'})
            files = ["web/src/styles/tokens.css", "web/src/components/board/board.css", "web/index.html", "web/src/styles/typeScale.ts"]
            found = tk.find_token_files(tmp, files, ["web"])
            self.assertEqual(found, {"css": ["web/src/styles/tokens.css"], "index_html": "web/index.html",
                                     "type_scale": "web/src/styles/typeScale.ts", "component_dirs": ["web/src/components/board"]})
            doc = tk.extract_css_tokens(tmp, found["css"], found["index_html"], found["type_scale"], found["component_dirs"])
            self.assertEqual(doc["producer"]["mode"], "source")
            self.assertEqual(doc["default_theme"]["declared"]["fallback"], "light")
            self.assertEqual([(h["file"], h["value"]) for h in doc["literals_outside"]], [("web/src/components/board/board.css", "#000")])
            self.assertIn("typography.title", doc["type_scale"])
            self.assertIn("color", doc["families"])


class CompareTokensTestCase(unittest.TestCase):
    def test_compare_and_required_families(self):
        ref = kit.make_tokens({"light": {"color.bg": "#fafbfc", "layout.lane.width": "400px", "spacing.x": "8px"},
                               "dark": {"color.bg": "#1b1d23"}})
        sub = kit.make_tokens({"light": {"color.bg": "#FAFBFC", "layout.lane.width": "401px"}, "dark": {"color.bg": "#000000"}})
        rows = {r["id"]: r for r in parity.compare_tokens(sub, ref, dict(parity.DEFAULT_THRESHOLDS))}
        self.assertEqual(rows["token:light:color.bg"]["status"], "PRESENT")          # case-insensitive hex
        self.assertEqual(rows["token:light:layout.lane.width"]["status"], "PRESENT")  # 1px within tolerance
        self.assertEqual(rows["token:dark:color.bg"]["status"], "CHANGED")
        self.assertNotIn("token:light:spacing.x", rows)                               # spacing not required → not MISSING
        sub2 = kit.make_tokens({"light": {"color.bg": "#fafbfc"}, "dark": {"color.bg": "#1b1d23"}})
        rows2 = {r["id"]: r for r in parity.compare_tokens(sub2, ref, dict(parity.DEFAULT_THRESHOLDS))}
        self.assertEqual(rows2["token:light:layout.lane.width"]["status"], "MISSING")

    def test_dimension_on_either_side_compares_in_px(self):
        """一侧标 dimension、另一侧只是字符串（`400.0px` / `25rem`）→ 按 px 比，不按文本比。"""
        ref = {"light": {"layout.lane.width": {"$type": "dimension", "$value": "400px"}}}
        sub = {"light": {"layout.lane.width": {"$type": "string", "$value": "25rem"}}}
        rows = parity.compare_tokens({"themes": sub}, {"themes": ref}, dict(parity.DEFAULT_THRESHOLDS))
        self.assertEqual(rows[0]["status"], "PRESENT")
        sub["light"]["layout.lane.width"]["$value"] = "398px"
        self.assertEqual(parity.compare_tokens({"themes": sub}, {"themes": ref}, dict(parity.DEFAULT_THRESHOLDS))[0]["status"], "CHANGED")

    def test_root_declarations_inherit_into_other_themes(self):
        """CSS 级联：dark 没重声明的 --native-layout-lane-width 在 dark 下生效的是 :root 值 → PRESENT(inherited)，不是
        MISSING；dark 重声明了不同的颜色照旧 CHANGED；:root 也没有才 MISSING。"""
        ref = kit.make_tokens({"light": {"layout.lane.width": "400px", "color.bg": "#fafbfc"}, "dark": {"layout.lane.width": "400px", "color.bg": "#1b1d23"}})
        sub = kit.make_tokens({"light": {"layout.lane.width": "400px", "color.bg": "#fafbfc"}, "dark": {"color.bg": "#000000"}})
        rows = {r["id"]: r for r in parity.compare_tokens(sub, ref, dict(parity.DEFAULT_THRESHOLDS))}
        self.assertEqual((rows["token:dark:layout.lane.width"]["status"], rows["token:dark:layout.lane.width"]["inherited"]), ("PRESENT", True))
        self.assertFalse(rows["token:light:layout.lane.width"]["inherited"])
        self.assertEqual(rows["token:dark:color.bg"]["status"], "CHANGED")
        sub2 = kit.make_tokens({"light": {"color.bg": "#fafbfc"}, "dark": {"color.bg": "#1b1d23"}})
        self.assertEqual({r["id"]: r for r in parity.compare_tokens(sub2, ref, dict(parity.DEFAULT_THRESHOLDS))}["token:dark:layout.lane.width"]["status"], "MISSING")

    def test_selectors_consuming(self):
        css = ":root { --native-layout-lane-width: 400px; }\n.lane, .strip { width: var(--native-layout-lane-width); }\n" \
              "@media (max-width: 900px) { .lane { width: var(--native-layout-lane-width); } }\n.other { color: red; }"
        self.assertEqual(tk.selectors_consuming([css], "--native-layout-lane-width"), [".lane, .strip", ".lane"])
        self.assertEqual(tk.selectors_consuming([css], "--native-layout-rail-default-width"), [])

    def test_theme_parity_negative_control(self):
        """dark 作用域缺 --surface → theme_parity 报 only_in light。"""
        doc = kit.make_tokens({"light": {"color.surface": "#fff", "color.bg": "#fff"}, "dark": {"color.bg": "#000"}})
        self.assertEqual(parity.theme_parity(doc), [{"path": "color.surface", "only_in": "light"}])
        hits = parity.rule_theme_parity(doc, parity.load_rules(), {})
        self.assertEqual((hits[0]["rule_id"], hits[0]["severity"]), ("tokens.theme_parity", "minor"))
        self.assertEqual(parity.theme_parity(kit.make_tokens({"light": {"a": "1px"}})), [])


if __name__ == "__main__":
    unittest.main()
