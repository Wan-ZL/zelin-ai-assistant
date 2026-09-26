"""退役只是把两列并成一列——它不许顺手开任何一扇新门。

契约：CONTRACT **§78** / **§78.10**（边界：本节明确不做的每一件事）/ §78.2
D80.14（iOS 明确不在射程内）/ §10（动词全集零删除零新增；``defer`` 墓碑）/
§32.2 + §5.4（迟到 / 过期的手机动作诚实 no-op，不是静默吞）/ §0 第 12 条
（``detected → approved`` 仍然是一次人的点击，唯一例外只有 §65）。

为什么边界也要判例：一次「顺手」的重构最容易在这里出事——手边正好有个动词
要改落点，于是多加一个动词；正好在动通知，于是多加一个分类；正好手机那一页
没跟上，于是让 server 悄悄把过期动作吞掉。每一件都不会报错，每一件都在退役
的射程之外。本文件把四条边界钉死：

1. **不开新 inbox 动词**：「促成运行」= 既有的 ``approve``，词表逐字不变；
2. **不删旧动词**：``defer`` 的源状态退役了，动词名与诚实 ack 路径留着
   （老手机 / 老 inbox 文件 / 云同步重放还会发它）；
3. **过期的手机动作诚实 no-op**：手机仍渲染那一页退役的提案列，它钉的
   ``expected_status: "card_sent"`` 对不上就报 ``noop``，绝不改卡；
4. **不加新通知分类**：三条结算信号仍走 ``KIND_PROPOSAL``，持久化的偏好键
   ``notify_proposals`` 一个字母不动。

沙箱 AIASSISTANT_HOME；notify / executor 全 mock，零子进程、零网络。
"""
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env before act imports

from act import actd
from act.lib import config, notify, registry
from act.lib.registry import Requirement, State
from server import inbox_writer

# §10 的动词全集（truth = server/inbox_writer.CARD_VERBS）——退役一个不删、一个不加
_CARD_VERBS = frozenset({
    "approve", "reject", "comment", "defer", "raise", "trash", "restore",
    "pin", "accept", "rework", "done_external", "abort_execution",
    "stop_to_review", "revert_review", "archive", "unarchive",
    "merge_apply", "merge_dismiss",
})


class VerbInventoryTestCase(unittest.TestCase):
    def test_the_card_verb_vocabulary_is_unchanged(self):
        self.assertEqual(inbox_writer.CARD_VERBS, _CARD_VERBS)

    def test_the_promotion_uses_the_existing_approve_verb(self):
        """「促成运行」不是新动词——`decisions._approve` 本来就收 detected。"""
        self.assertIn("approve", inbox_writer.ALLOWED_ACTIONS)
        self.assertNotIn("promote", inbox_writer.ALLOWED_ACTIONS)
        self.assertNotIn("run_it", inbox_writer.ALLOWED_ACTIONS)

    def test_the_retired_defer_verb_is_still_accepted_on_the_wire(self):
        """动词名与 ack 路径保留：迟到 / 重放的 inbox 文件落进幂等 no-op，
        比 ``unknown`` 诚实（老客户端、云同步重放照旧被正确回执）。"""
        self.assertIn("defer", inbox_writer.ALLOWED_ACTIONS)


class DecisionsOnTheOneLaneTestCase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()
        mock.patch.object(actd.notify, "notify", mock.Mock(return_value=True)).start()
        self.addCleanup(mock.patch.stopall)

    def _mk(self, rid, status, **kw):
        req = Requirement(id=rid, title="边界测试卡", status=status, **kw)
        registry.save(req)
        return req

    def test_approve_promotes_a_detected_card_in_one_click(self):
        self._mk("P-600", State.DETECTED.value)
        with registry.acting_as("user"):
            ack = actd._apply_decision(registry.load("P-600"), "approve", None)
        self.assertEqual(ack, "running")
        self.assertEqual(registry.load("P-600").status, State.APPROVED.value)

    def test_approve_still_works_on_a_retired_straggler(self):
        """落单卡也得批得动——否则归并扫描跑完之前它是一张点不动的卡。"""
        self._mk("P-601", State.CARD_SENT.value)
        with registry.acting_as("user"):
            actd._apply_decision(registry.load("P-601"), "approve", None)
        self.assertEqual(registry.load("P-601").status, State.APPROVED.value)

    def test_defer_is_a_permanent_no_op_now_that_its_source_state_is_retired(self):
        """`defer`（暂缓）的全部语义是 ``card_sent → detected``。卡本来就住在
        潜在任务列，这一下自此恒 no-op——但**不是**报 unknown。"""
        self._mk("P-602", State.DETECTED.value)
        ack = actd._apply_decision(registry.load("P-602"), "defer", None)
        self.assertEqual(ack, "noop")
        self.assertEqual(registry.load("P-602").status, State.DETECTED.value)

    def test_an_unknown_verb_is_still_reported_as_unknown(self):
        """诚实分级：退役的动词 no-op、没有的动词 unknown——两者不许混。"""
        self._mk("P-603", State.DETECTED.value)
        self.assertEqual(
            actd._apply_decision(registry.load("P-603"), "promote", None), "unknown")


class StalePhonePinTestCase(DecisionsOnTheOneLaneTestCase):
    """D80.14：手机上仍显示那一页退役的提案列，它钉的 ``card_sent`` 对不上就
    诚实过期——**明知且接受**的代价，但绝不能变成「静默改卡」。"""

    def test_a_stale_card_sent_pin_no_ops_a_comment(self):
        self._mk("P-610", State.EXECUTING.value, execution={"session_id": "sess-1"})
        ack = actd._apply_decision(registry.load("P-610"), "comment", "手机上打的字",
                                   expected_status=State.CARD_SENT.value)
        self.assertEqual(ack, "noop")
        req = registry.load("P-610")
        self.assertEqual(req.status, State.EXECUTING.value)
        self.assertFalse(req.notes)        # 一个字都没落卡

    def test_a_stale_card_sent_pin_no_ops_a_raise(self):
        self._mk("P-611", State.APPROVED.value)
        ack = actd._apply_decision(registry.load("P-611"), "raise", None,
                                   expected_status=State.CARD_SENT.value)
        self.assertEqual(ack, "noop")
        self.assertEqual(registry.load("P-611").status, State.APPROVED.value)

    def test_a_matching_card_sent_pin_still_applies_to_a_straggler(self):
        """过期的是**对不上**的钉，不是 ``card_sent`` 这个值本身。"""
        self._mk("P-612", State.CARD_SENT.value)
        ack = actd._apply_decision(registry.load("P-612"), "comment", "改个方向",
                                   expected_status=State.CARD_SENT.value)
        self.assertEqual(ack, "running")
        self.assertEqual(registry.load("P-612").status, State.DETECTED.value)

    def test_the_stale_guard_itself_treats_the_retired_value_like_any_other(self):
        card = Requirement(id="P-613", title="边界测试卡", status=State.DETECTED.value)
        self.assertFalse(actd._precondition_ok(card, State.CARD_SENT.value))
        self.assertTrue(actd._precondition_ok(card, State.DETECTED.value))
        self.assertTrue(actd._precondition_ok(card, None))


class NotificationVocabularyTestCase(unittest.TestCase):
    def test_no_new_notification_kind_was_added(self):
        kinds = {v for k, v in vars(notify).items()
                 if k.startswith("KIND_") and isinstance(v, str)}
        self.assertEqual(kinds, {"proposal", "review_ready", "needs_input",
                                 "failure", "receipt", "review_stale"})

    def test_the_persisted_preference_token_is_untouched(self):
        """`notify_proposals` / `KIND_PROPOSAL` 是**存储层字面量**（用户的偏好
        与历史事件行按它归档）——退役只改它周围的话，不动它本身。"""
        self.assertEqual(notify.KIND_PROPOSAL, "proposal")
        self.assertEqual(notify.CATEGORY_PREFERENCE[notify.KIND_PROPOSAL],
                         "notify_proposals")

    def test_the_settlement_signals_still_ride_the_same_kind(self):
        prev = {"needs_approval": [], "running": [], "needs_input": [], "review": [],
                "completed": [], "debt": [{"id": "P-1", "title": "一张卡"}],
                "trash": []}
        curr = {**prev, "debt": [{"id": "P-1", "title": "一张卡", "decision_due": True,
                                  "mention_escalated": True, "repeated": 5,
                                  "completion_hint": {"note": "好像做完了"}}]}
        msgs = actd.detect_transitions(prev, curr)
        self.assertEqual(len(msgs), 3)
        self.assertEqual({m[3] for m in msgs}, {notify.KIND_PROPOSAL})
