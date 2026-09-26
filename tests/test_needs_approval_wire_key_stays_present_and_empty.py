"""``needs_approval`` 这个 wire 键退役了，但**永远不消失**：恒发 ``[]``，
``counts.needs_approval`` 恒 ``0``。

契约：CONTRACT **§78** / **§78.2** D80.1（wire key 留着）/ **§78.6**（``_LANES``
七项与顺序一字不动）/ §2 §78 追记（``needs_approval[]`` 恒空的墓碑）/ CONTRACT
header 的 add-only 纪律（跨组件 JSON 字段只增不改不删）/ §0 第 6 条。

退役一条车道有两种做法，只有一种是对的：把键删掉，冻结的原生 app（D3，
`Contract.swift` / `BoardLane.allCases` 一个字节都不许改）与任何缓存了 wire
schema 的老客户端就整份 payload 解不开——一次「清理」换来一次解码崩溃。正确的
做法是**留着并恒空**：老 reader 读到一个合法的空列，不崩、不缺键、什么都不显示。

所以这里钉的不是「这一列现在是空的」（那是同义反复），而是**它必须还在**——
以及没有任何状态的卡能落进去。``_LANES`` 的顺序一并钉：它是 wire 上的列序，
不是内部实现细节。纯投影测试 + 一次真落盘 JSON 往返。
"""
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tests import TMP_HOME  # noqa: F401 - sandbox env before act imports

from act.lib import config, dashboard
from act.lib.registry import Requirement, State

# wire 上的列序（truth = act/lib/dashboard._LANES）——退役不许动它
_WIRE_LANES = ("needs_approval", "running", "needs_input", "review", "completed",
               "debt", "trash")


def _board(*reqs) -> dict:
    return dashboard.build_dashboard(reqs=list(reqs), agents=[],
                                     cfg=config.Config(), archived=[])


class TombstonedLaneKeyTestCase(unittest.TestCase):
    def test_the_key_exists_and_is_an_empty_list_on_an_empty_board(self):
        dash = _board()
        self.assertIn("needs_approval", dash)
        self.assertEqual(dash["needs_approval"], [])

    def test_the_count_exists_and_is_zero(self):
        dash = _board()
        self.assertIn("needs_approval", dash["counts"])
        self.assertEqual(dash["counts"]["needs_approval"], 0)

    def test_no_status_whatsoever_lands_in_the_retired_lane(self):
        """整张状态词表走一遍——包括退役的 ``card_sent`` 自己。"""
        for i, status in enumerate(State, start=1):
            with self.subTest(status=status.value):
                dash = _board(Requirement(id=f"P-{i}", title="一张卡",
                                          status=status.value))
                self.assertEqual(dash["needs_approval"], [])
                self.assertEqual(dash["counts"]["needs_approval"], 0)

    def test_a_full_board_still_reports_an_empty_retired_lane(self):
        dash = _board(
            Requirement(id="P-1", title="潜在任务", status=State.DETECTED.value),
            Requirement(id="P-2", title="研究中", status=State.RAISING.value),
            Requirement(id="P-3", title="落单卡", status=State.CARD_SENT.value),
            Requirement(id="P-4", title="排队中", status=State.APPROVED.value),
            Requirement(id="P-5", title="已验收", status=State.DELIVERED.value),
            Requirement(id="P-6", title="回收站", status=State.TRASHED.value,
                        prev_status=State.DETECTED.value))
        self.assertEqual(dash["needs_approval"], [])
        self.assertEqual(dash["counts"]["needs_approval"], 0)
        self.assertEqual(dash["counts"]["debt"], 3)   # 三态同住潜在任务列

    def test_the_count_is_derived_not_hardcoded(self):
        """恒 0 是「这一列有几张卡」的**答案**，不是写死的字面量——counts 的
        口径与分区长度在每一列上都必须一致（§2 的既有不变量）。"""
        dash = _board(Requirement(id="P-1", title="一张卡",
                                  status=State.DETECTED.value))
        for lane in _WIRE_LANES:
            self.assertEqual(dash["counts"][lane], len(dash[lane]), msg=lane)


class WireShapeTestCase(unittest.TestCase):
    def test_the_lane_order_on_the_wire_is_unchanged(self):
        self.assertEqual(dashboard._LANES, _WIRE_LANES)

    def test_every_lane_key_is_present_on_an_empty_board(self):
        dash = _board()
        for lane in _WIRE_LANES:
            self.assertIn(lane, dash, msg=lane)
            self.assertEqual(dash[lane], [], msg=lane)

    def test_the_written_json_still_carries_the_retired_key(self):
        """真落盘一次：老 reader 拿到的是**文件**，不是内存 dict。"""
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "dashboard.json"
            dashboard.write_dashboard(_board(
                Requirement(id="P-1", title="一张卡", status=State.DETECTED.value)),
                path=path)
            on_disk = json.loads(path.read_text(encoding="utf-8"))
        self.assertIn("needs_approval", on_disk)
        self.assertEqual(on_disk["needs_approval"], [])
        self.assertEqual(on_disk["counts"]["needs_approval"], 0)
        self.assertEqual([r["id"] for r in on_disk["debt"]], ["P-1"])
