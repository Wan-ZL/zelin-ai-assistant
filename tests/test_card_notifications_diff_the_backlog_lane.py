"""新卡通知与 §76.3 结算升级的差分源 = 潜在任务列（``debt[]``），不是恒空的
``needs_approval[]``。

契约：CONTRACT **§78** / **§78.6**「通知」条 / §40 + **§40.6 §78 修法**（新卡
合批通知的 diff 源换车道）/ §76.3（三条一次性结算升级）/ §2（``needs_approval``
恒空的墓碑）。

**这是整次退役里最容易静默失效的一行**（§78.3 与 D80.9 的原话）：
``needs_approval`` 这个键不改名、照常出现在 wire 上、只是永远空着——所以
``detect_transitions`` 继续 diff 它的话，**每一条新卡通知与三条结算通知都会无声
地停发，没有任何报错、CI 全绿**。本文件两个方向都钉：

1. 正向：卡在 ``debt[]`` 里出现 → 响；三个结算 bool 的 false→true 翻面 → 各响一次；
2. 反向：同一张卡**只**出现在退役的 ``needs_approval[]`` 里 → 一声都不响
   （那一列是墓碑，继续从它取信号就是把通知挂在一条没有卡的车道上）。

端到端那一半不手搓 wire dict：从**真注册表卡**经 ``dashboard.build_dashboard``
投影出两个快照再 diff——这样 dashboard 侧把行投错列、或 alerts 侧读错列，任一
处回归都红。纯函数 + 沙箱注册表，零子进程、零网络。

本文件钉的是**车道**（信号从哪一列来）。「哪些卡有资格响」是另一件事，由
``quiet_birth`` 承担，判例在 tests/test_quiet_birth_mirrors_the_retired_lane.py
——那里的候选一概不手搓，全部经真生产者（radar / apply_triage / 每日整理 /
digest）出生。
"""
import datetime as _dt
import unittest

from tests import TMP_HOME  # noqa: F401 - sandbox env before act imports

from act import actd
from act.lib import config, dashboard, notify
from act.lib.actd import alerts
from act.lib.registry import Requirement, State


def _row(rid="P-1", title="给老板回一封邮件", **kw) -> dict:
    row = {"id": rid, "title": title}
    row.update(kw)
    return row


def _dash(**lanes) -> dict:
    """一个最小 dashboard 快照：七条列名一个不少（``detect_transitions`` 对
    缺席的键按空列处理，但真投影永远发全集——照真形喂它）。"""
    base = {k: [] for k in ("needs_approval", "running", "needs_input", "review",
                            "completed", "debt", "trash")}
    for key, rows in lanes.items():
        base[key] = [dict(r) for r in rows]
    return base


def _kinds(msgs) -> list:
    return [m[3] for m in msgs]


def _titles(msgs) -> list:
    return [m[0] for m in msgs]


class NewCardDiffLaneTestCase(unittest.TestCase):
    """§40 / §40.6：新卡通知从 ``debt[]`` 的新行里长出来。"""

    def test_a_new_backlog_row_notifies_with_the_card_id(self):
        msgs = actd.detect_transitions(_dash(), _dash(debt=[_row()]))
        title, body, rid, kind = msgs[0]
        self.assertEqual(len(msgs), 1)
        self.assertEqual((title, body), notify.msg_new_card("给老板回一封邮件"))
        self.assertEqual(rid, "P-1")
        self.assertEqual(kind, notify.KIND_PROPOSAL)

    def test_a_row_in_the_retired_lane_alone_never_notifies(self):
        """墓碑列不是信号源。

        §2：``needs_approval[]`` 永久保留但恒空。一旦 diff 器还盯着它，本条
        会把「假信号」也钉出来——而真正的新卡（上一条）就再也不响了。"""
        self.assertEqual(
            actd.detect_transitions(_dash(), _dash(needs_approval=[_row()])), [])

    def test_a_row_persisting_in_the_backlog_is_silent(self):
        """只有**新出现**的行才响——每 pass 重播一遍就是通知风暴。"""
        curr = _dash(debt=[_row()])
        self.assertEqual(actd.detect_transitions(curr, curr), [])

    def test_more_than_two_fresh_backlog_rows_collapse_to_one_batch(self):
        """§40 合批：一次雷达回填曾经是 n 声连响。"""
        fresh = [_row(f"P-{i}", f"第 {i} 件事") for i in range(1, 5)]
        msgs = actd.detect_transitions(_dash(), _dash(debt=fresh))
        self.assertEqual(len(msgs), 1)
        self.assertEqual((msgs[0][0], msgs[0][1]), notify.msg_new_cards_batch(4))
        self.assertIsNone(msgs[0][2])

    def test_a_reraised_backlog_row_uses_the_returned_copy(self):
        """回锅卡认的是「你已经验收过的事」，不是一张全新的卡（§10 回锅）。"""
        msgs = actd.detect_transitions(
            _dash(), _dash(debt=[_row(reraised=True, reraised_note="还要中文版")]))
        self.assertEqual(len(msgs), 1)
        self.assertEqual((msgs[0][0], msgs[0][1]),
                         notify.msg_reraised("给老板回一封邮件", "还要中文版"))


class SettlementUpgradeDiffLaneTestCase(unittest.TestCase):
    """§76.3：三条结算升级各响一次，判据行长在 ``debt[]`` 上。"""

    def _flip(self, key, **extra):
        prev = _dash(debt=[_row(**{key: False})])
        curr = _dash(debt=[_row(**{key: True, **extra})])
        return actd.detect_transitions(prev, curr)

    def test_decision_due_flips_once(self):
        msgs = self._flip("decision_due")
        self.assertEqual(len(msgs), 1)
        self.assertEqual((msgs[0][0], msgs[0][1]),
                         notify.msg_deadline_due("给老板回一封邮件"))
        self.assertEqual(msgs[0][3], notify.KIND_PROPOSAL)

    def test_mention_escalated_flips_once(self):
        msgs = self._flip("mention_escalated", repeated=4)
        self.assertEqual(len(msgs), 1)
        self.assertEqual((msgs[0][0], msgs[0][1]),
                         notify.msg_repeated_unhandled("给老板回一封邮件", 4))

    def test_completion_hint_flips_once(self):
        msgs = self._flip("completion_hint", completion_hint={"note": "已经发了"})
        self.assertEqual(len(msgs), 1)
        self.assertEqual((msgs[0][0], msgs[0][1]),
                         notify.msg_completion_hint("给老板回一封邮件"))

    def test_a_flip_in_the_retired_lane_is_not_a_signal(self):
        prev = _dash(needs_approval=[_row(decision_due=False)])
        curr = _dash(needs_approval=[_row(decision_due=True)])
        self.assertEqual(actd.detect_transitions(prev, curr), [])

    def test_a_true_signal_that_stays_true_stops_ringing(self):
        curr = _dash(debt=[_row(decision_due=True)])
        self.assertEqual(actd.detect_transitions(curr, curr), [])


class EndToEndProjectionToNotificationTestCase(unittest.TestCase):
    """真卡 → 真投影 → 真 diff：投错列或读错列，任一处回归都红。"""

    def setUp(self):
        config.ensure_state_dirs()
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()
        self.cfg = config.Config()

    def _dash_of(self, req) -> dict:
        return dashboard.build_dashboard(reqs=[req], agents=[], cfg=self.cfg,
                                         archived=[])

    def test_a_detected_card_appearing_on_the_board_fires_the_new_card_ping(self):
        req = Requirement(id="P-500", title="雷达刚发现的一件事",
                          status=State.DETECTED.value)
        msgs = actd.detect_transitions(
            dashboard.build_dashboard(reqs=[], agents=[], cfg=self.cfg, archived=[]),
            self._dash_of(req))
        self.assertEqual([m[2] for m in msgs], ["P-500"])
        self.assertEqual(_kinds(msgs), [notify.KIND_PROPOSAL])

    def test_a_deadline_arriving_on_a_detected_card_fires_the_settlement_ping(self):
        """§76.2 的 ``decision_due`` 是 ``debt[]`` 行上的派生 bool（D80.8）——
        它要是没跟着卡搬到潜在任务列，这一条永远翻不了面。"""
        today = dashboard._today()
        future = Requirement(id="P-501", title="还没到期的那张卡",
                             status=State.DETECTED.value,
                             deadline=(today + _dt.timedelta(days=7)).isoformat())
        prev = self._dash_of(future)
        self.assertFalse(prev["debt"][0]["decision_due"])
        overdue = Requirement(id="P-501", title="还没到期的那张卡",
                              status=State.DETECTED.value,
                              deadline=(today - _dt.timedelta(days=1)).isoformat())
        curr = self._dash_of(overdue)
        self.assertTrue(curr["debt"][0]["decision_due"])
        msgs = actd.detect_transitions(prev, curr)
        self.assertEqual([m[2] for m in msgs], ["P-501"])
        self.assertEqual(_titles(msgs), [notify.msg_deadline_due("还没到期的那张卡")[0]])

    def test_the_wire_key_the_diff_reads_is_the_backlog_lane(self):
        """白盒一句：常量本身就是这次退役的单点。写在这里是为了让回归的
        诊断一眼可读（行为断言在上面每一条里）。"""
        self.assertEqual(alerts._CARD_LANE, "debt")
