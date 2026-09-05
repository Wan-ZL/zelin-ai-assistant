"""诊断页日志清单里的 ingest 链日志 ``screenpipe-auto.log``（CONTRACT §15.2 / §68.4 2026-09-05 追记）。

原生 IngestModel.revealIngestLog：手动 ingest 失败后「查看日志」直指 ``/tmp/screenpipe-auto.log``
——ingest/process-screenpipe.sh 的 LOGFILE，完整的 claude 输出只在那里（server/ingest_run 的回执
只有 400 字尾巴）。web 的日志清单只扫三个目录、/tmp 不在其中，所以 ``paths.ingest_log_path()``
（``$PROCESS_SCREENPIPE_LOG`` 或默认路径）作为一条**显式**项进 ``_log_entries``：名字固定
``screenpipe-auto.log``（IngestPage 的 ``?log=`` 深链认这个名），``path`` 是真实路径，文件不在就不列；
``GET /api/logs/screenpipe-auto.log`` 与其它日志同一条尾巴通道（白名单 + size-cap）。

subproc.default_runner 经 mock.patch 替换——测试绝不真起 ``python -m act.doctor``。
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first
from tests.test_server_common import assert_envelope, get_json, start_server, write_text

from server import diagnostics, doctor_run, paths, subproc

DOCTOR_JSON = json.dumps({"home": "/x", "checks": []})


class IngestLogEntryTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-ingest-log-")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        (self.home / "state").mkdir(parents=True)
        user_home = Path(self.tmp.name) / "user"
        user_home.mkdir()
        # ingest 脚本的 LOGFILE seam（tests/integration/test_ingest_smoke.py 同一个 env）——绝不碰真 /tmp 日志
        self.log = Path(self.tmp.name) / "scratch" / "my-ingest-run.txt"
        env = mock.patch.dict(os.environ, {"HOME": str(user_home), "USERPROFILE": str(user_home),
                                           "PROCESS_SCREENPIPE_LOG": str(self.log)})
        env.start()
        self.addCleanup(env.stop)
        doctor_run.reset_cache_for_tests()
        self.addCleanup(doctor_run.reset_cache_for_tests)
        patcher = mock.patch.object(subproc, "default_runner", lambda argv, env, cwd, timeout_s: (0, DOCTOR_JSON, ""))
        patcher.start()
        self.addCleanup(patcher.stop)
        _httpd, self.port = start_server(self, self.home)

    def test_path_seam_mirrors_the_script_default(self):
        self.assertEqual(paths.ingest_log_path(), self.log)
        with mock.patch.dict(os.environ, {"PROCESS_SCREENPIPE_LOG": ""}):
            self.assertEqual(paths.ingest_log_path(), Path("/tmp/screenpipe-auto.log"))
        self.assertEqual(diagnostics.INGEST_LOG_NAME, "screenpipe-auto.log")

    def test_absent_file_is_not_listed_and_404s(self):
        _s, diag = get_json(self.port, "/api/diagnostics")
        self.assertEqual(diag["logs"], [])
        status, obj = get_json(self.port, "/api/logs/screenpipe-auto.log")
        self.assertEqual(status, 404)
        assert_envelope(self, obj, "NOT_FOUND")

    def test_present_file_is_listed_under_the_fixed_name_and_tailable(self):
        write_text(self.log, "claude said\nsomething long\nexit 1\n")
        write_text(self.home / "state" / "logs" / "R-101.log", "card log\n")
        _s, diag = get_json(self.port, "/api/diagnostics")
        by_name = {e["name"]: e for e in diag["logs"]}
        self.assertEqual(sorted(by_name), ["R-101.log", "screenpipe-auto.log"])
        entry = by_name["screenpipe-auto.log"]
        self.assertEqual(entry["path"], str(self.log))          # 名字固定、路径如实
        self.assertEqual(entry["size"], len("claude said\nsomething long\nexit 1\n"))
        status, tail = get_json(self.port, "/api/logs/screenpipe-auto.log?lines=2")
        self.assertEqual(status, 200)
        self.assertEqual(tail["lines"], ["something long", "exit 1"])
        self.assertEqual(tail["path"], str(self.log))

    def test_directory_at_that_path_is_not_a_log(self):
        self.log.mkdir(parents=True)
        _s, diag = get_json(self.port, "/api/diagnostics")
        self.assertEqual(diag["logs"], [])

    @unittest.skipIf(sys.platform == "win32" or getattr(os, "geteuid", lambda: 1)() == 0,
                     "chmod 000 does not block on Windows / root")
    def test_untraversable_parent_dir_is_treated_as_absent_not_500(self):
        """Path.is_file 在 3.9-3.12 只吞 ENOENT 一族——父目录 EACCES 会抛 PermissionError（3.13 起它自己吞）；
        诊断页与 /api/logs/* 在哪个解释器上都不许因此 500（§0 第 11 条）。"""
        write_text(self.home / "state" / "logs" / "R-101.log", "card log\n")
        parent = self.log.parent
        parent.mkdir(parents=True)
        parent.chmod(0)
        self.addCleanup(parent.chmod, 0o700)
        with self.assertRaises(PermissionError):
            self.log.stat()                                      # 前提：这台机器上确实是 EACCES（stat 每个版本都抛）
        status, diag = get_json(self.port, "/api/diagnostics")
        self.assertEqual(status, 200)
        self.assertEqual([e["name"] for e in diag["logs"]], ["R-101.log"])
        status, obj = get_json(self.port, "/api/logs/screenpipe-auto.log")
        self.assertEqual(status, 404)
        assert_envelope(self, obj, "NOT_FOUND")
        status, _tail = get_json(self.port, "/api/logs/R-101.log")
        self.assertEqual(status, 200)


if __name__ == "__main__":
    unittest.main()
