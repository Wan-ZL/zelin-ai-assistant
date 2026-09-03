"""test-ui skill · 源清单提取器判例（web-source）：隐式角色 / role= / aria-label / labelledby / <label for> /
alt / title；hidden 三态；双语 `text("zh","en")`；`{flags.x && …}` gated；data-parity-id pin；组件子节点 = {dynamic}；
容器只认显式标签；TSX 的 `=>`、泛型、行首注释不当标签；同 id #n 序号跨文件全局；project:native 归一。
负控制：无名图标按钮 → unnamed；display:none → hidden。零子进程（native 走内存 dict）。

法典：docs/CONTRACT.md §UI-parity（id 语法、role 映射表）；设计 vnext2-plan R2.8。
"""
import unittest

from tests import skill_test_ui_testkit as kit

import inventory_a11y as inv  # noqa: E402

TSX = """// 顶栏：<select> 在注释里不是元素
import { useI18n } from "../../i18n";
export function HeaderBar({ items }: Props) {
  const { text } = useI18n();
  const ref = useRef<HTMLSpanElement>(null);
  return (
    <header className="shell-header">
      <h1 className="shell-title">{text("Zelin 的 AI 助理", "Zelin's AI Assistant")}</h1>
      <a className="shell-trash-link" href={buildAppUrl(window.location.href, "trash", null).toString()}>
        {text("回收站", "Trash")}
      </a>
      <button type="button" aria-label={text("设置", "Settings")} onClick={() => setOpen((v) => !v)}>
        <svg width="15" height="15" aria-hidden="true"><path d="M1 2" /></svg>
      </button>
      {flags.captions && <button type="button">{text("字幕", "Captions")}</button>}
      {items.map((item) => (
        <button key={item.id} type="button" disabled={busy}>{item.label}</button>
      ))}
      <button type="button" className="icon"><svg /></button>
      <span className="dyn">{count}</span>
      <a href="#x"><InlineView nodes={nodes} /></a>
      <input id="q" type="search" placeholder="…" />
      <label htmlFor="q">{text("搜索", "Search")}</label>
    </header>
  );
}
"""


def _items(text, screen="shell", rel="HeaderBar.tsx"):
    items, marks = inv.SourceExtractor(text, screen, rel).run()
    return inv.finish_inventory(kit.make_inventory(items, landmarks=marks))["items"]


class TsxTestCase(unittest.TestCase):
    def test_roles_names_and_bilingual(self):
        by = {i["id"]: i for i in _items(TSX)}
        self.assertIn("landmark:shell:banner:banner", by)
        heading = by["heading:shell:heading:zelin-s-ai-assistant"]
        self.assertEqual((heading["name"]["zh"], heading["name"]["en"], heading["level"]), ("Zelin 的 AI 助理", "Zelin's AI Assistant", 1))
        self.assertEqual(by["control:shell:link:trash"]["name_source"], "text")
        self.assertEqual(by["control:shell:button:settings"]["name_source"], "aria-label")
        self.assertNotIn("control:shell:combobox:combobox", by)  # <select> in the header comment is not an element

    def test_gated_dynamic_and_unnamed(self):
        by = {i["id"]: i for i in _items(TSX)}
        self.assertTrue(by["control:shell:button:captions"]["gated"])
        self.assertFalse(by["control:shell:button:settings"]["gated"])
        dyn = by["control:shell:button:dynamic"]
        self.assertTrue(dyn["dynamic"])
        self.assertTrue(dyn["states"]["source"]["focusable"], "disabled={busy} is runtime state, not literal disabled")
        self.assertEqual(by["control:shell:button:unnamed"]["name_source"], "none")  # negative control: icon button
        self.assertEqual(by["control:shell:link:dynamic"]["name"]["raw"], "{dynamic}")  # component child
        self.assertNotIn("control:shell:static:dynamic", by)  # <span>{count}</span> is not a static item
        self.assertEqual(by["control:shell:searchbox:search"]["name_source"], "label")

    def test_hidden_states_negative_control(self):
        html = '<main><button style="display:none">A</button><button hidden>B</button><div aria-hidden="true"><button>C</button></div>' \
               '<button disabled>D</button><button tabindex="-1">E</button><button>F</button></main>'
        by = {i["name"]["raw"]: i["states"]["source"] for i in _items(html, "x", "x.html")}
        self.assertEqual(by["A"]["hidden_by"], "display:none")
        self.assertEqual(by["B"]["hidden_by"], "hidden")
        self.assertEqual(by["C"]["hidden_by"], "aria-hidden")
        self.assertFalse(by["D"]["focusable"])
        self.assertFalse(by["E"]["focusable"])
        self.assertTrue(by["F"]["focusable"] and by["F"]["visible"])

    def test_pin_ordinals_and_containers(self):
        html = '<ul aria-label="L"><li>one <button>Go</button></li><li>two <button>Go</button></li></ul>' \
               '<span data-parity-id="control:x:button:go">Go!</span>'
        items = _items(html, "x", "x.html")
        by = {i["id"]: i for i in items}
        self.assertEqual(by["control:x:button:go"]["count"], 2)
        self.assertIn("control:x:button:go#2", by)
        self.assertEqual(by["control:x:listitem:listitem"]["name"]["raw"], "")  # container: label-only
        self.assertEqual(by["control:x:static:go"]["pin"], "control:x:button:go")

    def test_role_table(self):
        html = '<div role="switch" aria-label="S"></div><input type="checkbox" id="c"><label for="c">Check</label>' \
               '<input type="hidden"><a>no href</a><img alt="Logo"><img><section aria-label="R">x</section><div role="widget">w</div>'
        by = {i["id"]: i for i in _items(html, "x", "x.html")}
        self.assertIn("control:x:switch:s", by)
        self.assertEqual(by["control:x:checkbox:check"]["name_source"], "label")
        self.assertIn("control:x:img:logo", by)
        self.assertIn("control:x:img:img", by)  # unnamed image keeps role slug
        self.assertNotIn("control:x:textbox:unnamed", by)  # type=hidden skipped
        self.assertFalse(any(i["key"]["role"] == "link" for i in by.values()))  # <a> without href
        self.assertFalse(any(i["key"]["role"] == "generic" for i in by.values()))  # unknown explicit role → generic → no item

    def test_screen_mapping(self):
        self.assertEqual(inv.screen_for_file("web/src/pages/BoardPage.tsx"), "board")
        self.assertEqual(inv.screen_for_file("web/src/components/settings/ModelKnob.tsx"), "settings")
        self.assertEqual(inv.screen_for_file("board.html"), "board")
        self.assertEqual(inv.screen_for_file("web/src/x/Y.tsx", [{"id": "custom", "source": ["web/src/x/*"]}]), "custom")


class ExtractTreeTestCase(unittest.TestCase):
    def test_extract_source_dir_and_errors(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            kit.copy_fixture("ref", tmp)
            result = inv.extract_source(tmp)
            self.assertEqual(result["producer"]["mode"], "source")
            self.assertEqual([s["id"] for s in result["screens"]], ["board", "settings"])
            self.assertIn("control:board:button:批准", {i["id"] for i in result["items"]})
            self.assertIn("批准", result["names"])
            tree = inv.extract_tree(tmp)
            self.assertEqual(len(tree["items"]), len(result["items"]))
            self.assertEqual(inv.surface_roots(["a/b.html", "a/c.html"]), [("static-html", "a")])
            self.assertEqual(inv.surface_roots(["web/src/x.tsx", "docs/y.html"]), [("web-react", "web/src")])
            self.assertEqual(inv.surface_roots(["readme.md"]), [])


class NativeAdapterTestCase(unittest.TestCase):
    NATIVE = {"source": {"dir": "mac/Sources", "files": 1, "sha256": "abc"},
              "controls": [{"id": "control:board:button:approve", "zh": "批准", "en": "Approve", "role": "button", "screen": "board",
                            "owner": "web", "gated": True, "source": "Cards.swift:12"},
                           {"id": "control:about:label:about", "zh": "关于", "en": "About", "role": "label", "screen": "about", "owner": "web", "gated": True},
                           {"id": "control:board:toggle:mute", "zh": "静音", "en": "Mute", "role": "toggle", "screen": "board.card", "owner": "shell", "gated": True}],
              "rail": {"side": "left", "items": [{"id": "rail:dashboard", "zh": "任务台", "en": "Workbench", "slug": "dashboard", "index": 0, "owner": "web", "gated": True, "shortcut": "⌘1"}]},
              "lanes": {"items": [{"id": "lane:debt", "zh": "潜在任务", "en": "Backlog", "slug": "debt", "index": 0, "rail": "left", "owner": "web", "gated": True},
                                  {"id": "lane:needs_approval", "zh": "提案", "en": "Proposals", "slug": "needs_approval", "index": 1, "rail": None, "owner": "web", "gated": True}], "order": ["debt", "needs_approval"]},
              "screens": [{"id": "screen:settings", "zh": "设置", "en": "Settings", "kind": "rail-page", "owner": "web", "gated": True}],
              "shortcuts": [{"id": "shortcut:menu.main:cmd-,-settings", "zh": "设置…", "en": "Settings…", "key": "⌘,", "screen": "menu.main", "owner": "shell", "gated": False}],
              "settings_keys": [{"id": "setting:overrides:create_github_repo", "key": "create_github_repo", "store": "overrides", "owner": "web", "gated": True}],
              "theme_layout": [{"id": "theme:default", "value": "light", "owner": "web", "gated": True},
                               {"id": "layout:lane-width", "token": "layout.lane.width", "owner": "web", "gated": True}]}

    def test_normalize_native(self):
        result = inv.normalize_native(self.NATIVE, "/x/native-inventory.json")
        self.assertEqual(result["producer"]["mode"], "frozen")
        by = {i["id"]: i for i in result["items"]}
        approve = by["control:board:button:approve"]
        self.assertEqual((approve["key"], approve["name"]["en"], approve["source"]), ({"screen": "board", "role": "button", "slug": "approve"}, "Approve", {"file": "Cards.swift", "line": 12}))
        self.assertEqual(by["control:about:label:about"]["key"]["role"], "static")
        self.assertEqual((by["control:board:toggle:mute"]["key"]["role"], by["control:board:toggle:mute"]["owner"]), ("switch", "shell"))
        self.assertEqual(by["rail:navigation"]["topology"]["side"], "left")
        self.assertEqual((by["rail:dashboard"]["key"]["role"], by["rail:dashboard"]["shortcut"]), ("link", "⌘1"))
        self.assertEqual(by["lane:debt"]["topology"]["side"], "left")
        self.assertEqual(by["lane:needs_approval"]["topology"]["side"], "inside")
        self.assertEqual(by["screen:settings"]["key"]["role"], "heading")
        self.assertEqual(by["setting:overrides:create_github_repo"]["key"]["role"], "switch")
        self.assertEqual(result["dims"]["default_theme"], "light")
        self.assertEqual(result["layout_pointers"], {"layout:lane-width": "layout.lane.width"})
        self.assertEqual(kit.tc.validate_inventory(result), [])

    def test_load_native_paths(self):
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(inv.load_native(tmp))
            kit.make_repo(tmp, {"ui/parity/native-inventory.json": kit.tc.dump_json(self.NATIVE)})
            self.assertEqual(inv.load_native(tmp)["producer"]["mode"], "frozen")
            os.remove(os.path.join(tmp, "ui/parity/native-inventory.json"))
            kit.make_repo(tmp, {"scripts/ui/extract_native_inventory.py": "# stub"})
            runner = kit.FakeRunner(default=(1, "", "boom"))
            self.assertIsNone(inv.load_native(tmp, out_dir=os.path.join(tmp, "out"), runner=runner))
            self.assertIn("--out", runner.commands()[0])


if __name__ == "__main__":
    unittest.main()
