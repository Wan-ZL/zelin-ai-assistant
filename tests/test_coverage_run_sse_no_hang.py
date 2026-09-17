"""覆盖跑者的 HTTP body 读取封顶 + 硬墙钟，单线程（CONTRACT §77.2 / §77.7）。

2026-09-16 事故：`http:GET /api/events`（SSE 流）的 body 永不结束。第一版用
`resp.read()` 直接挂死；第二版另起线程读、到点 `resp.close()`，主线程死在
BufferedReader 的锁上（12 h）。判例钉住现在的形：`read1` 逐块 + 墙钟，永不返回的
流在 `READ_DEADLINE` 内放弃并返回已到手的部分；正常 body 读满到 EOF。
"""
import importlib.util
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


class _StreamResp:
    """read1() 永远有下一块（模拟 SSE keepalive 刷屏），永不 EOF。"""

    def __init__(self):
        self.calls = 0

    def read1(self, _n=-1):
        self.calls += 1
        time.sleep(0.01)
        return b": keepalive\n\n"

    def close(self):
        pass


class _BodyResp:
    """正常端点：两块 body 然后 EOF。"""

    def __init__(self):
        self._parts = [b'{"ok": ', b'true}', b""]

    def read1(self, _n=-1):
        return self._parts.pop(0)

    def close(self):
        pass


class _LegacyResp:
    """没有 read1 的对象（老 fp 形）：回落到 read()——真 read() 在 EOF 回 b""。"""

    def __init__(self):
        self._parts = [b"plain", b""]

    def read(self, _n=-1):
        return self._parts.pop(0)


class SseNoHangTest(unittest.TestCase):
    def test_streaming_body_is_abandoned_at_the_deadline_without_threads(self):
        http = cr.Http()
        http.READ_DEADLINE = 0.3
        resp = _StreamResp()
        t0 = time.monotonic()
        text = http._read_capped(resp)
        self.assertLess(time.monotonic() - t0, 2.0, "must stop at the wall clock, not hang")
        self.assertIn("keepalive", text)          # 已到手的部分被保留
        self.assertGreater(resp.calls, 1)          # 确实在逐块读，而不是一次 read()

    def test_normal_body_is_read_to_eof(self):
        self.assertEqual(cr.Http()._read_capped(_BodyResp()), '{"ok": true}')

    def test_object_without_read1_falls_back_to_read(self):
        self.assertEqual(cr.Http()._read_capped(_LegacyResp()), "plain")

    def test_socket_lookup_tolerates_fakes(self):
        self.assertIsNone(cr.Http._socket_of(_BodyResp()))


if __name__ == "__main__":
    unittest.main()
