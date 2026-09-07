"""§68.7（2026-09-05 追记，issue #216 / D36）：server 入队的 ``shell_line`` 在**真 shell** 里跑得起来——尤其是复合 copy_cmd。

住在 tests/integration/（防腐 #7：真子进程只许住这里）。投影里 actd 写的接管命令常是复合命令
``cd '<worktree>' && claude --resume <id>``（act/lib/dashboard._resume_cmd）；退役的 .command 通道把它写成
``exec <cmd>`` → ``exec cd`` 在 zsh / bash 里都是「执行内建后退出」，claude 永远起不来、终端一闪就关。
壳（shell/Sources/TerminalLauncher.swift）把 ``PATH 兜底 + shell_line`` 整段交给 ``/bin/zsh -lc '…'``；这里用
同一形状（``<shell> -c <bootstrap + line>``，zsh 有就 zsh 否则 /bin/sh）+ 一个假 ``claude``（记下 argv / cwd /
AIASSISTANT_HOME）钉三件事：cd 生效、复合命令跑到 claude、环境变量到位。绝不起真 claude（tests/__init__ 守卫）。

时间预算：BUDGET_SECONDS（一次 shell 子进程，亚秒级）。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from tests import TMP_HOME  # noqa: F401 - sandbox env first

from server import terminal_launch

BUDGET_SECONDS = 30
_WIN = sys.platform.startswith("win")
# 壳侧 TerminalLauncher.pathBootstrap 的同一形状（$HOME / $PATH 在目标 shell 里展开）
_PATH_BOOTSTRAP = 'export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"; '


@unittest.skipIf(_WIN, "POSIX shell（Windows 腿 informational）")
class ShellLineRunsTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-shell-line-")
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        # 假 claude 装在「$HOME/.local/bin」——正是壳 PATH 兜底要补的那一节（zsh -lc 不 source .zshrc，例4b）
        self.user_home = root / "user"
        bin_dir = self.user_home / ".local" / "bin"
        bin_dir.mkdir(parents=True)
        self.record = root / "claude.json"
        fake = bin_dir / "claude"
        # 记 argv / cwd / AIASSISTANT_HOME 的假 claude（解释器 = 本测试的 python，不依赖干净 PATH 里有 python3）
        fake.write_text(
            "#!%s\nimport json, os, sys\n"
            "json.dump({'argv': sys.argv[1:], 'cwd': os.getcwd(), 'home': os.environ.get('AIASSISTANT_HOME', '')},"
            " open(%r, 'w'))\n" % (sys.executable, str(self.record)),
            encoding="utf-8")
        fake.chmod(0o755)
        self.worktree = root / "wt with space"
        self.worktree.mkdir()
        self.home = root / "assistant-home"
        self.home.mkdir()
        self.shell = "/bin/zsh" if os.path.exists("/bin/zsh") else shutil.which("sh") or "/bin/sh"

    def _spawn(self, line: str) -> "subprocess.CompletedProcess[str]":
        """壳的形状：``<shell> -c '<PATH 兜底><shell_line>'``；干净 PATH——claude 只能靠兜底那一节找到。
        zsh 加 ``-f``（不读本机 rc，判例不依赖开发机配置）。"""
        flags = "-fc" if self.shell.endswith("zsh") else "-c"
        t0 = time.monotonic()
        proc = subprocess.run([self.shell, flags, _PATH_BOOTSTRAP + line],
                              env={"HOME": str(self.user_home), "PATH": "/usr/bin:/bin"},
                              cwd=self.tmp.name, capture_output=True, text=True, timeout=20)
        self.assertLess(time.monotonic() - t0, BUDGET_SECONDS)
        return proc

    def _run(self, shell_line: str) -> dict:
        proc = self._spawn(shell_line)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(self.record.is_file(), "the fake claude never ran: %r" % (proc.stderr,))
        return json.loads(self.record.read_text(encoding="utf-8"))

    def test_compound_copy_cmd_reaches_claude_in_the_worktree(self):
        compound = "cd %s && claude --resume 6f9619ff" % terminal_launch.shlex.quote(str(self.worktree))
        line = terminal_launch.shell_line_for(compound, str(self.home), self.home)
        got = self._run(line)
        self.assertEqual(got["argv"], ["--resume", "6f9619ff"])
        self.assertEqual(Path(got["cwd"]).resolve(), self.worktree.resolve())
        self.assertEqual(got["home"], str(self.home))

    def test_plain_attach_runs_in_the_row_cwd(self):
        line = terminal_launch.shell_line_for("claude attach a7c9e2f4", str(self.worktree), self.home)
        got = self._run(line)
        self.assertEqual(got["argv"], ["attach", "a7c9e2f4"])
        self.assertEqual(Path(got["cwd"]).resolve(), self.worktree.resolve())

    def test_missing_cwd_fails_loudly_instead_of_running_elsewhere(self):
        line = terminal_launch.shell_line_for("claude attach x", str(self.worktree / "gone"), self.home)
        proc = self._spawn(line)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("folder not found", proc.stdout + proc.stderr)
        self.assertFalse(self.record.exists(), "claude must not start when the cd failed")

    def test_the_retired_exec_form_would_have_lost_the_command(self):
        """反面判例：老 .command 通道的 ``exec <复合命令>``——shell 静默退出、claude 不跑（这就是要修的故障）。"""
        compound = "cd %s && claude --resume 6f9619ff" % terminal_launch.shlex.quote(str(self.worktree))
        self._spawn("exec " + compound)   # zsh / bash：静默退出 0；dash：exec: cd: not found——都没跑到 claude
        self.assertFalse(self.record.exists())


if __name__ == "__main__":
    unittest.main()
