"""server/paths.py 的布局镜像 drift-pin（BUILD-CONTRACT §2.3；CONTRACT §44）。

server/ **绝不 import act**（单写者纪律：act.lib.config import 期就带写路径），
所以 paths.py 手抄了 act/lib/config.py 的默认 HOME 与五个只读路径布局。生产侧
不能 import，**测试侧可以**——这里就是那道 pin：任何一方改了默认路径或目录
布局而另一方没跟上，本文件立刻红（否则症状是 server 静默读一个空目录/读不到
看板，而两边代码各自看着都对）。
"""
import os
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env 先于任何 act.* import

from act import actd, doctor, llm
from act.lib import config, heartbeat, registry
from server import health as server_health
from server import paths
from server import settings as server_settings

HOME = Path("/tmp/zai-paths-pin")


class DefaultHomeMirrorTestCase(unittest.TestCase):
    """AIASSISTANT_HOME 缺省时两侧必须落在同一个目录。"""

    def test_default_home_literal_matches_config(self):
        env = {k: v for k, v in os.environ.items() if k != "AIASSISTANT_HOME"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(paths.home_dir(), config._home())
            self.assertEqual(Path(paths.DEFAULT_HOME).expanduser(),
                             config._home())

    def test_env_override_agrees_with_config(self):
        with mock.patch.dict(os.environ, {"AIASSISTANT_HOME": str(HOME)}):
            self.assertEqual(paths.home_dir(), config._home())


class LayoutMirrorTestCase(unittest.TestCase):
    """五个只读路径的 HOME-相对布局与 config/registry 逐字一致。"""

    def _rel(self, absolute: Path) -> Path:
        """config 的常量是 import 期算的（HOME = 沙箱 TMP_HOME）——取相对形
        才能与 paths.* 的显式 home 参数比。"""
        return absolute.relative_to(config.HOME)

    def test_dashboard_registry_inbox_layout_matches_config(self):
        cases = (
            (paths.dashboard_path(HOME), config.DASHBOARD_PATH),
            (paths.registry_dir(HOME), config.REGISTRY_DIR),
            (paths.inbox_dir(HOME), config.INBOX_DIR),
            # §53.6 回滚开关：board_source 真源判定读的 config.yaml 必须
            # 就是 act 侧写规则的那一份
            (paths.config_path(HOME), config.CONFIG_PATH),
        )
        for got, expected in cases:
            self.assertEqual(got, HOME / self._rel(expected))

    def test_archive_dir_matches_registry_constant(self):
        self.assertEqual(paths.archive_dir(HOME),
                         HOME / self._rel(registry.ARCHIVE_DIR))

    def test_health_files_match_their_writers(self):
        # §47.4 heartbeat + §47.3 loop_health: server/health.py reads what
        # actd writes — the names live in exactly two places, pinned here.
        self.assertEqual(paths.heartbeat_path(HOME),
                         HOME / self._rel(heartbeat.HEARTBEAT_PATH))
        self.assertEqual(paths.loop_health_path(HOME),
                         HOME / "state" / actd.LOOP_HEALTH_NAME)

    def test_health_thresholds_mirror_the_python_side(self):
        self.assertEqual(server_health.LOOP_ALARM_AFTER, actd.LOOP_ALARM_AFTER)
        self.assertEqual(server_health.DASHBOARD_FRESH_SECONDS,
                         doctor.DASHBOARD_FRESH_SECONDS)
        self.assertEqual(server_health.DASHBOARD_FRESH_SECONDS,
                         heartbeat.STALE_FLOOR_SECONDS)


class ModelSettingsMirrorTestCase(unittest.TestCase):
    """§57：server/settings.py 手抄的模型旋钮常量与 act/lib/config.py 逐字一致
    ——两侧对「什么是合法旋钮值」意见不一，web 就会写出 daemon 忽略的键。"""

    def test_constants_mirror_config(self):
        self.assertEqual(server_settings.MODEL_FOLLOW, config.MODEL_FOLLOW)
        self.assertEqual(server_settings.MODEL_MODES, config.MODEL_MODES)
        self.assertEqual(server_settings.CANONICAL_MODELS, config.CANONICAL_MODELS)
        self.assertEqual(server_settings.MODEL_ID_RE.pattern, config.MODEL_ID_RE.pattern)

    def test_override_key_is_what_the_pipeline_reads(self):
        for mode in config.MODEL_MODES:
            self.assertIn(server_settings.OVERRIDE_KEY % mode, config._OVERRIDE_FIELDS)

    def test_paths_mirror(self):
        self.assertEqual(server_settings.settings_overrides_path(HOME),
                         HOME / config.SETTINGS_OVERRIDES_PATH.relative_to(config.HOME))
        self.assertEqual(server_settings.claude_code_settings_path(),
                         llm.claude_code_settings_path())

    def test_coerce_model_agrees_on_a_table(self):
        table = (None, "", "  ", "follow", "FOLLOW", " claude-opus-5 ",
                 "claude-fable-5-1[1m]", "has space", "-lead", "a" * 65, 12, True,
                 "x\ny", "q'uote")
        for value in table:
            with self.subTest(value=value):
                try:
                    a = ("ok", config.coerce_model(value))
                except ValueError:
                    a = ("err", None)
                try:
                    b = ("ok", server_settings.coerce_model(value))
                except ValueError:
                    b = ("err", None)
                self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
