"""豆包语音凭证的保存归一（CONTRACT §36 v0.37.1 / §68.3 2026-09-05 追记）。

原生 Settings.swift CredentialRowView.save() 把粘贴交给 ``VolcanoSpeechCredential.parse``，
旧版 App ID + Access Token 对存成两行 ``appid:<id>`` / ``token:<tok>``——壳里逐字节冻结的
``decode()``（shell/Sources/CaptionCore.swift）只认这一种旧版形状。web 的写者是 server，
所以 ``server/secrets_store.py`` 带一份 Python 镜像 ``volcano_speech_credential``，本文件：

- 镜像 fixture：25 组粘贴 → (isLegacy, fileRepresentation)，2026-09-05 用 ``swiftc`` 编译
  mac/Sources/CaptionCore.swift:431-565 的 enum 逐条跑出来的期望值，两侧必须逐字一致；
- ``PUT /api/secrets/volcano-speech-key.txt``：旧版对（两行 / 一行 / 带标签）→ 文件两行带标签，
  回执 add-only ``legacy_pair: true``；裸新版 key 原样、``legacy_pair: false``；硬折行的 key 拼回；
  其它名字照旧只留首行、``legacy_pair: false``；
- 值仍不回显；壳 decode() 读回的两行文件 present 为真。
"""
import json
import tempfile
import unittest
from pathlib import Path

from tests import TMP_HOME  # noqa: F401 - sandbox env first
from tests.test_server_common import auth_headers, get_json, http_request

from server import secrets_store

TOKEN = "2tzAbCdEfGhIjKlMnOpQrStUvWxYz012"
PAIR = "appid:123456789\ntoken:" + TOKEN

# (粘贴, 期望)：期望 None = 空；否则 (legacy, file)。顺序与 Swift harness 输出一一对应。
SWIFT_FIXTURES = [
    ("", None),
    ("   \n", None),
    ("sk-new-console-key-1234567890", (False, "sk-new-console-key-1234567890")),
    ("123456789:" + TOKEN, (True, PAIR)),
    ("123456789 " + TOKEN, (True, PAIR)),
    ("123456789：" + TOKEN, (True, PAIR)),
    ("123456789, " + TOKEN, (True, PAIR)),
    ("123456789\n" + TOKEN, (True, PAIR)),
    ("App ID: 123456789\nAccess Token: " + TOKEN, (True, PAIR)),
    ("appid:123456789\ntoken:" + TOKEN, (True, PAIR)),
    ("APP_ID：123456789 ACCESS_TOKEN：" + TOKEN, (True, PAIR)),
    ("123456789 token: " + TOKEN, (True, PAIR)),
    ("abcdefgh\nijklmnop", (False, "abcdefghijklmnop")),                      # 硬折行的 key 拼回
    ("12345:" + TOKEN, (False, "12345:" + TOKEN)),                            # 5 位不是 App ID
    ("123456789:short", (False, "123456789:short")),                          # token 太短
    ("note: this-is-a-key-with-colon-1234567890", (False, "note: this-is-a-key-with-colon-1234567890")),
    ("first\nsecond\nthird", (False, "firstsecondthird")),
    ("  sk-padded-key-1234567890  \n", (False, "sk-padded-key-1234567890")),
    ("Token: 123456789\nAppID: " + TOKEN, (True, PAIR)),                     # 标签只剥不校：形状说了算
    ("app key: 123456789 ; secret: " + TOKEN, (True, PAIR)),
    ("123456789\t" + TOKEN, (True, PAIR)),
    ("appid:123456789\n\n  token: " + TOKEN + "  \n", (True, PAIR)),
    ("Access Token: " + TOKEN + "\nApp ID: 123456789",
     (False, "Access Token: " + TOKEN + "App ID: 123456789")),               # 顺序反了 = 不是那一对
    ("1234567890123:" + TOKEN, (False, "1234567890123:" + TOKEN)),          # 13 位超出 App ID
    ("123456789:" + TOKEN + " extra", (False, "123456789:" + TOKEN + " extra")),
]


class VolcanoSpeechCredentialMirrorTestCase(unittest.TestCase):
    def test_python_mirror_matches_the_frozen_swift_enum_on_every_fixture(self):
        for pasted, expected in SWIFT_FIXTURES:
            with self.subTest(pasted=pasted[:40]):
                got = secrets_store.volcano_speech_credential(pasted)
                if expected is None:
                    self.assertIsNone(got)
                else:
                    self.assertEqual((got["legacy"], got["file"]), expected)

    def test_legacy_file_is_exactly_what_the_shell_decode_recognises(self):
        """decode() 的门：恰好两行、首行 ``appid:`` 前缀、次行 ``token:`` 前缀、两半非空。"""
        got = secrets_store.volcano_speech_credential("App ID: 123456789\nAccess Token: " + TOKEN)
        lines = got["file"].split("\n")
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].startswith("appid:") and lines[0][len("appid:"):])
        self.assertTrue(lines[1].startswith("token:") and lines[1][len("token:"):])


def _put(port, name, value):
    body = json.dumps({"value": value}).encode("utf-8")
    status, _h, data = http_request(port, "PUT", "/api/secrets/" + name, body=body, headers=auth_headers(port))
    return status, json.loads(data.decode("utf-8"))


class VolcanoSpeechPutTestCase(unittest.TestCase):
    def setUp(self):
        from tests.test_server_common import start_server
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-volcano-")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        (self.home / "state").mkdir(parents=True)
        _httpd, self.port = start_server(self, self.home)

    def _file(self, name="volcano-speech-key.txt"):
        return (self.home / "config" / "secrets" / name).read_text(encoding="utf-8")

    def test_legacy_pair_pastes_are_stored_as_two_labelled_lines(self):
        for pasted in ("123456789:" + TOKEN, "123456789\n" + TOKEN, "App ID: 123456789\nAccess Token: " + TOKEN):
            with self.subTest(pasted=pasted[:30]):
                status, obj = _put(self.port, "volcano-speech-key.txt", pasted)
                self.assertEqual(status, 200)
                self.assertTrue(obj["present"])
                self.assertIs(obj["legacy_pair"], True)
                self.assertNotIn(TOKEN, json.dumps(obj))
                self.assertEqual(self._file(), PAIR + "\n")   # 不是首行截断：token 半边活着

    def test_bare_new_console_key_is_stored_untouched_and_not_legacy(self):
        status, obj = _put(self.port, "volcano-speech-key.txt", "  sk-new-console-key-1234567890  \n")
        self.assertEqual(status, 200)
        self.assertIs(obj["legacy_pair"], False)
        self.assertEqual(self._file(), "sk-new-console-key-1234567890\n")

    def test_hard_wrapped_key_is_rejoined_not_truncated(self):
        status, obj = _put(self.port, "volcano-speech-key.txt", "abcdefgh\nijklmnop\n")
        self.assertEqual(status, 200)
        self.assertIs(obj["legacy_pair"], False)
        self.assertEqual(self._file(), "abcdefghijklmnop\n")

    def test_two_line_file_counts_as_present_on_get(self):
        _put(self.port, "volcano-speech-key.txt", "123456789:" + TOKEN)
        _s, obj = get_json(self.port, "/api/secrets")
        row = next(s for s in obj["secrets"] if s["name"] == "volcano-speech-key.txt")
        self.assertTrue(row["present"])
        self.assertNotIn("legacy_pair", row)          # 回执独有；GET 行不读内容
        self.assertNotIn(TOKEN, json.dumps(obj))

    def test_empty_value_still_deletes(self):
        _put(self.port, "volcano-speech-key.txt", "123456789:" + TOKEN)
        status, obj = _put(self.port, "volcano-speech-key.txt", "   \n")
        self.assertEqual(status, 200)
        self.assertFalse(obj["present"])
        self.assertIs(obj["legacy_pair"], False)
        self.assertFalse((self.home / "config" / "secrets" / "volcano-speech-key.txt").exists())

    def test_other_names_keep_the_first_line_rule_and_report_no_legacy_pair(self):
        for name in ("volcano-ark-key.txt", "anthropic-api-key.txt"):
            with self.subTest(name=name):
                status, obj = _put(self.port, name, "123456789\n" + TOKEN + "\n")
                self.assertEqual(status, 200)
                self.assertIs(obj["legacy_pair"], False)
                self.assertEqual(self._file(name), "123456789\n")


if __name__ == "__main__":
    unittest.main()
