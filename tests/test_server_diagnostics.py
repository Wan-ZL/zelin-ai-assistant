"""server/ 诊断面（CONTRACT §23 / §25 / §47.4 / §56 / §68.4）：doctor 子进程注入、缓存、
日志尾巴的 size-cap 与白名单。

subproc.default_runner 经 mock.patch 替换——测试绝不真起 ``python -m act.doctor``。
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first
from tests.test_server_common import assert_envelope, get_json, start_server, write_text

from server import doctor_run, subproc

DOCTOR_JSON = json.dumps({"home": "/x", "checks": [
    {"name": "claude CLI", "status": "OK", "detail": "ok", "fix": "", "failure_id": "", "action_id": ""},
    {"name": "launchd claude", "status": "FAIL", "detail": "blind", "fix": "grant FDA",
     "failure_id": "claude_blind", "action_id": "open_fda"},
]}, indent=1)


class _ServerCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-diag-")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        (self.home / "state").mkdir(parents=True)
        self.user_home = Path(self.tmp.name) / "user"
        self.user_home.mkdir()
        env = mock.patch.dict(os.environ, {"HOME": str(self.user_home), "USERPROFILE": str(self.user_home)})
        env.start()
        self.addCleanup(env.stop)
        doctor_run.reset_cache_for_tests()
        self.addCleanup(doctor_run.reset_cache_for_tests)
        self.calls = []
        _httpd, self.port = start_server(self, self.home)

    def patch_runner(self, rc=0, out=DOCTOR_JSON, err=""):
        def runner(argv, env, cwd, timeout_s):
            self.calls.append({"argv": argv, "env": env, "cwd": cwd, "timeout": timeout_s})
            return rc, out, err
        patcher = mock.patch.object(subproc, "default_runner", runner)
        patcher.start()
        self.addCleanup(patcher.stop)


class DoctorRouteTestCase(_ServerCase):
    def test_doctor_runs_act_doctor_json_fast_under_home(self):
        self.patch_runner()
        status, obj = get_json(self.port, "/api/doctor")
        self.assertEqual(status, 200)
        self.assertTrue(obj["ok"])
        self.assertEqual([c["name"] for c in obj["checks"]], ["claude CLI", "launchd claude"])
        call = self.calls[0]
        self.assertEqual(call["argv"][1:], ["-m", "act.doctor", "--json", "--fast"])
        self.assertEqual(call["env"]["AIASSISTANT_HOME"], str(self.home))
        self.assertTrue((call["cwd"] / "act").is_dir())

    def test_fast_0_drops_the_flag(self):
        self.patch_runner()
        get_json(self.port, "/api/doctor?fast=0")
        self.assertEqual(self.calls[0]["argv"][-1], "--json")

    def test_cached_within_ttl_and_refresh_bypasses(self):
        self.patch_runner()
        get_json(self.port, "/api/doctor")
        get_json(self.port, "/api/doctor")
        self.assertEqual(len(self.calls), 1)
        get_json(self.port, "/api/doctor?refresh=1")
        self.assertEqual(len(self.calls), 2)

    def test_non_json_output_is_honest_not_500(self):
        self.patch_runner(rc=1, out="Traceback...", err="boom")
        status, obj = get_json(self.port, "/api/doctor")
        self.assertEqual(status, 200)
        self.assertFalse(obj["ok"])
        self.assertEqual(obj["checks"], [])
        self.assertIn("boom", obj["error"])

    def test_counts_helper(self):
        self.assertEqual(doctor_run.counts([{"status": "OK"}, {"status": "FAIL"}, {"status": "weird"}]),
                         {"ok": 1, "warn": 0, "fail": 1})


class DiagnosticsRouteTestCase(_ServerCase):
    def test_snapshot_shape_and_sources(self):
        self.patch_runner()
        write_text(self.home / "state" / "dashboard.json", json.dumps({
            "generated_at": "2026-09-02T00:00:00Z", "counts": {},
            "deploy_state": {"status": "deployed", "version": "0.48.22"},
            "radar_sources": {"gmail": {"enabled": True, "last_ok": None, "skip_reason": "no_credentials", "stale": False}},
        }))
        write_text(self.home / "state" / "install_report.json", json.dumps({
            "version": "0.48.22", "generated_at": "x", "ok": True,
            "steps": [{"name": "cron", "status": "skipped_tcc", "detail": "EPERM", "secret": "no"}]}))
        status, obj = get_json(self.port, "/api/diagnostics")
        self.assertEqual(status, 200)
        self.assertTrue(obj["doctor"]["ok"])
        self.assertIn("verdict", obj["health"])
        self.assertEqual(obj["deploy_state"]["status"], "deployed")
        self.assertEqual(obj["radar_sources"]["gmail"]["skip_reason"], "no_credentials")
        self.assertEqual(obj["install_report"]["steps"], [{"name": "cron", "status": "skipped_tcc", "detail": "EPERM"}])
        self.assertIn(obj["registry_backend"], ("yaml", "sqlite"))
        self.assertEqual(obj["logs"], [])

    def test_missing_files_are_null_not_errors(self):
        self.patch_runner()
        _s, obj = get_json(self.port, "/api/diagnostics")
        self.assertIsNone(obj["deploy_state"])
        self.assertIsNone(obj["install_report"])
        self.assertIsNone(obj["radar_sources"])


class LogsRouteTestCase(_ServerCase):
    def _log(self, name, text, where="user"):
        base = (self.user_home / "Library" / "Logs" / "zelin-ai-assistant") if where == "user" \
            else self.home / "state" / "logs"
        write_text(base / name, text)
        return base / name

    def test_lists_logs_from_both_dirs_and_tails_them(self):
        self.patch_runner()
        self._log("actd.launchd.log", "one\ntwo\nthree\n")
        self._log("R-101.log", "card log\n", where="state")
        self._log("notes.txt", "not a log\n")
        _s, diag = get_json(self.port, "/api/diagnostics")
        names = sorted(e["name"] for e in diag["logs"])
        self.assertEqual(names, ["R-101.log", "actd.launchd.log"])
        status, obj = get_json(self.port, "/api/logs/actd.launchd.log?lines=2")
        self.assertEqual(status, 200)
        self.assertEqual(obj["lines"], ["two", "three"])
        self.assertTrue(obj["truncated"])
        _s, full = get_json(self.port, "/api/logs/actd.launchd.log")
        self.assertEqual(full["lines"], ["one", "two", "three"])
        self.assertFalse(full["truncated"])

    def test_tail_is_byte_capped(self):
        self._log("big.log", "".join("line %06d\n" % i for i in range(20_000)))
        _s, obj = get_json(self.port, "/api/logs/big.log?lines=5000")
        self.assertEqual(len(obj["lines"]), 1000)   # TAIL_LINES_MAX
        self.assertEqual(obj["lines"][-1], "line 019999")
        self.assertTrue(obj["truncated"])

    def test_bad_or_unknown_names_are_rejected(self):
        status, obj = get_json(self.port, "/api/logs/..%2F..%2Fetc%2Fpasswd")
        self.assertIn(status, (400, 404))
        status, obj = get_json(self.port, "/api/logs/passwd.log")
        self.assertEqual(status, 404)
        assert_envelope(self, obj, "NOT_FOUND")
        self._log("actd.launchd.log", "x\n")
        status, obj = get_json(self.port, "/api/logs/actd.launchd.log?lines=abc")
        self.assertEqual(status, 400)
        assert_envelope(self, obj, "INVALID_FIELD")

    def test_txt_files_are_not_served_even_if_present(self):
        self._log("secret.txt", "x\n")
        status, _obj = get_json(self.port, "/api/logs/secret.txt")
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()
