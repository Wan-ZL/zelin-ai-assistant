"""doctor `board server` row (CONTRACT §54; §55 resident set) — behavior pins.

The board server is a launchd agent since v0.48.18 (systemd unit on Linux).
`launchctl list` only proves the process was spawned; whether it BOUND the port
is what the web board and the shell app need — so the row is driven by a
loopback `GET /api/health` probe (injected here; the real one is switched off
in the suite by AIASSISTANT_HTTP_PROBE=0 so no test ever reads a developer's
live server).

  - reachable + hosted (label loaded / unit registered) → OK;
  - reachable but NOT hosted (a shell-spawned or hand-run server — the
    pre-v0.48.18 shape, dies with its parent) → WARN `board_server_down`,
    fix = the installer;
  - unreachable + hosted → FAIL `board_server_down` (crash loop / port fight),
    fix names the kickstart command the shell's dialog prints;
  - unreachable + not hosted → WARN, fix = the installer;
  - probe unavailable / windows → no row;
  - the server label is a RESIDENT label: a crash-looping server is FAIL in
    the launchd row (rolls an auto-deploy back, §56); the doctor no longer
    reports the label as an orphan (a template exists).
"""
import json
import os
import shutil
import socket
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env before any act.* import

from act import doctor
from act.lib import board_server, config, failures, install_report

REPO = Path(__file__).resolve().parents[1]
SERVER = "com.zelin.aiassistant.server"
PORT = config.DEFAULT_SERVER_PORT


def _probes(*, health, launchctl="", platform_units=None):
    return doctor.Probes(
        launchctl_list=lambda: launchctl,
        board_health=lambda port: dict(health, port=port),
        systemd_units=platform_units,
    )


def _row(results):
    rows = [r for r in results if r.name == "board server"]
    return rows[0] if rows else None


UP = {"state": "ok", "status": 200, "text": "HTTP 200"}
DOWN = {"state": "down", "status": None, "text": "Connection refused"}
OFF = {"state": "unavailable", "status": None, "text": "probe disabled"}


class BoardServerRowDarwinTestCase(unittest.TestCase):
    def setUp(self):
        p = mock.patch("sys.platform", "darwin")
        p.start()
        self.addCleanup(p.stop)

    def _check(self, health, launchctl):
        res = doctor._check_board_server(_probes(health=health, launchctl=launchctl))
        return _row(res if isinstance(res, list) else [res])

    def test_reachable_and_launchd_hosted_is_ok(self):
        r = self._check(UP, "4242\t0\t%s\n" % SERVER)
        self.assertEqual(r.status, doctor.OK)
        self.assertIn("launchd-hosted", r.detail)
        self.assertIn(str(PORT), r.detail)

    def test_reachable_but_not_hosted_warns_toward_the_installer(self):
        # the pre-v0.48.18 shape: the shell's child answers, launchd knows nothing
        r = self._check(UP, "4242\t0\tcom.zelin.aiassistant.actd\n")
        self.assertEqual(r.status, doctor.WARN)
        self.assertIn("not launchd-hosted", r.detail)
        self.assertIn("install.sh", r.fix)
        self.assertEqual(r.failure_id, "board_server_down")

    def test_hosted_but_unreachable_is_fail_with_kickstart(self):
        r = self._check(DOWN, "4242\t0\t%s\n" % SERVER)
        self.assertEqual(r.status, doctor.FAIL)
        self.assertEqual(r.failure_id, "board_server_down")
        self.assertIn(SERVER, r.detail)
        self.assertIn("Connection refused", r.detail)
        self.assertIn("launchctl kickstart -k gui/$(id -u)/%s" % SERVER, r.fix)
        self.assertIn("server.launchd.log", r.fix)

    def test_loaded_mid_throttle_without_pid_still_counts_as_hosted(self):
        # KeepAlive respawn window: pid "-" but the label IS loaded → FAIL, not
        # the "install it" WARN
        r = self._check(DOWN, "-\t75\t%s\n" % SERVER)
        self.assertEqual(r.status, doctor.FAIL)

    def test_unreachable_and_not_hosted_warns_toward_the_installer(self):
        r = self._check(DOWN, "4242\t0\tcom.zelin.aiassistant.actd\n")
        self.assertEqual(r.status, doctor.WARN)
        self.assertIn("not launchd-hosted", r.detail)
        self.assertIn("bash install.sh", r.fix)
        self.assertIn(SERVER, r.fix)

    def test_probe_unavailable_emits_no_row(self):
        self.assertIsNone(self._check(OFF, "4242\t0\t%s\n" % SERVER))

    def test_port_comes_from_config(self):
        seen = {}

        def health(port):
            seen["port"] = port
            return dict(UP)
        probes = doctor.Probes(launchctl_list=lambda: "1\t0\t%s\n" % SERVER,
                               board_health=health)
        with mock.patch.object(config, "load_config",
                               return_value=config.Config(server_port=47999)):
            r = _row([doctor._check_board_server(probes)])
        self.assertEqual(seen["port"], 47999)
        self.assertIn("47999", r.detail)

    def test_row_runs_inside_run_checks_fast(self):
        # the row is part of the fast set (auto-deploy's rollback judgement)
        probes = doctor.Probes(
            launchctl_list=lambda: "4242\t0\t%s\n" % SERVER,
            board_health=lambda port: dict(DOWN),
            launchd_labels=[SERVER], installed_agent_labels=lambda: [],
            installed_plist_text=lambda label: None, launchd_log_tail=lambda s: "",
            heartbeat_read=lambda: None, daemon_path_env=lambda: None,
            login_shell_claude=lambda: None, which=lambda n: None,
            launchd_claude_probe=lambda c, w: {"state": "unavailable", "rc": None, "text": ""},
            claude_code_settings=lambda: {},
        )
        results = doctor.run_checks(probes, fast=True)
        r = _row(results)
        self.assertIsNotNone(r, [x.name for x in results])
        self.assertEqual(r.status, doctor.FAIL)

    def test_crash_looping_server_label_is_a_resident_fail(self):
        # §55: KeepAlive template → RESIDENT_LABELS → "loaded, no pid, exit 75" is FAIL
        self.assertIn(SERVER, doctor.RESIDENT_LABELS)
        probes = doctor.Probes(launchctl_list=lambda: "-\t75\t%s\n" % SERVER,
                               launchd_labels=[SERVER], launchd_log_tail=lambda s: "")
        rows = doctor._check_launchd(probes)
        r = [x for x in rows if x.name == "server"][0]
        self.assertEqual(r.status, doctor.FAIL)
        self.assertIn("crash loop", r.detail)

    def test_server_label_is_not_an_orphan(self):
        # a template exists in act/launchd → the label the owner hand-made on
        # 2026-09-02 stops being flagged once this version is deployed
        templates = sorted(p.stem for p in (REPO / "act" / "launchd").glob("*.plist"))
        self.assertIn(SERVER, templates)
        probes = doctor.Probes(launchctl_list=lambda: "4242\t0\t%s\n" % SERVER,
                               installed_agent_labels=lambda: [SERVER],
                               launchd_labels=templates)
        r = doctor._check_launchd_orphans(probes)
        self.assertEqual(r.status, doctor.OK, r.detail)


class BoardServerRowLinuxTestCase(unittest.TestCase):
    def setUp(self):
        p = mock.patch("sys.platform", "linux")
        p.start()
        self.addCleanup(p.stop)

    def _check(self, health, listing):
        return _row([doctor._check_board_server(_probes(health=health, launchctl=listing))])

    def test_registered_unit_and_reachable_is_ok(self):
        r = self._check(UP, "zelin-server.service loaded active running Zelin\n")
        self.assertEqual(r.status, doctor.OK)
        self.assertIn("systemd-hosted", r.detail)

    def test_failed_unit_bullet_still_counts_as_hosted(self):
        r = self._check(DOWN, "● zelin-server.service loaded failed failed Zelin\n")
        self.assertEqual(r.status, doctor.FAIL)
        self.assertIn("systemctl --user restart zelin-server.service", r.fix)

    def test_not_registered_points_at_the_linux_installer(self):
        r = self._check(DOWN, "zelin-actd.service loaded active running Zelin\n")
        self.assertEqual(r.status, doctor.WARN)
        self.assertIn("install-linux.sh", r.fix)
        self.assertIn("zelin-server.service", r.fix)

    def test_server_unit_is_a_systemd_resident(self):
        self.assertIn("zelin-server.service", doctor.SYSTEMD_RESIDENT)


class BoardServerRowWindowsTestCase(unittest.TestCase):
    def test_windows_has_no_row(self):
        with mock.patch("sys.platform", "win32"):
            self.assertEqual(doctor._check_board_server(_probes(health=UP)), [])


class RealLoopbackProbeTestCase(unittest.TestCase):
    """The default probe against a throwaway loopback HTTP server owned by the
    test (never the developer's live board server: the port is ours)."""

    def _serve(self, status):
        class H(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                body = b"{}"
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass
        httpd = HTTPServer(("127.0.0.1", 0), H)
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        return httpd.server_address[1]

    def test_disabled_by_env_reports_unavailable_without_connecting(self):
        with mock.patch.dict(os.environ, {"AIASSISTANT_HTTP_PROBE": "0"}):
            self.assertEqual(board_server.health_probe(1)["state"], "unavailable")

    def test_2xx_is_ok(self):
        port = self._serve(200)
        with mock.patch.dict(os.environ, {"AIASSISTANT_HTTP_PROBE": "1"}):
            v = board_server.health_probe(port)
        self.assertEqual((v["state"], v["status"]), ("ok", 200))

    def test_5xx_is_down_with_the_status(self):
        port = self._serve(503)
        with mock.patch.dict(os.environ, {"AIASSISTANT_HTTP_PROBE": "1"}):
            v = board_server.health_probe(port)
        self.assertEqual((v["state"], v["status"]), ("down", 503))

    def test_closed_port_is_down(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()   # nothing listens here now
        with mock.patch.dict(os.environ, {"AIASSISTANT_HTTP_PROBE": "1"}):
            v = board_server.health_probe(port)
        self.assertEqual(v["state"], "down")
        self.assertIsNone(v["status"])
        self.assertTrue(v["text"])


class UiBuildRowTestCase(unittest.TestCase):
    """§56.5 `ui` step visibility: install_report `ui=skipped_tcc` → WARN with the
    Full Disk Access fix; `ui=fail` → WARN toward the build log; else no row."""

    def _with_report(self, steps):
        tmp = Path(tempfile.mkdtemp(prefix="ui-build-row-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        path = tmp / "install_report.json"
        if steps is not None:
            path.write_text(json.dumps({"version": "0.48.18", "steps": steps}), encoding="utf-8")
        return mock.patch.object(install_report, "REPORT_PATH", path)

    def _row(self, steps):
        with self._with_report(steps), mock.patch("sys.platform", "darwin"):
            res = doctor._check_ui_build(doctor.Probes())
        return res if not isinstance(res, list) else (res[0] if res else None)

    def test_skipped_tcc_warns_with_the_fda_fix(self):
        r = self._row([{"name": "ui", "status": "skipped_tcc",
                        "detail": "web skipped_tcc (npm ci exit 1: EPERM under launchd); shell ok"}])
        self.assertEqual(r.status, doctor.WARN)
        self.assertEqual(r.failure_id, "ui_build_tcc_blocked")
        self.assertIn("EPERM", r.detail)
        self.assertIn("Full Disk Access", r.fix)
        self.assertIn("install.sh", r.fix)

    def test_fail_warns_toward_the_build_log(self):
        r = self._row([{"name": "ui", "status": "fail", "detail": "web fail (npm run build exit 2)"}])
        self.assertEqual(r.status, doctor.WARN)
        self.assertEqual(r.failure_id, "")
        self.assertIn("ui-build.log", r.fix)

    def test_ok_skipped_or_absent_emit_no_row(self):
        self.assertIsNone(self._row([{"name": "ui", "status": "ok", "detail": "web ok"}]))
        self.assertIsNone(self._row([{"name": "ui", "status": "skipped", "detail": "no node"}]))
        self.assertIsNone(self._row([{"name": "cron", "status": "ok"}]))
        self.assertIsNone(self._row(None))

    def test_torn_report_emits_no_row(self):
        tmp = Path(tempfile.mkdtemp(prefix="ui-build-torn-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        path = tmp / "install_report.json"
        path.write_text("{not json", encoding="utf-8")
        with mock.patch.object(install_report, "REPORT_PATH", path):
            self.assertEqual(doctor._check_ui_build(doctor.Probes()), [])

    def test_last_ui_step_wins(self):
        r = self._row([{"name": "ui", "status": "skipped_tcc", "detail": "old"},
                       {"name": "ui", "status": "ok", "detail": "new"}])
        self.assertIsNone(r)


class FailureCatalogTestCase(unittest.TestCase):
    def test_board_server_down_is_catalogued_in_both_languages(self):
        entry = failures.describe("board_server_down")
        self.assertTrue(entry)
        for key in ("plain_zh", "plain_en"):
            self.assertIn(SERVER, entry[key])
        self.assertEqual(failures.action_id("board_server_down"), "open_deps")

    def test_ui_build_tcc_blocked_is_catalogued(self):
        entry = failures.describe("ui_build_tcc_blocked")
        self.assertTrue(entry)
        for key in ("plain_zh", "plain_en"):
            self.assertIn("install.sh", entry[key])


if __name__ == "__main__":
    unittest.main()
