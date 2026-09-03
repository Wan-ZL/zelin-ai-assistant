"""server/ 的默认子进程 runner（CONTRACT §49 / §68.4 / §68.8）——真子进程，但只起 ``sys.executable -c``。

住在 tests/integration/（防腐 #7：真 IO 只许住这里）。三种收场都钉住：正常退出（rc + stdout /
stderr 分开）、超时（rc 124 + 人话）、可执行文件不存在（rc 127）。`server/repair.py` 的 runner
只在 stdout+stderr 合并这一点不同。绝不起 claude / launchctl / 网络工具（tests/__init__ 守卫）。

时间预算：BUDGET_SECONDS（三个子进程各亚秒级；超时判例故意等 0.3 s）。
"""
import sys
import time
import unittest
from pathlib import Path

from tests import TMP_HOME  # noqa: F401 - sandbox env first

from server import repair, subproc

BUDGET_SECONDS = 30
_PY = sys.executable
_WIN = sys.platform.startswith("win")


@unittest.skipIf(_WIN, "POSIX env / timeout semantics（Windows 腿 informational）")
class SubprocDefaultRunnerTestCase(unittest.TestCase):
    def test_success_splits_stdout_and_stderr(self):
        t0 = time.monotonic()
        rc, out, err = subproc.default_runner(
            [_PY, "-c", "import sys; print('out'); print('err', file=sys.stderr)"],
            {"PATH": "/usr/bin:/bin"}, Path.cwd(), 20)
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "out")
        self.assertEqual(err.strip(), "err")
        self.assertLess(time.monotonic() - t0, BUDGET_SECONDS)

    def test_timeout_is_rc_124_with_a_sentence(self):
        rc, out, err = subproc.default_runner(
            [_PY, "-c", "import time; time.sleep(5)"], {"PATH": "/usr/bin:/bin"}, Path.cwd(), 1)
        self.assertEqual(rc, 124)
        self.assertEqual(out, "")
        self.assertIn("timed out", err)

    def test_missing_executable_is_rc_127(self):
        rc, out, err = subproc.default_runner(
            ["/nonexistent/zai-python", "-c", "pass"], {}, Path.cwd(), 5)
        self.assertEqual(rc, 127)
        self.assertEqual(out, "")
        self.assertTrue(err)

    def test_run_module_uses_sys_executable_and_repo_cwd(self):
        # -m 一个 stdlib 模块：证明 argv 组装 + cwd（repo 根）+ env 注入，不碰 act.* 入口
        rc, out, _err = subproc.run_module(
            Path(TMP_HOME), "json.tool", ["--help"], timeout_s=20)
        self.assertEqual(rc, 0)
        self.assertIn("json", out.lower())


@unittest.skipIf(_WIN, "POSIX subprocess semantics")
class RepairDefaultRunnerTestCase(unittest.TestCase):
    def test_merges_streams_and_returns_rc(self):
        rc, out = repair._default_runner(
            [_PY, "-c", "import sys; print('a'); print('b', file=sys.stderr); sys.exit(3)"])
        self.assertEqual(rc, 3)
        self.assertIn("a", out)
        self.assertIn("b", out)

    def test_missing_executable_is_rc_127(self):
        rc, out = repair._default_runner(["/nonexistent/launchctl", "print"])
        self.assertEqual(rc, 127)
        self.assertTrue(out)


if __name__ == "__main__":
    unittest.main()
