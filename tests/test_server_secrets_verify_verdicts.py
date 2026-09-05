"""凭证 verify 的三分判决与分类人话（CONTRACT §68.3 2026-09-05 追记；原生 KeyProbe.Outcome + humanAuthReason）。

原生 Settings.swift:2196-2257 / 2291-2346、SettingsSlack.swift:260-290 把探针结果分三支：``ok`` /
``unauthorized``（凭证本身的错，红，人话 = humanAuthReason 的分类句 + raw 括号）/ ``failed``（网络 / 服务——
判决未知，橙，章退回「已保存（未验证）」）。server 侧的镜像：

- ``ok:false, network:false`` 多带 add-only ``reason {zh, en}``：Slack 重新生成 User OAuth Token 句；Gmail 三个
  Workspace telltale（``disabled for your domain`` / ``web login required`` / ``imap access is disabled``）→
  「此路不通」句、``application-specific password required`` → 「粘的是普通密码」句、其余通用句；Anthropic 去
  console 重新生成句；每句括号里带 raw ``detail`` 原文；telltale 比对不分大小写；
- ``ok:true`` 与 ``network:true`` 的回执**不带** ``reason``（既有回执形状零改动）；
- 默认探针的重分类（网络层 mock，零出网）：Anthropic 401 / 403 = 凭证错、其余非 2xx（429 / 529 / 5xx）=
  ``ProbeNetworkError``（判决未知）、2xx 都算通过、错误体 ``error.message`` 进 detail；Slack 五个 token 形状的错误
  码 = 凭证错、其余错误码（ratelimited / internal_error / …）与非 JSON 回应 = ``ProbeNetworkError``；
- 经 HTTP：``ProbeNetworkError`` → 200 ``{ok:false, network:true}`` 无 ``reason``。
"""
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first
from tests.test_server_common import post_json, start_server, write_text

from server import secrets_store

REPO_ROOT = Path(__file__).resolve().parent.parent

# 原生 Settings.swift humanAuthReason 的五句（逐字）；{raw} = 探针 detail
NATIVE = {
    "slack": ("token 无效——到 api.slack.com/apps → OAuth & Permissions 重新生成 User OAuth Token 再粘贴（{raw}）",
              "The token is invalid — regenerate the User OAuth Token at api.slack.com/apps → OAuth & Permissions and paste it again ({raw})"),
    "gmail_workspace": ("你的公司 Google Workspace 禁用了这条登录路（{raw}）——此路不通，不用再试；你读邮件的画面仍会经屏幕录制链进入系统。",
                        "Your company's Google Workspace has disabled this login path ({raw}) — it's a dead end, don't keep trying; mail you read on screen still reaches the system via the recording pipeline."),
    "gmail_normal_password": ("粘贴的是账号普通密码——这里需要的是应用专用密码：点「打开 Google 应用专用密码页」生成一个再粘贴（{raw}）",
                              "That's your normal account password — this needs an app password: click \"Open Google app passwords\" to generate one and paste it ({raw})"),
    "gmail": ("应用密码或地址不对——重新生成一个应用专用密码再粘贴（{raw}）",
              "Wrong app password or address — generate a fresh app password and paste it ({raw})"),
    "anthropic": ("key 无效——到 console.anthropic.com 重新生成，回来粘贴保存（{raw}）",
                  "The key is invalid — regenerate it at console.anthropic.com, then paste and save ({raw})"),
}


def _expected(key, raw):
    zh, en = NATIVE[key]
    return {"zh": zh.replace("{raw}", raw), "en": en.replace("{raw}", raw)}


class HumanAuthReasonTestCase(unittest.TestCase):
    """纯函数：种类 + raw → {zh, en}，句子逐字镜像原生、raw 在括号里。"""

    def test_catalog_sentences_mirror_the_native_source(self):
        swift = (REPO_ROOT / "mac" / "Sources" / "Settings.swift").read_text(encoding="utf-8")
        for key, (zh, en) in NATIVE.items():
            with self.subTest(key=key):
                self.assertEqual(secrets_store.AUTH_REASONS[key], {"zh": zh, "en": en})
                # 原生用 \(raw) 插值；除此之外逐字
                self.assertIn(zh.replace("{raw}", "\\(raw)"), swift)
                self.assertIn(en.replace("{raw}", "\\(raw)").replace('"', '\\"'), swift)

    def test_slack_and_anthropic_have_one_sentence_each(self):
        self.assertEqual(secrets_store.human_auth_reason("slack", "auth.test failed: invalid_auth"),
                         _expected("slack", "auth.test failed: invalid_auth"))
        self.assertEqual(secrets_store.human_auth_reason("anthropic", "api.anthropic.com answered HTTP 401"),
                         _expected("anthropic", "api.anthropic.com answered HTTP 401"))

    def test_gmail_workspace_telltales_case_insensitive(self):
        for raw in ("IMAP LOGIN rejected: [ALERT] Application-specific password required",):
            self.assertEqual(secrets_store.human_auth_reason("gmail", raw), _expected("gmail_normal_password", raw))
        for telltale in ("Disabled For Your Domain", "web login required", "IMAP access is disabled"):
            raw = "IMAP LOGIN rejected: [AUTHENTICATIONFAILED] %s (Failure)" % telltale
            with self.subTest(telltale=telltale):
                self.assertEqual(secrets_store.human_auth_reason("gmail", raw), _expected("gmail_workspace", raw))

    def test_gmail_falls_back_to_the_generic_sentence(self):
        raw = "IMAP LOGIN rejected: [AUTHENTICATIONFAILED] Invalid credentials (Failure)"
        self.assertEqual(secrets_store.human_auth_reason("gmail", raw), _expected("gmail", raw))

    def test_workspace_telltale_wins_over_normal_password_when_both_appear(self):
        # 原生的比对顺序：先 Workspace 三条，再「普通密码」
        raw = "web login required; application-specific password required"
        self.assertEqual(secrets_store.human_auth_reason("gmail", raw), _expected("gmail_workspace", raw))

    def test_unknown_kind_returns_the_raw_in_both_languages(self):
        self.assertEqual(secrets_store.human_auth_reason("plain", "x"), {"zh": "x", "en": "x"})


class VerifyReceiptReasonTestCase(unittest.TestCase):
    """经 HTTP：凭证错的回执带 reason；ok / network 的不带。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-verdicts-")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        (self.home / "state").mkdir(parents=True)
        _httpd, self.port = start_server(self, self.home)

    def _patch_prober(self, fn):
        patcher = mock.patch.object(secrets_store, "default_prober", fn)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_credential_failure_carries_the_classified_reason(self):
        write_text(self.home / "config" / "secrets" / "slack-user-token.txt", "xoxp-1\n")
        self._patch_prober(lambda kind, token, ctx: (False, "auth.test failed: token_revoked", {}))
        status, obj = post_json(self.port, "/api/secrets/slack-user-token.txt/verify", {})
        self.assertEqual(status, 200)
        self.assertEqual(obj["ok"], False)
        self.assertEqual(obj["network"], False)
        self.assertEqual(obj["detail"], "auth.test failed: token_revoked")
        self.assertEqual(obj["reason"], _expected("slack", "auth.test failed: token_revoked"))
        self.assertNotIn("xoxp-1", json.dumps(obj))

    def test_gmail_workspace_telltale_reaches_the_wire(self):
        write_text(self.home / "config" / "secrets" / "gmail-app-password.txt", "abcdefghijklmnop\n")
        write_text(self.home / "config.yaml", "sources:\n  gmail:\n    address: me@corp.com\n")
        raw = "IMAP LOGIN rejected: [ALERT] Web login required: https://support.google.com/mail/answer/78754"
        self._patch_prober(lambda kind, token, ctx: (False, raw, {}))
        _s, obj = post_json(self.port, "/api/secrets/gmail-app-password.txt/verify", {})
        self.assertEqual(obj["reason"], _expected("gmail_workspace", raw))

    def test_ok_and_network_receipts_have_no_reason(self):
        write_text(self.home / "config" / "secrets" / "anthropic-api-key.txt", "sk-ant-1\n")
        self._patch_prober(lambda kind, token, ctx: (True, "key accepted", {}))
        _s, obj = post_json(self.port, "/api/secrets/anthropic-api-key.txt/verify", {})
        self.assertEqual(obj, {"ok": True, "network": False, "detail": "key accepted", "extra": {}})

        def overloaded(kind, token, ctx):
            raise secrets_store.ProbeNetworkError("api.anthropic.com answered HTTP 529: Overloaded")
        self._patch_prober(overloaded)
        _s, obj = post_json(self.port, "/api/secrets/anthropic-api-key.txt/verify", {})
        self.assertEqual(obj["ok"], False)
        self.assertEqual(obj["network"], True)
        self.assertNotIn("reason", obj)
        self.assertIn("HTTP 529", obj["detail"])

    def test_verify_by_value_failure_also_carries_the_reason(self):
        self._patch_prober(lambda kind, token, ctx: (False, "api.anthropic.com answered HTTP 403", {}))
        _s, obj = post_json(self.port, "/api/secrets/anthropic-api-key.txt/verify", {"value": "sk-ant-bad"})
        self.assertEqual(obj["reason"], _expected("anthropic", "api.anthropic.com answered HTTP 403"))
        self.assertFalse((self.home / "config" / "secrets" / "anthropic-api-key.txt").exists())


class _Resp:
    def __init__(self, status, body):
        self.status = status
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class DefaultProberReclassificationTestCase(unittest.TestCase):
    """默认探针的三分（urlopen 被替身替换，零出网）。"""

    def _patch_urlopen(self, fn):
        patcher = mock.patch.object(secrets_store.urllib.request, "urlopen", fn)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _http_error(self, code, body=b"{}"):
        def urlopen(req, timeout):
            raise secrets_store.urllib.error.HTTPError(req.full_url, code, "err", {}, io.BytesIO(body))
        return urlopen

    def test_anthropic_401_and_403_are_credential_failures(self):
        for code in (401, 403):
            with self.subTest(code=code):
                self._patch_urlopen(self._http_error(code, b'{"error": {"type": "authentication_error", "message": "invalid x-api-key"}}'))
                ok, detail, extra = secrets_store.default_prober("anthropic", "bad", {})
                self.assertFalse(ok)
                self.assertEqual(detail, "api.anthropic.com answered HTTP %d: invalid x-api-key" % code)
                self.assertEqual(extra, {})

    def test_anthropic_other_statuses_are_verdict_unknown(self):
        for code in (429, 500, 529):
            with self.subTest(code=code):
                self._patch_urlopen(self._http_error(code, b'{"error": {"message": "Overloaded"}}'))
                with self.assertRaises(secrets_store.ProbeNetworkError) as ctx:
                    secrets_store.default_prober("anthropic", "k", {})
                self.assertEqual(str(ctx.exception), "api.anthropic.com answered HTTP %d: Overloaded" % code)

    def test_anthropic_any_2xx_passes_and_non_json_body_has_no_suffix(self):
        self._patch_urlopen(lambda req, timeout: _Resp(204, b""))
        ok, _detail, _extra = secrets_store.default_prober("anthropic", "k", {})
        self.assertTrue(ok)
        self._patch_urlopen(self._http_error(401, b"<html>"))
        ok, detail, _extra = secrets_store.default_prober("anthropic", "k", {})
        self.assertFalse(ok)
        self.assertEqual(detail, "api.anthropic.com answered HTTP 401")

    def test_slack_token_shaped_codes_are_credential_failures(self):
        self.assertEqual(secrets_store.SLACK_TOKEN_ERRORS,
                         {"invalid_auth", "not_authed", "account_inactive", "token_revoked", "token_expired"})
        for code in sorted(secrets_store.SLACK_TOKEN_ERRORS):
            with self.subTest(code=code):
                self._patch_urlopen(lambda req, timeout, code=code: _Resp(200, json.dumps({"ok": False, "error": code}).encode()))
                ok, detail, extra = secrets_store.default_prober("slack", "xoxp-1", {})
                self.assertFalse(ok)
                self.assertEqual(detail, "auth.test failed: %s" % code)
                self.assertEqual(extra, {})

    def test_slack_other_codes_and_non_json_are_verdict_unknown(self):
        for code in ("ratelimited", "internal_error", "service_unavailable", "fatal_error"):
            with self.subTest(code=code):
                self._patch_urlopen(lambda req, timeout, code=code: _Resp(200, json.dumps({"ok": False, "error": code}).encode()))
                with self.assertRaises(secrets_store.ProbeNetworkError) as ctx:
                    secrets_store.default_prober("slack", "xoxp-1", {})
                self.assertEqual(str(ctx.exception), "auth.test failed: %s" % code)
        self._patch_urlopen(lambda req, timeout: _Resp(200, b'{"ok": false}'))
        with self.assertRaises(secrets_store.ProbeNetworkError) as ctx:
            secrets_store.default_prober("slack", "xoxp-1", {})
        self.assertEqual(str(ctx.exception), "auth.test failed: unknown_error")
        self._patch_urlopen(lambda req, timeout: _Resp(502, b"<html>bad gateway</html>"))
        with self.assertRaises(secrets_store.ProbeNetworkError):
            secrets_store.default_prober("slack", "xoxp-1", {})

    def test_slack_native_vocabulary_mirrors_the_swift_sources(self):
        # 原生两处（KeyProbe.slack / SettingsSlack.authTest）同一张表；server 常量逐字对上
        for rel in ("Settings.swift", "SettingsSlack.swift"):
            swift = (REPO_ROOT / "mac" / "Sources" / rel).read_text(encoding="utf-8")
            for code in secrets_store.SLACK_TOKEN_ERRORS:
                self.assertIn('"%s"' % code, swift, (rel, code))


if __name__ == "__main__":
    unittest.main()
