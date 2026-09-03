"""§66.3 设计 token 单源：scripts/ui/extract_native_tokens.py 的判例。

迷你 mac/Sources 钉住：语义色用量 → macOS light/dark 解析值、叠层比例键名、字号梯、
间距/圆角取值集、layout 定点（找不到即 fail-loud）、CSS 块的生成 / 替换 / 幂等、CLI 三态。
"""
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

_UI_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "ui")
if _UI_DIR not in sys.path:
    sys.path.insert(0, _UI_DIR)

import extract_native_tokens as tok  # noqa: E402

KANBAN = '''
struct KanbanView: View {
    var body: some View {
        ScrollView(.horizontal) {
            HStack(alignment: .top, spacing: 12) {
                lanes
            }
            .padding(16)
        }
        BoardFlightOverlay(controller: flights)
    }
    private func column() -> some View {
        VStack(alignment: .leading, spacing: 4) {
            ScrollView(.vertical) {
                LazyVStack(alignment: .leading, spacing: 8) { }
                .padding(.bottom, 10)
            }
        }
        .frame(width: 400)
        .frame(maxHeight: .infinity, alignment: .top)
        .background(Color.primary.opacity(0.018))
        .clipShape(RoundedRectangle(cornerRadius: 10))
        .boardMotionFrame("lane")
    }
    private func collapsedStrip() -> some View {
        Button(action: expand) {
            Text(title).font(.system(size: 12, weight: .semibold)).foregroundColor(.secondary)
            .frame(width: 44)
            .frame(maxHeight: .infinity, alignment: .top)
            .background(Color.primary.opacity(0.018))
            .clipShape(RoundedRectangle(cornerRadius: 10))
            .contentShape(RoundedRectangle(cornerRadius: 10))
        }
        .tint(.orange)
    }
}
'''

CARDS = '''
struct ApprovalCardView: View {
    var body: some View {
        CardSurface(bgOpacity: 0.04, padding: 10, cornerRadius: 8, stroked: true, idTag: card.id) {
            Text(card.summary).font(.system(size: 15, weight: .semibold)).foregroundColor(.primary)
            Text(note).font(.system(size: 10)).foregroundColor(.red)
            Text(more).font(.system(size: 10)).foregroundColor(.green)
        }
        .padding(8)
        .background(Color.accentColor.opacity(0.18))
        .background(Color.accentColor.opacity(0.10))
    }
}
'''

MAIN_WINDOW = '''
final class MainWindowController {
    func show() {
        let win = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 900, height: 640), styleMask: [])
        win.contentMinSize = NSSize(width: 720, height: 480)
    }
}
final class MainNav {
    private init() {
        let w = d.double(forKey: "sidebarWidth")
        sidebarWidth = w == 0 ? 200 : min(max(w, 160), 320)
    }
}
struct MainWindowView: View {
    private let collapsedWidth: Double = 48
}
'''


def _write(root):
    for name, text in (("Kanban.swift", KANBAN), ("Cards.swift", CARDS), ("MainWindow.swift", MAIN_WINDOW)):
        with open(os.path.join(root, name), "w", encoding="utf-8") as fh:
            fh.write(text)


class TokensTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        _write(cls.tmp.name)
        cls.tokens = tok.build_tokens(cls.tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_semantic_colors_resolve_to_macos_values_with_usage_counts(self):
        colors = self.tokens["color"]["semantic"]
        self.assertEqual(colors["orange"]["$value"], {"light": "#ff9500", "dark": "#ff9f0a"})
        self.assertEqual(colors["red"]["$extensions"]["zai"]["usages"], 1)
        self.assertEqual(colors["primary"]["$extensions"]["zai"]["usages"], 3)   # .primary ×1 + Color.primary ×2
        self.assertIn("windowBackground", colors)          # 窗口底永远带上
        self.assertNotIn("pink", colors)                   # 没用到的语义色不出现

    def test_overlay_keys_and_values(self):
        overlay = self.tokens["color"]["overlay"]
        self.assertEqual(overlay["primary-018"]["$value"], 0.018)
        self.assertEqual(overlay["primary-018"]["$extensions"]["zai"]["usages"], 2)
        self.assertEqual(overlay["accentColor-1"]["$value"], 0.1)
        self.assertEqual(overlay["accentColor-18"]["$value"], 0.18)

    def test_typography_spacing_radius(self):
        scale = self.tokens["typography"]["scale"]
        self.assertEqual(scale[0], {"size": 10, "weight": "regular", "usages": 2})
        self.assertEqual(scale[-1], {"size": 15, "weight": "semibold", "usages": 1})
        self.assertEqual(sorted(self.tokens["spacing"]["padding"]), ["10", "16", "8"])
        self.assertEqual(self.tokens["spacing"]["stack"]["12"]["$value"], "12px")
        self.assertEqual(sorted(self.tokens["radius"]), ["10", "8"])

    def test_layout_probes(self):
        layout = self.tokens["layout"]
        self.assertEqual(layout["lane"]["width"]["$value"], "400px")
        self.assertEqual(layout["lane"]["gap"]["$value"], "12px")
        self.assertEqual(layout["lane"]["radius"]["$value"], "10px")
        self.assertEqual(layout["strip"]["width"]["$value"], "44px")
        self.assertEqual(layout["board"]["padding"]["$value"], "16px")
        self.assertEqual(layout["card"]["radius"]["$value"], "8px")
        self.assertEqual(layout["rail"], {
            "collapsed_width": {"$type": "dimension", "$value": "48px", "$extensions": {"zai": {"source": "MainWindow.swift"}}},
            "default_width": {"$type": "dimension", "$value": "200px", "$extensions": {"zai": {"source": "MainWindow.swift"}}},
            "min_width": {"$type": "dimension", "$value": "160px", "$extensions": {"zai": {"source": "MainWindow.swift"}}},
            "max_width": {"$type": "dimension", "$value": "320px", "$extensions": {"zai": {"source": "MainWindow.swift"}}},
        })
        self.assertEqual(layout["window"]["min_height"]["$value"], "480px")
        self.assertEqual(self.tokens["theme"]["default"]["$value"], "light")

    def test_missing_layout_anchor_fails_loud(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "Kanban.swift"), "w", encoding="utf-8") as fh:
                fh.write("struct K: View { var body: some View { Text(\"x\") } }\n")
            with self.assertRaises(ValueError):
                tok.build_tokens(tmp)

    def test_css_block_render_and_splice(self):
        block = tok.render_css_block(self.tokens)
        self.assertIn("--native-default-theme: light;", block)
        self.assertIn("--native-layout-lane-width: 400px;", block)
        self.assertIn("--native-layout-rail-collapsed-width: 48px;", block)
        self.assertIn("--native-color-orange-light: #ff9500;", block)
        self.assertIn("--native-color-orange-dark: #ff9f0a;", block)
        self.assertIn("--native-overlay-primary-018: 0.018;", block)
        self.assertNotIn("--native-radius-", block)   # 泛取值集不出 CSS，只留 layout 命名圆角
        base = ":root {\n  --bg: #fff;\n}\n"
        spliced = tok.splice_block(base, block)
        self.assertTrue(spliced.startswith(base))
        self.assertTrue(spliced.endswith(tok.BLOCK_END + "\n"))
        self.assertEqual(tok.splice_block(spliced, block), spliced)          # 幂等
        other = tok.splice_block(spliced, block.replace("400px", "401px"))
        self.assertEqual(other.count(tok.BLOCK_BEGIN), 1)
        self.assertIn("401px", other)
        self.assertNotIn("400px", other)
        broken = tok.splice_block(spliced.replace(tok.BLOCK_END, ""), block)  # 缺尾标记 → 替换到文件末
        self.assertEqual(broken.count(tok.BLOCK_END), 1)

    def test_cli_write_then_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "src")
            os.makedirs(src)
            _write(src)
            out = os.path.join(tmp, "native-tokens.json")
            css = os.path.join(tmp, "tokens.css")
            with open(css, "w", encoding="utf-8") as fh:
                fh.write(":root { --x: 1; }\n")
            err = io.StringIO()
            with redirect_stdout(io.StringIO()), redirect_stderr(err):
                self.assertEqual(tok.main(["--check", "--root", src, "--out", out, "--css", css]), 1)
            self.assertIn("stale", err.getvalue())
            with redirect_stdout(io.StringIO()):
                self.assertEqual(tok.main(["--write", "--root", src, "--out", out, "--css", css]), 0)
                self.assertEqual(tok.main(["--check", "--root", src, "--out", out, "--css", css]), 0)
            with open(css, encoding="utf-8") as fh:
                text = fh.read()
            self.assertTrue(text.startswith(":root { --x: 1; }\n"))
            self.assertIn(tok.BLOCK_BEGIN, text)


if __name__ == "__main__":
    unittest.main()
