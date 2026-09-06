"""server/ 依赖检查 / 录制与数据接入 / 关于 三面的 §66 清账补丁（CONTRACT §15.1 / §15.2 / §25 / §68.4 / §68.6）。

- ``GET /api/diagnostics`` add-only：``cron_probe``（state/cron_probe.json 公开子集）、``activity``
  （screenpipe db / actd.log / vault unprocessed 三个时间戳；server 永不读 ~/Documents），日志清单
  第三个目录 ``~/.screenpipe/``（engine.log）；
- ``POST /api/ingest/export`` / ``POST /api/ingest/run`` + ``GET /api/ingest/jobs/{id}``：同一条 ingest/ 脚本、
  同一套退出码，POST 回 job id、后台线程跑、轮询拿回执；``SCREENPIPE_NO_WAIT=1`` 只给 ingest；同脚本在跑即复用；
  job 表有上限、running 不淘汰；脚本缺席 404、多余字段 400、未知 job 404；
- ``POST /api/update/install``：kickstart 自动部署 agent（不带 -k）；未加载 409、非 darwin 501；
- ``GET /api/failures``：§25 FailureCatalog 的 server-owned 投影，每个 id 双语句都在；
- 卸载 404 / open 失败的 details 带手动命令。
子进程一律经注入缝——测试绝不真跑脚本 / launchctl。
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first
from tests.test_server_common import assert_envelope, get_json, post_json, start_server, write_text

from act.lib import failures
from act.lib.checks import launchd as launchd_checks
from server import about, diagnostics, doctor_run, failure_catalog, ingest_run, paths, subproc, uninstall_launch

DOCTOR_JSON = json.dumps({"home": "/x", "checks": []})


class _ServerCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-r2a-")
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


class DiagnosticsAddOnlyTestCase(_ServerCase):
    def test_cron_probe_public_subset_and_absent(self):
        self.patch_runner()
        _s, obj = get_json(self.port, "/api/diagnostics")
        self.assertIsNone(obj["cron_probe"])
        write_text(self.home / "state" / "cron_probe.json", json.dumps(
            {"ts": "2026-09-02T11:30:00Z", "read_ok": True, "protected_path": "/Users/d/Documents/V", "extra": 1}))
        _s, obj = get_json(self.port, "/api/diagnostics")
        self.assertEqual(obj["cron_probe"], {"ts": "2026-09-02T11:30:00Z", "read_ok": True,
                                             "protected_path": "/Users/d/Documents/V"})
        write_text(self.home / "state" / "cron_probe.json", "{torn")
        self.assertIsNone(diagnostics.cron_probe(self.home))

    def test_activity_timestamps_null_when_absent_and_epoch_when_present(self):
        self.patch_runner()
        _s, obj = get_json(self.port, "/api/diagnostics")
        act = obj["activity"]
        self.assertIsNone(act["screenpipe_db"]["mtime"])
        self.assertIsNone(act["actd_log"]["mtime"])
        self.assertEqual(act["actd_log"]["path"], str(self.home / "state" / "actd.log"))
        write_text(self.user_home / ".screenpipe" / "db.sqlite", "x")
        write_text(self.home / "state" / "actd.log", "log")
        _s, obj = get_json(self.port, "/api/diagnostics")
        self.assertIsInstance(obj["activity"]["screenpipe_db"]["mtime"], int)
        self.assertIsInstance(obj["activity"]["actd_log"]["mtime"], int)

    def test_unprocessed_reads_the_mirror_in_mirror_mode(self):
        write_text(self.home / "state" / "vault_sync_mode", "mirror\n")
        unprocessed = self.home / "state" / "vault-mirror" / "1 - unprocessed"
        write_text(unprocessed / "note.md", "n")
        write_text(unprocessed / ".DS_Store", "junk")
        entry = diagnostics.unprocessed_activity(self.home)
        self.assertTrue(entry["readable"])
        self.assertIsInstance(entry["mtime"], int)
        self.assertTrue(entry["path"].endswith("1 - unprocessed"))

    def test_unprocessed_never_lists_documents_in_direct_mode(self):
        # 默认 obsidian_raw 住 ~/Documents → 直连模式如实 readable:false、不列目录
        entry = diagnostics.unprocessed_activity(self.home)
        self.assertFalse(entry["readable"])
        self.assertIsNone(entry["mtime"])
        self.assertIn("Documents", entry["path"])
        # 不在保护位置的 vault（override 指到 home 下）→ 列目录；空目录 = null
        write_text(self.home / "state" / "settings_overrides.json",
                   json.dumps({"obsidian_raw": str(self.home / "vault" / "2 - raw")}))
        (self.home / "vault" / "1 - unprocessed").mkdir(parents=True)
        entry = diagnostics.unprocessed_activity(self.home)
        self.assertTrue(entry["readable"])
        self.assertIsNone(entry["mtime"])

    def test_engine_log_is_listed_and_tailable(self):
        self.patch_runner()
        write_text(self.user_home / ".screenpipe" / "engine.log", "boot\ncrash\n")
        write_text(self.user_home / ".screenpipe" / "db.sqlite", "not a log")
        _s, diag = get_json(self.port, "/api/diagnostics")
        self.assertEqual([e["name"] for e in diag["logs"]], ["engine.log"])
        status, obj = get_json(self.port, "/api/logs/engine.log?lines=1")
        self.assertEqual(status, 200)
        self.assertEqual(obj["lines"], ["crash"])


class IngestRunTestCase(_ServerCase):
    """POST 立刻回 job id、脚本在后台线程跑；route 测试里把线程换成同步 spawn（patch _default_spawn）。"""

    def setUp(self):
        super().setUp()
        ingest_run.reset_jobs_for_tests()
        self.addCleanup(ingest_run.reset_jobs_for_tests)
        inline = mock.patch.object(ingest_run, "_default_spawn", lambda fn: fn())
        inline.start()
        self.addCleanup(inline.stop)

    def test_export_runs_the_export_script_under_home(self):
        self.patch_runner(rc=0, out="exported 3 files\n")
        status, obj = post_json(self.port, "/api/ingest/export", {})
        self.assertEqual(status, 200)
        self.assertEqual((obj["ok"], obj["state"], obj["script"], obj["reused"]), (True, "running", ingest_run.EXPORT_SCRIPT, False))
        status, job = get_json(self.port, "/api/ingest/jobs/" + obj["job"])
        self.assertEqual(status, 200)
        self.assertEqual(job["state"], "done")
        self.assertEqual((job["ok"], job["rc"], job["skipped"], job["tail"]), (True, 0, False, "exported 3 files"))
        call = self.calls[0]
        self.assertEqual(call["argv"], ["/bin/bash", str(paths.repo_root() / ingest_run.EXPORT_SCRIPT)])
        self.assertEqual(call["env"]["AIASSISTANT_HOME"], str(self.home))
        self.assertNotIn("SCREENPIPE_NO_WAIT", call["env"])
        self.assertEqual(call["timeout"], ingest_run.EXPORT_TIMEOUT_S)

    def test_ingest_sets_no_wait_and_exit_3_is_skipped(self):
        self.patch_runner(rc=3, out="another ingest holds the lock\n")
        _s, obj = post_json(self.port, "/api/ingest/run", {})
        _s, job = get_json(self.port, "/api/ingest/jobs/" + obj["job"])
        self.assertEqual((job["ok"], job["rc"], job["skipped"]), (False, 3, True))
        call = self.calls[0]
        self.assertEqual(call["argv"][1], str(paths.repo_root() / ingest_run.INGEST_SCRIPT))
        self.assertEqual(call["env"]["SCREENPIPE_NO_WAIT"], "1")
        self.assertEqual(call["timeout"], ingest_run.INGEST_TIMEOUT_S)

    def test_failure_carries_the_tail_and_export_exit_3_is_not_a_skip(self):
        self.patch_runner(rc=1, out="step 1 ok", err="claude: boom")
        _s, obj = post_json(self.port, "/api/ingest/run", {})
        _s, job = get_json(self.port, "/api/ingest/jobs/" + obj["job"])
        self.assertEqual((job["ok"], job["skipped"], job["tail"]), (False, False, "step 1 ok\nclaude: boom"))
        self.assertIsInstance(job["seconds"], (int, float))
        receipt = ingest_run.export_now(self.home, {}, runner=lambda a, e, c, t: (3, "", ""), now=lambda: 1.0, spawn=lambda fn: fn())
        job = ingest_run.job_status(receipt["job"])
        self.assertEqual((job["skipped"], job["seconds"]), (False, 0.0))

    def test_running_job_is_reused_and_only_running_state_is_exposed(self):
        pending = []
        receipt = ingest_run.ingest_now(self.home, {}, runner=lambda a, e, c, t: (0, "", ""), spawn=pending.append)
        job = ingest_run.job_status(receipt["job"])
        self.assertEqual(job["state"], "running")
        self.assertNotIn("rc", job)
        again = ingest_run.ingest_now(self.home, {}, runner=lambda a, e, c, t: (0, "", ""), spawn=pending.append)
        self.assertEqual((again["job"], again["reused"]), (receipt["job"], True))
        self.assertEqual(len(pending), 1)
        pending[0]()
        self.assertEqual(ingest_run.job_status(receipt["job"])["state"], "done")
        # export 是另一条脚本：不复用 ingest 的 job
        other = ingest_run.export_now(self.home, {}, runner=lambda a, e, c, t: (0, "", ""), spawn=lambda fn: fn())
        self.assertNotEqual(other["job"], receipt["job"])

    def test_job_table_is_capped_and_never_evicts_running(self):
        run = lambda a, e, c, t: (0, "", "")  # noqa: E731
        for _ in range(ingest_run.JOBS_CAP + 5):
            ingest_run.export_now(self.home, {}, runner=run, spawn=lambda fn: fn())
        held = []
        keep = ingest_run.ingest_now(self.home, {}, runner=run, spawn=held.append)
        for _ in range(3):
            ingest_run.export_now(self.home, {}, runner=run, spawn=lambda fn: fn())
        self.assertLessEqual(len(ingest_run._jobs), ingest_run.JOBS_CAP + 1)
        self.assertEqual(ingest_run.job_status(keep["job"])["state"], "running")

    def test_gates(self):
        status, obj = post_json(self.port, "/api/ingest/export", {"force": True})
        self.assertEqual(status, 400)
        assert_envelope(self, obj, "UNKNOWN_FIELD")
        with mock.patch.object(ingest_run.paths, "repo_root", lambda: Path(self.tmp.name)):
            status, obj = post_json(self.port, "/api/ingest/run", {})
        self.assertEqual(status, 404)
        assert_envelope(self, obj, "NOT_FOUND")
        status, obj = get_json(self.port, "/api/ingest/jobs/nope")
        self.assertEqual(status, 404)
        assert_envelope(self, obj, "NOT_FOUND")

    def test_scripts_exist_in_the_repo(self):
        for rel in (ingest_run.EXPORT_SCRIPT, ingest_run.INGEST_SCRIPT):
            self.assertTrue((paths.repo_root() / rel).is_file(), rel)


class UpdateInstallTestCase(_ServerCase):
    def _runner(self, loaded=True, kick_rc=0):
        seen = []

        def run(argv):
            seen.append(argv)
            if argv[1] == "print":
                return (0 if loaded else 113), ""
            return kick_rc, "kick failed" if kick_rc else ""
        return run, seen

    def test_label_mirrors_the_doctor(self):
        self.assertEqual(about.AUTODEPLOY_LABEL, launchd_checks.AUTODEPLOY_LABEL)

    def test_kickstart_without_minus_k_when_loaded(self):
        run, seen = self._runner(loaded=True)
        receipt = about.install_now({}, runner=run, platform="darwin")
        self.assertEqual(receipt, {"ok": True, "label": about.AUTODEPLOY_LABEL, "action": "kickstart"})
        self.assertEqual(seen[1][:3], ["/bin/launchctl", "kickstart", seen[1][2]])
        self.assertNotIn("-k", seen[1])
        self.assertTrue(seen[1][2].endswith("/" + about.AUTODEPLOY_LABEL))

    def test_not_loaded_is_409_and_kick_failure_500(self):
        run, _seen = self._runner(loaded=False)
        with self.assertRaises(about.ConflictError):
            about.install_now({}, runner=run, platform="darwin")
        run, _seen = self._runner(loaded=True, kick_rc=1)
        with self.assertRaises(about.ApiError) as ctx:
            about.install_now({}, runner=run, platform="darwin")
        self.assertEqual(ctx.exception.status, 500)

    def test_gates(self):
        with self.assertRaises(about.UnknownFieldError):
            about.install_now({"now": True}, runner=lambda a: (0, ""), platform="darwin")
        with self.assertRaises(about.NotImplementedError501):
            about.install_now({}, runner=lambda a: (0, ""), platform="linux")

    def test_route_is_wired(self):
        with mock.patch.object(about.repair, "default_runner", lambda argv: (0, "")), \
                mock.patch.object(about.sys, "platform", "darwin"):
            status, obj = post_json(self.port, "/api/update/install", {})
        self.assertEqual(status, 200)
        self.assertEqual(obj["action"], "kickstart")


class FailureCatalogTestCase(_ServerCase):
    def test_every_failure_id_has_both_sentences(self):
        status, obj = get_json(self.port, "/api/failures")
        self.assertEqual(status, 200)
        self.assertEqual(set(obj["failures"]), set(failures.FAILURES))
        for fid, entry in obj["failures"].items():
            self.assertTrue(entry["zh"] and entry["en"], fid)
            self.assertEqual(entry["action_id"], failures.FAILURES[fid].get("action_id"))
        self.assertEqual(failure_catalog.catalog()["failures"]["engine_crashed"]["zh"],
                         failures.FAILURES["engine_crashed"]["plain_zh"])


class UninstallDetailsTestCase(_ServerCase):
    def test_missing_script_and_open_failure_carry_the_manual_command(self):
        with mock.patch.object(uninstall_launch, "script_path", lambda: Path(self.tmp.name) / "nope.sh"):
            with self.assertRaises(uninstall_launch.NotFoundError) as ctx:
                uninstall_launch.launch({}, opener=lambda p: None, platform="darwin")
        self.assertEqual(ctx.exception.details["command"], uninstall_launch.shell_command())

        def boom(_path):
            raise OSError("no Terminal")
        with self.assertRaises(uninstall_launch.ApiError) as ctx:
            uninstall_launch.launch({}, opener=boom, out_dir=Path(self.tmp.name), platform="darwin")
        self.assertEqual(ctx.exception.details["command"], uninstall_launch.shell_command())
        self.assertIn("command_file", ctx.exception.details)


if __name__ == "__main__":
    unittest.main()
