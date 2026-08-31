"""§49 auth model 判例（server/security.py + app._check_auth）。

钉死 v0.48.1 关掉的 CRITICAL CSRF 洞（V048-AUDIT §5）：跨源浏览器
simple request（Content-Type: text/plain、无预检）曾经能对 /api/actions
直发 ``mode:"run"``，被 inbox 落款 via:"web" → actd 按 owner ingress 放行
→ APPROVED 直跑。四闸（Host / Origin / Content-Type / instance token）
之后，这条路在 body 被解析**之前**就断——本文件的每个拒绝判例都同时断言
「零落盘」。
"""
from __future__ import annotations

import http.client
import json
import os
import stat
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from tests import TMP_HOME  # noqa: F401 - 先落沙箱 env
from tests.test_server_common import (assert_envelope, auth_headers,
                                      http_request, rewrite_board, seed_scene,
                                      start_server)

from server import security
from server import app as app_mod

HERO = "R-101"

# Windows 口径（CI 判例，2026-08-31）：st_mode 组/他人位在 Windows 是合成值
# （可写文件一律 ~0o666），0600 断言只在 POSIX 有意义；权限收回在生产侧也是
# POSIX-only 关切（server/security.py）。内容校验/重铸与 symlink 拒跟随是
# 跨平台行为，照常在 Windows 上跑。
_POSIX = os.name == "posix"


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
        if _POSIX:  # 0600 是 POSIX mode-bit 语义；Windows 合成 0o666（文件头注）
            self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)
        # 跨启动稳定是跨平台契约——Windows 上也必须不换 token
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


class TokenInjectionEscapeTestCase(unittest.TestCase):
    """M1：inject_token 必须转义 ``<`` / ``/``，一个含 ``</script>`` 的 token
    不得提前闭合脚本标签逃逸。"""

    def test_script_close_in_token_is_escaped(self):
        html = b"<html><head></head><body></body></html>"
        # 人造毒 token（正常 token 过 _TOKEN_RE 不含这些字符——双保险测转义本身）
        out = security.inject_token(html, "abc</script><script>evil//x").decode()
        self.assertNotIn("</script><script>evil", out)
        self.assertIn("\\u003c", out)      # < 被转义
        self.assertIn("\\u002f", out)      # / 被转义
        # 注入的 script 标签本身仍然只有一对（未被 token 内容劈开）
        self.assertEqual(out.count("<script>"), 1)
        self.assertEqual(out.count("</script>"), 1)


class TokenFileHardeningTestCase(unittest.TestCase):
    """M2/M3：既有 token 文件的权限收回、坏内容重铸、symlink 拒跟随。"""

    def _home(self, prefix: str) -> Path:
        home = Path(tempfile.mkdtemp(prefix=prefix))
        (home / "state").mkdir(parents=True)
        return home

    @unittest.skipIf(sys.platform == "win32",
                     "权限收回是 POSIX mode-bit 语义——Windows 合成 mode 位、"
                     "无 fchmod，生产侧按设计不收回（ACL 管真实访问）")
    def test_group_other_readable_token_is_rehardened(self):
        # M2：历史 0644 token（任何本地账户可读）在读路径被 chmod 收回 0600
        home = self._home("zai-tok-perm-")
        p = security.token_path(home)
        p.write_text("existingtoken123\n", encoding="utf-8")
        os.chmod(p, 0o644)
        tok = security.load_or_create_token(home)
        self.assertEqual(tok, "existingtoken123")   # 合法值保留（不无谓换 token）
        self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)

    def test_malformed_token_is_discarded_and_reminted(self):
        # M1 纵深：坏字符 token（含 </script>）读回即弃用、重铸干净值——
        # 弃用/重铸是跨平台行为，Windows 也跑；只有 0600 断言是 POSIX 语义
        home = self._home("zai-tok-bad-")
        p = security.token_path(home)
        p.write_text("abc</script>def\n", encoding="utf-8")
        tok = security.load_or_create_token(home)
        self.assertNotEqual(tok, "abc</script>def")
        self.assertRegex(tok, r"^[A-Za-z0-9_-]+$")
        if _POSIX:
            self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)

    def test_symlinked_token_is_not_followed(self):
        # M3：state/server.token 是 symlink 时既不从它读、也不 truncate 目标。
        # 生产侧的拒绝是可移植的 is_symlink 检查（Windows 无 O_NOFOLLOW，CI
        # 判例：只靠 flag 会真的跟随过去）——所以本测试在 Windows 也照常跑；
        # 只有「符号链接创建本身」在无特权的 Windows 用户下不可用，届时跳过。
        home = self._home("zai-tok-link-")
        secret = home / "victim.txt"
        secret.write_text("do-not-touch", encoding="utf-8")
        p = security.token_path(home)
        try:
            os.symlink(secret, p)
        except OSError:
            self.skipTest("symlink creation not permitted for this user")
        tok = security.load_or_create_token(home)
        # 目标文件内容原封不动（没被当 token 覆写/截断）
        self.assertEqual(secret.read_text(encoding="utf-8"), "do-not-touch")
        # 铸出的是干净的新 token（不是 symlink 目标的内容）
        self.assertRegex(tok, r"^[A-Za-z0-9_-]+$")
        self.assertNotEqual(tok, "do-not-touch")


class PreAuthAndCorsTestCase(_AuthHomeMixin, unittest.TestCase):
    """审查补漏：鉴权先于 body 读取；OPTIONS 不发 CORS 头。"""

    def setUp(self):
        self._boot()

    def test_body_stays_unread_when_auth_fails(self):
        # (a) 声明超大 Content-Length 但只发几字节 + 坏 Host：若 server 在鉴权
        # 前读 body，会阻塞到超时；正确行为是**先鉴权**、立刻 403，不碰 body。
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.putrequest("POST", "/api/actions", skip_host=True)
            conn.putheader("Host", "evil.example")   # Host 闸先拒
            conn.putheader("Content-Type", "application/json")
            conn.putheader("Content-Length", str(10_000_000))
            conn.endheaders()
            conn.send(b"{}")                          # 远小于声明长度
            resp = conn.getresponse()
            self.assertEqual(resp.status, 403)        # 没读 body 就拒了
            resp.read()
        finally:
            conn.close()
        self.assertEqual(self._inbox_files(), set())

    def test_options_emits_no_cors_headers(self):
        # (b) OPTIONS（未实现方法）绝不回 Access-Control-Allow-* ——本面永不
        # 让跨源页面读任何响应
        status, headers, _body = http_request(
            self.port, "OPTIONS", "/api/actions",
            headers={"Origin": "http://evil.example"})
        joined = " ".join(f"{k}:{v}" for k, v in headers.items()).lower()
        self.assertNotIn("access-control-allow", joined)
        self.assertNotEqual(status, 200)


class DeliverableNotInjectedTestCase(unittest.TestCase):
    """(c) /files/deliverables/* 绝不注入 token——否则 token 会漏进 agent 生成
    的交付物 HTML（本面最该防的泄露路径）。"""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="zai-auth-dlv-"))
        dash = seed_scene(self.home, "initial")
        repo = self.home / "demo-repo"
        dlv = repo / "deliverables"
        dlv.mkdir(parents=True)
        for row in dash["needs_approval"]:
            if row["id"] == HERO:
                row["target_repo"] = str(repo)
        rewrite_board(self.home, dash)
        # 交付物 HTML 刻意含 __ZAI_TOKEN__ 字样 + <head>——若走注入会被改写
        (dlv / "out.html").write_text(
            "<html><head></head><body>__ZAI_TOKEN__</body></html>",
            encoding="utf-8")
        _, self.port = start_server(self, self.home)

    def test_deliverable_html_carries_no_token(self):
        tok = security.load_or_create_token(self.home)
        status, _h, body = http_request(
            self.port, "GET", f"/files/deliverables/{HERO}/out.html")
        self.assertEqual(status, 200)
        text = body.decode("utf-8")
        self.assertNotIn(tok, text)
        self.assertNotIn("window.__ZAI_TOKEN__=", text)   # 注入片段绝不出现
        self.assertIn("__ZAI_TOKEN__", text)              # 原文字样原样保留


if __name__ == "__main__":
    unittest.main()
