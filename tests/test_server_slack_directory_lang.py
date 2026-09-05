"""server/slack_directory 的 ``?lang=zh|en`` 透传（CONTRACT §68.1 2026-09-05 追记；§15 文案随 UI 语言）。

原生 SettingsSlack.swift fetchDirectory 起 ``act.lib.slack_setup --directory`` 时带
``AIASSISTANT_UI_LANG = LanguageMirror.current``——python 侧 slack_setup.error_message 经 failures.pick /
ui_lang 第一级读它，``ok:false`` 的 ``message`` 才与 app 语言一致。web 版的等价物：
``GET /api/slack/directory?lang=`` 经 server/doctor_run.parse_lang 校验后进子进程 env；其它值 400 且不起
子进程；不带 lang = 老行为（env 不注入）。目录内容与语言无关，所以没有按语言分份的缓存要钉。

subproc.default_runner 经 mock.patch 替换——测试绝不真起 ``python -m act.lib.slack_setup``。
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first
from tests.test_server_common import assert_envelope, get_json, start_server

from server import slack_directory, subproc

DIRECTORY_JSON = json.dumps({"ok": True, "fetched_at": "2026-09-05T10:00:00Z",
                             "channels": [{"id": "C1", "name": "eng"}],
                             "users": [{"id": "U1", "name": "sam", "real_name": "Sam R"}]})


class _ServerCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-slack-dir-lang-")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        (self.home / "state").mkdir(parents=True)
        user_home = Path(self.tmp.name) / "user"
        user_home.mkdir()
        env = mock.patch.dict(os.environ, {"HOME": str(user_home), "USERPROFILE": str(user_home)})
        env.start()
        self.addCleanup(env.stop)
        # 宿主 shell 可能带着 AIASSISTANT_UI_LANG——不带 lang 的断言要看「server 没注入」
        os.environ.pop("AIASSISTANT_UI_LANG", None)
        self.calls = []
        _httpd, self.port = start_server(self, self.home)

        def runner(argv, env, cwd, timeout_s):
            self.calls.append({"argv": argv, "env": env, "timeout_s": timeout_s})
            return 0, DIRECTORY_JSON, ""
        patcher = mock.patch.object(subproc, "default_runner", runner)
        patcher.start()
        self.addCleanup(patcher.stop)

    def langs_seen(self):
        return [c["env"].get("AIASSISTANT_UI_LANG") for c in self.calls]


class SlackDirectoryLangEnvTestCase(_ServerCase):
    def test_lang_query_becomes_ui_lang_env_for_the_subprocess(self):
        status, obj = get_json(self.port, "/api/slack/directory?lang=zh")
        self.assertEqual(status, 200)
        self.assertTrue(obj["ok"])
        self.assertEqual(obj["channels"], [{"id": "C1", "name": "eng"}])
        self.assertEqual(self.langs_seen(), ["zh"])
        # 其余不变：仍带 home、仍是 --directory、仍是目录超时
        call = self.calls[0]
        self.assertEqual(call["env"]["AIASSISTANT_HOME"], str(self.home))
        self.assertEqual(call["argv"][1:], ["-m", "act.lib.slack_setup", "--directory"])
        self.assertEqual(call["timeout_s"], slack_directory.DIRECTORY_TIMEOUT_S)

    def test_lang_rides_alongside_refresh(self):
        get_json(self.port, "/api/slack/directory?refresh=1&lang=en")
        self.assertEqual(self.langs_seen(), ["en"])
        self.assertEqual(self.calls[0]["argv"][1:], ["-m", "act.lib.slack_setup", "--directory", "--refresh"])

    def test_no_lang_means_no_env_injection(self):
        get_json(self.port, "/api/slack/directory")
        get_json(self.port, "/api/slack/directory?lang=")
        self.assertEqual(self.langs_seen(), [None, None])

    def test_bad_lang_is_400_and_never_spawns(self):
        for bad in ("fr", "ZH", "zh-CN"):
            status, obj = get_json(self.port, "/api/slack/directory?lang=%s" % bad)
            self.assertEqual(status, 400, bad)
            assert_envelope(self, obj, "INVALID_FIELD")
        self.assertEqual(self.calls, [])

    def test_failure_json_still_passes_through_with_lang(self):
        """lang 只决定 act 侧挑哪一句——server 仍原样透传 ``ok:false`` 的 message，不 500。"""
        def runner(argv, env, cwd, timeout_s):
            self.calls.append({"argv": argv, "env": env, "timeout_s": timeout_s})
            sentence = "Paste and save the token first" if env.get("AIASSISTANT_UI_LANG") == "en" else "先粘贴并保存 token"
            return 1, json.dumps({"ok": False, "error": "no_token", "message": sentence}), ""
        with mock.patch.object(subproc, "default_runner", runner):
            status, obj = get_json(self.port, "/api/slack/directory?lang=en")
        self.assertEqual(status, 200)
        self.assertEqual((obj["ok"], obj["error"], obj["message"]), (False, "no_token", "Paste and save the token first"))
        self.assertEqual(self.langs_seen(), ["en"])


class DirectoryFunctionTestCase(unittest.TestCase):
    """不经 HTTP 直接调 ``slack_directory.directory``：``lang`` 只在给了值时才进 env。"""

    def setUp(self):
        os.environ.pop("AIASSISTANT_UI_LANG", None)
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-slack-dir-fn-")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.calls = []

    def runner(self, argv, env, cwd, timeout_s):
        self.calls.append(env)
        return 0, DIRECTORY_JSON, ""

    def test_lang_kwarg_is_the_env_key(self):
        slack_directory.directory(self.home, lang="zh", runner=self.runner)
        slack_directory.directory(self.home, refresh=True, lang="en", runner=self.runner)
        slack_directory.directory(self.home, runner=self.runner)
        self.assertEqual([e.get("AIASSISTANT_UI_LANG") for e in self.calls], ["zh", "en", None])
        self.assertTrue(all(e["AIASSISTANT_HOME"] == str(self.home) for e in self.calls))


if __name__ == "__main__":
    unittest.main()
