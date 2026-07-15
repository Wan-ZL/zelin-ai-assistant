"""Audit regression tests — pairing/channel recovery paths.

Covers three confirmed audit findings:
  * a corrupt pairing-key file (valid JSON, wrong shape) must raise ValueError
    from e2e.load_pairing — the type both catch sites already handle — so the
    daemon pauses honestly and --pair regenerates instead of both crashing;
  * a present-but-corrupt write_secret must never be silently regenerated
    under the same channel_id (the server's hash is INSERT-only → permanent
    RLS brick); --pair rotates the CHANNEL, and the daemon pauses honestly;
  * re-running a bare --pair (the Settings page does this on every open) must
    keep a previously chosen custom label instead of clobbering it back to
    the default 这台 Mac.

Fake transport, sandbox AIASSISTANT_HOME, no network.
"""
import contextlib
import io
import json
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import syncd
from act.lib import e2e
from tests.test_syncd import (
    _CHAN, FakeTransport, _reset_state, _sync_cfg, _write_secret_files,
)

try:
    import cryptography  # noqa: F401

    HAVE_CRYPTO = True
except ImportError:
    HAVE_CRYPTO = False


class CorruptPairingFileTestCase(unittest.TestCase):
    def setUp(self):
        _reset_state()

    def _write_pairing(self, body: str):
        e2e.PAIRINGS_DIR.mkdir(parents=True, exist_ok=True)
        e2e.pairing_key_path(_CHAN).write_text(body, encoding="utf-8")

    def test_wrong_shape_raises_value_error_not_key_error(self):
        for body in ('{}', '"just a string"', '[1, 2]',
                     '{"key": "AAA", "epoch": {"x": 1}}'):
            self._write_pairing(body)
            with self.assertRaises(ValueError, msg=body):
                e2e.load_pairing(_CHAN)

    def test_daemon_pauses_honestly_on_corrupt_pairing(self):
        _write_secret_files()
        self._write_pairing("{}")
        d = syncd.Syncd(_sync_cfg(), FakeTransport())
        self.assertFalse(d._ensure_ready())
        self.assertIn("配对密钥", d._paused_reason)
        d.run_once()  # never raises; status.json stays honest (paused: true)
        status = json.loads(syncd.STATUS_PATH.read_text(encoding="utf-8"))
        self.assertTrue(status["paused"])

    @unittest.skipUnless(HAVE_CRYPTO, "cryptography not installed (optional cloud dep)")
    def test_repair_recovers_from_corrupt_pairing_file(self):
        # the user's natural fix — re-running --pair — must regenerate the
        # key instead of crashing with a traceback before writing anything.
        syncd._ensure_sync_dir()
        syncd.CHANNEL_ID_PATH.write_text(_CHAN + "\n", encoding="utf-8")
        _write_secret_files()
        self._write_pairing("{}")
        with mock.patch.object(syncd, "HttpTransport",
                               return_value=FakeTransport()):
            result = syncd.init_channel("这台 Mac")
        self.assertEqual(result["channel_id"], _CHAN)
        epoch, k_i = e2e.load_pairing(_CHAN)  # regenerated, loadable again
        self.assertEqual(len(k_i), 32)
        self.assertEqual(epoch, 1)


class CorruptWriteSecretTestCase(unittest.TestCase):
    def setUp(self):
        _reset_state()

    def test_daemon_pauses_on_corrupt_write_secret(self):
        # pre-fix, the corrupt text was sent as a garbage x-sync-write header:
        # every write failed RLS forever while status.json said paused: false.
        syncd._ensure_sync_dir()
        syncd.WRITE_SECRET_PATH.write_text("%%%corrupt%%%\n", encoding="utf-8")
        d = syncd.Syncd(_sync_cfg(), FakeTransport())
        self.assertFalse(d._ensure_ready())
        self.assertIn("写入密钥", d._paused_reason)

    @unittest.skipUnless(HAVE_CRYPTO, "cryptography not installed (optional cloud dep)")
    def test_repair_rotates_channel_never_mismatched_secret(self):
        # corrupt secret + surviving channel_id: regenerating the secret under
        # the SAME channel would brick every write forever (channels rows are
        # INSERT-only, the old hash can never be corrected). --pair must
        # rotate the channel so the re-pair lands on a consistent identity.
        syncd._ensure_sync_dir()
        syncd.CHANNEL_ID_PATH.write_text(_CHAN + "\n", encoding="utf-8")
        syncd.WRITE_SECRET_PATH.write_text("%%%corrupt%%%\n", encoding="utf-8")
        with mock.patch.object(syncd, "HttpTransport",
                               return_value=FakeTransport()):
            result = syncd.init_channel("这台 Mac")
        self.assertNotEqual(result["channel_id"], _CHAN)
        self.assertTrue(syncd._valid_write_secret(
            syncd.WRITE_SECRET_PATH.read_text(encoding="utf-8").strip()))
        cfg = json.loads(syncd.SYNC_CONFIG_PATH.read_text(encoding="utf-8"))
        self.assertEqual(cfg["channel_id"], result["channel_id"])


@unittest.skipUnless(HAVE_CRYPTO, "cryptography not installed (optional cloud dep)")
class PairLabelResolutionTestCase(unittest.TestCase):
    def setUp(self):
        _reset_state()

    def _pair(self, argv):
        buf = io.StringIO()
        with mock.patch.object(syncd, "HttpTransport",
                               return_value=FakeTransport()):
            with contextlib.redirect_stdout(buf):
                rc = syncd.main(argv)
        self.assertEqual(rc, 0)
        return json.loads(buf.getvalue())

    def test_bare_repair_keeps_custom_label(self):
        first = self._pair(["--pair", "--json", "--label", "公司 Mac"])
        self.assertEqual(first["label"], "公司 Mac")
        # the Mac Settings page re-runs a bare --pair on every open — pre-fix
        # the argparse default clobbered the label back to 这台 Mac here.
        second = self._pair(["--pair", "--json"])
        self.assertEqual(second["label"], "公司 Mac")
        cfg = json.loads(syncd.SYNC_CONFIG_PATH.read_text(encoding="utf-8"))
        self.assertEqual(cfg["label"], "公司 Mac")
        # the regenerated QR still carries the custom label
        parsed = e2e.parse_channel_qr(second["qr_blob"])
        self.assertEqual(parsed["label"], "公司 Mac")

    def test_explicit_label_still_wins(self):
        self._pair(["--pair", "--json", "--label", "公司 Mac"])
        renamed = self._pair(["--pair", "--json", "--label", "书房 Mac"])
        self.assertEqual(renamed["label"], "书房 Mac")
        cfg = json.loads(syncd.SYNC_CONFIG_PATH.read_text(encoding="utf-8"))
        self.assertEqual(cfg["label"], "书房 Mac")

    def test_fresh_pair_defaults_to_this_mac(self):
        first = self._pair(["--pair", "--json"])
        self.assertEqual(first["label"], "这台 Mac")


if __name__ == "__main__":
    unittest.main()
