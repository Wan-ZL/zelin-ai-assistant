"""act/lib/secrets.py — CONTRACT §19 resolution order + file modes.

Everything lives under the sandbox AIASSISTANT_HOME set in tests/__init__.py;
no real key file (~/Desktop/Keys, ~/.config) is ever read or written.
"""
import os
import stat
import unittest
from pathlib import Path

from tests import TMP_HOME

from act.lib import config, secrets
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


if __name__ == "__main__":
    unittest.main()
