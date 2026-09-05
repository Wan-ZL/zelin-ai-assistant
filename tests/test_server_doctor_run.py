"""server/doctor_run 的 ``?lang=zh|en`` 透传（CONTRACT §68.4 2026-09-05 追记；§15 文案随 UI 语言）。

原生 Pages.swift runFullOutput 起 doctor 时带 ``AIASSISTANT_UI_LANG = LanguageMirror.current``，
python 侧 act/lib/failures.ui_lang 第一级读它——doctor 的 detail / fix 人话与 app 语言一致。
web 版的等价物：``GET /api/doctor?lang=`` 与 ``GET /api/diagnostics?lang=`` 校验后进子进程 env，
且 lang 进 15 s 缓存键（两种语言各一份，互不串）。不带 lang = 老行为（env 不注入）。

subproc.default_runner 经 mock.patch 替换——测试绝不真起 ``python -m act.doctor``。
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first
from tests.test_server_common import assert_envelope, get_json, start_server

from server import doctor_run, subproc
from server.errors import InvalidFieldError

DOCTOR_JSON = json.dumps({"home": "/x", "checks": [
    {"name": "claude CLI", "status": "OK", "detail": "ok", "fix": "", "failure_id": "", "action_id": ""},
]})


class _ServerCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-doctor-lang-")
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
        doctor_run.reset_cache_for_tests()
        self.addCleanup(doctor_run.reset_cache_for_tests)
        self.calls = []
        _httpd, self.port = start_server(self, self.home)

        def runner(argv, env, cwd, timeout_s):
            self.calls.append({"argv": argv, "env": env})
            return 0, DOCTOR_JSON, ""
        patcher = mock.patch.object(subproc, "default_runner", runner)
        patcher.start()
        self.addCleanup(patcher.stop)

    def langs_seen(self):
        return [c["env"].get("AIASSISTANT_UI_LANG") for c in self.calls]


class DoctorLangEnvTestCase(_ServerCase):
    def test_lang_query_becomes_ui_lang_env_for_the_subprocess(self):
        status, obj = get_json(self.port, "/api/doctor?lang=zh")
        self.assertEqual(status, 200)
        self.assertTrue(obj["ok"])
        self.assertEqual(self.langs_seen(), ["zh"])
        # 其余 env 不变：仍带 home、仍是 --json --fast
        self.assertEqual(self.calls[0]["env"]["AIASSISTANT_HOME"], str(self.home))
        self.assertEqual(self.calls[0]["argv"][-2:], ["--json", "--fast"])

    def test_no_lang_means_no_env_injection(self):
        get_json(self.port, "/api/doctor")
        self.assertEqual(self.langs_seen(), [None])

    def test_diagnostics_route_passes_lang_too(self):
        get_json(self.port, "/api/diagnostics?lang=en")
        self.assertEqual(self.langs_seen(), ["en"])

    def test_bad_lang_is_400_and_never_spawns(self):
        status, obj = get_json(self.port, "/api/doctor?lang=fr")
        self.assertEqual(status, 400)
        assert_envelope(self, obj, "INVALID_FIELD")
        status, _obj = get_json(self.port, "/api/diagnostics?lang=ZH")
        self.assertEqual(status, 400)
        self.assertEqual(self.calls, [])


class DoctorLangCacheKeyTestCase(_ServerCase):
    def test_each_lang_has_its_own_cache_entry(self):
        get_json(self.port, "/api/doctor?lang=zh")
        get_json(self.port, "/api/doctor?lang=zh")
        self.assertEqual(self.langs_seen(), ["zh"])            # 同语言 15 s 内命中
        get_json(self.port, "/api/doctor?lang=en")
        self.assertEqual(self.langs_seen(), ["zh", "en"])      # 换语言 = 另一份
        get_json(self.port, "/api/doctor")
        self.assertEqual(self.langs_seen(), ["zh", "en", None])  # 不带 lang 也是自己的一份
        get_json(self.port, "/api/diagnostics?lang=en")
        self.assertEqual(len(self.calls), 3)                    # diagnostics 与 doctor 共用 (home, fast, lang) 键

    def test_refresh_reruns_only_the_requested_lang(self):
        get_json(self.port, "/api/doctor?lang=zh")
        get_json(self.port, "/api/doctor?lang=en")
        get_json(self.port, "/api/doctor?lang=zh&refresh=1")
        self.assertEqual(self.langs_seen(), ["zh", "en", "zh"])
        get_json(self.port, "/api/doctor?lang=en")
        self.assertEqual(len(self.calls), 3)                    # en 的缓存没被 zh 的 refresh 打掉


class ParseLangTestCase(unittest.TestCase):
    def test_parse_lang_vocabulary(self):
        self.assertIsNone(doctor_run.parse_lang(None))
        self.assertIsNone(doctor_run.parse_lang(""))
        self.assertEqual(doctor_run.parse_lang("zh"), "zh")
        self.assertEqual(doctor_run.parse_lang("en"), "en")
        with self.assertRaises(InvalidFieldError):
            doctor_run.parse_lang("zh-CN")
        self.assertEqual(doctor_run.LANGS, ("zh", "en"))


if __name__ == "__main__":
    unittest.main()
