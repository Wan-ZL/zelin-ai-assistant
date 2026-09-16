"""覆盖跑者的 HTTP body 读取封顶 + 硬墙钟（CONTRACT §77.2 / §77.7）。

2026-09-16 事故：`http:GET /api/events`（SSE 流）的 body 永不结束，`resp.read()`
把整轮全覆盖跑挂死。判例钉住：`Http._read_capped` 对一个永不返回的 read 在
`READ_DEADLINE` 内放弃、返回已到手的部分，绝不阻塞——状态码在读 body 之前已到手。
"""
import importlib.util
import threading
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "coverage_run_sse", REPO / "scripts" / "qa" / "coverage_run.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cr = _load()


class _BlockingResp:
    """read() 永不返回（模拟 SSE 流）；close() 让阻塞的 read 抛错解开。"""

    def __init__(self):
        self._gate = threading.Event()
        self.closed = False

    def read(self, _n=-1):
        self._gate.wait()          # 永不 set —— 永远阻塞，直到 close()
        raise OSError("closed")

    def close(self):
        self.closed = True
        self._gate.set()


class _SlowResp:
    """read() 睡一小会儿再返回一小段 body（正常快端点的样子）。"""

    def read(self, _n=-1):
        time.sleep(0.05)
        return b'{"ok": true}'

    def close(self):
        pass


class SseNoHangTest(unittest.TestCase):
    def test_streaming_body_is_abandoned_at_the_deadline(self):
        http = cr.Http()
        http.READ_DEADLINE = 0.5      # 判例内把墙钟压短
        resp = _BlockingResp()
        t0 = time.monotonic()
        text = http._read_capped(resp)
        elapsed = time.monotonic() - t0
        self.assertLess(elapsed, 3.0, "read_capped must not block on a streaming body")
        self.assertEqual(text, "")
        self.assertTrue(resp.closed, "the stuck socket must be closed to unblock it")

    def test_normal_body_is_read_in_full(self):
        http = cr.Http()
        self.assertEqual(http._read_capped(_SlowResp()), '{"ok": true}')


if __name__ == "__main__":
    unittest.main()
