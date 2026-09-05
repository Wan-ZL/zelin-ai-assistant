"""§54.4 左侧导航栏那轮补进 server 的三组小路由（CONTRACT §49 / §54.4 / §68.1 追记 / §68.6）：

- GET /api/slack/manifest：repo 的 config/slack-app-manifest.json 原文；缺席 404；
- GET /api/slack/directory[?refresh=1]：子进程 ``act.lib.slack_setup --directory`` 的 JSON 透传（runner 注入，绝不真起）；
- POST /api/uninstall/terminal / POST /api/maintainer/terminal：入队 state/terminal_queue 给壳（§68.7，2026-09-05 起
  不写 .command 不 open），server 自己不删不改任何东西、不 spawn。

本文件此前叫 tests/test_server_ask.py，第一组是问问助手的 /api/ask*——D29（2026-09-04）随 web 页一起退役
（§27 tombstone；``act/ask.py`` 与 tests/test_ask*.py 留给旧 app 到 P8），余下三组原样。
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first
from tests.test_server_common import assert_envelope, get_json, post_json, start_server

from server import maintainer_launch, slack_directory, subproc, terminal_launch, uninstall_launch
from server import slack_manifest as slack_setup
from tests.test_server_common import write_text


class _ServerCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-routes-")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        (self.home / "state").mkdir(parents=True)
        user_home = Path(self.tmp.name) / "user"
        user_home.mkdir()
        env = mock.patch.dict(os.environ, {"HOME": str(user_home), "USERPROFILE": str(user_home)})
        env.start()
        self.addCleanup(env.stop)
        _httpd, self.port = start_server(self, self.home)

    def _patch(self, out, rc=0, err=""):
        calls = []

        def runner(argv, env, cwd, timeout_s):
            calls.append((argv, timeout_s))
            return rc, out, err
        patcher = mock.patch.object(subproc, "default_runner", runner)
        patcher.start()
        self.addCleanup(patcher.stop)
        return calls


class SlackManifestTestCase(_ServerCase):
    """GET /api/slack/manifest：repo 的 config/slack-app-manifest.json 原文；缺席 404。"""

    def test_manifest_is_repo_file_verbatim_and_404_when_missing(self):
        status, obj = get_json(self.port, "/api/slack/manifest")
        self.assertEqual(status, 200)
        self.assertEqual(obj["manifest"], slack_setup.manifest_path().read_text(encoding="utf-8"))
        self.assertIn("oauth_config", json.loads(obj["manifest"]))
        with mock.patch.object(slack_setup, "manifest_path", lambda: Path(self.tmp.name) / "nope.json"):
            status, obj = get_json(self.port, "/api/slack/manifest")
        self.assertEqual(status, 404)
        assert_envelope(self, obj, "NOT_FOUND")


class SlackDirectoryTestCase(_ServerCase):
    """GET /api/slack/directory：子进程 ``act.lib.slack_setup --directory [--refresh]`` 的 JSON 透传（§68.1 追记）；
    起不来 → error no_python；没 JSON → directory_failed；都不 500。"""

    def test_directory_passes_the_cli_json_through_and_forwards_refresh(self):
        payload = {"ok": True, "fetched_at": "2026-09-03T10:00:00Z",
                   "channels": [{"id": "C1", "name": "eng"}], "users": [{"id": "U1", "name": "sam", "real_name": "Sam R"}]}
        calls = self._patch(json.dumps(payload))
        status, obj = get_json(self.port, "/api/slack/directory")
        self.assertEqual(status, 200)
        self.assertEqual(obj, payload)
        self.assertEqual(calls[0][0][1:], ["-m", "act.lib.slack_setup", "--directory"])
        self.assertEqual(calls[0][1], slack_directory.DIRECTORY_TIMEOUT_S)
        get_json(self.port, "/api/slack/directory?refresh=1")
        self.assertEqual(calls[1][0][1:], ["-m", "act.lib.slack_setup", "--directory", "--refresh"])

    def test_cli_failure_json_is_passed_through_with_its_bilingual_message(self):
        self._patch(json.dumps({"ok": False, "error": "no_token", "message": "先粘贴并保存 token"}), rc=1)
        status, obj = get_json(self.port, "/api/slack/directory")
        self.assertEqual(status, 200)
        self.assertEqual((obj["ok"], obj["error"], obj["message"]), (False, "no_token", "先粘贴并保存 token"))
        self.assertEqual((obj["channels"], obj["users"]), ([], []))

    def test_no_interpreter_and_no_json_are_honest_not_500(self):
        self._patch("", rc=127, err="[Errno 2] No such file or directory: 'python3'")
        _s, obj = get_json(self.port, "/api/slack/directory")
        self.assertEqual((obj["ok"], obj["error"]), (False, "no_python"))
        self.assertIn("Errno 2", obj["message"])
        self._patch("garbage", rc=1)
        _s, obj = get_json(self.port, "/api/slack/directory")
        self.assertEqual((obj["ok"], obj["error"], obj["message"]), (False, "directory_failed", "garbage"))


def _beat(home: Path) -> Path:
    """壳在跑 = state/shell.heartbeat 新鲜（§68.7 队列的消费者）。"""
    hb = terminal_launch.paths.shell_heartbeat_path(home)
    hb.write_text("pid=1\n", encoding="utf-8")
    return hb


def _entries(home: Path):
    q = terminal_launch.paths.terminal_queue_dir(home)
    return [json.loads(f.read_text(encoding="utf-8")) for f in sorted(q.glob("*.json"))] if q.is_dir() else []


class UninstallLaunchTestCase(_ServerCase):
    """POST /api/uninstall/terminal：入队 ``cd <repo>; exec bash uninstall.sh`` 给壳（§68.7 队列通道）；脚本缺席 404；
    非 darwin 501；多余字段 400；壳没在跑 503 带手动命令。server 自己永不删文件、不 spawn。"""

    def test_launch_enqueues_the_uninstall_command(self):
        _beat(self.home)
        receipt = uninstall_launch.launch({}, platform="darwin", home=self.home)
        self.assertTrue(receipt["ok"])
        entries = _entries(self.home)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["kind"], "uninstall")
        self.assertEqual(entries[0]["id"], receipt["queue_id"])
        self.assertTrue(entries[0]["shell_line"].endswith("exec bash uninstall.sh"))
        self.assertIn(str(uninstall_launch.paths.repo_root()), entries[0]["shell_line"])
        self.assertNotIn("AIASSISTANT_HOME", entries[0]["shell_line"])
        self.assertEqual(entries[0]["command"], receipt["command"])
        self.assertTrue(receipt["command"].endswith("bash uninstall.sh"))
        self.assertEqual(Path(receipt["command_file"]).name, receipt["queue_id"] + ".json")

    def test_gates(self):
        _beat(self.home)
        with self.assertRaises(uninstall_launch.UnknownFieldError):
            uninstall_launch.launch({"force": True}, platform="darwin", home=self.home)
        with self.assertRaises(uninstall_launch.NotImplementedError501):
            uninstall_launch.launch({}, platform="linux", home=self.home)
        with mock.patch.object(uninstall_launch, "script_path", lambda: Path(self.tmp.name) / "nope.sh"):
            with self.assertRaises(uninstall_launch.NotFoundError):
                uninstall_launch.launch({}, platform="darwin", home=self.home)
        self.assertEqual(_entries(self.home), [])

    def test_route_without_shell_is_503_with_manual_command(self):
        with mock.patch.object(uninstall_launch.sys, "platform", "darwin"):
            status, obj = post_json(self.port, "/api/uninstall/terminal", {})
        self.assertEqual(status, 503)
        assert_envelope(self, obj, "SHELL_UNAVAILABLE")
        self.assertEqual(obj["error"]["details"]["command"], uninstall_launch.shell_command())
        self.assertEqual(_entries(self.home), [])


class MaintainerLaunchTestCase(_ServerCase):
    """POST /api/maintainer/terminal：命令 = cd <effective repo_path> && claude [--resume <id>]（server 读设置，
    客户端零参数）；路径不存在 400；坏 session id 400；非 darwin 501；多余字段 400；壳没在跑 503。"""

    def test_default_repo_is_the_checkout_and_resume_follows_the_override(self):
        _beat(self.home)
        receipt = maintainer_launch.launch(self.home, {}, platform="darwin")
        repo = maintainer_launch.paths.repo_root()
        self.assertEqual(receipt["command"], "cd %s && claude" % maintainer_launch.shlex.quote(str(repo)))
        entries = _entries(self.home)
        self.assertEqual(entries[0]["kind"], "maintainer")
        self.assertEqual(entries[0]["command"], receipt["command"])
        self.assertTrue(entries[0]["shell_line"].startswith("cd %s ||" % maintainer_launch.shlex.quote(str(repo))))
        self.assertTrue(entries[0]["shell_line"].endswith("; exec claude"))
        write_text(self.home / "state" / "settings_overrides.json",
                   json.dumps({"maintainer_repo_path": str(self.home), "maintainer_session_id": "6f9619ff-8b86"}))
        receipt = maintainer_launch.launch(self.home, {}, platform="darwin")
        self.assertEqual(receipt["command"], "cd %s && claude --resume 6f9619ff-8b86" % maintainer_launch.shlex.quote(str(self.home)))
        self.assertEqual(receipt["cwd"], str(self.home))
        lines = sorted(e["shell_line"] for e in _entries(self.home))   # 文件名是 uuid，不按时间排
        self.assertEqual(len(lines), 2)
        self.assertTrue(any(line.endswith("; exec claude --resume 6f9619ff-8b86") for line in lines), lines)

    def test_gates(self):
        _beat(self.home)
        write_text(self.home / "state" / "settings_overrides.json", json.dumps({"maintainer_repo_path": str(self.home / "nope")}))
        with self.assertRaises(maintainer_launch.InvalidFieldError):
            maintainer_launch.launch(self.home, {}, platform="darwin")
        write_text(self.home / "state" / "settings_overrides.json", json.dumps({"maintainer_session_id": "bad id; rm -rf"}))
        with self.assertRaises(maintainer_launch.InvalidFieldError):
            maintainer_launch.launch(self.home, {}, platform="darwin")
        with self.assertRaises(maintainer_launch.UnknownFieldError):
            maintainer_launch.launch(self.home, {"x": 1}, platform="darwin")
        with self.assertRaises(maintainer_launch.NotImplementedError501):
            maintainer_launch.launch(self.home, {}, platform="linux")
        with mock.patch.object(maintainer_launch.sys, "platform", "linux"):
            status, obj = post_json(self.port, "/api/maintainer/terminal", {})
        self.assertEqual(status, 501)
        assert_envelope(self, obj, "NOT_IMPLEMENTED")
        self.assertEqual(_entries(self.home), [])

    def test_route_without_shell_is_503(self):
        write_text(self.home / "state" / "settings_overrides.json", json.dumps({}))
        with mock.patch.object(maintainer_launch.sys, "platform", "darwin"):
            status, obj = post_json(self.port, "/api/maintainer/terminal", {})
        self.assertEqual(status, 503)
        assert_envelope(self, obj, "SHELL_UNAVAILABLE")
        self.assertEqual(_entries(self.home), [])


if __name__ == "__main__":
    unittest.main()
