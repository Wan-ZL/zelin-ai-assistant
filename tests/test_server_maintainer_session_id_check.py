"""§68.7 追记：会话 id 在**保存时**就过原生 SettingsMaintainer.validateSessionID 的闸（parity 批 maintainer-rows，
gap settings-maintainer-session-id-validation）。

- 目录 ``maintainer_session_id`` 带 ``check: "session_id"``：``SESSION_ID_RE`` = ``^[A-Za-z0-9][A-Za-z0-9-]*$``（原生同款
  不设长度帽——字符句只说字符）——以 ``-`` 开头（``--dangerously-skip-permissions`` 全是白名单字符）→ reason ``leading_hyphen``
  与原生那句；其余不合白名单 → 原生的字符白名单句；两句 zh / en 逐字；
- PUT 不合格 → 400 INVALID_FIELD，message 双语并列，details ``{field, check[, reason]}``，overrides 不落；空串仍是清键；
- 投影 add-only ``check.reasons``（多句的 kind 才有；email 没有）；
- 启动（POST /api/maintainer/terminal）对 effective 的 id 再过同一道闸（原生 openSession）：config.yaml 里的坏 id 没经过
  PUT 也拦住，400 同一句、同一 details；合格的 id 照旧 ``--resume``。
"""
import json
import tempfile
import unittest
from pathlib import Path

from tests import TMP_HOME  # noqa: F401 - sandbox env first
from tests.test_server_common import assert_envelope, auth_headers, get_json, http_request, start_server, write_text

from server import maintainer_launch
from server import settings_catalog as catalog

HYPHEN = ("-abc", "--dangerously-skip-permissions", "-")
CHARSET = ("a b", "abc; rm -rf /", "abc_def", "会话", "abc\tdef")
# 原生 validateSessionID 不设长度帽：长的但全是白名单字符 = 合格（字符句不许被拿来说长度）
GOOD = ("6f9619ff-8b86-d011-b42d-00cf4fc964ff", "a", "A-1", "a" * 64, "a" * 65, "a-" * 100 + "z", "  padded-id  ")


def put_json(port, path, payload):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    status, _h, data = http_request(port, "PUT", path, body=body, headers=auth_headers(port))
    return status, json.loads(data.decode("utf-8"))


class SessionIdRuleTestCase(unittest.TestCase):
    def test_regex_and_reason_table(self):
        for raw in GOOD:
            self.assertIsNone(catalog.session_id_problem(raw), raw)
            self.assertIsNotNone(catalog.SESSION_ID_RE.match(raw.strip()), raw)
        for raw in HYPHEN:
            self.assertEqual(catalog.session_id_problem(raw), "leading_hyphen", raw)
        for raw in CHARSET:
            self.assertEqual(catalog.session_id_problem(raw), "charset", raw)
            self.assertIsNone(catalog.SESSION_ID_RE.match(raw), raw)

    def test_sentences_are_the_native_ones_verbatim(self):
        # 原生 SettingsMaintainer.swift:137-149 validateSessionID 的两句（server-owned，web 只取键）
        self.assertEqual(catalog.CHECK_REASONS["session_id"]["leading_hyphen"], {
            "zh": "会话 ID 不能以连字符（-）开头——那是命令行选项的形状，不是会话 ID。",
            "en": "A session id may not start with a hyphen (-) — that's the shape of a command-line flag, not a session id."})
        self.assertEqual(catalog.CHECKS["session_id"], {
            "zh": "会话 ID 只能包含字母、数字和连字符（-）——从 claude 里复制的会话 ID 就是这个样子。",
            "en": "A session id may only contain letters, digits, and hyphens (-) — the id you copy from claude is exactly that shape."})

    def test_check_projection_carries_reasons_only_for_multi_sentence_kinds(self):
        self.assertEqual(catalog.check_projection("session_id"),
                         {"kind": "session_id", "message": catalog.CHECKS["session_id"],
                          "reasons": catalog.CHECK_REASONS["session_id"]})
        self.assertEqual(catalog.check_projection("email"), {"kind": "email", "message": catalog.CHECKS["email"]})

    def test_run_check_is_the_public_gate_with_reason_in_details(self):
        field = catalog.field_index(catalog.lookup("maintainer"))["maintainer_session_id"]
        catalog.run_check(field, None, "maintainer_session_id")        # 空 = 清键，不查
        catalog.run_check(field, "abc-123", "maintainer_session_id")
        with self.assertRaises(catalog.InvalidFieldError) as ctx:
            catalog.run_check(field, "-abc", "maintainer_session_id")
        self.assertEqual(ctx.exception.details, {"field": "maintainer_session_id", "check": "session_id", "reason": "leading_hyphen"})
        with self.assertRaises(catalog.InvalidFieldError) as ctx:
            catalog.run_check(field, "a b", "maintainer_session_id")
        self.assertEqual(ctx.exception.details, {"field": "maintainer_session_id", "check": "session_id"})


class SessionIdSaveTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-maint-sid-")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        (self.home / "state").mkdir(parents=True)
        _httpd, self.port = start_server(self, self.home)

    @property
    def overrides_path(self) -> Path:
        return self.home / "state" / "settings_overrides.json"

    def _field(self, obj, key):
        return next(f for f in obj["fields"] if f["key"] == key)

    def test_leading_hyphen_is_400_with_the_hyphen_sentence_and_reason(self):
        for raw in HYPHEN:
            with self.subTest(raw=raw):
                status, obj = put_json(self.port, "/api/settings/maintainer", {"maintainer_session_id": raw})
                self.assertEqual(status, 400)
                assert_envelope(self, obj, "INVALID_FIELD")
                self.assertEqual(obj["error"]["details"],
                                 {"field": "maintainer_session_id", "check": "session_id", "reason": "leading_hyphen"})
                sentence = catalog.CHECK_REASONS["session_id"]["leading_hyphen"]
                self.assertEqual(obj["error"]["message"], "%s / %s" % (sentence["zh"], sentence["en"]))
        self.assertFalse(self.overrides_path.exists())

    def test_stray_characters_are_400_with_the_charset_sentence(self):
        for raw in CHARSET:
            with self.subTest(raw=raw):
                status, obj = put_json(self.port, "/api/settings/maintainer", {"maintainer_session_id": raw})
                self.assertEqual(status, 400)
                self.assertEqual(obj["error"]["details"], {"field": "maintainer_session_id", "check": "session_id"})
                self.assertIn(catalog.CHECKS["session_id"]["zh"], obj["error"]["message"])
                self.assertIn(catalog.CHECKS["session_id"]["en"], obj["error"]["message"])
        self.assertFalse(self.overrides_path.exists())

    def test_bad_id_blocks_the_whole_put(self):
        status, _obj = put_json(self.port, "/api/settings/maintainer",
                                {"maintainer_repo_path": str(self.home), "maintainer_session_id": "-x"})
        self.assertEqual(status, 400)
        self.assertFalse(self.overrides_path.exists())

    def test_good_ids_save_trimmed_and_empty_clears(self):
        for raw in GOOD:
            with self.subTest(raw=raw):
                status, obj = put_json(self.port, "/api/settings/maintainer", {"maintainer_session_id": raw})
                self.assertEqual(status, 200)
                self.assertEqual(self._field(obj, "maintainer_session_id")["effective"], raw.strip())
        status, obj = put_json(self.port, "/api/settings/maintainer", {"maintainer_session_id": "  "})
        self.assertEqual(status, 200)
        self.assertEqual(self._field(obj, "maintainer_session_id")["source"], "default")
        self.assertEqual(json.loads(self.overrides_path.read_text(encoding="utf-8")), {})

    def test_catalog_projects_the_check_with_reasons(self):
        _s, section = get_json(self.port, "/api/settings/maintainer")
        self.assertEqual(self._field(section, "maintainer_session_id")["check"], catalog.check_projection("session_id"))
        self.assertNotIn("check", self._field(section, "maintainer_repo_path"))
        _s, gmail = get_json(self.port, "/api/settings/gmail")
        self.assertNotIn("reasons", self._field(gmail, "gmail_address")["check"])


class SessionIdLaunchRecheckTestCase(unittest.TestCase):
    """POST /api/maintainer/terminal：effective 的 id（可能来自 config.yaml）启动前再过同一道闸（原生 openSession）。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-maint-launch-")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        (self.home / "state").mkdir(parents=True)

    def _launch(self):
        return maintainer_launch.launch(self.home, {}, opener=lambda p: None, out_dir=Path(self.tmp.name), platform="darwin")

    def test_config_yaml_leading_hyphen_id_is_refused_with_the_same_sentence(self):
        write_text(self.home / "config.yaml", "maintainer:\n  session_id: --dangerously-skip-permissions\n")
        with self.assertRaises(maintainer_launch.InvalidFieldError) as ctx:
            self._launch()
        self.assertEqual(ctx.exception.details,
                         {"field": "maintainer_session_id", "check": "session_id", "reason": "leading_hyphen"})
        sentence = catalog.CHECK_REASONS["session_id"]["leading_hyphen"]
        self.assertEqual(ctx.exception.message, "%s / %s" % (sentence["zh"], sentence["en"]))

    def test_override_with_stray_characters_is_refused_with_the_charset_sentence(self):
        write_text(self.home / "state" / "settings_overrides.json", json.dumps({"maintainer_session_id": "bad id; rm -rf"}))
        with self.assertRaises(maintainer_launch.InvalidFieldError) as ctx:
            self._launch()
        self.assertEqual(ctx.exception.details, {"field": "maintainer_session_id", "check": "session_id"})
        self.assertIn(catalog.CHECKS["session_id"]["en"], ctx.exception.message)

    def test_good_id_rides_on_resume_and_empty_starts_fresh(self):
        write_text(self.home / "config.yaml", "maintainer:\n  session_id: 6f9619ff-8b86-d011-b42d-00cf4fc964ff\n")
        receipt = self._launch()
        self.assertTrue(receipt["command"].endswith("&& claude --resume 6f9619ff-8b86-d011-b42d-00cf4fc964ff"))
        write_text(self.home / "config.yaml", "maintainer: {}\n")
        self.assertTrue(self._launch()["command"].endswith("&& claude"))


if __name__ == "__main__":
    unittest.main()
