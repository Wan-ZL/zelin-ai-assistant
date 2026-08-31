"""GET /api/events（SSE）+ BoardWatcher（§2.1 sse/watcher）。

覆盖：帧格式常量（A5 realtime 的解码判据）、EventHub 满队列静默丢弃、
watcher mtime 变更→广播 / 文件消失→不广播、端到端：真 server + 真 watcher，
touch dashboard.json 后客户端在既有连接上收到 ``board.updated``。
心跳间隔 25s 不做实时等待——只钉常量与注释行格式。
"""
from __future__ import annotations

import http.client
import json
import queue
import tempfile
import time
import unittest
from pathlib import Path

from tests import TMP_HOME  # noqa: F401 - ensures the sandbox env is set first
from tests.test_server_common import (DEMO_SEED_PATH, rewrite_board,
                                      seed_scene, start_server)

from server.sse import (CONNECTED_FRAME, HEARTBEAT_FRAME, HEARTBEAT_SECONDS,
                        EventHub, format_frame)
from server.watcher import BoardWatcher


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


@unittest.skipUnless(DEMO_SEED_PATH, "scripts/demo_seed.py not found")
class BoardWatcherTestCase(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="zai-g5-watch-"))
        self.dash = seed_scene(self.home, "initial")
        self.hub = EventHub()
        self.q = self.hub.subscribe()
        self.watcher = BoardWatcher(self.home, self.hub, interval=0.02)
        self.watcher.start()
        self.addCleanup(self.watcher.stop)

    def test_startup_baseline_emits_nothing(self):
        time.sleep(0.2)
        self.assertTrue(self.q.empty(),
                        "watcher must not fire on its startup baseline")

    def test_rewrite_publishes_board_updated_with_generated_at(self):
        self.dash["generated_at"] = "2031-12-31T23:59:59Z"
        rewrite_board(self.home, self.dash)
        frame = self.q.get(timeout=3)
        self.assertIn(b"event: board.updated", frame)
        data = json.loads(frame.split(b"data: ")[1].split(b"\n")[0])
        self.assertEqual(data, {"generated_at": "2031-12-31T23:59:59Z"})

    def test_file_disappearing_is_silent_until_it_returns(self):
        path = self.home / "state" / "dashboard.json"
        path.unlink()
        time.sleep(0.2)
        self.assertTrue(self.q.empty(), "deletion must not broadcast")
        self.dash["generated_at"] = "2032-01-01T00:00:00Z"
        rewrite_board(self.home, self.dash)
        frame = self.q.get(timeout=3)
        self.assertIn(b"2032-01-01T00:00:00Z", frame)

    def test_stop_is_idempotent(self):
        self.watcher.stop()
        self.watcher.stop()


@unittest.skipUnless(DEMO_SEED_PATH, "scripts/demo_seed.py not found")
class SseEndToEndTestCase(unittest.TestCase):
    """真 server + 真 watcher（默认 300ms 轮询）：连上 → touch → 收到事件。"""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="zai-g5-sse-"))
        self.dash = seed_scene(self.home, "initial")
        _, self.port = start_server(self, self.home, start_watcher=True)

    def test_board_updated_arrives_on_dashboard_touch(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        self.addCleanup(conn.close)
        conn.request("GET", "/api/events",
                     headers={"Accept": "text/event-stream"})
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)
        self.assertTrue(resp.getheader("Content-Type", "")
                        .startswith("text/event-stream"))
        # 连接即到的注释帧（客户端可用作「已连上」信号）
        self.assertEqual(resp.fp.readline(), b": connected\n")
        self.assertEqual(resp.fp.readline(), b"\n")

        marker = "2033-03-03T03:03:03Z"
        self.dash["generated_at"] = marker
        rewrite_board(self.home, self.dash)

        # 300ms 轮询 + 线程调度余量：在既有连接上等 board.updated
        deadline = time.time() + 10
        lines = []
        got_event = False
        while time.time() < deadline:
            line = resp.fp.readline()
            if not line:
                break
            lines.append(line)
            if line == b"event: board.updated\n":
                got_event = True
            if got_event and line.startswith(b"data: "):
                data = json.loads(line[len(b"data: "):])
                self.assertEqual(data, {"generated_at": marker})
                return
        self.fail(f"no board.updated within deadline; got: {lines!r}")


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


@unittest.skipUnless(DEMO_SEED_PATH, "scripts/demo_seed.py not found")
class MidStreamErrorTestCase(unittest.TestCase):
    """流已开后的非连接类异常：静默断流 + 退订，绝不把 500 envelope
    行写进已开启的 event-stream（app.py _serve_events 的 except Exception）。"""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="zai-g5-sse-err-"))
        seed_scene(self.home, "initial")
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
