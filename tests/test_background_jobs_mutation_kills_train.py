"""server/background_jobs.py 的变异残存补杀（CONTRACT §72.1 / §75.4 / §68.4）。

夜间变异跑（§57）在这道后台线程缝上留了一个活口：`spawn` 的 ``daemon=True`` 翻成
``False`` 时整套判例照绿——在飞表、有界 join、自动出表全都不受它影响，而它管的是
**生产语义**：三处请求路径起的都是「进程退出不等它」的后台活（磁盘快照、worktree
清点、手动 ingest），非 daemon 线程会把 server 的退出吊在一次 `os.walk` 上。

本文件只钉那一条性质，写法与 tests/test_server_background_jobs.py 互不重叠。
"""
import threading
import unittest

from tests import TMP_HOME  # noqa: F401 - sandbox env first

from server import background_jobs


class DaemonThreadsTestCase(unittest.TestCase):
    """§72.1：后台活是 daemon —— 进程退出绝不等它们落地。"""

    def setUp(self):
        self.gate = threading.Event()
        self.addCleanup(self.gate.set)          # 闸永远会开——失败也不留在飞线程
        self.jobs = background_jobs.Threads("daemon-probe")
        self.addCleanup(self.jobs.join)

    def test_spawned_threads_are_daemons_so_process_exit_never_waits_on_them(self):
        thread = self.jobs.spawn(self.gate.wait)
        self.assertTrue(thread.daemon,
                        "后台活必须是 daemon：非 daemon 线程会把 server 的退出"
                        "吊在一次 os.walk / sqlite 问询上（§72.1）")
        self.assertEqual(thread.name, "daemon-probe")
        self.gate.set()
        self.assertTrue(self.jobs.join(5.0))
