"""「一键更新」不跨大版本（CONTRACT §26 / §56.3 追记 2026-10-23，lite 2.0 之前的桥接版本）。

§56.3 的部署脚本永不跨 major（写 `major_boundary`），所以 `POST /api/update/install` 在
§26 缓存的 latest 的 major 大于运行版本、或上一轮部署判决就是 `major_boundary` 时
**不 kickstart**，回 409 `manual_upgrade`，details 带 current / latest / 手动命令。
这一问排在「agent 是否加载」之前；同 major 的新版照旧 kickstart。
"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first
from tests.test_server_common import post_json, start_server

from act.lib import deploy_state
from server import about


def _runner(loaded=True):
    seen = []

    def run(argv):
        seen.append(argv)
        return (0 if loaded else 113), ""
    return run, seen


def _kickstarts(seen):
    return [argv for argv in seen if len(argv) > 1 and argv[1] == "kickstart"]


class ManualUpgradeTestCase(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(about, "version", lambda: "1.0.117")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_new_major_in_the_update_cache_is_409_without_kickstart(self):
        for loaded in (True, False):
            run, seen = _runner(loaded=loaded)
            with self.assertRaises(about.ConflictError) as ctx:
                about.install_now({}, runner=run, platform="darwin", state_reader=lambda: None,
                                  update_reader=lambda: {"latest": "2.0.0", "url": "U"})
            self.assertEqual(ctx.exception.status, 409)
            details = ctx.exception.details
            self.assertEqual(details["reason"], "manual_upgrade")
            self.assertIs(details["manual_upgrade"], True)
            self.assertEqual(details["current"], "1.0.117")
            self.assertEqual(details["latest"], "2.0.0")
            self.assertEqual(details["fix"], "git fetch --tags origin && git checkout v2.0.0 && bash install.sh")
            self.assertIn("v2.0.0", str(ctx.exception))
            self.assertIn("never crosses a major version", str(ctx.exception))
            self.assertEqual(_kickstarts(seen), [], "loaded=%s must not kickstart" % loaded)

    def test_major_boundary_verdict_is_409_even_without_a_cache(self):
        run, seen = _runner(loaded=True)
        state = {"status": deploy_state.MAJOR_BOUNDARY,
                 "detail": "origin/main carries v2.0.0 (new major version)"}
        with self.assertRaises(about.ConflictError) as ctx:
            about.install_now({}, runner=run, platform="darwin", state_reader=lambda: dict(state),
                              update_reader=lambda: None)
        details = ctx.exception.details
        self.assertEqual(details["reason"], "manual_upgrade")
        self.assertEqual(details["deploy_status"], "major_boundary")
        self.assertEqual(details["deploy_detail"], state["detail"])
        self.assertEqual(details["fix"], deploy_state.auto_deploy_fix(deploy_state.MAJOR_BOUNDARY))
        self.assertEqual(_kickstarts(seen), [])

    def test_same_major_update_still_kickstarts(self):
        for cache in ({"latest": "1.0.118"}, None, {"latest": 2}, {"latest": "garbage"}):
            run, seen = _runner(loaded=True)
            receipt = about.install_now({}, runner=run, platform="darwin", state_reader=lambda: None,
                                        update_reader=lambda c=cache: c)
            self.assertTrue(receipt["ok"], cache)
            self.assertEqual(len(_kickstarts(seen)), 1, cache)

    def test_unreadable_cache_is_not_a_refusal(self):
        def boom():
            raise OSError("torn")
        run, seen = _runner(loaded=True)
        receipt = about.install_now({}, runner=run, platform="darwin", state_reader=lambda: None,
                                    update_reader=boom)
        self.assertTrue(receipt["ok"])
        self.assertEqual(len(_kickstarts(seen)), 1)

    def test_gates_still_come_first(self):
        with self.assertRaises(about.UnknownFieldError):
            about.install_now({"now": True}, runner=lambda a: (0, ""), platform="darwin",
                              update_reader=lambda: {"latest": "2.0.0"})
        with self.assertRaises(about.NotImplementedError501):
            about.install_now({}, runner=lambda a: (0, ""), platform="linux",
                              update_reader=lambda: {"latest": "2.0.0"})


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
                mock.patch.object(about.repair, "default_runner", lambda argv: (0, "")), \
                mock.patch.object(about.sys, "platform", "darwin"):
            status, obj = post_json(self.port, "/api/update/install", {})
        self.assertEqual(status, 409, obj)
        self.assertIn("manual_upgrade", str(obj))


if __name__ == "__main__":
    unittest.main()
