"""fold_receipts — dev 列车改动行上的变异幸存体判例（CONTRACT §44.6 追记，issue #308）。

一条契约：**判不了通道 = 不是用户通道（fail-closed）**。`_is_user_channel` 的判据单源
是 `policy.CHANNEL_CLASS` 的 HAND 类；policy 出问题时回执这条尽力而为的观测面既不
打断 fold（宪法第 11 条），也不许因此把闸**打开**——一个读不出身份的通道落回执，等于
雷达 / 每日整理的自动并入又开始在看板上弹「已并入」，正是 #308 关掉的那件事。

等价体（不强杀）：`_is_user_channel` 的 `return False`→`return None`。两个调用点
（`record` 的 `if not …`、`load_recent` 的 `and …`）都只看真假，False 与 None
在这里没有可观察差异。

Runs entirely inside the sandbox AIASSISTANT_HOME (tests/__init__.py); no LLM.
"""
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env before act imports

from act.lib import config, fold_receipts, policy


class ChannelGateFailClosedTestCase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        config.FOLD_RECEIPTS_DIR.mkdir(parents=True, exist_ok=True)
        for p in config.FOLD_RECEIPTS_DIR.glob("*"):
            p.unlink()

    def _files(self):
        return sorted(p.name for p in config.FOLD_RECEIPTS_DIR.glob("*.json"))

    def test_a_hand_channel_still_gets_its_receipt(self):
        self.assertIsNotNone(fold_receipts.record("R-1", "quick_capture", "并进来的一句"))
        self.assertEqual(len(self._files()), 1)

    def test_an_unreadable_channel_class_writes_nothing(self):
        with mock.patch.object(policy, "channel_class", side_effect=RuntimeError("boom")):
            self.assertIsNone(fold_receipts.record("R-1", "quick_capture", "并进来的一句"))
        self.assertEqual(self._files(), [])

    def test_an_unreadable_channel_class_also_drops_the_row_on_read_back(self):
        fold_receipts.record("R-1", "quick_capture", "并进来的一句")
        with mock.patch.object(policy, "channel_class", side_effect=RuntimeError("boom")):
            self.assertEqual(fold_receipts.load_recent(), [])


if __name__ == "__main__":   # pragma: no cover
    unittest.main()
