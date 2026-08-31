"""tests/__init__.py 的 fail-loud subprocess 守卫自身的判例（测试卫生 rule 7）。

守卫是执法机制，没有判例它就会在某次"这条测试报错真烦"里被悄悄删掉。这里钉
三件事：它确实装上了、它拦得住真花钱/真出网的形状、它不误伤合法的真
subprocess（CLI-under-test 的 python3 -m …、fixture 的 bash -c）。

拦截判据的边界（有意为之，见 tests/__init__.py 头注）：拦"带 prompt 起
agent"与出网工具，不拦本地能力探针（--version/--help/agents/mcp list）。
"""
import contextlib
import io
import subprocess
import sys
import unittest

import tests
from tests import TMP_HOME  # noqa: F401 - sandbox env 先于任何 act.* import


class GuardInstalledTestCase(unittest.TestCase):
    def test_popen_is_wrapped_for_every_entry_point(self):
        """run/check_output/check_call 都经 subprocess.Popen 这个名字建进程。"""
        self.assertIs(subprocess.Popen, tests._GuardedPopen)

    def test_ban_is_not_swallowable_by_best_effort_handlers(self):
        """守卫异常继承 BaseException：生产代码遍地 `except Exception`
        兜底（宪法第 11 条），可被吞掉的守卫等于没建。"""
        self.assertTrue(issubclass(tests.RealSubprocessBanned, BaseException))
        self.assertFalse(issubclass(tests.RealSubprocessBanned, Exception))


class ModelCallDetectionTestCase(unittest.TestCase):
    """"带 prompt 起 agent" = 真花钱真出网，一律拦。"""

    def test_print_mode_and_resume_are_banned(self):
        for argv in (["claude", "-p", "hi", "--output-format", "text"],
                     ["/opt/homebrew/bin/claude", "--print", "hi"],
                     ["claude", "--resume", "sid", "更多指令"]):
            self.assertTrue(tests._model_call(argv), argv)

    def test_background_dispatch_shape_is_banned(self):
        """executor._bg_base_cmd 的形：claude --bg [flags] <prompt>。"""
        self.assertTrue(tests._model_call(
            ["claude", "--bg", "--dangerously-skip-permissions",
             "--name", "R-001", "做这件事"]))

    def test_local_capability_probes_are_allowed(self):
        """doctor/ask 有意探真装的 CLI——零成本零网络，不在拦截面内。"""
        for argv in (["claude", "--version"], ["claude", "--help"],
                     ["claude", "--bg"], ["claude", "agents", "--json", "--all"],
                     ["claude", "mcp", "list"], ["claude", "stop", "abc123"]):
            self.assertFalse(tests._model_call(argv), argv)

    def test_other_programs_are_not_model_calls(self):
        self.assertFalse(tests._model_call([sys.executable, "-p", "x"]))


class NetworkDetectionTestCase(unittest.TestCase):
    def test_direct_and_shell_wrapped_egress_are_both_caught(self):
        self.assertEqual(tests._network_hits(["curl", "https://example.com"]),
                         ["curl"])
        self.assertEqual(
            tests._network_hits(["bash", "-c", "wget https://example.com"]),
            ["wget"])

    def test_fixture_paths_inside_a_script_body_are_not_egress(self):
        """install.sh 渲染用的 `bash -c` 脚本体里有 CLAUDE_LOGIN_BIN=… 这类
        赋值——路径名不是执行，误报会逼着后人删守卫。"""
        body = 'CLAUDE_LOGIN_BIN=/fake/claude-home/claude\nrender_plist "$OUT"\n'
        self.assertEqual(tests._network_hits(["bash", "-c", body]), [])


class EndToEndTestCase(unittest.TestCase):
    def test_banned_spawn_raises_before_exec(self):
        # 守卫会先往 stderr 落一行痕迹（防被 except 吞掉）——这里是有意触发，
        # 吃掉那行，绿跑的输出保持干净。
        with contextlib.redirect_stderr(io.StringIO()) as trace:
            with self.assertRaises(tests.RealSubprocessBanned):
                subprocess.run(["claude", "-p", "真的会花钱"], capture_output=True)
        self.assertIn("SUBPROCESS GUARD", trace.getvalue())

    def test_legitimate_real_subprocess_still_runs(self):
        """CLI-under-test 与 fixture 脚本照跑——守卫不是"禁止 subprocess"。"""
        proc = subprocess.run([sys.executable, "-c", "print('ok')"],
                              capture_output=True, text=True)
        self.assertEqual(proc.stdout.strip(), "ok")


if __name__ == "__main__":
    unittest.main()
