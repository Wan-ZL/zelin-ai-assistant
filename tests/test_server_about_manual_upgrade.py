"""「立即升级」不跨大版本（CONTRACT §26 / §79；PR #480 桥接语义，v2.0.0 保留）。

升级永不自己跨 major：`POST /api/update/install` 在 §26 缓存的 latest 的 major 大于运行
版本时**不起升级器**，回 409 `manual_upgrade`，details 带 current / latest / 手动命令。这一问
排在「升级器在不在」之前；同 major 的新版照旧往下走（升级器在就起，不在就 501）。v1 的
`major_boundary` 部署判决随 §56.3 自动部署退役（全文见 tag full），这里只剩缓存这一个依据。
"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first
from tests.test_server_common import post_json, start_server

from server import about


def _repo_with_upgrader() -> Path:
    root = Path(tempfile.mkdtemp(prefix="zai-about-repo-"))
    (root / "scripts").mkdir()
    (root / "scripts" / "upgrade.sh").write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
    return root


class ManualUpgradeTestCase(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(about, "version", lambda: "1.0.117")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.spawned = []
        self.repo = _repo_with_upgrader()

    def _spawner(self, argv, cwd, log_path):
        self.spawned.append((argv, cwd, log_path))

    def test_new_major_in_the_update_cache_is_409_without_spawning(self):
        with self.assertRaises(about.ConflictError) as ctx:
            about.install_now({}, update_reader=lambda: {"latest": "2.0.0", "url": "U"},
                              spawner=self._spawner, repo=self.repo)
        self.assertEqual(ctx.exception.status, 409)
        details = ctx.exception.details
        self.assertEqual(details["reason"], "manual_upgrade")
        self.assertIs(details["manual_upgrade"], True)
        self.assertEqual(details["current"], "1.0.117")
        self.assertEqual(details["latest"], "2.0.0")
        self.assertEqual(details["fix"], "git fetch --tags origin && git checkout v2.0.0 && bash install.sh")
        self.assertIn("v2.0.0", str(ctx.exception))
        self.assertIn("never cross a major version", str(ctx.exception))
        self.assertEqual(self.spawned, [])

    def test_a_2x_to_3x_major_is_refused_too(self):
        with mock.patch.object(about, "version", lambda: "2.4.1"):
            with self.assertRaises(about.ConflictError) as ctx:
                about.install_now({}, update_reader=lambda: {"latest": "3.0.0"},
                                  spawner=self._spawner, repo=self.repo)
        self.assertEqual(ctx.exception.details["latest"], "3.0.0")
        self.assertEqual(self.spawned, [])

    def test_the_refusal_comes_before_the_missing_upgrader_501(self):
        empty = Path(tempfile.mkdtemp(prefix="zai-about-norepo-"))
        with self.assertRaises(about.ConflictError):
            about.install_now({}, update_reader=lambda: {"latest": "2.0.0"},
                              spawner=self._spawner, repo=empty)

    def test_same_major_or_unknown_cache_goes_on_to_the_upgrader(self):
        for cache in ({"latest": "1.0.118"}, None, {"latest": 2}, {"latest": "garbage"}):
            self.spawned.clear()
            receipt = about.install_now({}, update_reader=lambda c=cache: c,
                                        spawner=self._spawner, repo=self.repo)
            self.assertTrue(receipt["ok"], cache)
            self.assertEqual(len(self.spawned), 1, cache)

    def test_unreadable_cache_is_not_a_refusal(self):
        def boom():
            raise OSError("torn")
        receipt = about.install_now({}, update_reader=boom, spawner=self._spawner, repo=self.repo)
        self.assertTrue(receipt["ok"])
        self.assertEqual(len(self.spawned), 1)

    def test_unknown_fields_still_come_first(self):
        with self.assertRaises(about.UnknownFieldError):
            about.install_now({"now": True}, update_reader=lambda: {"latest": "2.0.0"},
                              spawner=self._spawner, repo=self.repo)


class RouteTestCase(unittest.TestCase):
    """The route reads state/update_check.json under the server's own home."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-about-mu-")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        (self.home / "state").mkdir(parents=True)
        user_home = Path(self.tmp.name) / "user"
        user_home.mkdir()
        env = mock.patch.dict(os.environ, {"HOME": str(user_home), "USERPROFILE": str(user_home)})
        env.start()
        self.addCleanup(env.stop)
        _httpd, self.port = start_server(self, self.home)

    def test_route_answers_409_manual_upgrade(self):
        (self.home / "state" / "update_check.json").write_text(
            '{"latest": "2.0.0", "url": "U", "checked_at": "2026-10-23T00:00:00Z"}', encoding="utf-8")
        with mock.patch.object(about, "version", lambda: "1.0.117"), \
                mock.patch.object(about, "_default_spawner") as spawn:
            status, obj = post_json(self.port, "/api/update/install", {})
        self.assertEqual(status, 409, obj)
        self.assertIn("manual_upgrade", str(obj))
        spawn.assert_not_called()


if __name__ == "__main__":
    unittest.main()
