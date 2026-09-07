"""``POST /api/telemetry/consent-shown`` — the web host's consent-surface marker (CONTRACT §15 / §49; D49).

Nobody wrote ``state/telemetry_consent_shown`` after the native app retired, so
``telemetry_upload.consent_surfaced()`` stayed False on a fresh install and the
hourly sync never uploaded. Pinned here:

- the four write gates apply (no token → 401, nothing written);
- ``{}`` body only — an unknown field is 400 UNKNOWN_FIELD and writes nothing;
- first call writes BOTH markers (v1 + v2) with one ISO-8601 UTC timestamp line
  and answers ``written: true``; a second call answers ``written: false`` and
  leaves the files byte-identical (write-once — the first-shown time is the
  record);
- the marker paths mirror the act-side constants byte for byte, and the file
  the route writes is exactly the one ``consent_surfaced()`` reads.
Real server on a random port (tests/test_server_common.py); stdlib client.
"""
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first
from tests.test_server_common import (assert_envelope, get_json, http_request,
                                      post_json, start_server)

from act.lib import analytics, telemetry_upload
from server import paths, telemetry_consent

ISO_LINE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\n$")
ROUTE = "/api/telemetry/consent-shown"


class ConsentShownRouteTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-consent-")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        self.home.mkdir()
        _httpd, self.port = start_server(self, self.home)
        self.v1 = paths.telemetry_consent_marker_path(self.home)
        self.v2 = paths.telemetry_consent_v2_path(self.home)

    def test_first_call_writes_both_markers_once(self):
        status, receipt = post_json(self.port, ROUTE, {})
        self.assertEqual(status, 200)
        self.assertEqual(receipt["ok"], True)
        self.assertEqual(receipt["written"], True)
        self.assertTrue(self.v1.is_file())
        self.assertTrue(self.v2.is_file())
        v1_text = self.v1.read_text(encoding="utf-8")
        self.assertRegex(v1_text, ISO_LINE)
        self.assertEqual(self.v2.read_text(encoding="utf-8"), v1_text)
        self.assertEqual(receipt["shown_at"], v1_text.strip())
        # no temp file left behind (state/ otherwise holds only the server's own token file)
        self.assertEqual(sorted(p.name for p in (self.home / "state").iterdir() if p.name != "server.token"),
                         ["telemetry_consent_shown", "telemetry_consent_shown_v2"])

    def test_second_call_is_a_no_op_and_keeps_the_first_timestamp(self):
        post_json(self.port, ROUTE, {})
        before = (self.v1.read_bytes(), self.v1.stat().st_mtime_ns, self.v2.read_bytes())
        with mock.patch.object(telemetry_consent, "_iso_now", return_value="2099-01-01T00:00:00Z"):
            status, receipt = post_json(self.port, ROUTE, {})
        self.assertEqual(status, 200)
        self.assertEqual(receipt["written"], False)
        self.assertEqual(receipt["shown_at"], before[0].decode().strip())
        self.assertEqual((self.v1.read_bytes(), self.v1.stat().st_mtime_ns, self.v2.read_bytes()), before)

    def test_only_v1_present_backfills_v2_without_touching_v1(self):
        # native v0.13–v0.17 wrote only the v1 marker
        self.v1.parent.mkdir(parents=True, exist_ok=True)
        self.v1.write_text("2026-01-02T03:04:05Z\n", encoding="utf-8")
        status, receipt = post_json(self.port, ROUTE, {})
        self.assertEqual(status, 200)
        self.assertEqual(receipt["written"], False)
        self.assertEqual(receipt["shown_at"], "2026-01-02T03:04:05Z")
        self.assertEqual(self.v1.read_text(encoding="utf-8"), "2026-01-02T03:04:05Z\n")
        self.assertTrue(self.v2.is_file())

    def test_unknown_field_is_400_and_writes_nothing(self):
        status, obj = post_json(self.port, ROUTE, {"shown": True})
        self.assertEqual(status, 400)
        assert_envelope(self, obj, "UNKNOWN_FIELD")
        self.assertEqual(obj["error"]["details"], {"fields": ["shown"]})
        self.assertFalse(self.v1.exists())
        self.assertFalse(self.v2.exists())

    def test_write_gates_apply(self):
        body = b"{}"
        status, _h, data = http_request(self.port, "POST", ROUTE, body=body,
                                        headers={"Content-Type": "application/json"})
        self.assertEqual(status, 401)
        assert_envelope(self, json.loads(data.decode("utf-8")), "UNAUTHORIZED")
        self.assertFalse(self.v1.exists())
        status, _obj = get_json(self.port, ROUTE)
        self.assertEqual(status, 404)  # write-only face; no GET


class ConsentMarkerMirrorTestCase(unittest.TestCase):
    """server/paths.py mirrors the act-side constants (server does not import act for paths)."""

    def test_marker_names_mirror_act_constants(self):
        home = Path("/tmp/zai-mirror-home")
        self.assertEqual(paths.telemetry_consent_marker_path(home),
                         home / "state" / telemetry_upload.CONSENT_MARKER_PATH.name)
        self.assertEqual(paths.telemetry_consent_v2_path(home),
                         home / "state" / analytics.CONSENT_V2_PATH.name)
        self.assertEqual(telemetry_upload.CONSENT_MARKER_PATH.parent.name, "state")
        self.assertEqual(analytics.CONSENT_V2_PATH.parent.name, "state")

    def test_route_output_satisfies_the_upload_consent_gate(self):
        # the file mark_shown writes under the sandbox home is the very file consent_surfaced() reads
        home = Path(TMP_HOME)
        marker = telemetry_upload.CONSENT_MARKER_PATH
        self.assertEqual(paths.telemetry_consent_marker_path(home), marker)
        marker.unlink(missing_ok=True)
        analytics.CONSENT_V2_PATH.unlink(missing_ok=True)
        self.addCleanup(lambda: marker.unlink(missing_ok=True))
        self.addCleanup(lambda: analytics.CONSENT_V2_PATH.unlink(missing_ok=True))
        with mock.patch.object(telemetry_upload, "_config_has_telemetry_block", return_value=False), \
                mock.patch.object(telemetry_upload, "_overrides_have_telemetry_key", return_value=False):
            self.assertFalse(telemetry_upload.consent_surfaced())
            receipt = telemetry_consent.mark_shown(home, {})
            self.assertTrue(receipt["written"])
            self.assertTrue(telemetry_upload.consent_surfaced())


if __name__ == "__main__":
    unittest.main()
