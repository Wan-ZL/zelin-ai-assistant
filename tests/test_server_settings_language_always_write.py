"""general.language 是唯一的语言开关，显式选择必须落键（CONTRACT §15 追记 2026-09-06，owner 授权代拍 D37；§49 / §68.1）。

行为对齐审计 settings-language-two-switches / pages-shell-nav-first-run-language-not-persisted：此前 web 顶栏切换只写
localStorage、设置里的「界面语言」只管 python 侧，目录 help 还把两把开关写成法条。D37 把它们收回一把：web 的每个入口
（顶栏 / ``/lang`` / 向导 / 设置区保存 / 首启持久化）都 ``PUT /api/settings/general {language}``。server 这半边要保证：

- ``language`` 是 ``write: "always"``（原生 Settings.persistLanguage「an explicit user choice that must stick」）——等于目录
  default "zh" 也落键。目录里的 default 与 Config.language 的 dataclass 默认一样只是占位：python 侧 ``failures.ui_lang`` 没有
  持久化值时回落到 locale（launchd / cron 下无 LANG 即 en），diff-write 把 "zh" 删成「没选过」会让通知与看板各说一种语言；
- 落的是扁平键、与别的键并存、能被 python 侧 ``_override_language`` 读回；
- 目录 help 不再描述两把开关（"顶栏切换" 不再是「另一把」）——两种语言都说「同一把」。
"""
import json
import tempfile
import unittest
from pathlib import Path

from tests import TMP_HOME  # noqa: F401 - sandbox env first
from tests.test_server_common import auth_headers, get_json, http_request, start_server, write_text

from act.lib import config as act_config
from act.lib import failures
from server import settings_catalog as catalog


def put_json(port, path, payload):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    status, _h, data = http_request(port, "PUT", path, body=body, headers=auth_headers(port))
    return status, json.loads(data.decode("utf-8"))


def _field(obj, key):
    return next(f for f in obj["fields"] if f["key"] == key)


class LanguageAlwaysWriteTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-lang-")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        (self.home / "state").mkdir(parents=True)
        _httpd, self.port = start_server(self, self.home)
        self.overrides_path = self.home / "state" / "settings_overrides.json"

    def _overrides(self):
        return json.loads(self.overrides_path.read_text(encoding="utf-8"))

    def test_catalog_declares_language_write_always(self):
        field = catalog.field_index(catalog.lookup("general"))["language"]
        self.assertEqual(field["write"], "always")

    def test_put_equal_to_default_still_writes_the_key(self):
        # 首启持久化 / 显式选 zh：等于目录 default 也得落键，否则 python 侧回落 locale（launchd 下 = en）
        status, obj = put_json(self.port, "/api/settings/general", {"language": "zh"})
        self.assertEqual(status, 200)
        self.assertEqual((_field(obj, "language")["effective"], _field(obj, "language")["source"]), ("zh", "override"))
        self.assertEqual(self._overrides(), {"language": "zh"})

    def test_put_equal_to_config_layer_still_writes_the_key(self):
        write_text(self.home / "config.yaml", "language: en\n")
        _s, obj = put_json(self.port, "/api/settings/general", {"language": "en"})
        self.assertEqual(_field(obj, "language")["source"], "override")
        self.assertEqual(self._overrides(), {"language": "en"})

    def test_write_preserves_sibling_keys_and_flips_back(self):
        write_text(self.overrides_path, json.dumps({"review_notify": "off", "language": "en"}))
        _s, obj = put_json(self.port, "/api/settings/general", {"language": "zh"})
        self.assertEqual(_field(obj, "language")["effective"], "zh")
        self.assertEqual(self._overrides(), {"review_notify": "off", "language": "zh"})

    def test_invalid_language_is_400_and_writes_nothing(self):
        status, obj = put_json(self.port, "/api/settings/general", {"language": "fr"})
        self.assertEqual(status, 400)
        self.assertEqual(obj["error"]["code"], "INVALID_FIELD")
        self.assertFalse(self.overrides_path.exists())

    def test_python_side_reads_the_persisted_choice(self):
        # web 落的键就是 act/lib/failures._override_language 读的键（同一份文件、同一个扁平键名）
        put_json(self.port, "/api/settings/general", {"language": "zh"})
        original = act_config.SETTINGS_OVERRIDES_PATH
        act_config.SETTINGS_OVERRIDES_PATH = self.overrides_path
        try:
            self.assertEqual(failures._override_language(), "zh")
        finally:
            act_config.SETTINGS_OVERRIDES_PATH = original

    def test_source_default_is_visible_until_someone_writes(self):
        # web 的首启持久化只在 source == "default" 时写一次：这个投影就是它的判据
        _s, before = get_json(self.port, "/api/settings/general")
        self.assertEqual(_field(before, "language")["source"], "default")
        put_json(self.port, "/api/settings/general", {"language": "en"})
        _s, after = get_json(self.port, "/api/settings/general")
        self.assertEqual(_field(after, "language")["source"], "override")


class LanguageHelpCopyTestCase(unittest.TestCase):
    def test_help_describes_one_switch_not_two(self):
        help_ = catalog.field_index(catalog.lookup("general"))["language"]["help"]
        self.assertIn("这一把开关", help_["zh"])
        self.assertIn("同一个值", help_["zh"])
        self.assertIn("顶栏切换", help_["zh"])          # 入口列出来，但作为同一个值的写者
        self.assertNotIn("看板自己的语言", help_["zh"])  # 旧句：「看板自己的语言由顶栏切换」= 两把开关
        self.assertIn("One switch", help_["en"])
        self.assertNotIn("the board's own language", help_["en"])


if __name__ == "__main__":
    unittest.main()
