"""server 后台线程的在飞表与有界 join（CONTRACT §72.1 / §75.4 / §68.4；server/background_jobs.py）。

根因判例：三处请求路径把重活挪到 daemon 线程里跑，而**没有任何人能等它落地**——判例拿
`tempfile.TemporaryDirectory` 当 home，`shutil.rmtree` 走 `state/` 的同时线程还在往里写。
train PR 的 CI（Tests on ubuntu 3.9，head 0619da32）真红过一次：

    OSError: [Errno 39] Directory not empty: 'state'

本文件钉住那道缝本身的性质（不 sleep、不 `ignore_cleanup_errors`——3.9 也得过）：
- `spawn` 起的线程进在飞表，`join` 等得到它、等到了才回 `True`；
- `join` **有界**：还堵着就到点回 `False`，绝不把一整条测试挂死（§0 第 3 条：不知道就说不知道）；
- 落地的线程自动出表，在飞表不随 `spawn` 次数无限长（防腐 #4）；
- 三个后台模块都有同一道缝，且 `reset_*_for_tests()` 先 join 再清场。
「join 之后没有任何写落地」那一条拿真的临时目录钉在 tests/test_server_screenpipe_disk.py。
"""
import threading
import unittest

from tests import TMP_HOME  # noqa: F401 - sandbox env first

from server import background_jobs, ingest_run, screenpipe_disk, worktree_inventory


class ThreadsTestCase(unittest.TestCase):
    def setUp(self):
        self.gate = threading.Event()
        self.addCleanup(self.gate.set)          # 闸永远会开——测试失败也不留在飞线程
        self.jobs = background_jobs.Threads("test-jobs")
        self.addCleanup(self.jobs.join)

    def blocked(self, done: threading.Event):
        """一个堵在闸上的活：闸开了才落地（不 sleep，时序完全由测试说了算）。"""
        def work() -> None:
            self.gate.wait(10.0)
            done.set()
        return work

    def test_join_waits_for_the_thread_and_only_then_reports_true(self):
        done = threading.Event()
        self.jobs.spawn(self.blocked(done))
        self.assertFalse(self.jobs.join(0.05))      # 还堵在闸上：有界 join 如实回 False
        self.assertFalse(done.is_set())
        self.gate.set()
        self.assertTrue(self.jobs.join(10.0))
        self.assertTrue(done.is_set())              # True 回来的那一刻线程已经跑完了

    def test_join_is_bounded_and_never_hangs_the_suite(self):
        done = threading.Event()
        self.jobs.spawn(self.blocked(done))
        for _ in range(3):
            self.assertFalse(self.jobs.join(0.01))  # 闸不开，每一次都到点就走
        self.assertFalse(done.is_set())

    def test_landed_threads_leave_the_table_and_join_of_nothing_is_true(self):
        self.assertTrue(self.jobs.join(0.01))       # 空表
        self.assertEqual(self.jobs.live_count(), 0)
        self.gate.set()
        for _ in range(5):
            self.jobs.spawn(self.blocked(threading.Event()))
        self.assertTrue(self.jobs.join(10.0))
        self.assertEqual(self.jobs.live_count(), 0)

    def test_spawn_registers_before_start_so_join_never_races_the_launch(self):
        seen = []
        self.gate.set()
        thread = self.jobs.spawn(lambda: seen.append(threading.current_thread().name))
        self.assertTrue(self.jobs.join(10.0))
        self.assertFalse(thread.is_alive())
        self.assertEqual(seen, ["test-jobs"])       # 线程名 = 子系统 slug（防腐 #9）


class SeamIsWiredEverywhereTestCase(unittest.TestCase):
    """每一处 `spawn(` 站点都有同一道缝——再加一处后台线程时这条会提醒你补上。"""

    # (模块, 清场入口)；模块里那张在飞表是 `_THREADS`（判例按先例直接戳私名，同
    # tests/test_server_deps_ingest_about.py 之于 ingest_run._default_spawn）。
    WIRED = ((screenpipe_disk, "reset_cache_for_tests"),
             (worktree_inventory, "reset_cache_for_tests"),
             (ingest_run, "reset_jobs_for_tests"))

    def test_every_background_module_exposes_a_bounded_join(self):
        for mod, _reset in self.WIRED:
            with self.subTest(module=mod.__name__):
                self.assertIsInstance(mod._THREADS, background_jobs.Threads)
                self.assertTrue(mod.join_jobs_for_tests(0.5))

    def test_the_reset_seams_join_before_they_clear(self):
        """`reset_*_for_tests()` 自己先 join——判例只需把它的 addCleanup 排在临时目录之后（LIFO）。"""
        for mod, reset in self.WIRED:
            with self.subTest(module=mod.__name__):
                gate = threading.Event()
                self.addCleanup(gate.set)
                landed = threading.Event()
                mod._THREADS.spawn(lambda g=gate, la=landed: (g.wait(10.0), la.set()))
                self.assertFalse(mod.join_jobs_for_tests(0.05))   # 确认线程真的还在飞
                self.assertFalse(landed.is_set())
                gate.set()
                getattr(mod, reset)()
                self.assertTrue(landed.is_set())   # reset 回来时线程已落地：之后不会再有写漏出去
                self.assertEqual(mod._THREADS.live_count(), 0)


if __name__ == "__main__":
    unittest.main()
