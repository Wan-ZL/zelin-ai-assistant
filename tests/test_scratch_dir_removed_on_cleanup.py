"""§58.3 `mkdtemp:` 规则的另一半：scratch_dir 铸的目录在 cleanup 阶段真的消失（issue #436）。

传实例 → addCleanup（这条判例跑完就删）；传类 → addClassCleanup（整个类跑完再删）；
关键字与 mkdtemp 同名同义（prefix / suffix / dir）；目录落在套件的临时目录根之下。
宿主判例在函数体里现造（module-level 的 TestCase 子类会被 loader 当真判例收集），
并用 ``TestCase.run(result)`` 跑它——cleanup 阶段抛的异常会记进 ``result.errors``，
所以「已被删掉的目录不炸 cleanup」这一条对去掉 ``ignore_errors`` 的变异体是敏感的。
"""
import os
import tempfile
import unittest

from tests import TMP_HOME  # noqa: F401 - sandbox env first
from tests.scratch_testkit import scratch_dir


def _run_case(body):
    """把 ``body(case)`` 当一条判例跑完（含 cleanup 阶段），回 (result, body 的返回值)。"""
    box = {}

    class Host(unittest.TestCase):
        def runTest(self):
            box["value"] = body(self)

    result = unittest.TestResult()
    Host().run(result)
    return result, box.get("value")


class InstanceCleanupTestCase(unittest.TestCase):
    def test_directory_exists_until_the_case_cleanups_run(self):
        def body(case):
            path = scratch_dir(case, prefix="scratch-kit-")
            case.assertIsInstance(path, str)
            case.assertTrue(os.path.isdir(path))
            case.assertTrue(os.path.basename(path).startswith("scratch-kit-"))
            with open(os.path.join(path, "nested.txt"), "w", encoding="utf-8") as fh:
                fh.write("x")
            return path

        result, path = _run_case(body)
        self.assertTrue(result.wasSuccessful(), result.errors + result.failures)
        self.assertFalse(os.path.exists(path))

    def test_kwargs_mirror_mkdtemp(self):
        def body(case):
            parent = scratch_dir(case, prefix="scratch-parent-")
            child = scratch_dir(case, prefix="p-", suffix="-s", dir=parent)
            case.assertEqual(os.path.dirname(child), parent)
            name = os.path.basename(child)
            case.assertTrue(name.startswith("p-") and name.endswith("-s"), name)
            return parent

        result, parent = _run_case(body)
        self.assertTrue(result.wasSuccessful(), result.errors + result.failures)
        self.assertFalse(os.path.exists(parent))

    def test_directories_land_under_the_suite_scratch_root(self):
        result, path = _run_case(lambda case: scratch_dir(case, prefix="scratch-root-"))
        self.assertTrue(result.wasSuccessful(), result.errors + result.failures)
        self.assertEqual(os.path.dirname(path), tempfile.gettempdir())

    def test_an_already_removed_directory_does_not_break_cleanup(self):
        def body(case):
            path = scratch_dir(case, prefix="scratch-gone-")
            os.rmdir(path)  # a test that deletes its own dir must not error in cleanup
            return path

        result, path = _run_case(body)
        self.assertEqual(result.errors, [])
        self.assertTrue(result.wasSuccessful())
        self.assertFalse(os.path.exists(path))


class ClassCleanupTestCase(unittest.TestCase):
    def test_passing_the_class_defers_removal_to_class_cleanups(self):
        class Host(unittest.TestCase):
            def runTest(self):
                pass

        path = scratch_dir(Host, prefix="scratch-class-")
        self.assertTrue(os.path.isdir(path))
        Host().run(unittest.TestResult())  # a whole instance run does not touch it
        self.assertTrue(os.path.isdir(path))
        Host.doClassCleanups()
        self.assertFalse(os.path.exists(path))


if __name__ == "__main__":
    unittest.main()
