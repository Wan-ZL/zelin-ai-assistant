"""GET /api/health — pipeline liveness for the web banner (CONTRACT §47.4 / §49).

The Mac app's staleness banner was the ONLY detector of the 2026-08-31 silent
stall and it is retiring (D3); this endpoint is its replacement. Pinned:

- token-light GET (same read discipline as /api/board), JSON, no-store;
- verdict ladder: fresh heartbeat → ok; heartbeat older than the WRITER's
  stale_after_s → stalled; loop_health ≥ 3 crashes → failing; no heartbeat +
  stale/missing dashboard → stale; no heartbeat + fresh dashboard → unknown
  (pre-v0.48.4 daemon still writing);
- the threshold comes from the heartbeat body, never re-derived here;
- torn/missing files never 500 — they read as absent.

Real server on a random port (tests/test_server_common.py), tmp home per case.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path

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
