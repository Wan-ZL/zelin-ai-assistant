"""每一条「退回」的落点都是潜在任务列——一条也不许还指着退役的提案车道。

契约：CONTRACT **§78** / **§78.3** 的四条退回路径（`abort_execution` / 评论折叠 /
re-raise 回锅 / `restore`）/ **§78.2** D80.10（回程票夹逼，对 issue 原文的一处
明确偏离）/ §9 §78 追记（`prev_status` 是历史事实，只钳复位目标、不改写字段）/
§10（动词全集零删除，换的只是「到哪去」）/ §65.1（通道关掉时免批派发的撤回）。

为什么这几条要一起钉：`card_sent` 退役之后它**没有面**了。任何一条还往那儿
写的退回路径，效果都是「owner 点了停止 / 改了一句话 / 从回收站恢复了一张卡，
然后卡消失了」——状态合法、写盘成功、CI 全绿，只是看板上再也找不到它
（straggler 投影是兜底的第二层，但兜底不是设计：卡本身必须真的落在活着的那
一列，否则它连「促成运行」都点不了）。

沙箱 AIASSISTANT_HOME；executor 全 mock（stop_session 走注入缝），零子进程。
"""
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env before act imports
from tests.self_improve_testkit import lane_card

from act import actd
from act.lib import config, registry, self_improve
from act.lib.registry import Requirement, State


def _mk(rid, status, **kw):
    req = Requirement(id=rid, title="退回落点测试", status=status, **kw)
    registry.save(req)
    return req


class LandingPadBase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()
        mock.patch.object(actd.notify, "notify", mock.Mock(return_value=True)).start()
        self.addCleanup(mock.patch.stopall)

    def _decide(self, req, action, comment=None):
        with mock.patch.object(actd.executor, "stop_session_confirmed",
                               mock.Mock(return_value=(True, True, "stopped"))), \
                mock.patch.object(actd.executor, "stop_session",
                                  mock.Mock(return_value=True)):
            actd._apply_decision(req, action, comment)
        return registry.load(req.id)


class AbortExecutionTestCase(LandingPadBase):
    """「停止」= 丢弃这一轮、回潜在任务列重新决定（文案「退回潜在任务」）。"""

    def test_every_abortable_state_lands_in_the_backlog(self):
        for i, status in enumerate((State.APPROVED.value, State.EXECUTING.value,
                                    State.REVIEW.value), start=1):
            with self.subTest(status=status):
                rid = f"P-30{i}"
                _mk(rid, status, execution={"session_id": f"sess-{i}"})
                req = self._decide(registry.load(rid), "abort_execution")
                self.assertEqual(req.status, State.DETECTED.value)

    def test_the_dispatch_brake_ledger_is_cleared_on_the_way_back(self):
        """§4.1：卡带着刹车回到潜在任务列，§65 免批会把它原样再推进 approved，
        然后永远停在「需输入」（2026-09-01 审查复现）。"""
        _mk("P-310", State.EXECUTING.value,
            execution={"session_id": "sess-9", "dispatch_halted": True,
                       "dispatch_attempts": 3, "last_error": "boom"})
        req = self._decide(registry.load("P-310"), "abort_execution")
        self.assertEqual(req.status, State.DETECTED.value)
        ex = req.execution or {}
        self.assertNotIn("dispatch_halted", ex)
        self.assertNotIn("last_error", ex)


class CommentFoldTestCase(LandingPadBase):
    """修改 = 折进 plan/notes + 退回重批，落点同样是潜在任务列。"""

    def test_a_comment_on_a_raising_card_folds_back_to_the_backlog(self):
        _mk("P-320", State.RAISING.value)
        req = self._decide(registry.load("P-320"), "comment", "换个思路")
        self.assertEqual(req.status, State.DETECTED.value)

    def test_a_comment_on_a_retired_straggler_folds_into_the_backlog(self):
        """存量落单卡上的「修改」把它一并救进活着的那一列。"""
        _mk("P-321", State.CARD_SENT.value)
        req = self._decide(registry.load("P-321"), "comment", "换个思路")
        self.assertEqual(req.status, State.DETECTED.value)

    def test_a_comment_on_a_detected_card_keeps_it_in_the_backlog(self):
        _mk("P-322", State.DETECTED.value)
        req = self._decide(registry.load("P-322"), "comment", "补一句")
        self.assertEqual(req.status, State.DETECTED.value)
        self.assertIn("补一句", req.notes or "")   # 折叠真的落卡了

    def test_a_comment_never_resurrects_a_terminal_card(self):
        """§32.2：迟到的评论不许把回收站里的卡复活成一张活的潜在任务卡。"""
        _mk("P-323", State.TRASHED.value, prev_status=State.DETECTED.value)
        req = self._decide(registry.load("P-323"), "comment", "迟到的话")
        self.assertEqual(req.status, State.TRASHED.value)


class ReRaiseTestCase(LandingPadBase):
    """§10 回锅：已验收的卡被重新提起 → 翻回潜在任务列等 owner 再拍一次。"""

    def test_a_resolved_card_flips_back_into_the_backlog(self):
        parent = _mk("P-330", State.DELIVERED.value,
                     sources=[{"channel": "slack", "date": "2026-08-01", "quote": "原文"}])
        child = Requirement(id=registry.next_id(), title="退回落点测试",
                            summary="又提了一次，还带新要求",
                            sources=[{"channel": "slack", "date": "2026-09-26",
                                      "quote": "再提"}])
        kind, saved = registry.reraise_or_followup(
            parent, child, note="这次还要中文版", same_task=True, actionable=True)
        self.assertEqual(kind, "reraised")
        self.assertEqual(saved.status, State.DETECTED.value)
        self.assertEqual(registry.load("P-330").status, State.DETECTED.value)


class FrozenLaneWithdrawalTestCase(LandingPadBase):
    """§65.1：通道关掉时，免批批准但还没派出的 lane 卡退回潜在任务列。"""

    def setUp(self):
        super().setUp()
        self_improve.lane_state_path().unlink(missing_ok=True)
        self.addCleanup(lambda: config.CONFIG_PATH.unlink(missing_ok=True))

    def test_the_withdrawal_lands_in_the_backlog(self):
        registry.save(lane_card("P-340", status=State.APPROVED.value,
                                execution={"auto_dispatched": True}))
        ex_mock = mock.MagicMock()
        with mock.patch.object(actd, "executor", ex_mock):
            self.assertEqual(actd.dispatch_approved(config.Config(self_improve_enabled=False)), 0)
        ex_mock.dispatch.assert_not_called()
        req = registry.load("P-340")
        self.assertEqual(req.status, State.DETECTED.value)
        self.assertNotIn("auto_dispatched", req.execution or {})
        self.assertIn("退回潜在任务", req.notes)


class RestoreClampsTheReturnTicketTestCase(LandingPadBase):
    """D80.10：回收站的回程票遇到退役状态就地夹逼到 ``detected``。

    `prev_status` 本身是历史事实（宪法第 6 条），迁移侧刻意不改写它；夹逼只
    动**这一次复位的目标状态**——否则 owner 一点「恢复」，卡就回到一条已经
    没有面的车道，那是「可逆」的反面（宪法第 2 条）。"""

    def test_a_card_trashed_from_the_retired_lane_restores_into_the_backlog(self):
        req = _mk("P-350", State.CARD_SENT.value)
        registry.trash(req, "rejected")
        self.assertEqual(registry.load("P-350").prev_status, State.CARD_SENT.value)
        restored = registry.restore(registry.load("P-350"))
        self.assertEqual(restored.status, State.DETECTED.value)
        self.assertEqual(registry.load("P-350").status, State.DETECTED.value)

    def test_the_clamp_only_moves_the_target_not_the_history(self):
        """夹逼发生在读侧：盘上的 `prev_status` 在**恢复之前**仍逐字是
        `card_sent`（回程票不许被后来的法条篡改）。"""
        req = _mk("P-351", State.CARD_SENT.value)
        registry.trash(req, "deleted")
        on_disk = registry.load("P-351")
        self.assertEqual(on_disk.prev_status, State.CARD_SENT.value)
        self.assertEqual(on_disk.status, State.TRASHED.value)

    def test_every_other_return_ticket_restores_verbatim(self):
        """§9 的精确复位一字不变——夹逼只认退役的那一个值。"""
        for i, status in enumerate((State.DETECTED.value, State.RAISING.value,
                                    State.APPROVED.value, State.EXECUTING.value,
                                    State.REVIEW.value, State.DELIVERED.value),
                                   start=1):
            with self.subTest(prev_status=status):
                rid = f"P-36{i}"
                registry.trash(_mk(rid, status), "deleted")
                self.assertEqual(registry.restore(registry.load(rid)).status, status)

    def test_a_trashed_card_with_no_return_ticket_lands_in_the_backlog(self):
        req = _mk("P-370", State.TRASHED.value)
        req.prev_status = None
        registry.save(req)
        self.assertEqual(registry.restore(registry.load("P-370")).status,
                         State.DETECTED.value)
