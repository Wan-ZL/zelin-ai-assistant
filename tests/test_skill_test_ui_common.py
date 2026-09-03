"""test-ui skill · 共用件判例：颜色数学（对比度 4.5 边界、alpha 合成、hsl）、PNG 编解码往返 + 五种
过滤器 + fail closed（截断 / 16-bit / 坏签名）、slugify 与 parity 契约同口径、配对键、tokens 扁平化、
清单 schema 校验（坏清单报出路径）。零子进程。

法典：docs/CONTRACT.md §UI-parity（id 语法）；设计 vnext2-plan R2.8。负控制：#8a8f99 on white < 4.5；截断 PNG 抛。
"""
import struct
import unittest
import zlib

from tests import skill_test_ui_testkit as kit

tc = kit.tc


class ColorTestCase(unittest.TestCase):
    def test_parse_forms(self):
        self.assertEqual(tc.canonical_color("#fff"), "#ffffffff")
        self.assertEqual(tc.canonical_color("#12758c"), "#12758cff")
        self.assertEqual(tc.canonical_color("rgb(18, 117, 140)"), "#12758cff")
        self.assertEqual(tc.canonical_color("rgba(0, 0, 0, 0.5)"), "#00000080")
        self.assertEqual(tc.canonical_color("hsl(0, 100%, 50%)"), "#ff0000ff")
        self.assertEqual(tc.canonical_color("transparent"), "#00000000")
        self.assertIsNone(tc.canonical_color("var(--bg)"))
        self.assertIsNone(tc.canonical_color("#12"))

    def test_unparseable_channels_are_none_not_a_crash(self):
        """Tailwind / shadcn 的 token 表写 `rgb(var(--r) var(--g) var(--b) / <alpha-value>)`、`hsl(var(--h) …)`；
        `hsl(1turn …)`、`rgb(a, b, c)` 也一样——认不出就是 None，tokens_source 不许因此整层崩成 FAIL。"""
        for text in ("rgb(var(--r) var(--g) var(--b) / <alpha-value>)", "hsl(var(--primary))", "hsl(1turn 50% 50%)",
                     "rgb(a, b, c)", "rgba(0, 0, 0, 50%%)"):
            self.assertIsNone(tc.parse_color(text), text)
        self.assertEqual(tc.canonical_color("rgb(255 0 0 / 50%)"), "#ff000080")
        self.assertEqual(tc.canonical_color("hsl(120deg 100% 50%)"), "#00ff00ff")

    def test_composite_over_white(self):
        """rgba(0,0,0,.5) over white → #808080ff（对比度必须在合成色上算）。"""
        fg, bg = tc.parse_color("rgba(0,0,0,.5)"), tc.parse_color("white")
        self.assertEqual(tc.to_hex8(tc.composite(fg, bg)), "#808080ff")

    def test_contrast_boundary_negative_control(self):
        """#8a8f99 on #ffffff ≈ 3.3 → 低于 4.5（负控制）；#1a1c22 on white ≫ 4.5。"""
        grey = tc.contrast_ratio(tc.parse_color("#8a8f99"), tc.parse_color("#ffffff"))
        self.assertLess(grey, 4.5)
        self.assertGreater(grey, 3.0)
        self.assertGreater(tc.contrast_ratio(tc.parse_color("#1a1c22"), tc.parse_color("#ffffff")), 15)
        self.assertEqual(tc.contrast_ratio(tc.parse_color("#000000"), tc.parse_color("#ffffff")), 21.0)


class PngTestCase(unittest.TestCase):
    def test_roundtrip_rgb_and_rgba(self):
        data = kit.make_png(5, 3, (1, 2, 3), [[1, 1, 2, 1, (250, 0, 0)]])
        w, h, ch, rows = tc.decode_png(data)
        self.assertEqual((w, h, ch), (5, 3, 3))
        self.assertEqual(rows[1][3:6], bytes((250, 0, 0)))
        rgba = tc.encode_png(2, 1, [bytes((1, 2, 3, 4, 5, 6, 7, 8))], 4)
        self.assertEqual(tc.decode_png(rgba)[2:], (4, [bytes((1, 2, 3, 4, 5, 6, 7, 8))]))

    def _png_with_filter(self, ftype, rows):
        """手工构造带指定过滤器类型字节的 PNG（过滤后的数据用 filter 0 的原值——解码器只按类型反过滤）。"""
        raw = b"".join(bytes([ftype]) + bytes(row) for row in rows)
        ihdr = struct.pack(">IIBBBBB", len(rows[0]) // 3, len(rows), 8, 2, 0, 0, 0)
        return tc._PNG_SIG + tc._chunk(b"IHDR", ihdr) + tc._chunk(b"IDAT", zlib.compress(raw)) + tc._chunk(b"IEND", b"")

    def test_unfilter_each_filter_type(self):
        """五种过滤器各走一遍：sub/up/average/paeth 的反过滤公式对已知输入给已知输出。"""
        self.assertEqual(tc._unfilter_sub(bytearray([1, 1, 1, 1, 1, 1]), bytearray(6), 3), bytearray([1, 1, 1, 2, 2, 2]))
        self.assertEqual(tc._unfilter_up(bytearray([1, 2, 3]), bytearray([10, 10, 10]), 3), bytearray([11, 12, 13]))
        self.assertEqual(tc._unfilter_average(bytearray([4, 4, 4, 4, 4, 4]), bytearray([2, 2, 2, 2, 2, 2]), 3),
                         bytearray([5, 5, 5, 7, 7, 7]))
        self.assertEqual(tc._paeth(1, 2, 3), 1)
        self.assertEqual(tc._unfilter_paeth(bytearray([1, 1, 1]), bytearray([0, 0, 0]), 3), bytearray([1, 1, 1]))
        for ftype in range(5):
            w, h, ch, rows = tc.decode_png(self._png_with_filter(ftype, [bytes((9, 9, 9)), bytes((1, 1, 1))]))
            self.assertEqual((w, h, ch, len(rows)), (1, 2, 3, 2))

    def test_fail_closed(self):
        data = kit.make_png(4, 4)
        with self.assertRaises(ValueError):
            tc.decode_png(data[:40])  # truncated
        with self.assertRaises(ValueError):
            tc.decode_png(b"GIF89a" + data[6:])  # bad signature
        ihdr16 = struct.pack(">IIBBBBB", 1, 1, 16, 2, 0, 0, 0)
        bad_depth = tc._PNG_SIG + tc._chunk(b"IHDR", ihdr16) + tc._chunk(b"IDAT", zlib.compress(b"\x00" * 7)) + tc._chunk(b"IEND", b"")
        with self.assertRaises(ValueError):
            tc.decode_png(bad_depth)
        with self.assertRaises(ValueError):
            tc.decode_png(self._png_with_filter(7, [bytes((1, 1, 1))]))
        with self.assertRaises(ValueError):
            tc.encode_png(1, 1, [b"\x00"], channels=1)

    def test_corrupt_stream_and_bad_crc_are_value_errors(self):
        """坏块要以 ValueError 出来，visual._shot_row 才能把这张图记成 CHANGED/unreadable 而不是让整层崩：
        IDAT 内容被改（deflate 解不开 → zlib.error 不许漏出）；块 CRC 与内容不符（docstring 承诺的「CRC 坏」）。"""
        data = kit.make_png(2, 2)
        idat = data.find(b"IDAT")
        corrupt = data[:idat + 4] + b"\xff\xff\xff\xff" + data[idat + 8:]
        with self.assertRaises(ValueError) as caught:
            tc.decode_png(corrupt)
        self.assertIn("png:", str(caught.exception))
        iend = data.rfind(b"IEND")
        bad_crc = data[:iend + 4] + b"\x00\x00\x00\x00"
        with self.assertRaises(ValueError) as caught:
            tc.decode_png(bad_crc)
        self.assertIn("bad CRC", str(caught.exception))
        self.assertEqual(tc.decode_png(data)[:3], (2, 2, 3))  # the intact file still decodes


class IdGrammarTestCase(unittest.TestCase):
    def test_slugify_matches_parity_contract(self):
        """与 scripts/ui/ui_common.slugify 同口径：小写、非字母数字折 -、保留汉字、截断 48、空 → item。"""
        self.assertEqual(tc.slugify("Approve"), "approve")
        self.assertEqual(tc.slugify("Could not open Terminal."), "could-not-open-terminal")
        self.assertEqual(tc.slugify("批准 · approve"), "批准-approve")
        self.assertEqual(tc.slugify("Last checked 0"), "last-checked-0")
        self.assertEqual(tc.slugify("!!!"), "item")
        self.assertEqual(len(tc.slugify("x" * 80)), 48)

    def test_make_id_and_pair_key(self):
        self.assertEqual(tc.make_id("control", "board", "button", "approve"), "control:board:button:approve")
        self.assertEqual(tc.pair_key("board.card", "button", "Approve "), ("board", "button", "approve"))
        self.assertEqual(tc.screen_family("settings.models"), "settings")
        self.assertEqual(tc.screen_family(""), "window")

    def test_normalize_name(self):
        self.assertEqual(tc.normalize_name("  Last  checked 12 "), "last checked {n}")
        self.assertEqual(tc.normalize_name("Hello ${name}"), "hello {}")
        self.assertEqual(tc.normalize_name(None), "")
        # 首尾装饰符号（emoji / 省略号 / 冒号）不是改名——slugify 配对时本就忽略它们
        self.assertEqual(tc.normalize_name("🗑 Trash"), tc.normalize_name("Trash"))
        self.assertEqual(tc.normalize_name("Answer…"), "answer")
        self.assertEqual(tc.normalize_name("Delivered:"), "delivered")
        self.assertNotEqual(tc.normalize_name("Re-raised"), tc.normalize_name("Raised"))


class TokensFlattenTestCase(unittest.TestCase):
    def test_flatten_and_values(self):
        nested = {"$description": "x", "layout": {"lane": {"width": {"$type": "dimension", "$value": "400px"}}},
                  "color": {"bg": {"$type": "color", "$value": "#FAFBFC"}}}
        flat = tc.flatten_tokens(nested)
        self.assertEqual(sorted(flat), ["color.bg", "layout.lane.width"])
        self.assertEqual(tc.token_value_text(flat["color.bg"]), "#fafbfcff")
        self.assertEqual(tc.token_value_text({"$type": "typography", "$value": {"size": 15, "weight": 600}}),
                         '{"size": 15, "weight": 600}')
        self.assertEqual(tc.px_value("400px"), 400.0)
        self.assertEqual(tc.px_value("1.5rem"), 24.0)
        self.assertIsNone(tc.px_value("auto"))


class SchemaTestCase(unittest.TestCase):
    def test_validate_inventory_reports_paths(self):
        inv = kit.make_inventory([kit.make_item("board", "button", "Approve")])
        self.assertEqual(tc.validate_inventory(inv), [])
        inv["items"].append({"id": "x", "key": {"screen": "b", "role": "widget", "slug": "x"}, "kind": "static",
                             "name": {"raw": "x"}, "topology": {}, "states": {}})
        self.assertEqual(tc.validate_inventory(inv), ["items[1].role"])
        inv["items"].append("garbage")
        self.assertIn("items[2]", tc.validate_inventory(inv))
        self.assertEqual(tc.validate_inventory([]), ["<root>"])
        bad_mode = kit.make_inventory([])
        bad_mode["producer"]["mode"] = "guess"
        self.assertEqual(tc.validate_inventory(bad_mode), ["producer.mode"])


if __name__ == "__main__":
    unittest.main()
