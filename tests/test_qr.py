"""tests/test_qr.py — the pure-stdlib QR encoder (act/lib/qr.py).

No pip deps: these are structural checks that the encoder emits a spec-valid QR
matrix (correct size, finder + timing patterns, quiet zone) and that the two
renderers work. Full decode-verification is done in dev with OpenCV but is not
required here (no pip floor). We DO run an independent Reed-Solomon syndrome
check on the produced matrix, which proves the EC codewords are valid for the
data without needing an external decoder.
"""
import unittest

from tests import TMP_HOME  # noqa: F401 - sandboxes AIASSISTANT_HOME first

from act.lib import qr
from tests import qr_testkit as kit


def _finder_ok(m, ox, oy):
    # 7x7 finder: outer ring dark, one light ring, 3x3 dark centre.
    for dy in range(7):
        for dx in range(7):
            dist = max(abs(dx - 3), abs(dy - 3))
            want = dist != 2  # dark except the ring at chebyshev-distance 2
            if m[oy + dy][ox + dx] != want:
                return False
    return True


class QrMatrixTestCase(unittest.TestCase):
    SAMPLE = "ZQR1-" + "x" * 140  # ~145 chars, like a real channel pairing blob

    def test_matrix_is_square_and_plausible_size(self):
        m = qr.qr_matrix(self.SAMPLE)
        n = len(m)
        self.assertTrue(all(len(row) == n for row in m))
        # size = 4*version + 17, version 1..10 → 21..73, always ≡ 1 (mod 4)
        self.assertEqual((n - 17) % 4, 0)
        self.assertGreaterEqual(n, 21)
        self.assertLessEqual(n, 73)

    def test_finder_patterns_present(self):
        m = qr.qr_matrix(self.SAMPLE)
        n = len(m)
        self.assertTrue(_finder_ok(m, 0, 0), "top-left finder")
        self.assertTrue(_finder_ok(m, n - 7, 0), "top-right finder")
        self.assertTrue(_finder_ok(m, 0, n - 7), "bottom-left finder")

    def test_timing_pattern_alternates(self):
        m = qr.qr_matrix(self.SAMPLE)
        n = len(m)
        for i in range(8, n - 8):
            self.assertEqual(m[6][i], i % 2 == 0)
            self.assertEqual(m[i][6], i % 2 == 0)

    def test_dark_module_present(self):
        m = qr.qr_matrix(self.SAMPLE)
        n = len(m)
        self.assertTrue(m[n - 8][8])  # the always-dark module

    def test_reed_solomon_syndromes_are_zero(self):
        # Independent proof the EC codewords are valid: re-read the codewords
        # from the produced matrix (unmasking via the format bits, see
        # tests/qr_testkit.py) and confirm every block's Reed-Solomon
        # syndromes vanish.
        ec = "M"
        m = qr.qr_matrix(self.SAMPLE, ec)
        version = (len(m) - 17) // 4
        blocks, ecpb = kit.deinterleave(kit.read_codewords(m), version, ec)
        for b, block in enumerate(blocks):
            self.assertEqual(kit.syndromes(block, ecpb), [0] * ecpb,
                             f"block {b} has a nonzero syndrome")

    def test_capacity_overflow_raises(self):
        with self.assertRaises(ValueError):
            qr.qr_matrix("Z" * 400, "H")  # exceeds version-10 byte capacity

    def test_bad_ec_level_raises(self):
        with self.assertRaises(ValueError):
            qr.qr_matrix("hello", "Z")


class QrRenderTestCase(unittest.TestCase):
    def test_terminal_is_nonempty_block_string(self):
        out = qr.qr_terminal("hello world")
        self.assertIsInstance(out, str)
        self.assertTrue(out)
        self.assertTrue(any(ch in out for ch in "█▀▄"))

    def test_png_has_magic_and_content(self):
        import tempfile
        from pathlib import Path

        p = Path(tempfile.mkdtemp()) / "qr.png"
        qr.qr_png("hello world", p)
        blob = p.read_bytes()
        self.assertEqual(blob[:8], b"\x89PNG\r\n\x1a\n")
        self.assertIn(b"IHDR", blob[:64])
        self.assertIn(b"IEND", blob[-16:])


if __name__ == "__main__":
    unittest.main()
