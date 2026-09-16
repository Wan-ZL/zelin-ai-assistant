"""PWA 安装清单 + 生成的图标（docs/CONTRACT.md §73）。

四件事钉在这里：index.html 挂着 `<link rel="manifest">`；清单的 `name` 与
`<title>` 逐字相同；主题色/背景色逐字取自 `ui/tokens/native-tokens.json` 的
`windowBackground.light`（§66.3 token 单源，不许手写字面量）；两张 PNG 是
`scripts/ui/make_pwa_icons.py` 从 favicon.svg 栅格化的结果——重跑零 diff
（口径 = 像素，见该脚本 docstring）。
"""
import io
import json
import os
import re
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

_UI_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "ui")
if _UI_DIR not in sys.path:
    sys.path.insert(0, _UI_DIR)

import make_pwa_icons as icons  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST_PATH = os.path.join(REPO_ROOT, "web", "public", "manifest.webmanifest")
INDEX_PATH = os.path.join(REPO_ROOT, "web", "index.html")
TOKENS_PATH = os.path.join(REPO_ROOT, "ui", "tokens", "native-tokens.json")


def _read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def _manifest():
    return json.loads(_read(MANIFEST_PATH))


def _window_background():
    tokens = json.loads(_read(TOKENS_PATH))
    return tokens["color"]["semantic"]["windowBackground"]["$value"]


class ManifestShapeTestCase(unittest.TestCase):
    """安装清单本身：独立窗口 + 相对 start_url/scope + 三张图标。"""

    def test_index_html_links_the_manifest(self):
        link = re.search(r"<link[^>]*rel=\"manifest\"[^>]*>", _read(INDEX_PATH))
        self.assertIsNotNone(link, "web/index.html 没有 <link rel=\"manifest\">")
        self.assertIn('href="./manifest.webmanifest"', link.group(0))

    def test_name_is_the_document_title_verbatim(self):
        title = re.search(r"<title>(.*?)</title>", _read(INDEX_PATH), re.S)
        self.assertEqual(_manifest()["name"], title.group(1).strip())

    def test_standalone_window_rooted_at_the_document(self):
        doc = _manifest()
        self.assertEqual(doc["short_name"], "AI Assistant")
        self.assertEqual(doc["display"], "standalone")
        # 路由只用 query、文档恒在根：相对 "./" 让清单跟着端口/宿主走（vite base "./"）
        self.assertEqual(doc["start_url"], "./")
        self.assertEqual(doc["scope"], "./")

    def test_colours_come_from_the_native_tokens(self):
        doc, token = _manifest(), _window_background()
        self.assertEqual(doc["theme_color"], token["light"])
        self.assertEqual(doc["background_color"], token["light"])
        # 看板默认浅色且不跟随系统深色（§54.4）——暗色值在清单里没有落点
        self.assertNotIn(token["dark"], (doc["theme_color"], doc["background_color"]))

    def test_icons_exist_with_the_declared_types_and_sizes(self):
        declared = {icon["src"]: icon for icon in _manifest()["icons"]}
        self.assertEqual(declared["./favicon.svg"]["sizes"], "any")
        self.assertEqual(declared["./favicon.svg"]["type"], "image/svg+xml")
        for size in icons.SIZES:
            icon = declared["./icon-%d.png" % size]
            self.assertEqual(icon["type"], "image/png")
            self.assertEqual(icon["sizes"], "%dx%d" % (size, size))
            header, _rows = icons.pixels(_blob(icons.icon_path(size)))
            self.assertEqual(header[:2], (size, size))
            self.assertEqual(header[2:4], (8, 6))  # 8-bit RGBA


def _blob(path):
    with open(path, "rb") as handle:
        return handle.read()


class IconRegenTestCase(unittest.TestCase):
    """图标 = favicon.svg 的函数：committed 像素 == 重新栅格化的像素。"""

    def test_committed_icons_match_a_fresh_rasterisation(self):
        shapes = icons.load_shapes()
        for size in icons.SIZES:
            fresh = icons.render(size, shapes)
            self.assertEqual(icons.pixels(_blob(icons.icon_path(size))),
                             icons.pixels(fresh), "icon-%d.png 与 favicon.svg 不同步" % size)

    def test_rasterisation_is_deterministic_in_process(self):
        shapes = icons.load_shapes()
        self.assertEqual(icons.render(192, shapes), icons.render(192, shapes))

    def test_check_mode_passes_on_the_committed_icons(self):
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(icons.main(["--check"]), 0)
        self.assertIn("fresh", out.getvalue())

    def test_write_then_check_round_trips_and_a_tampered_icon_is_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = {size: os.path.join(tmp, "icon-%d.png" % size) for size in icons.SIZES}
            with mock.patch.object(icons, "icon_path", paths.__getitem__):
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(icons.main(["--write", "--check"]), 0)
                with open(paths[192], "wb") as handle:
                    handle.write(icons.render(512))  # 换成另一张真 PNG：像素不同
                err = io.StringIO()
                with redirect_stdout(io.StringIO()), redirect_stderr(err):
                    self.assertEqual(icons.main(["--check"]), 1)
                self.assertIn("icon-192.png", err.getvalue())


class IconGeometryTestCase(unittest.TestCase):
    """栅格化本身：形状真源是 favicon.svg，坏形状 fail-loud。"""

    def test_shapes_are_read_from_the_favicon(self):
        viewbox, rects = icons.load_shapes()
        self.assertEqual(viewbox, 64.0)
        self.assertEqual(rects[0]["rgb"], (0x12, 0x75, 0x8C))  # 圆角底板 = tokens.css 的 accent
        self.assertEqual(len(rects), 4)                        # 底板 + 三根看板柱
        self.assertTrue(all(0.0 < r["alpha"] <= 1.0 for r in rects))

    def test_background_is_opaque_inside_and_transparent_outside_the_corner(self):
        header, rows = icons.pixels(_blob(icons.icon_path(192)))
        stride = header[0] * 4 + 1
        center = 96 * stride + 1 + 96 * 4
        self.assertEqual(rows[center + 3], 255)       # 正中：不透明
        self.assertEqual(rows[1 + 3], 0)              # 左上角像素：圆角外，全透明

    def test_a_broken_svg_is_loud(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "bad.svg")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 32"></svg>')
            with self.assertRaises(ValueError):
                icons.load_shapes(path)


if __name__ == "__main__":
    unittest.main()
