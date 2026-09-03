"""§66.2 vitest demo fixture 生成器（scripts/ui/parity_fixture.py）的判例。

fixture = demo_seed 的 initial 场景（固定 now，确定性）+ 封存卡 + 词表行（状态词 / tier 提示 /
截止 / 分歧 / 合并建议三态…）+ server/lanes.py 目录 + 空 home 的设置目录与凭证快照；形状必须让
web 的每个渲染面都有东西可画（看板六列、回收站、右侧书立条、设置页每个通用区）。
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
        self.assertEqual(a["counts"]["archived"], 4)
        self.assertEqual({row["archive_reason"] for row in a["archived"]}, {"user", "auto"})
        self.assertEqual({row["prev_status"] for row in a["archived"]}, {"delivered", "merged", "review"})
        # 词表行：每个 tier 提示 / 每种 running 状态词 / 合并建议三态都至少出现一次
        self.assertEqual({c.get("tier_hint") for c in a["needs_approval"]} >= {"自动执行", "一键可批", "需文字确认", "未分级"}, True)
        self.assertTrue({r["state"] for r in a["running"]} >= {"working", "queued", "dispatched", "idle", "unknown"})
        self.assertEqual([m["status"] for m in a["merge_suggestions"]][:3], ["analyzing", "done", "failed"])
        self.assertTrue({m.get("verdict") for m in a["merge_suggestions"]} >= {"partition", "merge", "keep_separate", None})
        self.assertTrue(any(r["kind"] == "debt" and r["permanent"] for r in a["trash"]))
        self.assertEqual(a["device_label"], "demo-mac")
        self.assertEqual(a["generated_at"], "2026-09-02T12:00:00Z")

    def test_settings_and_secrets_snapshots_are_defaults(self):
        settings = pf.build_settings()
        self.assertEqual([s["id"] for s in settings["sections"]], [s["id"] for s in pf.settings_catalog.SECTIONS])
        for section in settings["sections"]:
            for field in section["fields"]:
                # 唯一的 override 是笔记库目录（§68.1 目录字段的 打开 / 创建 词表行要一个非空路径才渲染）
                expected = "override" if field["key"] == "obsidian_raw" else "default"
                self.assertEqual(field["source"], expected, field["key"])
                if field.get("path"):
                    # 目录字段的存在性抹成常量（真实存在性依赖生成机器的磁盘）；空值仍是 null
                    self.assertEqual(field["path_exists"], pf._FIXTURE_PATH_EXISTS if field["effective"] else None, field["key"])
        secrets = pf.build_secrets()
        self.assertEqual(len(secrets["secrets"]), 5)
        self.assertEqual([s["name"] for s in secrets["secrets"] if s["present"]], ["anthropic-api-key.txt"])
        self.assertEqual(pf.build_secrets(), secrets)

    def test_lanes_catalog_mirrors_server_order(self):
        lanes = pf.build_lanes()
        self.assertEqual([lane["slug"] for lane in lanes["lanes"]],
                         ["debt", "needs_approval", "running", "review", "completed", "archived"])
        self.assertIn("zh", lanes["lanes"][0]["help"])

    def test_cli_write_then_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            board = os.path.join(tmp, "demo-board.json")
            lanes = os.path.join(tmp, "lanes.json")
            argv = ["--board", board, "--lanes", lanes, "--settings", os.path.join(tmp, "settings.json"),
                    "--secrets", os.path.join(tmp, "secrets.json")]
            err = io.StringIO()
            with redirect_stdout(io.StringIO()), redirect_stderr(err):
                self.assertEqual(pf.main(["--check"] + argv), 1)
            self.assertIn("stale", err.getvalue())
            with redirect_stdout(io.StringIO()):
                self.assertEqual(pf.main(["--write"] + argv), 0)
                self.assertEqual(pf.main(["--check"] + argv), 0)
            self.assertTrue(os.path.exists(board) and os.path.exists(lanes))


if __name__ == "__main__":
    unittest.main()
