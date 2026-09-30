"""退役车道上的落单卡照样出现在潜在任务列里，而且长着**完整卡面**——没有一张卡
因为状态退役而隐身，也没有一张卡因为换列而缩水。

契约：CONTRACT **§78** / **§78.1** 第 3 条硬承诺（straggler 投影永远留着）/
**§78.6**（``_SIMPLE_LANES`` 三态共用 ``_backlog_row``；卡面 = 老提案行全形）/
§2 §78 追记（``debt[]`` 行的 add-only 生长）/ §7 + issue #11（``egress`` 出机
披露恒随批准键）/ §50（``effective_tier`` 打字确认）/ §40.1（费用三件套）。

两条法条在这一个文件里合流：

1. **不隐身**：``card_sent`` 还在盘上的那一天，它就在潜在任务列里显示一天。
   归并扫描（§78.5）跑完后这条分支恒不命中，但**永远留着**——「状态还在、
   面没了」是宪法第 3 条不能接受的形态；
2. **不缩水**：「促成运行」那颗键现在就坐在这一行上。卡面要是藏了 ``egress``
   / ``effective_tier`` / ``cost_*``，那颗键就成了瞎批——卡面缩水 = 审批降级
   （§78.6 原话：「必须而不是可选」）。

判据用的是**三态行形逐键相等**，不是列一串键名：谁给 ``detected`` 行加了一个
新键却忘了落单卡与灰占位行，这条当场红。纯投影测试，不碰注册表文件。
"""
import unittest

from tests import TMP_HOME  # noqa: F401 - sandbox env before act imports

from act.lib import config, dashboard
from act.lib.registry import Requirement, State

_LANES = ("needs_approval", "running", "needs_input", "review", "completed",
          "debt", "trash")

# 一张「什么都有」的卡：卡面缩水的每一处都能在这上面看出来
_FULL = dict(id="P-1", title="给老板回一封邮件", summary="回复季度计划那封",
             tier="T2", type="engineering", hardness="hard", deadline="2026-10-01",
             repeated_mentions=3, cost_estimate_usd=4.0, green_sign_required=True,
             plan=["先读原文", "再起草"], outputs=["draft.md"],
             definition_of_done=["老板回了 ok"],
             sources=[{"channel": "gmail", "date": "2026-09-01", "quote": "原文"}],
             target_repo="/tmp/some-repo", delivery_mode="repo",
             origin_trust="external", notes="一行留痕")


def _board(*reqs) -> dict:
    return dashboard.build_dashboard(reqs=list(reqs), agents=[],
                                     cfg=config.Config(), archived=[])


def _card(status, **over) -> Requirement:
    return Requirement(**{**_FULL, "status": status, **over})


def _lanes_of(dash, rid) -> list:
    return [k for k in _LANES if any(r.get("id") == rid for r in dash.get(k, []))]


class StragglerIsVisibleTestCase(unittest.TestCase):
    def test_a_card_sent_card_lands_in_the_backlog_lane_and_nowhere_else(self):
        dash = _board(_card(State.CARD_SENT.value))
        self.assertEqual(_lanes_of(dash, "P-1"), ["debt"])

    def test_it_is_counted_in_the_backlog_count(self):
        dash = _board(_card(State.CARD_SENT.value))
        self.assertEqual(dash["counts"]["debt"], 1)
        self.assertEqual(dash["counts"]["needs_approval"], 0)

    def test_it_shares_the_lane_with_detected_and_raising_cards(self):
        dash = _board(_card(State.DETECTED.value, id="P-1"),
                      _card(State.RAISING.value, id="P-2"),
                      _card(State.CARD_SENT.value, id="P-3"))
        self.assertEqual(sorted(r["id"] for r in dash["debt"]), ["P-1", "P-2", "P-3"])
        self.assertEqual(dash["counts"]["debt"], 3)

    def test_a_card_trashed_from_the_retired_lane_reads_as_a_backlog_deletion(self):
        """回收站行的 ``kind`` 说的是「这张卡从哪一列被删的」——再报
        「建议」就是指着一条已经不存在的列。"""
        dash = _board(_card(State.TRASHED.value, prev_status=State.CARD_SENT.value,
                            trashed_at="2026-09-20T00:00:00Z", trash_reason="rejected"))
        self.assertEqual(dash["trash"][0]["kind"], "debt")


class FullCardFaceTestCase(unittest.TestCase):
    """三态一张卡面：谁给一态加键忘了另外两态，这里当场红。"""

    def _row(self, status, **over) -> dict:
        return _board(_card(status, **over))["debt"][0]

    def test_a_straggler_row_is_byte_for_byte_a_backlog_row(self):
        self.assertEqual(self._row(State.CARD_SENT.value),
                         self._row(State.DETECTED.value))

    def test_a_raising_row_differs_only_by_the_processing_placeholder(self):
        """灰占位只多两处：``processing: true`` 与「AI 研究中」的档位提示
        （扩写完成前说「一键可批」是假话）——字段一个不少。"""
        raising = self._row(State.RAISING.value)
        detected = self._row(State.DETECTED.value)
        self.assertEqual(set(raising), set(detected))
        diff = {k for k in detected if raising[k] != detected[k]}
        self.assertEqual(diff, {"processing", "tier_hint"})
        self.assertTrue(raising["processing"])
        self.assertFalse(detected["processing"])
        self.assertEqual(raising["tier_hint"], "AI 研究中")

    def test_the_approval_keys_are_all_on_the_row(self):
        """「促成运行」坐在这一行上，所以后果披露、生效档位、钱面必须同行。"""
        row = self._row(State.CARD_SENT.value)
        self.assertIn("egress", row)               # §7 / issue #11：批了什么出机
        self.assertEqual(row["effective_tier"], "T2")   # §50 打字确认的真档位
        self.assertEqual(row["cost_usd"], 4.0)
        self.assertEqual(row["cost_state"], "estimated")
        self.assertIn("show_cost", row)
        self.assertTrue(row["green_sign"])
        self.assertEqual(row["dod"], ["老板回了 ok"])
        self.assertEqual(row["plan"], ["先读原文", "再起草"])
        self.assertEqual(row["outputs"], ["draft.md"])
        self.assertEqual([s["channel"] for s in row["sources"]], ["gmail"])
        self.assertEqual(row["origin_trust"], "external")
        self.assertEqual(row["delivery_mode"], "repo")

    def test_the_old_debt_row_keys_survive_the_growth(self):
        """add-only **生长**：老债务行本来就有的键一个不许掉。"""
        row = self._row(State.CARD_SENT.value)
        for key in ("id", "title", "summary", "display_title", "hardness", "type",
                    "sources"):
            self.assertIn(key, row)
        self.assertEqual(row["type"], "engineering")

    def test_the_settlement_bools_now_live_on_this_row(self):
        """D80.8 作废了 §76.2 的「备选卡面没有 deadline 决策行」那一句——这里
        是 owner 唯一能拍板的卡面，两个派生 bool 必须跟过来。"""
        row = self._row(State.CARD_SENT.value)
        self.assertIn("decision_due", row)
        self.assertIn("mention_escalated", row)

    # 按法条**可以整键省略**的 add-only 字段（值为假 = 缺键，见 dashboard._opt）
    _OPTIONAL = frozenset({"notes_text", "origin_trust", "capture_id",
                           "completion_hint", "quiet_birth", "auto_dispatch_block",
                           "work_id", "reraised_note"})

    def test_a_bare_card_still_gets_the_decision_keys(self):
        """字段全空的一张卡也长完整卡面——「没有估价」「什么都不出机」由
        ``cost_state: unknown`` / ``egress: []`` 诚实表达，不是靠整键消失
        （缺键 = 客户端拿回落值，看起来像「没有风险」）。缺席只允许出现在
        按法条可省略的 add-only 尾巴上。"""
        bare = Requirement(id="P-9", title="只有一个标题", status=State.CARD_SENT.value)
        row = _board(bare)["debt"][0]
        missing = set(self._row(State.DETECTED.value)) - set(row)
        self.assertTrue(missing <= self._OPTIONAL, f"卡面缩水：{sorted(missing)}")
        for key in ("egress", "effective_tier", "cost_usd", "cost_state", "show_cost",
                    "tier", "tier_hint", "plan", "dod", "outputs", "sources",
                    "decision_due", "mention_escalated", "processing",
                    "delivery_mode", "display_id", "id_kind"):
            self.assertIn(key, row)
        self.assertEqual(row["cost_state"], "unknown")
        self.assertEqual(row["egress"], [])
