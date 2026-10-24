"""GET /api/health — the liveness line the web shows as a banner (CONTRACT §49; v2.0.0).

The server answering is the first half of liveness (the shell's connection
probe hits this route); the other two are read-only files:

- ``state/cron_probe.json`` ``ts`` (§25) — when the §18 cron ingest chain last fired;
- ``state/recap/sessions.json`` ``updated_at`` (§63) — when ``act.recap`` last
  finished a round.

Pinned: token-light GET, JSON, no-store; body = ``{verdict, server, cron, recap,
checked_at}``; ladder = cron probe older than CRON_FRESH_SECONDS → ``stale``, no
probe yet → ``unknown``, fresh → ``ok``; the probe ``ts`` may be epoch seconds or
ISO-Z; torn / missing files never 500 — they read as absent. (The v1 actd
heartbeat / dashboard freshness / loop-health ladder retired with actd — full text
in tag ``full``; tests/test_server_health_verdict_ladder.py went with it.)

Real server on a random port (tests/test_server_common.py), tmp home per case.
"""
from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from tests import TMP_HOME  # noqa: F401 - ensures the sandbox env is set first
from tests.test_server_common import get_json, http_request, start_server, write_text

from server import health


def _iso(ts: float) -> str:
    import datetime as _dt
    return _dt.datetime.fromtimestamp(ts, _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class HealthSnapshotTestCase(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="zai-health-"))
        (self.home / "state").mkdir()
        self.now = float(int(time.time()))   # whole seconds: ISO-Z stamps carry no fraction

    def _probe(self, ts, read_ok=True):
        write_text(self.home / "state" / "cron_probe.json", json.dumps({"ts": ts, "read_ok": read_ok}))

    def _sessions(self, age_s: float):
        write_text(self.home / "state" / "recap" / "sessions.json",
                   json.dumps({"updated_at": _iso(self.now - age_s), "cursor": {}}))

    def test_fresh_cron_probe_is_ok(self):
        self._probe(_iso(self.now - 600))
        snap = health.snapshot(self.home, now=self.now)
        self.assertEqual(snap["verdict"], "ok")
        self.assertEqual(snap["server"], {"ok": True})
        self.assertEqual(snap["cron"], {"age_s": 600.0, "read_ok": True, "stale": False})
        self.assertEqual(set(snap), {"verdict", "server", "cron", "recap", "checked_at"})

    def test_epoch_stamp_reads_too(self):
        self._probe(self.now - 60)
        self.assertEqual(health.snapshot(self.home, now=self.now)["verdict"], "ok")

    def test_old_cron_probe_is_stale(self):
        self._probe(_iso(self.now - health.CRON_FRESH_SECONDS - 60))
        snap = health.snapshot(self.home, now=self.now)
        self.assertEqual(snap["verdict"], "stale")
        self.assertTrue(snap["cron"]["stale"])

    def test_exactly_the_threshold_is_still_fresh(self):
        self._probe(self.now - health.CRON_FRESH_SECONDS)
        self.assertEqual(health.snapshot(self.home, now=self.now)["verdict"], "ok")

    def test_no_probe_is_unknown_not_a_crash(self):
        snap = health.snapshot(self.home, now=self.now)
        self.assertEqual(snap["verdict"], "unknown")
        self.assertIsNone(snap["cron"])
        self.assertIsNone(snap["recap"])

    def test_torn_or_garbage_probe_reads_as_absent(self):
        write_text(self.home / "state" / "cron_probe.json", "{torn")
        self.assertEqual(health.snapshot(self.home, now=self.now)["verdict"], "unknown")
        for ts in (True, "yesterday", None, [1]):
            with self.subTest(ts=ts):
                self._probe(ts)
                self.assertEqual(health.snapshot(self.home, now=self.now)["verdict"], "unknown")

    def test_future_timestamp_clamps_age_to_zero(self):
        self._probe(self.now + 3600)
        self.assertEqual(health.snapshot(self.home, now=self.now)["cron"]["age_s"], 0.0)

    def test_recap_block_reports_the_last_round(self):
        self._sessions(120)
        snap = health.snapshot(self.home, now=self.now)
        self.assertEqual(snap["recap"]["age_s"], 120.0)
        self.assertEqual(snap["recap"]["updated_at"], _iso(self.now - 120))
        write_text(self.home / "state" / "recap" / "sessions.json", "[1]")
        self.assertIsNone(health.snapshot(self.home, now=self.now)["recap"])

    def test_threshold_mirrors_the_doctor_cron_row(self):
        from act.lib.checks import cron
        self.assertEqual(health.CRON_FRESH_SECONDS, cron.CRON_PROBE_FRESH_SECONDS)


class HealthRouteTestCase(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="zai-health-route-"))
        (self.home / "state").mkdir()
        _, self.port = start_server(self, self.home)

    def test_route_is_token_light_json_no_store(self):
        write_text(self.home / "state" / "cron_probe.json",
                   json.dumps({"ts": _iso(time.time() - 60), "read_ok": True}))
        status, headers, body = http_request(self.port, "GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertIn("application/json", headers.get("Content-Type", ""))
        self.assertEqual(headers.get("Cache-Control"), "no-store")
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        snap = json.loads(body.decode("utf-8"))
        self.assertEqual(snap["verdict"], "ok")
        self.assertIn("checked_at", snap)

    def test_empty_home_answers_unknown_not_500(self):
        status, snap = get_json(self.port, "/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(snap["verdict"], "unknown")


if __name__ == "__main__":
    unittest.main()
