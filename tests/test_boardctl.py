"""boardctl（M5 agent 有界通道）测试。

覆盖面：
- 输出契约：stdout 单 JSON + schemaVersion；stderr error JSON；exit 0/2/3/4/5；
- 读路径：/api/board 透传、--lane 过滤、/api/cards 详情；
- 写路径：capture/comment 落 inbox 文件（真 server + tmp HOME）；
- permission wall：决策动词不是子命令（exit 2）、无 mode/preset 直跑面；
- 网络故障：server 不可达 → exit 3。

夹具复用 tests/test_server_common（真 server 起在 port 0 + demo_seed 种数据，
绝不触碰生产 state/）。
"""
from __future__ import annotations

import io
import json
import shutil
import socket
import tempfile
import unittest
from pathlib import Path

from tests import TMP_HOME  # noqa: F401 - 先落沙箱 env
from tests import test_server_common as common

from act import boardctl


class _CtlBase(unittest.TestCase):
    """起真 server 的公共夹具；run_ctl 走 main() 注入缝，不 spawn 子进程。"""

    scene = "initial"

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="boardctl-test-home-"))
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        self.board = common.seed_scene(self.home, self.scene)
        _httpd, self.port = common.start_server(self, self.home)
        self.env = {"ZAI_PORT": str(self.port)}

    def run_ctl(self, *argv, env=None):
        out, err = io.StringIO(), io.StringIO()
        rc = boardctl.main(list(argv), stdout=out, stderr=err,
                           environ=self.env if env is None else env)
        return rc, out.getvalue(), err.getvalue()

    def ok_json(self, *argv):
        rc, out, err = self.run_ctl(*argv)
        self.assertEqual(rc, 0, f"stderr: {err!r}")
        self.assertEqual(err, "")
        doc = json.loads(out)
        self.assertEqual(doc["schemaVersion"], boardctl.SCHEMA_VERSION)
        return doc

    def err_json(self, expected_rc, *argv, env=None):
        rc, out, err = self.run_ctl(*argv, env=env)
        self.assertEqual(rc, expected_rc, f"stdout: {out!r} stderr: {err!r}")
        self.assertEqual(out, "")
        doc = json.loads(err)
        self.assertEqual(doc["schemaVersion"], boardctl.SCHEMA_VERSION)
        self.assertIsInstance(doc["error"]["code"], str)
        self.assertIsInstance(doc["error"]["message"], str)
        return doc["error"]

    def inbox_files(self):
        inbox = self.home / "state" / "inbox"
        return sorted(inbox.glob("*.json")) if inbox.is_dir() else []


class BoardReadTest(_CtlBase):
    def test_board_passthrough(self):
        doc = self.ok_json("board")
        # /api/board = dashboard.json 原样透传，包在 "board" 键下
        self.assertEqual(doc["board"], self.board)

    def test_board_lane_filter(self):
        doc = self.ok_json("board", "--lane", "needs_approval")
        self.assertEqual(doc["lane"], "needs_approval")
        ids = [row["id"] for row in doc["cards"]]
        self.assertIn("R-101", ids)

    def test_board_unknown_lane_is_usage_error(self):
        err = self.err_json(2, "board", "--lane", "bogus")
        self.assertEqual(err["code"], "USAGE_ERROR")

    def test_card_detail_merges_lane(self):
        doc = self.ok_json("card", "R-101")
        self.assertEqual(doc["card"]["id"], "R-101")
        self.assertEqual(doc["card"]["lane"], "needs_approval")

    def test_card_bad_id_fails_client_side(self):
        err = self.err_json(2, "card", "../../etc/passwd")
        self.assertEqual(err["code"], "USAGE_ERROR")

    def test_card_not_found_passes_through_envelope(self):
        err = self.err_json(4, "card", "R-999")
        self.assertEqual(err["code"], "NOT_FOUND")

    def test_help_is_plain_text_exit_zero(self):
        rc, out, err = self.run_ctl("--help")
        self.assertEqual((rc, err), (0, ""))
        self.assertIn("Usage: boardctl", out)
        with self.assertRaises(ValueError):
            json.loads(out)  # help 是唯一非 JSON 的成功输出


class WriteVerbsTest(_CtlBase):
    def test_capture_writes_inbox_file(self):
        doc = self.ok_json("capture", "--text", "candidate: add CI badge")
        self.assertTrue(doc["ok"])
        self.assertEqual(doc["action"], "capture")
        files = self.inbox_files()
        self.assertEqual([p.name for p in files], [doc["file"]])
        self.assertTrue(doc["file"].startswith("capture-"))
        rec = json.loads(files[0].read_text(encoding="utf-8"))
        self.assertEqual(rec["action"], "capture")
        self.assertEqual(rec["text"], "candidate: add CI badge")
        self.assertIn("ts", rec)
        self.assertNotIn("mode", rec)    # agent 通道绝无直跑
        self.assertNotIn("preset", rec)

    def test_capture_text_file_source(self):
        src = self.home / "note.txt"
        src.write_text("from file 候选", encoding="utf-8")
        doc = self.ok_json("capture", "--text-file", str(src))
        rec = json.loads(self.inbox_files()[0].read_text(encoding="utf-8"))
        self.assertEqual(rec["text"], "from file 候选")
        self.assertTrue(doc["ok"])

    def test_capture_requires_exactly_one_text_source(self):
        self.err_json(2, "capture")
        self.err_json(2, "capture", "--text", "a", "--text-file", "/tmp/x")
        self.assertEqual(self.inbox_files(), [])

    def test_capture_rejects_relative_or_excess_images(self):
        self.err_json(2, "capture", "--text", "x", "--image", "rel.png")
        args = ["capture", "--text", "x"]
        for i in range(5):
            args += ["--image", f"/tmp/p{i}.png"]
        self.err_json(2, *args)
        self.assertEqual(self.inbox_files(), [])

    def test_capture_has_no_run_mode_flag(self):
        # W18/信任矩阵:agent 通道没有 mode:"run" 面——连 flag 都不存在
        err = self.err_json(2, "capture", "--text", "x", "--mode", "run")
        self.assertEqual(err["code"], "USAGE_ERROR")
        self.assertEqual(self.inbox_files(), [])

    def test_comment_writes_inbox_file(self):
        doc = self.ok_json("comment", "R-101", "--body",
                           "progress: tests green, risk none")
        self.assertTrue(doc["ok"])
        self.assertEqual(doc["action"], "comment")
        rec = json.loads(self.inbox_files()[0].read_text(encoding="utf-8"))
        self.assertEqual(rec["action"], "comment")
        self.assertEqual(rec["id"], "R-101")
        self.assertEqual(rec["comment"], "progress: tests green, risk none")

    def test_comment_empty_body_fails_closed(self):
        self.err_json(2, "comment", "R-101", "--body", "   ")
        self.assertEqual(self.inbox_files(), [])

    def test_state_verbs_are_not_subcommands(self):
        # permission wall 的 CLI 面:决策动词连子命令都不是(exit 2,零落盘)
        for verb in ("approve", "reject", "accept", "rework", "trash",
                     "archive", "merge_apply", "restore"):
            err = self.err_json(2, verb, "R-101")
            self.assertEqual(err["code"], "USAGE_ERROR")
        self.assertEqual(self.inbox_files(), [])


class TransportTest(unittest.TestCase):
    def test_server_down_maps_to_exit_3(self):
        # 拿一个刚释放的空闲端口——连接必被拒
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            free_port = s.getsockname()[1]
        out, err = io.StringIO(), io.StringIO()
        rc = boardctl.main(["board"], stdout=out, stderr=err,
                           environ={"ZAI_PORT": str(free_port)})
        self.assertEqual(rc, 3)
        doc = json.loads(err.getvalue())
        self.assertEqual(doc["error"]["code"], "SERVICE_UNAVAILABLE")

    def test_bad_zai_port_is_usage_error(self):
        out, err = io.StringIO(), io.StringIO()
        rc = boardctl.main(["board"], stdout=out, stderr=err,
                           environ={"ZAI_PORT": "not-a-port"})
        self.assertEqual(rc, 2)
        self.assertEqual(json.loads(err.getvalue())["error"]["code"],
                         "USAGE_ERROR")

    def test_conflict_409_maps_to_exit_5(self):
        # PR-current server 不发 409;映射规则先在单元层钉死(CAS 时代要用)
        e = boardctl._api_error(
            409, b'{"error":{"code":"CONFLICT","message":"stale version",'
                 b'"details":{}}}')
        self.assertEqual(e.exit_code, 5)
        self.assertEqual(e.code, "CONFLICT")

    def test_api_error_falls_back_on_garbage_body(self):
        e = boardctl._api_error(502, b"<html>bad gateway</html>")
        self.assertEqual(e.exit_code, 4)
        self.assertEqual(e.code, "HTTP_502")


if __name__ == "__main__":
    unittest.main()
