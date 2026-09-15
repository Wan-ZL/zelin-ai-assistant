"""§44.4 静默并入的看板回执按**副卡出身**发，不按「静默并入」这条路的名字发.

§44.6 追记（issue #308）把回执收窄到用户通道后，本模块此前写死的 "radar" 会让
owner 刚敲进来的卡被折走时一声不响地消失——§44.1/§44.4 只要求副卡处在 LIGHT
状态，从不问它的出身，所以 card_sent 里一张手打卡完全可能被折进主卡。那正是
§44.6 当初要堵的 8-07 黑洞，于是回执通道改读副卡的 sources（policy.CHANNEL_CLASS
的 HAND 类，any 聚合）。

判例：手打副卡 → 有回执；雷达/外部出身的副卡 → 无回执；混合来源里只要有一条
手打就算手打。无网络、无真 claude。
"""
import unittest
from pathlib import Path

from tests import TMP_HOME  # noqa: F401 - sandbox env before act imports

from act.lib import config, fold_receipts, registry, silent_merge
from act.lib.registry import Requirement, State

DUP_A = "整理 EB-1A 推荐信 recommendation letters 清单 wegreened"
DUP_B = "EB-1A 推荐信 recommendation letters wegreened 跟进"


def _clean():
    config.ensure_state_dirs()
    for p in config.REGISTRY_DIR.glob("*.yaml"):
        p.unlink()
    frdir = Path(config.FOLD_RECEIPTS_DIR)
    if frdir.exists():
        for p in frdir.glob("*.json"):
            p.unlink()


def _seed(rid, summary, sources, status=State.CARD_SENT.value):
    r = Requirement(id=rid, title=rid, status=status, summary=summary,
                    sources=sources)
    registry.save(r)
    return r


def _src(channel):
    return {"who": "zelin", "channel": channel, "date": "2026-09-09", "quote": "x"}


class SilentMergeReceiptProvenanceTestCase(unittest.TestCase):
    def setUp(self):
        _clean()
        self.addCleanup(_clean)

    def _merge(self, secondary_sources):
        primary = _seed("R-001", DUP_A, [_src("meeting")])
        secondary = _seed("R-002", DUP_B, secondary_sources)
        self.assertTrue(silent_merge.execute(primary, secondary, "补充了预算数字"))
        return fold_receipts.load_recent()

    def test_hand_typed_secondary_still_gets_a_receipt(self):
        # owner 敲进来的卡被折走 = 8-07 黑洞的原型，必须留痕
        got = self._merge([_src("quick")])
        self.assertEqual(len(got), 1)
        self.assertEqual((got[0]["req"], got[0]["channel"]), ("R-001", "quick"))

    def test_radar_born_secondary_is_silent(self):
        self.assertEqual(self._merge([_src("meeting")]), [])

    def test_external_secondary_is_silent(self):
        self.assertEqual(self._merge([_src("slack")]), [])

    def test_mixed_sources_with_one_hand_entry_still_get_a_receipt(self):
        # 聚合取 any（与 §50 classify_origin 的最小信任相反）：手打过就是手打过
        got = self._merge([_src("slack"), _src("quick_capture")])
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["channel"], "quick")

    def test_sourceless_and_malformed_secondaries_are_silent(self):
        self.assertEqual(self._merge([]), [])
        _clean()
        self.assertEqual(self._merge(["not-a-dict"]), [])

    def test_channel_helper_never_raises_on_odd_shapes(self):
        odd = Requirement(id="R-900", title="x", sources="not-a-list")
        self.assertEqual(silent_merge._receipt_channel(odd), "radar")


if __name__ == "__main__":
    unittest.main()
