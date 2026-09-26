"""§45 的回声环那一刀在提案列退役后仍然可观测：LIMITED 出身的卡**落同一条
车道但不响**，FULL 出身的同款卡照响。

契约：CONTRACT **§78** / **§78.6**「安静出生」条（D80.7）/ §45（出生资格闸：
FULL / LIMITED / CORROBORATE）/ §1 §78 追记（add-only 卡字段 ``quiet_birth``）/
§0 第 10 条修宪（「拿不准的静默」自此由安静出生承担）。

§78 之前，「拿不准」这件事是靠**落哪一列**表达的：FULL 进提案列、LIMITED 压进
备选列。车道退役后两条路落点相同，这半条法条整个悬空——如果没有别的载体，
「屏幕 OCR 的内容不许打扰 owner」就会在一次重构里无声消失。D80.7 把它平移到
**通知资格**：``quiet_birth: true`` 的行，``alerts`` 的新卡 diff 整条跳过。

本文件钉三段，全是端到端（出生闸 → 落盘 → 投影 → 通知 diff）：

1. LIMITED 出生：落 ``detected``（**看得见**，没有一张卡因为静默而隐形）、
   卡上带 ``quiet_birth``、投影行带它、新卡通知**一声不响**；
2. FULL 出生：同样的卡、同样的一列，**响一次**——差别只有这一声；
3. 安静只影响出生那一声：这张卡日后翻出 §76.3 的结算信号照常响。

零子进程、零网络（沙箱 AIASSISTANT_HOME + fail-loud 守卫）。
"""
import datetime as _dt
import unittest

from tests import TMP_HOME  # noqa: F401 - sandbox env before act imports

from act import actd, radar_claude_sessions, radar_slack
from act.lib import config, dashboard, provenance, quick_capture, registry
from act.lib.registry import Requirement, State

TODAY = "2026-09-26"


def _candidate(rid_title="屏幕上瞥见的一件事", channel="screen") -> Requirement:
    return Requirement(id=registry.next_id(), title=rid_title, summary=rid_title,
                       sources=[{"channel": channel, "date": TODAY, "quote": "原文"}])


class QuietBirthBase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()
        self.cfg = config.Config()

    def _file(self, gate):
        _kind, saved = quick_capture.apply_triage(
            {"action": "new_proposal", "confidence": "high"},
            _candidate(), self.cfg, high_confidence=True, gate=gate)
        return saved

    def _board(self, reqs=None):
        reqs = registry.load_all() if reqs is None else reqs
        return dashboard.build_dashboard(reqs=reqs, agents=[], cfg=self.cfg,
                                         archived=[])

    def _empty_board(self):
        return dashboard.build_dashboard(reqs=[], agents=[], cfg=self.cfg, archived=[])


class LimitedBirthIsVisibleButSilentTestCase(QuietBirthBase):
    def test_limited_birth_lands_in_the_backlog_and_is_marked_quiet(self):
        saved = self._file(provenance.LIMITED)
        self.assertEqual(saved.status, State.DETECTED.value)   # 看得见
        self.assertTrue(saved.quiet_birth)                     # 但不打扰
        self.assertTrue(registry.load(saved.id).quiet_birth)   # 落盘了，不是内存痕

    def test_the_quiet_flag_rides_the_projection_row(self):
        saved = self._file(provenance.LIMITED)
        row = self._board()["debt"][0]
        self.assertEqual(row["id"], saved.id)
        self.assertTrue(row["quiet_birth"])

    def test_a_limited_birth_fires_no_new_card_notification(self):
        self._file(provenance.LIMITED)
        self.assertEqual(actd.detect_transitions(self._empty_board(), self._board()), [])

    def test_the_card_is_still_on_the_board(self):
        """静默 ≠ 隐形（§78.1 第 3 条 / 宪法第 3 条）：不响，但 owner 打开看板
        就看得见它。"""
        saved = self._file(provenance.LIMITED)
        board = self._board()
        self.assertEqual([r["id"] for r in board["debt"]], [saved.id])
        self.assertEqual(board["counts"]["debt"], 1)


class FullBirthStillRingsTestCase(QuietBirthBase):
    def test_a_full_birth_lands_in_the_same_lane_and_notifies_once(self):
        saved = self._file(provenance.FULL)
        self.assertEqual(saved.status, State.DETECTED.value)
        self.assertFalse(saved.quiet_birth)
        board = self._board()
        self.assertNotIn("quiet_birth", board["debt"][0])   # 假 = 整键不出
        msgs = actd.detect_transitions(self._empty_board(), board)
        self.assertEqual([m[2] for m in msgs], [saved.id])

    def test_an_absent_gate_is_a_designed_channel_and_still_rings(self):
        """``gate=None`` = slack / gmail / 手打捕获这些**显式设计的发起渠道**
        （§45 等同 FULL）——退役不许顺手把它们也静音。"""
        _kind, saved = quick_capture.apply_triage(
            {"action": "new_proposal", "confidence": "high"},
            _candidate("老板在 Slack 上问的事", channel="slack"), self.cfg,
            high_confidence=True)
        self.assertFalse(saved.quiet_birth)
        msgs = actd.detect_transitions(self._empty_board(), self._board())
        self.assertEqual([m[2] for m in msgs], [saved.id])


class QuietOnlySilencesTheBirthTestCase(QuietBirthBase):
    def test_a_quiet_card_still_rings_when_a_settlement_signal_flips(self):
        """§78.6 明写：安静**只影响出生那一声**。一张静静躺了两周的卡到了
        截止日，照样要把 owner 叫起来（§76.3）。"""
        today = dashboard._today()
        quiet_future = Requirement(
            id="P-700", title="安静出生的那张卡", status=State.DETECTED.value,
            quiet_birth=True, deadline=(today + _dt.timedelta(days=7)).isoformat())
        quiet_overdue = Requirement(
            id="P-700", title="安静出生的那张卡", status=State.DETECTED.value,
            quiet_birth=True, deadline=(today - _dt.timedelta(days=1)).isoformat())
        prev, curr = self._board([quiet_future]), self._board([quiet_overdue])
        self.assertTrue(curr["debt"][0]["quiet_birth"])
        self.assertEqual([m[2] for m in actd.detect_transitions(prev, curr)], ["P-700"])


class ProducersStampTheQuietStampTestCase(QuietBirthBase):
    """出生那一刻谁盖章（§78.6：铸卡漏斗按 §45 天花板盖，LLM 不写）。"""

    def test_a_non_urgent_slack_item_is_born_quiet(self):
        req = radar_slack._mcp_requirement(
            {"title": "回头再说的事", "summary": "回头再说的事", "urgent": False,
             "date": TODAY, "quote": "原文"})
        self.assertEqual(req.status, State.DETECTED.value)
        self.assertTrue(req.quiet_birth)

    def test_an_urgent_slack_item_is_born_loud(self):
        req = radar_slack._mcp_requirement(
            {"title": "今天就要的事", "summary": "今天就要的事", "urgent": True,
             "date": TODAY, "quote": "原文"})
        self.assertEqual(req.status, State.DETECTED.value)
        self.assertFalse(req.quiet_birth)

    def test_a_session_not_waiting_on_you_is_born_quiet(self):
        base = {"title": "上周那个重构", "gist": "重构 dashboard",
                "last_activity": TODAY}
        quiet = radar_claude_sessions._session_card(
            {**base, "session_id": "aaaa0001", "ended_waiting_on_user": False})
        loud = radar_claude_sessions._session_card(
            {**base, "session_id": "aaaa0002", "ended_waiting_on_user": True})
        self.assertEqual((quiet.status, loud.status),
                         (State.DETECTED.value, State.DETECTED.value))
        self.assertTrue(quiet.quiet_birth)
        self.assertFalse(loud.quiet_birth)

    def test_low_confidence_triage_stamps_it_too(self):
        """三选一判「真实但不紧急」：§78 之前是降列，现在是不打扰。"""
        _kind, saved = quick_capture.apply_triage(
            {"action": "new_proposal", "confidence": "low"},
            _candidate("以后想做的事", channel="slack"), self.cfg,
            high_confidence=True)
        self.assertEqual(saved.status, State.DETECTED.value)
        self.assertTrue(saved.quiet_birth)
