"""「从我的消息生成/更新档案」的 Python 半边（CONTRACT §68.1 追记 / §10 / D47）：inbox 特形 ``voice_generate``
→ actd ``_DETACHED_ACTIONS`` → act/lib/voice_job（没在跑才分离起 ``act.voice_gen --job``，spawn 前写
``state/voice_gen/job.json`` running）→ 子进程 ``act.voice_gen --job`` 跑完写 done / failed（保留 started_at）。

绝不真 spawn：detached.spawn 注入记录器；voice_gen 用注入 runner（不起真 claude）。
"""
import contextlib
import datetime as _dt
import io
import json
import shutil
import subprocess
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first

from act import actd, voice_gen
from act.lib import config, detached, voice_job
from server import inbox_writer
from server.errors import InvalidFieldError, UnknownFieldError

NOW = _dt.datetime(2026, 9, 6, 12, 0, 0, tzinfo=_dt.timezone.utc)


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _clean():
    shutil.rmtree(voice_job.JOB_DIR, ignore_errors=True)
    for p in (voice_gen.profile_path(),):
        if p.exists():
            p.unlink()


class RequestTestCase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        _clean()
        self.addCleanup(_clean)
        self.spawned = []
        patcher = mock.patch.object(detached, "spawn", lambda argv, log_name: self.spawned.append((argv, log_name)))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.log = []

    def test_request_spawns_voice_gen_with_job_flag_and_records_running(self):
        rc = voice_job.request({"action": "voice_generate"}, self.log.append)
        self.assertEqual(rc, "running")
        self.assertEqual(self.spawned, [(["act.voice_gen", "--job"], voice_job.LOG_NAME)])
        job = json.loads(voice_job.JOB_PATH.read_text(encoding="utf-8"))
        self.assertEqual(job["status"], "running")
        self.assertRegex(job["started_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        self.assertEqual((job["finished_at"], job["error"], job["message"], job["profile_path"]), (None, None, None, None))
        self.assertTrue(voice_job.JOB_DIR.is_dir())   # detached.spawn 的 run.log 目录先建好
        self.assertTrue(any("subprocess started" in m for m in self.log))

    def test_a_running_generation_blocks_a_second_one(self):
        # 原生 guard !voiceGenRunning：一份在跑就不再起第二份（两份 claude 同时改档案没有好结果）
        voice_job.request({"action": "voice_generate"}, self.log.append)
        before = voice_job.JOB_PATH.read_text(encoding="utf-8")
        rc = voice_job.request({"action": "voice_generate"}, self.log.append)
        self.assertEqual(rc, "noop")
        self.assertEqual(len(self.spawned), 1)
        self.assertEqual(voice_job.JOB_PATH.read_text(encoding="utf-8"), before)
        self.assertTrue(any("already running" in m for m in self.log))

    def test_a_lost_generation_does_not_block(self):
        # running 却超过 LOST_AFTER_S 没回执（崩在 import / 被杀）：诚实当它丢了，让新的一份跑
        voice_job.JOB_DIR.mkdir(parents=True, exist_ok=True)
        voice_job.JOB_PATH.write_text(json.dumps({"status": "running", "started_at": "2020-01-01T00:00:00Z"}), encoding="utf-8")
        rc = voice_job.request({"action": "voice_generate"}, self.log.append)
        self.assertEqual(rc, "running")
        self.assertEqual(len(self.spawned), 1)

    def test_finished_jobs_never_block(self):
        for status in ("done", "failed"):
            with self.subTest(status=status):
                self.spawned.clear()
                voice_job.JOB_DIR.mkdir(parents=True, exist_ok=True)
                voice_job.JOB_PATH.write_text(json.dumps({"status": status, "started_at": _iso(NOW)}), encoding="utf-8")
                self.assertEqual(voice_job.request({"action": "voice_generate"}, self.log.append), "running")
                self.assertEqual(len(self.spawned), 1)

    def test_launch_failure_is_recorded_honestly(self):
        with mock.patch.object(detached, "spawn", side_effect=OSError("no fork")):
            rc = voice_job.request({"action": "voice_generate"}, self.log.append)
        self.assertEqual(rc, "noop")
        job = voice_job.load()
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["error"], "launch_failed: no fork")
        self.assertIsNotNone(job["finished_at"])
        self.assertTrue(any("launch FAILED" in m for m in self.log))

    def test_malformed_decision_is_noop_without_a_record(self):
        for bad in ({"action": "radar_test_round"}, {}, "not-a-dict", None):
            with self.subTest(bad=bad):
                self.assertEqual(voice_job.request(bad, self.log.append), "noop")
        self.assertEqual(self.spawned, [])
        self.assertIsNone(voice_job.load())

    def test_actd_routes_the_inbox_action_through_the_detached_table(self):
        self.assertIn("voice_generate", actd._DETACHED_ACTIONS)
        rc = actd._DETACHED_ACTIONS["voice_generate"]({"action": "voice_generate"})
        self.assertEqual(rc, "running")
        self.assertEqual(self.spawned[0][0], ["act.voice_gen", "--job"])

    def test_actd_process_inbox_consumes_the_file(self):
        config.INBOX_DIR.mkdir(parents=True, exist_ok=True)
        path = config.INBOX_DIR / "test-voice-generate.json"
        path.write_text(json.dumps({"action": "voice_generate", "ts": "2026-09-06T10:00:00Z", "via": "web"}), encoding="utf-8")
        n = actd.process_inbox()
        self.assertEqual(n, 1)
        self.assertFalse(path.exists())  # actd reads then deletes
        self.assertEqual(self.spawned[0][0], ["act.voice_gen", "--job"])
        self.assertEqual(voice_job.load()["status"], "running")


class LedgerTestCase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        _clean()
        self.addCleanup(_clean)

    def test_load_tolerates_missing_corrupt_and_foreign_files(self):
        self.assertIsNone(voice_job.load())
        voice_job.JOB_DIR.mkdir(parents=True, exist_ok=True)
        for text in ("{not json", "[]", "null", json.dumps({"status": "weird"}), json.dumps({"no": "status"})):
            with self.subTest(text=text):
                voice_job.JOB_PATH.write_text(text, encoding="utf-8")
                self.assertIsNone(voice_job.load())

    def test_lost_and_running_predicates(self):
        fresh = {"status": "running", "started_at": _iso(NOW - _dt.timedelta(minutes=5))}
        old = {"status": "running", "started_at": _iso(NOW - _dt.timedelta(seconds=voice_job.LOST_AFTER_S + 1))}
        self.assertFalse(voice_job.is_lost(fresh, NOW))
        self.assertTrue(voice_job.is_running(fresh, NOW))
        self.assertTrue(voice_job.is_lost(old, NOW))
        self.assertFalse(voice_job.is_running(old, NOW))
        # running 却连 started_at 都读不出——没法再等它
        self.assertTrue(voice_job.is_lost({"status": "running"}, NOW))
        self.assertTrue(voice_job.is_lost({"status": "running", "started_at": "yesterday"}, NOW))
        for status in ("done", "failed"):
            self.assertFalse(voice_job.is_lost({"status": status, "started_at": "2020-01-01T00:00:00Z"}, NOW))
            self.assertFalse(voice_job.is_running({"status": status, "started_at": _iso(NOW)}, NOW))
        self.assertFalse(voice_job.is_running(None, NOW))

    def test_finish_preserves_started_at_and_writes_the_outcome(self):
        started = voice_job.mark_running()["started_at"]
        done = voice_job.finish(True, "已生成你的语气档案：/x/voice-profile.md", "/x/voice-profile.md")
        self.assertEqual(done["status"], "done")
        self.assertEqual(done["started_at"], started)
        self.assertIsNotNone(done["finished_at"])
        self.assertEqual((done["message"], done["profile_path"], done["error"]),
                         ("已生成你的语气档案：/x/voice-profile.md", "/x/voice-profile.md", None))
        self.assertEqual(voice_job.load(), done)
        failed = voice_job.finish(False, "生成失败：claude 运行出错。旧档案未改动。")
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["started_at"], started)
        self.assertEqual((failed["error"], failed["message"], failed["profile_path"]),
                         ("生成失败：claude 运行出错。旧档案未改动。", None, None))

    def test_finish_without_a_prior_record_stamps_started_at_itself(self):
        # CLI 手动带 --job（没有 actd 的 running 记录）：started_at = finished_at
        done = voice_job.finish(True, "ok", "/x")
        self.assertEqual(done["started_at"], done["finished_at"])


def _proc(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=["claude"], returncode=returncode, stdout=stdout, stderr="")


_GOOD_PROFILE = (
    "# Voice Profile\n\n## 全局铁律（所有语境）\n\n1. 短。\n\n## 桶 A：请求\n\n模式：一句 ask。\n\n"
    + "- \"line\"\n" * 40
    + "\n## 反面清单\n\n- 套话\n"
)


class VoiceGenJobFlagTestCase(unittest.TestCase):
    """``act.voice_gen --job``：generate() 之后把那一句人话写回 job.json；不带 --job 不碰台账。"""

    def setUp(self):
        config.ensure_state_dirs()
        _clean()
        self.addCleanup(_clean)

    def _run(self, argv, runner):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = voice_gen._main(argv, runner=runner)
        return rc, out.getvalue().strip()

    def test_job_flag_writes_done_with_the_stdout_line_and_profile_path(self):
        voice_job.mark_running()
        rc, line = self._run(["--job"], lambda prompt: _proc(_GOOD_PROFILE))
        self.assertEqual(rc, 0)
        job = voice_job.load()
        self.assertEqual(job["status"], "done")
        self.assertEqual(job["message"], line)
        self.assertEqual(job["profile_path"], str(voice_gen.profile_path()))
        self.assertTrue(voice_gen.profile_path().exists())

    def test_job_flag_writes_failed_with_the_human_error_line(self):
        voice_job.mark_running()
        rc, line = self._run(["--job"], lambda prompt: _proc("", returncode=1))
        self.assertEqual(rc, 1)
        job = voice_job.load()
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["error"], line)
        self.assertIsNone(job["profile_path"])

    def test_without_the_flag_the_ledger_is_untouched(self):
        rc, _line = self._run([], lambda prompt: _proc(_GOOD_PROFILE))
        self.assertEqual(rc, 0)
        self.assertIsNone(voice_job.load())


class InboxWriterTestCase(unittest.TestCase):
    """server 入站面：``{action}`` 一个键，其余零容忍；golden ``voice_generate`` 由 test_server_actions 逐字节判。"""

    def test_builds_the_bare_record(self):
        rec = inbox_writer._build_record("voice_generate", {"action": "voice_generate"}, None)
        self.assertEqual(rec, {"action": "voice_generate"})
        self.assertIn("voice_generate", inbox_writer.ALLOWED_ACTIONS)
        self.assertEqual(inbox_writer._allowed_fields("voice_generate"), {"action"})

    def test_gates(self):
        with self.assertRaises(UnknownFieldError):
            inbox_writer._reject_unknown_fields("voice_generate", {"action": "voice_generate", "id": "R-1"})
        with self.assertRaises(UnknownFieldError):
            inbox_writer._reject_unknown_fields("voice_generate", {"action": "voice_generate", "lang": "en"})
        # 不是 agent 动词（boardctl 没有这颗按钮）：actor 也是 schema 外的键
        with self.assertRaises(UnknownFieldError):
            inbox_writer._reject_unknown_fields("voice_generate", {"action": "voice_generate", "actor": "agent"})
        with self.assertRaises(InvalidFieldError):
            inbox_writer.write_action({"action": "voice_generate_now"}, home=config.HOME)


if __name__ == "__main__":
    unittest.main()
