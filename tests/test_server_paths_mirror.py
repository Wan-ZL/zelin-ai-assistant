"""server/ 手抄常量与路径布局的 drift-pin（BUILD-CONTRACT §2.3；CONTRACT §49 / §58.3）。

server/paths.py、server/settings.py、server/recaps.py、server/health.py 手抄了
act 侧的默认 HOME、只读路径布局、模型旋钮词表与 recap 键形 / 词表（server 的
请求路径不 import act.lib.config——它 import 期就带写路径）。测试侧可以 import
两边——这里就是那道 pin：任何一方改了默认路径或词表而另一方没跟上，本文件立刻红
（否则症状是 server 静默读一个空目录 / 写出 daemon 忽略的键，而两边代码各自看着都对）。

v2.0.0：dashboard / registry / inbox / heartbeat / loop_health 路径、daily-loop 旋钮、
inbox_writer 的 recap 特形与 follow 模式随 Act 管线退役（全文见 tag full）。
"""
import os
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env 先于任何 act.* import

from act import recap
from act.lib import config, recap_intent, recap_store, recap_text, secrets, update_check
from act.lib.checks import cron as checks_cron
from server import health as server_health
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
    """server 只读的 HOME-相对布局与 act 侧的写方逐字一致。"""

    def _rel(self, absolute: Path) -> Path:
        """act 的常量是 import 期算的（HOME = 沙箱 TMP_HOME）——取相对形
        才能与 paths.* 的显式 home 参数比。"""
        return absolute.relative_to(config.HOME)

    def test_layout_matches_the_writers(self):
        cases = (
            (paths.config_path(HOME), config.CONFIG_PATH),
            (paths.settings_overrides_path(HOME), config.SETTINGS_OVERRIDES_PATH),
            (paths.secrets_dir(HOME), secrets.SECRETS_DIR),
            (paths.update_check_path(HOME), update_check.STATE_PATH),
            (paths.cron_probe_path(HOME), checks_cron.CRON_PROBE_PATH),
            (paths.recap_log_path(HOME), recap_store.log_path()),
        )
        for got, expected in cases:
            with self.subTest(expected=str(expected)):
                self.assertEqual(got, HOME / self._rel(expected))

    def test_health_reads_the_recap_layer_and_the_cron_probe(self):
        """§49：server/health.py 的活性 = cron 链探针新鲜度 + 上次 recap 一轮。"""
        self.assertEqual(server_health.CRON_FRESH_SECONDS, checks_cron.CRON_PROBE_FRESH_SECONDS)


class ModelSettingsMirrorTestCase(unittest.TestCase):
    """§59：server/settings.py 手抄的模型旋钮常量与 act/lib/config.py 逐字一致
    ——两侧对「什么是合法旋钮值」意见不一，web 就会写出 daemon 忽略的键。"""

    def test_constants_mirror_config(self):
        self.assertEqual(server_settings.LEGACY_MODEL_FOLLOW, config.LEGACY_MODEL_FOLLOW)
        self.assertEqual((server_settings.MODEL_PIPELINE,), config.MODEL_MODES)
        self.assertEqual(server_settings.CANONICAL_MODELS, config.CANONICAL_MODELS)
        self.assertEqual(server_settings.DEFAULT_MODEL_PIPELINE, config.DEFAULT_MODEL_PIPELINE)
        self.assertEqual(server_settings.DEFAULT_MODEL_FALLBACK, config.DEFAULT_MODEL_FALLBACK)
        self.assertEqual(server_settings.MODEL_FALLBACK_OFF, config.MODEL_FALLBACK_OFF)
        self.assertEqual(server_settings.MODEL_ID_RE.pattern, config.MODEL_ID_RE.pattern)
        self.assertEqual(server_settings.CLOCK_TIME_RE.pattern, config.CLOCK_TIME_RE.pattern)

    def test_override_key_is_what_the_pipeline_reads(self):
        for knob in server_settings.MODEL_KNOBS:
            self.assertIn(server_settings.OVERRIDE_KEY % knob, config._OVERRIDE_FIELDS)

    def test_paths_mirror(self):
        self.assertEqual(server_settings.settings_overrides_path(HOME),
                         HOME / config.SETTINGS_OVERRIDES_PATH.relative_to(config.HOME))
        # §78: there is no reader of the owner's Claude Code settings on either side
        self.assertFalse(hasattr(server_settings, "claude_code_settings_path"))

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
    """§63：server/recaps.py 手抄的 recap 键形、语言词表、§63.10 形状词表、§63.11 答案形状、
    override 键名、marks 路径与 bool 归一必须与 act 侧逐字一致。"""

    def test_key_shape_and_note_cap(self):
        self.assertEqual(server_recaps.KEY_RE.pattern, recap_store.KEY_RE.pattern)
        self.assertEqual(server_recaps.NOTE_MAX, 500)
        self.assertEqual(server_recaps.LOG_NAME, recap_store.log_path().name)

    def test_languages_defaults_and_override_keys(self):
        self.assertEqual(server_recaps.LANGUAGES, config.RECAP_LANGUAGES)
        cfg = config.Config()
        self.assertEqual(server_recaps.DEFAULTS, {
            "enabled": cfg.recap_enabled, "default_language": cfg.recap_default_language,
            # §63.10：出厂形状与 act 侧同一个字面量（config.yaml 层，无 overrides 扁平键）
            "default_shape": recap_text.DEFAULT_SHAPE})
        for key in server_recaps.OVERRIDE_KEYS.values():
            self.assertIn(key, config._OVERRIDE_FIELDS)
        # 只读的那一格永不进 PUT 的白名单——写进 overrides 的键管线根本不读
        self.assertNotIn("default_shape", server_recaps.OVERRIDE_KEYS)

    def test_shape_vocabulary_mirrors_the_daemon(self):
        """§63.10：server 手抄了一份形状词表——第三个字面量出现时必须红。

        没有这道 pin，`recap_text.SHAPES` 加一个形状（或换掉第一个）之后
        `POST /api/recaps/generate` 会 400 掉那个新形状、`_version_shape` 会把每一条老
        history 条目标成错的形状，而全量测试一片绿。"""
        self.assertEqual(server_recaps.SHAPES, recap_text.SHAPES)
        # `_version_shape` / DEFAULTS 拿 SHAPES[0] 当兜底形状 = act 侧的默认形
        self.assertEqual(server_recaps.SHAPES[0], recap_text.DEFAULT_SHAPE)

    def test_answer_shape_mirrors_the_daemon(self):
        """§63.11：`answers` 的一条形状与条数上限在两侧各有一份手抄——必须逐字一致。

        server 的形状闸只查形状；id 与选项的**闭表**住 act 侧
        （`recap_intent.answers_ok`，经 `recap_store.request_argv`）。没有这道 pin，act 侧改一
        次正则（比如以后允许两位数的 id 后缀）之后 `POST /api/recaps/generate` 会 400 掉一组
        完全合法的答案，而全量测试一片绿。"""
        self.assertEqual(server_recaps.ANSWER_RE.pattern, recap_intent.ANSWER_RE.pattern)
        self.assertEqual(server_recaps.ANSWERS_MAX, recap_intent.MAX_ANSWERS)
        # 问出来的每一条都必须答得上去（面板不许给一个送不出去的答案格）
        self.assertLessEqual(recap_intent.MAX_QUESTIONS, server_recaps.ANSWERS_MAX)

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
                    a = ("ok", config.coerce_bool(value))
                except (TypeError, ValueError):
                    a = ("err", None)
                try:
                    b = ("ok", server_recaps.coerce_bool(value))
                except (TypeError, ValueError):
                    b = ("err", None)
                self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
