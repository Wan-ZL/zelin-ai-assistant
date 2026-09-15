"""qr — the static ISO/IEC 18004 tables and the GF(256) field they ride on (§41).

act/lib/qr.py 里 90% 的代码体积是三张静态表：`_EC_TABLE`（每个 version × EC 级别的
块布局）、`_ALIGN_POS`（对齐图案中心）、以及 `_init_gf` 铸的 GF(256) 对数表。全套件
只编码少数几个 payload，于是**表里没被走到的那些格子随便改一位都不会有测试红**——
夜间变异（§57）在这三张表上留下 357 个存活体就是这么来的。

这个文件不再靠「跑一遍看输出」，而是把 ISO/IEC 18004 的**结构不变量**写成判例：
每个 (version, ec) 的块布局必须正好用满该 version 的数据模块总数（模块数公式独立于
被测代码）、group-2 的块比 group-1 多且只多一个数据码字、纠错强度越高容量越小；
对齐中心的个数与坐标由 version 唯一决定；GF(256) 的 exp/log 互为逆、指数表 255 周期。
表里任何一格 ±1 都会撞破其中至少一条——这一点由 R-206 的模拟穷举验证过。

另有 byte-mode 容量边界（`_choose_version`）：40 个 (version, EC) 组合的最大 payload
字节数直接对照 ISO 公布的表钉死，边界 +1 必须跳到下一个 version。
"""
import unittest

from tests import TMP_HOME  # noqa: F401 - sandboxes AIASSISTANT_HOME first

from act.lib import qr
from tests.qr_testkit import ISO_BYTE_CAPACITY as _ISO_BYTE_CAPACITY


def _raw_data_modules(version: int) -> int:
    """ISO 18004 表 1 的模块计数（独立于被测代码）：整块面积扣掉功能图案。

    (16v + 128)v + 64 是去掉 finder / 分隔符 / timing 之后的模块数；v ≥ 2 起
    每个对齐图案再扣 25 个（与 timing 重叠的部分回补），v ≥ 7 再扣两块 18 位
    的 version information。
    """
    result = (16 * version + 128) * version + 64
    if version >= 2:
        numalign = version // 7 + 2
        result -= (25 * numalign - 10) * numalign - 55
        if version >= 7:
            result -= 36
    return result


class GaloisFieldTestCase(unittest.TestCase):
    """_init_gf 铸的表：Reed–Solomon 全靠它，错一格 = 全部 EC 码字错。"""

    def test_exp_and_log_are_inverses_over_the_whole_field(self):
        self.assertEqual(qr._GF_EXP[0], 1)
        self.assertEqual(qr._GF_LOG[1], 0)
        seen = set()
        for i in range(255):
            value = qr._GF_EXP[i]
            self.assertTrue(1 <= value <= 255, (i, value))
            self.assertEqual(qr._GF_LOG[value], i)
            seen.add(value)
        self.assertEqual(len(seen), 255)          # 生成元遍历整个乘法群

    def test_tables_have_field_shape_and_the_exponent_table_wraps_at_255(self):
        self.assertEqual(len(qr._GF_EXP), 512)
        self.assertEqual(len(qr._GF_LOG), 256)
        self.assertEqual(qr._GF_LOG[0], 0)        # 0 没有对数，占位必须是 0
        for i in range(255, len(qr._GF_EXP)):
            self.assertEqual(qr._GF_EXP[i], qr._GF_EXP[i - 255], i)

    def test_multiplication_absorbs_zero_and_keeps_the_identity(self):
        for x in (0, 1, 2, 7, 128, 255):
            self.assertEqual(qr._gf_mul(0, x), 0, x)
            self.assertEqual(qr._gf_mul(x, 0), 0, x)
            self.assertEqual(qr._gf_mul(1, x), x, x)
            self.assertEqual(qr._gf_mul(x, 1), x, x)

    def test_multiplication_is_commutative_associative_and_distributive(self):
        sample = (1, 2, 3, 5, 17, 100, 200, 255)
        for a in sample:
            for b in sample:
                self.assertEqual(qr._gf_mul(a, b), qr._gf_mul(b, a))
                for c in sample:
                    self.assertEqual(qr._gf_mul(qr._gf_mul(a, b), c),
                                     qr._gf_mul(a, qr._gf_mul(b, c)))
                    self.assertEqual(qr._gf_mul(a, b ^ c),
                                     qr._gf_mul(a, b) ^ qr._gf_mul(a, c))

    def test_generator_polynomial_has_the_expected_roots(self):
        # g(x) = ∏ (x − a^i)：a^0 … a^(nsym−1) 逐个代入必须得 0。
        for nsym in (2, 7, 10, 26):
            gen = qr._rs_generator_poly(nsym)
            self.assertEqual(len(gen), nsym + 1)
            self.assertEqual(gen[0], 1)
            for i in range(nsym):
                acc = 0
                for coef in gen:
                    acc = qr._gf_mul(acc, qr._GF_EXP[i]) ^ coef
                self.assertEqual(acc, 0, (nsym, i))

    def test_remainder_makes_the_codeword_divisible_by_the_generator(self):
        data = [0x40, 0xD2, 0x75, 0x47, 0x76, 0x17, 0x32, 0x06]
        for nsym in (7, 10, 17):
            full = data + qr._rs_ec(data, nsym)
            for i in range(nsym):
                acc = 0
                for coef in full:
                    acc = qr._gf_mul(acc, qr._GF_EXP[i]) ^ coef
                self.assertEqual(acc, 0, (nsym, i))


class EcTableTestCase(unittest.TestCase):
    """_EC_TABLE 的每一格都被 ISO 结构不变量夹住（R-206：±1 全部撞破）。"""

    def test_block_layout_fills_the_version_exactly(self):
        for version in range(1, 11):
            total_codewords = _raw_data_modules(version) // 8
            for level in qr._EC_LEVELS:
                ecpb, g1, g1d, g2, g2d = qr._EC_TABLE[version][qr._EC_LEVELS.index(level)]
                with self.subTest(version=version, ec=level):
                    self.assertEqual(g1 * (g1d + ecpb) + g2 * (g2d + ecpb),
                                     total_codewords)

    def test_group_two_blocks_hold_exactly_one_more_data_codeword(self):
        for version in range(1, 11):
            for level in qr._EC_LEVELS:
                ecpb, g1, g1d, g2, g2d = qr._EC_TABLE[version][qr._EC_LEVELS.index(level)]
                with self.subTest(version=version, ec=level):
                    self.assertGreaterEqual(g1, 1)
                    self.assertGreaterEqual(g1d, 1)
                    self.assertGreaterEqual(ecpb, 1)
                    self.assertGreaterEqual(g2, 0)
                    if g2:
                        self.assertEqual(g2d, g1d + 1)
                    else:
                        self.assertEqual(g2d, 0)
                    # RS 块长上限：数据 + 纠错 ≤ 255 码字。
                    self.assertLessEqual(g1d + ecpb, 255)

    def test_capacity_shrinks_as_error_correction_gets_stronger(self):
        for version in range(1, 11):
            caps = [qr._data_capacity_cw(version, level) for level in qr._EC_LEVELS]
            with self.subTest(version=version):
                self.assertEqual(caps, sorted(caps, reverse=True))
                self.assertEqual(len(set(caps)), 4)

    def test_data_capacity_counts_both_groups(self):
        self.assertEqual(qr._data_capacity_cw(1, "L"), 19)
        self.assertEqual(qr._data_capacity_cw(5, "Q"), 2 * 15 + 2 * 16)
        self.assertEqual(qr._data_capacity_cw(10, "H"), 6 * 15 + 2 * 16)


class AlignmentPositionsTestCase(unittest.TestCase):
    def test_alignment_centres_match_the_iso_layout(self):
        for version in range(1, 11):
            centres = qr._ALIGN_POS[version]
            with self.subTest(version=version):
                if version == 1:
                    self.assertEqual(centres, [])
                    continue
                self.assertEqual(len(centres), version // 7 + 2)
                last = 4 * version + 10          # size − 7
                self.assertEqual(centres[0], 6)
                self.assertEqual(centres[-1], last)
                if len(centres) == 3:
                    self.assertEqual(centres[1], (6 + last) // 2)
                self.assertEqual(centres, sorted(centres))
                for c in centres:
                    self.assertEqual(c % 2, 0)   # 中心永远落在偶数坐标上


class VersionChoiceTestCase(unittest.TestCase):
    """_choose_version 的容量边界——ISO 表 7 的字符数逐格钉死。"""

    def test_byte_mode_capacity_matches_the_published_table(self):
        for level, caps in _ISO_BYTE_CAPACITY.items():
            for version, nbytes in zip(range(1, 11), caps):
                with self.subTest(ec=level, version=version):
                    self.assertEqual(qr._choose_version(nbytes, level), version)

    def test_one_byte_past_the_boundary_moves_up_a_version(self):
        for level, caps in _ISO_BYTE_CAPACITY.items():
            for version, nbytes in zip(range(1, 10), caps[:9]):
                with self.subTest(ec=level, version=version):
                    self.assertEqual(qr._choose_version(nbytes + 1, level), version + 1)

    def test_one_byte_past_version_ten_is_refused(self):
        for level, caps in _ISO_BYTE_CAPACITY.items():
            with self.subTest(ec=level):
                with self.assertRaises(ValueError):
                    qr._choose_version(caps[9] + 1, level)

    def test_character_count_field_widens_at_version_ten(self):
        for version in range(1, 10):
            self.assertEqual(qr._char_count_bits(version), 8, version)
        self.assertEqual(qr._char_count_bits(10), 16)

    def test_empty_payload_fits_version_one(self):
        for level in qr._EC_LEVELS:
            self.assertEqual(qr._choose_version(0, level), 1)


class DataBitsBoundaryTestCase(unittest.TestCase):
    """终止符按剩余容量截断，补齐位必须是 0（§41 的 payload 装配）。"""

    def test_terminator_shrinks_to_the_remaining_capacity(self):
        # 头 4 + 计数 8 + 一个字节 8 = 20 bit，永远差 4 bit 到整字节：终止符给
        # 几位取决于容量，剩下的由对齐补零——两条路都只写 0，长度恒为 24。
        for cap_cw in (2, 3, 4, 10):
            with self.subTest(cap_cw=cap_cw):
                self.assertEqual(len(qr._data_bits(b"\xff", 1, cap_cw)), 24)

    def test_padding_to_a_byte_boundary_writes_zeros_not_ones(self):
        # cap 2 = 16 bit < 20 bit 数据 → 终止符一位都不加，尾部 4 位全部来自
        # 对齐补齐；补 1 会把 0xEC/0x11 之前的那个码字改掉。
        bits = qr._data_bits(b"\xff", 1, 2)
        self.assertEqual(bits[:4], [0, 1, 0, 0])                 # byte mode
        self.assertEqual(bits[4:12], [0, 0, 0, 0, 0, 0, 0, 1])   # 计数 = 1
        self.assertEqual(bits[12:20], [1] * 8)                   # 载荷 0xff
        self.assertEqual(bits[20:], [0, 0, 0, 0])                # 对齐补齐全 0
        self.assertTrue(all(b in (0, 1) for b in bits))

    def test_roomy_capacity_ends_with_a_full_zero_terminator(self):
        bits = qr._data_bits(b"\xff", 1, 16)
        self.assertEqual(len(bits), 24)
        self.assertEqual(bits[20:], [0, 0, 0, 0])

    def test_two_byte_payload_terminator_and_alignment(self):
        bits = qr._data_bits(b"\x01\x02", 1, 4)
        self.assertEqual(len(bits), 32)
        self.assertEqual(bits[28:], [0, 0, 0, 0])
        self.assertTrue(all(b in (0, 1) for b in bits))

    def test_version_ten_uses_a_sixteen_bit_character_count(self):
        bits = qr._data_bits(b"\x01", 10, 20)
        self.assertEqual(bits[:4], [0, 1, 0, 0])
        self.assertEqual(bits[4:20], [0] * 15 + [1])             # 计数 = 1，16 bit


if __name__ == "__main__":
    unittest.main()
