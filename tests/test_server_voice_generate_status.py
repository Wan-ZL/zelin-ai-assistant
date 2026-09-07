"""GET /api/voice/generate-status —— 「从我的消息生成/更新档案」的回执（CONTRACT §68.1 追记 / §49 / D47）。

钉住：server 只读 ``state/voice_gen/job.json``（写者是 actd 与 ``act.voice_gen --job``，act/lib/voice_job）；
缺失 / 坏文件 / 词表外 status → ``{"job": null}`` 永不 500；六键逐字消毒成 str|null；``lost`` = running 却超过
LOST_AFTER_S 没回执；server/paths 与 server/voice_profile 的常量镜像 act/lib/voice_job（server 绝不 import act）。
"""
import datetime as _dt
import json
import tempfile
import unittest
from pathlib import Path

from tests import TMP_HOME  # noqa: F401 - sandbox env first
from tests.test_server_common import get_json, start_server, write_text

from act.lib import config, voice_job
from server import paths, voice_profile

NOW = _dt.datetime(2026, 9, 6, 12, 0, 0, tzinfo=_dt.timezone.utc)


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


class GenerateStatusTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-voice-gen-")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        (self.home / "state").mkdir(parents=True)
        _httpd, self.port = start_server(self, self.home)

    def _write_job(self, obj) -> None:
        write_text(paths.voice_gen_job_path(self.home), obj if isinstance(obj, str) else json.dumps(obj))

    def test_absent_ledger_is_job_null_not_404(self):
        status, obj = get_json(self.port, "/api/voice/generate-status")
        self.assertEqual((status, obj), (200, {"job": None}))

    def test_running_done_failed_are_echoed_with_lost_false(self):
        started = _iso(_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(minutes=1))
        self._write_job({"status": "running", "started_at": started, "finished_at": None, "error": None,
                         "message": None, "profile_path": None})
        _s, obj = get_json(self.port, "/api/voice/generate-status")
        self.assertEqual(obj["job"], {"status": "running", "started_at": started, "finished_at": None, "error": None,
                                      "message": None, "profile_path": None, "lost": False})
        self._write_job({"status": "done", "started_at": started, "finished_at": started,
                         "message": "已生成你的语气档案：/h/state/voice-profile.md", "profile_path": "/h/state/voice-profile.md"})
        _s, obj = get_json(self.port, "/api/voice/generate-status")
        self.assertEqual((obj["job"]["status"], obj["job"]["message"], obj["job"]["profile_path"], obj["job"]["lost"]),
                         ("done", "已生成你的语气档案：/h/state/voice-profile.md", "/h/state/voice-profile.md", False))
        self.assertIsNone(obj["job"]["error"])
        self._write_job({"status": "failed", "started_at": started, "finished_at": started, "error": "生成失败：claude 运行出错"})
        _s, obj = get_json(self.port, "/api/voice/generate-status")
        self.assertEqual((obj["job"]["status"], obj["job"]["error"], obj["job"]["lost"]), ("failed", "生成失败：claude 运行出错", False))

    def test_running_too_long_is_lost(self):
        self._write_job({"status": "running", "started_at": "2020-01-01T00:00:00Z"})
        _s, obj = get_json(self.port, "/api/voice/generate-status")
        self.assertEqual((obj["job"]["status"], obj["job"]["lost"]), ("running", True))
        # 边界：刚好在预算内 → 没丢；预算外一秒 → 丢
        inside = {"status": "running", "started_at": _iso(NOW - _dt.timedelta(seconds=voice_profile.LOST_AFTER_S))}
        outside = {"status": "running", "started_at": _iso(NOW - _dt.timedelta(seconds=voice_profile.LOST_AFTER_S + 1))}
        self.assertFalse(voice_profile.is_lost(inside, NOW))
        self.assertTrue(voice_profile.is_lost(outside, NOW))
        # running 却连 started_at 都读不出 → 丢（没法再等它）
        self.assertTrue(voice_profile.is_lost({"status": "running", "started_at": 12}, NOW))

    def test_corrupt_or_foreign_ledger_is_job_null_never_500(self):
        for text in ("{not json", "[]", "null", "42", json.dumps({"status": "weird"}), json.dumps({"no": "status"})):
            with self.subTest(text=text):
                self._write_job(text)
                status, obj = get_json(self.port, "/api/voice/generate-status")
                self.assertEqual((status, obj), (200, {"job": None}))

    def test_fields_are_sanitized_to_str_or_null(self):
        # LLM / 子进程写的值不可信：非字串一律 null（宪法第 11 条口径），status 之外多出来的键不透传
        self._write_job({"status": "done", "started_at": 1, "finished_at": ["x"], "error": {"a": 1}, "message": "",
                         "profile_path": True, "pid": 4242})
        _s, obj = get_json(self.port, "/api/voice/generate-status")
        self.assertEqual(obj["job"], {"status": "done", "started_at": None, "finished_at": None, "error": None,
                                      "message": None, "profile_path": None, "lost": False})

    def test_is_token_light_get(self):
        # 同源纪律与 /api/voice 一致：GET 不要 X-Zai-Token（get_json 不带 token）
        status, _obj = get_json(self.port, "/api/voice/generate-status")
        self.assertEqual(status, 200)


class MirrorPinTestCase(unittest.TestCase):
    """server 手抄 act/lib/voice_job 的路径与词表（server 绝不 import act）——测试侧可以，这里就是那道 pin。"""

    def test_job_path_mirrors_the_act_writer(self):
        home = Path("/tmp/zai-voice-pin")
        self.assertEqual(paths.voice_gen_job_path(home), home / voice_job.JOB_PATH.relative_to(config.HOME))

    def test_vocabulary_and_budget_mirror_the_act_writer(self):
        self.assertEqual(tuple(voice_profile.JOB_STATUSES), tuple(voice_job.STATUSES))
        self.assertEqual(voice_profile.LOST_AFTER_S, voice_job.LOST_AFTER_S)

    def test_server_reads_what_actd_and_the_child_write(self):
        # 端到端不起进程：act 侧真的写、server 侧真的读（同一个沙箱 HOME）
        try:
            voice_job.mark_running()
            self.assertEqual(voice_profile.generate_status(config.HOME)["job"]["status"], "running")
            voice_job.finish(True, "ok line", "/p")
            job = voice_profile.generate_status(config.HOME)["job"]
            self.assertEqual((job["status"], job["message"], job["profile_path"], job["lost"]), ("done", "ok line", "/p", False))
        finally:
            if voice_job.JOB_PATH.exists():
                voice_job.JOB_PATH.unlink()


if __name__ == "__main__":
    unittest.main()
