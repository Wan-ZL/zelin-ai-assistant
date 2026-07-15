"""Audit fix — act/doctor.py _resolve_key first-token-line semantics.

Doctor used to read the Anthropic key file whole (``read_text().strip()``),
so a multiline key file — a shape act/lib/secrets explicitly tolerates and
every runtime consumer handles via ``_first_token_line`` — produced a key
with an embedded newline, a failing live probe, and a false
"[FAIL] claude auth" on a perfectly healthy key.

_resolve_key now goes through act/lib/secrets so doctor shares the exact
resolution semantics of headless runs.
"""
import tempfile
import unittest
from pathlib import Path

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act.* import

from act import doctor
from act.lib import secrets as secrets_lib


class DoctorResolveKeyTestCase(unittest.TestCase):
    def setUp(self):
        self.key_file = secrets_lib.SECRETS_DIR / secrets_lib.ANTHROPIC_API_KEY_FILE
        self.addCleanup(lambda: self.key_file.unlink(missing_ok=True))
        self.key_file.unlink(missing_ok=True)

    def _probes(self, legacy: Path) -> doctor.Probes:
        return doctor.Probes(legacy_key_path=legacy)

    def test_multiline_secrets_file_yields_first_token_line(self):
        secrets_lib.SECRETS_DIR.mkdir(parents=True, exist_ok=True)
        self.key_file.write_text("sk-ant-test-123\n# pasted note to self\n",
                                 encoding="utf-8")
        key, source = doctor._resolve_key(
            self._probes(Path("/nonexistent/anthropic-key.txt")))
        self.assertEqual(key, "sk-ant-test-123")
        self.assertEqual(source, "config/secrets/anthropic-api-key.txt")

    def test_multiline_legacy_file_yields_first_token_line(self):
        with tempfile.TemporaryDirectory(prefix="doctor-key-") as tmp:
            legacy = Path(tmp) / "anthropic-key.txt"
            legacy.write_text("sk-ant-legacy-456\nsecond line warning\n",
                              encoding="utf-8")
            key, source = doctor._resolve_key(self._probes(legacy))
            self.assertEqual(key, "sk-ant-legacy-456")
            self.assertEqual(source, str(legacy))

    def test_missing_everywhere_is_none(self):
        key, source = doctor._resolve_key(
            self._probes(Path("/nonexistent/anthropic-key.txt")))
        self.assertIsNone(key)
        self.assertEqual(source, "")


if __name__ == "__main__":
    unittest.main()
