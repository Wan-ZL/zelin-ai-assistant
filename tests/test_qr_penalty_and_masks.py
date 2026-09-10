"""qr — the mask conditions and penalty rules as standalone table tests.

The golden (tests/test_qr_golden.py) pins whole codes; these pin the pieces
the P3b split exposed so a wrong mask formula or a boundary slip in a rule is
named by the failing test rather than by a whole-matrix diff.

R-206（§57 变异补网）追加：原来的规则判例只用**对称**素材（全黑 / 棋盘 / 尾部
run），于是 rule 2 的邻居下标、rule 3 的 ×40 权重、rule 4 的百分比分桶、四条规则
的加总方向全都测不到——非对称小矩阵与手算定值补在下面。素材尺寸直接改
`_Matrix.size`：两条规则只读 `self.size` / `self.mods`，不需要真的铸一张 21×21。
"""
import unittest

from act.lib import qr

M = qr._Matrix


def _grid(rows):
    """把 "1100" 之类的行拼成一张 n×n 的假矩阵（只供规则函数消费）。"""
    mx = M(1)
    mx.size = len(rows)
    mx.mods = [[c == "1" for c in row] for row in rows]
    assert all(len(row) == mx.size for row in mx.mods), "fixture must be square"
    return mx


def _with_dark_count(size, dark):
    """n×n 矩阵，按行优先点亮前 ``dark`` 个模块——只有黑模块总数有意义。"""
    mx = M(1)
    mx.size = size
    mx.mods = [[y * size + x < dark for x in range(size)] for y in range(size)]
    return mx


def _finder_like_fixture():
    """21×21 的浅色底 + 三处 finder-like 图案（两行一列），行列命中 3 + 1。"""
    n = 21
    mods = [[False] * n for _ in range(n)]
    mods[3][0:11] = M._PATT_A
    mods[10][5:16] = M._PATT_B
    for i, dark in enumerate(M._PATT_A):
        mods[i][18] = dark               # 竖着摆一份（顺带给行再添 1 个命中）
    mx = M(1)
    mx.size = n
    mx.mods = mods
    return mx


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

    def test_run_penalty_ignores_interior_runs_shorter_than_five(self):
        # 行内（非行尾）恰好 4 连：一分不给——这条边只有内层的那个 run>=5 管。
        self.assertEqual(M._run_penalty([True] * 4 + [False] * 4 + [True] * 4), 0)
        self.assertEqual(M._run_penalty([True] * 4 + [False] * 7), 5)      # 只算尾部 7 连
        self.assertEqual(M._run_penalty([False] * 6 + [True] * 4), 4)      # 只算头部 6 连
        self.assertEqual(M._run_penalty([True] * 5 + [False] * 4 + [True] * 5), 6)

    def test_rule2_counts_only_true_two_by_two_blocks(self):
        # 三块 2×2 同色（左上 4 个黑、右上 4 个白、左下 4 个白），每块 3 分。
        mx = _grid(["1100",
                    "1100",
                    "0010",
                    "0001"])
        self.assertEqual(mx._rule2(), 9)
        # 纵向成对但横向不成对：一块都不算。
        self.assertEqual(_grid(["10", "10"])._rule2(), 0)
        self.assertEqual(_grid(["11", "00"])._rule2(), 0)
        self.assertEqual(_grid(["11", "11"])._rule2(), 3)

    def test_rule3_scores_forty_per_finder_like_window(self):
        mx = _finder_like_fixture()
        n, mods = mx.size, mx.mods
        rows = sum(M._finder_like_count(mods[y]) for y in range(n))
        cols = sum(M._finder_like_count([mods[y][x] for y in range(n)]) for x in range(n))
        self.assertEqual((rows, cols), (3, 1))
        self.assertEqual(mx._rule3(), 4 * 40)
        self.assertEqual(_grid(["0" * 21] * 21)._rule3(), 0)

    def test_finder_patterns_are_the_iso_ratio_and_each_other_reversed(self):
        # 1:1:3:1:1 深浅交替 + 4 个浅模块的安静区，B 是 A 的镜像。
        ratio = [True] + [False] + [True] * 3 + [False] + [True]
        self.assertEqual(M._PATT_A, ratio + [False] * 4)
        self.assertEqual(M._PATT_B, [False] * 4 + ratio)
        self.assertEqual(M._PATT_B, list(reversed(M._PATT_A)))
        self.assertEqual(len(M._PATT_A), 11)

    def test_rule4_scores_the_distance_from_a_fifty_percent_dark_ratio(self):
        # 每格：(边长, 黑模块数) → 期望罚分。ISO：把黑占比向下取到 5% 的两个
        # 桶边，取离 50% 近的那个，每偏离 5% 记 10 分。
        cases = [
            ((10, 50), 0),      # 50% → 0
            ((10, 36), 20),     # 36% → 桶 35/40，近的是 40 → |40−50|/5*10
            ((10, 20), 50),     # 20% → 桶 20/25 → |25−50|/5*10
            ((10, 0), 90),      # 全白
            ((10, 100), 100),   # 全黑
            ((25, 372), 10),    # 59.52% → 桶 55/60，近的是 55
        ]
        for (size, dark), want in cases:
            with self.subTest(size=size, dark=dark):
                self.assertEqual(_with_dark_count(size, dark)._rule4(), want)

    def test_penalty_adds_all_four_rules(self):
        mx = _finder_like_fixture()
        # 四条规则在这张素材上都非零，任何一项被减掉都对不上（定值实测钉死；
        # 上面的规则判例各自单独钉住了它们的算法）。
        self.assertEqual((mx._rule1(), mx._rule2(), mx._rule3(), mx._rule4()),
                         (725, 1068, 160, 90))
        self.assertEqual(mx.penalty(), 2043)

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
