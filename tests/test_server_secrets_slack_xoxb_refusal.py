"""Slack token 保存拒 ``xoxb-`` Bot token（CONTRACT §68.3 2026-09-05 追记）。

原生 SettingsSlack.swift saveToken：Bot token 能过 auth.test 却读不了你的 DM——门口拒绝、永不落盘，
说「这是 Bot token（xoxb-）——雷达读你的 DM 需要 User OAuth Token（xoxp- 开头，在 OAuth & Permissions
页的 User 区）。」web SecretRow 先按同一句拒（不 PUT）；server 这层给绕过 UI 的写者留同一道门：

- ``PUT /api/secrets/slack-user-token.txt {value: "xoxb-…"}`` → 400 INVALID_FIELD，
  ``details.reason`` 带 {zh, en} 原生原句（逐字），文件不产生、既有文件不动；
- 判断在首个非空行、trim 之后（多行粘贴 / 前后空白照旧）；
- 非 xoxp- 的其它前缀（原生只给橙色提示）server 照存——提示是 UI 的事；
- 别的凭证名里出现 ``xoxb-`` 不管（只有 Slack 行有这个语义）。
"""
import json
import tempfile
import unittest
from pathlib import Path

from tests import TMP_HOME  # noqa: F401 - sandbox env first
from tests.test_server_common import (assert_envelope, auth_headers, http_request,
                                      start_server, write_text)

from server import secrets_store

NATIVE_ZH = "这是 Bot token（xoxb-）——雷达读你的 DM 需要 User OAuth Token（xoxp- 开头，在 OAuth & Permissions 页的 User 区）。"
NATIVE_EN = "That's a Bot token (xoxb-) — reading your DMs needs the User OAuth Token (starts with xoxp-, in the User section of OAuth & Permissions)."


def _put(port, name, value):
    body = json.dumps({"value": value}).encode("utf-8")
    status, _h, data = http_request(port, "PUT", "/api/secrets/" + name, body=body, headers=auth_headers(port))
    return status, json.loads(data.decode("utf-8"))


class SlackXoxbRefusalTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-xoxb-")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        (self.home / "state").mkdir(parents=True)
        _httpd, self.port = start_server(self, self.home)
        self.path = self.home / "config" / "secrets" / "slack-user-token.txt"

    def test_bot_token_is_400_with_the_native_sentence_and_never_written(self):
        status, obj = _put(self.port, "slack-user-token.txt", "  xoxb-1234-BOTTOKEN  \n")
        self.assertEqual(status, 400)
        assert_envelope(self, obj, "INVALID_FIELD")
        self.assertEqual(obj["error"]["details"]["field"], "value")
        self.assertEqual(obj["error"]["details"]["reason"], {"zh": NATIVE_ZH, "en": NATIVE_EN})
        self.assertNotIn("BOTTOKEN", json.dumps(obj))
        self.assertFalse(self.path.exists())

    def test_refusal_leaves_an_existing_user_token_untouched(self):
        write_text(self.path, "xoxp-GOOD\n")
        status, _obj = _put(self.port, "slack-user-token.txt", "xoxb-BAD")
        self.assertEqual(status, 400)
        self.assertEqual(self.path.read_text(encoding="utf-8"), "xoxp-GOOD\n")

    def test_first_non_empty_line_decides(self):
        status, _obj = _put(self.port, "slack-user-token.txt", "\n\n  xoxb-hidden-on-line-three\n")
        self.assertEqual(status, 400)
        self.assertFalse(self.path.exists())

    def test_other_prefixes_are_stored_as_before(self):
        for value in ("xoxp-USER", "not-a-slack-shape"):
            with self.subTest(value=value):
                status, obj = _put(self.port, "slack-user-token.txt", value)
                self.assertEqual(status, 200)
                self.assertTrue(obj["present"])
                self.assertEqual(self.path.read_text(encoding="utf-8"), value + "\n")

    def test_only_the_slack_row_has_the_rule(self):
        status, obj = _put(self.port, "anthropic-api-key.txt", "xoxb-looks-odd-but-not-our-business")
        self.assertEqual(status, 200)
        self.assertTrue(obj["present"])

    def test_native_sentence_is_the_one_the_module_exports(self):
        """web SecretRow 内联的是同一句（判例把两侧钉在这两个字面量上）。"""
        self.assertEqual(secrets_store.XOXB_REFUSAL, {"zh": NATIVE_ZH, "en": NATIVE_EN})
        src = (Path(__file__).resolve().parent.parent / "web" / "src" / "components" / "settings" / "SecretRow.tsx").read_text(encoding="utf-8")
        self.assertIn(NATIVE_ZH, src)
        self.assertIn(NATIVE_EN, src)


if __name__ == "__main__":
    unittest.main()
