"""凭证 verify 的三分判决与分类人话（CONTRACT §68.3 2026-09-05 追记；原生 KeyProbe.Outcome + humanAuthReason）。

原生 Settings.swift 把探针结果分三支：``ok`` / ``unauthorized``（凭证本身的错，红，人话 = humanAuthReason 的
分类句 + raw 括号）/ ``failed``（网络 / 服务——判决未知，橙，章退回「已保存（未验证）」）。server 侧的镜像：

- ``ok:false, network:false`` 多带 add-only ``reason {zh, en}``：Anthropic 去 console 重新生成句，括号里带 raw
  ``detail`` 原文；
- ``ok:true`` 与 ``network:true`` 的回执**不带** ``reason``（既有回执形状零改动）；
- 默认探针的重分类（网络层 mock，零出网）：Anthropic 401 / 403 = 凭证错、其余非 2xx（429 / 529 / 5xx）=
  ``ProbeNetworkError``（判决未知）、2xx 都算通过、错误体 ``error.message`` 进 detail；
- 经 HTTP：``ProbeNetworkError`` → 200 ``{ok:false, network:true}`` 无 ``reason``。
（v2.0.0：Slack / Gmail 的分类句、Gmail 地址前提与 IMAP 探针随雷达退役——全文见 tag full。）
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

# 原生 Settings.swift humanAuthReason 的 Anthropic 句（逐字）；{raw} = 探针 detail
NATIVE = {
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

    def test_anthropic_has_one_sentence_and_the_retired_kinds_none(self):
        self.assertEqual(set(secrets_store.AUTH_REASONS), {"anthropic"})
        self.assertEqual(secrets_store.human_auth_reason("anthropic", "api.anthropic.com answered HTTP 401"),
                         _expected("anthropic", "api.anthropic.com answered HTTP 401"))

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
        write_text(self.home / "config" / "secrets" / "anthropic-api-key.txt", "sk-ant-SECRET1\n")
        self._patch_prober(lambda kind, token, ctx: (False, "api.anthropic.com answered HTTP 401", {}))
        status, obj = post_json(self.port, "/api/secrets/anthropic-api-key.txt/verify", {})
        self.assertEqual(status, 200)
        self.assertEqual(obj["ok"], False)
        self.assertEqual(obj["network"], False)
        self.assertEqual(obj["detail"], "api.anthropic.com answered HTTP 401")
        self.assertEqual(obj["reason"], _expected("anthropic", "api.anthropic.com answered HTTP 401"))
        self.assertNotIn("sk-ant-SECRET1", json.dumps(obj))

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


if __name__ == "__main__":
    unittest.main()
