"""§58.3 `mkdtemp:` 规则的另一半：scratch_dir 铸的目录在 cleanup 阶段真的消失（issue #436）。

传实例 → addCleanup（这条判例跑完就删）；传类 → addClassCleanup（整个类跑完再删）；
关键字与 mkdtemp 同名同义（prefix / suffix / dir）；目录落在套件的临时目录根之下。
用一个一次性的 TestCase 实例手动 doCleanups()，而不是等本条判例自己的 cleanup——
这样断言能在同一条判例里看到「删前在、删后不在」。
"""
import os
import tempfile
import unittest

from tests import TMP_HOME  # noqa: F401 - sandbox env first
from tests.scratch_testkit import scratch_dir


class _Probe(unittest.TestCase):
    """一次性的宿主：只为借它的 addCleanup / addClassCleanup 账本。"""

    def runTest(self):  # pragma: no cover - never run as a test
        pass


class InstanceCleanupTestCase(unittest.TestCase):
    def test_directory_exists_until_the_case_cleanups_run(self):
        host = _Probe()
        path = scratch_dir(host, prefix="scratch-kit-")
        self.assertIsInstance(path, str)
        self.assertTrue(os.path.isdir(path))
        self.assertTrue(os.path.basename(path).startswith("scratch-kit-"))
        with open(os.path.join(path, "nested.txt"), "w", encoding="utf-8") as fh:
            fh.write("x")
        host.doCleanups()
        self.assertFalse(os.path.exists(path))

    def test_kwargs_mirror_mkdtemp(self):
        host = _Probe()
        parent = scratch_dir(host, prefix="scratch-parent-")
        child = scratch_dir(host, prefix="p-", suffix="-s", dir=parent)
        self.assertEqual(os.path.dirname(child), parent)
        name = os.path.basename(child)
        self.assertTrue(name.startswith("p-") and name.endswith("-s"), name)
        host.doCleanups()
        self.assertFalse(os.path.exists(parent))

    def test_directories_land_under_the_suite_scratch_root(self):
        host = _Probe()
        path = scratch_dir(host, prefix="scratch-root-")
        self.assertEqual(os.path.dirname(path), tempfile.gettempdir())
        host.doCleanups()

    def test_an_already_removed_directory_does_not_break_cleanup(self):
        host = _Probe()
        path = scratch_dir(host, prefix="scratch-gone-")
        os.rmdir(path)
        host.doCleanups()  # ignore_errors: a test that deleted its own dir is fine
        self.assertFalse(os.path.exists(path))


class ClassCleanupTestCase(unittest.TestCase):
    def test_passing_the_class_defers_removal_to_class_cleanups(self):
        class Host(unittest.TestCase):
            def runTest(self):  # pragma: no cover
                pass

        path = scratch_dir(Host, prefix="scratch-class-")
        self.assertTrue(os.path.isdir(path))
        Host().doCleanups()  # instance-level cleanups do not touch it
        self.assertTrue(os.path.isdir(path))
        Host.doClassCleanups()
        self.assertFalse(os.path.exists(path))


if __name__ == "__main__":
    unittest.main()
