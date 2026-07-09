"""act/lib/health.py — radar health file (CONTRACT §E, v0.10).

update_radar_health must (a) write valid JSON atomically, (b) bump
``last_attempt`` on every call, (c) on ok=True set ``last_ok`` and clear
``skip_reason``, (d) on ok=False record ``skip_reason`` while PRESERVING the
previous ``last_ok``, and (e) never raise — even over a corrupt old file.

Everything lives under the sandbox AIASSISTANT_HOME (tests/__init__.py).
"""
import datetime as _dt
import json
import unittest

from tests import TMP_HOME  # noqa: F401 - ensures the sandbox env is set first

from act.lib import health


def _read() -> dict:
    return json.loads(health.HEALTH_PATH.read_text(encoding="utf-8"))


def _parse_iso(ts: str) -> _dt.datetime:
    # the file uses second-precision "...Z" (what the Swift reader parses)
    return _dt.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")


class RadarHealthTestCase(unittest.TestCase):
    def setUp(self):
        if health.HEALTH_PATH.exists():
            health.HEALTH_PATH.unlink()

    def test_ok_writes_valid_json_with_last_ok_and_no_skip_reason(self):
        health.update_radar_health("gmail", ok=True)
        data = _read()
        entry = data["gmail"]
        self.assertIsNone(entry["skip_reason"])
        # both timestamps set, ISO "…Z" at second precision (parseable by the app)
        _parse_iso(entry["last_attempt"])
        _parse_iso(entry["last_ok"])
        # atomic write leaves no .tmp behind
        self.assertFalse(health.HEALTH_PATH.with_suffix(".json.tmp").exists())

    def test_failure_records_reason_and_preserves_previous_last_ok(self):
        health.update_radar_health("slack", ok=True)
        last_ok = _read()["slack"]["last_ok"]

        health.update_radar_health("slack", ok=False,
                                    skip_reason="no_credentials")
        entry = _read()["slack"]
        self.assertEqual(entry["skip_reason"], "no_credentials")
        self.assertEqual(entry["last_ok"], last_ok)  # success history preserved

    def test_failure_before_any_success_has_null_last_ok(self):
        health.update_radar_health("gmail", ok=False, skip_reason="disabled")
        entry = _read()["gmail"]
        self.assertIsNone(entry["last_ok"])
        self.assertEqual(entry["skip_reason"], "disabled")
        _parse_iso(entry["last_attempt"])

    def test_corrupt_old_file_never_raises_and_is_replaced(self):
        health.HEALTH_PATH.parent.mkdir(parents=True, exist_ok=True)
        health.HEALTH_PATH.write_text("{{{ not json at all", encoding="utf-8")
        # must not raise (health must never break a radar pass)
        health.update_radar_health("gmail", ok=False, skip_reason="connect_failed")
        data = _read()  # file is valid JSON again (started fresh from {})
        self.assertEqual(data["gmail"]["skip_reason"], "connect_failed")
        self.assertIsNone(data["gmail"]["last_ok"])

    def test_sources_are_independent_entries(self):
        health.update_radar_health("gmail", ok=True)
        health.update_radar_health("slack", ok=False, skip_reason="connect_failed")
        data = _read()
        self.assertIsNone(data["gmail"]["skip_reason"])
        self.assertEqual(data["slack"]["skip_reason"], "connect_failed")
        self.assertIsNotNone(data["gmail"]["last_ok"])


if __name__ == "__main__":
    unittest.main()
