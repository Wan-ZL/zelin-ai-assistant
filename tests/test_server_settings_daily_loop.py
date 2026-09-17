"""server/ settings face for the daily loop knobs (CONTRACT §70, D10; §49 routes).

- GET /api/settings/daily-loop: effective six knobs + per-field source
  (``review_stale_days`` is the 6th — §70.2 追记 / D74, issue #312).
- PUT /api/settings/daily-loop: four write gates (same as POST), field whitelist,
  shape validation (bool / HH:MM / non-negative int) with plain-language 400s,
  diff-write into state/settings_overrides.json (equal-to-effective deletes the
  key; other keys preserved), and the pipeline (config._OVERRIDE_FIELDS) reads
  exactly what the web wrote.
- PUT /api/settings/daily-loop with an unreadable state/settings_overrides.json
  (bad JSON / not an object / non-UTF-8 bytes) is 409 CONFLICT — never a 500 —
  and leaves the file byte-for-byte alone (§59 read_overrides: never overwrite
  what the owner had in there); GET reports the same conflict.

Real server on a random port (tests/test_server_common.py); stdlib client.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first
from tests.test_server_common import (auth_headers, get_json, http_request,
                                      start_server, write_text)

from act.lib import config


def put_json(port, path, payload, headers=None):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    status, _h, data = http_request(port, "PUT", path, body=body,
                                    headers=headers if headers is not None
                                    else auth_headers(port))
    return status, json.loads(data.decode("utf-8"))


class _ServerCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-dl-settings-")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        (self.home / "state").mkdir(parents=True)
        _httpd, self.port = start_server(self, self.home)

    @property
    def overrides_path(self) -> Path:
        return self.home / "state" / "settings_overrides.json"

    def _overrides(self):
        return json.loads(self.overrides_path.read_text(encoding="utf-8"))


class DailyLoopGetTestCase(_ServerCase):
    def test_defaults(self):
        status, obj = get_json(self.port, "/api/settings/daily-loop")
        self.assertEqual(status, 200)
        self.assertEqual({k: obj[k] for k in ("enabled", "time", "max_proposals_per_day",
                                              "stale_days", "trash_retention_days",
                                              "review_stale_days")},
                         {"enabled": True, "time": "03:30", "max_proposals_per_day": 2,     # D33: 5 → 2
                          "stale_days": 45, "trash_retention_days": 90,
                          "review_stale_days": 14})                                          # D74
        self.assertEqual(set(obj["source"].values()), {"default"})

    def test_layering_override_over_config_over_default(self):
        write_text(self.home / "config.yaml",
                   "daily_loop:\n  time: '4:15'\n  max_proposals_per_day: 3\n  stale_days: -9\n")
        write_text(self.overrides_path, json.dumps({"daily_loop_max_proposals_per_day": 5,
                                                    "daily_loop_time": "junk"}))
        _s, obj = get_json(self.port, "/api/settings/daily-loop")
        self.assertEqual(obj["time"], "04:15")                      # config, normalised
        self.assertEqual(obj["source"]["time"], "config")           # bad override skipped
        self.assertEqual(obj["max_proposals_per_day"], 5)
        self.assertEqual(obj["source"]["max_proposals_per_day"], "override")
        self.assertEqual(obj["stale_days"], 0)                      # negative in yaml → 0 (mirror)
        self.assertEqual(obj["trash_retention_days"], 90)
        self.assertEqual(obj["source"]["trash_retention_days"], "default")

    def test_config_yaml_bad_values_fall_back_leniently(self):
        write_text(self.home / "config.yaml",
                   "daily_loop:\n  enabled: maybe\n  time: noon\n  max_proposals_per_day: lots\n")
        _s, obj = get_json(self.port, "/api/settings/daily-loop")
        self.assertEqual((obj["enabled"], obj["time"], obj["max_proposals_per_day"]), (True, "03:30", 2))
        self.assertEqual(obj["source"]["enabled"], "config")     # present in yaml, even if bad

    def test_get_is_token_light(self):
        status, _h, _d = http_request(self.port, "GET", "/api/settings/daily-loop", headers={})
        self.assertEqual(status, 200)


class DailyLoopOverridesEdgeTestCase(_ServerCase):
    """R-219：坏文件 409 本体之外、走这条路由还没有判例的三条边——闸门顺序、目录冒充文件、只剩空白的文件。
    （后两条的底层分支在 read_overrides 的单元层判例与别的设置面里已有钉法，走这条路由的没有；闸门顺序哪里都没有。
    坏 JSON / 非 object / 非 UTF-8 → PUT 与 GET 409 的本体在文件末尾的 DailyLoopConflictTestCase；
    为什么另起一类而不并进去，见 docs/design/progress/2026-09-16-r219-daily-loop-409-twin.md。）"""

    def test_bad_payload_on_a_broken_file_is_400_not_409(self):
        """闸门顺序（§70 追加原文的排列：字段白名单 400 → 形状 400 → diff-write → 文件坏 409）：
        校验先于读盘。文件已经坏了、payload 也坏，web 拿到的是指名字段的 400 人话，不是 409——
        否则用户先看到「文件坏了」、修完文件再被告知「时间格式不对」，两趟。文件照旧一个字节不动，
        也不许留下 atomic_write 的 .tmp 尾巴（还没走到写那一步）。"""
        raw = b'{"daily_loop_enabled": tru'
        self.overrides_path.write_bytes(raw)
        for payload, code, field in (({"time": "25:00"}, "INVALID_FIELD", "time"),
                                     ({"nope": 1}, "UNKNOWN_FIELD", None),
                                     ({}, "INVALID_FIELD", None)):
            with self.subTest(payload=payload):
                status, obj = put_json(self.port, "/api/settings/daily-loop", payload)
                self.assertEqual(status, 400, payload)
                self.assertEqual(obj["error"]["code"], code)
                if field is not None:
                    self.assertEqual(obj["error"]["details"]["field"], field)
        self.assertEqual(self.overrides_path.read_bytes(), raw)
        self.assertFalse(self.overrides_path.with_suffix(".json.tmp").exists())

    def test_a_directory_at_the_path_is_409_naming_the_file(self):
        """read_overrides 的 OSError 分支（§59：坏文件 = 409 不是 500）从这条路由也够得着：
        目录冒充文件 → read_text 抛 IsADirectoryError / PermissionError（都是 OSError、不是
        FileNotFoundError）→ PUT 与 GET 都 409 CONFLICT；details.path 指着那份文件，owner 照着去修。"""
        self.overrides_path.mkdir()
        status, obj = put_json(self.port, "/api/settings/daily-loop", {"enabled": False})
        self.assertEqual(status, 409)
        self.assertEqual(obj["error"]["code"], "CONFLICT")
        self.assertEqual(obj["error"]["details"]["path"], str(self.overrides_path))
        self.assertTrue(obj["error"]["details"]["error"])          # 底层 OSError 原话随行
        self.assertTrue(self.overrides_path.is_dir())               # 没被换成文件
        status, obj = get_json(self.port, "/api/settings/daily-loop")
        self.assertEqual((status, obj["error"]["code"]), (409, "CONFLICT"))

    def test_a_blank_file_is_not_a_conflict(self):
        """只剩空白的 overrides 文件（owner `> state/settings_overrides.json` 清空过）= `{}`，
        不是坏文件：PUT 200、写回的就是这一把键；GET 报 override。§59 的「坏文件 409」
        只拦真解析不了的，别把清空过的文件当成锁把 web 关在外面。"""
        self.overrides_path.write_bytes(b"  \n\t\n")
        status, obj = put_json(self.port, "/api/settings/daily-loop", {"enabled": False})
        self.assertEqual(status, 200)
        self.assertEqual((obj["enabled"], obj["source"]["enabled"]), (False, "override"))
        self.assertEqual(self._overrides(), {"daily_loop_enabled": False})
        status, obj = get_json(self.port, "/api/settings/daily-loop")
        self.assertEqual((status, obj["source"]["enabled"]), (200, "override"))


class DailyLoopPutTestCase(_ServerCase):
    def test_put_writes_overrides_and_preserves_other_keys(self):
        write_text(self.overrides_path, json.dumps({"language": "en"}))
        status, obj = put_json(self.port, "/api/settings/daily-loop",
                               {"enabled": False, "time": "5:00", "max_proposals_per_day": 5})
        self.assertEqual(status, 200)
        self.assertEqual((obj["enabled"], obj["time"], obj["max_proposals_per_day"]), (False, "05:00", 5))
        self.assertEqual(self._overrides(), {"language": "en", "daily_loop_enabled": False,
                                             "daily_loop_time": "05:00",
                                             "daily_loop_max_proposals_per_day": 5})

    def test_put_equal_to_default_deletes_the_key(self):
        write_text(self.overrides_path, json.dumps({"daily_loop_max_proposals_per_day": 5}))
        _s, obj = put_json(self.port, "/api/settings/daily-loop", {"max_proposals_per_day": 2})
        self.assertEqual(obj["source"]["max_proposals_per_day"], "default")
        self.assertEqual(self._overrides(), {})

    def test_bad_shapes_are_400_with_plain_reason(self):
        for payload, field in (({"time": "25:00"}, "time"), ({"max_proposals_per_day": -1}, "max_proposals_per_day"),
                               ({"stale_days": True}, "stale_days"), ({"enabled": "maybe"}, "enabled")):
            status, obj = put_json(self.port, "/api/settings/daily-loop", payload)
            self.assertEqual(status, 400, payload)
            self.assertEqual(obj["error"]["code"], "INVALID_FIELD")
            self.assertEqual(obj["error"]["details"]["field"], field)
        status, obj = put_json(self.port, "/api/settings/daily-loop", {"nope": 1})
        self.assertEqual((status, obj["error"]["code"]), (400, "UNKNOWN_FIELD"))
        status, _obj = put_json(self.port, "/api/settings/daily-loop", {})
        self.assertEqual(status, 400)

    def test_put_without_token_is_401(self):
        status, _obj = put_json(self.port, "/api/settings/daily-loop", {"enabled": False},
                                headers={"Content-Type": "application/json"})
        self.assertEqual(status, 401)

    def test_pipeline_reads_what_the_web_wrote(self):
        put_json(self.port, "/api/settings/daily-loop",
                 {"enabled": False, "time": "6:30", "stale_days": 10, "trash_retention_days": 120})
        with mock.patch.object(config, "SETTINGS_OVERRIDES_PATH", self.overrides_path), \
                mock.patch.object(config, "CONFIG_PATH", self.home / "config.yaml"), \
                mock.patch.object(config, "CONFIG_EXAMPLE_PATH", self.home / "nope.yaml"):
            cfg = config.load_config()
        self.assertFalse(cfg.daily_loop_enabled)
        self.assertEqual(cfg.daily_loop_time, "06:30")
        self.assertEqual(cfg.daily_loop_stale_days, 10)
        self.assertEqual(cfg.daily_loop_trash_retention_days, 120)
        self.assertEqual(cfg.daily_loop_max_proposals_per_day, 2)      # untouched → D33 default

    def test_the_review_stale_knob_round_trips(self):
        """D74 第六把旋钮：写得进 overrides、读得回管线、0 = 关掉那条规则。"""
        _s, obj = put_json(self.port, "/api/settings/daily-loop", {"review_stale_days": 0})
        self.assertEqual(obj["review_stale_days"], 0)
        self.assertEqual(obj["source"]["review_stale_days"], "override")
        self.assertEqual(self._overrides(), {"daily_loop_review_stale_days": 0})
        status, err = put_json(self.port, "/api/settings/daily-loop", {"review_stale_days": -1})
        self.assertEqual((status, err["error"]["details"]["field"]), (400, "review_stale_days"))
        with mock.patch.object(config, "SETTINGS_OVERRIDES_PATH", self.overrides_path), \
                mock.patch.object(config, "CONFIG_PATH", self.home / "config.yaml"), \
                mock.patch.object(config, "CONFIG_EXAMPLE_PATH", self.home / "nope.yaml"):
            self.assertEqual(config.load_config().daily_loop_review_stale_days, 0)


class DailyLoopConflictTestCase(_ServerCase):
    """坏掉的 state/settings_overrides.json：PUT /api/settings/daily-loop → 409，文件一个字节不动。"""

    CORRUPT = (("bad json", b'{"daily_loop_enabled": tru'),
               ("not an object", b'["daily_loop_enabled"]'),
               ("non-utf8 bytes", b'{"daily_loop_enabled": \xff\xfe}'))

    def test_put_on_an_unreadable_overrides_file_is_409(self):
        for what, raw in self.CORRUPT:
            with self.subTest(what):
                self.overrides_path.write_bytes(raw)
                status, obj = put_json(self.port, "/api/settings/daily-loop", {"enabled": False})
                self.assertEqual(status, 409, what)
                self.assertEqual(obj["error"]["code"], "CONFLICT")
                self.assertIn("settings_overrides.json", obj["error"]["message"])
                # 拒绝覆盖 = owner 手里那份原封不动（§59 read_overrides）
                self.assertEqual(self.overrides_path.read_bytes(), raw)

    def test_get_reports_the_same_conflict_instead_of_a_500(self):
        """读面同一诊断：坏文件下 GET 也是 409 CONFLICT（同一句人话，不是 500）——
        分层读的第一层就是这个文件，静默当它不存在会把「你的覆写没生效」藏起来。"""
        self.overrides_path.write_bytes(b"{nope")
        status, obj = get_json(self.port, "/api/settings/daily-loop")
        self.assertEqual(status, 409)
        self.assertEqual(obj["error"]["code"], "CONFLICT")


if __name__ == "__main__":
    unittest.main()
