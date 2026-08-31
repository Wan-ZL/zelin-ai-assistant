"""GET /files/deliverables/{card_id}/{name} + POST /api/reveal（§2.1 files）。

安全红线覆盖：路径由 server 端从卡片记录推导（target_repo|cwd →
deliverables/）、name 纯 basename、目录穿越/NUL/dotfile/symlink 逃逸全拒、
reveal 非 darwin 501。reveal 的 ``open -R`` 用注入缝 mock——测试绝不真弹访达
（与 repo「绝不 spawn 真 claude」同款纪律）。
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - ensures the sandbox env is set first
from tests.test_server_common import (DEMO_SEED_PATH, assert_envelope,
                                      get_json, http_request, post_json,
                                      rewrite_board, seed_scene, start_server)

from server import files as files_mod

HERO = "R-101"


@unittest.skipUnless(DEMO_SEED_PATH, "scripts/demo_seed.py not found")
class _DeliverablesHome(unittest.TestCase):
    """公共布景：R-101 的 target_repo 改指 tmpdir 内的假 repo（不出沙箱）。"""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="zai-g5-files-"))
        dash = seed_scene(self.home, "initial")
        self.repo = self.home / "demo-repo"
        self.dlv = self.repo / "deliverables"
        self.dlv.mkdir(parents=True)
        # 投影行是 card_detail 的第一真源：直接改 R-101 的 target_repo
        for row in dash["needs_approval"]:
            if row["id"] == HERO:
                row["target_repo"] = str(self.repo)
        rewrite_board(self.home, dash)

        self.older = self.dlv / "report.txt"
        self.older.write_text("交付物正文 older", encoding="utf-8")
        self.newer = self.dlv / "report.html"
        self.newer.write_text("<h1>交付物 newer</h1>", encoding="utf-8")
        os.utime(self.older, (1_700_000_000, 1_700_000_000))
        os.utime(self.newer, (1_800_000_000, 1_800_000_000))

        _, self.port = start_server(self, self.home)


class ServeDeliverableTestCase(_DeliverablesHome):
    def test_serves_file_with_content_type(self):
        status, headers, body = http_request(
            self.port, "GET", f"/files/deliverables/{HERO}/report.txt")
        self.assertEqual(status, 200)
        self.assertEqual(body.decode("utf-8"), "交付物正文 older")
        self.assertTrue(headers.get("Content-Type", "").startswith("text/plain"))
        self.assertEqual(headers.get("Cache-Control"), "no-store")
        self.assertEqual(headers.get("X-Content-Type-Options"), "nosniff")

    def test_missing_file_404(self):
        status, obj = get_json(self.port,
                               f"/files/deliverables/{HERO}/nope.txt")
        self.assertEqual(status, 404)
        assert_envelope(self, obj, "NOT_FOUND")

    def test_unknown_card_404(self):
        status, obj = get_json(self.port,
                               "/files/deliverables/R-424242/report.txt")
        self.assertEqual(status, 404)
        assert_envelope(self, obj, "NOT_FOUND")

    def test_card_without_deliverable_root_404(self):
        # debt 卡 R-113 无 target_repo/cwd —— server 推不出根目录必须 404
        status, obj = get_json(self.port,
                               "/files/deliverables/R-113/report.txt")
        self.assertEqual(status, 404)
        assert_envelope(self, obj, "NOT_FOUND")

    def test_encoded_traversal_rejected(self):
        # ../../secret.txt 从 deliverables/ 上跳两级正好落在 home 根——先埋一个
        # 真文件，断言其内容绝不外泄（envelope 回显请求路径是允许的）
        marker = "TOP-SECRET-DO-NOT-LEAK"
        (self.home / "secret.txt").write_text(marker, encoding="utf-8")
        status, _h, body = http_request(
            self.port, "GET",
            f"/files/deliverables/{HERO}/..%2F..%2Fsecret.txt")
        self.assertEqual(status, 404)
        self.assertNotIn(marker.encode("utf-8"), body)

    def test_dotfile_and_separator_names_rejected(self):
        (self.dlv / ".hidden").write_text("dot", encoding="utf-8")
        for bad in (".hidden", "%2e%2e", "a%5Cb.txt"):  # dotfile / ".." / 反斜杠
            with self.subTest(name=bad):
                status, obj = get_json(
                    self.port, f"/files/deliverables/{HERO}/{bad}")
                self.assertEqual(status, 400)
                assert_envelope(self, obj, "INVALID_FIELD")

    def test_nul_in_name_rejected(self):
        status, obj = get_json(self.port,
                               f"/files/deliverables/{HERO}/a%00.txt")
        self.assertEqual(status, 400)
        assert_envelope(self, obj, "INVALID_FIELD")

    def test_symlink_escape_rejected(self):
        outside = self.home / "outside-secret.txt"
        outside.write_text("MUST-NOT-LEAK", encoding="utf-8")
        os.symlink(outside, self.dlv / "link.txt")
        status, _h, body = http_request(
            self.port, "GET", f"/files/deliverables/{HERO}/link.txt")
        self.assertEqual(status, 404)  # realpath 包含性双保险
        self.assertNotIn(b"MUST-NOT-LEAK", body)

    def test_bad_card_id_rejected(self):
        status, obj = get_json(self.port,
                               "/files/deliverables/..%2FR-101/report.txt")
        self.assertNotEqual(status, 200)  # 3 段 → 404（id 闸门在 2 段形下 400）
        status, obj = get_json(self.port,
                               "/files/deliverables/a.b/report.txt")
        self.assertEqual(status, 400)
        assert_envelope(self, obj, "INVALID_FIELD")


class RevealTestCase(_DeliverablesHome):
    def test_non_darwin_returns_501(self):
        with mock.patch.object(files_mod.sys, "platform", "linux"):
            status, obj = post_json(self.port, "/api/reveal",
                                    {"card_id": HERO})
        self.assertEqual(status, 501)
        assert_envelope(self, obj, "NOT_IMPLEMENTED")

    def test_reveals_newest_deliverable_via_open_dash_r(self):
        with mock.patch.object(files_mod.sys, "platform", "darwin"), \
                mock.patch.object(files_mod.subprocess, "run") as run:
            status, obj = post_json(self.port, "/api/reveal",
                                    {"card_id": HERO})
        self.assertEqual(status, 200)
        self.assertIs(obj.get("ok"), True)
        self.assertEqual(obj.get("revealed"), str(self.newer))
        run.assert_called_once()
        self.assertEqual(run.call_args[0][0],
                         ["open", "-R", str(self.newer)])

    def test_empty_deliverables_dir_reveals_dir_itself(self):
        self.newer.unlink()
        self.older.unlink()
        with mock.patch.object(files_mod.sys, "platform", "darwin"), \
                mock.patch.object(files_mod.subprocess, "run") as run:
            status, obj = post_json(self.port, "/api/reveal",
                                    {"card_id": HERO})
        self.assertEqual(status, 200)
        self.assertEqual(obj.get("revealed"), str(self.dlv))
        self.assertEqual(run.call_args[0][0], ["open", "-R", str(self.dlv)])

    def test_missing_deliverables_dir_404(self):
        import shutil
        shutil.rmtree(self.dlv)
        with mock.patch.object(files_mod.sys, "platform", "darwin"), \
                mock.patch.object(files_mod.subprocess, "run"):
            status, obj = post_json(self.port, "/api/reveal",
                                    {"card_id": HERO})
        self.assertEqual(status, 404)
        assert_envelope(self, obj, "NOT_FOUND")

    def test_unknown_field_rejected(self):
        status, obj = post_json(self.port, "/api/reveal",
                                {"card_id": HERO, "path": "/etc/passwd"})
        self.assertEqual(status, 400)
        assert_envelope(self, obj, "UNKNOWN_FIELD")

    def test_card_id_must_be_string(self):
        for payload in ({}, {"card_id": 7}, {"card_id": None}):
            with self.subTest(payload=payload):
                status, obj = post_json(self.port, "/api/reveal", payload)
                self.assertEqual(status, 400)
                assert_envelope(self, obj, "INVALID_FIELD")

    def test_traversal_card_id_rejected(self):
        status, obj = post_json(self.port, "/api/reveal",
                                {"card_id": "../../etc"})
        self.assertEqual(status, 400)
        assert_envelope(self, obj, "INVALID_FIELD")


if __name__ == "__main__":
    unittest.main()
