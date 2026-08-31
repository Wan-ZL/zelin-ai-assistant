"""dashboard.json 变更监视 → SSE ``board.updated``（BUILD-CONTRACT §2.1）。

300ms mtime 轮询（不用 FSEvents/inotify：stdlib 白名单 + actd 是原子写
tmp+rename，mtime_ns+size 足够可靠）。事件 data 只带 {generated_at}——
客户端收到后自行 refetch /api/board，server 不推全量。
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Optional

from server import paths
from server.sse import EventHub

POLL_INTERVAL = 0.3


def _stat_key(p: Path) -> Optional[tuple]:
    try:
        st = p.stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def _generated_at(p: Path) -> Optional[str]:
    """读投影里的 generated_at；写入是原子 rename，读到半截文件的概率≈0，
    但解析失败也绝不抛穿（宪法第 11 条精神）——降级为 None。"""
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    v = doc.get("generated_at") if isinstance(doc, dict) else None
    return v if isinstance(v, str) else None


class BoardWatcher(threading.Thread):
    """守护线程；stop() 幂等。baseline = 启动时的现状（启动不触发事件）。"""

    def __init__(self, home: Path, hub: EventHub,
                 interval: float = POLL_INTERVAL) -> None:
        super().__init__(name="board-watcher", daemon=True)
        self._path = paths.dashboard_path(home)
        self._hub = hub
        self._interval = interval
        self._stop = threading.Event()
        self._last = _stat_key(self._path)

    def run(self) -> None:
        while not self._stop.wait(self._interval):
            cur = _stat_key(self._path)
            if cur == self._last:
                continue
            self._last = cur
            if cur is None:
                continue  # 文件消失：不广播（下次出现再推）
            self._hub.publish("board.updated",
                              {"generated_at": _generated_at(self._path)})

    def stop(self) -> None:
        self._stop.set()
