"""权限体检 FDA 清单里的 cron 条目（CONTRACT §25 / §68.3 追记 2026-09-05；parity gap
diagnostics-setup-cron-fda-guided-grant）。

原生 Doctor.swift CronFDA.beginGrant 把 ``/usr/sbin/cron`` 放进剪贴板再开「完全磁盘访问」面板；
web 的权限体检页按 ``GET /api/permissions`` ``fda.executables`` 渲染「复制路径」——清单里没有 cron
就没有地方复制。本判例钉住：``cron`` 条目在、路径是同一字面量（与 act/lib/fresh_install.CRON_BINARY
一致，server 不 import 它）、note 双语、排在壳 app 之前（FDA 四把 → GUI 三项）。
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first
from tests.test_server_common import get_json, start_server, write_text

from act.lib import fresh_install
from server import doctor_run, permissions, subproc

DOCTOR_JSON = json.dumps({"home": "/x", "checks": []})


class CronExecutableTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-perm-cron-")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        (self.home / "state").mkdir(parents=True)
        user_home = Path(self.tmp.name) / "user"
        user_home.mkdir()
        env = mock.patch.dict(os.environ, {"HOME": str(user_home), "USERPROFILE": str(user_home)})
        env.start()
        self.addCleanup(env.stop)
        doctor_run.reset_cache_for_tests()
        self.addCleanup(doctor_run.reset_cache_for_tests)
        runner = mock.patch.object(subproc, "default_runner", lambda argv, env, cwd, timeout_s: (0, DOCTOR_JSON, ""))
        runner.start()
        self.addCleanup(runner.stop)
        _httpd, self.port = start_server(self, self.home)

    def test_cron_binary_is_one_literal_across_act_and_server(self):
        self.assertEqual(permissions.CRON_BINARY, "/usr/sbin/cron")
        self.assertEqual(permissions.CRON_BINARY, fresh_install.CRON_BINARY)

    def test_executables_list_cron_with_the_copyable_path(self):
        entries = permissions.executables(self.home)
        roles = [e["role"] for e in entries]
        self.assertIn("cron", roles)
        cron = entries[roles.index("cron")]
        self.assertEqual(cron["path"], "/usr/sbin/cron")
        # exists 如实：darwin 有、linux CI 通常没有——两边都不许崩
        self.assertEqual(cron["exists"], os.path.exists("/usr/sbin/cron"))
        self.assertIn("vault", cron["note"]["en"].lower())
        self.assertIn("完全磁盘访问", cron["note"]["zh"])
        # FDA 四把（守护 python / claude / node / cron）在前，GUI 三项的壳 app 收尾
        self.assertLess(roles.index("node"), roles.index("cron"))
        self.assertEqual(roles[-1], "shell_app")

    def test_snapshot_carries_cron_over_http(self):
        write_text(self.home / "config" / "runtime.json", json.dumps({"python": "/usr/bin/python3"}))
        status, obj = get_json(self.port, "/api/permissions")
        self.assertEqual(status, 200)
        roles = {e["role"]: e for e in obj["fda"]["executables"]}
        self.assertEqual(roles["cron"]["path"], permissions.CRON_BINARY)
        self.assertEqual(set(roles["cron"]["note"]), {"zh", "en"})


if __name__ == "__main__":
    unittest.main()
