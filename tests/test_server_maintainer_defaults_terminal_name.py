"""§68.7 追记：开发者区两行的**生效默认灰字**与「会在 <终端> 中打开」的终端名（parity 批 maintainer-rows，
gap settings-maintainer-defaults-and-launch-copy；原生 SettingsMaintainer.swift:264-265, 272-273, 292-295, 330-331）。

- ``maintainer_repo_path.placeholder`` = 生效默认：config.yaml ``maintainer.repo_path``（``~`` 展开）否则本 checkout
  （``paths.repo_root()``——maintainer_launch.resolve 用的同一条）；override 不改灰字（灰字说的是「留空时用什么」）；
- ``maintainer_session_id.placeholder`` = config.yaml ``maintainer.session_id`` 设了就是它，没设保留目录里的示例句；
- 两键 zh / en 同一句（路径 / id 不分语言）；
- maintainer section 投影 add-only ``terminal_app_name`` = resolved 终端的展示名（auto → 装了 Ghostty 就 Ghostty 否则
  Terminal；``iterm2`` → ``iTerm2``，不是 ``open -a`` 用的 ``iTerm``）；其它 section 不带；
- ``POST /api/maintainer/terminal`` 回执 add-only ``terminal_app_name``（同一个答案）；open 失败 500 的 details 带 ``command``
  （原生「或手动在终端运行：」）；
- fixture 生成器把 checkout 路径灰字与终端名抹成固定值（零 diff）。
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first
from tests.test_server_common import assert_envelope, get_json, post_json, start_server, write_text

from server import maintainer_launch, paths, terminal_launch
from server import settings_catalog as catalog

_UI_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "ui")
if _UI_DIR not in sys.path:
    sys.path.insert(0, _UI_DIR)

import parity_fixture as pf  # noqa: E402

EXAMPLE = {"zh": "例：6f9619ff-8b86-d011-b42d-00cf4fc964ff", "en": "e.g. 6f9619ff-8b86-d011-b42d-00cf4fc964ff"}


class _HomeCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-maint-defaults-")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        (self.home / "state").mkdir(parents=True)
        user_home = Path(self.tmp.name) / "user"
        user_home.mkdir()
        env = mock.patch.dict(os.environ, {"HOME": str(user_home), "USERPROFILE": str(user_home)})
        env.start()
        self.addCleanup(env.stop)
        self.user_home = user_home

    def _field(self, key):
        section = catalog.section_snapshot(self.home, "maintainer")
        return next(f for f in section["fields"] if f["key"] == key)


class PlaceholderTestCase(_HomeCase):
    def test_repo_placeholder_is_this_checkout_when_nothing_is_configured(self):
        field = self._field("maintainer_repo_path")
        self.assertEqual(field["effective"], "")
        self.assertEqual(field["placeholder"], {"zh": str(paths.repo_root()), "en": str(paths.repo_root())})

    def test_repo_placeholder_follows_config_yaml_with_tilde_expanded(self):
        write_text(self.home / "config.yaml", "maintainer:\n  repo_path: ~/clones/assistant\n")
        field = self._field("maintainer_repo_path")
        expected = str(self.user_home / "clones" / "assistant")
        self.assertEqual(field["placeholder"], {"zh": expected, "en": expected})
        self.assertEqual((field["effective"], field["source"]), ("~/clones/assistant", "config"))

    def test_override_does_not_move_the_repo_placeholder(self):
        # 灰字 = 留空时用什么；override 只改 effective
        write_text(self.home / "state" / "settings_overrides.json", json.dumps({"maintainer_repo_path": str(self.home)}))
        field = self._field("maintainer_repo_path")
        self.assertEqual((field["effective"], field["source"]), (str(self.home), "override"))
        self.assertEqual(field["placeholder"]["zh"], str(paths.repo_root()))

    def test_repo_placeholder_matches_what_launch_resolves(self):
        for cfg in ("", "maintainer:\n  repo_path: %s\n" % self.home):
            with self.subTest(cfg=cfg):
                write_text(self.home / "config.yaml", cfg)
                repo, _sid = maintainer_launch.resolve(self.home)
                self.assertEqual(self._field("maintainer_repo_path")["placeholder"]["en"], str(repo))

    def test_session_placeholder_is_the_example_until_config_sets_one(self):
        self.assertEqual(self._field("maintainer_session_id")["placeholder"], EXAMPLE)
        write_text(self.home / "config.yaml", "maintainer:\n  session_id: 6f9619ff-8b86-d011-b42d-00cf4fc964ff\n")
        field = self._field("maintainer_session_id")
        self.assertEqual(field["placeholder"], {"zh": "6f9619ff-8b86-d011-b42d-00cf4fc964ff", "en": "6f9619ff-8b86-d011-b42d-00cf4fc964ff"})
        self.assertEqual(field["source"], "config")
        # override 压着时灰字仍是 config 的 id（用户清空即回到它——原生「已清空——按钮用 config.yaml 里的会话 ID（灰字）」）
        write_text(self.home / "state" / "settings_overrides.json", json.dumps({"maintainer_session_id": "abc"}))
        field = self._field("maintainer_session_id")
        self.assertEqual((field["effective"], field["placeholder"]["zh"]), ("abc", "6f9619ff-8b86-d011-b42d-00cf4fc964ff"))

    def test_blank_config_session_id_keeps_the_example(self):
        write_text(self.home / "config.yaml", "maintainer:\n  session_id: '   '\n")
        self.assertEqual(self._field("maintainer_session_id")["placeholder"], EXAMPLE)

    def test_only_the_two_maintainer_fields_are_dynamic(self):
        self.assertEqual(sorted(catalog.DYNAMIC_PLACEHOLDERS), ["maintainer_repo_path", "maintainer_session_id"])
        for key in catalog.DYNAMIC_PLACEHOLDERS:
            self.assertIn(key, catalog.field_index(catalog.lookup("maintainer")))


class TerminalNameTestCase(_HomeCase):
    def test_display_names_mirror_native_terminal_app(self):
        self.assertEqual(terminal_launch.display_name("iTerm"), "iTerm2")
        self.assertEqual(terminal_launch.display_name("Ghostty"), "Ghostty")
        self.assertEqual(terminal_launch.display_name("Terminal"), "Terminal")
        self.assertEqual(terminal_launch.display_name("Other"), "Other")
        self.assertEqual(set(terminal_launch.TERMINAL_DISPLAY_NAMES), set(terminal_launch.TERMINAL_APP_NAMES.values()))

    def test_section_carries_the_resolved_terminal_name_and_others_do_not(self):
        apps = Path(self.tmp.name) / "apps"
        apps.mkdir()
        with mock.patch.object(terminal_launch, "_APP_DIRS", (str(apps),)):
            # auto：没装 Ghostty → Terminal；装了 → Ghostty（原生 TerminalLauncher.preferred）
            self.assertEqual(catalog.section_snapshot(self.home, "maintainer")["terminal_app_name"], "Terminal")
            (apps / "Ghostty.app").mkdir()
            self.assertEqual(catalog.section_snapshot(self.home, "maintainer")["terminal_app_name"], "Ghostty")
        write_text(self.home / "state" / "settings_overrides.json", json.dumps({"terminal_app": "iterm2"}))
        self.assertEqual(catalog.section_snapshot(self.home, "maintainer")["terminal_app_name"], "iTerm2")
        snapshot = catalog.snapshot(self.home)
        with_name = [s["id"] for s in snapshot["sections"] if "terminal_app_name" in s]
        self.assertEqual(with_name, ["maintainer"])

    def test_receipt_carries_the_same_terminal_name(self):
        write_text(self.home / "state" / "settings_overrides.json", json.dumps({"terminal_app": "iterm2"}))
        receipt = maintainer_launch.launch(self.home, {}, opener=lambda p: None, out_dir=Path(self.tmp.name), platform="darwin")
        self.assertEqual(receipt["terminal_app_name"], "iTerm2")
        self.assertEqual(set(receipt), {"ok", "command", "command_file", "cwd", "terminal_app_name"})

    def test_open_failure_carries_the_manual_command(self):
        def boom(_path):
            raise OSError("no Terminal")
        with self.assertRaises(maintainer_launch.ApiError) as ctx:
            maintainer_launch.launch(self.home, {}, opener=boom, out_dir=Path(self.tmp.name), platform="darwin")
        self.assertEqual(ctx.exception.status, 500)
        self.assertEqual(ctx.exception.details["command"], maintainer_launch.command_for(paths.repo_root(), ""))
        self.assertIn("command_file", ctx.exception.details)


class RouteTestCase(_HomeCase):
    def setUp(self):
        super().setUp()
        _httpd, self.port = start_server(self, self.home)

    def test_get_settings_and_post_receipt_over_http(self):
        write_text(self.home / "state" / "settings_overrides.json", json.dumps({"terminal_app": "terminal"}))
        _s, section = get_json(self.port, "/api/settings/maintainer")
        self.assertEqual(section["terminal_app_name"], "Terminal")
        repo = next(f for f in section["fields"] if f["key"] == "maintainer_repo_path")
        self.assertEqual(repo["placeholder"]["zh"], str(paths.repo_root()))
        with mock.patch.object(maintainer_launch.sys, "platform", "darwin"), \
                mock.patch.object(terminal_launch, "_default_opener", lambda _path, _app=None: None):
            status, receipt = post_json(self.port, "/api/maintainer/terminal", {})
        self.assertEqual(status, 200)
        self.assertEqual(receipt["terminal_app_name"], "Terminal")

    def test_open_failure_route_is_500_with_the_manual_command(self):
        def boom(_path, _app=None):
            raise OSError("no Terminal")
        with mock.patch.object(maintainer_launch.sys, "platform", "darwin"), \
                mock.patch.object(terminal_launch, "_default_opener", boom):
            status, obj = post_json(self.port, "/api/maintainer/terminal", {})
        self.assertEqual(status, 500)
        assert_envelope(self, obj, "INTERNAL_ERROR")
        self.assertTrue(obj["error"]["details"]["command"].endswith("&& claude"))


class FixtureScrubTestCase(unittest.TestCase):
    def test_fixture_scrubs_the_machine_dependent_values(self):
        snap = pf.build_settings()
        section = next(s for s in snap["sections"] if s["id"] == "maintainer")
        self.assertEqual(section["terminal_app_name"], pf._FIXTURE_TERMINAL_APP_NAME)
        repo = next(f for f in section["fields"] if f["key"] == "maintainer_repo_path")
        self.assertEqual(repo["placeholder"], {"zh": pf._FIXTURE_REPO_PLACEHOLDER, "en": pf._FIXTURE_REPO_PLACEHOLDER})
        self.assertNotIn(str(paths.repo_root()), json.dumps(snap))
        sid = next(f for f in section["fields"] if f["key"] == "maintainer_session_id")
        self.assertEqual(sid["placeholder"], EXAMPLE)


if __name__ == "__main__":
    unittest.main()
