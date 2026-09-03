"""server/ 看板工具面（CONTRACT §21 / §22 / §47.4 / §54 / §68.7–§68.10）：

- POST /api/terminal：命令由 server 从投影行推导（copy_cmd → claude --resume），写
  .command + open（opener 注入）；404 / 400 / 501 / UNKNOWN_FIELD；
- POST /api/repair/actd：launchctl 注入——已加载 → kickstart；未加载 → 409 指向
  install.sh；label 与 act/doctor.ACTD_LABEL 逐字一致；
- GET /api/mcp：MCP 只读 mcpServers 子树、掩码、env 只给个数（Skills 商店 = §67 自己的判例）；
- GET /api/claude-sessions：--scan --window N 子进程注入与 window 校验。
"""
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first
from tests.test_server_common import (assert_envelope, get_json, post_json,
                                      seed_scene, start_server, write_text)

from act import doctor as act_doctor
from server import repair, subproc, terminal_launch

_WIN = sys.platform.startswith("win")


class _ServerCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-tools-")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        (self.home / "state").mkdir(parents=True)
        self.user_home = Path(self.tmp.name) / "user"
        self.user_home.mkdir()
        env = mock.patch.dict(os.environ, {"HOME": str(self.user_home), "USERPROFILE": str(self.user_home)})
        env.start()
        self.addCleanup(env.stop)
        _httpd, self.port = start_server(self, self.home)


class TerminalLaunchTestCase(_ServerCase):
    def setUp(self):
        super().setUp()
        self.board = seed_scene(self.home, "running")
        self.opened = []
        for target, value in (("_default_opener", lambda p: self.opened.append(p)),):
            patcher = mock.patch.object(terminal_launch, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        darwin = mock.patch.object(terminal_launch.sys, "platform", "darwin")
        darwin.start()
        self.addCleanup(darwin.stop)

    def _running_row(self):
        return next(r for r in self.board["running"] if r.get("session_id") or r.get("copy_cmd"))

    def test_launch_writes_command_file_from_projection_and_opens_it(self):
        row = self._running_row()
        status, obj = post_json(self.port, "/api/terminal", {"card_id": row["id"]})
        self.assertEqual(status, 200, obj)
        self.assertTrue(obj["ok"])
        self.assertEqual(obj["command"], terminal_launch.command_for(row))
        path = Path(obj["command_file"])
        self.assertEqual(self.opened, [path])
        text = path.read_text(encoding="utf-8")
        self.assertIn("exec " + obj["command"], text)
        self.assertIn(row["id"], text)
        if not _WIN:
            self.assertTrue(path.stat().st_mode & stat.S_IXUSR)
        path.unlink()

    def test_command_for_prefers_copy_cmd_then_resume(self):
        self.assertEqual(terminal_launch.command_for({"copy_cmd": " claude --resume abc "}), "claude --resume abc")
        self.assertEqual(terminal_launch.command_for({"session_id": "0123abcd"}), "claude --resume 0123abcd")
        self.assertIsNone(terminal_launch.command_for({"session_id": "bad id/../x"}))
        self.assertIsNone(terminal_launch.command_for({}))

    @unittest.skipIf(_WIN, "POSIX shell quoting")
    def test_script_quotes_cwd_and_falls_back_to_home(self):
        text = terminal_launch.script_for("R-1", "claude --resume x", "/tmp/my dir", Path("/h"))
        self.assertIn("cd '/tmp/my dir'", text)
        text = terminal_launch.script_for("R-1", "claude --resume x", None, Path("/h"))
        self.assertIn("cd /h ", text)

    def test_unknown_card_is_404_and_no_session_is_400(self):
        status, obj = post_json(self.port, "/api/terminal", {"card_id": "R-999999"})
        self.assertEqual(status, 404)
        assert_envelope(self, obj, "NOT_FOUND")
        proposal = self.board["needs_approval"][0]["id"]
        status, obj = post_json(self.port, "/api/terminal", {"card_id": proposal})
        self.assertEqual(status, 400)
        assert_envelope(self, obj, "INVALID_FIELD")
        self.assertEqual(self.opened, [])

    def test_field_gates(self):
        status, obj = post_json(self.port, "/api/terminal", {"card_id": "R-1", "cmd": "rm -rf /"})
        self.assertEqual(status, 400)
        assert_envelope(self, obj, "UNKNOWN_FIELD")
        status, obj = post_json(self.port, "/api/terminal", {"card_id": "../x"})
        self.assertEqual(status, 400)
        assert_envelope(self, obj, "INVALID_FIELD")

    def test_non_darwin_is_501(self):
        with self.assertRaises(terminal_launch.NotImplementedError501):
            terminal_launch.launch(self.home, {"card_id": "R-1"}, platform="linux")


class RepairTestCase(_ServerCase):
    def _runner(self, loaded=True, kick_rc=0):
        calls = []

        def run(argv):
            calls.append(argv)
            if argv[1] == "print":
                return (0 if loaded else 113), ""
            return kick_rc, "kicked" if kick_rc == 0 else "Boot-out failed"
        return run, calls

    def test_loaded_agent_is_kickstarted(self):
        run, calls = self._runner()
        out = repair.kickstart_actd({}, runner=run, platform="darwin")
        self.assertEqual(out, {"ok": True, "label": repair.ACTD_LABEL, "action": "kickstart"})
        self.assertEqual(calls[1][:3], ["/bin/launchctl", "kickstart", "-k"])
        self.assertTrue(calls[1][3].endswith("/" + repair.ACTD_LABEL))

    def test_unloaded_agent_is_409_pointing_at_install(self):
        run, _calls = self._runner(loaded=False)
        with self.assertRaises(repair.ConflictError) as ctx:
            repair.kickstart_actd({}, runner=run, platform="darwin")
        self.assertEqual(ctx.exception.details["fix"], "bash install.sh")

    def test_kickstart_failure_is_500_with_output(self):
        run, _calls = self._runner(kick_rc=5)
        with self.assertRaises(repair.ApiError) as ctx:
            repair.kickstart_actd({}, runner=run, platform="darwin")
        self.assertIn("Boot-out failed", ctx.exception.message)

    def test_gates(self):
        with self.assertRaises(repair.UnknownFieldError):
            repair.kickstart_actd({"label": "x"}, runner=self._runner()[0], platform="darwin")
        with self.assertRaises(repair.NotImplementedError501):
            repair.kickstart_actd({}, runner=self._runner()[0], platform="linux")

    def test_route_uses_default_runner_and_label_mirrors_doctor(self):
        self.assertEqual(repair.ACTD_LABEL, act_doctor.ACTD_LABEL)
        run, calls = self._runner()
        with mock.patch.object(repair, "_default_runner", run), \
                mock.patch.object(repair.sys, "platform", "darwin"):
            status, obj = post_json(self.port, "/api/repair/actd", {})
        self.assertEqual(status, 200)
        self.assertTrue(obj["ok"])
        self.assertEqual(len(calls), 2)


class McpTestCase(_ServerCase):
    def test_mcp_reads_only_mcp_servers_and_masks(self):
        write_text(self.user_home / ".claude.json", json.dumps({
            "oauthAccount": {"email": "private@example.com"},
            "mcpServers": {
                "fs": {"command": "npx", "args": ["-y", "@x/fs", "--token", "xoxb-1234567890abc"],
                       "env": {"A": "1", "B": "2"}},
                "remote": {"type": "streamable-http", "url": "https://mcp.example.com/v1?key=sk-ant-abcdefgh"},
            }}))
        write_text(self.home / ".mcp.json", "{broken")
        status, obj = get_json(self.port, "/api/mcp")
        self.assertEqual(status, 200)
        text = json.dumps(obj)
        self.assertNotIn("private@example.com", text)
        self.assertNotIn("xoxb-1234567890abc", text)
        self.assertNotIn("sk-ant-abcdefgh", text)
        user = next(s for s in obj["scopes"] if s["scope"] == "user")
        by = {s["name"]: s for s in user["servers"]}
        self.assertEqual(by["fs"]["transport"], "stdio")
        self.assertEqual(by["fs"]["env_count"], 2)
        self.assertNotIn("env", by["fs"])
        self.assertEqual(by["remote"]["transport"], "http")
        self.assertTrue(by["remote"]["summary"].endswith("?●●●"))
        project = next(s for s in obj["scopes"] if s["scope"] == "project")
        self.assertTrue(project["exists"])
        self.assertFalse(project["parseable"])

    def test_mcp_missing_files(self):
        _s, obj = get_json(self.port, "/api/mcp")
        self.assertEqual([s["exists"] for s in obj["scopes"]], [False, False])


class ClaudeSessionsTestCase(_ServerCase):
    def _patch(self, out, rc=0, err=""):
        calls = []

        def runner(argv, env, cwd, timeout_s):
            calls.append(argv)
            return rc, out, err
        patcher = mock.patch.object(subproc, "default_runner", runner)
        patcher.start()
        self.addCleanup(patcher.stop)
        return calls

    def test_scan_forwards_cli_json_with_window(self):
        calls = self._patch(json.dumps({"ok": True, "root": "/r", "candidates": [{"session_id": "abc"}]}))
        status, obj = get_json(self.port, "/api/claude-sessions?window=3")
        self.assertEqual(status, 200)
        self.assertTrue(obj["ok"])
        self.assertEqual(obj["window"], 3)
        self.assertEqual(obj["candidates"][0]["session_id"], "abc")
        self.assertEqual(calls[0][1:], ["-m", "act.radar_claude_sessions", "--scan", "--window", "3"])

    def test_default_window_and_no_claude_dir(self):
        calls = self._patch(json.dumps({"ok": False, "reason": "no_claude_dir", "root": "/r"}))
        _s, obj = get_json(self.port, "/api/claude-sessions")
        self.assertEqual(obj["reason"], "no_claude_dir")
        self.assertEqual(obj["candidates"], [])
        self.assertEqual(calls[0][-1], "7")

    def test_bad_window_is_400_and_failed_scan_is_honest(self):
        status, obj = get_json(self.port, "/api/claude-sessions?window=0")
        self.assertEqual(status, 400)
        assert_envelope(self, obj, "INVALID_FIELD")
        status, obj = get_json(self.port, "/api/claude-sessions?window=abc")
        self.assertEqual(status, 400)
        self._patch("", rc=1, err="boom")
        _s, obj = get_json(self.port, "/api/claude-sessions")
        self.assertFalse(obj["ok"])
        self.assertEqual(obj["reason"], "scan_failed")


if __name__ == "__main__":
    unittest.main()
