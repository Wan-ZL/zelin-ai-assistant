"""qr — the mask conditions and penalty rules as standalone table tests.

The golden (tests/test_qr_golden.py) pins whole codes; these pin the pieces
the P3b split exposed so a wrong mask formula or a boundary slip in a rule is
named by the failing test rather than by a whole-matrix diff.
"""
import unittest

from act.lib import qr

M = qr._Matrix


class MaskBitTestCase(unittest.TestCase):
    def test_eight_masks_match_iso_18004_formulas(self):
        pts = [(x, y) for x in range(7) for y in range(7)]
        formulas = {
            0: lambda x, y: (x + y) % 2 == 0,
            1: lambda x, y: y % 2 == 0,
            2: lambda x, y: x % 3 == 0,
            3: lambda x, y: (x + y) % 3 == 0,
            4: lambda x, y: (y // 2 + x // 3) % 2 == 0,
            5: lambda x, y: (x * y) % 2 + (x * y) % 3 == 0,
            6: lambda x, y: ((x * y) % 2 + (x * y) % 3) % 2 == 0,
            7: lambda x, y: ((x + y) % 2 + (x * y) % 3) % 2 == 0,
        }
        for mask, f in formulas.items():
            for x, y in pts:
                self.assertEqual(M._mask_bit(mask, x, y), f(x, y), (mask, x, y))

    def test_masks_differ_from_each_other(self):
        pts = [(x, y) for x in range(12) for y in range(12)]
        sigs = {tuple(M._mask_bit(m, x, y) for x, y in pts) for m in range(8)}
        self.assertEqual(len(sigs), 8)

    def test_apply_mask_skips_function_modules_and_is_an_involution(self):
        mx = M(1)
        mx.draw_function_patterns()
        before = [row[:] for row in mx.mods]
        mx.apply_mask(0)
        for y in range(mx.size):
            for x in range(mx.size):
                if mx.fun[y][x]:
                    self.assertEqual(mx.mods[y][x], before[y][x])
        mx.apply_mask(0)
        self.assertEqual(mx.mods, before)


class PenaltyRulesTestCase(unittest.TestCase):
    def test_run_penalty_boundaries(self):
        self.assertEqual(M._run_penalty([True] * 4), 0)
        self.assertEqual(M._run_penalty([True] * 5), 3)
        self.assertEqual(M._run_penalty([True] * 7), 5)
        self.assertEqual(M._run_penalty([True] * 5 + [False] * 6), 3 + 4)
        self.assertEqual(M._run_penalty([True, False] * 6), 0)

    def test_finder_like_count(self):
        a = M._PATT_A
        b = M._PATT_B
        self.assertEqual(M._finder_like_count(a), 1)
        self.assertEqual(M._finder_like_count(b), 1)
        self.assertEqual(M._finder_like_count(a + b), 2)
        self.assertEqual(M._finder_like_count([True] * 11), 0)
        self.assertEqual(M._finder_like_count([True] * 10), 0)

    def test_rule2_and_rule4_on_uniform_matrices(self):
        mx = M(1)
        n = mx.size
        mx.mods = [[True] * n for _ in range(n)]
        self.assertEqual(mx._rule2(), 3 * (n - 1) * (n - 1))
        self.assertEqual(mx._rule4(), 100)          # 100% dark → 50/5*10
        mx.mods = [[(x + y) % 2 == 0 for x in range(n)] for y in range(n)]
        self.assertEqual(mx._rule2(), 0)
        self.assertEqual(mx._rule4(), 0)            # ~50% dark
        self.assertEqual(mx.penalty(), mx._rule1() + mx._rule2() + mx._rule3() + mx._rule4())

    def test_place_bit_respects_function_modules_and_stream_end(self):
        mx = M(1)
        mx.fun[0][0] = True
        self.assertEqual(mx._place_bit(0, 0, [0xFF], 0), 0)
        self.assertEqual(mx._place_bit(1, 0, [0x80], 0), 1)
        self.assertTrue(mx.mods[0][1])
        self.assertEqual(mx._place_bit(2, 0, [0x80], 8), 8)   # stream exhausted


class CodewordHelpersTestCase(unittest.TestCase):
    def test_split_and_interleave(self):
        cw = list(range(10))
        blocks = qr._split_blocks(cw, 1, 4, 2, 3)
        self.assertEqual(blocks, [[0, 1, 2, 3], [4, 5, 6], [7, 8, 9]])
        self.assertEqual(qr._interleave_data(blocks), [0, 4, 7, 1, 5, 8, 2, 6, 9, 3])
        self.assertEqual(qr._interleave_ec([[1, 2], [3, 4]], 2), [1, 3, 2, 4])

    def test_pad_codewords_alternates(self):
        self.assertEqual(qr._pad_codewords([0, 0, 0, 0, 0, 0, 0, 1], 4), [1, 0xEC, 0x11, 0xEC])

    def test_data_bits_header_terminator_and_alignment(self):
        bits = qr._data_bits(b"", 1, 16)
        # 0100 mode, 8-bit count (=0), 4 zero terminator → 16 bits
        self.assertEqual(bits, [0, 1, 0, 0] + [0] * 8 + [0] * 4)
        tight = qr._data_bits(b"\xff", 1, 2)   # 4 + 8 + 8 = 20 bits > 16 cap → no terminator
        self.assertEqual(len(tight), 24)


if __name__ == "__main__":
    unittest.main()
