"""§15 UI 语言解析（act/lib/failures.ui_lang / _persisted_language）直接判例。

tests/test_doctor.py 经 doctor 的文案间接钉了优先级；这里直接钉 failures 自己的
每条分支——2026-09-02 夜报里 failures.py 的 10 个存活变异体全部落在这两个函数：
config.yaml 的 language 路径、LC_ALL 对 LANG 的优先、env 值直接返回、异常回落
"zh"、`config.yaml is None`（PyYAML 缺席）时跳过 yaml 源。三个来源都在沙箱
AIASSISTANT_HOME 里（tests/__init__.py），用完复原。

已认定的等价变异体（不补测试）：classify 的 `not raw or not str(raw).strip()`
`or`→`and`——None/空白经规则链同样落 None，规则永不匹配 "None" 字面。
"""
import json
import os
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act.lib import config, failures


class _Sandboxed(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        self._stashed = {p: self._stash(p) for p in
                         (config.SETTINGS_OVERRIDES_PATH, config.CONFIG_PATH)}
        self.addCleanup(self._restore)
        base = {k: v for k, v in os.environ.items()
                if k not in ("AIASSISTANT_UI_LANG", "LANG", "LC_ALL")}
        self._env = mock.patch.dict(os.environ, base, clear=True)
        self._env.start()
        self.addCleanup(self._env.stop)

    @staticmethod
    def _stash(path):
        if path.exists():
            content = path.read_text(encoding="utf-8")
            path.unlink()
            return content
        return None

    def _restore(self):
        for path, content in self._stashed.items():
            if content is not None:
                path.write_text(content, encoding="utf-8")
            elif path.exists():
                path.unlink()

    @staticmethod
    def _overrides(**data):
        config.SETTINGS_OVERRIDES_PATH.write_text(json.dumps(data), encoding="utf-8")

    @staticmethod
    def _config_yaml(text):
        config.CONFIG_PATH.write_text(text, encoding="utf-8")


class PersistedLanguageTestCase(_Sandboxed):
    def test_nothing_persisted_is_none(self):
        self.assertIsNone(failures._persisted_language())

    def test_overrides_language_normalizes(self):
        self._overrides(language="EN")
        self.assertEqual(failures._persisted_language(), "en")
        self._overrides(language="zh-Hans")
        self.assertEqual(failures._persisted_language(), "zh")

    def test_empty_override_falls_through_to_config_yaml(self):
        self._overrides(language="")
        self._config_yaml("language: en\n")
        self.assertEqual(failures._persisted_language(), "en")

    def test_config_yaml_language_is_read_and_normalized(self):
        self._config_yaml("language: en\n")
        self.assertEqual(failures._persisted_language(), "en")
        self._config_yaml("language: zh_CN\n")
        self.assertEqual(failures._persisted_language(), "zh")

    def test_overrides_win_over_config_yaml(self):
        self._overrides(language="en")
        self._config_yaml("language: zh\n")
        self.assertEqual(failures._persisted_language(), "en")

    def test_config_yaml_without_language_or_non_mapping_is_none(self):
        self._config_yaml("language: ''\n")
        self.assertIsNone(failures._persisted_language())
        self._config_yaml("- just\n- a list\n")
        self.assertIsNone(failures._persisted_language())

    def test_corrupt_overrides_fall_through_to_config_yaml(self):
        self._overrides_raw("{not json")
        self._config_yaml("language: en\n")
        self.assertEqual(failures._persisted_language(), "en")

    def test_corrupt_config_yaml_resolves_to_the_zh_fail_safe(self):
        # status quo pinned, not endorsed: a YAML parse error is not a
        # ValueError, so _persisted_language propagates it and ui_lang's
        # outer guard answers "zh" (the locale is never consulted). A fix
        # is a behaviour change — out of scope for the P3 refactor.
        self._config_yaml("language: [unclosed\n")
        with mock.patch.dict(os.environ, {"LANG": "en_US.UTF-8"}):
            self.assertEqual(failures.ui_lang(), "zh")

    @staticmethod
    def _overrides_raw(text):
        config.SETTINGS_OVERRIDES_PATH.write_text(text, encoding="utf-8")

    def test_without_pyyaml_the_config_yaml_source_is_skipped(self):
        self._config_yaml("language: en\n")
        with mock.patch.object(config, "yaml", None):
            self.assertIsNone(failures._persisted_language())


class UiLangTestCase(_Sandboxed):
    def test_env_var_is_returned_verbatim(self):
        with mock.patch.dict(os.environ, {"AIASSISTANT_UI_LANG": "en"}):
            self.assertEqual(failures.ui_lang(), "en")
        with mock.patch.dict(os.environ, {"AIASSISTANT_UI_LANG": " ZH "}):
            self.assertEqual(failures.ui_lang(), "zh")

    def test_unknown_env_value_is_ignored(self):
        self._overrides(language="en")
        with mock.patch.dict(os.environ, {"AIASSISTANT_UI_LANG": "fr"}):
            self.assertEqual(failures.ui_lang(), "en")

    def test_persisted_beats_locale(self):
        self._overrides(language="en")
        with mock.patch.dict(os.environ, {"LANG": "zh_CN.UTF-8"}):
            self.assertEqual(failures.ui_lang(), "en")

    def test_lc_all_beats_lang(self):
        with mock.patch.dict(os.environ, {"LC_ALL": "zh_TW.UTF-8", "LANG": "en_US.UTF-8"}):
            self.assertEqual(failures.ui_lang(), "zh")
        with mock.patch.dict(os.environ, {"LC_ALL": "en_US.UTF-8", "LANG": "zh_CN.UTF-8"}):
            self.assertEqual(failures.ui_lang(), "en")

    def test_locale_fallback(self):
        with mock.patch.dict(os.environ, {"LANG": "zh_CN.UTF-8"}):
            self.assertEqual(failures.ui_lang(), "zh")
        with mock.patch.dict(os.environ, {"LANG": "en_US.UTF-8"}):
            self.assertEqual(failures.ui_lang(), "en")
        self.assertEqual(failures.ui_lang(), "en")   # no locale at all

    def test_resolution_failure_falls_back_to_zh(self):
        with mock.patch.object(failures, "_persisted_language",
                               side_effect=RuntimeError("boom")):
            self.assertEqual(failures.ui_lang(), "zh")

    def test_pick_and_user_message_follow_ui_lang(self):
        with mock.patch.dict(os.environ, {"AIASSISTANT_UI_LANG": "en"}):
            self.assertEqual(failures.pick("中", "en"), "en")
            self.assertEqual(failures.user_message("network_error"),
                             failures.FAILURES["network_error"]["plain_en"])
        with mock.patch.dict(os.environ, {"AIASSISTANT_UI_LANG": "zh"}):
            self.assertEqual(failures.pick("中", "en"), "中")
        self.assertEqual(failures.pick("中", "en", lang="en"), "en")
        self.assertIsNone(failures.user_message("no_such_id"))
        self.assertIsNone(failures.action_id(None))


if __name__ == "__main__":
    unittest.main()
