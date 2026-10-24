"""state/recap/ 变更监视 → SSE ``recaps.updated``（CONTRACT §49 / §63）。

300ms mtime 轮询（不用 FSEvents：stdlib 白名单）。被看的是两个目录本身的 mtime：
``state/recap/``（sessions.json / marks.json 的原子 rename 落在这里）与
``state/recap/recaps/``（每份纪要文件的原子 rename 落在这里）——每个写者都是
tmp + rename，目录项一变目录 mtime 就变，读不到半截文件。事件 data 只带
``{"updated_at"}``，客户端收到后自行 refetch ``GET /api/recaps``，server 不推全量。

v2.0.0：dashboard.json 与 ``board.updated`` 事件随 actd 退役（全文见 tag full）。
"""
from __future__ import annotations

import datetime as _dt
import threading
from pathlib import Path
from typing import Optional

from server.sse import EventHub

POLL_INTERVAL = 0.3
EVENT = "recaps.updated"


def recap_dirs(home: Path) -> tuple:
    # mirrors act/lib/recap_store.recap_dir / recaps_dir
    base = home / "state" / "recap"
    return (base, base / "recaps")


def _stat_key(dirs: tuple) -> Optional[tuple]:
    out = []
    for d in dirs:
        try:
            out.append(d.stat().st_mtime_ns)
        except OSError:
            out.append(None)
    return tuple(out) if any(v is not None for v in out) else None


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class RecapWatcher(threading.Thread):
    """守护线程；stop() 幂等。baseline = 启动时的现状（启动不触发事件）。"""

    def __init__(self, home: Path, hub: EventHub,
                 interval: float = POLL_INTERVAL) -> None:
        super().__init__(name="recap-watcher", daemon=True)
        self._dirs = recap_dirs(home)
        self._hub = hub
        self._interval = interval
        self._stop = threading.Event()
        self._last = _stat_key(self._dirs)

    def run(self) -> None:
        while not self._stop.wait(self._interval):
            cur = _stat_key(self._dirs)
            if cur == self._last:
                continue
            self._last = cur
            if cur is None:
                continue  # 目录消失：不广播（下次出现再推）
            self._hub.publish(EVENT, {"updated_at": _now_iso()})

    def stop(self) -> None:
        self._stop.set()


BoardWatcher = RecapWatcher   # v1 name (server/app.py and older tests)
