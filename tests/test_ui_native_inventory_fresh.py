"""§64 UI 对齐契约：提交进仓的机器生成物必须与生成器重跑结果逐字节一致。

ui/parity/native-inventory.json（终版规格）、ui/tokens/native-tokens.json + tokens.css 的
`@generated native-tokens` 块、ui/parity/fixtures/*.json（vitest 的 demo fixture）都是
「脚本 → 文件」的产物；这里重跑一遍比对，任何手改或 mac/Sources 漂移都在这里红。
P8 删 mac/ 时本文件改成 tombstone（§6）。
"""
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_UI_DIR = os.path.join(_ROOT, "scripts", "ui")
if _UI_DIR not in sys.path:
    sys.path.insert(0, _UI_DIR)

import extract_native_inventory as inventory  # noqa: E402
import extract_native_tokens as tokens  # noqa: E402
import parity_fixture  # noqa: E402
import ui_common as uc  # noqa: E402


@unittest.skipUnless(os.path.isdir(uc.MAC_SOURCES), "mac/Sources retired (P8) — turn this file into a tombstone")
class GeneratedArtifactsFreshTestCase(unittest.TestCase):
    def test_native_inventory_json_matches_a_fresh_extraction(self):
        fresh = uc.dump_json(inventory.build_inventory())
        self.assertEqual(uc.read_text(uc.INVENTORY_PATH), fresh,
                         "ui/parity/native-inventory.json is stale — python3 scripts/ui/extract_native_inventory.py --write")

    def test_inventory_is_non_trivial_and_carries_the_owner_named_deltas(self):
        doc = uc.load_json(uc.INVENTORY_PATH)
        self.assertEqual([r["slug"] for r in doc["rail"]["items"]],
                         ["dashboard", "ask", "deps", "ingest", "trash", "archive", "settings", "about"])
        self.assertEqual(doc["lanes"]["order"], ["debt", "needs_approval", "running", "review", "completed", "archived"])
        self.assertEqual([lane["rail"] for lane in doc["lanes"]["items"]], ["left", None, None, None, None, "right"])
        self.assertGreaterEqual(len([s for s in doc["screens"] if s["kind"] == "settings-section"]), 19)
        self.assertGreater(len([c for c in doc["controls"] if c["gated"]]), 500)
        self.assertEqual(next(t for t in doc["theme_layout"] if t["id"] == "theme:default")["value"], "light")

    def test_native_tokens_json_and_css_block_are_fresh(self):
        built = tokens.build_tokens()
        self.assertEqual(uc.read_text(uc.NATIVE_TOKENS_PATH), uc.dump_json(built),
                         "ui/tokens/native-tokens.json is stale — python3 scripts/ui/extract_native_tokens.py --write")
        css = uc.read_text(tokens.TOKENS_CSS)
        self.assertEqual(css, tokens.splice_block(css, tokens.render_css_block(built)),
                         "tokens.css native-tokens block is stale — python3 scripts/ui/extract_native_tokens.py --write")
        self.assertEqual(built["layout"]["lane"]["width"]["$value"], "400px")
        self.assertEqual(built["layout"]["strip"]["width"]["$value"], "44px")
        self.assertEqual(built["layout"]["rail"]["collapsed_width"]["$value"], "48px")
        self.assertEqual(built["theme"]["default"]["$value"], "light")

    def test_parity_fixtures_are_fresh(self):
        self.assertEqual(uc.read_text(parity_fixture.BOARD_PATH), uc.dump_json(parity_fixture.build_board()),
                         "ui/parity/fixtures/demo-board.json is stale — python3 scripts/ui/parity_fixture.py --write")
        self.assertEqual(uc.read_text(parity_fixture.LANES_PATH), uc.dump_json(parity_fixture.build_lanes()),
                         "ui/parity/fixtures/lanes.json is stale — python3 scripts/ui/parity_fixture.py --write")


if __name__ == "__main__":
    unittest.main()
