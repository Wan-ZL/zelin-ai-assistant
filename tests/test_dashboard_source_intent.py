"""§48.4 意愿信号：``radar_sources.<src>.intent`` / ``secret_present``（add-only）。

原生 Diagnostics.swift:161-168 + DiagnosticsRules.gmailCardEligible 的 web 版
把「用户真的动过这个源」的判断搬进 actd 投影（§44 单写者；web 只读）：

- ``intent`` = 碰过开关（settings_overrides 里 ``<src>_enabled`` / 嵌套
  ``features.<src>_radar`` / 平铺 ``"features.<src>_radar"`` / ``<src>: {enabled}``，
  开或关都算）∨ 凭证文件存在（哪怕为空 = 配到一半）∨ 凭证非空；
  obsidian 的「配到一半」= 指定过 vault 目录。
- ``secret_present`` = §19 凭证非空（secrets 文件 或 config.yaml 显式路径；
  旧默认路径那层已弃用、不算）；obsidian 恒 False。
- 全新安装（没 overrides、没凭证）三源皆 False —— 这就是 anti-nag 的那张牌：
  「enabled 默认 true」本身不是 intent。
- 坏 overrides / 探不到 → False，投影绝不崩。
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first

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
        for patcher in (
            mock.patch.object(dashboard.config, "SETTINGS_OVERRIDES_PATH", self.overrides),
            mock.patch.object(secrets, "SECRETS_DIR", self.secrets_dir),
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

    def test_default_legacy_path_is_not_probed(self):
        # gmail_app_password_path 的缺省值就是 §19 第三层（~/Desktop/Keys，已弃用）：
        # 投影不读它——哪怕那里真有文件（用假 read_path 证明没被调用）
        cfg = _Cfg()
        self.assertEqual(cfg.gmail_app_password_path, config.Config.gmail_app_password_path)
        with mock.patch.object(secrets, "read_path",
                               side_effect=lambda p: "would-be-a-token" if p else None) as rp:
            gm = self._project(cfg)["gmail"]
        self.assertFalse(gm["secret_present"])
        self.assertEqual({c.args[0] for c in rp.call_args_list}, {None})   # 从不带真路径去读

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
