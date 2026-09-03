"""server/ask_assistant.py — 问问助手的 server 落点（CONTRACT §27 / §54.4）：

- GET /api/ask/history：只读 state/ask_history.json（缺席 / 坏 JSON → 空表；cap 20；非 dict 行丢弃）；
- POST /api/ask {question}：子进程 ``python -m act.ask <question>``（runner 注入，绝不真起）——
  一行 JSON 原样透传；空问题 / 非字符串 / 超长 400 INVALID_FIELD；多余字段 400 UNKNOWN_FIELD 由
  路由层统一把关；子进程没给 JSON → ok:false 带 stderr 尾巴与 timeout 判定。
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first
from tests.test_server_common import assert_envelope, get_json, post_json, start_server

from server import ask_assistant as ask, maintainer_launch, subproc, uninstall_launch
from server import slack_manifest as slack_setup
from tests.test_server_common import write_text


class _ServerCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-ask-")
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


class AskTestCase(_ServerCase):
    def test_history_missing_bad_and_capped(self):
        _s, obj = get_json(self.port, "/api/ask/history")
        self.assertEqual(obj, {"items": []})
        ask.history_path(self.home).write_text("{not json", encoding="utf-8")
        self.assertEqual(get_json(self.port, "/api/ask/history")[1], {"items": []})
        rows = [{"q": "q%d" % i, "a": "a"} for i in range(25)] + ["junk"]
        ask.history_path(self.home).write_text(json.dumps(rows), encoding="utf-8")
        _s, obj = get_json(self.port, "/api/ask/history")
        self.assertEqual(len(obj["items"]), 20)
        self.assertEqual(obj["items"][0]["q"], "q0")

    def test_ask_forwards_question_as_argv_and_passes_json_through(self):
        calls = self._patch(json.dumps({"ok": True, "answer": "42", "citation": "README", "lang": "en", "elapsed_s": 1.5}))
        status, obj = post_json(self.port, "/api/ask", {"question": "  why   no cards? "})
        self.assertEqual(status, 200)
        self.assertEqual(obj["answer"], "42")
        argv, timeout_s = calls[0]
        self.assertEqual(argv[1:], ["-m", "act.ask", "why no cards?"])
        self.assertEqual(timeout_s, ask.TIMEOUT_S)

    def test_bad_question_is_400(self):
        for body in ({"question": ""}, {"question": "   "}, {"question": 7}, {}):
            status, obj = post_json(self.port, "/api/ask", body)
            self.assertEqual(status, 400, body)
            assert_envelope(self, obj, "INVALID_FIELD")
        status, obj = post_json(self.port, "/api/ask", {"question": "x" * (ask.QUESTION_MAX + 1)})
        self.assertEqual(status, 400)
        status, obj = post_json(self.port, "/api/ask", {"question": "hi", "extra": 1})
        self.assertEqual(status, 400)
        assert_envelope(self, obj, "UNKNOWN_FIELD")

    def test_subprocess_without_json_is_honest_not_500(self):
        self._patch("", rc=124, err="act.ask timed out after 75s")
        status, obj = post_json(self.port, "/api/ask", {"question": "hi"})
        self.assertEqual(status, 200)
        self.assertFalse(obj["ok"])
        self.assertTrue(obj["timeout"])
        self.assertIn("timed out", obj["error"])
        self._patch("", rc=1, err="")
        _s, obj = post_json(self.port, "/api/ask", {"question": "hi"})
        self.assertEqual(obj["error"], "ask exited 1")


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


class UninstallLaunchTestCase(_ServerCase):
    """POST /api/uninstall/terminal：写 .command（cd repo && exec bash uninstall.sh）并 open；脚本缺席 404；
    非 darwin 501；多余字段 400；open 失败 500 带手动命令。server 自己永不删文件。"""

    def test_launch_writes_command_and_opens(self):
        opened = []
        out = Path(self.tmp.name) / "cmds"
        out.mkdir()
        receipt = uninstall_launch.launch({}, opener=opened.append, out_dir=out, platform="darwin")
        self.assertTrue(receipt["ok"])
        self.assertEqual(len(opened), 1)
        text = opened[0].read_text(encoding="utf-8")
        self.assertIn("exec bash uninstall.sh", text)
        self.assertIn(str(uninstall_launch.paths.repo_root()), text)
        self.assertTrue(receipt["command"].endswith("bash uninstall.sh"))

    def test_gates(self):
        with self.assertRaises(uninstall_launch.UnknownFieldError):
            uninstall_launch.launch({"force": True}, opener=lambda p: None, platform="darwin")
        with self.assertRaises(uninstall_launch.NotImplementedError501):
            uninstall_launch.launch({}, opener=lambda p: None, platform="linux")
        with mock.patch.object(uninstall_launch, "script_path", lambda: Path(self.tmp.name) / "nope.sh"):
            with self.assertRaises(uninstall_launch.NotFoundError):
                uninstall_launch.launch({}, opener=lambda p: None, platform="darwin")

    def test_route_open_failure_is_500_with_manual_command(self):
        from server import terminal_launch

        def boom(_path, _app=None):
            raise OSError("no Terminal")
        with mock.patch.object(terminal_launch, "_default_opener", boom), \
                mock.patch.object(uninstall_launch.sys, "platform", "darwin"):
            status, obj = post_json(self.port, "/api/uninstall/terminal", {})
        self.assertEqual(status, 500)
        self.assertIn("could not open Terminal", obj["error"]["message"])


class MaintainerLaunchTestCase(_ServerCase):
    """POST /api/maintainer/terminal：命令 = cd <effective repo_path> && claude [--resume <id>]（server 读设置，
    客户端零参数）；路径不存在 400；坏 session id 400；非 darwin 501；多余字段 400。"""

    def test_default_repo_is_the_checkout_and_resume_follows_the_override(self):
        opened = []
        receipt = maintainer_launch.launch(self.home, {}, opener=opened.append, out_dir=Path(self.tmp.name), platform="darwin")
        self.assertEqual(receipt["command"], "cd %s && claude" % maintainer_launch.shlex.quote(str(maintainer_launch.paths.repo_root())))
        self.assertIn("exec cd", opened[0].read_text(encoding="utf-8"))
        write_text(self.home / "state" / "settings_overrides.json",
                   json.dumps({"maintainer_repo_path": str(self.home), "maintainer_session_id": "6f9619ff-8b86"}))
        receipt = maintainer_launch.launch(self.home, {}, opener=opened.append, out_dir=Path(self.tmp.name), platform="darwin")
        self.assertEqual(receipt["command"], "cd %s && claude --resume 6f9619ff-8b86" % maintainer_launch.shlex.quote(str(self.home)))
        self.assertEqual(receipt["cwd"], str(self.home))

    def test_gates(self):
        write_text(self.home / "state" / "settings_overrides.json", json.dumps({"maintainer_repo_path": str(self.home / "nope")}))
        with self.assertRaises(maintainer_launch.InvalidFieldError):
            maintainer_launch.launch(self.home, {}, opener=lambda p: None, platform="darwin")
        write_text(self.home / "state" / "settings_overrides.json", json.dumps({"maintainer_session_id": "bad id; rm -rf"}))
        with self.assertRaises(maintainer_launch.InvalidFieldError):
            maintainer_launch.launch(self.home, {}, opener=lambda p: None, platform="darwin")
        with self.assertRaises(maintainer_launch.UnknownFieldError):
            maintainer_launch.launch(self.home, {"x": 1}, opener=lambda p: None, platform="darwin")
        with self.assertRaises(maintainer_launch.NotImplementedError501):
            maintainer_launch.launch(self.home, {}, opener=lambda p: None, platform="linux")
        with mock.patch.object(maintainer_launch.sys, "platform", "linux"):
            status, obj = post_json(self.port, "/api/maintainer/terminal", {})
        self.assertEqual(status, 501)
        assert_envelope(self, obj, "NOT_IMPLEMENTED")


if __name__ == "__main__":
    unittest.main()
