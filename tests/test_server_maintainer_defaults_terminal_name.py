"""§68.7 追记：开发者区两行的**生效默认灰字**与「会在 <终端> 中打开」的终端名（parity 批 maintainer-rows，
gap settings-maintainer-defaults-and-launch-copy；原生 SettingsMaintainer.swift:264-265, 272-273, 292-295, 330-331）。

- ``maintainer_repo_path.placeholder`` = 生效默认：config.yaml ``maintainer.repo_path``（``~`` 展开）否则本 checkout
  （``paths.repo_root()``——maintainer_launch.resolve 用的同一条）；override 不改灰字（灰字说的是「留空时用什么」）；
- ``maintainer_session_id.placeholder`` = config.yaml ``maintainer.session_id`` 设了就是它，没设保留目录里的示例句；
- 两键 zh / en 同一句（路径 / id 不分语言）；
- maintainer section 投影 add-only ``terminal_app_name`` = resolved 终端的展示名（显式选择装了才算；auto / 选了没装的 →
  装了 Ghostty 就 Ghostty 否则 Terminal——壳 ``TerminalLauncher.resolve`` 逐字同一条规则；``iterm2`` 装了 → ``iTerm2``，
  不是 ``open -a`` 用的 ``iTerm``）；其它 section 不带；
- ``POST /api/maintainer/terminal`` 回执 add-only ``terminal_app_name``（同一个答案）；壳没在跑 503 / 入队失败 500 的 details
  带 ``command``（原生「或手动在终端运行：」；§68.7 2026-09-05 起走队列通道，server 不写 .command 不 open）；
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

    def _beat(self):
        """壳在跑 = state/shell.heartbeat 新鲜（§68.7 队列的消费者）——启动路才会入队而不是 503。"""
        paths.shell_heartbeat_path(self.home).write_text("pid=1\n", encoding="utf-8")


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

    def _apps(self):
        """假的 /Applications：``_APP_DIRS`` 只指到它，装没装终端由测试摆 ``<Name>.app`` 目录决定。"""
        apps = Path(self.tmp.name) / "apps"
        apps.mkdir(exist_ok=True)
        patch = mock.patch.object(terminal_launch, "_APP_DIRS", (str(apps),))
        patch.start()
        self.addCleanup(patch.stop)
        return apps

    def test_resolve_terminal_mirrors_the_shell_and_native_preferred(self):
        """壳 ``TerminalLauncher.resolve(setting:installed:)``（shell/tests/run.sh 第 7 节六例）逐字同一条规则：显式选择装了才算，
        auto / 未知 / 选了没装的 → 装了 Ghostty 就 Ghostty 否则 Terminal——否则「会在 iTerm2 中打开」会说一个壳不会开的终端。"""
        every = lambda _n: True  # noqa: E731
        none = lambda _n: False  # noqa: E731
        only_terminal = lambda n: n == "Terminal"  # noqa: E731
        self.assertEqual(terminal_launch.resolve_terminal("auto", every), "Ghostty")
        self.assertEqual(terminal_launch.resolve_terminal("auto", only_terminal), "Terminal")
        self.assertEqual(terminal_launch.resolve_terminal("iterm2", every), "iTerm")
        self.assertEqual(terminal_launch.resolve_terminal("iterm2", only_terminal), "Terminal")   # 选了没装的 = auto
        self.assertEqual(terminal_launch.resolve_terminal("iterm2", none), "Terminal")
        self.assertEqual(terminal_launch.resolve_terminal("ghostty", only_terminal), "Terminal")
        self.assertEqual(terminal_launch.resolve_terminal("terminal", every), "Terminal")
        self.assertEqual(terminal_launch.resolve_terminal("bogus", every), "Ghostty")
        self.assertEqual(terminal_launch.resolve_terminal("", only_terminal), "Terminal")

    def test_section_carries_the_resolved_terminal_name_and_others_do_not(self):
        apps = self._apps()
        # auto：没装 Ghostty → Terminal；装了 → Ghostty（原生 TerminalLauncher.preferred）
        self.assertEqual(catalog.section_snapshot(self.home, "maintainer")["terminal_app_name"], "Terminal")
        (apps / "Ghostty.app").mkdir()
        self.assertEqual(catalog.section_snapshot(self.home, "maintainer")["terminal_app_name"], "Ghostty")
        # 选了 iTerm2 但没装 → 壳会开 Ghostty，帮助句也说 Ghostty；装上才是 iTerm2
        write_text(self.home / "state" / "settings_overrides.json", json.dumps({"terminal_app": "iterm2"}))
        self.assertEqual(catalog.section_snapshot(self.home, "maintainer")["terminal_app_name"], "Ghostty")
        (apps / "iTerm.app").mkdir()
        self.assertEqual(catalog.section_snapshot(self.home, "maintainer")["terminal_app_name"], "iTerm2")
        snapshot = catalog.snapshot(self.home)
        with_name = [s["id"] for s in snapshot["sections"] if "terminal_app_name" in s]
        self.assertEqual(with_name, ["maintainer"])

    def test_receipt_carries_the_same_terminal_name(self):
        self._beat()
        (self._apps() / "iTerm.app").mkdir()
        write_text(self.home / "state" / "settings_overrides.json", json.dumps({"terminal_app": "iterm2"}))
        receipt = maintainer_launch.launch(self.home, {}, platform="darwin")
        self.assertEqual(receipt["terminal_app_name"], "iTerm2")
        self.assertEqual(set(receipt), {"ok", "command", "command_file", "cwd", "queue_id", "terminal_app_name"})

    def test_shell_unavailable_and_queue_failure_carry_the_manual_command(self):
        # 壳没在跑（没有心跳）→ 503，details 带手动命令（原生「或手动在终端运行：」）
        with self.assertRaises(terminal_launch.ShellUnavailableError) as ctx:
            maintainer_launch.launch(self.home, {}, platform="darwin")
        self.assertEqual(ctx.exception.status, 503)
        self.assertEqual(ctx.exception.details["command"], maintainer_launch.command_for(paths.repo_root(), ""))
        # 壳在跑但队列目录被普通文件占着 → 入队 500，同样带手动命令 + 队列目录
        self._beat()
        paths.terminal_queue_dir(self.home).write_text("not a dir", encoding="utf-8")
        with self.assertRaises(maintainer_launch.ApiError) as ctx:
            maintainer_launch.launch(self.home, {}, platform="darwin")
        self.assertEqual(ctx.exception.status, 500)
        self.assertEqual(ctx.exception.details["command"], maintainer_launch.command_for(paths.repo_root(), ""))
        self.assertIn("queue_dir", ctx.exception.details)


class RouteTestCase(_HomeCase):
    def setUp(self):
        super().setUp()
        _httpd, self.port = start_server(self, self.home)

    def test_get_settings_and_post_receipt_over_http(self):
        self._beat()
        write_text(self.home / "state" / "settings_overrides.json", json.dumps({"terminal_app": "terminal"}))
        _s, section = get_json(self.port, "/api/settings/maintainer")
        self.assertEqual(section["terminal_app_name"], "Terminal")
        repo = next(f for f in section["fields"] if f["key"] == "maintainer_repo_path")
        self.assertEqual(repo["placeholder"]["zh"], str(paths.repo_root()))
        with mock.patch.object(maintainer_launch.sys, "platform", "darwin"):
            status, receipt = post_json(self.port, "/api/maintainer/terminal", {})
        self.assertEqual(status, 200)
        self.assertEqual(receipt["terminal_app_name"], "Terminal")
        self.assertEqual(Path(receipt["command_file"]).name, receipt["queue_id"] + ".json")

    def test_shell_unavailable_route_is_503_with_the_manual_command(self):
        with mock.patch.object(maintainer_launch.sys, "platform", "darwin"):
            status, obj = post_json(self.port, "/api/maintainer/terminal", {})
        self.assertEqual(status, 503)
        assert_envelope(self, obj, "SHELL_UNAVAILABLE")
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
