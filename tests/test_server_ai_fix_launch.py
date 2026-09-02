"""POST /api/ai-fix —— web 看板「让 AI 修」（CONTRACT §49 路由表 / §54 parity）。

原生 AIFix.launch = ``python3 -m act.ai_fix --open --context-file <f>``；本面
是同一条命令的 server 落点。钉住：字段白名单（UNKNOWN_FIELD 零容忍）、id /
lang 校验、非 darwin 501、投影查无此卡 404、上下文只由 server 从投影行推导
（last_error / dispatch_error，客户端文本进不了 prompt）、argv / cwd / env 形状、
临时上下文文件用完即删、退出码 2（config 关闭）→ 501 整句转出、其它非零 →
500 带输出尾巴。子进程一律走注入缝——测试绝不真起 act.ai_fix / claude。
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first

from server import ai_fix_launch
from server.errors import ApiError
from tests.test_server_common import (assert_envelope, post_json, rewrite_board,
                                      start_server)

BOARD = {
    "generated_at": "2026-09-01T00:00:00Z",
    "counts": {},
    "needs_approval": [],
    "running": [
        {"id": "R-501", "name": "修 flaky e2e", "state": "working",
         "last_error": "Traceback: boom"},
        {"id": "R-502", "name": "排队卡", "state": "queued",
         "dispatch_error": "spawn failed: fd limit"},
    ],
    "needs_input": [],
    "review": [],
    "completed": [{"id": "R-503", "name": "已验收", "state": "delivered"}],
    "debt": [],
    "trash": [],
}


def _fake_runner(rc: int = 0, out: str = "/tmp/zelin-ai-fix-1.command\n"):
    """记录一次调用的 argv/env/cwd 与上下文文件内容（文件在 launch 返回前会被删）。"""
    calls: dict = {}

    def run(argv, env, cwd):
        calls["argv"] = list(argv)
        calls["env"] = dict(env)
        calls["cwd"] = cwd
        calls["context"] = Path(argv[-1]).read_text(encoding="utf-8")
        return rc, out

    run.calls = calls  # type: ignore[attr-defined]
    return run


class _Home(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="zai-aifix-"))
        (self.home / "state").mkdir()
        rewrite_board(self.home, BOARD)


class LaunchUnitTestCase(_Home):
    def test_context_prefers_last_error_then_dispatch_error(self):
        self.assertIn("error: Traceback: boom", ai_fix_launch.context_for(self.home, "R-501"))
        self.assertIn("error: spawn failed: fd limit", ai_fix_launch.context_for(self.home, "R-502"))
        plain = ai_fix_launch.context_for(self.home, "R-503")
        self.assertIn("R-503", plain)
        self.assertIn("completed lane", plain)
        self.assertNotIn("error:", plain)

    def test_context_unknown_card_404(self):
        with self.assertRaises(ApiError) as cm:
            ai_fix_launch.context_for(self.home, "R-999")
        self.assertEqual(cm.exception.status, 404)

    def test_launch_argv_env_cwd_and_context_file_lifecycle(self):
        run = _fake_runner()
        with mock.patch.object(ai_fix_launch.sys, "platform", "darwin"):
            result = ai_fix_launch.launch(self.home, {"card_id": "R-501", "lang": "zh"}, runner=run)
        self.assertEqual(result, {"ok": True, "command_file": "/tmp/zelin-ai-fix-1.command"})
        argv = run.calls["argv"]
        self.assertEqual(argv[:5], [sys.executable, "-m", "act.ai_fix", "--open", "--context-file"])
        self.assertEqual(run.calls["cwd"], self.home)
        self.assertEqual(run.calls["env"]["AIASSISTANT_HOME"], str(self.home))
        self.assertEqual(run.calls["env"]["AIASSISTANT_UI_LANG"], "zh")
        # 上下文 = server 从投影行推导；用完即删
        self.assertIn("R-501", run.calls["context"])
        self.assertIn("Traceback: boom", run.calls["context"])
        self.assertFalse(Path(argv[-1]).exists())

    def test_launch_without_lang_leaves_ui_lang_alone(self):
        run = _fake_runner()
        with mock.patch.object(ai_fix_launch.sys, "platform", "darwin"), \
                mock.patch.dict(ai_fix_launch.os.environ, {}, clear=False):
            ai_fix_launch.os.environ.pop("AIASSISTANT_UI_LANG", None)
            ai_fix_launch.launch(self.home, {"card_id": "R-502"}, runner=run)
        self.assertNotIn("AIASSISTANT_UI_LANG", run.calls["env"])

    def test_disabled_by_config_is_501_with_the_python_sentence(self):
        run = _fake_runner(rc=2, out="「让 AI 修」已在 config.yaml 里关闭\n")
        with mock.patch.object(ai_fix_launch.sys, "platform", "darwin"):
            with self.assertRaises(ApiError) as cm:
                ai_fix_launch.launch(self.home, {"card_id": "R-501"}, runner=run)
        self.assertEqual(cm.exception.status, 501)
        self.assertIn("config.yaml", cm.exception.message)

    def test_other_failure_is_500_with_output_tail(self):
        run = _fake_runner(rc=1, out="x" * 500 + "TAIL")
        with mock.patch.object(ai_fix_launch.sys, "platform", "darwin"):
            with self.assertRaises(ApiError) as cm:
                ai_fix_launch.launch(self.home, {"card_id": "R-501"}, runner=run)
        self.assertEqual(cm.exception.status, 500)
        self.assertTrue(cm.exception.message.endswith("TAIL"))
        self.assertLessEqual(len(cm.exception.message), 300)
        self.assertEqual(cm.exception.details, {"rc": 1})

    def test_non_darwin_is_501_before_any_subprocess(self):
        run = _fake_runner()
        with mock.patch.object(ai_fix_launch.sys, "platform", "linux"):
            with self.assertRaises(ApiError) as cm:
                ai_fix_launch.launch(self.home, {"card_id": "R-501"}, runner=run)
        self.assertEqual(cm.exception.status, 501)
        self.assertEqual(run.calls, {})


class DefaultRunnerTestCase(unittest.TestCase):
    def test_default_runner_concats_stdout_and_stderr(self):
        proc = subprocess.CompletedProcess(["x"], 0, stdout="/tmp/a.command\n", stderr="warn\n")
        with mock.patch.object(ai_fix_launch.subprocess, "run", return_value=proc) as run:
            rc, out = ai_fix_launch._default_runner(["x"], {"A": "1"}, Path("/tmp"))
        self.assertEqual((rc, out), (0, "/tmp/a.command\nwarn\n"))
        # runner 传的是 str(Path)——Windows 上是 "\\tmp"，别钉 POSIX 字面量
        self.assertEqual(run.call_args.kwargs["cwd"], str(Path("/tmp")))
        self.assertEqual(run.call_args.kwargs["env"], {"A": "1"})

    def test_default_runner_maps_timeout_and_oserror(self):
        with mock.patch.object(ai_fix_launch.subprocess, "run",
                               side_effect=subprocess.TimeoutExpired("x", 1)):
            rc, _out = ai_fix_launch._default_runner(["x"], {}, Path("/tmp"))
        self.assertEqual(rc, 124)
        with mock.patch.object(ai_fix_launch.subprocess, "run", side_effect=OSError("no python")):
            rc, out = ai_fix_launch._default_runner(["x"], {}, Path("/tmp"))
        self.assertEqual((rc, out), (127, "no python"))


class RouteTestCase(_Home):
    def setUp(self):
        super().setUp()
        _, self.port = start_server(self, self.home)

    def test_success_through_http(self):
        run = _fake_runner()
        with mock.patch.object(ai_fix_launch.sys, "platform", "darwin"), \
                mock.patch.object(ai_fix_launch, "_default_runner", run):
            status, obj = post_json(self.port, "/api/ai-fix", {"card_id": "R-501", "lang": "en"})
        self.assertEqual(status, 200)
        self.assertEqual(obj, {"ok": True, "command_file": "/tmp/zelin-ai-fix-1.command"})
        self.assertEqual(run.calls["env"]["AIASSISTANT_UI_LANG"], "en")

    def test_unknown_field_rejected(self):
        status, obj = post_json(self.port, "/api/ai-fix",
                                {"card_id": "R-501", "context": "rm -rf /"})
        self.assertEqual(status, 400)
        assert_envelope(self, obj, "UNKNOWN_FIELD")

    def test_bad_card_id_and_bad_lang_rejected(self):
        for payload in ({"card_id": "../etc"}, {"card_id": 7}, {}, {"card_id": "R-501", "lang": "fr"}):
            with self.subTest(payload=payload):
                status, obj = post_json(self.port, "/api/ai-fix", payload)
                self.assertEqual(status, 400)
                assert_envelope(self, obj, "INVALID_FIELD")

    def test_unknown_card_404(self):
        with mock.patch.object(ai_fix_launch.sys, "platform", "darwin"):
            status, obj = post_json(self.port, "/api/ai-fix", {"card_id": "R-999"})
        self.assertEqual(status, 404)
        assert_envelope(self, obj, "NOT_FOUND")

    def test_non_darwin_501(self):
        with mock.patch.object(ai_fix_launch.sys, "platform", "linux"):
            status, obj = post_json(self.port, "/api/ai-fix", {"card_id": "R-501"})
        self.assertEqual(status, 501)
        assert_envelope(self, obj, "NOT_IMPLEMENTED")

    def test_disabled_501_through_http(self):
        run = _fake_runner(rc=2, out="disabled by config\n")
        with mock.patch.object(ai_fix_launch.sys, "platform", "darwin"), \
                mock.patch.object(ai_fix_launch, "_default_runner", run):
            status, obj = post_json(self.port, "/api/ai-fix", {"card_id": "R-501"})
        self.assertEqual(status, 501)
        assert_envelope(self, obj, "NOT_IMPLEMENTED")
        self.assertEqual(obj["error"]["message"], "disabled by config")


if __name__ == "__main__":
    unittest.main()
