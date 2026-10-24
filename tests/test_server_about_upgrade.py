"""「关于」与 §79 升级器的 server 半边（CONTRACT §26 / §68.6 / §79；server/about.py）。

取代 v1 的 tests/test_server_about_deploy_state.py（§56 自动部署 agent、``deploy_state`` 现读与
``deploy_refused`` 409 随 Act 管线退役——全文见 tag full）。钉住：

1. ``GET /api/about`` 的形状：``update_available`` 由 server **现算**（缓存 latest 严格新于运行版本且
   检查开着 → ``{current, latest, url, pkg_asset_url, manual_upgrade}``，否则 null），``update_check``
   是缓存的公开子集（ETag 不外发）；
2. §26 v2.0.0 的周期触发：缓存缺席 / 超过 24h 且检查开着 → 后台起一次
   ``python -m act.lib.update_check``（GET 不等它）；缓存新鲜、检查关着、或
   ``AIASSISTANT_UPDATE_AUTOCHECK=0`` → 不起；
3. ``POST /api/update/install``：升级器 ``scripts/upgrade.sh`` 不在这个 checkout → 501
   ``upgrader_missing``（details 带手动命令）；在 → 分离起 ``/bin/bash scripts/upgrade.sh``，cwd = repo、
   日志 = ``<home>/state/upgrade.log``；跨大版本的 409 在 tests/test_server_about_manual_upgrade.py。
子进程一律经注入缝——测试绝不真跑升级器、绝不出网。
"""
import datetime as dt
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first
from tests.test_server_common import get_json, post_json, start_server, write_text

from act.lib import update_check
from server import about


def _iso(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class _Home(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-about-up-")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        (self.home / "state").mkdir(parents=True)
        patcher = mock.patch.object(about, "version", lambda: "2.0.0")
        patcher.start()
        self.addCleanup(patcher.stop)

    def _cache(self, **doc):
        write_text(self.home / "state" / "update_check.json", json.dumps(doc))


class UpdateAvailableTestCase(_Home):
    def test_newer_same_major_is_a_plain_update(self):
        self._cache(latest="2.0.1", url="U", checked_at=_iso(1e9), etag='W/"x"')
        snap = about.snapshot(self.home, now=1e9)
        self.assertEqual(snap["update_available"], {"current": "2.0.0", "latest": "2.0.1", "url": "U",
                                                    "pkg_asset_url": None, "manual_upgrade": False})
        self.assertEqual(snap["update_check"], {"checked_at": _iso(1e9), "latest": "2.0.1", "url": "U",
                                                "pkg_asset_url": None})   # ETag never leaves
        self.assertIs(snap["check_enabled"], True)
        self.assertEqual(snap["version"], "2.0.0")

    def test_new_major_is_flagged_manual(self):
        self._cache(latest="3.0.0", checked_at=_iso(1e9))
        self.assertIs(about.snapshot(self.home, now=1e9)["update_available"]["manual_upgrade"], True)

    def test_same_older_or_garbage_or_disabled_is_null(self):
        for doc in ({"latest": "2.0.0"}, {"latest": "1.9.9"}, {"latest": 7}, {}):
            with self.subTest(doc=doc):
                self._cache(checked_at=_iso(1e9), **doc)
                self.assertIsNone(about.snapshot(self.home, now=1e9)["update_available"])
        self._cache(latest="2.0.1", checked_at=_iso(1e9))
        write_text(self.home / "state" / "settings_overrides.json",
                   json.dumps({"updates_check_enabled": False}))
        snap = about.snapshot(self.home, now=1e9)
        self.assertIsNone(snap["update_available"])
        self.assertIs(snap["check_enabled"], False)

    def test_mirrors_the_act_side_projection(self):
        self.assertEqual(about.CHECK_INTERVAL_S, update_check.CHECK_INTERVAL_SECONDS)
        self.assertTrue(about.update_check.is_newer("2.0.1", "2.0.0"))


class PeriodicCheckTestCase(_Home):
    def setUp(self):
        super().setUp()
        env = mock.patch.dict(os.environ, {about.AUTOCHECK_ENV: "1"})
        env.start()
        self.addCleanup(env.stop)
        self.calls = []

    def _runner(self, argv, env, cwd, timeout_s):
        self.calls.append(argv)
        return 0, "{}", ""

    def _snap(self, now):
        about.snapshot(self.home, runner=self._runner, now=now)
        self.assertTrue(about.join_jobs_for_tests(10.0))

    def test_absent_or_stale_cache_starts_one_background_check(self):
        self._snap(now=1e9)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0][-1:], ["act.lib.update_check"])
        self._cache(latest="2.0.0", checked_at=_iso(1e9 - about.CHECK_INTERVAL_S - 1))
        self._snap(now=1e9)
        self.assertEqual(len(self.calls), 2)

    def test_fresh_cache_or_check_off_or_autocheck_off_starts_nothing(self):
        self._cache(latest="2.0.0", checked_at=_iso(1e9 - 60))
        self._snap(now=1e9)
        self.assertEqual(self.calls, [])
        self._cache(latest="2.0.0", checked_at=_iso(1))
        write_text(self.home / "state" / "settings_overrides.json",
                   json.dumps({"updates_check_enabled": False}))
        self._snap(now=1e9)
        self.assertEqual(self.calls, [])
        (self.home / "state" / "settings_overrides.json").unlink()
        with mock.patch.dict(os.environ, {about.AUTOCHECK_ENV: "0"}):
            self._snap(now=1e9)
        self.assertEqual(self.calls, [])

    def test_check_due_edges(self):
        self.assertTrue(about.check_due(None, now=1e9))
        self.assertTrue(about.check_due({"checked_at": "yesterday"}, now=1e9))
        self.assertFalse(about.check_due({"checked_at": _iso(1e9)}, now=1e9 + 10))
        self.assertTrue(about.check_due({"checked_at": _iso(1e9)}, now=1e9 + about.CHECK_INTERVAL_S))


class UpgraderTestCase(_Home):
    def setUp(self):
        super().setUp()
        self.spawned = []

    def _spawner(self, argv, cwd, log_path):
        self.spawned.append((argv, cwd, log_path))

    def test_missing_upgrader_is_501_with_the_manual_command(self):
        empty = Path(tempfile.mkdtemp(prefix="zai-about-norepo-"))
        with self.assertRaises(about.NotImplementedError501) as ctx:
            about.install_now({}, home=self.home, update_reader=lambda: {"latest": "2.0.1"},
                              spawner=self._spawner, repo=empty)
        details = ctx.exception.details
        self.assertEqual((details["reason"], details["script"]), ("upgrader_missing", "scripts/upgrade.sh"))
        self.assertEqual(details["fix"], "git fetch --tags origin && git checkout v2.0.1 && bash install.sh")
        self.assertEqual(self.spawned, [])

    def test_present_upgrader_is_spawned_detached_with_its_log(self):
        repo = Path(tempfile.mkdtemp(prefix="zai-about-repo-"))
        write_text(repo / "scripts" / "upgrade.sh", "#!/bin/bash\n")
        receipt = about.install_now({}, home=self.home, update_reader=lambda: {"latest": "2.0.1"},
                                    spawner=self._spawner, repo=repo)
        log = self.home / "state" / "upgrade.log"
        self.assertEqual(receipt, {"ok": True, "action": "upgrade", "script": "scripts/upgrade.sh",
                                   "log": str(log), "latest": "2.0.1"})
        self.assertEqual(self.spawned, [(["/bin/bash", str(repo / "scripts" / "upgrade.sh")], repo, log)])

    def test_default_spawner_is_detached(self):
        with mock.patch.object(about.subprocess, "Popen") as popen:
            about._default_spawner(["/bin/bash", "x"], self.home, self.home / "state" / "upgrade.log")
        self.assertTrue(popen.call_args[1]["start_new_session"])
        self.assertEqual(popen.call_args[1]["cwd"], str(self.home))

    def test_manual_fix_without_a_known_latest(self):
        self.assertEqual(about.manual_fix(""),
                         "git fetch --tags origin && git checkout v<latest> && bash install.sh")


class RouteTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-about-route-")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        (self.home / "state").mkdir(parents=True)
        _httpd, self.port = start_server(self, self.home)

    def test_get_about_shape(self):
        status, obj = get_json(self.port, "/api/about")
        self.assertEqual(status, 200)
        self.assertEqual(set(obj), {"version", "home", "repo", "update_available", "update_check",
                                    "check_enabled"})
        self.assertEqual(obj["home"], str(self.home))
        self.assertIsNone(obj["update_available"])
        self.assertNotIn("deploy_state", obj)     # §56 retired with auto-deploy

    def test_install_route_is_wired_to_the_upgrader(self):
        with mock.patch.object(about, "_require_upgrader",
                               side_effect=about.NotImplementedError501("no upgrader", {})):
            status, obj = post_json(self.port, "/api/update/install", {})
        self.assertEqual(status, 501, obj)
        with mock.patch.object(about, "_require_upgrader", return_value=Path("/x/scripts/upgrade.sh")), \
                mock.patch.object(about, "_default_spawner") as spawn:
            status, obj = post_json(self.port, "/api/update/install", {})
        self.assertEqual(status, 200, obj)
        self.assertEqual(obj["action"], "upgrade")
        spawn.assert_called_once()
        self.assertEqual(spawn.call_args[0][2], self.home / "state" / "upgrade.log")


if __name__ == "__main__":
    unittest.main()
