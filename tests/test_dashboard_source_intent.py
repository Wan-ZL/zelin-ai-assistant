"""§48.4 意愿信号：``radar_sources.<src>.intent`` / ``secret_present``（add-only）。

原生 Diagnostics.swift:161-168 + DiagnosticsRules.gmailCardEligible 的 web 版
把「用户真的动过这个源」的判断搬进 actd 投影（§44 单写者；web 只读）：

- ``intent`` = 碰过开关（settings_overrides 里 ``<src>_enabled`` / 点式
  ``"sources.<src>_enabled"`` / 嵌套 ``features.<src>_radar`` / 平铺
  ``"features.<src>_radar"`` / 仅 gmail 的 ``gmail: {enabled}``——恰好是 config
  ``_apply_settings_overrides`` 认的拼法，开或关都算）∨ 凭证文件存在（哪怕为空
  = 配到一半）∨ 凭证非空；obsidian 的「配到一半」= 指定过 vault 目录。
- ``secret_present`` = §19 凭证非空，三层与雷达 get_token / get_app_password
  同一套（secrets 文件 → config.yaml 路径 → 旧默认路径 ``~/Desktop/Keys``）：
  雷达能解析到 token 才写得出 ``connect_failed``，旧路径用户那张「Slack token
  无效」卡不能因为投影少看一层而消失；旧默认路径经 read_path 探、不响弃用告警。
  obsidian 恒 False。
- 全新安装（没 overrides、没凭证）三源皆 False —— 这就是 anti-nag 的那张牌：
  「enabled 默认 true」本身不是 intent。
- 坏 overrides / 探不到 → False，投影绝不崩。
"""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first

from act import radar_gmail, radar_slack   # import 前表未 patch：常量绑定的是真值
from act.lib import config, dashboard, radar_health, secrets, sources


class _Cfg(config.Config):
    def feature(self, name):  # noqa: D401 - test double: every flag on
        return True


class SourceIntentSignalsTestCase(unittest.TestCase):
    def setUp(self):
        root = Path(tempfile.mkdtemp(prefix="intent-"))
        self.overrides = root / "settings_overrides.json"
        self.secrets_dir = root / "secrets"
        self.secrets_dir.mkdir()
        # §19 第三层（旧默认路径）指进沙箱：tests/__init__.py 只沙箱 AIASSISTANT_HOME，
        # 不沙箱 ~，开发机 ~/Desktop/Keys 的真凭证绝不能成为判例输入
        self.legacy_dir = root / "legacy-keys"
        self.legacy_dir.mkdir()
        legacy = {n: str(self.legacy_dir / n) for n in secrets.LEGACY_DEFAULT_PATHS}
        for patcher in (
            mock.patch.object(dashboard.config, "SETTINGS_OVERRIDES_PATH", self.overrides),
            mock.patch.object(secrets, "SECRETS_DIR", self.secrets_dir),
            mock.patch.object(secrets, "LEGACY_DEFAULT_PATHS", legacy),
            mock.patch.object(radar_health, "load_radar_health", return_value={}),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _project(self, cfg=None) -> dict:
        cfg = cfg or _Cfg()
        with mock.patch.object(dashboard.config, "load_config", return_value=cfg):
            return dashboard._radar_sources(cfg)

    def _write_overrides(self, doc) -> None:
        self.overrides.write_text(doc if isinstance(doc, str) else json.dumps(doc),
                                  encoding="utf-8")

    # ---- fresh install: nothing intended -----------------------------------
    def test_fresh_install_has_no_intent_anywhere(self):
        rs = self._project()
        self.assertEqual(set(rs), set(sources.SOURCES))
        for src in sources.SOURCES:
            self.assertTrue(rs[src]["enabled"], src)         # 默认全开……
            self.assertFalse(rs[src]["intent"], src)         # ……但没人碰过 = 没 intent
            self.assertFalse(rs[src]["secret_present"], src)

    # ---- switch touched: every spelling config accepts --------------------
    def test_switch_touched_flat_source_key_either_direction(self):
        self._write_overrides({"gmail_enabled": False})
        cfg = _Cfg()
        cfg.gmail_enabled = False                            # config 合成后的结果
        rs = self._project(cfg)
        self.assertFalse(rs["gmail"]["enabled"])
        self.assertTrue(rs["gmail"]["intent"])               # 关掉也是碰过
        self.assertFalse(rs["gmail"]["secret_present"])
        self.assertFalse(rs["slack"]["intent"])              # 别的源不沾光

    def test_switch_touched_nested_feature_flag(self):
        self._write_overrides({"features": {"slack_radar": True}})
        rs = self._project()
        self.assertTrue(rs["slack"]["intent"])
        self.assertFalse(rs["gmail"]["intent"])

    def test_switch_touched_flat_feature_flag_spelling(self):
        self._write_overrides({"features.obsidian_radar": False})
        rs = self._project()
        self.assertTrue(rs["obsidian"]["intent"])
        self.assertFalse(rs["obsidian"]["secret_present"])   # obsidian 无凭证概念

    def test_switch_touched_nested_source_block(self):
        self._write_overrides({"gmail": {"enabled": True, "address": "me@example.com"}})
        self.assertTrue(self._project()["gmail"]["intent"])

    def test_switch_touched_dotted_sources_key(self):
        # config._override_sources_key 认 "sources.<src>_enabled"（手写点式）——
        # 它真的关掉了源，所以也真的算碰过
        self._write_overrides({"sources.gmail_enabled": False})
        self.assertFalse(config.load_config().gmail_enabled)
        rs = self._project()
        self.assertTrue(rs["gmail"]["intent"])
        self.assertFalse(rs["slack"]["intent"])

    def test_nested_source_block_counts_only_for_gmail(self):
        # _OVERRIDE_HANDLERS 只有 gmail 的嵌套处理器：slack / obsidian 的
        # `<src>: {enabled}` 对 config 是无效键（源照旧开着）——不算碰过，
        # 拼法表与 config 一致，不多不少
        self._write_overrides({"slack": {"enabled": False}, "obsidian": {"enabled": False}})
        cfg = config.load_config()
        self.assertTrue((cfg.slack_enabled, cfg.obsidian_enabled) == (True, True))
        rs = self._project()
        self.assertFalse(rs["slack"]["intent"])
        self.assertFalse(rs["obsidian"]["intent"])

    def test_unrelated_override_keys_are_not_intent(self):
        self._write_overrides({"language": "en", "features": {"digest": False},
                               "gmail": {"address": "me@example.com"}})
        rs = self._project()
        for src in sources.SOURCES:
            self.assertFalse(rs[src]["intent"], src)

    # ---- credential files -------------------------------------------------
    def test_empty_secret_file_is_intent_but_not_secret_present(self):
        (self.secrets_dir / secrets.SLACK_TOKEN_FILE).write_text("\n", encoding="utf-8")
        sl = self._project()["slack"]
        self.assertTrue(sl["intent"])                        # 配到一半（原生 slackStarted）
        self.assertFalse(sl["secret_present"])

    def test_non_empty_secret_file_is_both(self):
        (self.secrets_dir / secrets.GMAIL_APP_PASSWORD_FILE).write_text(
            "abcd efgh ijkl mnop\n", encoding="utf-8")
        gm = self._project()["gmail"]
        self.assertEqual((gm["intent"], gm["secret_present"]), (True, True))
        self.assertFalse(self._project()["slack"]["secret_present"])

    def test_explicit_config_path_counts_as_secret_present(self):
        token = Path(tempfile.mkdtemp(prefix="tok-")) / "slack.txt"
        token.write_text("xoxp-explicit\n", encoding="utf-8")
        cfg = _Cfg()
        cfg.slack_token_path = str(token)
        sl = self._project(cfg)["slack"]
        self.assertEqual((sl["intent"], sl["secret_present"]), (True, True))

    # ---- §19 third tier: the legacy default path the radars still resolve ----
    def test_legacy_default_path_counts_as_secret_present_without_warning(self):
        # radar_slack.get_token 会从旧默认路径解析到 token → 投影必须同样报 present；
        # 探针走 read_path，不触发 resolve_credential 那条弃用告警（stderr 静默，
        # _warned_legacy 不变）
        (self.legacy_dir / secrets.SLACK_TOKEN_FILE).write_text("xoxp-legacy\n", encoding="utf-8")
        secrets._warned_legacy.clear()
        self.addCleanup(secrets._warned_legacy.clear)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            sl = self._project()["slack"]
        self.assertEqual((sl["intent"], sl["secret_present"]), (True, True))
        self.assertEqual(err.getvalue(), "")
        self.assertEqual(secrets._warned_legacy, set())
        self.assertFalse(self._project()["gmail"]["secret_present"])   # 另一源不沾光

    def test_legacy_slack_token_keeps_connect_failed_visible(self):
        # 回归钉：token 只在旧路径、Slack 拒绝它 → 雷达写 connect_failed（那条分支
        # 只在解析到 token 时可达）——web 的「Slack token 无效」卡靠 secret_present
        # 出，投影少看一层 = 主干上可见的失败在 web 上静默
        (self.legacy_dir / secrets.SLACK_TOKEN_FILE).write_text("xoxp-rejected\n", encoding="utf-8")
        with mock.patch.object(radar_health, "load_radar_health",
                               return_value={"slack": {"skip_reason": "connect_failed"}}):
            sl = self._project()["slack"]
        self.assertEqual(sl["skip_reason"], "connect_failed")
        self.assertTrue(sl["secret_present"])

    def test_legacy_gmail_password_is_intent_for_no_address(self):
        # gmail_app_password_path 的缺省值就是旧默认路径：应用密码只在那里、没填
        # 地址 → 雷达写 no_address（setup 类，web 要 intent）——凭证在 = 显然配过
        cfg = _Cfg()
        self.assertEqual(cfg.gmail_app_password_path, config.Config.gmail_app_password_path)
        (self.legacy_dir / secrets.GMAIL_APP_PASSWORD_FILE).write_text(
            "abcd efgh ijkl mnop\n", encoding="utf-8")
        with mock.patch.object(radar_health, "load_radar_health",
                               return_value={"gmail": {"skip_reason": "no_address"}}), \
                mock.patch.object(secrets, "read_path", wraps=secrets.read_path) as rp:
            gm = self._project(cfg)["gmail"]
        self.assertEqual((gm["skip_reason"], gm["intent"], gm["secret_present"]),
                         ("no_address", True, True))
        # Config 缺省字面就是旧默认路径：不当「显式路径」探（那会绕过沙箱读开发机
        # 的真 ~/Desktop/Keys），只经 LEGACY_DEFAULT_PATHS 这一张表探一次
        probed = [c.args[0] for c in rp.call_args_list]
        self.assertNotIn(config.Config.gmail_app_password_path, probed)
        self.assertEqual(probed.count(str(self.legacy_dir / secrets.GMAIL_APP_PASSWORD_FILE)), 1)

    def test_empty_legacy_file_is_neither(self):
        # 旧路径上的空文件：雷达解析不到 → 不 present；「配到一半」只认 config/secrets
        (self.legacy_dir / secrets.SLACK_TOKEN_FILE).write_text("\n", encoding="utf-8")
        sl = self._project()["slack"]
        self.assertEqual((sl["intent"], sl["secret_present"]), (False, False))

    def test_radar_defaults_derive_from_the_single_table(self):
        # 雷达的 DEFAULT_*_PATH 与投影探针取自同一张表（防腐 #9 命名单源）
        self.assertEqual(radar_slack.DEFAULT_TOKEN_PATH, "~/Desktop/Keys/slack-user-token.txt")
        self.assertEqual(radar_gmail.DEFAULT_APP_PASSWORD_PATH,
                         "~/Desktop/Keys/gmail-app-password.txt")

    def test_obsidian_vault_configured_is_intent(self):
        cfg = _Cfg()
        cfg.obsidian_raw = "/tmp/vault/raw"
        ob = self._project(cfg)["obsidian"]
        self.assertEqual((ob["intent"], ob["secret_present"]), (True, False))

    # ---- shape + robustness ------------------------------------------------
    def test_keys_are_appended_add_only_and_bool(self):
        entry = self._project()["gmail"]
        self.assertEqual(list(entry)[-2:], ["intent", "secret_present"])
        self.assertIsInstance(entry["intent"], bool)
        self.assertIsInstance(entry["secret_present"], bool)

    def test_disabled_source_still_reports_signals(self):
        # 关着的源 health 摘要被屏蔽（§48.4），意愿信号照算——消费者自己合取 enabled
        self._write_overrides({"gmail_enabled": False})
        cfg = _Cfg()
        cfg.gmail_enabled = False
        with mock.patch.object(radar_health, "load_radar_health",
                               return_value={"gmail": {"skip_reason": "no_credentials"}}):
            gm = self._project(cfg)["gmail"]
        self.assertFalse(gm["enabled"])
        self.assertIsNone(gm["skip_reason"])
        self.assertTrue(gm["intent"])

    def test_corrupt_overrides_never_raise(self):
        self._write_overrides("{not json")
        rs = self._project()
        self.assertFalse(rs["gmail"]["intent"])
        self._write_overrides("[1, 2]")
        self.assertFalse(self._project()["gmail"]["intent"])

    def test_probe_failure_degrades_to_no_signal(self):
        with mock.patch.object(secrets, "read_secret", side_effect=RuntimeError("disk")):
            rs = self._project()
        self.assertEqual({"intent": False, "secret_present": False},
                         {k: rs["slack"][k] for k in ("intent", "secret_present")})


if __name__ == "__main__":
    unittest.main()
