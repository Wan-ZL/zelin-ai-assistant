"""server/ 的磁盘占用与保留期面（CONTRACT §71，issue #28；§49 路由）。

- GET /api/settings/storage：token-light；wire 形恒全（旋钮 + bounds + usage +
  growth + prune）；**渲染路径零阻塞 IO**——扫描在后台，读面立刻回。
- PUT /api/settings/storage：四闸同 POST、字段白名单、夹取、diff-write；
  管线（config._OVERRIDE_FIELDS）读到的正是 web 写下的那个值。

真 server 起在随机端口（tests/test_server_common.py），stdlib 客户端。
录制目录 = `ZAI_SCREENPIPE_DIR` 缝指到 tmp（判例永不碰真 ~/.screenpipe）。
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first
from tests.test_server_common import (auth_headers, get_json, http_request,
                                      start_server, write_text)

from act.lib import config
from server import storage


def put_json(port, path, payload, headers=None):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    status, _h, data = http_request(port, "PUT", path, body=body,
                                    headers=headers if headers is not None
                                    else auth_headers(port))
    return status, json.loads(data.decode("utf-8"))


class _ServerCase(unittest.TestCase):
    def setUp(self):
        storage.reset_cache()
        self.addCleanup(storage.reset_cache)
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-storage-settings-")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        (self.home / "state").mkdir(parents=True)
        # §71 的录制目录缝：判例永不碰这台机器真实的 ~/.screenpipe（几十万个文件，
        # 结果还随机器不同）。指到一个不存在的 tmp 路径 = usage.state "missing"。
        seam = mock.patch.dict(os.environ,
                               {"ZAI_SCREENPIPE_DIR": str(Path(self.tmp.name) / "screenpipe")})
        seam.start()
        self.addCleanup(seam.stop)
        _httpd, self.port = start_server(self, self.home)

    @property
    def overrides_path(self) -> Path:
        return self.home / "state" / "settings_overrides.json"

    def _overrides(self):
        return json.loads(self.overrides_path.read_text(encoding="utf-8"))


class StorageGetTestCase(_ServerCase):
    def test_shape_and_defaults(self):
        status, obj = get_json(self.port, "/api/settings/storage")
        self.assertEqual(status, 200)
        self.assertEqual(obj["media_retention_minutes"], 60)
        self.assertEqual(obj["source"], "default")
        self.assertEqual(obj["bounds"], {"min": 5, "max": 365 * 24 * 60, "default": 60})
        self.assertIn(obj["usage"]["state"], ("scanning", "missing", "ready"))
        self.assertEqual(obj["prune"]["state"], "never")   # 回执文件还不存在
        self.assertTrue(obj["prune"]["stale"])

    def test_layering(self):
        write_text(self.home / "config.yaml", "recording:\n  media_retention_minutes: 120\n")
        _s, obj = get_json(self.port, "/api/settings/storage")
        self.assertEqual((obj["media_retention_minutes"], obj["source"]), (120, "config"))
        write_text(self.overrides_path, json.dumps({"recording_media_retention_minutes": 240}))
        _s, obj = get_json(self.port, "/api/settings/storage")
        self.assertEqual((obj["media_retention_minutes"], obj["source"]), (240, "override"))

    def test_bad_yaml_value_falls_back_leniently(self):
        write_text(self.home / "config.yaml", "recording:\n  media_retention_minutes: soon\n")
        _s, obj = get_json(self.port, "/api/settings/storage")
        self.assertEqual((obj["media_retention_minutes"], obj["source"]), (60, "config"))

    def test_prune_receipt_is_projected(self):
        write_text(self.home / "state" / "screenpipe_prune.json",
                   json.dumps({"ts": "2026-09-11T04:00:00Z", "state": "ok",
                               "retention_minutes": 60, "deleted_files": 7,
                               "deleted_bytes": 1234, "data_dir": "/x"}))
        _s, obj = get_json(self.port, "/api/settings/storage")
        self.assertEqual(obj["prune"]["ran_at"], "2026-09-11T04:00:00Z")
        self.assertEqual(obj["prune"]["deleted_files"], 7)

    def test_get_is_token_light(self):
        status, _h, _d = http_request(self.port, "GET", "/api/settings/storage", headers={})
        self.assertEqual(status, 200)

    def test_read_never_walks_the_tree_in_the_request_thread(self):
        # 请求线程里如果真去走目录树，这个 patch 会直接把它炸出来
        with mock.patch.object(storage, "walk", side_effect=AssertionError("blocking IO")):
            status, obj = get_json(self.port, "/api/settings/storage")
        self.assertEqual(status, 200)
        self.assertIn(obj["usage"]["state"], ("scanning", "missing"))


class StoragePutTestCase(_ServerCase):
    def test_put_writes_and_preserves_other_keys(self):
        write_text(self.overrides_path, json.dumps({"language": "en"}))
        status, obj = put_json(self.port, "/api/settings/storage", {"media_retention_minutes": 180})
        self.assertEqual(status, 200)
        self.assertEqual((obj["media_retention_minutes"], obj["source"]), (180, "override"))
        self.assertEqual(self._overrides(), {"language": "en",
                                             "recording_media_retention_minutes": 180})

    def test_put_equal_to_default_deletes_the_key(self):
        write_text(self.overrides_path, json.dumps({"recording_media_retention_minutes": 180}))
        _s, obj = put_json(self.port, "/api/settings/storage", {"media_retention_minutes": 60})
        self.assertEqual(obj["source"], "default")
        self.assertEqual(self._overrides(), {})

    def test_out_of_range_is_clamped_not_refused(self):
        _s, obj = put_json(self.port, "/api/settings/storage", {"media_retention_minutes": 1})
        self.assertEqual(obj["media_retention_minutes"], 5)
        _s, obj = put_json(self.port, "/api/settings/storage", {"media_retention_minutes": 10 ** 9})
        self.assertEqual(obj["media_retention_minutes"], 365 * 24 * 60)

    def test_gates(self):
        status, obj = put_json(self.port, "/api/settings/storage", {"nope": 1})
        self.assertEqual((status, obj["error"]["code"]), (400, "UNKNOWN_FIELD"))
        status, obj = put_json(self.port, "/api/settings/storage", {})
        self.assertEqual((status, obj["error"]["code"]), (400, "INVALID_FIELD"))
        status, obj = put_json(self.port, "/api/settings/storage",
                               {"media_retention_minutes": "soon"})
        self.assertEqual((status, obj["error"]["code"]), (400, "INVALID_FIELD"))
        self.assertEqual(obj["error"]["details"]["field"], "media_retention_minutes")

    def test_put_without_token_is_401(self):
        status, _obj = put_json(self.port, "/api/settings/storage",
                                {"media_retention_minutes": 90},
                                headers={"Content-Type": "application/json"})
        self.assertEqual(status, 401)

    def test_pipeline_reads_what_the_web_wrote(self):
        put_json(self.port, "/api/settings/storage", {"media_retention_minutes": 300})
        with mock.patch.object(config, "SETTINGS_OVERRIDES_PATH", self.overrides_path), \
                mock.patch.object(config, "CONFIG_PATH", self.home / "config.yaml"), \
                mock.patch.object(config, "CONFIG_EXAMPLE_PATH", self.home / "nope.yaml"):
            cfg = config.load_config()
        self.assertEqual(cfg.recording_media_retention_minutes, 300)


if __name__ == "__main__":
    unittest.main()
