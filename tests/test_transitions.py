"""actd.detect_transitions — dashboard diff -> notification triples (P1-11).

Three transition classes produce (title, body, req_id) 3-tuples (the req id is
what lets the Slack ✅-reaction approve the right R-id):

  ∅ -> needs_approval          "有新需求待审批"     (notify.msg_new_card)
  running -> review            "待验收：AI 已交付草稿"
  running -> needs_input       "任务需要你输入"     (notify.msg_needs_input)

Everything else — first pass after daemon start (prev None), items persisting
in a partition, review/needs_input appearing WITHOUT having been running —
must stay silent (no notification storms on restart).

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


def _needs_input(name):
    t, b = notify.msg_needs_input(name)
    return t, b


def _dash(needs_approval=(), running=(), needs_input=(), review=()):
    return {
        "needs_approval": [dict(i) for i in needs_approval],
        "running": [dict(i) for i in running],
        "needs_input": [dict(i) for i in needs_input],
        "review": [dict(i) for i in review],
    }


class FirstPassTestCase(unittest.TestCase):
    def test_prev_none_is_silent(self):
        # daemon (re)start: everything on the board is "new" vs no prev —
        # notifying would replay every card on every restart
        curr = _dash(needs_approval=[{"id": "R-1", "title": "写周报"}],
                     review=[{"id": "R-2", "name": "任务二"}],
                     needs_input=[{"id": "R-3", "name": "任务三"}])
        self.assertEqual(actd.detect_transitions(None, curr), [])


class NewCardTestCase(unittest.TestCase):
    def test_new_card_sent_notifies_with_req_id(self):
        prev = _dash()
        curr = _dash(needs_approval=[{"id": "R-1", "title": "写周报"}])
        self.assertEqual(actd.detect_transitions(prev, curr),
                         [(*_new_card("写周报"), "R-1")])

    def test_existing_card_stays_silent(self):
        prev = _dash(needs_approval=[{"id": "R-1", "title": "写周报"}])
        curr = _dash(needs_approval=[{"id": "R-1", "title": "写周报"}])
        self.assertEqual(actd.detect_transitions(prev, curr), [])

    def test_card_missing_title_falls_back_to_id(self):
        prev = _dash()
        curr = _dash(needs_approval=[{"id": "R-1"}])
        self.assertEqual(actd.detect_transitions(prev, curr),
                         [(*_new_card("R-1"), "R-1")])


class ReviewTransitionTestCase(unittest.TestCase):
    def test_running_to_review_notifies(self):
        prev = _dash(running=[{"id": "R-2", "name": "任务二"}])
        curr = _dash(review=[{"id": "R-2", "name": "任务二"}])
        self.assertEqual(actd.detect_transitions(prev, curr),
                         [(*_review_ready("任务二"), "R-2")])

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
                         [(*_review_ready("任务二"), "R-2")])

    def test_review_persisting_is_silent(self):
        prev = _dash(review=[{"id": "R-2", "name": "任务二"}])
        curr = _dash(review=[{"id": "R-2", "name": "任务二"}])
        self.assertEqual(actd.detect_transitions(prev, curr), [])

    def test_review_item_without_name_uses_id_as_body(self):
        prev = _dash(running=[{"id": "R-2"}])
        curr = _dash(review=[{"id": "R-2"}])
        self.assertEqual(actd.detect_transitions(prev, curr),
                         [(*_review_ready("R-2"), "R-2")])


class NeedsInputTransitionTestCase(unittest.TestCase):
    def test_running_to_needs_input_notifies(self):
        prev = _dash(running=[{"id": "R-3", "name": "任务三"}])
        curr = _dash(needs_input=[{"id": "R-3", "name": "任务三"}])
        self.assertEqual(actd.detect_transitions(prev, curr),
                         [(*_needs_input("任务三"), "R-3")])

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
        curr = _dash(needs_approval=[{"id": "R-1", "title": "写周报"}],
                     review=[{"id": "R-2", "name": "任务二"}],
                     needs_input=[{"id": "R-3", "name": "任务三"}])
        msgs = actd.detect_transitions(prev, curr)
        self.assertEqual(set(msgs), {
            (*_new_card("写周报"), "R-1"),
            (*_review_ready("任务二"), "R-2"),
            (*_needs_input("任务三"), "R-3"),
        })

    def test_approval_to_running_is_silent(self):
        # approve is user-initiated — echoing it back would be noise
        prev = _dash(needs_approval=[{"id": "R-1", "title": "写周报"}])
        curr = _dash(running=[{"id": "R-1", "name": "写周报"}])
        self.assertEqual(actd.detect_transitions(prev, curr), [])

    def test_missing_partitions_tolerated(self):
        # prev written by an older build without some partitions
        msgs = actd.detect_transitions({}, _dash(
            needs_approval=[{"id": "R-1", "title": "写周报"}]))
        self.assertEqual(msgs, [(*_new_card("写周报"), "R-1")])

    def test_items_without_id_are_ignored(self):
        prev = _dash()
        curr = _dash(needs_approval=[{"title": "没有 id 的坏卡"}])
        self.assertEqual(actd.detect_transitions(prev, curr), [])


if __name__ == "__main__":
    unittest.main()
