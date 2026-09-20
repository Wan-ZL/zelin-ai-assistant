"""§58.3 / issue #436 的终局判例：一次 `import tests` 的进程退出后，它的 $TMPDIR 零残留。

真 python 子进程（住 integration/，防腐 #7），TMPDIR/TEMP/TMP 指向一个只有这个子进程
会写的空目录：子进程 import tests（铸沙箱 HOME + 重定向 + 登记 atexit），再故意用三种
tempfile 构造器各漏一个不 cleanup 的草稿，退出。断言：子进程报的 TMP_HOME 不存在了，
那个空目录仍然是空的——这就是 issue 里「suite leaves $TMPDIR entry count unchanged」
的机器版。时间预算：一个亚秒级子进程。
"""
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tests import TMP_HOME  # noqa: F401 - sandbox env first

REPO = Path(__file__).resolve().parents[2]

_CHILD = (
    "import os, tempfile, tests\n"
    "tempfile.mkdtemp(prefix='leak-dir-')\n"
    "tempfile.NamedTemporaryFile(prefix='leak-file-', delete=False).close()\n"
    "tempfile.TemporaryDirectory(prefix='leak-td-')  # dropped without cleanup\n"
    "print(tests.TMP_HOME)\n"
    "print(tempfile.gettempdir())\n"
)


class ScratchRootRemovedAtExitTestCase(unittest.TestCase):
    def test_tmpdir_is_empty_after_the_process_exits(self):
        with TemporaryDirectory(prefix="fresh-tmpdir-") as fresh:
            env = dict(os.environ, TMPDIR=fresh, TEMP=fresh, TMP=fresh, PYTHONDONTWRITEBYTECODE="1")
            proc = subprocess.run([sys.executable, "-c", _CHILD], cwd=str(REPO), env=env,
                                  capture_output=True, text=True, timeout=60)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            home, scratch = proc.stdout.strip().splitlines()[-2:]
            self.assertEqual(os.path.dirname(home), fresh, "sandbox HOME was minted in the child's TMPDIR")
            self.assertEqual(scratch, os.path.join(home, "tmp"), "child redirected its temp root into the sandbox")
            self.assertFalse(os.path.exists(home), "atexit removed the whole sandbox")
            self.assertEqual(os.listdir(fresh), [], "nothing leaked into the child's $TMPDIR")


if __name__ == "__main__":
    unittest.main()
