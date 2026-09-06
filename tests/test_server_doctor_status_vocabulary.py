"""doctor 行的 status 词表 = §25 小写 ``ok|warn|fail``，server/doctor_run 是唯一归一边界（CONTRACT §25 / §68.4 2026-09-05 追记）。

act/lib/checks/core 的常量就是小写、``python -m act.doctor --json`` 原样吐小写；曾经 web 全按大写比、
server/ai_fix_launch 也按 ``("FAIL", "WARN")`` 过滤——真数据上「让 AI 修」的上下文永远是 ``0 check(s) not OK``，
依赖快速行每行都算没过。这里钉三件事：
- ``_succeeded`` 把 status 归一成小写（大写 / 混写夹具进来也是小写出去），其余键原样；
- ``ai_fix_launch.context_for_doctor`` 用**真形**（小写）报告能数出 fail + warn 行，徽记印大写；
- ``permissions.tcc_rows`` 与 ``doctor_run.counts`` 在真形上照常工作。

subproc.default_runner 经 mock.patch 替换——测试绝不真起 ``python -m act.doctor``。
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first
from tests.test_server_common import get_json, start_server

from server import ai_fix_launch, doctor_run, permissions, subproc

# 照抄真 `python3 -m act.doctor --json --fast` 的行形（2026-09-05 临时 home 一跑；detail 截短）
REAL_ROWS = [
    {"name": "claude CLI", "status": "ok", "detail": "/Users/demo/.local/bin/claude (2.1.261 (Claude Code))",
     "fix": "", "failure_id": "", "action_id": "", "row_class": ""},
    {"name": "cron disk access", "status": "warn", "detail": "no probe yet - the cron chain has not run",
     "fix": "rerun bash install.sh (updates the cron line), then wait ~30 min", "failure_id": "", "action_id": "", "row_class": ""},
    {"name": "dashboard", "status": "fail", "detail": "state/dashboard.json missing - the app shows 'missing' forever",
     "fix": "start actd (bash install.sh), or seed once: python3 -m act.lib.dashboard", "failure_id": "", "action_id": "", "row_class": ""},
    {"name": "launchd claude", "status": "fail", "detail": "claude in launchd cannot read the vault (EPERM)",
     "fix": "grant Full Disk Access", "failure_id": "claude_blind", "action_id": "open_fda", "row_class": ""},
]


class _Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-doctor-vocab-")
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

    def use_rows(self, rows):
        payload = json.dumps({"home": "/x", "checks": rows})
        patcher = mock.patch.object(subproc, "default_runner", lambda argv, env, cwd, timeout_s: (0, payload, ""))
        patcher.start()
        self.addCleanup(patcher.stop)


class StatusNormalisedAtTheServerBoundaryTestCase(_Case):
    def test_real_lowercase_rows_pass_through_unchanged(self):
        self.use_rows(REAL_ROWS)
        report = doctor_run.report(self.home)
        self.assertEqual(report["checks"], REAL_ROWS)
        self.assertEqual(doctor_run.counts(report["checks"]), {"ok": 1, "warn": 1, "fail": 2})

    def test_uppercase_or_mixed_rows_come_out_lowercase_with_other_keys_intact(self):
        self.use_rows([{"name": "a", "status": "OK", "detail": "d", "fix": "", "failure_id": "x"},
                       {"name": "b", "status": "Warn", "detail": "d"},
                       {"name": "c", "status": None, "detail": "d"},
                       "not a row"])
        checks = doctor_run.report(self.home)["checks"]
        self.assertEqual([c["status"] for c in checks], ["ok", "warn", ""])
        self.assertEqual(checks[0], {"name": "a", "status": "ok", "detail": "d", "fix": "", "failure_id": "x"})

    def test_wire_shape_over_http_is_lowercase(self):
        self.use_rows(REAL_ROWS)
        _httpd, port = start_server(self, self.home)
        _s, obj = get_json(port, "/api/doctor")
        self.assertEqual([c["status"] for c in obj["checks"]], ["ok", "warn", "fail", "fail"])
        _s, diag = get_json(port, "/api/diagnostics")
        self.assertEqual([c["status"] for c in diag["doctor"]["checks"]], ["ok", "warn", "fail", "fail"])


class ConsumersReadTheLowercaseVocabularyTestCase(_Case):
    def test_ai_fix_context_counts_fail_and_warn_rows_of_a_real_report(self):
        self.use_rows(REAL_ROWS)
        context = ai_fix_launch.context_for_doctor(self.home)
        lines = context.split("\n")
        self.assertEqual(lines[0], "doctor --fast: 3 check(s) not OK")
        self.assertEqual(lines[1], "WARN cron disk access: no probe yet - the cron chain has not run"
                                   " (fix: rerun bash install.sh (updates the cron line), then wait ~30 min)")
        self.assertTrue(lines[2].startswith("FAIL dashboard: "))
        self.assertTrue(lines[3].startswith("FAIL launchd claude: "))
        self.assertNotIn("claude CLI", context)                 # ok 行不进上下文

    def test_permissions_tcc_rows_come_from_the_real_shape(self):
        self.use_rows(REAL_ROWS)
        _httpd, port = start_server(self, self.home)
        _s, obj = get_json(port, "/api/permissions")
        self.assertEqual([(r["name"], r["status"]) for r in obj["doctor"]],
                         [("cron disk access", "warn"), ("launchd claude", "fail")])   # TCC_ROW_NAMES + TCC_FAILURE_IDS
        self.assertEqual([r["failure_id"] for r in permissions.tcc_rows({"checks": REAL_ROWS})], ["", "claude_blind"])


if __name__ == "__main__":
    unittest.main()
