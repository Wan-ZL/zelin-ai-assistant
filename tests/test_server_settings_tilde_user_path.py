"""§68.1：目录字段里 ``~nosuchuser/...`` 这种坏路径**不许**把设置快照打成 500（§0 第 11 条）。

Python 的 ``Path.expanduser()`` 对查不到的用户名抛 RuntimeError（原生 ``expandingTildeInPath`` 原样返回）；
``settings_catalog.expand_user_path`` 兜住它，目录字段的 ``path_exists`` 用同一把：

- ``expand_user_path``：``~/x`` 照常展开；``~nosuchuser/x`` 原样返回（不抛）；
- ``path_exists("~nosuchuser/x")`` → False（不抛）；
- config.yaml ``sources.obsidian_raw: ~nosuchuser/repo`` 下 ``GET /api/settings`` 仍 200。
（v1 的开发者区仓库路径与 ``POST /api/maintainer/terminal`` 随 Act 管线退役。）
"""
import tempfile
import unittest
from pathlib import Path

from tests import TMP_HOME  # noqa: F401 - sandbox env first
from tests.test_server_common import get_json, start_server, write_text

from server import settings_catalog as catalog

# 不存在的用户名（pwd.getpwnam 查不到 → RuntimeError）；Windows 上 ~user 不查账户，展开成 <home 的父目录>/<user>/repo，
# 断言只看「不抛、不是目录、快照 200」，两边都成立。
BAD_USER_PATH = "~zai-no-such-user-9f3c/repo"


class ExpandUserPathTestCase(unittest.TestCase):
    def test_home_tilde_still_expands(self):
        self.assertEqual(catalog.expand_user_path("~/x"), Path.home() / "x")
        self.assertEqual(catalog.expand_user_path("/abs/x"), Path("/abs/x"))

    def test_unknown_user_does_not_raise(self):
        path = catalog.expand_user_path(BAD_USER_PATH)
        self.assertIsInstance(path, Path)
        self.assertFalse(path.is_dir())

    def test_path_exists_is_false_not_a_crash(self):
        self.assertIs(catalog.path_exists(BAD_USER_PATH), False)
        self.assertIsNone(catalog.path_exists(""))


class BadVaultPathInConfigTestCase(unittest.TestCase):
    """目录字段（obsidian_raw）在 config.yaml 里写成 ``~nosuchuser/...``：快照照样 200。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-tilde-user-")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        (self.home / "state").mkdir(parents=True)
        write_text(self.home / "config.yaml", "sources:\n  obsidian_raw: %s\n" % BAD_USER_PATH)

    def test_settings_snapshot_survives_and_reports_the_path_missing(self):
        _httpd, port = start_server(self, self.home)
        status, snap = get_json(port, "/api/settings")
        self.assertEqual(status, 200)
        obsidian = next(s for s in snap["sections"] if s["id"] == "obsidian")
        field = next(f for f in obsidian["fields"] if f["key"] == "obsidian_raw")
        self.assertEqual(field["effective"], BAD_USER_PATH)
        self.assertIs(field["path_exists"], False)


if __name__ == "__main__":
    unittest.main()
