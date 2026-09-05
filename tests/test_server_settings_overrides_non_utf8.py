"""settings_overrides.json 字节级坏文件（非 UTF-8）→ 409 CONFLICT，不 500（CONTRACT §59 设置面；2026-09-05）。

``server.settings.read_overrides`` 此前只把 OSError 映成 ConflictError；``read_text(encoding="utf-8")`` 对
非 UTF-8 字节抛的是 UnicodeDecodeError（ValueError），会一路穿到 500——设置页失去「坏文件请手修」的诚实 409，
关于页（§68.6 追记 ``check_enabled`` 读同一文件）整份快照跟着消失。这里钉：坏字节 = 坏 JSON 同款 409，文件不被碰。
"""
import json
import tempfile
import unittest
from pathlib import Path

from tests import TMP_HOME  # noqa: F401 - sandbox env first
from tests.test_server_common import assert_envelope, auth_headers, http_request, start_server

from server import settings
from server.errors import ConflictError

GARBAGE = b"\xff\xfe{garbage"


class OverridesNonUtf8TestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-overrides-bytes-")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        (self.home / "state").mkdir(parents=True)
        self.overrides_path = self.home / "state" / "settings_overrides.json"
        self.overrides_path.write_bytes(GARBAGE)

    def test_read_overrides_raises_conflict_not_unicode_error(self):
        with self.assertRaises(ConflictError) as cm:
            settings.read_overrides(self.home)
        self.assertIn("unreadable", cm.exception.message)

    def test_settings_put_is_409_and_file_untouched(self):
        _httpd, port = start_server(self, self.home)
        status, _h, data = http_request(port, "PUT", "/api/settings/general", body=b'{"language": "en"}',
                                        headers=auth_headers(port))
        self.assertEqual(status, 409)
        assert_envelope(self, json.loads(data.decode("utf-8")), "CONFLICT")
        self.assertEqual(self.overrides_path.read_bytes(), GARBAGE)


if __name__ == "__main__":
    unittest.main()
