"""§68.7 追记 / §68.1：目录字段里 ``~nosuchuser/...`` 这种坏路径**不许**把设置快照或开发会话启动打成 500（§0 第 11 条）。

Python 的 ``Path.expanduser()`` 对查不到的用户名抛 RuntimeError（原生 ``expandingTildeInPath`` 原样返回）；
``settings_catalog.expand_user_path`` 兜住它，目录字段的 ``path_exists``、开发者区仓库路径的动态灰字与
``maintainer_launch.resolve`` 三处同一把：

- ``expand_user_path``：``~/x`` 照常展开；``~nosuchuser/x`` 原样返回（不抛）；
- ``path_exists("~nosuchuser/x")`` → False（不抛）；
- config.yaml ``maintainer.repo_path: ~nosuchuser/repo`` 下 ``GET /api/settings`` 仍 200，灰字带着原样的路径；
- 同一份 config 下 ``POST /api/maintainer/terminal`` 是「路径不存在」400，不是 500。
"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first
from tests.test_server_common import assert_envelope, get_json, post_json, start_server, write_text

from server import maintainer_launch
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


class BadRepoPathInConfigTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-tilde-user-")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        (self.home / "state").mkdir(parents=True)
        write_text(self.home / "config.yaml", "maintainer:\n  repo_path: %s\n" % BAD_USER_PATH)

    def _field(self, section, key):
        return next(f for f in section["fields"] if f["key"] == key)

    def test_settings_snapshot_survives_and_shows_the_raw_path_as_placeholder(self):
        _httpd, port = start_server(self, self.home)
        status, snap = get_json(port, "/api/settings")
        self.assertEqual(status, 200)
        maintainer = next(s for s in snap["sections"] if s["id"] == "maintainer")
        field = self._field(maintainer, "maintainer_repo_path")
        self.assertEqual(field["effective"], BAD_USER_PATH)
        self.assertIs(field["path_exists"], False)
        self.assertTrue(field["placeholder"]["zh"].endswith("repo"))
        self.assertEqual(field["placeholder"]["zh"], field["placeholder"]["en"])

    def test_launch_is_a_path_missing_400_not_a_500(self):
        with self.assertRaises(maintainer_launch.InvalidFieldError) as ctx:
            maintainer_launch.resolve(self.home)
        self.assertEqual(ctx.exception.message, "repo path does not exist")
        self.assertTrue(ctx.exception.details["path"].endswith("repo"))
        _httpd, port = start_server(self, self.home)
        with mock.patch.object(maintainer_launch.sys, "platform", "darwin"):   # 路径检查在 open 之前，走不到 opener
            status, obj = post_json(port, "/api/maintainer/terminal", {})
        self.assertEqual(status, 400)
        assert_envelope(self, obj, "INVALID_FIELD")
        self.assertEqual(obj["error"]["message"], "repo path does not exist")


if __name__ == "__main__":
    unittest.main()
