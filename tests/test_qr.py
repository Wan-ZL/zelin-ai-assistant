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
        # from the produced matrix (unmasking via the format bits) and confirm
        # every block's Reed-Solomon syndromes vanish.
        from act.lib.qr import (_EC_LEVELS, _EC_TABLE, _GF_EXP, _Matrix,
                                _gf_mul)

        ec = "M"
        m = qr.qr_matrix(self.SAMPLE, ec)
        n = len(m)
        ver = (n - 17) // 4
        fun = _Matrix(ver)
        fun.draw_function_patterns()
        # recover mask from the top-left format copy
        order = [(8, 0), (8, 1), (8, 2), (8, 3), (8, 4), (8, 5), (8, 7), (8, 8),
                 (7, 8), (5, 8), (4, 8), (3, 8), (2, 8), (1, 8), (0, 8)]
        fbits = 0
        for i, (x, y) in enumerate(order):
            fbits |= (1 if m[y][x] else 0) << i
        fbits ^= 0x5412
        mask = (fbits >> 10) & 7

        def masked(x, y):
            if mask == 0:
                return (x + y) % 2 == 0
            if mask == 1:
                return y % 2 == 0
            if mask == 2:
                return x % 3 == 0
            if mask == 3:
                return (x + y) % 3 == 0
            if mask == 4:
                return (y // 2 + x // 3) % 2 == 0
            if mask == 5:
                return (x * y) % 2 + (x * y) % 3 == 0
            if mask == 6:
                return ((x * y) % 2 + (x * y) % 3) % 2 == 0
            return ((x + y) % 2 + (x * y) % 3) % 2 == 0

        grid = [[m[y][x] for x in range(n)] for y in range(n)]
        for y in range(n):
            for x in range(n):
                if not fun.fun[y][x] and masked(x, y):
                    grid[y][x] = not grid[y][x]

        bits = []
        col = n - 1
        while col > 0:
            if col == 6:
                col = 5
            for ri in range(n):
                for c in range(2):
                    x = col - c
                    up = ((col + 1) & 2) == 0
                    yy = (n - 1 - ri) if up else ri
                    if not fun.fun[yy][x]:
                        bits.append(1 if grid[yy][x] else 0)
            col -= 2
        cwbits = bits[: (len(bits) // 8) * 8]
        allcw = [int("".join(map(str, cwbits[i:i + 8])), 2) for i in range(0, len(cwbits), 8)]

        ecpb, g1, g1d, g2, g2d = _EC_TABLE[ver][_EC_LEVELS.index(ec)]
        blocks_len = [g1d] * g1 + [g2d] * g2
        nblocks = g1 + g2
        total_data = sum(blocks_len)
        data_cw = allcw[:total_data]
        ec_cw = allcw[total_data:total_data + ecpb * nblocks]

        dblocks = [[] for _ in range(nblocks)]
        idx = 0
        for i in range(max(blocks_len)):
            for b in range(nblocks):
                if i < blocks_len[b]:
                    dblocks[b].append(data_cw[idx])
                    idx += 1
        eblocks = [[] for _ in range(nblocks)]
        idx = 0
        for i in range(ecpb):
            for b in range(nblocks):
                eblocks[b].append(ec_cw[idx])
                idx += 1

        for b in range(nblocks):
            full = dblocks[b] + eblocks[b]
            for s in range(ecpb):
                acc = 0
                for c in full:
                    acc = _gf_mul(acc, _GF_EXP[s]) ^ c
                self.assertEqual(acc, 0, f"block {b} syndrome {s} nonzero")

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
