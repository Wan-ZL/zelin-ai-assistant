"""settings_overrides contract behind the app's iPhone 联动 (iMessage) section.

The Mac app's Settings section (mac/Sources/SettingsIMessage.swift) writes
exactly two keys into state/settings_overrides.json — ``phone_channel``
("imessage"|"none") and ``imessage_self_handle`` — and reads the radar's
truth back from state/radar_health.json's "imessage" entry. These tests pin
both sides of that handshake:

- the two keys stay in config's override allowlist and are applied on load
  (overrides beat config.yaml, CONTRACT §15);
- a scan under each misconfiguration writes the exact skip_reason string the
  UI maps to plain-language guidance ("disabled", "no_self_handle",
  "db_missing").
"""
import json
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 — sets AIASSISTANT_HOME before act imports

from act import radar_imessage
from act.lib import config


def _write_overrides(data: dict) -> None:
    config.SETTINGS_OVERRIDES_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.SETTINGS_OVERRIDES_PATH.write_text(
        json.dumps(data), encoding="utf-8")


class IMessageOverridesTest(unittest.TestCase):
    def tearDown(self):
        config.SETTINGS_OVERRIDES_PATH.unlink(missing_ok=True)

    def test_allowlist_keeps_imessage_keys(self):
        # the app writes these two keys — removing them from the allowlist
        # would silently break the Settings toggle
        self.assertIn("phone_channel", config._OVERRIDE_FIELDS)
        self.assertIn("imessage_self_handle", config._OVERRIDE_FIELDS)

    def test_enable_via_overrides(self):
        _write_overrides({"phone_channel": "imessage",
                          "imessage_self_handle": "+14155551234"})
        cfg = config.load_config()
        self.assertEqual(cfg.phone_channel, "imessage")
        self.assertEqual(cfg.imessage_self_handle, "+14155551234")

    def test_disable_via_overrides(self):
        _write_overrides({"phone_channel": "none",
                          "imessage_self_handle": "+14155551234"})
        cfg = config.load_config()
        self.assertEqual(cfg.phone_channel, "none")
        # handle is kept — re-enabling must not require retyping it
        self.assertEqual(cfg.imessage_self_handle, "+14155551234")


class IMessageHealthSkipReasonTest(unittest.TestCase):
    """scan() must leave the exact skip_reason codes the UI explains."""

    def tearDown(self):
        config.SETTINGS_OVERRIDES_PATH.unlink(missing_ok=True)
        (config.STATE_DIR / "radar_health.json").unlink(missing_ok=True)

    def _health(self) -> dict:
        data = json.loads(
            (config.STATE_DIR / "radar_health.json").read_text(encoding="utf-8"))
        return data["imessage"]

    def test_disabled_scan_records_disabled(self):
        _write_overrides({"phone_channel": "none"})
        self.assertEqual(radar_imessage.scan(), 0)
        self.assertEqual(self._health()["skip_reason"], "disabled")

    def test_enabled_without_handle_records_no_self_handle(self):
        _write_overrides({"phone_channel": "imessage"})
        # pin darwin: off-macOS the platform guard outranks the handle check
        # (see test_imessage_radar's platform_unsupported test)
        with mock.patch("sys.platform", "darwin"):
            self.assertEqual(radar_imessage.scan(), 0)
        self.assertEqual(self._health()["skip_reason"], "no_self_handle")

    def test_enabled_with_handle_missing_db_records_db_missing(self):
        _write_overrides({"phone_channel": "imessage",
                          "imessage_self_handle": "+14155551234"})
        missing = Path(TMP_HOME) / "no-such-chat.db"
        self.assertEqual(radar_imessage.scan(db_path=missing), 0)
        self.assertEqual(self._health()["skip_reason"], "db_missing")


if __name__ == "__main__":
    unittest.main()
