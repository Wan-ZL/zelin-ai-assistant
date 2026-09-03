"""server/ settings face for the daily loop knobs (CONTRACT §70, D10; §49 routes).

- GET /api/settings/daily-loop: effective five knobs + per-field source.
- PUT /api/settings/daily-loop: four write gates (same as POST), field whitelist,
  shape validation (bool / HH:MM / non-negative int) with plain-language 400s,
  diff-write into state/settings_overrides.json (equal-to-effective deletes the
  key; other keys preserved), and the pipeline (config._OVERRIDE_FIELDS) reads
  exactly what the web wrote.

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
                                              "stale_days", "trash_retention_days")},
                         {"enabled": True, "time": "03:30", "max_proposals_per_day": 5,
                          "stale_days": 45, "trash_retention_days": 90})
        self.assertEqual(set(obj["source"].values()), {"default"})

    def test_layering_override_over_config_over_default(self):
        write_text(self.home / "config.yaml",
                   "daily_loop:\n  time: '4:15'\n  max_proposals_per_day: 3\n  stale_days: -9\n")
        write_text(self.overrides_path, json.dumps({"daily_loop_max_proposals_per_day": 2,
                                                    "daily_loop_time": "junk"}))
        _s, obj = get_json(self.port, "/api/settings/daily-loop")
        self.assertEqual(obj["time"], "04:15")                      # config, normalised
        self.assertEqual(obj["source"]["time"], "config")           # bad override skipped
        self.assertEqual(obj["max_proposals_per_day"], 2)
        self.assertEqual(obj["source"]["max_proposals_per_day"], "override")
        self.assertEqual(obj["stale_days"], 0)                      # negative in yaml → 0 (mirror)
        self.assertEqual(obj["trash_retention_days"], 90)
        self.assertEqual(obj["source"]["trash_retention_days"], "default")

    def test_config_yaml_bad_values_fall_back_leniently(self):
        write_text(self.home / "config.yaml",
                   "daily_loop:\n  enabled: maybe\n  time: noon\n  max_proposals_per_day: lots\n")
        _s, obj = get_json(self.port, "/api/settings/daily-loop")
        self.assertEqual((obj["enabled"], obj["time"], obj["max_proposals_per_day"]), (True, "03:30", 5))
        self.assertEqual(obj["source"]["enabled"], "config")     # present in yaml, even if bad

    def test_get_is_token_light(self):
        status, _h, _d = http_request(self.port, "GET", "/api/settings/daily-loop", headers={})
        self.assertEqual(status, 200)


class DailyLoopPutTestCase(_ServerCase):
    def test_put_writes_overrides_and_preserves_other_keys(self):
        write_text(self.overrides_path, json.dumps({"language": "en"}))
        status, obj = put_json(self.port, "/api/settings/daily-loop",
                               {"enabled": False, "time": "5:00", "max_proposals_per_day": 2})
        self.assertEqual(status, 200)
        self.assertEqual((obj["enabled"], obj["time"], obj["max_proposals_per_day"]), (False, "05:00", 2))
        self.assertEqual(self._overrides(), {"language": "en", "daily_loop_enabled": False,
                                             "daily_loop_time": "05:00",
                                             "daily_loop_max_proposals_per_day": 2})

    def test_put_equal_to_default_deletes_the_key(self):
        write_text(self.overrides_path, json.dumps({"daily_loop_max_proposals_per_day": 2}))
        _s, obj = put_json(self.port, "/api/settings/daily-loop", {"max_proposals_per_day": 5})
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
        self.assertEqual(cfg.daily_loop_max_proposals_per_day, 5)


if __name__ == "__main__":
    unittest.main()
