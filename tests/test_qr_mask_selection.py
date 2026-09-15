"""qr — 八个掩码全部参选，罚分最低者胜，平手取小号（§41）。

`qr_matrix` 对同一份码字铸八张矩阵、逐张打罚分、留最低的那张。golden 判例钉的是
「最后长什么样」，钉不住「为什么是它」：把循环改成 `range(7)`（七号掩码从此不参选）
或把 `p < best[0]` 改成 `<=`（平手时改判后来者）都能悄悄活下来——只要 golden 里的
六个 payload 恰好都不撞这两种情形。

这里逐个 payload 钉住**被选中的掩码号**（从矩阵的格式信息里读回来，不看内部状态），
八个掩码各有一个代表 payload，外加一个两号掩码罚分打平的 payload 钉「取小号」。
掩码号与罚分表都是从当前编码器测出来再写死的常量——漂了就得有人解释为什么。
"""
import unittest

from tests import TMP_HOME  # noqa: F401 - sandboxes AIASSISTANT_HOME first

from act.lib import qr
from tests.qr_testkit import read_format as _read_format


def _penalties(data: str, ec: str = "M"):
    """八个掩码各自的罚分（复刻 qr_matrix 的循环，但不做选择）。"""
    raw = data.encode("utf-8")
    version = qr._choose_version(len(raw), ec)
    codewords = qr._encode_codewords(raw, version, ec)
    scores = []
    for mask in range(8):
        mx = qr._Matrix(version)
        mx.draw_function_patterns()
        mx.draw_codewords(codewords)
        mx.apply_mask(mask)
        mx._draw_format(mask, ec)
        scores.append(mx.penalty())
    return scores


# payload → 被选中的掩码号（R-206 实测；每个掩码至少有一个代表）。
_WINNERS = {
    "P001": 0, "P244": 1, "P002": 2, "P068": 3,
    "P069": 4, "P291": 5, "P000": 6, "P005": 7,
}
# 掩码 2 与掩码 4 罚分打平（均 354）——ISO 要求取号小的那个。
_TIE_PAYLOAD = "P030"
_TIE_MASKS = (2, 4)


class MaskSelectionTestCase(unittest.TestCase):
    def test_every_mask_can_win_and_the_winner_is_written_into_the_format_bits(self):
        for payload, want in _WINNERS.items():
            with self.subTest(payload=payload):
                scores = _penalties(payload)
                self.assertEqual(scores.index(min(scores)), want,
                                 f"{payload} 的最低罚分不再是掩码 {want}: {scores}")
                _ec_bits, mask = _read_format(qr.qr_matrix(payload))
                self.assertEqual(mask, want)

    def test_the_chosen_mask_really_is_the_cheapest_one(self):
        for payload in _WINNERS:
            with self.subTest(payload=payload):
                scores = _penalties(payload)
                _ec_bits, mask = _read_format(qr.qr_matrix(payload))
                self.assertEqual(scores[mask], min(scores))

    def test_a_tie_is_broken_towards_the_lower_mask_number(self):
        scores = _penalties(_TIE_PAYLOAD)
        low = min(scores)
        self.assertEqual([i for i, s in enumerate(scores) if s == low], list(_TIE_MASKS))
        _ec_bits, mask = _read_format(qr.qr_matrix(_TIE_PAYLOAD))
        self.assertEqual(mask, _TIE_MASKS[0])

    def test_format_bits_carry_the_error_correction_level(self):
        for level in qr._EC_LEVELS:
            with self.subTest(ec=level):
                ec_bits, _mask = _read_format(qr.qr_matrix("P000", level))
                self.assertEqual(ec_bits, qr._FORMAT_BITS[level])

    def test_error_correction_level_is_case_insensitive(self):
        self.assertEqual(qr.qr_matrix("P000", "h"), qr.qr_matrix("P000", "H"))


if __name__ == "__main__":
    unittest.main()
