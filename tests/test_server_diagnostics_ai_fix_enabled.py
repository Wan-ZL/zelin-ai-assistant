"""``GET /api/diagnostics`` 的 add-only 键 ``ai_fix_enabled``（CONTRACT §25 / §68.4 2026-09-05 追记）。

原生 Doctor.swift ``AIFix.enabled``：config.yaml ``doctor.ai_fix_enabled: false`` 让「让 AI 修」按钮
整个不出现，而不是点了才报错。web 版此前只在点击后吃 501（server/ai_fix_launch）；本键把开关
暴露给页面。语义镜像 act.lib.config ``_bool_or``：缺席 / 非映射块 / 坏值 = true，只有明确的 false 才关。

subproc.default_runner 经 mock.patch 替换——测试绝不真起 ``python -m act.doctor``。
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first
from tests.test_server_common import get_json, start_server, write_text

from server import diagnostics, doctor_run, subproc

DOCTOR_JSON = json.dumps({"home": "/x", "checks": []})


class AiFixEnabledTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-aifix-flag-")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        (self.home / "state").mkdir(parents=True)
        user_home = Path(self.tmp.name) / "user"
        user_home.mkdir()
        env = mock.patch.dict(os.environ, {"HOME": str(user_home), "USERPROFILE": str(user_home)})
        env.start()
        self.addCleanup(env.stop)
        doctor_run.reset_cache_for_tests()
        self.addCleanup(doctor_run.reset_cache_for_tests)
        patcher = mock.patch.object(subproc, "default_runner", lambda argv, env, cwd, timeout_s: (0, DOCTOR_JSON, ""))
        patcher.start()
        self.addCleanup(patcher.stop)
        _httpd, self.port = start_server(self, self.home)

    def _config(self, text):
        write_text(self.home / "config.yaml", text)

    def test_absent_config_defaults_to_enabled(self):
        status, obj = get_json(self.port, "/api/diagnostics")
        self.assertEqual(status, 200)
        self.assertIs(obj["ai_fix_enabled"], True)

    def test_explicit_false_hides_the_button(self):
        self._config("doctor:\n  ai_fix_enabled: false\n")
        _s, obj = get_json(self.port, "/api/diagnostics")
        self.assertIs(obj["ai_fix_enabled"], False)

    def test_string_spellings_follow_config_bool_words(self):
        # act.lib.config._bool_word 的词表：no / off / 0 也是关
        for spelling in ('"no"', "off", "0"):
            self._config("doctor:\n  ai_fix_enabled: %s\n" % spelling)
            self.assertIs(diagnostics.ai_fix_enabled(self.home), False, spelling)
        for spelling in ("true", '"yes"', "on", "1"):
            self._config("doctor:\n  ai_fix_enabled: %s\n" % spelling)
            self.assertIs(diagnostics.ai_fix_enabled(self.home), True, spelling)

    def test_bad_values_and_bad_shapes_keep_the_default(self):
        self._config("doctor:\n  ai_fix_enabled: maybe\n")
        self.assertIs(diagnostics.ai_fix_enabled(self.home), True)
        self._config("doctor: false\n")                      # 非映射块 → 块当 {}
        self.assertIs(diagnostics.ai_fix_enabled(self.home), True)
        self._config("doctor:\n  other_key: 1\n")            # 键缺席
        self.assertIs(diagnostics.ai_fix_enabled(self.home), True)
        self._config(":: not yaml [\n")                      # 坏 YAML → 整份 {}
        self.assertIs(diagnostics.ai_fix_enabled(self.home), True)


if __name__ == "__main__":
    unittest.main()
