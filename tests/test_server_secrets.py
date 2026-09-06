"""server/ 凭证面（CONTRACT §19 / §49 / §68）：值 write-only、探针注入、名单镜像。

- GET /api/secrets 只报状态，响应体里绝不出现凭证值；
- PUT /api/secrets/{name}：0600 文件、多行只留首行、空值删文件、未知名 404、四闸；
- POST /api/secrets/{name}/verify：prober 注入（ok / 拒绝 / 网络错），Slack 成功自动
  填 owner_slack_user_id，火山 key 无探针 400，未保存 400；
- 名单与 act/lib/secrets.py 常量 + shell ShellSupport.SecretsIO 的字幕文件名逐字一致。
"""
import io
import json
import os
import re
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first
from tests.test_server_common import (assert_envelope, auth_headers, get_json,
                                      http_request, post_json, start_server,
                                      write_text)

from act.lib import secrets as act_secrets
from server import secrets_store

REPO_ROOT = Path(__file__).resolve().parent.parent
_WIN = sys.platform.startswith("win")


def put_json(port, path, payload):
    body = json.dumps(payload).encode("utf-8")
    status, _h, data = http_request(port, "PUT", path, body=body, headers=auth_headers(port))
    return status, json.loads(data.decode("utf-8"))


class _ServerCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-secrets-")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        (self.home / "state").mkdir(parents=True)
        _httpd, self.port = start_server(self, self.home)

    def secret_path(self, name):
        return self.home / "config" / "secrets" / name


class SecretsStatusTestCase(_ServerCase):
    def test_status_never_echoes_values(self):
        write_text(self.secret_path("slack-user-token.txt"), "xoxp-SUPERSECRET\n")
        status, obj = get_json(self.port, "/api/secrets")
        self.assertEqual(status, 200)
        self.assertNotIn("SUPERSECRET", json.dumps(obj))
        by_name = {s["name"]: s for s in obj["secrets"]}
        self.assertTrue(by_name["slack-user-token.txt"]["present"])
        self.assertFalse(by_name["anthropic-api-key.txt"]["present"])
        self.assertFalse(by_name["volcano-ark-key.txt"]["verifiable"])
        self.assertTrue(by_name["gmail-app-password.txt"]["verifiable"])

    def test_whitespace_only_file_counts_as_absent(self):
        write_text(self.secret_path("anthropic-api-key.txt"), "\n  \n")
        _s, obj = get_json(self.port, "/api/secrets")
        anth = next(s for s in obj["secrets"] if s["name"] == "anthropic-api-key.txt")
        self.assertFalse(anth["present"])

    def test_legacy_flag_reports_the_older_tiers_without_reading_them_out(self):
        """add-only ``legacy``（原生「使用旧路径」）：secrets 缺席 + §19 第二 / 三层文件非空 → True；
        secrets 在 → False（第一层赢）；火山两把无旧路径恒 False；值仍不回显。"""
        fake_home = Path(self.tmp.name) / "user"
        write_text(fake_home / ".config" / "anthropic-key.txt", "sk-ant-LEGACYVALUE\n")
        write_text(fake_home / "Desktop" / "Keys" / "gmail-app-password.txt", "   \n")   # 空白 = 缺席
        write_text(fake_home / "vault" / "slack.txt", "xoxp-EXPLICIT\n")
        write_text(self.home / "config.yaml", "sources:\n  slack_token_path: %s\n" % (fake_home / "vault" / "slack.txt"))
        with mock.patch.dict(os.environ, {"HOME": str(fake_home)}):
            _s, obj = get_json(self.port, "/api/secrets")
            by_name = {s["name"]: s for s in obj["secrets"]}
            self.assertEqual({n: s["legacy"] for n, s in by_name.items()},
                             {"anthropic-api-key.txt": True, "slack-user-token.txt": True,
                              "gmail-app-password.txt": False, "volcano-speech-key.txt": False,
                              "volcano-ark-key.txt": False})
            self.assertNotIn("LEGACYVALUE", json.dumps(obj))
            self.assertNotIn("EXPLICIT", json.dumps(obj))
            write_text(self.secret_path("anthropic-api-key.txt"), "sk-ant-new\n")
            _s, obj = get_json(self.port, "/api/secrets")
            anth = next(s for s in obj["secrets"] if s["name"] == "anthropic-api-key.txt")
            self.assertEqual((anth["present"], anth["legacy"]), (True, False))


class SecretsPutTestCase(_ServerCase):
    def test_put_writes_first_line_with_0600(self):
        status, obj = put_json(self.port, "/api/secrets/anthropic-api-key.txt",
                               {"value": "  sk-ant-abc123  \nsecond line pasted by mistake\n"})
        self.assertEqual(status, 200)
        self.assertTrue(obj["present"])
        self.assertNotIn("sk-ant", json.dumps(obj))
        p = self.secret_path("anthropic-api-key.txt")
        self.assertEqual(p.read_text(encoding="utf-8"), "sk-ant-abc123\n")
        if not _WIN:
            self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(p.parent.stat().st_mode), 0o700)

    def test_put_empty_value_deletes_the_file(self):
        write_text(self.secret_path("slack-user-token.txt"), "xoxp-1\n")
        status, obj = put_json(self.port, "/api/secrets/slack-user-token.txt", {"value": ""})
        self.assertEqual(status, 200)
        self.assertFalse(obj["present"])
        self.assertFalse(self.secret_path("slack-user-token.txt").exists())

    def test_put_requires_write_gates(self):
        body = json.dumps({"value": "x"}).encode("utf-8")
        status, _h, _d = http_request(self.port, "PUT", "/api/secrets/anthropic-api-key.txt",
                                      body=body, headers={"Content-Type": "application/json"})
        self.assertEqual(status, 401)
        self.assertFalse(self.secret_path("anthropic-api-key.txt").exists())

    def test_unknown_name_is_404_and_writes_nothing(self):
        status, obj = put_json(self.port, "/api/secrets/passwd", {"value": "x"})
        self.assertEqual(status, 404)
        assert_envelope(self, obj, "NOT_FOUND")
        self.assertFalse((self.home / "config").exists())

    def test_bad_payloads_are_400(self):
        for payload in ({"value": 1}, {"token": "x"}, {"value": "x" * 5000}):
            with self.subTest(payload=str(payload)[:30]):
                status, obj = put_json(self.port, "/api/secrets/anthropic-api-key.txt", payload)
                self.assertEqual(status, 400)
                self.assertIn(obj["error"]["code"], ("INVALID_FIELD", "UNKNOWN_FIELD"))


class SecretsVerifyTestCase(_ServerCase):
    def _patch_prober(self, fn):
        patcher = mock.patch.object(secrets_store, "default_prober", fn)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_verify_ok_reports_detail_without_the_token(self):
        write_text(self.secret_path("anthropic-api-key.txt"), "sk-ant-zzz\n")
        seen = {}

        def prober(kind, token, ctx):
            seen.update(kind=kind, token=token)
            return True, "key accepted", {}
        self._patch_prober(prober)
        status, obj = post_json(self.port, "/api/secrets/anthropic-api-key.txt/verify", {})
        self.assertEqual(status, 200)
        self.assertEqual(seen, {"kind": "anthropic", "token": "sk-ant-zzz"})
        self.assertEqual(obj, {"ok": True, "network": False, "detail": "key accepted", "extra": {}})

    def test_slack_success_autofills_owner_slack_user_id(self):
        write_text(self.secret_path("slack-user-token.txt"), "xoxp-1\n")
        write_text(self.home / "state" / "settings_overrides.json", json.dumps({"language": "en"}))
        self._patch_prober(lambda kind, token, ctx: (True, "auth.test ok", {"user_id": "U0ZELIN", "user": "zelin"}))
        _s, obj = post_json(self.port, "/api/secrets/slack-user-token.txt/verify", {})
        self.assertTrue(obj["ok"])
        self.assertEqual(obj["extra"]["user_id"], "U0ZELIN")
        overrides = json.loads((self.home / "state" / "settings_overrides.json").read_text())
        self.assertEqual(overrides, {"language": "en", "owner_slack_user_id": "U0ZELIN"})

    def test_gmail_probe_gets_the_effective_address(self):
        write_text(self.secret_path("gmail-app-password.txt"), "abcd efgh\n")
        write_text(self.home / "config.yaml", "sources:\n  gmail:\n    address: me@gmail.com\n")
        seen = {}

        def prober(kind, token, ctx):
            seen.update(ctx)
            return False, "IMAP LOGIN rejected", {}
        self._patch_prober(prober)
        _s, obj = post_json(self.port, "/api/secrets/gmail-app-password.txt/verify", {})
        self.assertEqual(seen, {"address": "me@gmail.com"})
        self.assertFalse(obj["ok"])
        self.assertFalse(obj["network"])

    def test_network_failure_is_reported_as_network_not_credential(self):
        write_text(self.secret_path("anthropic-api-key.txt"), "sk-ant-zzz\n")

        def prober(kind, token, ctx):
            raise secrets_store.ProbeNetworkError("dns down")
        self._patch_prober(prober)
        status, obj = post_json(self.port, "/api/secrets/anthropic-api-key.txt/verify", {})
        self.assertEqual(status, 200)
        self.assertFalse(obj["ok"])
        self.assertTrue(obj["network"])
        self.assertIn("dns down", obj["detail"])

    def test_verify_without_saved_value_is_400(self):
        status, obj = post_json(self.port, "/api/secrets/anthropic-api-key.txt/verify", {})
        self.assertEqual(status, 400)
        assert_envelope(self, obj, "INVALID_FIELD")

    def test_unverifiable_secret_is_400(self):
        write_text(self.secret_path("volcano-ark-key.txt"), "k\n")
        status, obj = post_json(self.port, "/api/secrets/volcano-ark-key.txt/verify", {})
        self.assertEqual(status, 400)
        assert_envelope(self, obj, "INVALID_FIELD")

    def test_verify_with_body_fields_is_400_unknown_field(self):
        status, obj = post_json(self.port, "/api/secrets/anthropic-api-key.txt/verify", {"x": 1})
        self.assertEqual(status, 400)
        assert_envelope(self, obj, "UNKNOWN_FIELD")

    def test_verify_by_value_probes_the_pasted_token_without_saving(self):
        # §68.3 粘贴即验证（原生 SetupWizard verify-on-paste）：只探 body 里的值、不落盘
        seen = {}

        def prober(kind, token, ctx):
            seen.update(kind=kind, token=token)
            return True, "key accepted", {}
        self._patch_prober(prober)
        status, obj = post_json(self.port, "/api/secrets/anthropic-api-key.txt/verify",
                                {"value": "  sk-ant-pasted\n"})
        self.assertEqual(status, 200)
        self.assertTrue(obj["ok"])
        self.assertEqual(seen, {"kind": "anthropic", "token": "sk-ant-pasted"})
        self.assertFalse(self.secret_path("anthropic-api-key.txt").exists())

    def test_verify_by_value_never_autofills_slack_owner(self):
        # 还没落盘的 token 探成功也不动 override（落盘后再验才写 owner_slack_user_id）
        self._patch_prober(lambda kind, token, ctx: (True, "auth.test ok", {"user_id": "U0PASTE"}))
        status, obj = post_json(self.port, "/api/secrets/slack-user-token.txt/verify", {"value": "xoxp-2"})
        self.assertEqual(status, 200)
        self.assertEqual(obj["extra"]["user_id"], "U0PASTE")
        self.assertFalse((self.home / "state" / "settings_overrides.json").exists())

    def test_verify_by_value_rejects_bad_shapes(self):
        for payload in ({"value": 1}, {"value": ""}, {"value": "   \n"}, {"value": "x" * 5000}):
            with self.subTest(payload=str(payload)[:30]):
                status, obj = post_json(self.port, "/api/secrets/anthropic-api-key.txt/verify", payload)
                self.assertEqual(status, 400)
                assert_envelope(self, obj, "INVALID_FIELD")

    def test_secrets_prefix_without_verify_is_404(self):
        status, obj = post_json(self.port, "/api/secrets/anthropic-api-key.txt", {})
        self.assertEqual(status, 404)
        assert_envelope(self, obj, "NOT_FOUND")

    def test_verify_unknown_name_is_404(self):
        status, _obj = post_json(self.port, "/api/secrets/nope.txt/verify", {})
        self.assertEqual(status, 404)


class NameMirrorTestCase(unittest.TestCase):
    def test_server_names_mirror_act_secrets_and_shell_support(self):
        names = {s["name"] for s in secrets_store.SECRETS}
        self.assertIn(act_secrets.SLACK_TOKEN_FILE, names)
        self.assertIn(act_secrets.GMAIL_APP_PASSWORD_FILE, names)
        self.assertIn(act_secrets.ANTHROPIC_API_KEY_FILE, names)
        swift = (REPO_ROOT / "shell" / "Sources" / "ShellSupport.swift").read_text(encoding="utf-8")
        for const in ("volcanoSpeechFile", "volcanoArkFile"):
            m = re.search(r'static let %s = "([^"]+)"' % const, swift)
            self.assertIsNotNone(m, const)
            self.assertIn(m.group(1), names)

    def test_secrets_dir_layout_mirrors_act(self):
        from server import paths
        home = Path(os.environ["AIASSISTANT_HOME"])
        self.assertEqual(paths.secrets_dir(home), act_secrets.SECRETS_DIR)


if __name__ == "__main__":
    unittest.main()


class ProbeImplementationTestCase(unittest.TestCase):
    """默认探针的三个实现（§68.3）——全部 mock 网络层：urlopen / IMAP4_SSL 被替身替换，零出网。"""

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

    def _patch_urlopen(self, fn):
        patcher = mock.patch.object(secrets_store.urllib.request, "urlopen", fn)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_anthropic_200_ok_and_401_rejected(self):
        seen = {}

        def urlopen(req, timeout):
            seen["auth"] = req.get_header("X-api-key")
            return self._Resp(200, b"{}")
        self._patch_urlopen(urlopen)
        ok, detail, extra = secrets_store.default_prober("anthropic", "sk-ant-1", {})
        self.assertTrue(ok)
        self.assertEqual(seen["auth"], "sk-ant-1")
        self.assertEqual(extra, {})

        def rejected(req, timeout):
            raise secrets_store.urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, io.BytesIO(b"{}"))
        self._patch_urlopen(rejected)
        ok, detail, _extra = secrets_store.default_prober("anthropic", "bad", {})
        self.assertFalse(ok)
        self.assertIn("401", detail)

    def test_slack_ok_returns_identity_and_failure_returns_error_code(self):
        self._patch_urlopen(lambda req, timeout: self._Resp(200, b'{"ok": true, "user_id": "U1", "user": "z", "team": "t"}'))
        ok, detail, extra = secrets_store.default_prober("slack", "xoxp-1", {})
        self.assertTrue(ok)
        self.assertEqual(extra, {"user_id": "U1", "user": "z", "team": "t"})
        self._patch_urlopen(lambda req, timeout: self._Resp(200, b'{"ok": false, "error": "invalid_auth"}'))
        ok, detail, extra = secrets_store.default_prober("slack", "xoxp-1", {})
        self.assertFalse(ok)
        self.assertIn("invalid_auth", detail)
        self.assertEqual(extra, {})

    def test_network_layer_failures_become_probe_network_error(self):
        def down(req, timeout):
            raise secrets_store.urllib.error.URLError("dns")
        self._patch_urlopen(down)
        with self.assertRaises(secrets_store.ProbeNetworkError):
            secrets_store.default_prober("anthropic", "k", {})
        # 非 JSON 响应体不崩：没有 ok 字段 = 没有判决（原生 .failed("no response")），走网络 / 服务层那一支
        # （§68.3 2026-09-05 追记；三分判决的细则在 tests/test_server_secrets_verify_verdicts.py）
        self._patch_urlopen(lambda req, timeout: self._Resp(200, b"<html>"))
        with self.assertRaises(secrets_store.ProbeNetworkError):
            secrets_store.default_prober("slack", "k", {})

    def test_gmail_probe_paths(self):
        # 无地址：不出网，直接 ok:false 人话
        ok, detail, _extra = secrets_store.default_prober("gmail", "pw", {"address": ""})
        self.assertFalse(ok)
        self.assertIn("address", detail)

        class FakeImap:
            instances = []

            def __init__(self, host, port, timeout):
                self.calls = []
                FakeImap.instances.append(self)

            def login(self, user, pw):
                self.calls.append(("login", user))
                if pw == "wrong":
                    raise secrets_store.imaplib.IMAP4.error(b"[AUTHENTICATIONFAILED] Invalid credentials")

            def logout(self):
                self.calls.append(("logout",))
        patcher = mock.patch.object(secrets_store.imaplib, "IMAP4_SSL", FakeImap)
        patcher.start()
        self.addCleanup(patcher.stop)
        ok, detail, _extra = secrets_store.default_prober("gmail", "good", {"address": "me@gmail.com"})
        self.assertTrue(ok)
        self.assertIn("me@gmail.com", detail)
        self.assertEqual(FakeImap.instances[-1].calls, [("login", "me@gmail.com"), ("logout",)])
        ok, detail, _extra = secrets_store.default_prober("gmail", "wrong", {"address": "me@gmail.com"})
        self.assertFalse(ok)
        self.assertIn("AUTHENTICATIONFAILED", detail)

        def refused(host, port, timeout):
            raise OSError("connection refused")
        with mock.patch.object(secrets_store.imaplib, "IMAP4_SSL", refused):
            with self.assertRaises(secrets_store.ProbeNetworkError):
                secrets_store.default_prober("gmail", "pw", {"address": "me@gmail.com"})
