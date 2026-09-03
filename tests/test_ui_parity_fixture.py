"""§63.2 vitest demo fixture 生成器（scripts/ui/parity_fixture.py）的判例。

fixture = demo_seed 的 initial 场景（固定 now，确定性）+ 两行封存卡 + server/lanes.py
目录；形状必须让 web 的每个渲染面都有东西可画（看板六列、回收站、右侧书立条）。
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

import parity_fixture as pf  # noqa: E402


class FixtureShapeTestCase(unittest.TestCase):
    def test_board_has_every_lane_populated_and_is_deterministic(self):
        a = pf.build_board()
        b = pf.build_board()
        self.assertEqual(a, b)
        for lane in ("needs_approval", "running", "needs_input", "review", "completed", "debt", "trash", "archived"):
            self.assertGreater(len(a[lane]), 0, lane)
        self.assertEqual(a["counts"]["archived"], 2)
        self.assertEqual({row["archive_reason"] for row in a["archived"]}, {"user", "auto"})
        self.assertEqual(a["device_label"], "demo-mac")
        self.assertEqual(a["generated_at"], "2026-09-02T12:00:00Z")

    def test_lanes_catalog_mirrors_server_order(self):
        lanes = pf.build_lanes()
        self.assertEqual([lane["slug"] for lane in lanes["lanes"]],
                         ["debt", "needs_approval", "running", "review", "completed", "archived"])
        self.assertIn("zh", lanes["lanes"][0]["help"])

    def test_cli_write_then_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            board = os.path.join(tmp, "demo-board.json")
            lanes = os.path.join(tmp, "lanes.json")
            err = io.StringIO()
            with redirect_stdout(io.StringIO()), redirect_stderr(err):
                self.assertEqual(pf.main(["--check", "--board", board, "--lanes", lanes]), 1)
            self.assertIn("stale", err.getvalue())
            with redirect_stdout(io.StringIO()):
                self.assertEqual(pf.main(["--write", "--board", board, "--lanes", lanes]), 0)
                self.assertEqual(pf.main(["--check", "--board", board, "--lanes", lanes]), 0)
            self.assertTrue(os.path.exists(board) and os.path.exists(lanes))


if __name__ == "__main__":
    unittest.main()
