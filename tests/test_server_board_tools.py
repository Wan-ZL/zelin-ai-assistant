"""server/ 看板工具面（CONTRACT §21 / §22 / §47.4 / §54 / §68.7–§68.10）：

- POST /api/terminal：命令由 server 从投影行推导（copy_cmd → claude --resume），**入队**
  state/terminal_queue/<id>.json 给壳消费（2026-09-05 起不写 .command 不 open）；404 / 400 /
  501 / 503 SHELL_UNAVAILABLE（壳心跳缺席）/ UNKNOWN_FIELD；写侧清扫过期条目；
- POST /api/repair/actd：launchctl 注入——已加载 → kickstart；未加载 → 409 指向
  install.sh；label 与 act/doctor.ACTD_LABEL 逐字一致；
- GET /api/mcp：MCP 只读 mcpServers 子树、掩码、env 只给个数（Skills 商店 = §67 自己的判例）；
- GET /api/claude-sessions：--scan --window N 子进程注入与 window 校验。
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
    """§68.7（2026-09-05，issue #216）：server 只入队，不写 .command、不 open、不 spawn 任何进程。"""

    def setUp(self):
        super().setUp()
        self.board = seed_scene(self.home, "running")
        darwin = mock.patch.object(terminal_launch.sys, "platform", "darwin")
        darwin.start()
        self.addCleanup(darwin.stop)
        self.queue = terminal_launch.paths.terminal_queue_dir(self.home)
        self.heartbeat = terminal_launch.paths.shell_heartbeat_path(self.home)
        self._beat()

    def _beat(self):
        """壳在跑 = state/shell.heartbeat 新鲜（壳每 5 s touch 一次）。"""
        self.heartbeat.write_text("pid=1\n", encoding="utf-8")

    def _running_row(self):
        return next(r for r in self.board["running"] if r.get("session_id") or r.get("copy_cmd"))

    def _entries(self):
        return sorted(self.queue.glob("*.json")) if self.queue.is_dir() else []

    def test_launch_enqueues_a_request_derived_from_the_projection(self):
        row = self._running_row()
        status, obj = post_json(self.port, "/api/terminal", {"card_id": row["id"]})
        self.assertEqual(status, 200, obj)
        self.assertTrue(obj["ok"])
        self.assertEqual(obj["command"], terminal_launch.command_for(row))
        entries = self._entries()
        self.assertEqual([str(e) for e in entries], [obj["command_file"]])
        entry = json.loads(entries[0].read_text(encoding="utf-8"))
        self.assertEqual(entry["id"], obj["queue_id"])
        self.assertEqual(entries[0].name, entry["id"] + ".json")
        self.assertEqual(entry["kind"], "takeover")
        self.assertEqual(entry["card_id"], row["id"])
        self.assertEqual(entry["command"], obj["command"])
        self.assertEqual(entry["cwd"], obj["cwd"])
        self.assertTrue(entry["shell_line"].endswith("exec " + obj["command"]))
        self.assertIn("export AIASSISTANT_HOME=", entry["shell_line"])
        self.assertIsInstance(entry["created_at"], int)
        # 队列目录里只有这一条：没有 .tmp 尸体，也没有 .command（通道已 retired，server 不再写 $TMPDIR）
        self.assertEqual([f.name for f in self.queue.iterdir()], [entries[0].name])
        self.assertFalse(hasattr(terminal_launch, "write_command_file"))
        self.assertFalse(hasattr(terminal_launch, "open_command_file"))

    def test_command_for_prefers_copy_cmd_then_resume(self):
        self.assertEqual(terminal_launch.command_for({"copy_cmd": " claude --resume abc "}), "claude --resume abc")
        self.assertEqual(terminal_launch.command_for({"session_id": "0123abcd"}), "claude --resume 0123abcd")
        self.assertIsNone(terminal_launch.command_for({"session_id": "bad id/../x"}))
        self.assertIsNone(terminal_launch.command_for({}))

    @unittest.skipIf(_WIN, "POSIX shell quoting")
    def test_shell_line_quotes_cwd_and_home_and_skips_what_is_absent(self):
        line = terminal_launch.shell_line_for("claude --resume x", "/tmp/my dir", Path("/h"))
        self.assertEqual(line, "cd '/tmp/my dir' || { echo \"folder not found: /tmp/my dir\"; exit 1; }; "
                               "export AIASSISTANT_HOME=/h; exec claude --resume x")
        # 相对 / 缺席的 cwd 不 cd；home None（卸载脚本）不导出
        self.assertEqual(terminal_launch.shell_line_for("bash uninstall.sh", "relative", None), "exec bash uninstall.sh")
        self.assertEqual(terminal_launch.shell_line_for("claude", None, Path("/h")),
                         "export AIASSISTANT_HOME=/h; exec claude")

    def test_launch_falls_back_to_home_when_the_row_has_no_cwd(self):
        row = self._running_row()
        row_id = row["id"]
        for r in self.board["running"]:
            if r["id"] == row_id:
                r.pop("cwd", None)
        from tests.test_server_common import rewrite_board
        rewrite_board(self.home, self.board)
        status, obj = post_json(self.port, "/api/terminal", {"card_id": row_id})
        self.assertEqual(status, 200, obj)
        self.assertEqual(obj["cwd"], str(self.home))

    def test_unknown_card_is_404_and_no_session_is_400(self):
        status, obj = post_json(self.port, "/api/terminal", {"card_id": "R-999999"})
        self.assertEqual(status, 404)
        assert_envelope(self, obj, "NOT_FOUND")
        proposal = self.board["needs_approval"][0]["id"]
        status, obj = post_json(self.port, "/api/terminal", {"card_id": proposal})
        self.assertEqual(status, 400)
        assert_envelope(self, obj, "INVALID_FIELD")
        self.assertEqual(self._entries(), [])

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

    # ---- 壳在不在跑：心跳新鲜才入队；没有消费者 → 503（页面降级为复制指令 + 提示）----
    def test_missing_or_stale_heartbeat_is_503_and_nothing_is_queued(self):
        row = self._running_row()
        self.heartbeat.unlink()
        status, obj = post_json(self.port, "/api/terminal", {"card_id": row["id"]})
        self.assertEqual(status, 503)
        assert_envelope(self, obj, "SHELL_UNAVAILABLE")
        self.assertEqual(obj["error"]["details"]["heartbeat"], str(self.heartbeat))
        self.assertEqual(self._entries(), [])
        # 过期心跳（壳死了没来得及删）同样 503
        self._beat()
        stale = self.heartbeat.stat().st_mtime + terminal_launch.HEARTBEAT_FRESH_S + 1
        with self.assertRaises(terminal_launch.ShellUnavailableError):
            terminal_launch.launch(self.home, {"card_id": row["id"]}, platform="darwin", now=stale)
        self.assertEqual(self._entries(), [])
        self.assertTrue(terminal_launch.shell_alive(self.home))
        self.assertFalse(terminal_launch.shell_alive(self.home, now=stale))
        self.assertFalse(terminal_launch.shell_alive(Path(self.tmp.name) / "nowhere"))

    # ---- 写侧清扫：过期条目（含 .tmp 尸体）随下一次入队被删——壳一直没跑时目录不会无限长 ----
    def test_enqueue_sweeps_stale_entries_but_keeps_fresh_ones(self):
        self.queue.mkdir(parents=True)
        old = self.queue / "old.json"
        old.write_text("{}", encoding="utf-8")
        corpse = self.queue / "half.json.tmp"
        corpse.write_text("", encoding="utf-8")
        fresh = self.queue / "fresh.json"
        fresh.write_text("{}", encoding="utf-8")
        past = fresh.stat().st_mtime - terminal_launch.STALE_AFTER_S - 5
        os.utime(old, (past, past))
        os.utime(corpse, (past, past))
        entry, path = terminal_launch.enqueue(self.home, "takeover", "claude", "exec claude", str(self.home),
                                              card_id="R-1")
        names = sorted(f.name for f in self.queue.iterdir())
        self.assertEqual(names, sorted(["fresh.json", path.name]))
        self.assertEqual(entry["card_id"], "R-1")
        with self.assertRaises(ValueError):
            terminal_launch.enqueue(self.home, "bogus", "claude", "exec claude", str(self.home))
        self.assertEqual(terminal_launch.KINDS, ("takeover", "maintainer", "uninstall"))

    def test_unwritable_queue_dir_is_500_not_a_crash(self):
        blocker = self.queue
        blocker.parent.mkdir(parents=True, exist_ok=True)
        blocker.write_text("not a dir", encoding="utf-8")   # mkdir(exist_ok) 撞上普通文件 → OSError
        with self.assertRaises(terminal_launch.ApiError) as ctx:
            terminal_launch.enqueue(self.home, "takeover", "claude", "exec claude", str(self.home))
        self.assertEqual(ctx.exception.status, 500)
        self.assertIn("could not queue", ctx.exception.message)


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
        with mock.patch.object(repair, "default_runner", run), \
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
