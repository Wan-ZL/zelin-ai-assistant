"""server/ 通用设置目录（CONTRACT §15.3 / §49 / §68）。

- GET /api/settings：全目录，每 field 带 effective + source（override / config / default）；
- GET/PUT /api/settings/{section}：四闸、字段白名单、类型校验、diff-write
  （等于 config 层即删键）、nested 拼法（telemetry / features）与 write:always
  （telemetry.capture_input 知情选择不被 diff-drop）、字符串空值 = 清键、409 坏文件；
- 镜像判例：目录里每个 override 键都在 act/lib/config 的允许列表内，默认值与
  Config 数据类逐字一致（server 不 import act，这里是那道 pin）。
"""
import json
import tempfile
import unittest
from pathlib import Path

from tests import TMP_HOME  # noqa: F401 - sandbox env first
from tests.test_server_common import (assert_envelope, auth_headers, get_json,
                                      http_request, start_server, write_text)

from act.lib import config as act_config
from server import settings_catalog as catalog


def put_json(port, path, payload, headers=None):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    status, _h, data = http_request(port, "PUT", path, body=body,
                                    headers=headers if headers is not None else auth_headers(port))
    return status, json.loads(data.decode("utf-8"))


class _ServerCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-catalog-")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        (self.home / "state").mkdir(parents=True)
        _httpd, self.port = start_server(self, self.home)

    @property
    def overrides_path(self) -> Path:
        return self.home / "state" / "settings_overrides.json"

    def _overrides(self):
        return json.loads(self.overrides_path.read_text(encoding="utf-8"))

    def _field(self, obj, key):
        return next(f for f in obj["fields"] if f["key"] == key)


class CatalogGetTestCase(_ServerCase):
    def test_full_catalog_lists_every_section_with_effective_defaults(self):
        status, obj = get_json(self.port, "/api/settings")
        self.assertEqual(status, 200)
        ids = [s["id"] for s in obj["sections"]]
        self.assertEqual(ids, catalog.section_ids())
        for section in obj["sections"]:
            self.assertIn("zh", section["title"])
            self.assertIn("en", section["title"])
            for field in section["fields"]:
                self.assertEqual(field["source"], "default")
                self.assertEqual(field["effective"], field["default"])
                self.assertNotIn("config", field)      # 内部键不外发
                self.assertNotIn("override", field)

    def test_layering_override_over_config_over_default(self):
        write_text(self.home / "config.yaml",
                   "digest:\n  frequency: weekly\nsources:\n  gmail:\n    enabled: 'no'\n")
        write_text(self.overrides_path, json.dumps({"digest_frequency": "daily"}))
        _s, digest = get_json(self.port, "/api/settings/digest")
        freq = self._field(digest, "digest_frequency")
        self.assertEqual((freq["effective"], freq["source"]), ("daily", "override"))
        _s, sources = get_json(self.port, "/api/settings/gmail")
        gmail = self._field(sources, "gmail_enabled")
        self.assertEqual((gmail["effective"], gmail["source"]), (False, "config"))

    def test_nested_and_flat_override_spellings_both_read(self):
        write_text(self.overrides_path, json.dumps({
            "telemetry": {"enabled": False}, "telemetry.level": "basic",
            "features": {"digest": False}, "features.analytics": "off"}))
        _s, tele = get_json(self.port, "/api/settings/telemetry")
        self.assertEqual(self._field(tele, "telemetry.enabled")["effective"], False)
        self.assertEqual(self._field(tele, "telemetry.level")["effective"], "basic")
        _s, flags = get_json(self.port, "/api/settings/flags")
        self.assertEqual(self._field(flags, "features.digest")["effective"], False)
        self.assertEqual(self._field(flags, "features.analytics")["effective"], False)

    def test_bad_override_value_falls_back_like_the_pipeline(self):
        write_text(self.overrides_path, json.dumps({"digest_frequency": "hourly", "trash_retention_days": "x"}))
        _s, digest = get_json(self.port, "/api/settings/digest")
        self.assertEqual(self._field(digest, "digest_frequency")["source"], "default")
        _s, approval = get_json(self.port, "/api/settings/approval")
        self.assertEqual(self._field(approval, "trash_retention_days")["effective"], 60)

    def test_unknown_section_is_404(self):
        status, obj = get_json(self.port, "/api/settings/nope")
        self.assertEqual(status, 404)
        assert_envelope(self, obj, "NOT_FOUND")

    def test_models_section_still_served_by_its_own_module(self):
        status, obj = get_json(self.port, "/api/settings/models")
        self.assertEqual(status, 200)
        self.assertIn("canonical", obj)


class CatalogPutTestCase(_ServerCase):
    def test_put_requires_the_write_gates(self):
        status, _obj = put_json(self.port, "/api/settings/general", {"language": "en"},
                                headers={"Content-Type": "application/json"})
        self.assertEqual(status, 401)
        self.assertFalse(self.overrides_path.exists())

    def test_put_writes_flat_key_and_preserves_others(self):
        write_text(self.overrides_path, json.dumps({"models_dispatch": "claude-opus-5"}))
        status, obj = put_json(self.port, "/api/settings/general", {"language": "en"})
        self.assertEqual(status, 200)
        self.assertEqual(self._field(obj, "language")["source"], "override")
        self.assertEqual(self._overrides(), {"models_dispatch": "claude-opus-5", "language": "en"})

    def test_put_equal_to_default_deletes_the_key(self):
        write_text(self.overrides_path, json.dumps({"language": "en", "review_notify": "off"}))
        _s, obj = put_json(self.port, "/api/settings/general", {"language": "zh"})
        self.assertEqual(self._field(obj, "language")["source"], "default")
        self.assertEqual(self._overrides(), {"review_notify": "off"})

    def test_put_equal_to_config_layer_deletes_the_key(self):
        write_text(self.home / "config.yaml", "trash:\n  retention_days: 30\n")
        write_text(self.overrides_path, json.dumps({"trash_retention_days": 7}))
        _s, obj = put_json(self.port, "/api/settings/approval", {"trash_retention_days": 30})
        self.assertEqual(self._field(obj, "trash_retention_days")["source"], "config")
        self.assertEqual(self._overrides(), {})

    def test_nested_telemetry_written_nested_and_flat_twin_removed(self):
        write_text(self.overrides_path, json.dumps({"telemetry.enabled": False, "language": "en"}))
        _s, obj = put_json(self.port, "/api/settings/telemetry",
                           {"telemetry.enabled": False, "telemetry.level": "basic"})
        self.assertEqual(self._field(obj, "telemetry.level")["effective"], "basic")
        self.assertEqual(self._overrides(),
                         {"language": "en", "telemetry": {"enabled": False, "level": "basic"}})

    def test_nested_block_dropped_when_its_last_key_returns_to_default(self):
        write_text(self.overrides_path, json.dumps({"telemetry": {"level": "basic"}}))
        _s, _obj = put_json(self.port, "/api/settings/telemetry", {"telemetry.level": "detailed"})
        self.assertEqual(self._overrides(), {})

    def test_capture_input_is_write_always_even_when_equal_to_default(self):
        # §15 v0.18 ④：切动过即为知情选择——false 也落键，不被 diff-drop
        _s, obj = put_json(self.port, "/api/settings/telemetry", {"telemetry.capture_input": False})
        self.assertEqual(self._field(obj, "telemetry.capture_input")["source"], "override")
        self.assertEqual(self._overrides(), {"telemetry": {"capture_input": False}})

    def test_feature_flags_written_nested(self):
        _s, obj = put_json(self.port, "/api/settings/flags", {"features.digest": False})
        self.assertEqual(self._field(obj, "features.digest")["effective"], False)
        self.assertEqual(self._overrides(), {"features": {"digest": False}})

    def test_empty_string_clears_a_string_override(self):
        write_text(self.overrides_path, json.dumps({"gmail_address": "a@b.c"}))
        _s, obj = put_json(self.port, "/api/settings/gmail", {"gmail_address": "  "})
        self.assertEqual(self._field(obj, "gmail_address")["source"], "default")
        self.assertEqual(self._overrides(), {})

    def test_list_kind_accepts_csv_or_json_list_and_clears_on_empty(self):
        """§68.1 list 字段（Slack 频道 / 关注的人）：web 输入框给逗号分隔字串，也接受 JSON 字串表；
        空 = 清键；非字串元素 400；config.yaml 里的 {id, name} 字典投影成 id。"""
        _s, obj = put_json(self.port, "/api/settings/slack", {"slack_channels": " C1, C2 ,,\nC3 "})
        self.assertEqual(self._field(obj, "slack_channels")["effective"], ["C1", "C2", "C3"])
        self.assertEqual(self._overrides(), {"slack_channels": ["C1", "C2", "C3"]})
        _s, obj = put_json(self.port, "/api/settings/slack", {"watch_people": ["alice", " bob "]})
        self.assertEqual(self._field(obj, "watch_people")["effective"], ["alice", "bob"])
        _s, obj = put_json(self.port, "/api/settings/slack", {"slack_channels": ""})
        self.assertEqual(self._field(obj, "slack_channels")["source"], "default")
        self.assertNotIn("slack_channels", self._overrides())
        status, obj = put_json(self.port, "/api/settings/slack", {"slack_channels": [1, 2]})
        self.assertEqual(status, 400)
        assert_envelope(self, obj, "INVALID_FIELD")
        for too_big in (["x" * (catalog.LIST_MAX + 1)], ["C"] * (catalog.LIST_MAX + 1), 7):
            status, obj = put_json(self.port, "/api/settings/slack", {"watch_people": too_big})
            self.assertEqual(status, 400, too_big if not isinstance(too_big, list) else len(too_big))
            assert_envelope(self, obj, "INVALID_FIELD")
        write_text(self.home / "config.yaml", "sources:\n  slack_channels:\n    - {id: C9, name: general}\n    - C8\n")
        _s, slack = get_json(self.port, "/api/settings/slack")
        self.assertEqual(self._field(slack, "slack_channels")["effective"], ["C9", "C8"])

    def test_unknown_field_is_400(self):
        status, obj = put_json(self.port, "/api/settings/general", {"language": "en", "theme": "x"})
        self.assertEqual(status, 400)
        assert_envelope(self, obj, "UNKNOWN_FIELD")
        self.assertFalse(self.overrides_path.exists())

    def test_type_and_choice_validation(self):
        cases = (
            ("general", {"language": "fr"}),
            ("general", {"updates_check_enabled": "yes"}),
            ("approval", {"trash_retention_days": 1.5}),
            ("approval", {"show_cost_above_usd": -1}),
            ("approval", {"default_target_repo": 42}),
            ("approval", {"default_target_repo": "a\nb"}),
            ("approval", {"default_target_repo": "x" * 1025}),
            ("general", {}),
        )
        for section, payload in cases:
            with self.subTest(section=section, payload=payload):
                status, obj = put_json(self.port, "/api/settings/%s" % section, payload)
                self.assertEqual(status, 400)
                assert_envelope(self, obj, "INVALID_FIELD")
        self.assertFalse(self.overrides_path.exists())

    def test_unparsable_overrides_file_is_409_and_untouched(self):
        write_text(self.overrides_path, "{not json")
        status, obj = put_json(self.port, "/api/settings/general", {"language": "en"})
        self.assertEqual(status, 409)
        assert_envelope(self, obj, "CONFLICT")
        self.assertEqual(self.overrides_path.read_text(encoding="utf-8"), "{not json")

    def test_unreadable_overrides_file_is_409(self):
        # 目录冒充文件：read_text 抛 IsADirectoryError（OSError 非 FileNotFoundError）→ 409，不 500
        self.overrides_path.mkdir()
        status, obj = put_json(self.port, "/api/settings/general", {"language": "en"})
        self.assertEqual(status, 409)
        assert_envelope(self, obj, "CONFLICT")

    def test_unknown_section_put_is_404(self):
        status, obj = put_json(self.port, "/api/settings/nope", {"x": 1})
        self.assertEqual(status, 404)
        assert_envelope(self, obj, "NOT_FOUND")


class ConfigMirrorTestCase(unittest.TestCase):
    """目录 ↔ act/lib/config.py：键必须是管线真读的键，默认值必须一致。"""

    NESTED_BLOCKS = {"telemetry": ("enabled", "level", "capture_input"), "features": None}

    def _fields(self):
        for section in catalog.SECTIONS:
            for field in section["fields"]:
                yield field

    def test_every_override_key_is_read_by_the_pipeline(self):
        for field in self._fields():
            spelling = field["override"]
            with self.subTest(key=spelling):
                if "." in spelling:
                    block, sub = spelling.split(".", 1)
                    self.assertIn(block, self.NESTED_BLOCKS)
                    allowed = self.NESTED_BLOCKS[block]
                    if allowed is not None:
                        self.assertIn(sub, allowed)
                    else:
                        self.assertIn(sub, act_config.DEFAULT_FEATURES)
                elif spelling in act_config._OVERRIDE_LIST_FIELDS:
                    self.assertEqual(field["kind"], "list")
                else:
                    self.assertIn(spelling, act_config._OVERRIDE_FIELDS)

    def test_defaults_match_the_config_dataclass(self):
        cfg = act_config.Config()
        attr_for = {"telemetry.enabled": "telemetry_enabled", "telemetry.level": "telemetry_level",
                    "telemetry.capture_input": "telemetry_capture_input"}
        for field in self._fields():
            key = field["key"]
            if key.startswith("features."):
                self.assertTrue(act_config.DEFAULT_FEATURES[key.split(".", 1)[1]])
                continue
            attr = attr_for.get(key, key)
            expected = getattr(cfg, attr)
            with self.subTest(key=key):
                if field["kind"] == "string":
                    self.assertEqual(field["default"] or "", expected or "")
                else:
                    self.assertEqual(field["default"], expected)

    def test_enum_choices_mirror_config_vocabularies(self):
        by_key = {f["key"]: f for f in self._fields()}
        self.assertEqual(tuple(by_key["digest_frequency"]["choices"]), act_config.DIGEST_FREQUENCIES)
        self.assertEqual(tuple(by_key["telemetry.level"]["choices"]), tuple(act_config.TELEMETRY_LEVELS))
        self.assertEqual(tuple(by_key["terminal_app"]["choices"]), act_config.TERMINAL_APPS)
        self.assertEqual(by_key["terminal_app"]["label"], {"zh": "终端应用", "en": "Terminal app"})  # 原生 Settings.swift 逐字


if __name__ == "__main__":
    unittest.main()
