"""act/radar_gmail — IMAP connection classification and the ``--check`` CLI
(§14 / §14bis). imaplib is stubbed at the class level: no socket is opened.

Pinned (P3 mutation net — none of these paths had a killing test):
- ``connect_ex``: no address -> no_address; IMAP4_SSL() OSError ->
  connect_failed; LOGIN rejected -> auth_failed; LOGIN network error ->
  connect_failed; SELECT failure -> connect_failed; success -> (conn, None)
  with a READONLY INBOX select; ``connect`` returns just the connection;
- ``_check`` command mode: an existing executable is ok (exit 0), a missing
  one is not (exit 1), an unparseable command string is not;
- ``_check`` IMAP mode: no password / no address -> exit 1 with a hint,
  login failure -> exit 1 + JSON error, success -> exit 0 + JSON ok;
- ``_main``: --check dispatches to _check; a plain run prints the card count.
"""
import imaplib
import io
import json
import shlex
import sys
import unittest
from contextlib import redirect_stdout
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import radar_gmail
from act.lib import config


class _FakeImap:
    """imaplib.IMAP4_SSL stand-in: scripted login/select outcomes."""
    instances: list = []

    def __init__(self, host, login_exc=None, select_exc=None):
        self.host = host
        self.login_exc = login_exc
        self.select_exc = select_exc
        self.calls = []
        _FakeImap.instances.append(self)

    def login(self, user, password):
        self.calls.append(("login", user))
        if self.login_exc is not None:
            raise self.login_exc

    def select(self, mailbox, readonly=False):
        self.calls.append(("select", mailbox, readonly))
        if self.select_exc is not None:
            raise self.select_exc

    def logout(self):
        self.calls.append(("logout",))


def _imap_factory(login_exc=None, select_exc=None):
    return lambda host: _FakeImap(host, login_exc=login_exc, select_exc=select_exc)


class ConnectExTestCase(unittest.TestCase):
    def setUp(self):
        _FakeImap.instances.clear()
        self.cfg = config.Config()
        self.cfg.gmail_address = "me@example.com"

    def test_no_address(self):
        self.cfg.gmail_address = ""
        self.assertEqual(radar_gmail.connect_ex(self.cfg, "pw"), (None, "no_address"))

    def test_ssl_connect_failure(self):
        def boom(host):
            raise OSError("dns")
        with mock.patch.object(imaplib, "IMAP4_SSL", boom):
            self.assertEqual(radar_gmail.connect_ex(self.cfg, "pw"), (None, "connect_failed"))

    def test_login_rejected_is_auth_failed(self):
        with mock.patch.object(imaplib, "IMAP4_SSL",
                               _imap_factory(login_exc=imaplib.IMAP4.error("bad creds"))):
            self.assertEqual(radar_gmail.connect_ex(self.cfg, "pw"), (None, "auth_failed"))

    def test_login_network_error_is_connect_failed(self):
        with mock.patch.object(imaplib, "IMAP4_SSL",
                               _imap_factory(login_exc=OSError("reset"))):
            self.assertEqual(radar_gmail.connect_ex(self.cfg, "pw"), (None, "connect_failed"))

    def test_select_failure_is_connect_failed(self):
        with mock.patch.object(imaplib, "IMAP4_SSL",
                               _imap_factory(select_exc=imaplib.IMAP4.error("no inbox"))):
            self.assertEqual(radar_gmail.connect_ex(self.cfg, "pw"), (None, "connect_failed"))
        with mock.patch.object(imaplib, "IMAP4_SSL",
                               _imap_factory(select_exc=OSError("gone"))):
            self.assertEqual(radar_gmail.connect_ex(self.cfg, "pw"), (None, "connect_failed"))

    def test_success_selects_inbox_readonly(self):
        with mock.patch.object(imaplib, "IMAP4_SSL", _imap_factory()):
            conn, reason = radar_gmail.connect_ex(self.cfg, "pw")
            self.assertIsNone(reason)
            self.assertIs(conn, _FakeImap.instances[-1])
            self.assertEqual(conn.host, radar_gmail.IMAP_HOST)
            self.assertEqual(conn.calls, [("login", "me@example.com"),
                                          ("select", "INBOX", True)])
            self.assertIs(radar_gmail.connect(self.cfg, "pw"), _FakeImap.instances[-1])


class CheckTestCase(unittest.TestCase):
    def setUp(self):
        _FakeImap.instances.clear()
        self.cfg = config.Config()

    def _check(self, cfg):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = radar_gmail._check(cfg)
        return rc, buf.getvalue()

    def test_command_mode_ok_when_executable_resolves(self):
        self.cfg.gmail_fetch_command = f"{shlex.quote(sys.executable)} -c pass"
        rc, out = self._check(self.cfg)
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out), {"ok": True, "mode": "command",
                                           "command": self.cfg.gmail_fetch_command})

    def test_command_mode_missing_executable_fails(self):
        self.cfg.gmail_fetch_command = "/definitely/not/here --flag"
        rc, out = self._check(self.cfg)
        self.assertEqual(rc, 1)
        self.assertFalse(json.loads(out)["ok"])

    def test_command_mode_unparseable_or_blank_argv_fails(self):
        self.cfg.gmail_fetch_command = "unterminated 'quote"
        rc, _out = self._check(self.cfg)
        self.assertEqual(rc, 1)
        self.assertEqual(radar_gmail._command_exe("   "), "")     # empty argv
        self.assertEqual(radar_gmail._command_exe("unterminated 'q"), "")
        self.assertFalse(radar_gmail._exe_resolves(""))

    def test_command_mode_resolves_bare_names_on_path(self):
        self.assertTrue(radar_gmail._exe_resolves("python3") or
                        radar_gmail._exe_resolves(sys.executable))

    def test_imap_mode_precheck_messages(self):
        with mock.patch.object(radar_gmail, "get_app_password", return_value=None):
            rc, out = self._check(self.cfg)
        self.assertEqual(rc, 1)
        self.assertIn("no app password at", out)
        self.assertIn(radar_gmail.DEFAULT_APP_PASSWORD_PATH, out)
        self.cfg.gmail_app_password_path = "~/custom/pw.txt"
        with mock.patch.object(radar_gmail, "get_app_password", return_value=None):
            _rc, out = self._check(self.cfg)
        self.assertIn("~/custom/pw.txt", out)
        self.cfg.gmail_address = ""
        with mock.patch.object(radar_gmail, "get_app_password", return_value="pw"):
            rc, out = self._check(self.cfg)
        self.assertEqual(rc, 1)
        self.assertIn("no gmail address", out)

    def test_imap_mode_login_failure_and_success(self):
        self.cfg.gmail_address = "me@example.com"
        with mock.patch.object(radar_gmail, "get_app_password", return_value="pw"), \
                mock.patch.object(radar_gmail, "connect_ex", return_value=(None, "auth_failed")):
            rc, out = self._check(self.cfg)
        self.assertEqual(rc, 1)
        self.assertEqual(json.loads(out), {"ok": False, "address": "me@example.com",
                                           "error": "auth_failed"})
        with mock.patch.object(radar_gmail, "get_app_password", return_value="pw"), \
                mock.patch.object(radar_gmail, "connect_ex", return_value=(None, None)):
            _rc, out = self._check(self.cfg)
        self.assertEqual(json.loads(out)["error"], "login/select failed")
        conn = _FakeImap("h")
        with mock.patch.object(radar_gmail, "get_app_password", return_value="pw"), \
                mock.patch.object(radar_gmail, "connect_ex", return_value=(conn, None)):
            rc, out = self._check(self.cfg)
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out), {"ok": True, "address": "me@example.com"})
        self.assertEqual(conn.calls, [("logout",)])


class MainTestCase(unittest.TestCase):
    def test_check_flag_dispatches(self):
        with mock.patch.object(radar_gmail, "_check", return_value=3) as check, \
                mock.patch.object(config, "load_config", return_value=config.Config()):
            self.assertEqual(radar_gmail._main(["--check"]), 3)
        check.assert_called_once()

    def test_plain_run_prints_count(self):
        buf = io.StringIO()
        with mock.patch.object(radar_gmail, "scan", return_value=2), \
                mock.patch.object(config, "load_config", return_value=config.Config()), \
                redirect_stdout(buf):
            self.assertEqual(radar_gmail._main(["--once"]), 0)
        self.assertEqual(buf.getvalue().strip(), "gmail radar: 2 new card(s)")


if __name__ == "__main__":
    unittest.main()
