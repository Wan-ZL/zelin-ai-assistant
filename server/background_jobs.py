"""server/background_jobs.py — server 后台线程的唯一生成缝 + 有界 join（CONTRACT §72.1 / §75.4 / §68.4）。

三处请求路径用同一个形制把重活挪出 handler 线程：录制磁盘快照（§72.1 `server/screenpipe_disk.py`）、
worktree 清点（§75.4 `server/worktree_inventory.py`）、手动 ingest / export（§68.4 `server/ingest_run.py`）。
生产里它们是 daemon 线程，这没问题——进程退出不等它们。**判例里却是一个真的竞态**：每个 TestCase 拿
`tempfile.TemporaryDirectory` 当 home，`shutil.rmtree` 走 `state/` 的同时后台线程还在往里写样本文件
（train PR CI 2026-09-15 · ubuntu 3.9 真红过一次：``OSError: [Errno 39] Directory not empty: 'state'``），
更坏的一种是 `mock.patch` 已经 stop、线程这才去读被恢复回来的真 runner / 真 `~/.screenpipe`。

根因是「起了线程却没有任何人能等它」。所以线程不再由各模块自己 `threading.Thread(...).start()`：一律经
:class:`Threads` 登记进在飞表，判例在拆临时目录**之前**等一次（三个模块的 ``reset_*_for_tests()`` /
``join_jobs_for_tests()`` 就是这道缝；``addCleanup`` 是 LIFO，所以它必须**晚于**临时目录那一下登记）。

`join` 永远有界（默认 :data:`JOIN_TIMEOUT_S`）：等不到只回 ``False``，绝不把一整条测试挂死——
不知道就说不知道，不假装干净（§0 第 3 条）。生产路径上本模块只多一次加锁 append + 一次死线程清扫，
对外行为一个字不变。
"""
from __future__ import annotations

import threading
import time
from typing import Callable, List

JOIN_TIMEOUT_S = 30.0


class Threads:
    """一个子系统的在飞后台线程表：``spawn`` 起并登记，``join`` 有界地等它们落地。"""

    def __init__(self, name: str) -> None:
        self.name = name
        self._lock = threading.Lock()
        self._live: List[threading.Thread] = []

    def spawn(self, fn: Callable[[], None]) -> threading.Thread:
        """起一个 daemon 线程跑 ``fn``；登记与 ``start()`` 在同一把锁里——先 append 后 start 的话，
        中间那一瞬 `join` 会撞上「还没 start 的线程」（`Thread.join` 对它抛 RuntimeError）。"""
        thread = threading.Thread(target=fn, name=self.name, daemon=True)
        with self._lock:
            self._live[:] = [t for t in self._live if t.is_alive()]
            self._live.append(thread)
            thread.start()
        return thread

    def live_count(self) -> int:
        """此刻还活着的线程数（顺手把落地的清出表——在飞表不许只增不减，防腐 #4）。"""
        with self._lock:
            self._live[:] = [t for t in self._live if t.is_alive()]
            return len(self._live)

    def join(self, timeout: float = JOIN_TIMEOUT_S) -> bool:
        """等在飞的线程落地；``True`` = 全落地（此刻再删 home 撞不上任何写），``False`` = 到点还有活的。"""
        deadline = time.monotonic() + max(0.0, timeout)
        with self._lock:
            pending = list(self._live)
        for thread in pending:
            thread.join(max(0.0, deadline - time.monotonic()))
        with self._lock:
            self._live[:] = [t for t in self._live if t.is_alive()]
            return not self._live
