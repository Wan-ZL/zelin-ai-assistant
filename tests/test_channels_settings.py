"""Slack/Gmail in-app setup — config overrides + gmail connect classification.

§15.3 add-only override keys written by the Settings Slack/Gmail sections:
``owner_slack_user_id`` (str), ``slack_channels`` (list), ``watch_people``
(list). Plus act/radar_gmail.connect_ex's failure classification
(no_address / auth_failed / connect_failed) that powers the Gmail status row.

Sandboxed under the tests/__init__.py AIASSISTANT_HOME.
"""
import imaplib
import json
import unittest
from unittest import mock

from act import radar_gmail
from act.lib import config


class ChannelsOverridesTestCase(unittest.TestCase):
    def setUp(self):
        self._cleanup()

    def tearDown(self):
        self._cleanup()

    @staticmethod
    def _cleanup():
        for p in (config.CONFIG_PATH, config.SETTINGS_OVERRIDES_PATH):
            if p.exists():
                p.unlink()

    def _write_overrides(self, data):
        config.SETTINGS_OVERRIDES_PATH.parent.mkdir(parents=True, exist_ok=True)
        config.SETTINGS_OVERRIDES_PATH.write_text(json.dumps(data),
                                                  encoding="utf-8")

    def test_owner_slack_user_id_override(self):
        config.CONFIG_PATH.write_text(
            "owner:\n  slack_user_id: U_FROM_YAML\n", encoding="utf-8")
        self._write_overrides({"owner_slack_user_id": "U_FROM_APP"})
        self.assertEqual(config.load_config().owner_slack_user_id, "U_FROM_APP")

    def test_slack_channels_override_beats_yaml(self):
        config.CONFIG_PATH.write_text(
            "sources:\n  slack_channels:\n    - id: C_YAML\n      name: old\n",
            encoding="utf-8")
        self._write_overrides({"slack_channels": [
            {"id": "C1", "name": "team"},
            "C2",                                # bare id string form
            {"name": "no-id-dropped"},           # malformed → skipped
            {"id": "C3"},                        # name optional
            42,                                  # junk → skipped
        ]})
        cfg = config.load_config()
        self.assertEqual(cfg.slack_channels,
                         [{"id": "C1", "name": "team"}, "C2", {"id": "C3"}])

    def test_slack_channels_empty_list_means_watch_none(self):
        config.CONFIG_PATH.write_text(
            "sources:\n  slack_channels:\n    - id: C_YAML\n", encoding="utf-8")
        self._write_overrides({"slack_channels": []})
        self.assertEqual(config.load_config().slack_channels, [])

    def test_watch_people_override(self):
        self._write_overrides({"watch_people": ["alice.a", "  bob.b  ", "",
                                                None, {"bad": 1}]})
        self.assertEqual(config.load_config().watch_people,
                         ["alice.a", "bob.b"])

    def test_dotted_sources_form(self):
        self._write_overrides({"sources.watch_people": ["c.c"],
                               "sources.slack_channels": [{"id": "C9"}]})
        cfg = config.load_config()
        self.assertEqual(cfg.watch_people, ["c.c"])
        self.assertEqual(cfg.slack_channels, [{"id": "C9"}])

    def test_wrong_type_ignored(self):
        """Non-list values for list keys must be skipped, not crash."""
        config.CONFIG_PATH.write_text(
            "sources:\n  watch_people:\n    - keep.me\n", encoding="utf-8")
        self._write_overrides({"slack_channels": "not-a-list",
                               "watch_people": 7})
        cfg = config.load_config()
        self.assertEqual(cfg.watch_people, ["keep.me"])


class GmailConnectExTestCase(unittest.TestCase):
    def _cfg(self, address="me@gmail.com"):
        cfg = config.Config()
        cfg.gmail_address = address
        return cfg

    def test_no_address(self):
        conn, reason = radar_gmail.connect_ex(self._cfg(address=None), "pw")
        self.assertIsNone(conn)
        self.assertEqual(reason, "no_address")

    def test_constructor_failure_is_connect_failed(self):
        with mock.patch.object(radar_gmail.imaplib, "IMAP4_SSL",
                               side_effect=OSError("no route")):
            conn, reason = radar_gmail.connect_ex(self._cfg(), "pw")
        self.assertIsNone(conn)
        self.assertEqual(reason, "connect_failed")

    def test_login_rejection_is_auth_failed(self):
        fake = mock.Mock()
        fake.login.side_effect = imaplib.IMAP4.error(
            b"[AUTHENTICATIONFAILED] Invalid credentials (Failure)")
        with mock.patch.object(radar_gmail.imaplib, "IMAP4_SSL",
                               return_value=fake):
            conn, reason = radar_gmail.connect_ex(self._cfg(), "pw")
        self.assertIsNone(conn)
        self.assertEqual(reason, "auth_failed")

    def test_select_failure_is_connect_failed(self):
        fake = mock.Mock()
        fake.select.side_effect = imaplib.IMAP4.error("select broke")
        with mock.patch.object(radar_gmail.imaplib, "IMAP4_SSL",
                               return_value=fake):
            conn, reason = radar_gmail.connect_ex(self._cfg(), "pw")
        self.assertIsNone(conn)
        self.assertEqual(reason, "connect_failed")

    def test_success(self):
        fake = mock.Mock()
        with mock.patch.object(radar_gmail.imaplib, "IMAP4_SSL",
                               return_value=fake):
            conn, reason = radar_gmail.connect_ex(self._cfg(), "pw")
        self.assertIs(conn, fake)
        self.assertIsNone(reason)
        fake.select.assert_called_once_with("INBOX", readonly=True)

    def test_scan_notes_auth_failed(self):
        """scan() surfaces the classified reason in radar health."""
        noted = {}

        def fake_note(source, ok, skip_reason=None):
            noted.update(source=source, ok=ok, reason=skip_reason)

        cfg = self._cfg()
        fake = mock.Mock()
        fake.login.side_effect = imaplib.IMAP4.error("bad")
        with mock.patch.object(radar_gmail.imaplib, "IMAP4_SSL",
                               return_value=fake), \
             mock.patch.object(radar_gmail, "get_app_password",
                               return_value="pw"), \
             mock.patch.object(radar_gmail.health, "update_radar_health",
                               side_effect=fake_note):
            created = radar_gmail.scan(cfg)
        self.assertEqual(created, 0)
        self.assertEqual(noted.get("reason"), "auth_failed")


if __name__ == "__main__":
    unittest.main()
