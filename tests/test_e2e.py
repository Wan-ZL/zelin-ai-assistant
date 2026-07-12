"""act/lib/e2e.py — E2E crypto for the iOS cloud-sync backend (plan §4).

Covers: board + action + label AEAD roundtrips (byte-identical plaintext back);
AEAD integrity (tampered ciphertext / tag → raise); wrong key → raise; AAD
binding (wrong seq / epoch / device_id / action_id → raise); the opaque QR
pairing-blob build→parse roundtrip; new_pairing_key = 32 random bytes; the
frozen blob header layout; pairing key storage roundtrip; and that the sync-only
device UUID is a SEPARATE file/value from the telemetry state/device_id (§8-4).

Skips gracefully when `cryptography` is absent (it is an OPTIONAL cloud-only dep,
lazy-imported by e2e.py) — but it IS installed in dev, so these run here. The
module import itself must NOT need cryptography; that is asserted too.

Everything lives under the sandbox AIASSISTANT_HOME (tests/__init__.py).
"""
import struct
import unittest

from tests import TMP_HOME  # noqa: F401 - ensures the sandbox env is set first

from act.lib import config, e2e

try:
    import cryptography  # noqa: F401

    HAVE_CRYPTO = True
except ImportError:
    HAVE_CRYPTO = False

_K = b"\x11" * 32
_DEV = "11111111-1111-4111-8111-111111111111"
_BOARD = b'{"generated_at":"2026-07-12T00:00:00Z","cards":[{"id":"R-001"}]}'


@unittest.skipUnless(HAVE_CRYPTO, "cryptography not installed (optional cloud dep)")
class BoardCryptoTestCase(unittest.TestCase):
    def test_board_roundtrip_bytes_identical(self):
        blob = e2e.encrypt_board(_K, 1, _DEV, 7, _BOARD)
        self.assertEqual(e2e.decrypt_board(_K, 1, _DEV, 7, blob), _BOARD)

    def test_ciphertext_is_not_plaintext(self):
        blob = e2e.encrypt_board(_K, 1, _DEV, 7, _BOARD)
        self.assertNotIn(b"R-001", blob)

    def test_tampered_ciphertext_raises(self):
        blob = bytearray(e2e.encrypt_board(_K, 1, _DEV, 7, _BOARD))
        # flip a byte in the ciphertext region (just past the fixed header)
        blob[e2e._HEADER_LEN] ^= 0x01
        with self.assertRaises(Exception):
            e2e.decrypt_board(_K, 1, _DEV, 7, bytes(blob))

    def test_tampered_tag_raises(self):
        blob = bytearray(e2e.encrypt_board(_K, 1, _DEV, 7, _BOARD))
        blob[-1] ^= 0x01  # last byte is inside the 16-byte tag
        with self.assertRaises(Exception):
            e2e.decrypt_board(_K, 1, _DEV, 7, bytes(blob))

    def test_wrong_key_raises(self):
        blob = e2e.encrypt_board(_K, 1, _DEV, 7, _BOARD)
        with self.assertRaises(Exception):
            e2e.decrypt_board(b"\x22" * 32, 1, _DEV, 7, blob)

    def test_wrong_seq_raises(self):
        blob = e2e.encrypt_board(_K, 1, _DEV, 7, _BOARD)
        with self.assertRaises(Exception):
            e2e.decrypt_board(_K, 1, _DEV, 8, blob)

    def test_wrong_device_id_raises(self):
        blob = e2e.encrypt_board(_K, 1, _DEV, 7, _BOARD)
        with self.assertRaises(Exception):
            e2e.decrypt_board(_K, 1, "22222222-2222-4222-8222-222222222222", 7, blob)

    def test_wrong_epoch_raises(self):
        blob = e2e.encrypt_board(_K, 1, _DEV, 7, _BOARD)
        with self.assertRaises(Exception):
            e2e.decrypt_board(_K, 2, _DEV, 7, blob)

    def test_blob_header_layout(self):
        blob = e2e.encrypt_board(_K, 5, _DEV, 7, _BOARD)
        self.assertEqual(blob[0:4], e2e.MAGIC)
        self.assertEqual(blob[4], e2e.VERSION)
        self.assertEqual(blob[5], e2e.ALG_CHACHA20POLY1305_IETF)
        self.assertEqual(struct.unpack(">I", blob[6:10])[0], 5)
        self.assertGreaterEqual(len(blob), e2e._HEADER_LEN + 16)


@unittest.skipUnless(HAVE_CRYPTO, "cryptography not installed (optional cloud dep)")
class ActionCryptoTestCase(unittest.TestCase):
    _ACTION = b'{"action":"approve","id":"R-001"}'
    _AID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"

    def test_action_roundtrip(self):
        blob = e2e.encrypt_action(_K, 1, _DEV, self._AID, 7, self._ACTION)
        self.assertEqual(e2e.decrypt_action(_K, 1, _DEV, self._AID, 7, blob), self._ACTION)

    def test_wrong_action_id_raises(self):
        blob = e2e.encrypt_action(_K, 1, _DEV, self._AID, 7, self._ACTION)
        with self.assertRaises(Exception):
            e2e.decrypt_action(_K, 1, _DEV, "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", 7, blob)

    def test_wrong_board_seq_raises(self):
        blob = e2e.encrypt_action(_K, 1, _DEV, self._AID, 7, self._ACTION)
        with self.assertRaises(Exception):
            e2e.decrypt_action(_K, 1, _DEV, self._AID, 8, blob)

    def test_board_blob_not_decryptable_as_action(self):
        # domain separation: a board blob must not open with the action AAD/info
        blob = e2e.encrypt_board(_K, 1, _DEV, 7, _BOARD)
        with self.assertRaises(Exception):
            e2e.decrypt_action(_K, 1, _DEV, self._AID, 7, blob)


@unittest.skipUnless(HAVE_CRYPTO, "cryptography not installed (optional cloud dep)")
class LabelCryptoTestCase(unittest.TestCase):
    def test_label_roundtrip_unicode(self):
        blob = e2e.encrypt_label(_K, 1, _DEV, "公司 Mac")
        self.assertEqual(e2e.decrypt_label(_K, 1, _DEV, blob), "公司 Mac")

    def test_label_wrong_key_raises(self):
        blob = e2e.encrypt_label(_K, 1, _DEV, "公司 Mac")
        with self.assertRaises(Exception):
            e2e.decrypt_label(b"\x33" * 32, 1, _DEV, blob)


@unittest.skipUnless(HAVE_CRYPTO, "cryptography not installed (optional cloud dep)")
class PairingBlobTestCase(unittest.TestCase):
    def test_build_parse_roundtrip(self):
        k = e2e.new_pairing_key()
        blob = e2e.build_pairing_blob(_DEV, 3, k, "书房 Mac mini")
        parsed = e2e.parse_pairing_blob(blob)
        self.assertEqual(parsed["device_id"], _DEV)
        self.assertEqual(parsed["epoch"], 3)
        self.assertEqual(parsed["key"], k)
        self.assertEqual(parsed["label"], "书房 Mac mini")

    def test_blob_is_opaque_no_scheme(self):
        # base64 alphabet is [A-Za-z0-9+/=], so ":" (and thus any "scheme://")
        # can never appear — the blob is not a URL scheme.
        blob = e2e.build_pairing_blob(_DEV, 1, e2e.new_pairing_key(), "x")
        self.assertNotIn(":", blob)


class PairingKeyStorageTestCase(unittest.TestCase):
    """Storage helpers do NOT need cryptography (pure fs), so these always run."""

    def test_new_pairing_key_is_32_random_bytes(self):
        a = e2e.new_pairing_key()
        b = e2e.new_pairing_key()
        self.assertEqual(len(a), 32)
        self.assertEqual(len(b), 32)
        self.assertNotEqual(a, b)

    def test_save_load_pairing_roundtrip(self):
        k = e2e.new_pairing_key()
        path = e2e.save_pairing(_DEV, 4, k)
        self.assertTrue(path.exists())
        epoch, loaded = e2e.load_pairing(_DEV)
        self.assertEqual(epoch, 4)
        self.assertEqual(loaded, k)

    def test_pairing_file_is_0600(self):
        e2e.save_pairing(_DEV, 1, e2e.new_pairing_key())
        mode = e2e.pairing_key_path(_DEV).stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)

    def test_pairing_rejects_non_uuid_device_id(self):
        with self.assertRaises(Exception):
            e2e.save_pairing("../../etc/passwd", 1, e2e.new_pairing_key())

    def test_sync_device_id_distinct_from_telemetry_id(self):
        from act.lib import analytics_sync

        sync_id = e2e.sync_device_id()
        telemetry_id = analytics_sync._device_id()
        # different files ...
        self.assertNotEqual(e2e.SYNC_DEVICE_ID_PATH, analytics_sync.DEVICE_ID_PATH)
        self.assertEqual(e2e.SYNC_DEVICE_ID_PATH, config.STATE_DIR / "sync_device_id")
        # ... and different values (both fresh uuid4)
        self.assertNotEqual(sync_id, telemetry_id)
        # stable across calls
        self.assertEqual(e2e.sync_device_id(), sync_id)


class ModuleImportTestCase(unittest.TestCase):
    def test_module_import_does_not_pull_cryptography_at_top_level(self):
        # The module must be importable on the PyYAML-only floor: no top-level
        # `cryptography` reference. We assert the source never imports it at
        # module scope (only lazily inside _crypto()).
        import inspect

        src = inspect.getsource(e2e)
        head = src.split("def _crypto")[0]
        self.assertNotIn("import cryptography", head)
        self.assertNotIn("from cryptography", head)


if __name__ == "__main__":
    unittest.main()
