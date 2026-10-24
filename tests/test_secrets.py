"""act/lib/secrets.py — CONTRACT §19 resolution order + file modes (v2.0.0:
the Anthropic key is the only credential; §78 never falls back to the owner's
Claude Code login or the keychain).

Everything lives under the sandbox AIASSISTANT_HOME set in tests/__init__.py;
no real key file (~/Desktop/Keys, ~/.config) is ever read or written. On top
of that, every test points ``secrets.SECRETS_DIR`` at its own fresh temp dir
(:func:`_scratch_secrets_dir`, the PR #480 pattern): a run whose ``config.HOME``
resolved to the real checkout once left ``config/secrets/multi-w.txt`` behind
there — and an old setUp that emptied ``SECRETS_DIR`` before every test would
have deleted the real token files in the same run.
"""
import contextlib
import io
import os
import shutil
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME

from act.lib import config, secrets

_MODULE_SECRETS_DIR = secrets.SECRETS_DIR   # the import-time value, before any patch


def _scratch_secrets_dir(case: unittest.TestCase) -> Path:
    """A fresh, empty ``SECRETS_DIR`` for one test, removed afterwards."""
    root = Path(tempfile.mkdtemp(prefix="zai-test-secrets-"))
    case.addCleanup(shutil.rmtree, root, ignore_errors=True)
    scratch = root / "config" / "secrets"
    patcher = mock.patch.object(secrets, "SECRETS_DIR", scratch)
    patcher.start()
    case.addCleanup(patcher.stop)
    return scratch


class SecretsTestCase(unittest.TestCase):
    def setUp(self):
        self.home = Path(TMP_HOME)
        # scratch files standing in for explicit-config + legacy paths
        self.keys = Path(tempfile.mkdtemp(prefix="zai-test-keys-"))
        self.addCleanup(shutil.rmtree, self.keys, ignore_errors=True)
        self.scratch = _scratch_secrets_dir(self)

    def _path(self, name: str, content: str) -> Path:
        p = self.keys / name
        p.write_text(content, encoding="utf-8")
        return p

    # -- sandbox sanity ------------------------------------------------------ #
    def test_paths_are_sandboxed(self):
        self.assertEqual(config.HOME, self.home)
        self.assertEqual(_MODULE_SECRETS_DIR, self.home / "config" / "secrets")
        # …and the tests themselves write into a per-test scratch dir, never there
        self.assertEqual(secrets.SECRETS_DIR, self.scratch)
        self.assertFalse(str(self.scratch).startswith(str(self.home)))

    def test_the_only_credential_is_the_anthropic_key(self):
        self.assertEqual(secrets.ANTHROPIC_API_KEY_FILE, "anthropic-api-key.txt")
        self.assertEqual(secrets.LEGACY_ANTHROPIC_KEY_PATH, "~/.config/anthropic-key.txt")
        for gone in ("SLACK_TOKEN_FILE", "GMAIL_APP_PASSWORD_FILE", "SUPABASE_KEY_FILE"):
            self.assertFalse(hasattr(secrets, gone), gone)

    # -- write/read ---------------------------------------------------------- #
    def test_write_secret_enforces_modes_and_read_roundtrip(self):
        path = secrets.write_secret(secrets.ANTHROPIC_API_KEY_FILE, "  sk-ant-123  \n")
        self.assertEqual(path.read_text(encoding="utf-8"), "sk-ant-123\n")
        self.assertEqual(path.parent, self.scratch)
        if os.name != "nt":  # NTFS has no POSIX mode bits; chmod is a no-op there
            self.assertEqual(stat.S_IMODE(os.stat(secrets.SECRETS_DIR).st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertEqual(secrets.read_secret(secrets.ANTHROPIC_API_KEY_FILE), "sk-ant-123")

    def test_read_secret_missing_or_empty_is_none(self):
        self.assertIsNone(secrets.read_secret("no-such-file.txt"))
        secrets.SECRETS_DIR.mkdir(parents=True, exist_ok=True)
        (secrets.SECRETS_DIR / "empty.txt").write_text("  \n", encoding="utf-8")
        self.assertIsNone(secrets.read_secret("empty.txt"))

    # -- 多行文件 = 畸形：取首个非空行 + log 一条（§19「各一行纯 token」） --- #
    def test_multiline_secret_uses_first_line_only(self):
        secrets.SECRETS_DIR.mkdir(parents=True, exist_ok=True)
        (secrets.SECRETS_DIR / "multi.txt").write_text(
            "abcd efgh\nWARNING do not share\n", encoding="utf-8")
        secrets._warned_multiline.clear()
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            val = secrets.read_secret("multi.txt")
        self.assertEqual(val, "abcd efgh")
        self.assertNotIn("\n", val)
        self.assertIn("multi.txt", buf.getvalue())          # 有一条 log…
        self.assertNotIn("abcd efgh", buf.getvalue())       # …但绝不带凭证值
        self.assertNotIn("WARNING do not share", buf.getvalue())

    def test_multiline_warning_fires_once_per_file(self):
        secrets.SECRETS_DIR.mkdir(parents=True, exist_ok=True)
        (secrets.SECRETS_DIR / "multi2.txt").write_text("tok\nrest\n", encoding="utf-8")
        secrets._warned_multiline.clear()
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            secrets.read_secret("multi2.txt")
            secrets.read_secret("multi2.txt")
        self.assertEqual(buf.getvalue().count("multi2.txt"), 1)

    def test_resolve_credential_multiline_explicit_path(self):
        explicit = self._path("multi-explicit.txt", "tok-123\n# pasted comment\n")
        secrets._warned_multiline.clear()
        with contextlib.redirect_stderr(io.StringIO()):
            val = secrets.resolve_credential("no-such.txt", explicit, None)
        self.assertEqual(val, "tok-123")

    def test_write_secret_multiline_paste_keeps_first_line(self):
        secrets._warned_multiline.clear()
        with contextlib.redirect_stderr(io.StringIO()):
            path = secrets.write_secret("multi-w.txt", "tok-456\nrest of paste\n")
        self.assertEqual(path.read_text(encoding="utf-8"), "tok-456\n")
        # the PR #480 regression: never a byte next to the real checkout's secrets
        self.assertFalse((_MODULE_SECRETS_DIR / "multi-w.txt").exists())

    # -- resolution order (§19): secrets -> explicit -> legacy --------------- #
    def test_resolution_order(self):
        name = secrets.ANTHROPIC_API_KEY_FILE
        legacy = self._path("legacy.txt", "legacy-tok\n")
        explicit = self._path("explicit.txt", "explicit-tok\n")
        self.assertEqual(secrets.resolve_credential(name, None, legacy), "legacy-tok")
        self.assertEqual(secrets.resolve_credential(name, explicit, legacy), "explicit-tok")
        secrets.write_secret(name, "secret-tok")
        self.assertEqual(secrets.resolve_credential(name, explicit, legacy), "secret-tok")
        # EMPTY secrets file counts as absent -> falls through to explicit
        (secrets.SECRETS_DIR / name).write_text("", encoding="utf-8")
        self.assertEqual(secrets.resolve_credential(name, explicit, legacy), "explicit-tok")
        (secrets.SECRETS_DIR / name).unlink()
        self.assertEqual(secrets.resolve_credential(name, self.keys / "nope.txt", legacy),
                         "legacy-tok")
        self.assertIsNone(secrets.resolve_credential(name, self.keys / "nope.txt",
                                                     self.keys / "nope2.txt"))

    # -- the one caller: llm.runner_env via resolve_anthropic_key ---------- #
    def test_resolve_anthropic_key_order(self):
        legacy = self._path("anthropic-key.txt", "sk-ant-legacy\n")
        with mock.patch.object(secrets, "LEGACY_ANTHROPIC_KEY_PATH", str(legacy)):
            self.assertEqual(secrets.resolve_anthropic_key(), "sk-ant-legacy")
            secrets.write_secret(secrets.ANTHROPIC_API_KEY_FILE, "sk-ant-file")
            self.assertEqual(secrets.resolve_anthropic_key(), "sk-ant-file")
        with mock.patch.object(secrets, "LEGACY_ANTHROPIC_KEY_PATH", str(self.keys / "absent")):
            (secrets.SECRETS_DIR / secrets.ANTHROPIC_API_KEY_FILE).unlink()
            self.assertIsNone(secrets.resolve_anthropic_key())

    def test_never_reads_the_owners_claude_code_login(self):
        src = Path(secrets.__file__).read_text(encoding="utf-8")
        for banned in (".claude/.credentials", "security find-generic-password", "keychain("):
            self.assertNotIn(banned, src)


if __name__ == "__main__":
    unittest.main()
