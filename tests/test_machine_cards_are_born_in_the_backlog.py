"""机器卡的出生地只有一个：潜在任务（``detected``）——每一个生产者逐条点名。

契约：CONTRACT **§78**（提案车道退役）/ **§78.1** 第 2 条硬承诺「每一个生产者
都落 `detected`」/ **§78.3** 写路径重定向表 / §45（出生资格：FULL 与 LIMITED 的
差别搬到通知资格，不再是「哪一列」）/ §51（hand lane 墓碑）/ §65（self_improve
跟进卡）/ §70.2（夜间去重合成卡）/ §8（欠账扩写就地写厚）。

**这个文件是整次退役的防回归网**（§78.3 原话：「上面每一行漏掉一条，症状都是
同一种——卡照常出生、照常落盘、CI 照常全绿，只是 owner 永远看不见它」）。所以
它是表驱动的：每一行 = §78.3 表里的一条产地，断言两件事——落点恒 `detected`，
且**绝不**是退役的 `card_sent`。

刻意**不**用「凡是 card_sent 都改」这种口号式断言（源码扫描），而是逐个真的调
产地函数：`card_sent` 作为枚举值、作为存量卡的值、作为白名单行都**必须留着**
（§0 第 6 条 add-only），能被禁的只有「谁往里写」。

注入缝：analyze 的 runner、maintenance/registry 的纯函数入口；零子进程、零网络
（tests/__init__.py 的沙箱 AIASSISTANT_HOME + fail-loud 守卫）。
"""
import datetime as _dt
import subprocess
import unittest

from tests import TMP_HOME  # noqa: F401 - sandbox env before act imports

from act import analyze, radar_claude_sessions, radar_gmail, radar_slack
from act.lib import (config, daily_loop, maintenance, provenance, quick_capture,
                     registry, self_improve)
from act.lib.loop_inputs import Signal
from act.lib.registry import Requirement, State

NOW = _dt.datetime(2026, 9, 26, 10, 0, tzinfo=_dt.timezone.utc)
TODAY = "2026-09-26"


def _expansion(_prompt):
    """analyze 的注入 runner：一次成功的扩写（绝不起真 claude）。"""
    return subprocess.CompletedProcess(
        ["claude"], 0,
        stdout='{"summary":"扩写后的摘要","plan":["第一步"],'
               '"definition_of_done":["做完了"],"cost_estimate_usd":1.5}')


def _slack_item(urgent):
    return {"title": "帮我看下这个", "summary": "帮我看下这个",
            "urgent": urgent, "date": TODAY, "quote": "原文",
            "channel": "#general", "type": "comms", "tier": "T1"}


class MachineCardBirthLaneTestCase(unittest.TestCase):
    """§78.3 的产地表，逐条走一遍真产地函数。"""

    def setUp(self):
        config.ensure_state_dirs()
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()
        self.cfg = config.Config()

    # -- 产地们（每个返回一张已铸出的卡） --------------------------------- #
    def _slack_mcp(self):
        return radar_slack._mcp_requirement(_slack_item(True))

    def _slack_mcp_non_urgent(self):
        return radar_slack._mcp_requirement(_slack_item(False))

    def _slack_native(self):
        return radar_slack._native_requirement(
            _slack_item(True), {"channel": "slack", "date": TODAY, "quote": "原文"})

    def _gmail(self):
        return radar_gmail._gmail_requirement(
            {"summary": "老板问进度", "type": "comms", "tier": "T1"},
            {"channel": "gmail", "date": TODAY, "quote": "原文"})

    def _claude_session_waiting(self):
        return radar_claude_sessions._session_card(
            {"title": "那个重构", "gist": "重构 dashboard", "session_id": "abcd1234",
             "ended_waiting_on_user": True, "last_activity": TODAY})

    def _claude_session_recent(self):
        return radar_claude_sessions._session_card(
            {"title": "那个重构", "gist": "重构 dashboard", "session_id": "abcd5678",
             "ended_waiting_on_user": False, "last_activity": TODAY})

    def _quick_capture_full(self):
        """三选一闸门的 new_proposal 出口（FULL 出身、high confidence、硬截止）
        ——§78 之前这一组条件正是「直发提案列」的那条分流。"""
        req = Requirement(id=registry.next_id(), title="办签证", summary="办签证",
                          hardness="hard", deadline="2026-10-01",
                          sources=[{"channel": "slack", "date": TODAY, "quote": "原文"}])
        _kind, saved = quick_capture.apply_triage(
            {"action": "new_proposal", "confidence": "high"}, req, self.cfg,
            high_confidence=True)
        return saved

    def _quick_capture_limited(self):
        """§45 非 FULL 出身：照样落同一条车道（差别只剩通知资格，D80.7）。"""
        req = Requirement(id=registry.next_id(), title="屏幕上看到的事", summary="佐证",
                          sources=[{"channel": "screen", "date": TODAY, "quote": "原文"}])
        _kind, saved = quick_capture.apply_triage(
            {"action": "new_proposal", "confidence": "high"}, req, self.cfg,
            high_confidence=True, gate=provenance.LIMITED)
        return saved

    def _daily_loop(self):
        sig = Signal(kind="issue", fingerprint="issue:447", title="一条够长的每日循环信号标题",
                     summary="为什么", plan=["第一步"], dod=["做完了"], cost_usd=2.0,
                     evidence="证据", priority=50, ref="")
        return daily_loop.build_card(sig, TODAY, str(config.HOME))

    def _self_improve_followup(self):
        pr = {"number": 447, "url": "https://github.com/o/r/pull/447",
              "headRefName": "ai/self-improve/R-229", "headRefOid": "deadbeef"}
        return self_improve.mint_followup(
            pr, [{"login": "Wan-ZL", "at": "2026-09-26T09:00:00Z", "body": "补个测试",
                  "url": "https://github.com/o/r/pull/447#c1"}], [], self.cfg, NOW)

    def _analyze_expand(self):
        req = Requirement(id=registry.next_id(), title="研究并提议的那张卡",
                          status=State.RAISING.value)
        registry.save(req)
        return analyze.expand_debt(req, self.cfg, runner=_expansion)

    def _analyze_expand_fallback(self):
        """扩写失败的兜底路径落点必须和成功路径一样（§8：欠账永不丢）。"""
        req = Requirement(id=registry.next_id(), title="扩写会失败的那张卡",
                          status=State.RAISING.value)
        registry.save(req)
        return analyze.expand_debt(
            req, self.cfg,
            runner=lambda _p: subprocess.CompletedProcess(["claude"], 1, stdout=""))

    def _maintenance_merge(self):
        """夜间去重：簇里混着一张退役车道的存量卡，合成卡也不许回那个格子。"""
        src = [{"channel": "meeting", "date": "2026-08-01", "quote": "同一件事"}]
        old = Requirement(id="P-9001", title="同一件事要办", status=State.DETECTED.value,
                          sources=list(src))
        straggler = Requirement(id="P-9002", title="同一件事要办",
                                status=State.CARD_SENT.value, sources=list(src))
        return maintenance.plan_merge([old, straggler])

    def _registry_brand_new(self):
        """§78 之前的唯一自动入口：high confidence + hard + deadline。"""
        new = Requirement(id=registry.next_id(), title="高置信 + 硬截止的全新卡",
                          summary="s", hardness="hard", deadline="2026-10-01",
                          sources=[{"channel": "gmail", "date": TODAY, "quote": "原文"}])
        _kind, saved = registry.merge_or_new_with_kind(new, high_confidence=True)
        return saved

    def _registry_reraise(self):
        """§10 回锅：已验收卡被重新提起 → 翻回潜在任务，不是提案列。"""
        parent = Requirement(id=registry.next_id(), title="早就交付过的那件事",
                             status=State.DELIVERED.value,
                             sources=[{"channel": "slack", "date": "2026-08-01",
                                       "quote": "原文"}])
        registry.save(parent)
        child = Requirement(id=registry.next_id(), title="早就交付过的那件事",
                            summary="又提了一次，还带新要求",
                            sources=[{"channel": "slack", "date": TODAY, "quote": "再提"}])
        kind, saved = registry.reraise_or_followup(
            parent, child, note="这次还要加一版中文", same_task=True, actionable=True)
        self.assertEqual(kind, "reraised")
        return saved

    def _registry_follow_up(self):
        """§10 同线程不同事 → follow-up 子卡，同样落潜在任务。"""
        parent = Requirement(id=registry.next_id(), title="上一件已交付的事",
                             status=State.DELIVERED.value, thread_id="T-1",
                             sources=[{"channel": "gmail", "date": "2026-08-01",
                                       "quote": "原文"}])
        registry.save(parent)
        child = Requirement(id=registry.next_id(), title="同一封邮件里的另一件事",
                            summary="另一件事",
                            sources=[{"channel": "gmail", "date": TODAY, "quote": "新事"}])
        kind, saved = registry.reraise_or_followup(
            parent, child, note="另一件事", same_task=False, actionable=True)
        self.assertEqual(kind, "follow_up")
        return saved

    # -- §78.3 的表本体 ---------------------------------------------------- #
    def _producers(self) -> list:
        return [
            ("radar_slack MCP 回落（urgent）", self._slack_mcp),
            ("radar_slack MCP 回落（非 urgent）", self._slack_mcp_non_urgent),
            ("radar_slack 原生路径", self._slack_native),
            ("radar_gmail 铸卡", self._gmail),
            ("radar_claude_sessions（等你回话）", self._claude_session_waiting),
            ("radar_claude_sessions（只是最近）", self._claude_session_recent),
            ("quick_capture 三选一（FULL）", self._quick_capture_full),
            ("quick_capture 三选一（LIMITED）", self._quick_capture_limited),
            ("daily_loop 🤖 卡", self._daily_loop),
            ("self_improve PR 跟进卡", self._self_improve_followup),
            ("analyze 欠账扩写（成功）", self._analyze_expand),
            ("analyze 欠账扩写（兜底）", self._analyze_expand_fallback),
            ("maintenance 夜间去重合成卡", self._maintenance_merge),
            ("registry 全新卡（高置信 + 硬截止）", self._registry_brand_new),
            ("registry 回锅（re-raise）", self._registry_reraise),
            ("registry follow-up 子卡", self._registry_follow_up),
        ]

    def test_every_producer_mints_detected_and_never_the_retired_lane(self):
        for label, produce in self._producers():
            with self.subTest(producer=label):
                card = produce()
                self.assertIsNotNone(card, label)
                self.assertEqual(str(card.status), State.DETECTED.value,
                                 f"{label} 没有落在潜在任务列（§78.3）")
                self.assertNotEqual(str(card.status), State.CARD_SENT.value,
                                    f"{label} 还在往退役的提案车道写（§78.1 第 2 条）")

    def test_a_full_producer_sweep_leaves_the_retired_lane_empty_on_disk(self):
        """所有产地跑完之后，盘上一张 `card_sent` 都不该有。

        逐条 subTest 会漏掉「某个产地在落盘时又把状态改回去」这一类；这条从
        注册表**真源**再确认一次：退役车道的存量只可能来自迁移前的老卡。"""
        for _label, produce in self._producers():
            produce()
        on_disk = [r.id for r in registry.load_all()
                   if str(r.status) == State.CARD_SENT.value]
        self.assertEqual(on_disk, [], "有产地把卡落进了退役的提案车道（§78.1）")
        self.assertTrue([r for r in registry.load_all()
                         if str(r.status) == State.DETECTED.value])


class RetiredLaneStaysLegalTestCase(unittest.TestCase):
    """退役 ≠ 删除（§78.1 第 1 条 / §0 第 6 条 add-only）。

    生产者不许再写它，但值本身、枚举成员、存量卡的读出必须一字不动——否则
    这次退役就从「折叠一条车道」变成「丢用户数据」。"""

    def setUp(self):
        config.ensure_state_dirs()
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()

    def test_the_enum_member_and_its_value_survive(self):
        self.assertEqual(State.CARD_SENT.value, "card_sent")
        self.assertIn(State.CARD_SENT, list(State))

    def test_a_straggler_card_still_loads_with_its_retired_status(self):
        registry.save(Requirement(id="P-9100", title="退役车道上的存量卡",
                                  status=State.CARD_SENT.value))
        self.assertEqual(registry.load("P-9100").status, State.CARD_SENT.value)
