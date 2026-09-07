"""POST /api/attachments —— 贴图的 web 落盘面（CONTRACT §10bis 追记 / §49 路由；owner 决策 D41）。

钉的行为（server/attachments.py 模块 docstring 的每一条）：
- 四闸同 /api/actions：Host / Origin / instance token 拒绝路径零落盘；Content-Type 闸对本路由
  只认 image/png——application/json / text/plain / multipart 一律 415，反过来 image/png 发到
  /api/actions 也是 415（闸按路径取 media type，不是全局放宽）；
- body 上限独立于 JSON 面的 1MiB：8MiB + 1 → 413 只看 Content-Length；1MiB + 1 的 PNG 照收；
- 前 8 字节必须是 PNG 签名：JPEG / 随机字节 / 空体 → 400 INVALID_FIELD，不落盘；
- 路径永不由客户端决定：query / 自定义头里的「文件名」不参与，落盘名 = uuid4-1.png、目录固定、
  回执 path 在 state/attachments/ 之内、0600（POSIX）；
- 回执 path 可直接作 capture 的 images[] 进 /api/actions（wire 零改动）；
- 没有读回路由：GET /api/attachments 404、/files/ 不服务它；
- 目录与 actd 附件 GC 扫的是同一处（留存 = 既有 GC，不另起台账）。

真 server 起在随机端口（tests/test_server_common.py），tmp HOME，无子进程、无网络。
"""
from __future__ import annotations

import base64
import http.client
import json
import os
import re
import stat
import tempfile
import unittest
from pathlib import Path

from tests import TMP_HOME  # noqa: F401 - sandbox env first

from server import attachments, paths, security
from tests.test_server_common import (assert_envelope, auth_headers, get_json,
                                      http_request, post_json, start_server)

# 1×1 透明 PNG（parity fixture 同一份）——真 PNG 签名 + 合法 chunk
PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")
JPEG_HEAD = b"\xff\xd8\xff\xe0" + b"\x00" * 64
_NAME_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}-1\.png$")


class _AttachmentsHome:
    def _boot(self):
        self.home = Path(tempfile.mkdtemp(prefix="zai-attachments-"))
        _, self.port = start_server(self, self.home)
        self.dir = self.home / "state" / "attachments"

    def _files(self):
        return sorted(p.name for p in self.dir.iterdir()) if self.dir.is_dir() else []

    def _post(self, body: bytes, headers: dict | None = None, path: str = attachments.ROUTE):
        headers = auth_headers(self.port, content_type=attachments.CONTENT_TYPE) if headers is None else headers
        status, _h, data = http_request(self.port, "POST", path, body=body, headers=headers)
        return status, json.loads(data.decode("utf-8"))

    def _post_len_only(self, length: int, content_type: str = attachments.CONTENT_TYPE):
        """只发头不发体：server 看 Content-Length 即裁。"""
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.putrequest("POST", attachments.ROUTE)
            for k, v in auth_headers(self.port, content_type=content_type).items():
                conn.putheader(k, v)
            conn.putheader("Content-Length", str(length))
            conn.endheaders()
            resp = conn.getresponse()
            return resp.status, json.loads(resp.read().decode("utf-8"))
        finally:
            conn.close()


class HappyPathTestCase(_AttachmentsHome, unittest.TestCase):
    def setUp(self):
        self._boot()

    def test_png_lands_under_state_attachments_with_receipt(self):
        status, obj = self._post(PNG_1X1)
        self.assertEqual(status, 200, obj)
        self.assertEqual(obj["ok"], True)
        self.assertEqual(obj["bytes"], len(PNG_1X1))
        written = Path(obj["path"])
        self.assertTrue(written.is_absolute())
        self.assertEqual(written.parent, self.dir)
        self.assertRegex(written.name, _NAME_RE)
        self.assertEqual(written.read_bytes(), PNG_1X1)
        self.assertEqual(self._files(), [written.name])  # 没有半截 .tmp 残留

    @unittest.skipUnless(os.name == "posix", "mode bits are POSIX-only")
    def test_file_is_0600(self):
        _status, obj = self._post(PNG_1X1)
        mode = stat.S_IMODE(os.stat(obj["path"]).st_mode)
        self.assertEqual(mode, 0o600)

    def test_each_upload_mints_its_own_name(self):
        _s1, a = self._post(PNG_1X1)
        _s2, b = self._post(PNG_1X1)
        self.assertNotEqual(a["path"], b["path"])
        self.assertEqual(len(self._files()), 2)

    def test_second_channel_cap_is_wider_than_json_face(self):
        # 1MiB + 1：JSON 面 413（BodyGateTestCase），本路由照收
        body = PNG_1X1 + b"\x00" * ((1 << 20) + 1 - len(PNG_1X1))
        status, obj = self._post(body)
        self.assertEqual(status, 200, obj)
        self.assertEqual(obj["bytes"], len(body))

    @unittest.skipUnless(os.name == "posix", "inbox_writer requires '/'-rooted paths")
    def test_receipt_path_rides_a_capture_unchanged(self):
        _status, obj = self._post(PNG_1X1)
        status, rec = post_json(self.port, "/api/actions",
                                {"action": "capture", "text": "看看这张截图", "images": [obj["path"]]})
        self.assertEqual(status, 200, rec)
        inbox_file = self.home / "state" / "inbox" / rec["file"]
        written = json.loads(inbox_file.read_text(encoding="utf-8"))
        self.assertEqual(written["images"], [obj["path"]])


class GatesTestCase(_AttachmentsHome, unittest.TestCase):
    """四闸 + PNG 校验的拒绝路径：每条都断言零落盘。"""

    def setUp(self):
        self._boot()

    def _assert_rejected(self, status_got, obj, status_want, code):
        self.assertEqual(status_got, status_want, obj)
        assert_envelope(self, obj, code)
        self.assertEqual(self._files(), [])

    def test_json_content_type_is_415_on_this_route(self):
        status, obj = self._post(PNG_1X1, headers=auth_headers(self.port))
        self._assert_rejected(status, obj, 415, "INVALID_FIELD")
        self.assertIn("image/png", obj["error"]["message"])

    def test_simple_request_content_types_are_415(self):
        for ct in ("text/plain", "multipart/form-data; boundary=x",
                   "application/x-www-form-urlencoded", "image/jpeg"):
            status, obj = self._post(PNG_1X1, headers=auth_headers(self.port, content_type=ct))
            self._assert_rejected(status, obj, 415, "INVALID_FIELD")

    def test_png_content_type_does_not_unlock_the_json_face(self):
        # 闸按路径取 media type：image/png 发到 /api/actions 仍是 415，不是全局放宽
        body = json.dumps({"action": "weekly_digest_now"}).encode("utf-8")
        status, _h, data = http_request(
            self.port, "POST", "/api/actions", body=body,
            headers=auth_headers(self.port, content_type=attachments.CONTENT_TYPE))
        self.assertEqual(status, 415)
        assert_envelope(self, json.loads(data.decode("utf-8")), "INVALID_FIELD")
        self.assertFalse((self.home / "state" / "inbox").is_dir()
                         and any((self.home / "state" / "inbox").iterdir()))

    def test_missing_token_is_401(self):
        headers = auth_headers(self.port, content_type=attachments.CONTENT_TYPE)
        del headers[security.TOKEN_HEADER]
        status, obj = self._post(PNG_1X1, headers=headers)
        self._assert_rejected(status, obj, 401, "UNAUTHORIZED")

    def test_cross_origin_is_403(self):
        headers = auth_headers(self.port, content_type=attachments.CONTENT_TYPE)
        headers["Origin"] = "http://evil.example"
        status, obj = self._post(PNG_1X1, headers=headers)
        self._assert_rejected(status, obj, 403, "FORBIDDEN")

    def test_rebound_host_is_403(self):
        headers = auth_headers(self.port, content_type=attachments.CONTENT_TYPE)
        headers["Host"] = "evil.example"
        status, obj = self._post(PNG_1X1, headers=headers)
        self._assert_rejected(status, obj, 403, "FORBIDDEN")

    def test_non_png_bytes_are_400(self):
        for body in (JPEG_HEAD, b"not a png at all", PNG_1X1[:4] + b"junk"):
            status, obj = self._post(body)
            self._assert_rejected(status, obj, 400, "INVALID_FIELD")

    def test_empty_body_is_400(self):
        status, obj = self._post(b"")
        self._assert_rejected(status, obj, 400, "INVALID_FIELD")

    def test_magic_alone_is_400(self):
        status, obj = self._post(attachments.PNG_MAGIC)
        self._assert_rejected(status, obj, 400, "INVALID_FIELD")

    def test_oversize_is_413_without_reading_body(self):
        status, obj = self._post_len_only(attachments.MAX_BYTES + 1)
        self._assert_rejected(status, obj, 413, "INVALID_FIELD")
        self.assertEqual(obj["error"]["details"]["limit"], attachments.MAX_BYTES)

    def test_put_is_not_a_route(self):
        status, _h, data = http_request(
            self.port, "PUT", attachments.ROUTE, body=PNG_1X1,
            headers=auth_headers(self.port, content_type=attachments.CONTENT_TYPE))
        self.assertEqual(status, 404)
        assert_envelope(self, json.loads(data.decode("utf-8")), "NOT_FOUND")
        self.assertEqual(self._files(), [])


class PathNeverClientControlledTestCase(_AttachmentsHome, unittest.TestCase):
    def setUp(self):
        self._boot()

    def test_query_and_name_headers_are_ignored(self):
        headers = auth_headers(self.port, content_type=attachments.CONTENT_TYPE)
        headers["X-File-Name"] = "../../evil.png"
        headers["Content-Disposition"] = 'attachment; filename="../../../etc/passwd"'
        status, obj = self._post(PNG_1X1, headers=headers,
                                 path=attachments.ROUTE + "?name=../../x.png&path=/tmp/y")
        self.assertEqual(status, 200, obj)
        written = Path(obj["path"])
        self.assertEqual(written.resolve().parent, self.dir.resolve())
        self.assertRegex(written.name, _NAME_RE)
        self.assertFalse((self.home / "x.png").exists())

    def test_no_read_back_route(self):
        _status, obj = self._post(PNG_1X1)
        status, body = get_json(self.port, attachments.ROUTE)
        self.assertEqual(status, 404)
        assert_envelope(self, body, "NOT_FOUND")
        name = Path(obj["path"]).name
        status, _h, _d = http_request(self.port, "GET", f"/files/attachments/{name}")
        self.assertEqual(status, 404)


class LayoutMirrorTestCase(unittest.TestCase):
    """server 落盘的目录 = actd 附件 GC（act/lib/actd/housekeeping.sweep_attachment_dirs）扫的目录。
    server 不 import act（§44），所以这里用测试侧 pin：两边的 HOME-相对形必须相等。"""

    def test_attachments_dir_matches_actd_gc_dir(self):
        from act.lib import config
        home = Path("/tmp/zai-attachments-pin")
        self.assertEqual(paths.attachments_dir(home).relative_to(home),
                         (config.STATE_DIR / "attachments").relative_to(config.HOME))

    def test_is_png_truth_table(self):
        self.assertTrue(attachments.is_png(PNG_1X1))
        self.assertFalse(attachments.is_png(b""))
        self.assertFalse(attachments.is_png(attachments.PNG_MAGIC))
        self.assertFalse(attachments.is_png(JPEG_HEAD))
        self.assertFalse(attachments.is_png(b"\x89PNG\r\n\x1a" + b"x" * 20))  # 第 8 字节错

    def test_content_type_is_exact_media_type(self):
        self.assertTrue(security.content_type_is("image/png", "image/png"))
        self.assertTrue(security.content_type_is("Image/PNG; charset=binary", "image/png"))
        self.assertFalse(security.content_type_is("image/png", "application/json"))
        self.assertFalse(security.content_type_is("multipart/form-data", "image/png"))
        self.assertFalse(security.content_type_is(None, "image/png"))


if __name__ == "__main__":
    unittest.main()
