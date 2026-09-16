"""alerts — dev 列车改动行上的变异幸存体判例（CONTRACT §76.3 / §40 / §28）。

三条契约，每条都是一个存活变异体改掉过的答案：

  * **§76.3 结算信号只对两个快照里都在的提案行响**：出生即带信号的新卡由 §40
    的新卡通知负责，不许在同一 pass 里再响第二声；而一张「上一版就在」的卡不因为
    它前面排着一张刚出生的卡就被漏掉——跳过新卡的那一步是 `continue`，不是 `break`。
    翻面是一次性的：信号在下一个 pass 里恒为真，通知却不再响。
  * **「被提了 N 次」的 N 是卡自己的计数**：缺席、零、读不懂一律读成 0——不是 ±1
    的兜底数，也不是 None（一条说「被提了 None 次」的横幅是在骗 owner）。
  * **§28 追记：`check_auth_failures` 的 `suppressed` 默认为假**——调用方不声明
    「失败类此刻被静音」时就要落 anti-nag 台账；只有被静音的那一趟才不落笔。

等价体（不强杀，理由记在这里）：`_judge_source` 的 `suppressed: bool = False`
默认值。本文件唯一的生产调用点 `check_radar_liveness` 永远按位置传实参，这个默认
值在生产路径上取不到——翻成 True 不产生任何可观察差异。

Runs entirely inside the sandbox AIASSISTANT_HOME (tests/__init__.py); no LLM.
"""
import unittest

from tests import TMP_HOME  # noqa: F401 - sandbox env before act imports

from act.lib import config, notify, registry
from act.lib.actd import alerts
from act.lib.registry import Requirement, State


def _na(*items):
    """A dashboard snapshot whose only populated partition is 待审批."""
    return {"needs_approval": [dict(i) for i in items], "running": [], "review": []}


def _proposal(title, rid, kind=notify.KIND_PROPOSAL):
    t, b = title
    return (t, b, rid, kind)


class SettlementFlipTestCase(unittest.TestCase):
    """§76.3：三条结算信号的一次性翻面。"""

    def test_a_signal_that_turns_true_on_a_known_card_rings_exactly_once(self):
        prev = _na({"id": "R-1", "title": "报销"})
        curr = _na({"id": "R-1", "title": "报销", "completion_hint": True})
        self.assertEqual(
            alerts.detect_transitions(prev, curr),
            [_proposal(notify.msg_completion_hint("报销"), "R-1")])
        # 下一个 pass 信号仍为真——投影是幂等的，通知不是
        self.assertEqual(alerts.detect_transitions(curr, curr), [])

    def test_a_card_born_with_signals_only_gets_the_new_card_copy(self):
        """新卡的信号由 §40 新卡通知代言：不许在同一 pass 里再响三声。"""
        curr = _na({"id": "R-2", "title": "续签", "completion_hint": True,
                    "decision_due": True, "mention_escalated": True, "repeated": 4})
        self.assertEqual(alerts.detect_transitions(_na(), curr),
                         [_proposal(notify.msg_new_card("续签"), "R-2")])

    def test_a_fresh_card_in_front_does_not_stop_the_settlement_scan(self):
        prev = _na({"id": "R-2", "title": "续签"})
        curr = _na({"id": "R-1", "title": "报销"},                        # 本 pass 新出生 → 跳过
                   {"id": "R-2", "title": "续签", "decision_due": True})  # 老卡 → 该响
        self.assertEqual(
            alerts.detect_transitions(prev, curr),
            [_proposal(notify.msg_new_card("报销"), "R-1"),
             _proposal(notify.msg_deadline_due("续签"), "R-2")])


class RepeatedCountTestCase(unittest.TestCase):
    """§76.3 第三条：横幅里的次数是卡上的计数，读不出就是 0。"""

    def _escalate(self, rid, **fields):
        prev = _na(dict({"id": rid, "title": "催进度"}, **fields))
        curr = _na(dict({"id": rid, "title": "催进度", "mention_escalated": True}, **fields))
        return alerts.detect_transitions(prev, curr)

    def test_the_copy_carries_the_cards_own_count(self):
        self.assertEqual(self._escalate("R-3", repeated=5),
                         [_proposal(notify.msg_repeated_unhandled("催进度", 5), "R-3")])

    def test_a_missing_count_reads_as_zero(self):
        self.assertEqual(self._escalate("R-4"),
                         [_proposal(notify.msg_repeated_unhandled("催进度", 0), "R-4")])

    def test_an_unparsable_count_reads_as_zero(self):
        self.assertEqual(self._escalate("R-5", repeated="五次"),
                         [_proposal(notify.msg_repeated_unhandled("催进度", 0), "R-5")])


_AUTH_LOG = ("# dispatch R-1 @ 2026-09-10T09:00:00\n=== STDERR ===\n"
             "authentication_error: invalid api key\n")


class AuthScanDefaultTestCase(unittest.TestCase):
    """§28 追记：静音是调用方显式声明的事，默认那一趟照常落 anti-nag 台账。"""

    def setUp(self):
        config.ensure_state_dirs()
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()
        self.logs = config.STATE_DIR / "alerts-train-logs"
        self.logs.mkdir(parents=True, exist_ok=True)

    def test_an_unsuppressed_scan_spends_the_anti_nag_ledger(self):
        log = self.logs / "auth.log"
        log.write_text(_AUTH_LOG, encoding="utf-8")
        registry.save(Requirement(id="R-1", title="登录坏了", status=State.EXECUTING.value,
                                  execution={"session_id": "sid-1", "log": str(log)}))
        notified = set()
        self.assertEqual(alerts.check_auth_failures(notified),   # 不传 suppressed
                         [notify.msg_auth("登录坏了")])
        self.assertEqual(notified, {"R-1"})
        # 台账花掉了 → 同一张卡本进程内不再响
        self.assertEqual(alerts.check_auth_failures(notified), [])


if __name__ == "__main__":   # pragma: no cover
    unittest.main()
