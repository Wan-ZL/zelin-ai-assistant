"""act/lib/secrets.py — CONTRACT §19 resolution order + file modes.

Everything lives under the sandbox AIASSISTANT_HOME set in tests/__init__.py;
no real key file (~/Desktop/Keys, ~/.config) is ever read or written.
"""
import contextlib
import io
import os
import stat
import unittest
from pathlib import Path

from tests import TMP_HOME

from act.lib import analytics, config, secrets
from act import radar_gmail, radar_slack


class SecretsTestCase(unittest.TestCase):
    def setUp(self):
        self.home = Path(TMP_HOME)
        # scratch files standing in for explicit-config + legacy paths
        self.keys = self.home / "fake-keys"
        self.keys.mkdir(parents=True, exist_ok=True)
        if secrets.SECRETS_DIR.exists():
            for p in secrets.SECRETS_DIR.iterdir():
                p.unlink()

    def _path(self, name: str, content: str) -> Path:
        p = self.keys / name
        p.write_text(content, encoding="utf-8")
        return p

    # -- sandbox sanity ------------------------------------------------------ #
    def test_paths_are_sandboxed(self):
        self.assertEqual(config.HOME, self.home)
        self.assertEqual(secrets.SECRETS_DIR, self.home / "config" / "secrets")

    # -- write/read ---------------------------------------------------------- #
    def test_write_secret_enforces_modes_and_read_roundtrip(self):
        path = secrets.write_secret(secrets.SLACK_TOKEN_FILE, "  xoxp-123  \n")
        self.assertEqual(path.read_text(encoding="utf-8"), "xoxp-123\n")
        if os.name != "nt":  # NTFS has no POSIX mode bits; chmod is a no-op there
            self.assertEqual(stat.S_IMODE(os.stat(secrets.SECRETS_DIR).st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertEqual(secrets.read_secret(secrets.SLACK_TOKEN_FILE), "xoxp-123")

    def test_read_secret_missing_or_empty_is_none(self):
        self.assertIsNone(secrets.read_secret("no-such-file.txt"))
        secrets.SECRETS_DIR.mkdir(parents=True, exist_ok=True)
        (secrets.SECRETS_DIR / "empty.txt").write_text("  \n", encoding="utf-8")
        self.assertIsNone(secrets.read_secret("empty.txt"))

    # -- resolution order (§19): secrets -> explicit -> legacy --------------- #
    def test_resolution_order(self):
        name = secrets.SLACK_TOKEN_FILE
        legacy = self._path("legacy.txt", "legacy-tok\n")
        explicit = self._path("explicit.txt", "explicit-tok\n")

        # legacy only
        self.assertEqual(
            secrets.resolve_credential(name, None, legacy), "legacy-tok")
        # explicit beats legacy
        self.assertEqual(
            secrets.resolve_credential(name, explicit, legacy), "explicit-tok")
        # secrets beats both
        secrets.write_secret(name, "secret-tok")
        self.assertEqual(
            secrets.resolve_credential(name, explicit, legacy), "secret-tok")
        # EMPTY secrets file counts as absent -> falls through to explicit
        (secrets.SECRETS_DIR / name).write_text("", encoding="utf-8")
        self.assertEqual(
            secrets.resolve_credential(name, explicit, legacy), "explicit-tok")
        # missing explicit path falls through to legacy
        (secrets.SECRETS_DIR / name).unlink()
        self.assertEqual(
            secrets.resolve_credential(name, self.keys / "nope.txt", legacy),
            "legacy-tok")
        # nothing anywhere -> None
        self.assertIsNone(
            secrets.resolve_credential(name, self.keys / "nope.txt",
                                       self.keys / "nope2.txt"))

    # -- callers are wired through secrets ----------------------------------- #
    def test_slack_get_token_prefers_secrets_over_config_path(self):
        explicit = self._path("slack-explicit.txt", "xoxp-from-explicit\n")
        cfg = config.Config()
        cfg.slack_token_path = str(explicit)
        secrets.write_secret(secrets.SLACK_TOKEN_FILE, "xoxp-from-secrets")
        self.assertEqual(radar_slack.get_token(cfg), "xoxp-from-secrets")
        (secrets.SECRETS_DIR / secrets.SLACK_TOKEN_FILE).unlink()
        self.assertEqual(radar_slack.get_token(cfg), "xoxp-from-explicit")

    def test_gmail_get_app_password_prefers_secrets_over_config_path(self):
        explicit = self._path("gmail-explicit.txt", "abcdefghijklmnop\n")
        cfg = config.Config()
        cfg.gmail_app_password_path = str(explicit)
        secrets.write_secret(secrets.GMAIL_APP_PASSWORD_FILE, "ponmlkjihgfedcba")
        self.assertEqual(radar_gmail.get_app_password(cfg), "ponmlkjihgfedcba")
        (secrets.SECRETS_DIR / secrets.GMAIL_APP_PASSWORD_FILE).unlink()
        self.assertEqual(radar_gmail.get_app_password(cfg), "abcdefghijklmnop")


class LegacyDeprecationWarnTestCase(unittest.TestCase):
    """§19 legacy tier is warn-only deprecated: one stderr line + one analytics
    event per credential per process; the credential VALUE never appears in
    either; resolution behavior is unchanged and never raises."""

    def setUp(self):
        self.home = Path(TMP_HOME)
        self.keys = self.home / "fake-keys"
        self.keys.mkdir(parents=True, exist_ok=True)
        if secrets.SECRETS_DIR.exists():
            for p in secrets.SECRETS_DIR.iterdir():
                p.unlink()
        secrets._warned_legacy.clear()
        self.addCleanup(secrets._warned_legacy.clear)

    def _resolve_capturing_stderr(self, *args):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            val = secrets.resolve_credential(*args)
        return val, err.getvalue()

    def test_legacy_hit_warns_once_without_leaking_the_value(self):
        legacy = self.keys / "legacy.txt"
        legacy.write_text("legacy-tok\n", encoding="utf-8")
        name = secrets.SLACK_TOKEN_FILE

        val, err = self._resolve_capturing_stderr(name, None, legacy)
        self.assertEqual(val, "legacy-tok")     # resolution unchanged
        self.assertEqual(err.count("\n"), 1)    # exactly one line
        self.assertIn("DEPRECATED", err)
        self.assertIn(name, err)
        self.assertNotIn("legacy-tok", err)     # §19: value never logged

        # second resolution of the SAME name is silent (radars poll in a loop)
        val, err = self._resolve_capturing_stderr(name, None, legacy)
        self.assertEqual(val, "legacy-tok")
        self.assertEqual(err, "")

    @staticmethod
    def _legacy_event_lines():
        try:
            text = analytics.EVENTS_PATH.read_text(encoding="utf-8")
        except OSError:
            return []
        return [ln for ln in text.splitlines() if "legacy_secret_path" in ln]

    def test_legacy_hit_logs_analytics_event_without_the_value(self):
        legacy = self.keys / "legacy-gmail.txt"
        legacy.write_text("hunter2secret\n", encoding="utf-8")

        before = self._legacy_event_lines()
        self._resolve_capturing_stderr(secrets.GMAIL_APP_PASSWORD_FILE, None, legacy)
        new = self._legacy_event_lines()[len(before):]

        self.assertEqual(len(new), 1)
        self.assertIn(secrets.GMAIL_APP_PASSWORD_FILE, new[0])
        self.assertNotIn("hunter2secret",
                         analytics.EVENTS_PATH.read_text(encoding="utf-8"))

    def test_secrets_and_explicit_tiers_do_not_warn(self):
        name = secrets.SLACK_TOKEN_FILE
        legacy = self.keys / "legacy.txt"
        legacy.write_text("legacy-tok\n", encoding="utf-8")
        explicit = self.keys / "explicit.txt"
        explicit.write_text("explicit-tok\n", encoding="utf-8")

        val, err = self._resolve_capturing_stderr(name, explicit, legacy)
        self.assertEqual(val, "explicit-tok")
        self.assertEqual(err, "")

        secrets.write_secret(name, "secret-tok")
        val, err = self._resolve_capturing_stderr(name, explicit, legacy)
        self.assertEqual(val, "secret-tok")
        self.assertEqual(err, "")


if __name__ == "__main__":
    unittest.main()
