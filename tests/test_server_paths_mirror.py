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

from act import actd, doctor, llm, recap
from act.lib import config, heartbeat, registry
from act.lib import recap_store, recap_text
from server import health as server_health
from server import inbox_writer as server_inbox
from server import paths
from server import recaps as server_recaps
from server.errors import InvalidFieldError
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
    """§59：server/settings.py 手抄的模型旋钮常量与 act/lib/config.py 逐字一致
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


class RecapMirrorTestCase(unittest.TestCase):
    """§63：server/recaps.py 与 server/inbox_writer.py 手抄的 recap 键形、语言词表、
    §63.10 形状词表、override 键名、marks 路径与 bool 归一必须与 act 侧逐字一致。"""

    def test_key_and_channel_shapes(self):
        self.assertEqual(server_recaps.KEY_RE.pattern, recap_store.KEY_RE.pattern)
        self.assertEqual(server_inbox._RECAP_KEY_RE.pattern, recap_store.KEY_RE.pattern)
        self.assertEqual(server_inbox._SLACK_CHANNEL_RE.pattern, recap_store.CHANNEL_ID_RE.pattern)
        self.assertEqual(server_inbox._RECAP_NOTE_MAX, 500)

    def test_languages_defaults_and_override_keys(self):
        self.assertEqual(server_recaps.LANGUAGES, config.RECAP_LANGUAGES)
        cfg = config.Config()
        self.assertEqual(server_recaps.DEFAULTS, {
            "enabled": cfg.recap_enabled, "default_language": cfg.recap_default_language,
            "slack_draft_enabled": cfg.recap_slack_draft_enabled,
            # §63.10：出厂形状与 act 侧同一个字面量（config.yaml 层，无 overrides 扁平键）
            "default_shape": recap_text.DEFAULT_SHAPE})
        for key in server_recaps.OVERRIDE_KEYS.values():
            self.assertIn(key, config._OVERRIDE_FIELDS)
        # 只读的那一格永不进 PUT 的白名单——写进 overrides 的键管线根本不读
        self.assertNotIn("default_shape", server_recaps.OVERRIDE_KEYS)

    def test_shape_vocabulary_mirrors_the_daemon(self):
        """§63.10：两个 server 模块各手抄了一份形状词表——第三个字面量出现时必须红。

        没有这道 pin，`recap_text.SHAPES` 加一个形状（或换掉第一个）之后
        `POST /api/actions` 会 400 掉那个新形状、`_version_shape` 会把每一条老
        history 条目标成错的形状，而全量测试一片绿。"""
        self.assertEqual(server_inbox._RECAP_SHAPES, recap_text.SHAPES)
        self.assertEqual(server_recaps.SHAPES, recap_text.SHAPES)
        # `_version_shape` / DEFAULTS 拿 SHAPES[0] 当兜底形状 = act 侧的默认形
        self.assertEqual(server_recaps.SHAPES[0], recap_text.DEFAULT_SHAPE)

    def test_marks_path_mirror(self):
        with mock.patch.object(config, "STATE_DIR", HOME / "state"):
            self.assertEqual(server_recaps.marks_path(HOME), recap_store.marks_path())

    def test_recap_file_path_mirror(self):
        """§63.9：GET /api/recaps/history 读的那个文件名与 act 侧逐字同一个（key 的 ':' → '_'）。"""
        key = "meeting:2026-08-31T1256-zoom"
        with mock.patch.object(config, "STATE_DIR", HOME / "state"):
            self.assertEqual(server_recaps.recap_file_path(HOME, key), recap_store.recap_path(key))

    def test_recap_file_path_refuses_a_key_that_is_not_a_key(self):
        # 客户端永不指名路径：KEY_RE 之外一律 400，路径根本不被拼出来
        for key in (None, "", "R-101", "meeting:../../etc/passwd", "meeting:2026-08-31T1256-zoom/x"):
            with self.subTest(key=key):
                with self.assertRaises(InvalidFieldError):
                    server_recaps.recap_file_path(HOME, key)

    def test_history_cap_mirrors_the_daemon(self):
        self.assertEqual(server_recaps.HISTORY_CAP, recap.HISTORY_CAP)

    def test_coerce_bool_agrees_on_a_table(self):
        table = (True, False, 0, 1, "true", "FALSE", " on ", "off", "yes", "no", 2, 1.0, "maybe", None, [])
        for value in table:
            with self.subTest(value=value):
                try:
                    a = ("ok", config._coerce_bool(value))
                except (TypeError, ValueError):
                    a = ("err", None)
                try:
                    b = ("ok", server_recaps.coerce_bool(value))
                except (TypeError, ValueError):
                    b = ("err", None)
                self.assertEqual(a, b)


class DailyLoopSettingsMirrorTestCase(unittest.TestCase):
    """§70：server/settings.py 手抄的每日循环旋钮常量与 act/lib/config.py 逐字一致。"""

    def test_defaults_and_keys_mirror_config(self):
        cfg = config.Config()
        for field in server_settings.DAILY_LOOP_FIELDS:
            self.assertEqual(server_settings.DAILY_LOOP_DEFAULTS[field], getattr(cfg, f"daily_loop_{field}"))
            self.assertIn(server_settings.DAILY_LOOP_KEY % field, config._OVERRIDE_FIELDS)
        self.assertEqual(server_settings.CLOCK_TIME_RE.pattern, config.CLOCK_TIME_RE.pattern)
        self.assertEqual(server_settings.DAILY_LOOP_DEFAULTS["time"], config.DEFAULT_DAILY_LOOP_TIME)

    def test_coercers_agree_on_a_table(self):
        table = {
            "enabled": (True, False, 0, 1, 2, "yes", "OFF", "maybe", None, [], 1.0),
            "time": ("03:30", "3:30", " 23:59 ", "24:00", "3:5", "noon", None, 330),
            "max_proposals_per_day": (0, 5, -1, "7", "x", True, None, 2.5, []),
        }
        for field, values in table.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    try:
                        a = ("ok", config._OVERRIDE_FIELDS["daily_loop_%s" % field](value))
                    except (TypeError, ValueError):
                        a = ("err", None)
                    try:
                        b = ("ok", server_settings.coerce_daily_loop(field, value))
                    except (TypeError, ValueError):
                        b = ("err", None)
                    self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
