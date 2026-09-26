"""actd.detect_transitions — dashboard diff -> notification tuples (P1-11).

Three transition classes produce (title, body, req_id, kind) 4-tuples (the req
id is what lets the Slack ✅-reaction approve the right R-id; kind tags the
class for per-event preferences — "review_ready" for fresh deliveries,
"proposal" for new / re-raised / batched cards (§28 追记, issue #29)):

  ∅ -> debt（潜在任务）         新卡待审批            (notify.msg_new_card)
  running -> review            "待验收：AI 已交付草稿"

**§78（issue #447 / owner 决策 D80）**：第一类的差分源从退役的 `needs_approval[]`
换成 `debt[]`（§40.6 修法 / §78.6）。这是整次退役里最容易静默失效的一处——键不
改名、投影照常出、只是永远空着，不换就等于新卡通知与 §76.3 的三条结算升级**全部
无声停发**，而 CI 全绿。本文件把既有的四类判决逐条重锚到那一列；退役本身的两条
新行为各有专属判例，不在这里重复（一个 behavior 一个文件）：
`tests/test_card_notifications_diff_the_backlog_lane.py`（只出现在墓碑列里的行
一声不响）与 `tests/test_limited_provenance_births_are_quiet.py`（§45 / D80.7
`quiet_birth` 落列不响）。

「running -> needs_input」类：retired v0.48.8（#119）——受阻会话由 reconcile
收割进待验收并当场发精确文案（msg_review_interrupted 等），diff 器对
needs_input 分区不再发声；interrupted 收割行也不冒充「已交付草稿」。

Everything else — first pass after daemon start (prev None), items persisting
in a partition, review appearing WITHOUT having been running — must stay
silent (no notification storms on restart).

Pure-function tests: no registry, no roster, no mocks.
"""
import unittest

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import actd
from act.lib import notify


def _new_card(title):
    t, b = notify.msg_new_card(title)
    return t, b


def _review_ready(name):
    t, b = notify.msg_review_ready(name)
    return t, b


def _dash(debt=(), running=(), needs_input=(), review=()):
    """一份看板快照。§78（issue #447 / owner 决策 D80）：机器卡住在潜在任务列
    （``debt[]``），``needs_approval[]`` 是恒空的墓碑键——它仍然摆在这里，钉的
    就是「diff 器绝不许再看那一列」：手搭一份带 ``needs_approval`` 行的快照正是
    让新卡通知**无声死掉**的那个形状（§40.6 §78 修法 / §78.6），用它写出来的
    判例会全部空转通过。"""
    return {
        "needs_approval": [],
        "debt": [dict(i) for i in debt],
        "running": [dict(i) for i in running],
        "needs_input": [dict(i) for i in needs_input],
        "review": [dict(i) for i in review],
    }


class FirstPassTestCase(unittest.TestCase):
    def test_prev_none_is_silent(self):
        # daemon (re)start: everything on the board is "new" vs no prev —
        # notifying would replay every card on every restart
        curr = _dash(debt=[{"id": "R-1", "title": "写周报"}],
                     review=[{"id": "R-2", "name": "任务二"}],
                     needs_input=[{"id": "R-3", "name": "任务三"}])
        self.assertEqual(actd.detect_transitions(None, curr), [])


class NewCardTestCase(unittest.TestCase):
    def test_new_backlog_card_notifies_with_req_id(self):
        prev = _dash()
        curr = _dash(debt=[{"id": "R-1", "title": "写周报"}])
        self.assertEqual(actd.detect_transitions(prev, curr),
                         [(*_new_card("写周报"), "R-1", notify.KIND_PROPOSAL)])

    def test_existing_card_stays_silent(self):
        prev = _dash(debt=[{"id": "R-1", "title": "写周报"}])
        curr = _dash(debt=[{"id": "R-1", "title": "写周报"}])
        self.assertEqual(actd.detect_transitions(prev, curr), [])

    def test_card_missing_title_falls_back_to_id(self):
        prev = _dash()
        curr = _dash(debt=[{"id": "R-1"}])
        self.assertEqual(actd.detect_transitions(prev, curr),
                         [(*_new_card("R-1"), "R-1", notify.KIND_PROPOSAL)])


class ReviewTransitionTestCase(unittest.TestCase):
    def test_running_to_review_notifies(self):
        prev = _dash(running=[{"id": "R-2", "name": "任务二"}])
        curr = _dash(review=[{"id": "R-2", "name": "任务二"}])
        self.assertEqual(actd.detect_transitions(prev, curr),
                         [(*_review_ready("任务二"), "R-2", "review_ready")])

    def test_review_without_prior_running_is_silent(self):
        # e.g. actd restarted while the item already sat in review upstream
        prev = _dash()
        curr = _dash(review=[{"id": "R-2", "name": "任务二"}])
        self.assertEqual(actd.detect_transitions(prev, curr), [])

    def test_from_review_rerun_settling_back_is_silent(self):
        # §30 v0.28.1: an already-delivered 待验收 card whose attach-reactivated
        # session was projected into 运行中 (from_review) settles back to review
        # when the session goes idle/done. That running->review bounce must NOT
        # fire "待验收：AI 已交付草稿" — it was never a fresh delivery (on main it
        # stayed in review the whole time). Guard = prev running row's from_review.
        prev = _dash(running=[{"id": "R-2", "name": "任务二", "from_review": True}])
        curr = _dash(review=[{"id": "R-2", "name": "任务二"}])
        self.assertEqual(actd.detect_transitions(prev, curr), [])

    def test_genuine_executing_to_review_still_notifies(self):
        # regression guard: a normal executing run finishing (prev running row
        # has NO from_review) still fires the fresh-delivery notification.
        prev = _dash(running=[{"id": "R-2", "name": "任务二"}])
        curr = _dash(review=[{"id": "R-2", "name": "任务二"}])
        self.assertEqual(actd.detect_transitions(prev, curr),
                         [(*_review_ready("任务二"), "R-2", "review_ready")])

    def test_review_persisting_is_silent(self):
        prev = _dash(review=[{"id": "R-2", "name": "任务二"}])
        curr = _dash(review=[{"id": "R-2", "name": "任务二"}])
        self.assertEqual(actd.detect_transitions(prev, curr), [])

    def test_review_item_without_name_uses_id_as_body(self):
        prev = _dash(running=[{"id": "R-2"}])
        curr = _dash(review=[{"id": "R-2"}])
        self.assertEqual(actd.detect_transitions(prev, curr),
                         [(*_review_ready("R-2"), "R-2", "review_ready")])


class NeedsInputTransitionTestCase(unittest.TestCase):
    def test_running_to_needs_input_is_silent(self):
        # #119：needs_input 只剩 §4 刹车行，executor 已发 msg_dispatch_halted——
        # diff 器对该分区零发声（msg_needs_input 已退役）
        prev = _dash(running=[{"id": "R-3", "name": "任务三"}])
        curr = _dash(needs_input=[{"id": "R-3", "name": "任务三"}])
        self.assertEqual(actd.detect_transitions(prev, curr), [])

    def test_interrupted_review_row_is_silent(self):
        # #119：中断收割（受阻/放弃救活 -> review）已由 reconcile 发过精确文案，
        # 「AI 已交付草稿」对它是虚报——interrupted 标记行跳过
        prev = _dash(running=[{"id": "R-4", "name": "任务四"}])
        curr = _dash(review=[{"id": "R-4", "name": "任务四", "interrupted": True}])
        self.assertEqual(actd.detect_transitions(prev, curr), [])

    def test_needs_input_persisting_is_silent(self):
        prev = _dash(needs_input=[{"id": "R-3", "name": "任务三"}])
        curr = _dash(needs_input=[{"id": "R-3", "name": "任务三"}])
        self.assertEqual(actd.detect_transitions(prev, curr), [])

    def test_needs_input_without_prior_running_is_silent(self):
        prev = _dash()
        curr = _dash(needs_input=[{"id": "R-3", "name": "任务三"}])
        self.assertEqual(actd.detect_transitions(prev, curr), [])


class CombinedAndEdgeTestCase(unittest.TestCase):
    def test_all_three_classes_in_one_pass(self):
        prev = _dash(running=[{"id": "R-2", "name": "任务二"},
                              {"id": "R-3", "name": "任务三"}])
        curr = _dash(debt=[{"id": "R-1", "title": "写周报"}],
                     review=[{"id": "R-2", "name": "任务二"}],
                     needs_input=[{"id": "R-3", "name": "任务三"}])
        msgs = actd.detect_transitions(prev, curr)
        # #119：needs_input 类不再发声——只剩新卡与 review 两类
        self.assertEqual(set(msgs), {
            (*_new_card("写周报"), "R-1", notify.KIND_PROPOSAL),
            (*_review_ready("任务二"), "R-2", "review_ready"),
        })

    def test_approval_to_running_is_silent(self):
        # approve is user-initiated — echoing it back would be noise
        prev = _dash(debt=[{"id": "R-1", "title": "写周报"}])
        curr = _dash(running=[{"id": "R-1", "name": "写周报"}])
        self.assertEqual(actd.detect_transitions(prev, curr), [])

    def test_missing_partitions_tolerated(self):
        # prev written by an older build without some partitions
        msgs = actd.detect_transitions({}, _dash(
            debt=[{"id": "R-1", "title": "写周报"}]))
        self.assertEqual(msgs, [(*_new_card("写周报"), "R-1", notify.KIND_PROPOSAL)])

    def test_items_without_id_are_ignored(self):
        prev = _dash()
        curr = _dash(debt=[{"title": "没有 id 的坏卡"}])
        self.assertEqual(actd.detect_transitions(prev, curr), [])


if __name__ == "__main__":
    unittest.main()
