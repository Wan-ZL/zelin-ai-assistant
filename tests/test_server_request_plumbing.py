"""server/app.py 请求管线的边角判例（CONTRACT §49；P3a 重构护网）。

覆盖此前没有判例的分支——Content-Length 闸的每一种坏值、_dispatch 的三条
兜底（NotImplementedError → 501、客户端提前挂断静默、未知异常 → 500 不泄栈）、
HEAD /api/events、静态资源面（目录回落 index、SPA 深链、带扩展名缺失 404、
../ 穿越、hashed assets 长缓存、dist 未 build 的占位页）与 main() 的两条
出口（EADDRINUSE → 75 一行人话；Ctrl-C → 0 且停 watcher）。全部 hermetic：
handler 直接实例化（不占端口）或 port=0 真 server。
"""
from __future__ import annotations

import errno
import io
import json
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - 先落沙箱 env
from tests.test_server_common import (assert_envelope, http_request,
                                      start_server)

from server import app as app_mod
from server import security
from server.errors import InvalidFieldError


def _bare_handler(path: str = "/api/health", command: str = "GET",
                  headers: dict | None = None, ctx=None):
    """不经 socket 的 Handler：只灌 _dispatch 用到的属性。"""
    h = app_mod.Handler.__new__(app_mod.Handler)
    h.path = path
    h.command = command
    h.request_version = "HTTP/1.1"
    h.requestline = f"{command} {path} HTTP/1.1"
    h.client_address = ("127.0.0.1", 0)
    h.close_connection = False
    h.headers = Message()
    for k, v in (headers or {"Host": "127.0.0.1"}).items():
        h.headers[k] = v
    h.rfile = io.BytesIO(b"")
    h.wfile = io.BytesIO()
    h.server = SimpleNamespace(ctx=ctx or SimpleNamespace(
        home=Path(TMP_HOME), token="t", allowed_origins=frozenset(),
        hub=None, static_dir=Path(TMP_HOME) / "no-dist"))
    return h


def _status_and_body(h) -> "tuple[int, dict | None]":
    raw = h.wfile.getvalue()
    head, _, body = raw.partition(b"\r\n\r\n")
    status = int(head.split(b" ", 2)[1])
    return status, (json.loads(body.decode("utf-8")) if body else None)


class ContentLengthGateTestCase(unittest.TestCase):
    def test_missing_is_400(self):
        with self.assertRaises(InvalidFieldError) as cm:
            app_mod._content_length(None)
        self.assertEqual(cm.exception.status, 400)

    def test_non_integer_is_400(self):
        with self.assertRaises(InvalidFieldError) as cm:
            app_mod._content_length("abc")
        self.assertIn("bad Content-Length", cm.exception.message)

    def test_negative_is_400(self):
        with self.assertRaises(InvalidFieldError):
            app_mod._content_length("-1")

    def test_over_limit_is_413_with_limit_detail(self):
        with self.assertRaises(InvalidFieldError) as cm:
            app_mod._content_length(str(app_mod.MAX_BODY_BYTES + 1))
        self.assertEqual(cm.exception.status, 413)
        self.assertEqual(cm.exception.details, {"limit": app_mod.MAX_BODY_BYTES})

    def test_limit_itself_passes(self):
        self.assertEqual(app_mod._content_length(str(app_mod.MAX_BODY_BYTES)),
                         app_mod.MAX_BODY_BYTES)
        self.assertEqual(app_mod._content_length("0"), 0)


class DispatchFallbackTestCase(unittest.TestCase):
    """_dispatch 的兜底三条：都不许泄栈、都不许让 handler 线程崩。"""

    def test_not_implemented_becomes_honest_501(self):
        h = _bare_handler()
        with mock.patch.object(app_mod.Handler, "_handle",
                               side_effect=NotImplementedError("stub")):
            h._dispatch("GET")
        status, obj = _status_and_body(h)
        self.assertEqual(status, 501)
        assert_envelope(self, obj, "NOT_IMPLEMENTED")

    def test_client_hangup_is_silent(self):
        h = _bare_handler()
        with mock.patch.object(app_mod.Handler, "_handle",
                               side_effect=BrokenPipeError()):
            h._dispatch("GET")
        self.assertEqual(h.wfile.getvalue(), b"")

    def test_unexpected_exception_is_500_envelope_without_trace(self):
        h = _bare_handler()
        err = io.StringIO()
        with mock.patch.object(app_mod.Handler, "_handle",
                               side_effect=RuntimeError("boom")), \
                mock.patch("sys.stderr", err):
            h._dispatch("GET")
        status, obj = _status_and_body(h)
        self.assertEqual(status, 500)
        assert_envelope(self, obj, "INTERNAL_ERROR")
        self.assertNotIn("boom", json.dumps(obj))   # 不泄栈
        self.assertIn("RuntimeError", err.getvalue())  # 但 stderr 有痕迹

    def test_nul_in_path_is_400(self):
        h = _bare_handler(path="/api/%00x")
        h._dispatch("GET")
        status, obj = _status_and_body(h)
        self.assertEqual(status, 400)
        assert_envelope(self, obj, "INVALID_FIELD")

    def test_head_on_events_answers_without_streaming(self):
        h = _bare_handler(path="/api/events", command="HEAD")
        h._dispatch("GET")
        status, _ = _status_and_body(h)
        self.assertEqual(status, 200)
        self.assertIn(b"text/event-stream", h.wfile.getvalue())

    def test_unknown_files_prefix_is_404(self):
        h = _bare_handler(path="/files/other/x")
        h._dispatch("GET")
        status, obj = _status_and_body(h)
        self.assertEqual(status, 404)
        assert_envelope(self, obj, "NOT_FOUND")


class StaticServingTestCase(unittest.TestCase):
    """web/dist 静态面：真 server + 临时 dist。"""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="zai-static-"))
        self.dist = self.home / "dist"
        (self.dist / "assets").mkdir(parents=True)
        (self.dist / "index.html").write_text("<head></head><body>ok</body>",
                                             encoding="utf-8")
        (self.dist / "assets" / "app-abc123.js").write_text("1;", encoding="utf-8")
        (self.dist / "sub").mkdir()
        (self.dist / "sub" / "index.html").write_text("<p>sub</p>", encoding="utf-8")
        httpd = app_mod.make_server(port=0, home=self.home, static_dir=self.dist,
                                    start_watcher=False)
        import threading
        threading.Thread(target=httpd.serve_forever,
                         kwargs={"poll_interval": 0.05}, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        self.port = httpd.server_address[1]

    def test_hashed_asset_is_immutable_cached_and_not_injected(self):
        status, headers, body = http_request(self.port, "GET", "/assets/app-abc123.js")
        self.assertEqual(status, 200)
        self.assertIn("immutable", headers.get("Cache-Control", ""))
        self.assertNotIn(b"__ZAI_TOKEN__", body)

    def test_directory_falls_back_to_its_index(self):
        status, headers, body = http_request(self.port, "GET", "/sub")
        self.assertEqual(status, 200)
        # 任何名为 index.html 的页面都拿到 token 注入（无 </head> 则前置）
        self.assertTrue(body.endswith(b"<p>sub</p>"))
        self.assertIn(b"__ZAI_TOKEN__", body)
        self.assertEqual(headers.get("Cache-Control"), "no-cache")

    def test_spa_deep_link_serves_root_index_with_token(self):
        status, _h, body = http_request(self.port, "GET", "/cards/R-1")
        self.assertEqual(status, 200)
        self.assertIn(b"__ZAI_TOKEN__", body)

    def test_missing_file_with_extension_is_404(self):
        status, _h, body = http_request(self.port, "GET", "/missing.png")
        self.assertEqual(status, 404)
        assert_envelope(self, json.loads(body.decode("utf-8")), "NOT_FOUND")

    def test_traversal_is_404_not_a_file(self):
        (self.home / "secret.txt").write_text("s", encoding="utf-8")
        status, _h, body = http_request(self.port, "GET", "/../secret.txt")
        self.assertEqual(status, 404)
        self.assertNotIn(b"s\n", body)

    def test_head_sends_headers_only(self):
        status, headers, body = http_request(self.port, "HEAD", "/")
        self.assertEqual(status, 200)
        self.assertEqual(body, b"")
        self.assertNotEqual(headers.get("Content-Length"), "0")


class PlaceholderTestCase(unittest.TestCase):
    def test_missing_dist_gives_placeholder_at_root_and_404_elsewhere(self):
        home = Path(tempfile.mkdtemp(prefix="zai-nodist-"))
        _httpd, port = start_server(self, home)   # static_dir = home/no-dist
        status, _h, body = http_request(port, "GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"not built yet", body)
        status, _h, body = http_request(port, "GET", "/anything")
        self.assertEqual(status, 404)


class StaticHelpersTestCase(unittest.TestCase):
    def test_static_target_none_when_dist_missing(self):
        self.assertIsNone(app_mod._static_target(Path("/nonexistent-dist-xyz"), "/"))

    def test_inside_accepts_dist_itself_and_children_only(self):
        dist = Path("/a/dist")
        self.assertTrue(app_mod._inside(dist, dist))
        self.assertTrue(app_mod._inside(dist / "x.js", dist))
        self.assertFalse(app_mod._inside(Path("/a/dist2/x.js"), dist))
        self.assertFalse(app_mod._inside(Path("/a"), dist))

    def test_ctype_falls_back_to_octet_stream(self):
        self.assertEqual(app_mod._static_ctype(Path("blob.unknownext")),
                         "application/octet-stream")
        # 平台 mimetypes 表对 .js 给 text/javascript 或 application/javascript
        self.assertIn(app_mod._static_ctype(Path("a.js")),
                      ("text/javascript", "application/javascript"))

    def test_cache_policy_by_location(self):
        self.assertIn("immutable", app_mod._static_cache(Path("/d/assets/a-1.js")))
        self.assertEqual(app_mod._static_cache(Path("/d/index.html")), "no-cache")


class MainEntryTestCase(unittest.TestCase):
    """main()：端口被占 → 一行人话 + 75；正常 serve 到 Ctrl-C → 0 且清理。"""

    def test_port_busy_exits_75_with_one_line(self):
        out = io.StringIO()
        busy = OSError(errno.EADDRINUSE, "in use")
        with mock.patch.object(app_mod, "make_server", side_effect=busy), \
                mock.patch("sys.stdout", out):
            rc = app_mod.main()
        self.assertEqual(rc, app_mod.EX_PORT_BUSY)
        self.assertIn("is busy", out.getvalue())
        self.assertEqual(out.getvalue().count("\n"), 1)

    def test_other_oserror_propagates(self):
        with mock.patch.object(app_mod, "make_server",
                               side_effect=OSError(errno.EACCES, "denied")):
            with self.assertRaises(OSError):
                app_mod.main()

    def test_ctrl_c_returns_0_and_stops_watcher(self):
        watcher = mock.Mock()
        httpd = mock.Mock()
        httpd.server_address = ("127.0.0.1", 12345)
        httpd.ctx = SimpleNamespace(home=Path("/h"))
        httpd.watcher = watcher
        httpd.serve_forever.side_effect = KeyboardInterrupt()
        out = io.StringIO()
        with mock.patch.object(app_mod, "make_server", return_value=httpd), \
                mock.patch("sys.stdout", out):
            rc = app_mod.main()
        self.assertEqual(rc, 0)
        watcher.stop.assert_called_once_with()
        httpd.server_close.assert_called_once_with()
        self.assertIn("http://127.0.0.1:12345", out.getvalue())

    def test_serve_without_watcher_still_closes_socket(self):
        httpd = mock.Mock()
        httpd.watcher = None
        httpd.serve_forever.side_effect = KeyboardInterrupt()
        app_mod._serve(httpd)
        httpd.server_close.assert_called_once_with()


class WriteAuthUnitTestCase(unittest.TestCase):
    """_check_write_auth 三闸的直接判例（真 server 面的判例在 test_server_auth）。"""

    def _ctx(self):
        return SimpleNamespace(token="tok", allowed_origins=frozenset({"http://127.0.0.1:1"}),
                               home=Path(TMP_HOME), hub=None, static_dir=Path("/x"))

    def test_bad_origin_closes_connection(self):
        h = _bare_handler(headers={"Host": "127.0.0.1", "Origin": "http://evil"},
                          ctx=self._ctx())
        h._dispatch("POST")
        status, obj = _status_and_body(h)
        self.assertEqual(status, 403)
        self.assertTrue(h.close_connection)

    def test_wrong_content_type_is_415(self):
        h = _bare_handler(headers={"Host": "127.0.0.1", "Content-Type": "text/plain",
                                   security.TOKEN_HEADER: "tok"}, ctx=self._ctx())
        h._dispatch("POST")
        status, obj = _status_and_body(h)
        self.assertEqual(status, 415)
        assert_envelope(self, obj, "INVALID_FIELD")

    def test_missing_token_is_401(self):
        h = _bare_handler(headers={"Host": "127.0.0.1",
                                   "Content-Type": "application/json"}, ctx=self._ctx())
        h._dispatch("POST")
        status, obj = _status_and_body(h)
        self.assertEqual(status, 401)
        assert_envelope(self, obj, "UNAUTHORIZED")

    def test_bad_host_rejected_even_on_get(self):
        h = _bare_handler(headers={"Host": "evil.example"}, ctx=self._ctx())
        h._dispatch("GET")
        status, _ = _status_and_body(h)
        self.assertEqual(status, 403)
        self.assertTrue(h.close_connection)


if __name__ == "__main__":
    unittest.main()
