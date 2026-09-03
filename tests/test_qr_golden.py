"""qr — golden module matrices pin the encoder byte-for-byte (§41 pairing QR).

``tests/fixtures/qr/matrices.golden.json`` was captured from the pre-P3b
encoder: version choice, data+EC codeword stream, the final masked matrix and
the terminal rendering (sha256) for six payloads spanning versions 1–10 and all
four EC levels. Any drift in bit assembly, block interleave, mask selection or
penalty scoring flips a row here.
"""
import hashlib
import json
import unittest
from pathlib import Path

from act.lib import qr

GOLDEN = Path(__file__).parent / "fixtures" / "qr" / "matrices.golden.json"


class QrGoldenTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = json.loads(GOLDEN.read_text(encoding="utf-8"))

    def test_fixture_covers_all_ec_levels_and_a_v7_plus_code(self):
        self.assertEqual({c["ec"] for c in self.cases.values()}, {"L", "M", "Q", "H"})
        self.assertTrue(any(c["version"] >= 7 for c in self.cases.values()))

    def test_codewords_match_golden(self):
        for name, case in self.cases.items():
            with self.subTest(name=name):
                raw = case["data"].encode("utf-8")
                version = qr._choose_version(len(raw), case["ec"])
                self.assertEqual(version, case["version"])
                self.assertEqual(qr._encode_codewords(raw, version, case["ec"]),
                                 case["codewords"])

    def test_matrix_matches_golden(self):
        for name, case in self.cases.items():
            with self.subTest(name=name):
                m = qr.qr_matrix(case["data"], case["ec"])
                self.assertEqual(len(m), case["size"])
                rows = ["".join("1" if c else "0" for c in row) for row in m]
                self.assertEqual(rows, case["rows"])

    def test_terminal_rendering_matches_golden(self):
        for name, case in self.cases.items():
            with self.subTest(name=name):
                text = qr.qr_terminal(case["data"], case["ec"])
                self.assertEqual(hashlib.sha256(text.encode("utf-8")).hexdigest(),
                                 case["terminal_sha256"])


if __name__ == "__main__":
    unittest.main()
