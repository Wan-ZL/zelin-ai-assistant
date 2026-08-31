"""SSE hub（参考 dashi server/app.mjs EventHub，Apache-2.0，模式非逐字）。

事件词表仅 ``board.updated`` ``{generated_at}``；25s heartbeat 注释行；
无重连契约——客户端断线后全量 refetch（dashi 实证的务实分层）。

线程模型：ThreadingHTTPServer 下每个 SSE 连接占一个 handler 线程，
handler 阻塞在自己的 Queue 上；publish 方 put_nowait，队列满就丢
（客户端只要收到任意一条 board.updated 都会 refetch，丢事件无损）。
"""
from __future__ import annotations

import json
import queue
import threading

HEARTBEAT_SECONDS = 25.0
_CLIENT_QUEUE_MAX = 16


class EventHub:
    def __init__(self) -> None:
        self._clients: "set[queue.Queue]" = set()
        self._lock = threading.Lock()

    def subscribe(self) -> "queue.Queue":
        q: "queue.Queue" = queue.Queue(maxsize=_CLIENT_QUEUE_MAX)
        with self._lock:
            self._clients.add(q)
        return q

    def unsubscribe(self, q: "queue.Queue") -> None:
        with self._lock:
            self._clients.discard(q)

    def client_count(self) -> int:
        with self._lock:
            return len(self._clients)

    def publish(self, event: str, data: dict) -> None:
        """向所有在线客户端广播一帧；满队列静默丢弃（见模块注释）。"""
        frame = format_frame(event, data)
        with self._lock:
            clients = list(self._clients)
        for q in clients:
            try:
                q.put_nowait(frame)
            except queue.Full:
                pass


def format_frame(event: str, data: dict) -> bytes:
    payload = json.dumps(data, ensure_ascii=False, sort_keys=True)
    return f"event: {event}\ndata: {payload}\n\n".encode("utf-8")


# heartbeat 是注释行（``:`` 前缀），EventSource 忽略但保活代理/连接
HEARTBEAT_FRAME = b": keep-alive\n\n"
CONNECTED_FRAME = b": connected\n\n"
