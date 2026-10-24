"""GET /api/events（SSE）+ RecapWatcher（§49 / §63；server/sse.py + server/watcher.py）。

覆盖：帧格式常量（A5 realtime 的解码判据）、EventHub 满队列静默丢弃、
watcher 对 ``state/recap/`` 与 ``state/recap/recaps/`` 的 mtime 变更→广播 / 目录消失→不广播、
端到端：真 server + 真 watcher，原子写一份纪要文件后客户端在既有连接上收到
``recaps.updated``（v2.0.0：dashboard.json 与 ``board.updated`` 随 actd 退役）。
心跳间隔 25s 不做实时等待——只钉常量与注释行格式。
"""
from __future__ import annotations

import http.client
import json
import tempfile
import time
import unittest
from pathlib import Path

from tests import TMP_HOME  # noqa: F401 - ensures the sandbox env is set first
from tests.test_server_common import start_server

from server.sse import (CONNECTED_FRAME, HEARTBEAT_FRAME, HEARTBEAT_SECONDS,
                        EventHub, format_frame)
from server import watcher as watcher_mod
from server.watcher import RecapWatcher


class EventHubTestCase(unittest.TestCase):
    def test_frame_format_is_the_wire_contract(self):
        # A5 realtime.ts 只认这个词表/形状：event 名 + 单行 JSON data
        frame = format_frame("board.updated",
                             {"generated_at": "2030-01-01T00:00:00Z"})
        self.assertEqual(
            frame,
            b'event: board.updated\n'
            b'data: {"generated_at": "2030-01-01T00:00:00Z"}\n\n')
        self.assertEqual(CONNECTED_FRAME, b": connected\n\n")
        self.assertEqual(HEARTBEAT_FRAME, b": keep-alive\n\n")
        self.assertEqual(HEARTBEAT_SECONDS, 25.0)

    def test_publish_reaches_all_subscribers(self):
        hub = EventHub()
        q1, q2 = hub.subscribe(), hub.subscribe()
        hub.publish("board.updated", {"generated_at": None})
        for q in (q1, q2):
            self.assertIn(b"board.updated", q.get_nowait())
        hub.unsubscribe(q1)
        hub.publish("board.updated", {"generated_at": None})
        self.assertIn(b"board.updated", q2.get_nowait())
        self.assertTrue(q1.empty())

    def test_full_queue_drops_silently(self):
        hub = EventHub()
        q = hub.subscribe()
        for _ in range(64):  # 超过 maxsize=16，绝不抛、绝不阻塞
            hub.publish("board.updated", {"generated_at": None})
        self.assertEqual(q.qsize(), 16)


def _write_recap(home: Path, name: str = "meeting_2026-09-01T1000-zoom.json") -> None:
    """Atomic tmp + rename into state/recap/recaps/ — the act.recap writer's shape."""
    d = home / "state" / "recap" / "recaps"
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / (name + ".tmp")
    tmp.write_text(json.dumps({"key": "meeting:2026-09-01T1000-zoom", "t": time.time()}),
                   encoding="utf-8")
    tmp.replace(d / name)


class RecapWatcherTestCase(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="zai-g5-watch-"))
        (self.home / "state" / "recap" / "recaps").mkdir(parents=True)
        self.hub = EventHub()
        self.q = self.hub.subscribe()
        self.watcher = RecapWatcher(self.home, self.hub, interval=0.02)
        self.watcher.start()
        self.addCleanup(self.watcher.stop)

    def test_vocabulary(self):
        self.assertEqual(watcher_mod.EVENT, "recaps.updated")
        self.assertIs(watcher_mod.BoardWatcher, RecapWatcher)   # v1 name kept as an alias
        self.assertEqual(watcher_mod.recap_dirs(self.home),
                         (self.home / "state" / "recap", self.home / "state" / "recap" / "recaps"))

    def test_startup_baseline_emits_nothing(self):
        time.sleep(0.2)
        self.assertTrue(self.q.empty(), "watcher must not fire on its startup baseline")

    def test_a_new_recap_file_publishes_recaps_updated(self):
        time.sleep(0.05)
        _write_recap(self.home)
        frame = self.q.get(timeout=3)
        self.assertIn(b"event: recaps.updated", frame)
        data = json.loads(frame.split(b"data: ")[1].split(b"\n")[0])
        self.assertEqual(set(data), {"updated_at"})

    def test_directory_disappearing_is_silent_until_it_returns(self):
        import shutil
        shutil.rmtree(self.home / "state" / "recap")
        time.sleep(0.2)
        self.assertTrue(self.q.empty(), "deletion must not broadcast")
        _write_recap(self.home)
        frame = self.q.get(timeout=3)
        self.assertIn(b"recaps.updated", frame)

    def test_stop_is_idempotent(self):
        self.watcher.stop()
        self.watcher.stop()


class SseEndToEndTestCase(unittest.TestCase):
    """真 server + 真 watcher（默认 300ms 轮询）：连上 → 写一份纪要 → 收到事件。"""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="zai-g5-sse-"))
        (self.home / "state" / "recap" / "recaps").mkdir(parents=True)
        _, self.port = start_server(self, self.home, start_watcher=True)

    def test_recaps_updated_arrives_on_a_recap_write(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        self.addCleanup(conn.close)
        conn.request("GET", "/api/events", headers={"Accept": "text/event-stream"})
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)
        self.assertTrue(resp.getheader("Content-Type", "").startswith("text/event-stream"))
        # 连接即到的注释帧（客户端可用作「已连上」信号）
        self.assertEqual(resp.fp.readline(), b": connected\n")
        self.assertEqual(resp.fp.readline(), b"\n")
        time.sleep(0.05)
        _write_recap(self.home)
        deadline = time.time() + 10
        lines = []
        got_event = False
        while time.time() < deadline:
            line = resp.fp.readline()
            if not line:
                break
            lines.append(line)
            if line == b"event: recaps.updated\n":
                got_event = True
            if got_event and line.startswith(b"data: "):
                self.assertIn("updated_at", json.loads(line[len(b"data: "):]))
                return
        self.fail(f"no recaps.updated within deadline; got: {lines!r}")


class _PoisonQueue:
    """get() 即炸——模拟已开流后的任意 mid-stream 异常。"""

    def get(self, timeout=None):
        raise RuntimeError("mid-stream boom")


class _PoisonHub:
    def __init__(self):
        self.unsubscribed = False

    def subscribe(self):
        return _PoisonQueue()

    def unsubscribe(self, q):
        self.unsubscribed = True

    def publish(self, event, data):
        pass


class MidStreamErrorTestCase(unittest.TestCase):
    """流已开后的非连接类异常：静默断流 + 退订，绝不把 500 envelope
    行写进已开启的 event-stream（app.py _serve_events 的 except Exception）。"""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="zai-g5-sse-err-"))
        (self.home / "state").mkdir()
        self.httpd, self.port = start_server(self, self.home)
        self.hub = _PoisonHub()
        self.httpd.ctx.hub = self.hub

    def test_mid_stream_error_closes_stream_quietly(self):
        import contextlib
        import io
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        self.addCleanup(conn.close)
        # 静音 server 线程的 traceback 日志（断言只看 wire 上的字节）
        with contextlib.redirect_stderr(io.StringIO()):
            conn.request("GET", "/api/events",
                         headers={"Accept": "text/event-stream"})
            resp = conn.getresponse()
            self.assertEqual(resp.status, 200)
            self.assertEqual(resp.fp.readline(), b": connected\n")
            self.assertEqual(resp.fp.readline(), b"\n")
            rest = resp.fp.read()  # 修复前这里会读到 500 status 行 + envelope
        self.assertEqual(rest, b"")
        self.assertTrue(self.hub.unsubscribed,
                        "handler must unsubscribe on the way out")


if __name__ == "__main__":
    unittest.main()
