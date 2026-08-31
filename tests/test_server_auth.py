"""§49 auth model 判例（server/security.py + app._check_auth）。

钉死 v0.48.1 关掉的 CRITICAL CSRF 洞（V048-AUDIT §5）：跨源浏览器
simple request（Content-Type: text/plain、无预检）曾经能对 /api/actions
直发 ``mode:"run"``，被 inbox 落款 via:"web" → actd 按 owner ingress 放行
→ APPROVED 直跑。四闸（Host / Origin / Content-Type / instance token）
之后，这条路在 body 被解析**之前**就断——本文件的每个拒绝判例都同时断言
「零落盘」。
"""
from __future__ import annotations

import json
import stat
import tempfile
import unittest
from pathlib import Path

from tests import TMP_HOME  # noqa: F401 - 先落沙箱 env
from tests.test_server_common import (assert_envelope, auth_headers,
                                      http_request, start_server)

from server import security
from server import app as app_mod


def _run_payload() -> bytes:
    # 审计探针原样：无 actor、直跑 mode——正是要被闸死的那发
    return json.dumps({"action": "capture", "text": "pwn",
                       "mode": "run"}).encode("utf-8")


class _AuthHomeMixin:
    def _boot(self):
        self.home = Path(tempfile.mkdtemp(prefix="zai-auth-"))
        _, self.port = start_server(self, self.home)
        self.inbox = self.home / "state" / "inbox"

    def _inbox_files(self):
        if not self.inbox.is_dir():
            return set()
        return {p.name for p in self.inbox.iterdir()}

    def _post(self, headers: dict, body: bytes = None, path="/api/actions"):
        return http_request(self.port, "POST", path,
                            body=_run_payload() if body is None else body,
                            headers=headers)


class CsrfProbeTestCase(_AuthHomeMixin, unittest.TestCase):
    """审计的原始攻击序列逐条复放——每条都必须在落盘前被拒。"""

    def setUp(self):
        self._boot()

    def test_cross_origin_text_plain_direct_run_rejected_no_inbox(self):
        # 浏览器 simple request 全形：跨源 Origin + text/plain + 无 token
        status, _h, data = self._post({"Content-Type": "text/plain",
                                       "Origin": "http://evil.example"})
        self.assertEqual(status, 403)
        assert_envelope(self, json.loads(data.decode("utf-8")), "FORBIDDEN")
        self.assertEqual(self._inbox_files(), set())

    def test_cross_origin_rejected_even_with_valid_token_and_json(self):
        # Origin 闸独立于 token：拿到 token 也不许跨源写（纵深）
        headers = auth_headers(self.port)
        headers["Origin"] = "http://evil.example"
        status, _h, data = self._post(headers)
        self.assertEqual(status, 403)
        assert_envelope(self, json.loads(data.decode("utf-8")), "FORBIDDEN")
        self.assertEqual(self._inbox_files(), set())

    def test_null_origin_rejected(self):
        # sandboxed iframe / data: 页的 Origin: "null"——不在白名单，403
        headers = auth_headers(self.port)
        headers["Origin"] = "null"
        status, _h, _d = self._post(headers)
        self.assertEqual(status, 403)
        self.assertEqual(self._inbox_files(), set())

    def test_text_plain_with_same_origin_and_token_rejected_415(self):
        # simple-request 向量单杀：即便同源 + token，text/plain 也不解析
        headers = auth_headers(self.port, content_type="text/plain")
        status, _h, data = self._post(headers)
        self.assertEqual(status, 415)
        assert_envelope(self, json.loads(data.decode("utf-8")),
                        "INVALID_FIELD")
        self.assertEqual(self._inbox_files(), set())


class HostGateTestCase(_AuthHomeMixin, unittest.TestCase):
    """anti-rebind：Host 是回环 hostname 才放行（每个请求，GET 也查）。"""

    def setUp(self):
        self._boot()

    def test_rebound_host_rejected_on_get(self):
        status, _h, data = http_request(self.port, "GET", "/api/board",
                                        headers={"Host": "evil.example"})
        self.assertEqual(status, 403)
        assert_envelope(self, json.loads(data.decode("utf-8")), "FORBIDDEN")

    def test_rebound_host_rejected_on_post_no_inbox(self):
        headers = auth_headers(self.port)
        headers["Host"] = "evil.example:80"
        status, _h, _d = self._post(headers)
        self.assertEqual(status, 403)
        self.assertEqual(self._inbox_files(), set())

    def test_loopback_hosts_any_port_accepted(self):
        # hostname 判定、端口不参与（vite dev proxy 原样转发 Host:...:5173）
        for host in ("127.0.0.1:5173", "localhost", "LOCALHOST:80",
                     "[::1]:9999"):
            with self.subTest(host=host):
                status, _h, _d = http_request(
                    self.port, "GET", "/api/board", headers={"Host": host})
                self.assertNotEqual(status, 403)


class TokenGateTestCase(_AuthHomeMixin, unittest.TestCase):
    """instance token：一切写必带；读 token-light；owner 合法面照常工作。"""

    def setUp(self):
        self._boot()

    def test_missing_token_write_is_401_no_inbox(self):
        headers = auth_headers(self.port)
        del headers[security.TOKEN_HEADER]
        status, _h, data = self._post(headers)
        self.assertEqual(status, 401)
        assert_envelope(self, json.loads(data.decode("utf-8")),
                        "UNAUTHORIZED")
        self.assertEqual(self._inbox_files(), set())

    def test_wrong_token_write_is_401_no_inbox(self):
        headers = auth_headers(self.port)
        headers[security.TOKEN_HEADER] = "not-the-token"
        status, _h, _d = self._post(headers)
        self.assertEqual(status, 401)
        self.assertEqual(self._inbox_files(), set())

    def test_owner_write_with_origin_and_token_still_works(self):
        # 同源 + token 的 owner 面必须完好——含 §34 直跑（本面 owner 特权，
        # 依据 = 四闸鉴权而非裸信 localhost；§49 v0.48.1 拍板）
        status, _h, data = self._post(auth_headers(self.port))
        self.assertEqual(status, 200)
        obj = json.loads(data.decode("utf-8"))
        self.assertTrue(obj.get("ok"))
        self.assertEqual(obj.get("via"), "web")
        files = self._inbox_files()
        self.assertEqual(len(files), 1)
        rec = json.loads((self.inbox / next(iter(files))).read_text("utf-8"))
        self.assertEqual(rec.get("mode"), "run")
        self.assertEqual(rec.get("via"), "web")

    def test_non_browser_write_without_origin_needs_token_only(self):
        # 无 Origin = 非浏览器客户端（boardctl/curl）：token 即墙。浏览器的
        # 跨源写恒带 Origin，这条缺席通道不构成 CSRF 面（security.py 注）。
        headers = auth_headers(self.port)
        del headers["Origin"]
        status, _h, _d = self._post(headers)
        self.assertEqual(status, 200)

    def test_reads_stay_token_light(self):
        # dashboard.json 缺席 → 404（而非 401/403）：读路径不吃 token 闸
        status, _h, data = http_request(self.port, "GET", "/api/board")
        self.assertEqual(status, 404)
        assert_envelope(self, json.loads(data.decode("utf-8")), "NOT_FOUND")

    def test_reveal_route_is_token_gated_too(self):
        headers = auth_headers(self.port)
        del headers[security.TOKEN_HEADER]
        status, _h, _d = self._post(
            headers, body=json.dumps({"card_id": "R-1"}).encode("utf-8"),
            path="/api/reveal")
        self.assertEqual(status, 401)


class TokenLifecycleTestCase(unittest.TestCase):
    """token 铸造/注入：0600、跨启动稳定、只进本面服务的 index.html。"""

    def test_token_file_created_0600_and_stable(self):
        home = Path(tempfile.mkdtemp(prefix="zai-auth-tok-"))
        tok = security.load_or_create_token(home)
        p = security.token_path(home)
        self.assertTrue(p.is_file())
        self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)
        self.assertEqual(security.load_or_create_token(home), tok)

    def test_index_html_gets_token_injected(self):
        home = Path(tempfile.mkdtemp(prefix="zai-auth-inject-"))
        dist = home / "dist"
        dist.mkdir(parents=True)
        (dist / "index.html").write_text(
            "<html><head><title>x</title></head><body></body></html>",
            encoding="utf-8")
        httpd = app_mod.make_server(port=0, home=home, static_dir=dist,
                                    start_watcher=False)
        import threading
        threading.Thread(target=httpd.serve_forever,
                         kwargs={"poll_interval": 0.05}, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        port = httpd.server_address[1]
        status, headers, body = http_request(port, "GET", "/")
        self.assertEqual(status, 200)
        tok = security.load_or_create_token(home)
        snippet = "window.__ZAI_TOKEN__=%s" % json.dumps(tok)
        self.assertIn(snippet, body.decode("utf-8"))
        # 注入页绝不进别人的 iframe（webui X-Frame 纪律的移植）
        self.assertEqual(headers.get("X-Frame-Options"), "DENY")

    def test_assets_are_not_injected(self):
        home = Path(tempfile.mkdtemp(prefix="zai-auth-asset-"))
        dist = home / "dist"
        dist.mkdir(parents=True)
        (dist / "index.html").write_text("<head></head>", encoding="utf-8")
        (dist / "app.js").write_text("__ZAI_TOKEN__; // reference only",
                                     encoding="utf-8")
        httpd = app_mod.make_server(port=0, home=home, static_dir=dist,
                                    start_watcher=False)
        import threading
        threading.Thread(target=httpd.serve_forever,
                         kwargs={"poll_interval": 0.05}, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        port = httpd.server_address[1]
        tok = security.load_or_create_token(home)
        _s, _h, body = http_request(port, "GET", "/app.js")
        self.assertNotIn(tok, body.decode("utf-8"))


class SecurityUnitTestCase(unittest.TestCase):
    """纯函数面（host/origin/content-type 判定）的真值表。"""

    def test_host_ok_truth_table(self):
        ok = ("127.0.0.1", "127.0.0.1:47820", "localhost:5173",
              "LocalHost", "[::1]", "[::1]:8080")
        bad = ("evil.example", "evil.example:47820", "127.0.0.1.evil.com",
               "", None, 42, "10.0.0.5:80", "::1")
        for h in ok:
            self.assertTrue(security.host_ok(h), h)
        for h in bad:
            self.assertFalse(security.host_ok(h), repr(h))

    def test_origin_ok_exact_match_only(self):
        allowed = security.allowed_origins(47820)
        self.assertTrue(security.origin_ok("http://127.0.0.1:47820", allowed))
        self.assertTrue(security.origin_ok("http://localhost:47820", allowed))
        for o in ("http://127.0.0.1:5173", "https://127.0.0.1:47820",
                  "http://evil.example", "null", "", None):
            self.assertFalse(security.origin_ok(o, allowed), repr(o))

    def test_content_type_json_with_params_ok(self):
        self.assertTrue(security.content_type_is_json("application/json"))
        self.assertTrue(security.content_type_is_json(
            "application/json; charset=utf-8"))
        self.assertTrue(security.content_type_is_json("Application/JSON"))
        for ct in ("text/plain", "multipart/form-data",
                   "application/x-www-form-urlencoded", "", None):
            self.assertFalse(security.content_type_is_json(ct), repr(ct))


if __name__ == "__main__":
    unittest.main()
