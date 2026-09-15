"""qr — 每个 version × EC 级别都真能编出一张解得开的码（§41）。

现有判例只编 6–7 个 payload，于是 versions/EC 组合里绝大多数格子从未被走到：
块布局表、交错顺序、v ≥ 7 的 version information、10 号 version 的 16 位计数字段
错了都没人红（§57 夜报在这一段留下三百多个存活体）。

这里在 40 个 (version, EC) 组合的**容量边界**上各编一张码，然后用
`tests/qr_testkit.py`（判例侧独立解码器）把它解回来：尺寸、格式信息、
Reed–Solomon 校验子、以及原始载荷逐字节回读。整轮不到一秒。
"""
import unittest

from tests import TMP_HOME  # noqa: F401 - sandboxes AIASSISTANT_HOME first

from act.lib import qr
from tests import qr_testkit as kit

CAPACITY = kit.ISO_BYTE_CAPACITY      # ISO/IEC 18004 表 7 的 byte-mode 容量


def _payload(nbytes: int) -> str:
    # 可打印 ASCII，逐字节各异——回读时能看出错位。
    return "".join(chr(33 + (i % 90)) for i in range(nbytes))


class VersionSweepTestCase(unittest.TestCase):
    def test_every_version_and_level_encodes_to_the_expected_size(self):
        for ec, caps in CAPACITY.items():
            for version, nbytes in zip(range(1, 11), caps):
                with self.subTest(ec=ec, version=version):
                    m = qr.qr_matrix(_payload(nbytes), ec)
                    self.assertEqual(len(m), 4 * version + 17)
                    self.assertTrue(all(len(row) == len(m) for row in m))

    def test_every_version_and_level_survives_a_decode(self):
        for ec, caps in CAPACITY.items():
            for version, nbytes in zip(range(1, 11), caps):
                with self.subTest(ec=ec, version=version):
                    data = _payload(nbytes)
                    m = qr.qr_matrix(data, ec)
                    ec_bits, mask = kit.read_format(m)
                    self.assertEqual(ec_bits, qr._FORMAT_BITS[ec])
                    self.assertIn(mask, range(8))
                    blocks, ecpb = kit.deinterleave(kit.read_codewords(m), version, ec)
                    for i, block in enumerate(blocks):
                        self.assertEqual(kit.syndromes(block, ecpb), [0] * ecpb,
                                         f"block {i} of v{version}-{ec} is not clean")
                    mode, payload = kit.read_payload(m, ec)
                    self.assertEqual(mode, 0b0100)          # byte mode
                    self.assertEqual(payload, data.encode("utf-8"))

    def test_function_patterns_are_in_place_at_every_version(self):
        for ec, caps in CAPACITY.items():
            for version, nbytes in zip(range(1, 11), caps):
                with self.subTest(ec=ec, version=version):
                    m = qr.qr_matrix(_payload(nbytes), ec)
                    n = len(m)
                    for ox, oy in ((0, 0), (n - 7, 0), (0, n - 7)):
                        for dy in range(7):
                            for dx in range(7):
                                dist = max(abs(dx - 3), abs(dy - 3))
                                self.assertEqual(m[oy + dy][ox + dx], dist != 2,
                                                 (ox, oy, dx, dy))
                    for i in range(8, n - 8):
                        self.assertEqual(m[6][i], i % 2 == 0, i)
                        self.assertEqual(m[i][6], i % 2 == 0, i)
                    self.assertTrue(m[n - 8][8])            # 恒黑模块

    def test_alignment_patterns_sit_at_their_iso_centres(self):
        for version in range(2, 11):
            data = _payload(CAPACITY["M"][version - 1])
            m = qr.qr_matrix(data)
            n = len(m)
            centres = qr._ALIGN_POS[version]
            for a in centres:
                for b in centres:
                    if (a, b) in ((6, 6), (6, n - 7), (n - 7, 6)):
                        continue          # 与 finder 重叠的三个位置不画
                    with self.subTest(version=version, centre=(a, b)):
                        for dy in range(-2, 3):
                            for dx in range(-2, 3):
                                want = max(abs(dx), abs(dy)) != 1
                                self.assertEqual(m[b + dy][a + dx], want, (dx, dy))

    def test_utf8_multibyte_payload_round_trips(self):
        data = "配对二维码 · pairing"
        m = qr.qr_matrix(data)
        _mode, payload = kit.read_payload(m, "M")
        self.assertEqual(payload, data.encode("utf-8"))

    def test_empty_payload_is_encodable_and_decodes_back_to_nothing(self):
        m = qr.qr_matrix("")
        self.assertEqual(len(m), 21)
        mode, payload = kit.read_payload(m, "M")
        self.assertEqual((mode, payload), (0b0100, b""))


if __name__ == "__main__":
    unittest.main()
