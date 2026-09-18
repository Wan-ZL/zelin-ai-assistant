"""GET /api/health — pipeline liveness for the web banner (CONTRACT §47.4 / §49).

The Mac app's staleness banner was the ONLY detector of the 2026-08-31 silent
stall and it is retiring (D3); this endpoint is its replacement. Pinned:

- token-light GET (same read discipline as /api/board), JSON, no-store;
- verdict ladder: fresh heartbeat → ok; heartbeat older than the WRITER's
  stale_after_s → stalled; loop_health ≥ 3 crashes → failing; no heartbeat +
  stale/missing dashboard → stale; no heartbeat + fresh dashboard → unknown
  (pre-v0.48.4 daemon still writing);
- the threshold comes from the heartbeat body, never re-derived here;
- torn/missing files never 500 — they read as absent;
- a read DENIAL is a third thing (§47.4 追记 2026-09-18, issue #423): still 200,
  still ``dashboard: null``, plus ``dashboard_error {path, errno, strerror}``.

Real server on a random port (tests/test_server_common.py), tmp home per case.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - ensures the sandbox env is set first
from tests.test_server_common import (get_json, http_request, start_server,
                                      write_text)

from server import health


def _iso(ts: float) -> str:
    import datetime as _dt
    return _dt.datetime.fromtimestamp(ts, _dt.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


class HealthSnapshotTestCase(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="zai-health-"))
        (self.home / "state").mkdir()
        self.now = time.time()

    def _heartbeat(self, age_s: float, phase="idle", stale_after=90, body=True):
        p = self.home / "state" / "actd.heartbeat"
        if body:
            p.write_text(json.dumps({"ts": _iso(self.now - age_s), "phase": phase,
                                     "pid": 4242, "interval": 10,
                                     "stale_after_s": stale_after}),
                         encoding="utf-8")
        else:
            p.write_text("{torn", encoding="utf-8")
        os.utime(p, (self.now - age_s, self.now - age_s))

    def _dashboard(self, age_s: float):
        write_text(self.home / "state" / "dashboard.json",
                   json.dumps({"generated_at": _iso(self.now - age_s)}))

    def _loop_health(self, n: int):
        write_text(self.home / "state" / "loop_health.json",
                   json.dumps({"consecutive_failures": n, "last_error": "NameError: x"}))

    def test_fresh_heartbeat_is_ok(self):
        self._heartbeat(4)
        self._dashboard(5)
        snap = health.snapshot(self.home, now=self.now)
        self.assertEqual(snap["verdict"], "ok")
        self.assertEqual(snap["heartbeat"]["phase"], "idle")
        self.assertFalse(snap["heartbeat"]["stale"])
        self.assertAlmostEqual(snap["heartbeat"]["age_s"], 4, delta=1)
        self.assertFalse(snap["dashboard"]["stale"])
        self.assertEqual(snap["loop_health"]["consecutive_failures"], 0)

    def test_stale_heartbeat_is_stalled_even_with_a_fresh_looking_body(self):
        # 2026-08-31 22:31: the body says "idle", the mtime says 150 min ago
        self._heartbeat(150 * 60, phase="reconcile")
        self._dashboard(150 * 60)
        snap = health.snapshot(self.home, now=self.now)
        self.assertEqual(snap["verdict"], "stalled")
        self.assertTrue(snap["heartbeat"]["stale"])
        self.assertEqual(snap["heartbeat"]["phase"], "reconcile")

    def test_threshold_comes_from_the_writer(self):
        self._heartbeat(120, stale_after=180)      # a 60 s-interval daemon
        self._dashboard(5)
        self.assertEqual(health.snapshot(self.home, now=self.now)["verdict"], "ok")
        self._heartbeat(120, stale_after=90)
        self.assertEqual(health.snapshot(self.home, now=self.now)["verdict"], "stalled")

    def test_loop_crashes_outrank_a_fresh_heartbeat(self):
        self._heartbeat(3)
        self._dashboard(3)
        self._loop_health(3)
        snap = health.snapshot(self.home, now=self.now)
        self.assertEqual(snap["verdict"], "failing")
        self.assertEqual(snap["loop_health"]["consecutive_failures"], 3)
        self.assertIn("NameError", snap["loop_health"]["last_error"])

    def test_no_heartbeat_with_stale_dashboard_is_stale(self):
        self._dashboard(600)
        snap = health.snapshot(self.home, now=self.now)
        self.assertEqual(snap["verdict"], "stale")
        self.assertIsNone(snap["heartbeat"])
        self.assertTrue(snap["dashboard"]["stale"])

    def test_no_heartbeat_but_fresh_dashboard_is_unknown(self):
        # an old daemon (pre-v0.48.4) still writing the board: not dead, not proven alive
        self._dashboard(5)
        self.assertEqual(health.snapshot(self.home, now=self.now)["verdict"], "unknown")

    def test_nothing_on_disk_is_stale_not_a_crash(self):
        snap = health.snapshot(self.home, now=self.now)
        self.assertEqual(snap["verdict"], "stale")
        self.assertIsNone(snap["heartbeat"])
        self.assertIsNone(snap["dashboard"])

    def test_torn_heartbeat_body_still_uses_its_mtime(self):
        self._heartbeat(5, body=False)
        self._dashboard(5)
        snap = health.snapshot(self.home, now=self.now)
        self.assertEqual(snap["verdict"], "ok")
        self.assertIsNone(snap["heartbeat"]["phase"])
        self.assertEqual(snap["heartbeat"]["stale_after_s"], 90)   # floor fallback


class DashboardErrorTestCase(unittest.TestCase):
    """``dashboard_error`` — 「没有看板」与「读不动看板」自此分得出来。

    CONTRACT §47.4 追记 2026-09-18（issue #423）：``dashboard: null`` 同时覆盖
    缺席 / 撕裂 / 读被拒三种，wire 上长得一模一样；这一键只把第三种分出来，
    其余一切情况恒为 null，``dashboard: null`` 的含义一字不变。
    """

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="zai-health-denial-"))
        (self.home / "state").mkdir()
        self.dash = self.home / "state" / "dashboard.json"
        self.now = time.time()

    def _snap(self):
        return health.snapshot(self.home, now=self.now)

    def test_a_good_board_reports_no_denial(self):
        write_text(self.dash, json.dumps({"generated_at": _iso(self.now)}))
        snap = self._snap()
        self.assertIsNotNone(snap["dashboard"])
        self.assertIsNone(snap["dashboard_error"])

    def test_an_absent_board_reports_no_denial(self):
        # 缺席不是拒绝：两个键都 null，verdict 阶梯照旧
        snap = self._snap()
        self.assertIsNone(snap["dashboard"])
        self.assertIsNone(snap["dashboard_error"])
        self.assertEqual(snap["verdict"], "stale")

    def test_a_torn_json_body_reports_no_denial(self):
        write_text(self.dash, "{torn")
        snap = self._snap()
        self.assertIsNone(snap["dashboard"])
        self.assertIsNone(snap["dashboard_error"])

    def test_a_non_utf8_body_is_absent_not_a_500(self):
        """``UnicodeDecodeError`` 是 ``ValueError``、**不是** ``OSError``。

        它必须与 ``json.loads`` 共用同一个 try——漏掉它，一份非 UTF-8 的看板会
        让 ``snapshot()`` 抛出去、/api/health 变成 500（§49 路由行「永不 500」）。"""
        self.dash.write_bytes(b'{"generated_at": "\xff\xfe"}')
        snap = self._snap()                        # 不抛就是判例本身
        self.assertIsNone(snap["dashboard"])
        self.assertIsNone(snap["dashboard_error"])

    def _denied(self, exc: OSError):
        """只拒 dashboard.json 那一次读，别把心跳与 loop_health 一起拒掉。

        进程级 ``side_effect`` 会让三个文件一起读不到，于是 verdict 无论怎样都是
        ``stale``——那样的 verdict 断言证不了任何东西（本轮 review 抓到的原状）。"""
        write_text(self.dash, json.dumps({"generated_at": _iso(self.now)}))
        real = Path.read_text

        def only_dashboard(self_path, *a, **kw):
            if self_path == self.dash:
                raise exc
            return real(self_path, *a, **kw)

        with mock.patch.object(Path, "read_text", only_dashboard):
            return health.snapshot(self.home, now=self.now)

    def test_a_read_denial_reports_path_errno_and_strerror(self):
        import errno as _errno
        snap = self._denied(PermissionError(_errno.EPERM,
                                            os.strerror(_errno.EPERM)))
        self.assertIsNone(snap["dashboard"])       # 既有键含义不变
        self.assertEqual(snap["dashboard_error"]["errno"], _errno.EPERM)
        self.assertEqual(snap["dashboard_error"]["path"], str(self.dash))
        self.assertIsInstance(snap["dashboard_error"]["strerror"], str)

    def test_a_bare_oserror_is_a_denial_too_not_just_permissionerror(self):
        """分界线是 ``OSError``，**不是** ``PermissionError``。

        EIO / EISDIR / ELOOP 都是裸 ``OSError``（EISDIR 那枚是子类但也不是
        ``PermissionError``）。把 except 收窄成 ``PermissionError`` 会让它们穿透
        出去、``/api/health`` 变 500——而在加这条判例之前，那个收窄**一条判例都
        不红**（本轮 review 实测 49 条全绿）。"""
        import errno as _errno
        for exc in (OSError(_errno.EIO, os.strerror(_errno.EIO)),
                    IsADirectoryError(_errno.EISDIR, "is a directory"),
                    OSError(_errno.ELOOP, os.strerror(_errno.ELOOP))):
            with self.subTest(errno=exc.errno):
                snap = self._denied(exc)           # 不抛出去就是判例的一半
                self.assertEqual(snap["dashboard_error"]["errno"], exc.errno)
                self.assertIsNone(snap["dashboard"])

    def test_a_denial_alone_does_not_change_the_verdict(self):
        """读被拒**不**长第六个 verdict，也不把一个活着的心跳说成别的。

        读者 3 的 ``default: return null`` 会让新 verdict 一个横幅都不渲染，
        ``repairActd`` 还会把每次一键修复都报成超时——所以这里钉**确切值** `ok`
        （心跳新鲜、只有看板读不动），不是「在五个里面」那种恒真断言。"""
        import errno as _errno
        hb = self.home / "state" / "actd.heartbeat"
        hb.write_text(json.dumps({"phase": "idle", "pid": 1, "interval": 10,
                                  "stale_after_s": 90}), encoding="utf-8")
        os.utime(hb, (self.now - 3, self.now - 3))
        snap = self._denied(PermissionError(_errno.EACCES, "denied"))
        self.assertEqual(snap["verdict"], "ok")
        self.assertEqual(snap["dashboard_error"]["errno"], _errno.EACCES)

    def test_the_dashboard_is_read_exactly_once_per_snapshot(self):
        """§47.4「它只 stat 三个文件」：新键不许换来第二次读。"""
        write_text(self.dash, json.dumps({"generated_at": _iso(self.now)}))
        real = Path.read_text
        seen = []

        def counting(self_path, *a, **kw):
            if self_path == self.dash:
                seen.append(str(self_path))
            return real(self_path, *a, **kw)

        with mock.patch.object(Path, "read_text", counting):
            self._snap()
        self.assertEqual(len(seen), 1)


class HealthRouteTestCase(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="zai-health-route-"))
        (self.home / "state").mkdir()
        _, self.port = start_server(self, self.home)

    def test_route_is_token_light_json_no_store(self):
        p = self.home / "state" / "actd.heartbeat"
        p.write_text(json.dumps({"phase": "idle", "pid": 1, "interval": 10,
                                 "stale_after_s": 90}), encoding="utf-8")
        status, headers, body = http_request(self.port, "GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertIn("application/json", headers.get("Content-Type", ""))
        self.assertEqual(headers.get("Cache-Control"), "no-store")
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        snap = json.loads(body.decode("utf-8"))
        self.assertEqual(snap["verdict"], "ok")
        self.assertIn("checked_at", snap)

    def test_empty_home_answers_stale_not_500(self):
        status, snap = get_json(self.port, "/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(snap["verdict"], "stale")


if __name__ == "__main__":
    unittest.main()
