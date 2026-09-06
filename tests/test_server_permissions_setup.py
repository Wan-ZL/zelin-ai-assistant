"""server/ 权限体检 + 首次运行向导 + 关于/更新（CONTRACT §15 / §26 / §55 / §68.3 / §68.5 / §68.6）。

- /api/permissions：FDA 可执行清单（runtime.json 解释器、claude_bin、node、壳 app）、
  受保护位置判定、TCC 相关 doctor 行的过滤；
- /api/setup：needed 判定、config-from-example（不覆盖 409）、complete / reset 标记；
- /api/about + POST /api/update/check：版本非空、update_check 子集、子进程注入。
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first
from tests.test_server_common import (assert_envelope, get_json, post_json,
                                      start_server, write_text)

from server import doctor_run, permissions, subproc

_WIN = sys.platform.startswith("win")

DOCTOR_JSON = json.dumps({"home": "/x", "checks": [
    {"name": "claude CLI", "status": "OK", "detail": "", "fix": "", "failure_id": "", "action_id": ""},
    {"name": "launchd volume access", "status": "FAIL", "detail": "EPERM", "fix": "grant FDA",
     "failure_id": "deploy_blind_tcc", "action_id": ""},
    {"name": "cron write access", "status": "WARN", "detail": "", "fix": "", "failure_id": "cron_tcc_blocked",
     "action_id": ""},
    {"name": "board ui build", "status": "WARN", "detail": "", "fix": "", "failure_id": "", "action_id": ""},
]})


class _ServerCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-perm-")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        (self.home / "state").mkdir(parents=True)
        self.user_home = Path(self.tmp.name) / "user"
        self.user_home.mkdir()
        env = mock.patch.dict(os.environ, {"HOME": str(self.user_home), "USERPROFILE": str(self.user_home)})
        env.start()
        self.addCleanup(env.stop)
        doctor_run.reset_cache_for_tests()
        self.addCleanup(doctor_run.reset_cache_for_tests)
        self.calls = []
        _httpd, self.port = start_server(self, self.home)

    def patch_runner(self, rc=0, out=DOCTOR_JSON, err=""):
        def runner(argv, env, cwd, timeout_s):
            self.calls.append(argv)
            return rc, out, err
        patcher = mock.patch.object(subproc, "default_runner", runner)
        patcher.start()
        self.addCleanup(patcher.stop)


class PermissionsTestCase(_ServerCase):
    @unittest.skipIf(_WIN, "POSIX paths / realpath semantics")
    def test_snapshot_lists_executables_with_copyable_paths(self):
        self.patch_runner()
        py = self.home / "venv" / "bin" / "python3"
        write_text(py, "#!/bin/sh\n")
        write_text(self.home / "config" / "runtime.json", json.dumps({"python": str(py)}))
        write_text(self.home / "config.yaml", "execution:\n  claude_bin: %s\n" % (self.home / "bin" / "claude"))
        status, obj = get_json(self.port, "/api/permissions")
        self.assertEqual(status, 200)
        roles = {e["role"]: e for e in obj["fda"]["executables"]}
        self.assertEqual(roles["daemon_python"]["path"], str(py))
        self.assertTrue(roles["daemon_python"]["exists"])
        self.assertEqual(roles["claude"]["path"], str(self.home / "bin" / "claude"))
        self.assertFalse(roles["claude"]["exists"])
        self.assertEqual(roles["shell_app"]["path"], permissions.SHELL_APP_PATH)
        self.assertIn("Privacy_AllFiles", obj["fda"]["pane"])
        # files_folders = 笔记库（Documents）授权被拒后的第二次机会（原生 requestVaultAccess 深链；§68.13）
        self.assertEqual(set(obj["panes"]), {"full_disk", "screen", "microphone", "notifications", "files_folders"})

    def test_vault_status_is_passive_and_root_follows_obsidian_raw(self):
        # 原生 PermissionsModel 的被动探针：只认 state/vault_sync_mode == mirror，永不读 ~/Documents
        self.patch_runner()
        _s, obj = get_json(self.port, "/api/permissions")
        self.assertEqual(obj["vault"]["status"], "unknown")
        self.assertTrue(obj["vault"]["root"].endswith(os.path.join("Documents", "Obsidian Vault")))
        write_text(self.home / "state" / "vault_sync_mode", "mirror\n")
        write_text(self.home / "state" / "settings_overrides.json",
                   json.dumps({"obsidian_raw": str(self.user_home / "Notes" / "2 - raw")}))
        _s, obj = get_json(self.port, "/api/permissions")
        self.assertEqual(obj["vault"]["status"], "granted")
        self.assertEqual(obj["vault"]["root"], str(self.user_home / "Notes"))

    def test_cron_disk_access_row_is_forwarded(self):
        # 向导末步「定时任务磁盘权限」读这一行（OK / WARN 无探针 / FAIL cron_fda_blocked 全要）
        rows = json.loads(DOCTOR_JSON)
        rows["checks"].append({"name": "cron disk access", "status": "OK", "detail": "cron read ok", "fix": "",
                               "failure_id": "", "action_id": ""})
        self.patch_runner(out=json.dumps(rows))
        _s, obj = get_json(self.port, "/api/permissions")
        self.assertIn("cron disk access", [r["name"] for r in obj["doctor"]])

    def test_only_tcc_shaped_doctor_rows_are_forwarded(self):
        self.patch_runner()
        _s, obj = get_json(self.port, "/api/permissions")
        self.assertEqual([r["name"] for r in obj["doctor"]],
                         ["launchd volume access", "cron write access", "board ui build"])
        self.assertTrue(obj["doctor_ok"])
        self.assertEqual(self.calls[0][1:], ["-m", "act.doctor", "--json", "--fast"])

    def test_missing_runtime_json_and_claude_are_honest_nulls(self):
        self.patch_runner()
        with mock.patch("server.permissions.shutil.which", return_value=None):
            _s, obj = get_json(self.port, "/api/permissions")
        roles = {e["role"]: e for e in obj["fda"]["executables"]}
        self.assertIsNone(roles["daemon_python"]["path"])
        self.assertFalse(roles["daemon_python"]["exists"])
        self.assertIsNone(roles["claude"]["path"])
        self.assertIsNone(roles["node"]["path"])

    def test_stable_daemon_copy_wins_over_the_versioned_claude(self):
        # §55 第五幕: the FDA subject is install.sh's stable copy when it exists;
        # the note stops telling the owner to redo the grant after every update
        self.patch_runner()
        stable = self.home / "stable-bin" / "claude"
        write_text(stable, "#!/bin/sh\n")
        with mock.patch.dict(os.environ, {"AIASSISTANT_STABLE_CLAUDE": str(stable)}):
            _s, obj = get_json(self.port, "/api/permissions")
        roles = {e["role"]: e for e in obj["fda"]["executables"]}
        self.assertEqual(roles["claude"]["path"], str(stable))
        self.assertTrue(roles["claude"]["exists"])
        self.assertIn("survives", roles["claude"]["note"]["en"])
        self.assertNotIn("redo after every", roles["claude"]["note"]["en"])

    def test_stable_claude_path_mirrors_the_pipeline(self):
        # server never imports act (§49): the §55 第五幕 path is hand-mirrored and pinned here
        from act.lib import config as act_config
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AIASSISTANT_STABLE_CLAUDE", None)
            self.assertEqual(permissions.stable_claude_bin().relative_to(Path.home()),
                             act_config.STABLE_CLAUDE_BIN.relative_to(act_config.STABLE_CLAUDE_BIN.parents[4]))

    @unittest.skipIf(_WIN, "POSIX path prefixes")
    def test_protected_location_rules(self):
        self.assertTrue(permissions.protected_location(Path("/Volumes/Storage/x")))
        self.assertTrue(permissions.protected_location(self.user_home / "Documents" / "repo"))
        self.assertTrue(permissions.protected_location(self.user_home / "Desktop"))
        self.assertFalse(permissions.protected_location(self.user_home / "Projects" / "repo"))
        self.assertFalse(permissions.protected_location(Path("/opt/zai")))


class SetupTestCase(_ServerCase):
    def test_fresh_home_needs_setup(self):
        status, obj = get_json(self.port, "/api/setup")
        self.assertEqual(status, 200)
        self.assertTrue(obj["needed"])
        self.assertFalse(obj["done"])
        self.assertFalse(obj["config_exists"])
        self.assertEqual(set(obj["secrets"]),
                         {"anthropic-api-key.txt", "slack-user-token.txt", "gmail-app-password.txt"})

    def test_config_plus_one_secret_means_not_needed(self):
        write_text(self.home / "config.yaml", "owner:\n  name: x\n")
        write_text(self.home / "config" / "secrets" / "slack-user-token.txt", "xoxp-1\n")
        _s, obj = get_json(self.port, "/api/setup")
        self.assertFalse(obj["needed"])

    def test_config_from_example_copies_once_and_refuses_overwrite(self):
        write_text(self.home / "config.example.yaml", "owner:\n  name: Your Name\n")
        status, obj = post_json(self.port, "/api/setup/config-from-example", {})
        self.assertEqual(status, 200)
        self.assertTrue(obj["setup"]["config_exists"])
        self.assertEqual((self.home / "config.yaml").read_text(encoding="utf-8"), "owner:\n  name: Your Name\n")
        write_text(self.home / "config.yaml", "owner:\n  name: edited\n")
        status, obj = post_json(self.port, "/api/setup/config-from-example", {})
        self.assertEqual(status, 409)
        assert_envelope(self, obj, "CONFLICT")
        self.assertEqual((self.home / "config.yaml").read_text(encoding="utf-8"), "owner:\n  name: edited\n")

    def test_config_from_example_without_template_is_404(self):
        status, obj = post_json(self.port, "/api/setup/config-from-example", {})
        self.assertEqual(status, 404)
        assert_envelope(self, obj, "NOT_FOUND")

    def test_complete_writes_marker_and_reset_removes_it(self):
        status, obj = post_json(self.port, "/api/setup/complete", {})
        self.assertEqual(status, 200)
        self.assertTrue(obj["setup"]["done"])
        self.assertFalse(obj["setup"]["needed"])
        marker = json.loads((self.home / "state" / "setup_done.json").read_text(encoding="utf-8"))
        self.assertIn("completed_at", marker)
        status, obj = post_json(self.port, "/api/setup/reset", {})
        self.assertEqual(status, 200)
        self.assertFalse(obj["setup"]["done"])
        self.assertTrue(obj["setup"]["needed"])

    def test_unknown_fields_are_400(self):
        for path in ("/api/setup/complete", "/api/setup/reset", "/api/setup/config-from-example",
                     "/api/setup/seed-dashboard"):
            with self.subTest(path=path):
                status, obj = post_json(self.port, path, {"x": 1})
                self.assertEqual(status, 400)
                assert_envelope(self, obj, "UNKNOWN_FIELD")

    def test_seed_dashboard_runs_the_dashboard_module_once(self):
        self.patch_runner(out="{}\n")
        status, obj = post_json(self.port, "/api/setup/seed-dashboard", {})
        self.assertEqual(status, 200)
        self.assertEqual(obj, {"ok": True, "rc": 0})
        self.assertEqual(self.calls[0][1:], ["-m", "act.lib.dashboard"])

    def test_seed_dashboard_failure_is_honest_not_500(self):
        self.patch_runner(rc=1, out="", err="Traceback: boom")
        status, obj = post_json(self.port, "/api/setup/seed-dashboard", {})
        self.assertEqual(status, 200)
        self.assertFalse(obj["ok"])
        self.assertEqual(obj["rc"], 1)
        self.assertIn("boom", obj["error"])


class EngineDetectTestCase(_ServerCase):
    """``GET /api/setup/engine``（原生 EngineDetector 的 server 半边）：CLI + 认证梯子，零 subprocess。"""

    def _engine(self, calls_ok=(), platform="darwin", env=None):
        from server import setup as setup_mod
        seen = []

        def runner(argv, timeout_s):
            seen.append(argv)
            if argv[-1] == "--version":
                return 0, "1.0.99 (Claude Code)\nextra line\n"
            return (0, "") if any(k in " ".join(argv) for k in calls_ok) else (44, "not found")
        snap = setup_mod.engine_snapshot(self.home, runner=runner, platform=platform, env=env or {})
        return snap, seen

    def test_nothing_installed(self):
        with mock.patch("server.permissions.shutil.which", return_value=None):
            snap, seen = self._engine()
        self.assertEqual(snap["cli_path"], None)
        self.assertIsNone(snap["version"])
        self.assertIsNone(snap["auth"])
        self.assertFalse(snap["ready"])
        self.assertEqual(set(snap["auth_sources"]), {"oauth", "env_key", "secrets_file", "legacy_file"})
        self.assertFalse(any(argv[-1] == "--version" for argv in seen))

    def test_cli_with_keychain_login_is_ready_via_oauth(self):
        cli = self.home / "bin" / "claude"
        write_text(cli, "#!/bin/sh\n")
        write_text(self.home / "config.yaml", "execution:\n  claude_bin: %s\n" % cli)
        snap, seen = self._engine(calls_ok=("find-generic-password",))
        self.assertEqual(snap["cli_path"], str(cli))
        self.assertEqual(snap["version"], "1.0.99 (Claude Code)")
        self.assertEqual(snap["auth"], "oauth")
        self.assertTrue(snap["ready"])
        # 钥匙串探针只问存在：永不带 -w
        for argv in seen:
            self.assertNotIn("-w", argv)

    def test_auth_ladder_order_and_sources(self):
        cli = self.home / "bin" / "claude"
        write_text(cli, "#!/bin/sh\n")
        write_text(self.home / "config.yaml", "execution:\n  claude_bin: %s\n" % cli)
        write_text(self.home / "config" / "secrets" / "anthropic-api-key.txt", "sk-ant-1\n")
        write_text(self.user_home / ".config" / "anthropic-key.txt", "sk-legacy\n")
        snap, _seen = self._engine(platform="linux", env={"ANTHROPIC_API_KEY": "sk-env"})
        self.assertEqual(snap["auth_sources"],
                         {"oauth": False, "env_key": True, "secrets_file": True, "legacy_file": True})
        self.assertEqual(snap["auth"], "env_key")   # 梯子顺序：oauth → env_key → secrets_file → legacy_file
        snap, _seen = self._engine(platform="linux", env={})
        self.assertEqual(snap["auth"], "secrets_file")
        (self.home / "config" / "secrets" / "anthropic-api-key.txt").unlink()
        snap, _seen = self._engine(platform="linux", env={})
        self.assertEqual(snap["auth"], "legacy_file")

    def test_credentials_json_counts_as_oauth_without_keychain(self):
        write_text(self.user_home / ".claude" / ".credentials.json", '{"claudeAiOauth": {}}')
        with mock.patch("server.permissions.shutil.which", return_value=None):
            snap, _seen = self._engine(platform="linux")
        self.assertTrue(snap["auth_sources"]["oauth"])
        self.assertFalse(snap["ready"])   # 有登录没 CLI 仍不算就绪

    def test_route_serves_the_snapshot(self):
        with mock.patch("server.setup.engine_snapshot", return_value={"cli_path": None, "version": None, "auth": None,
                                                                     "auth_sources": {}, "ready": False}):
            status, obj = get_json(self.port, "/api/setup/engine")
        self.assertEqual(status, 200)
        self.assertFalse(obj["ready"])


class AboutTestCase(_ServerCase):
    def test_about_reports_version_and_update_fields(self):
        write_text(self.home / "state" / "dashboard.json", json.dumps({
            "generated_at": "x", "counts": {},
            "update_available": {"current": "0.1", "latest": "0.2", "url": "https://r", "pkg_asset_url": None}}))
        write_text(self.home / "state" / "update_check.json", json.dumps({
            "checked_at": "2026-09-02T00:00:00Z", "etag": "W/secret", "latest": "0.2", "url": "https://r"}))
        status, obj = get_json(self.port, "/api/about")
        self.assertEqual(status, 200)
        self.assertTrue(obj["version"] and obj["version"] != "unknown")
        self.assertEqual(obj["home"], str(self.home))
        self.assertEqual(obj["update_available"]["latest"], "0.2")
        self.assertEqual(obj["update_check"]["checked_at"], "2026-09-02T00:00:00Z")
        self.assertNotIn("etag", obj["update_check"])

    def test_about_without_files(self):
        _s, obj = get_json(self.port, "/api/about")
        self.assertIsNone(obj["update_available"])
        self.assertIsNone(obj["update_check"])

    def test_update_check_runs_cli_force_and_returns_its_json(self):
        line = json.dumps({"ok": True, "enabled": True, "current": "0.1", "latest": "0.1", "update_available": False})
        self.patch_runner(out=line + "\n")
        status, obj = post_json(self.port, "/api/update/check", {})
        self.assertEqual(status, 200)
        self.assertEqual(obj["update_available"], False)
        self.assertEqual(self.calls[0][1:], ["-m", "act.lib.update_check", "--force"])

    def test_update_check_failure_is_honest(self):
        self.patch_runner(rc=1, out="", err="no network")
        _s, obj = post_json(self.port, "/api/update/check", {})
        self.assertFalse(obj["ok"])
        self.assertIn("no network", obj["error"])

    def test_update_check_rejects_fields(self):
        status, obj = post_json(self.port, "/api/update/check", {"force": True})
        self.assertEqual(status, 400)
        assert_envelope(self, obj, "UNKNOWN_FIELD")


if __name__ == "__main__":
    unittest.main()
