"""§58.3 / issue #436：套件 bootstrap 把整次 run 的临时目录根指进沙箱 HOME。

tests/__init__.py 在铸完 TMP_HOME 之后把 `tempfile.tempdir` 与 `TMPDIR`/`TEMP`/`TMP`
一起指到 `<TMP_HOME>/tmp`——于是判例进程里 mkdtemp / TemporaryDirectory /
NamedTemporaryFile 铸的一切、以及子进程按 env 铸的一切，都落在同一棵退出时整树删的
树下。这里钉的是「指向」；「退出时真的删了」是真子进程的事，住
tests/integration/test_suite_scratch_root_removed_at_exit.py。
"""
import os
import tempfile
import unittest

from tests import TMP_HOME
from tests.scratch_testkit import scratch_dir


class ScratchRootTestCase(unittest.TestCase):
    def test_gettempdir_is_the_tmp_dir_inside_the_sandbox_home(self):
        self.assertEqual(tempfile.gettempdir(), os.path.join(TMP_HOME, "tmp"))
        self.assertTrue(os.path.isdir(tempfile.gettempdir()))

    def test_child_process_env_points_at_the_same_root(self):
        for key in ("TMPDIR", "TEMP", "TMP"):
            self.assertEqual(os.environ.get(key), tempfile.gettempdir(), key)

    def test_every_tempfile_constructor_lands_under_the_root(self):
        root = tempfile.gettempdir()
        made = scratch_dir(self, prefix="redirect-probe-")  # the sanctioned mkdtemp
        self.assertEqual(os.path.dirname(made), root)
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(os.path.dirname(d), root)
        with tempfile.NamedTemporaryFile() as fh:
            self.assertEqual(os.path.dirname(fh.name), root)


if __name__ == "__main__":
    unittest.main()
