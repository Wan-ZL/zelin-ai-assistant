"""steer 标注与 queued_reason/steers 透传（M6，vnext-amendments §M6）。

两条纪律钉死：
1. POST /api/actions 的 steer 标注只活在 **响应** 里（add-only 键 steer/
   steer_status）——inbox 文件本体保持 §3 comment 原形，一个字段都不加
   （actd 侧才做 steer 分类与 §44.3 中继，两层职责不混淆）；
2. 投影新字段（queued_reason / steers[]）经 /api/board 与 /api/cards/{id}
   **原样透传**——server 不改写、不吞、不发明（wire add-only）。
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tests import TMP_HOME  # noqa: F401 - ensures the sandbox env is set first
from tests.test_server_common import (DEMO_SEED_PATH, get_json, post_json,
                                      rewrite_board, seed_scene, start_server)


def _comment(card_id: str) -> dict:
    return {"action": "comment", "id": card_id, "comment": "先别动 schema，走兼容层"}


@unittest.skipUnless(DEMO_SEED_PATH, "scripts/demo_seed.py not found")
class SteerFlagTestCase(unittest.TestCase):
    """running 场景：R-105/R-107 working、R-106 queued、R-108 blocked（needs_input）。
    §60 起这些是 demo 卡的**工作编号**（主键 P-1xx）——inbox / is_executing 按
    工作编号也能指到卡，本类的动作全部用工作编号发，顺带钉住 §60.3。"""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="zai-m6-steer-"))
        seed_scene(self.home, "running")
        _httpd, self.port = start_server(self, self.home)

    def _inbox_files(self):
        return sorted((self.home / "state" / "inbox").glob("*.json"))

    def test_comment_on_working_card_is_flagged_steer(self):
        status, obj = post_json(self.port, "/api/actions", _comment("R-105"))
        self.assertEqual(status, 200)
        self.assertIs(obj.get("ok"), True)
        self.assertIs(obj.get("steer"), True)
        # 落盘即排队——server 只能诚实报 queued，送达状态由投影回流
        self.assertEqual(obj.get("steer_status"), "queued")

    def test_inbox_file_stays_plain_comment_shape(self):
        # steer 标注绝不渗进 inbox 文件：§3 comment 四键形 + T-28 via 落款，
        # 一字不多（steer/steer_status 只活在响应里）
        post_json(self.port, "/api/actions", _comment("R-105"))
        files = self._inbox_files()
        self.assertEqual(len(files), 1)
        rec = json.loads(files[0].read_bytes().decode("utf-8"))
        self.assertEqual(set(rec), {"action", "comment", "id", "ts", "via"})
        self.assertEqual(rec["action"], "comment")
        self.assertEqual(rec["id"], "R-105")
        self.assertEqual(rec["via"], "web")

    def test_comment_on_blocked_card_is_flagged_steer(self):
        # needs_input（blocked）也是 executing——会话活着，steer 可达
        status, obj = post_json(self.port, "/api/actions", _comment("R-108"))
        self.assertEqual(status, 200)
        self.assertIs(obj.get("steer"), True)
        self.assertEqual(obj.get("steer_status"), "queued")

    def test_comment_on_queued_card_is_not_steer(self):
        # queued（approved 未派发）没有活会话——普通 comment，无 steer 键
        status, obj = post_json(self.port, "/api/actions", _comment("R-106"))
        self.assertEqual(status, 200)
        self.assertNotIn("steer", obj)
        self.assertNotIn("steer_status", obj)

    def test_comment_on_review_card_is_not_steer(self):
        status, obj = post_json(self.port, "/api/actions", _comment("R-109"))
        self.assertEqual(status, 200)
        self.assertNotIn("steer", obj)

    def test_agent_comment_on_working_card_is_not_steer(self):
        # T-28：agent ingress 的评论只记录不 steer——标注必须反映实际裁决
        payload = dict(_comment("R-105"), actor="agent")
        status, obj = post_json(self.port, "/api/actions", payload)
        self.assertEqual(status, 200)
        self.assertIs(obj.get("ok"), True)
        self.assertIs(obj.get("steer"), False)
        self.assertNotIn("steer_status", obj)
        rec = json.loads(self._inbox_files()[0].read_bytes().decode("utf-8"))
        self.assertEqual(rec["via"], "agent")

    def test_non_comment_verb_on_working_card_is_not_flagged(self):
        # steer 分类只跟 comment 动词走；停止/验收等动词不沾 steer 键
        status, obj = post_json(self.port, "/api/actions",
                                {"action": "stop_to_review", "id": "R-105"})
        self.assertEqual(status, 200)
        self.assertNotIn("steer", obj)


@unittest.skipUnless(DEMO_SEED_PATH, "scripts/demo_seed.py not found")
class ProjectionPassthroughTestCase(unittest.TestCase):
    """queued_reason / steers[] 投影新字段经两个读端点原样透传。"""

    QUEUED_REASON = {"kind": "waiting_card", "blocking_id": "R-105",
                     "detail": "等 R-105 交付后复用它的 worktree"}
    STEERS = [
        {"ts": "2026-08-30T09:00:00Z", "text": "先别动 schema，走兼容层",
         "status": "delivered", "delivered_at": "2026-08-30T09:02:11Z"},
        {"ts": "2026-08-30T09:30:00Z", "text": "加一条 v1 回归用例",
         "status": "queued"},
    ]

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="zai-m6-passthru-"))
        dash = seed_scene(self.home, "running")
        for row in dash["running"]:
            if row["id"] == "P-106":           # 主键（工作编号 R-106，§60）
                row["queued_reason"] = dict(self.QUEUED_REASON)
            elif row["id"] == "P-105":
                row["steers"] = [dict(n) for n in self.STEERS]
        rewrite_board(self.home, dash)
        _httpd, self.port = start_server(self, self.home)

    def test_board_passthrough_keeps_new_fields(self):
        status, board = get_json(self.port, "/api/board")
        self.assertEqual(status, 200)
        by_id = {row["id"]: row for row in board["running"]}
        self.assertEqual(by_id["P-106"].get("queued_reason"), self.QUEUED_REASON)
        self.assertEqual(by_id["P-105"].get("steers"), self.STEERS)

    def test_card_detail_keeps_queued_reason(self):
        # 按工作编号取详情（§60.3）：响应 id 恒为主键
        status, detail = get_json(self.port, "/api/cards/R-106")
        self.assertEqual(status, 200)
        self.assertEqual(detail.get("id"), "P-106")
        self.assertEqual(detail.get("queued_reason"), self.QUEUED_REASON)
        self.assertEqual(detail.get("lane"), "running")

    def test_card_detail_keeps_steers(self):
        status, detail = get_json(self.port, "/api/cards/R-105")
        self.assertEqual(status, 200)
        self.assertEqual(detail.get("steers"), self.STEERS)


if __name__ == "__main__":
    unittest.main()
