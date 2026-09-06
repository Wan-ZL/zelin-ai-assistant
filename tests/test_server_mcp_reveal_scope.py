"""MCP 区每个作用域的「在 Finder 显示」+ 路径的 ``~`` 缩写（CONTRACT §68.9 追记；原生 SettingsMCP.swift
reveal(scope) / abbrevHome）。

钉住：``POST /api/reveal {target:"mcp_user"|"mcp_project"}`` → ``open -R`` 该作用域的配置文件——路径由
server/mcp_servers.scope_paths 单点计算（客户端只点名词表项、绝不传路径）；文件不在 → 404 且不 spawn；
``GET /api/mcp`` 的每个作用域另带 add-only ``path_display``（``$HOME`` → ``~``，不在 HOME 下的原样）。
``open`` 用注入缝 mock——测试绝不真弹访达。
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first
from tests.test_server_common import assert_envelope, get_json, post_json, start_server, write_text

from server import files as files_mod
from server import mcp_servers


class McpRevealScopeTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-mcp-reveal-")
        self.addCleanup(self.tmp.cleanup)
        self.user_home = Path(self.tmp.name) / "user"
        self.user_home.mkdir()
        # 项目级 home 放在 user home 之下：project 行的 path_display 也该缩成 ~（原生 abbrevHome 对两个作用域一视同仁）
        self.home = self.user_home / "zai"
        (self.home / "state").mkdir(parents=True)
        env = mock.patch.dict(os.environ, {"HOME": str(self.user_home), "USERPROFILE": str(self.user_home)})
        env.start()
        self.addCleanup(env.stop)
        _httpd, self.port = start_server(self, self.home)

    def _reveal(self, payload):
        with mock.patch.object(files_mod.sys, "platform", "darwin"), mock.patch.object(files_mod.subprocess, "run") as run:
            status, obj = post_json(self.port, "/api/reveal", payload)
        return status, obj, run

    def test_targets_are_in_the_vocabulary_and_resolve_the_same_paths_the_list_uses(self):
        self.assertIn("mcp_user", files_mod.REVEAL_TARGETS)
        self.assertIn("mcp_project", files_mod.REVEAL_TARGETS)
        paths = mcp_servers.scope_paths(self.home)
        self.assertEqual(paths, {"user": self.user_home / ".claude.json", "project": self.home / ".mcp.json"})

    def test_reveal_each_scope_via_open_dash_r_once_the_file_exists(self):
        write_text(self.user_home / ".claude.json", json.dumps({"mcpServers": {"fs": {"command": "npx"}}}))
        write_text(self.home / ".mcp.json", json.dumps({"mcpServers": {}}))
        for target, path in (("mcp_user", self.user_home / ".claude.json"), ("mcp_project", self.home / ".mcp.json")):
            with self.subTest(target=target):
                status, obj, run = self._reveal({"target": target})
                self.assertEqual(status, 200, obj)
                self.assertEqual(obj, {"ok": True, "revealed": str(path)})
                self.assertEqual(run.call_args[0][0], ["open", "-R", str(path)])

    def test_missing_file_is_404_and_nothing_is_spawned(self):
        for target in ("mcp_user", "mcp_project"):
            with self.subTest(target=target):
                status, obj, run = self._reveal({"target": target})
                self.assertEqual(status, 404)
                assert_envelope(self, obj, "NOT_FOUND")
                self.assertEqual(obj["error"]["details"].get("target"), target)
                run.assert_not_called()

    def test_client_cannot_smuggle_a_path_or_a_name(self):
        write_text(self.user_home / ".claude.json", "{}")
        status, obj, run = self._reveal({"target": "mcp_user", "path": str(self.user_home / ".claude.json")})
        self.assertEqual(status, 400)
        assert_envelope(self, obj, "UNKNOWN_FIELD")
        run.assert_not_called()
        # ``name`` 是 skill 专用的 add-only 字段：带在 mcp_* 上不报错、也绝不改路径（仍是 scope_paths 算出的那一个）
        for name in ("x", "../../etc/passwd", str(self.home / ".mcp.json")):
            with self.subTest(name=name):
                status, obj, run = self._reveal({"target": "mcp_user", "name": name})
                self.assertEqual(status, 200, obj)
                self.assertEqual(obj, {"ok": True, "revealed": str(self.user_home / ".claude.json")})
                self.assertEqual(run.call_args[0][0], ["open", "-R", str(self.user_home / ".claude.json")])
        # 词表之外的 mcp_* 变体也是 400（不是 404）：词表先于路径
        status, obj, run = self._reveal({"target": "mcp_local"})
        self.assertEqual(status, 400)
        assert_envelope(self, obj, "INVALID_FIELD")
        run.assert_not_called()

    def test_non_darwin_returns_501(self):
        write_text(self.user_home / ".claude.json", "{}")
        with mock.patch.object(files_mod.sys, "platform", "linux"), mock.patch.object(files_mod.subprocess, "run") as run:
            status, obj = post_json(self.port, "/api/reveal", {"target": "mcp_user"})
        self.assertEqual(status, 501)
        assert_envelope(self, obj, "NOT_IMPLEMENTED")
        run.assert_not_called()

    def test_list_carries_path_display_with_home_abbreviated(self):
        write_text(self.user_home / ".claude.json", json.dumps({"mcpServers": {"fs": {"command": "npx"}}}))
        status, obj = get_json(self.port, "/api/mcp")
        self.assertEqual(status, 200)
        rows = {s["scope"]: s for s in obj["scopes"]}
        # 绝对路径照旧（add-only：老客户端读 path 不受影响），展示用的另给一份
        self.assertEqual(rows["user"]["path"], str(self.user_home / ".claude.json"))
        self.assertEqual(rows["user"]["path_display"], "~/.claude.json")
        self.assertEqual(rows["project"]["path_display"], "~/zai/.mcp.json")
        self.assertTrue(rows["user"]["exists"])
        self.assertFalse(rows["project"]["exists"])

    def test_abbrev_home_only_matches_the_home_prefix_as_a_path_component(self):
        home = Path("/Users/demo")
        self.assertEqual(mcp_servers.abbrev_home(Path("/Users/demo/.claude.json"), home), "~/.claude.json")
        self.assertEqual(mcp_servers.abbrev_home(Path("/Users/demo"), home), "~")
        # /Users/demo2/... 不是 /Users/demo 之下：不许缩（纯 startswith 会把它错缩成 ~2/...）
        self.assertEqual(mcp_servers.abbrev_home(Path("/Users/demo2/.mcp.json"), home), "/Users/demo2/.mcp.json")
        self.assertEqual(mcp_servers.abbrev_home(Path("/srv/repo/.mcp.json"), home), "/srv/repo/.mcp.json")
        # HOME 是根目录时什么都不缩（否则所有路径都成 ~/…）
        self.assertEqual(mcp_servers.abbrev_home(Path("/srv/repo/.mcp.json"), Path("/")), "/srv/repo/.mcp.json")
        # 列表里的 project 行不在 HOME 下 → 原样
        outside = Path(self.tmp.name) / "elsewhere"
        (outside / "state").mkdir(parents=True)
        rows = {s["scope"]: s for s in mcp_servers.mcp(outside, self.user_home)["scopes"]}
        self.assertEqual(rows["project"]["path_display"], str(outside / ".mcp.json"))


if __name__ == "__main__":
    unittest.main()
