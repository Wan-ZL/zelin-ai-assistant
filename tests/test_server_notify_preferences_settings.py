"""§28 追记（issue #29）通知偏好的设置面：通知区的六把新旋钮 + `clock_time` 形状校验。

写方半边（抑制逻辑）住 tests/test_notify_preferences.py；这里只钉 wire：
  - 通知区的 field 顺序与键（完成三档在前，分类三把，安静时段三把）；
  - 每把的 default 与 act.lib.config.Config 逐字一致（server/ 不 import act，
    目录是镜像——默认值走样 = 设置页显示的「出厂值」是假的）；
  - 安静时段两端带 `check: clock_time`：坏值 400 INVALID_FIELD 且 overrides 不落，
    好值 diff-write（等于出厂值就删键）；
  - 三把分类开关与失败类的文案说明（issue #29 验收第 3 条：文案得解释每一类是什么）。
"""
import json
import tempfile
import unittest
from pathlib import Path

from tests import TMP_HOME  # noqa: F401 - sandbox env first
from tests.test_server_common import assert_envelope, auth_headers, get_json, http_request, start_server

from act.lib import config as act_config
from server import settings_catalog as catalog

SECTION = "/api/settings/notifications"


def put_json(port, path, payload):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    status, _h, data = http_request(port, "PUT", path, body=body, headers=auth_headers(port))
    return status, json.loads(data.decode("utf-8"))


class NotificationPreferencesSettingsTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-notify-prefs-")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        (self.home / "state").mkdir(parents=True)
        _httpd, self.port = start_server(self, self.home)

    @property
    def overrides_path(self) -> Path:
        return self.home / "state" / "settings_overrides.json"

    def _overrides(self) -> dict:
        if not self.overrides_path.exists():
            return {}
        return json.loads(self.overrides_path.read_text(encoding="utf-8"))

    def _field(self, obj, key):
        return next(f for f in obj["fields"] if f["key"] == key)

    def test_section_lists_every_knob_in_order(self):
        _s, section = get_json(self.port, SECTION)
        self.assertEqual([f["key"] for f in section["fields"]],
                         ["review_notify", "notify_proposals", "notify_needs_input",
                          "notify_failures", "quiet_hours_enabled",
                          "quiet_hours_start", "quiet_hours_end"])

    def test_defaults_mirror_the_config_dataclass(self):
        """出厂值单源 = act/lib/config.Config；目录只是它的 server 侧镜像。"""
        cfg = act_config.Config()
        _s, section = get_json(self.port, SECTION)
        for key in ("notify_proposals", "notify_needs_input", "notify_failures",
                    "quiet_hours_enabled", "quiet_hours_start", "quiet_hours_end"):
            with self.subTest(key=key):
                field = self._field(section, key)
                self.assertEqual(field["default"], getattr(cfg, key))
                self.assertEqual(field["effective"], getattr(cfg, key))
                self.assertEqual(field["source"], "default")

    def test_the_three_category_switches_default_on(self):
        """issue #29 验收②：失败通知默认开（三把都是，关掉是显式动作）。"""
        _s, section = get_json(self.port, SECTION)
        for key in ("notify_proposals", "notify_needs_input", "notify_failures"):
            with self.subTest(key=key):
                self.assertIs(self._field(section, key)["default"], True)

    def test_every_category_help_explains_what_it_is(self):
        """issue #29 验收③：每一类的文案都得说清它涵盖什么（双语，非空）。"""
        _s, section = get_json(self.port, SECTION)
        for key in ("notify_proposals", "notify_needs_input", "notify_failures",
                    "quiet_hours_enabled"):
            with self.subTest(key=key):
                help_text = self._field(section, key)["help"]
                self.assertTrue(help_text["zh"].strip(), key)
                self.assertTrue(help_text["en"].strip(), key)

    def test_quiet_hours_help_is_honest_about_the_staleness_rule(self):
        """§28 的 10 分钟 stale 清扫让「压到早上」不可能——文案必须说丢弃，不许说攒着。"""
        _s, section = get_json(self.port, SECTION)
        help_text = self._field(section, "quiet_hours_enabled")["help"]
        self.assertIn("10 分钟", help_text["zh"])
        self.assertIn("10 minutes", help_text["en"])
        self.assertIn("dropped, not held until morning", help_text["en"])

    def test_failure_help_says_quiet_hours_do_not_silence_it(self):
        _s, section = get_json(self.port, SECTION)
        help_text = self._field(section, "notify_failures")["help"]
        self.assertIn("不受安静时段", help_text["zh"])
        self.assertIn("quiet hours do not silence it", help_text["en"])

    def test_clock_check_is_projected_on_both_endpoints(self):
        _s, section = get_json(self.port, SECTION)
        for key in ("quiet_hours_start", "quiet_hours_end"):
            with self.subTest(key=key):
                self.assertEqual(self._field(section, key)["check"],
                                 {"kind": "clock_time", "message": catalog.CHECKS["clock_time"]})
        self.assertNotIn("check", self._field(section, "quiet_hours_enabled"))

    def test_bad_clock_is_400_and_nothing_is_written(self):
        for raw in ("10pm", "24:00", "9:60", "0830", "22:00-08:00"):
            with self.subTest(raw=raw):
                status, obj = put_json(self.port, SECTION, {"quiet_hours_start": raw})
                self.assertEqual(status, 400)
                assert_envelope(self, obj, "INVALID_FIELD")
                self.assertEqual(obj["error"]["details"],
                                 {"field": "quiet_hours_start", "check": "clock_time"})
        self.assertEqual(self._overrides(), {})

    def test_blank_clears_the_key_without_checking(self):
        """空串 = 清键（目录的通用语义，run_check 不查空值）——回到出厂的 22:00。"""
        status, _obj = put_json(self.port, SECTION, {"quiet_hours_start": "23:30"})
        self.assertEqual(status, 200)
        status, obj = put_json(self.port, SECTION, {"quiet_hours_start": "   "})
        self.assertEqual(status, 200)
        self.assertEqual(self._field(obj, "quiet_hours_start")["effective"], "22:00")
        self.assertEqual(self._overrides(), {})

    def test_good_clock_saves_and_the_factory_value_deletes_the_key(self):
        status, obj = put_json(self.port, SECTION, {"quiet_hours_start": "23:30"})
        self.assertEqual(status, 200)
        self.assertEqual(self._field(obj, "quiet_hours_start")["effective"], "23:30")
        self.assertEqual(self._overrides(), {"quiet_hours_start": "23:30"})
        # diff-write：写回出厂值 = 删键
        status, _obj = put_json(self.port, SECTION, {"quiet_hours_start": "22:00"})
        self.assertEqual(status, 200)
        self.assertEqual(self._overrides(), {})

    def test_switches_round_trip_through_overrides(self):
        status, obj = put_json(self.port, SECTION,
                               {"notify_proposals": False, "quiet_hours_enabled": True})
        self.assertEqual(status, 200)
        self.assertIs(self._field(obj, "notify_proposals")["effective"], False)
        # 落盘形 = 写方读的那两把扁键（act/lib/config._OVERRIDE_FIELDS）
        self.assertEqual(self._overrides(), {"notify_proposals": False, "quiet_hours_enabled": True})
        for key in self._overrides():
            self.assertIn(key, act_config._OVERRIDE_FIELDS, key)


if __name__ == "__main__":
    unittest.main()
