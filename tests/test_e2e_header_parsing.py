"""e2e sync blob header — parsed and rejected without ``cryptography`` (§41 E2E).

The canonical qa-gates leg does not install cryptography, so the whole
tests/test_e2e.py suite skips there; this file pins the header rules with
plain bytes: too short, bad magic, unsupported version / alg, epoch mismatch,
and the (salt, nonce, ct_and_tag) split of a well-formed header.
"""
import struct
import unittest

from tests import TMP_HOME  # noqa: F401 - sandbox env first

from act.lib import e2e


def _blob(*, magic=e2e.MAGIC, ver=e2e.VERSION, alg=e2e.ALG_CHACHA20POLY1305_IETF, epoch=3,
          salt=b"s" * e2e._SALT_LEN, nonce=b"n" * e2e._NONCE_LEN, tail=b"t" * (e2e._TAG_LEN + 4)):
    return magic + bytes([ver, alg]) + struct.pack(">I", epoch) + salt + nonce + tail


class HeaderShapeTestCase(unittest.TestCase):
    def test_well_formed_header_splits(self):
        salt, nonce, ct = e2e._parse_header(_blob(), 3)
        self.assertEqual((salt, nonce, ct), (b"s" * 32, b"n" * 12, b"t" * 20))

    def test_too_short(self):
        with self.assertRaisesRegex(ValueError, "too short"):
            e2e._check_blob_shape(_blob()[:-5])
        minimal = _blob(tail=b"t" * e2e._TAG_LEN)
        e2e._check_blob_shape(minimal)                     # exactly header + tag is allowed

    def test_bad_magic_version_alg(self):
        with self.assertRaisesRegex(ValueError, "bad magic"):
            e2e._check_blob_shape(_blob(magic=b"ZQR1"))
        with self.assertRaisesRegex(ValueError, "unsupported blob version 2"):
            e2e._check_blob_shape(_blob(ver=2))
        with self.assertRaisesRegex(ValueError, "unsupported alg 9"):
            e2e._check_blob_shape(_blob(alg=9))

    def test_epoch_mismatch(self):
        with self.assertRaisesRegex(ValueError, "epoch mismatch: blob=3 expected=4"):
            e2e._check_blob_epoch(_blob(epoch=3), 4)
        e2e._check_blob_epoch(_blob(epoch=4), 4)
        with self.assertRaises(ValueError):
            e2e._parse_header(_blob(epoch=1), 2)

    def test_open_rejects_before_any_key_work(self):
        # a bad header never reaches the lazy cryptography import
        with self.assertRaisesRegex(ValueError, "bad magic"):
            e2e._open(b"k" * e2e.KEY_LEN, 1, b"info", lambda e: b"", _blob(magic=b"XXXX", epoch=1))


if __name__ == "__main__":
    unittest.main()
