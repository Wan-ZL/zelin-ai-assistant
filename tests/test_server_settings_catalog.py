"""server/ 通用设置目录（CONTRACT §15.3 / §49 / §68.1）。

- GET /api/settings：全目录，每 field 带 effective + source（override / config / default）；
- GET/PUT /api/settings/{section}：四闸、字段白名单、类型校验、diff-write
  （等于 config 层即删键）、write:always（general.language）、字符串空值 = 清键、
  list 字段（recording.ignored_apps，§15 追记 v2.0.0 add-only）、409 坏文件；
- 镜像判例：目录里每个 override 键都在 act/lib/config 的允许列表内，默认值与
  Config 数据类逐字一致（server 请求路径不 import act.lib.config，这里是那道 pin）。

v2.0.0 的 section 集合（§68.1 修订）：general / recording / storage / obsidian /
notifications / updates / redaction。slack / gmail / telemetry / digest / approval /
flags / voice / maintainer 八个 section 随 Act 管线退役（全文见 tag full）——请求它们 = 404。
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

LITE_SECTIONS = ["general", "recording", "storage", "obsidian", "notifications", "updates",
                 "redaction"]
RETIRED_SECTIONS = ["slack", "gmail", "telemetry", "digest", "approval", "flags", "voice",
                    "maintainer"]


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
    def test_the_lite_section_set(self):
        self.assertEqual(catalog.section_ids(), LITE_SECTIONS)

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
                   "recording:\n  retention_days: 30\nupdates:\n  check_enabled: 'no'\n")
        write_text(self.overrides_path, json.dumps({"screenpipe_retention_days": 7}))
        _s, storage = get_json(self.port, "/api/settings/storage")
        days = self._field(storage, "screenpipe_retention_days")
        self.assertEqual((days["effective"], days["source"]), (7, "override"))
        _s, updates = get_json(self.port, "/api/settings/updates")
        check = self._field(updates, "updates_check_enabled")
        self.assertEqual((check["effective"], check["source"]), (False, "config"))

    def test_bad_override_value_falls_back_like_the_pipeline(self):
        write_text(self.overrides_path, json.dumps({"language": "fr",
                                                    "screenpipe_media_retention_minutes": 2}))
        _s, general = get_json(self.port, "/api/settings/general")
        self.assertEqual(self._field(general, "language")["source"], "default")
        _s, storage = get_json(self.port, "/api/settings/storage")
        self.assertEqual(self._field(storage, "screenpipe_media_retention_minutes")["effective"], 60)

    def test_unknown_and_retired_sections_are_404(self):
        for sid in ["nope"] + RETIRED_SECTIONS:
            with self.subTest(section=sid):
                status, obj = get_json(self.port, "/api/settings/%s" % sid)
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
        write_text(self.overrides_path, json.dumps({"models_pipeline": "claude-opus-5"}))
        status, obj = put_json(self.port, "/api/settings/general", {"language": "en"})
        self.assertEqual(status, 200)
        self.assertEqual(self._field(obj, "language")["source"], "override")
        self.assertEqual(self._overrides(), {"models_pipeline": "claude-opus-5", "language": "en"})

    def test_put_equal_to_default_deletes_the_key(self):
        write_text(self.overrides_path, json.dumps({"redaction_enabled": True, "language": "en"}))
        _s, obj = put_json(self.port, "/api/settings/redaction", {"redaction_enabled": False})
        self.assertEqual(self._field(obj, "redaction_enabled")["source"], "default")
        self.assertEqual(self._overrides(), {"language": "en"})

    def test_put_equal_to_config_layer_deletes_the_key(self):
        write_text(self.home / "config.yaml", "recording:\n  retention_days: 30\n")
        write_text(self.overrides_path, json.dumps({"screenpipe_retention_days": 7}))
        _s, obj = put_json(self.port, "/api/settings/storage", {"screenpipe_retention_days": 30})
        self.assertEqual(self._field(obj, "screenpipe_retention_days")["source"], "config")
        self.assertEqual(self._overrides(), {})

    def test_auto_install_defaults_off_and_round_trips(self):
        """§79：自动升级出厂关；打开 = 落扁平键 updates_auto_install。"""
        _s, obj = get_json(self.port, "/api/settings/updates")
        self.assertIs(self._field(obj, "updates_auto_install")["default"], False)
        _s, obj = put_json(self.port, "/api/settings/updates", {"updates_auto_install": True})
        self.assertIs(self._field(obj, "updates_auto_install")["effective"], True)
        self.assertEqual(self._overrides(), {"updates_auto_install": True})

    def test_empty_string_clears_a_string_override(self):
        write_text(self.overrides_path, json.dumps({"redaction_terms_file": "/x/terms.txt"}))
        _s, obj = put_json(self.port, "/api/settings/redaction", {"redaction_terms_file": "  "})
        self.assertEqual(self._field(obj, "redaction_terms_file")["source"], "default")
        self.assertEqual(self._overrides(), {})

    def test_list_kind_accepts_csv_or_json_list_and_clears_on_empty(self):
        """§15 追记 v2.0.0 `recording.ignored_apps`：web 输入框给逗号分隔字串，也接受 JSON 字串表；
        空 = 清键（回到出厂表）；非字串元素 400；config.yaml 的列表照读。"""
        _s, obj = put_json(self.port, "/api/settings/recording",
                           {"recording_ignored_apps": " Zoom, Signal ,,\nWhatsApp "})
        self.assertEqual(self._field(obj, "recording_ignored_apps")["effective"],
                         ["Zoom", "Signal", "WhatsApp"])
        self.assertEqual(self._overrides(), {"recording_ignored_apps": ["Zoom", "Signal", "WhatsApp"]})
        _s, obj = put_json(self.port, "/api/settings/recording",
                           {"recording_ignored_apps": ["1Password", " Signal "]})
        self.assertEqual(self._field(obj, "recording_ignored_apps")["effective"], ["1Password", "Signal"])
        _s, obj = put_json(self.port, "/api/settings/recording", {"recording_ignored_apps": ""})
        self.assertEqual(self._field(obj, "recording_ignored_apps")["source"], "default")
        self.assertNotIn("recording_ignored_apps", self._overrides())
        status, obj = put_json(self.port, "/api/settings/recording", {"recording_ignored_apps": [1, 2]})
        self.assertEqual(status, 400)
        assert_envelope(self, obj, "INVALID_FIELD")
        for too_big in (["x" * (catalog.LIST_MAX + 1)], ["C"] * (catalog.LIST_MAX + 1), 7):
            status, obj = put_json(self.port, "/api/settings/recording",
                                   {"recording_ignored_apps": too_big})
            self.assertEqual(status, 400)
            assert_envelope(self, obj, "INVALID_FIELD")
        write_text(self.home / "config.yaml", "recording:\n  ignored_apps:\n    - Zoom\n    - Teams\n")
        _s, rec = get_json(self.port, "/api/settings/recording")
        self.assertEqual(self._field(rec, "recording_ignored_apps")["effective"], ["Zoom", "Teams"])

    def test_the_pipeline_reads_what_the_list_field_writes(self):
        _s, _obj = put_json(self.port, "/api/settings/recording",
                            {"recording_ignored_apps": "Zoom, Signal"})
        doc = self._overrides()
        cfg = act_config.Config()
        act_config._apply_override(cfg, "recording_ignored_apps", doc["recording_ignored_apps"])
        self.assertEqual(cfg.recording_ignored_apps, ["Zoom", "Signal"])

    def test_unknown_field_is_400(self):
        status, obj = put_json(self.port, "/api/settings/general", {"language": "en", "theme": "x"})
        self.assertEqual(status, 400)
        assert_envelope(self, obj, "UNKNOWN_FIELD")
        self.assertFalse(self.overrides_path.exists())

    def test_type_and_choice_validation(self):
        cases = (
            ("general", {"language": "fr"}),
            ("updates", {"updates_check_enabled": "yes"}),
            ("storage", {"screenpipe_retention_days": 1.5}),
            ("storage", {"screenpipe_media_retention_minutes": 4}),
            ("redaction", {"redaction_terms_file": 42}),
            ("redaction", {"redaction_terms_file": "a\nb"}),
            ("redaction", {"redaction_terms_file": "x" * 1025}),
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

    def test_unknown_and_retired_section_put_is_404(self):
        for sid in ("nope", "telemetry", "maintainer"):
            with self.subTest(section=sid):
                status, obj = put_json(self.port, "/api/settings/%s" % sid, {"x": 1})
                self.assertEqual(status, 404)
                assert_envelope(self, obj, "NOT_FOUND")


class ConfigMirrorTestCase(unittest.TestCase):
    """目录 ↔ act/lib/config.py：键必须是管线真读的键，默认值必须一致。"""

    def _fields(self):
        for section in catalog.SECTIONS:
            for field in section["fields"]:
                yield field

    def test_every_override_key_is_read_by_the_pipeline(self):
        for field in self._fields():
            spelling = field["override"]
            with self.subTest(key=spelling):
                self.assertNotIn(".", spelling)   # v2.0.0: no nested blocks left
                if spelling in act_config._OVERRIDE_LIST_FIELDS:
                    self.assertEqual(field["kind"], "list")
                else:
                    self.assertIn(spelling, act_config._OVERRIDE_FIELDS)

    def test_defaults_match_the_config_dataclass(self):
        cfg = act_config.Config()
        for field in self._fields():
            key = field["key"]
            expected = getattr(cfg, key)
            with self.subTest(key=key):
                if field["kind"] == "string":
                    self.assertEqual(field["default"] or "", expected or "")
                else:
                    self.assertEqual(field["default"], expected)

    def test_ignored_apps_default_mirrors_config(self):
        self.assertEqual(catalog.DEFAULT_IGNORED_APPS, act_config.DEFAULT_IGNORED_APPS)

    def test_bounds_mirror_the_config_coercer(self):
        by_key = {f["key"]: f for f in self._fields()}
        self.assertEqual(by_key["screenpipe_media_retention_minutes"]["bounds"],
                         (act_config.MIN_MEDIA_RETENTION_MINUTES,
                          act_config.MAX_MEDIA_RETENTION_MINUTES))


if __name__ == "__main__":
    unittest.main()
