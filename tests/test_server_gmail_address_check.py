"""§68.1 追记：目录字段的形状校验 ``check``（今日词表 ``email``，挂在 gmail 区的 ``gmail_address``）。

原生 SettingsGmail.validateAddress（保存前拦：恰好一个 @、本地部分非空、域名含 . 且不以 . 起止）：
- PUT 不合格 → 400 INVALID_FIELD，message 双语并列（zh / en，server/settings.py 同款），details 带 ``check``；
  overrides 不落；空串仍是「清键」、不查；
- 目录投影 add-only ``check: {kind, message{zh,en}}``（句子 server-owned，web 镜像同一条规则、同一句）；
  没有 check 的字段不带这把键。
"""
import json
import tempfile
import unittest
from pathlib import Path

from tests import TMP_HOME  # noqa: F401 - sandbox env first
from tests.test_server_common import assert_envelope, auth_headers, get_json, http_request, start_server, write_text

from server import settings_catalog as catalog

BAD = ("foo", "a@b", "@b.c", "a@.b", "a@b.", "a b@c.d", "a@b@c.d", "a@", "a@b c.d")
GOOD = ("you@gmail.com", "first.last@corp.example.com", "  padded@x.io  ", "a+tag@b.co")


def put_json(port, path, payload):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    status, _h, data = http_request(port, "PUT", path, body=body, headers=auth_headers(port))
    return status, json.loads(data.decode("utf-8"))


class LooksLikeEmailTestCase(unittest.TestCase):
    def test_rule_table(self):
        for raw in GOOD:
            self.assertTrue(catalog.looks_like_email(raw), raw)
        for raw in BAD:
            self.assertFalse(catalog.looks_like_email(raw), raw)

    def test_unknown_check_kind_is_a_catalog_authoring_error(self):
        with self.assertRaises(ValueError):
            catalog._f("k", "string", "x", "y", check="phone")


class GmailAddressCheckTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-gmail-addr-")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        (self.home / "state").mkdir(parents=True)
        _httpd, self.port = start_server(self, self.home)

    @property
    def overrides_path(self) -> Path:
        return self.home / "state" / "settings_overrides.json"

    def _field(self, obj, key):
        return next(f for f in obj["fields"] if f["key"] == key)

    def test_bad_shapes_are_400_with_the_native_sentence_and_nothing_written(self):
        for raw in BAD:
            with self.subTest(raw=raw):
                status, obj = put_json(self.port, "/api/settings/gmail", {"gmail_address": raw})
                self.assertEqual(status, 400)
                assert_envelope(self, obj, "INVALID_FIELD")
                self.assertEqual(obj["error"]["details"], {"field": "gmail_address", "check": "email"})
                self.assertIn(catalog.CHECKS["email"]["zh"], obj["error"]["message"])
                self.assertIn(catalog.CHECKS["email"]["en"], obj["error"]["message"])
        self.assertFalse(self.overrides_path.exists())

    def test_bad_address_blocks_the_whole_put(self):
        # 全部键先校验再落盘：同一 payload 里合法的开关也不落
        status, _obj = put_json(self.port, "/api/settings/gmail", {"gmail_enabled": False, "gmail_address": "nope"})
        self.assertEqual(status, 400)
        self.assertFalse(self.overrides_path.exists())

    def test_good_shapes_save_trimmed(self):
        for raw in GOOD:
            with self.subTest(raw=raw):
                status, obj = put_json(self.port, "/api/settings/gmail", {"gmail_address": raw})
                self.assertEqual(status, 200)
                self.assertEqual(self._field(obj, "gmail_address")["effective"], raw.strip())
        self.assertEqual(json.loads(self.overrides_path.read_text(encoding="utf-8")), {"gmail_address": "a+tag@b.co"})

    def test_empty_still_clears_without_checking(self):
        write_text(self.overrides_path, json.dumps({"gmail_address": "you@gmail.com"}))
        status, obj = put_json(self.port, "/api/settings/gmail", {"gmail_address": "   "})
        self.assertEqual(status, 200)
        self.assertEqual(self._field(obj, "gmail_address")["source"], "default")
        self.assertEqual(json.loads(self.overrides_path.read_text(encoding="utf-8")), {})

    def test_catalog_projects_the_check_only_on_checked_fields(self):
        _s, gmail = get_json(self.port, "/api/settings/gmail")
        self.assertEqual(self._field(gmail, "gmail_address")["check"],
                         {"kind": "email", "message": catalog.CHECKS["email"]})
        for key in ("gmail_enabled", "gmail_fetch_command"):
            self.assertNotIn("check", self._field(gmail, key))
        _s, everything = get_json(self.port, "/api/settings")
        checked = [(s["id"], f["key"]) for s in everything["sections"] for f in s["fields"] if "check" in f]
        # session_id 的 check 归 §68.7 追记（tests/test_server_maintainer_session_id_check.py）
        self.assertEqual(checked, [("gmail", "gmail_address"), ("maintainer", "maintainer_session_id")])

    def test_check_sentence_is_the_native_one_verbatim(self):
        # 原生 SettingsGmail.swift validateAddress 的两句逐字（§66.2 copy：server-owned，web 只取键）
        self.assertEqual(catalog.CHECKS["email"]["zh"], "邮箱格式不对——例：you@gmail.com（公司 Google Workspace 邮箱也可以）")
        self.assertEqual(catalog.CHECKS["email"]["en"],
                         "That email doesn't look right — e.g. you@gmail.com (a Google Workspace address works too)")


if __name__ == "__main__":
    unittest.main()
