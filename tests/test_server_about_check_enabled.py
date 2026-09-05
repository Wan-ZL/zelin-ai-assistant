"""``GET /api/about`` 的 ``check_enabled``（CONTRACT §68.6 2026-09-05 追记；parity 批次 about-update-guards-uninstall-copy）。

原生 AboutView 一进页就读 ``updates_check_enabled`` override → config.yaml ``updates.check_enabled`` → 默认 true
（Pages.swift UpdateCheckModel.reload），关着时立刻显示「自动检查新版本已关闭」并灰掉「立即检查」；web 版此前只在
第一次点击的回执里才知道——所以 /api/about add-only 带上同一把旋钮的 effective 值。三层同 settings_catalog；
overrides 坏文件当空（原生 readOverrides 回 [:]），关于页不 409。
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first
from tests.test_server_common import get_json, start_server, write_text

from server import about, settings_catalog


class AboutCheckEnabledTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-about-ce-")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        (self.home / "state").mkdir(parents=True)
        user_home = Path(self.tmp.name) / "user"
        user_home.mkdir()
        env = mock.patch.dict(os.environ, {"HOME": str(user_home), "USERPROFILE": str(user_home)})
        env.start()
        self.addCleanup(env.stop)
        _httpd, self.port = start_server(self, self.home)

    def test_default_is_true_and_field_is_add_only(self):
        status, obj = get_json(self.port, "/api/about")
        self.assertEqual(status, 200)
        self.assertIs(obj["check_enabled"], True)
        # 老键一个不少（字段 add-only）
        for key in ("version", "home", "repo", "update_available", "update_check"):
            self.assertIn(key, obj)

    def test_override_false_wins(self):
        write_text(self.home / "state" / "settings_overrides.json", json.dumps({"updates_check_enabled": False}))
        _s, obj = get_json(self.port, "/api/about")
        self.assertIs(obj["check_enabled"], False)

    def test_config_layer_and_override_precedence_match_settings_catalog(self):
        write_text(self.home / "config.yaml", "updates:\n  check_enabled: false\n")
        self.assertIs(about.check_enabled(self.home), False)
        # override 压过 config（与设置页「自动检查新版本」的 effective 同一答案）
        write_text(self.home / "state" / "settings_overrides.json", json.dumps({"updates_check_enabled": True}))
        self.assertIs(about.check_enabled(self.home), True)
        self.assertEqual(about.check_enabled(self.home),
                         settings_catalog.effective_value(self.home, *about.CHECK_ENABLED_FIELD))

    def test_corrupt_overrides_fall_back_like_native_read_overrides(self):
        write_text(self.home / "state" / "settings_overrides.json", "{torn")
        # 关于页不 409：坏 overrides 当空 → config.yaml 层仍生效
        status, obj = get_json(self.port, "/api/about")
        self.assertEqual(status, 200)
        self.assertIs(obj["check_enabled"], True)
        write_text(self.home / "config.yaml", "updates:\n  check_enabled: false\n")
        self.assertIs(about.check_enabled(self.home), False)


if __name__ == "__main__":
    unittest.main()
