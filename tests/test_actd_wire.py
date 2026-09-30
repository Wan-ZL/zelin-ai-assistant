"""actd v-next 接线行为测试（vnext-amendments §50/§51/§78/§44.3-S/W1.c/W17）。

§78（issue #447 / owner decision D80）：提案列退役，机器卡一律落潜在任务
（``detected``）；§51 的 hand 免批车道随之 tombstone（D80.4），§65 self_improve
免批 lane 于 D86 整条删除——``auto_dispatch_pass`` 已不存在，本文件的卡全部铸在
``detected``，钉的是「跑完一整个 pass 仍然留在那儿」。

覆盖：
  dispatch_approved — 并发上限排队（queued 子状态）、无预算复核（D9）；
  comment-on-EXECUTING — steer 入队（ts+stem 带键 dedup，owner ingress 限定）、
      agent/remote 评论只记录（T-28）、其余状态保基线 fold；
  ingress marker（T-28）— capture via:"agent"/"remote"/未知值 → agent_capture/
      remote_capture 通道（PROPOSED，结构性关死自动派发）、owner ingress 保 HAND；
  approve + W17 — 外部出身未扩写 → 转 raising 留 [W17] 痕；
  reconcile — blocked 窗口 flush（成功 mark_delivered / 失败计数 / 3 次放弃
      drop 留痕）、done 晋升时未送达 steer 诚实丢弃；
  archive_stale — 默认 30 天冷封存 + 24h 门 + registry RELOCATE 往返；
  registry.merge_or_new — origin_trust 铸卡盖章与 fold 降级刷新；
  dashboard — queued_reason（结构化 C-2）/ steers[]（C-3/C-4）/ origin_trust /
      auto_dispatch_block 投影。

沙箱 AIASSISTANT_HOME（tests/__init__.py）；绝不 spawn 真 claude（executor/
notify 全 mock，roster 注入）。
"""
import datetime as _dt
import json
import unittest
import uuid
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import actd
from act.lib import config, registry, steer
from act.lib.dashboard import build_dashboard
from act.lib.registry import Requirement, State

_HAND_SRC = [{"who": "zelin", "channel": "quick_capture",
              "date": "2026-08-30", "quote": "手打的活"}]
_SLACK_SRC = [{"who": "boss", "channel": "slack",
               "date": "2026-08-30", "quote": "外部请求"}]

# v0.48 当日花费台账文件名（retired v0.48.7，D9）：只用来钉「不再写它」。
_LEGACY_LEDGER = "autodispatch_spend.json"


def _mk(req_id="R-700", status=State.DETECTED.value, sources=None, **kw):
    base = dict(id=req_id, title=f"wire 测试 {req_id}", type="other", tier="T1",
                status=status, sources=list(sources or _HAND_SRC),
                target_repo=TMP_HOME)
    base.update(kw)
    req = Requirement(**base)
    registry.save(req)
    return req


def _reload(req_id):
    return registry.load(req_id)


def _clean_registry():
    config.ensure_state_dirs()
    for p in config.REGISTRY_DIR.glob("*.yaml"):
        p.unlink()
    if registry.ARCHIVE_DIR.exists():
        for p in registry.ARCHIVE_DIR.glob("*.yaml"):
            p.unlink()
    for p in config.INBOX_DIR.glob("*.json"):
        p.unlink()
    ledger = config.STATE_DIR / _LEGACY_LEDGER
    if ledger.exists():
        ledger.unlink()
    marker = config.STATE_DIR / actd._ARCHIVE_SWEEP_MARKER
    if marker.exists():
        marker.unlink()


def _cfg(**auto):
    """`autodispatch:` 块（D86 起只剩 max_concurrent / require_awake 还被读）。"""
    return config.Config(raw={"autodispatch": auto} if auto else {})


class WireBase(unittest.TestCase):
    def setUp(self):
        _clean_registry()
        self.notify = mock.patch.object(actd.notify, "notify").start()
        self.addCleanup(mock.patch.stopall)


# --------------------------------------------------------------------------- #
# dispatch_approved：并发排队；无预算复核（§51 / M1.c；D9）
# --------------------------------------------------------------------------- #
class TestDispatchGates(WireBase):
    def test_concurrency_cap_queues_approved_card(self):
        _mk("R-710", status=State.EXECUTING.value,
            execution={"session_id": "sid-1"})
        _mk("R-711", status=State.APPROVED.value)
        ex_mock = mock.MagicMock()
        with mock.patch.object(actd, "executor", ex_mock):
            actd.dispatch_approved(_cfg(max_concurrent=1))
        ex_mock.dispatch.assert_not_called()
        self.assertEqual(_reload("R-711").status, State.APPROVED.value)

    def test_slot_free_dispatches(self):
        _mk("R-712", status=State.APPROVED.value)
        ex_mock = mock.MagicMock()
        with mock.patch.object(actd, "executor", ex_mock):
            actd.dispatch_approved(_cfg(max_concurrent=1))
        ex_mock.dispatch.assert_called_once()

    def test_auto_card_never_waits_on_budget_d9(self):
        # 原判例 test_auto_card_waits_when_budget_tightened 钉「台账 3+4 > 5 →
        # auto 卡留队、人批卡照发」。D9 retired v0.48.7：旧 config 里残留的
        # daily_budget_usd 键不起作用。（D86 起没有 auto 卡：带 auto_dispatched 痕的
        # 存量卡被退役护栏撤回，那一条的判例在 test_retired_self_improve_lane_is_inert。）
        _mk("R-720", status=State.APPROVED.value, cost_estimate_usd=40.0)
        _mk("R-721", status=State.APPROVED.value, cost_estimate_usd=4.0)
        ex_mock = mock.MagicMock()
        with mock.patch.object(actd, "executor", ex_mock):
            actd.dispatch_approved(_cfg(daily_budget_usd=5))
        dispatched = sorted(c.args[0].id for c in ex_mock.dispatch.call_args_list)
        self.assertEqual(dispatched, ["R-720", "R-721"])


# --------------------------------------------------------------------------- #
# comment：EXECUTING → steer；其余状态保基线 fold（§44.3-S）
# --------------------------------------------------------------------------- #
class TestCommentRouting(WireBase):
    def _drop(self, req_id, comment, ts="2026-08-30T01:00:00Z", via=None,
              name=None):
        path = config.INBOX_DIR / f"{name or uuid.uuid4()}.json"
        rec = {"id": req_id, "action": "comment", "comment": comment, "ts": ts}
        if via is not None:
            rec["via"] = via
        path.write_text(json.dumps(rec), encoding="utf-8")

    def test_comment_on_executing_enqueues_steer(self):
        _mk("R-730", status=State.EXECUTING.value, plan=["原计划"],
            execution={"session_id": "sid-1"})
        self._drop("R-730", "改用 B 方案")
        actd.process_inbox()
        req = _reload("R-730")
        self.assertEqual(req.status, State.EXECUTING.value)   # 状态机零改动
        self.assertEqual(req.plan, ["原计划"])                 # 不再 fold 进 plan
        pend = steer.pending_steers(req)
        self.assertEqual(len(pend), 1)
        self.assertEqual(pend[0]["text"], "改用 B 方案")
        self.assertTrue(pend[0]["key"].startswith("2026-08-30T01:00:00Z|"))

    def test_replayed_inbox_file_dedups_by_stem(self):
        # m1：dedup 键带 inbox stem——只有真正的同文件重放（unlink 失败：
        # 同 stem 同 ts 同文）才去重
        _mk("R-731", status=State.EXECUTING.value,
            execution={"session_id": "sid-1"})
        self._drop("R-731", "快一点", ts="t1", name="replayed-decision")
        actd.process_inbox()
        self._drop("R-731", "快一点", ts="t1", name="replayed-decision")  # 重放
        actd.process_inbox()
        self.assertEqual(len(steer.pending_steers(_reload("R-731"))), 1)

    def test_identical_texts_in_same_second_are_two_steers(self):
        # m1：同秒同文的两条指令是两个 inbox 文件（stem 不同）→ 两条 steer
        _mk("R-736", status=State.EXECUTING.value,
            execution={"session_id": "sid-1"})
        self._drop("R-736", "快一点", ts="t1")
        self._drop("R-736", "快一点", ts="t1")
        actd.process_inbox()
        self.assertEqual(len(steer.pending_steers(_reload("R-736"))), 2)

    def test_same_text_new_ts_is_new_steer(self):
        _mk("R-738", status=State.EXECUTING.value,
            execution={"session_id": "sid-1"})
        self._drop("R-738", "快一点", ts="t1", name="same-stem")
        actd.process_inbox()
        self._drop("R-738", "快一点", ts="t2", name="same-stem")  # owner 重申
        actd.process_inbox()
        self.assertEqual(len(steer.pending_steers(_reload("R-738"))), 2)

    def test_comment_on_backlog_card_keeps_baseline_fold(self):
        # §78：待决卡如今住在潜在任务（detected）——评论仍是「折进备注、状态
        # 不动」的基线 fold，不排 steer（那是 EXECUTING 专属）。
        _mk("R-732", status=State.DETECTED.value, plan=["step"])
        self._drop("R-732", "补充一下")
        actd.process_inbox()
        req = _reload("R-732")
        self.assertEqual(req.status, State.DETECTED.value)
        self.assertIn("修改方向", req.notes)

    def test_owner_web_comment_on_executing_steers(self):
        # via:"web" = localhost 看板，owner-class ingress——照旧 steer
        _mk("R-734", status=State.EXECUTING.value,
            execution={"session_id": "sid-1"})
        self._drop("R-734", "改用 B 方案", via="web")
        actd.process_inbox()
        self.assertEqual(len(steer.pending_steers(_reload("R-734"))), 1)

    def test_agent_comment_on_executing_recorded_never_steered(self):
        # T-28：agent 评论上卡可见（notes），但绝不排进 OWNER UPDATE 队列、
        # 不折进 plan（executor 指令面）、不动状态机
        _mk("R-735", status=State.EXECUTING.value, plan=["原计划"],
            execution={"session_id": "sid-1"})
        self._drop("R-735", "progress: tests green", via="agent")
        actd.process_inbox()
        req = _reload("R-735")
        self.assertEqual(req.status, State.EXECUTING.value)
        self.assertEqual(steer.pending_steers(req), [])
        self.assertIn("agent 备注", req.notes)
        self.assertIn("progress: tests green", req.notes)
        self.assertEqual(req.plan, ["原计划"])

    def test_remote_comment_on_executing_recorded_never_steered(self):
        _mk("R-739", status=State.EXECUTING.value,
            execution={"session_id": "sid-1"})
        self._drop("R-739", "远程补充", via="remote")
        actd.process_inbox()
        req = _reload("R-739")
        self.assertEqual(steer.pending_steers(req), [])
        self.assertIn("remote 备注", req.notes)

    def test_agent_comment_never_knocks_card_back(self):
        # 非 owner 评论不触发「打回重批」：approved 卡收 agent 评论状态不动
        _mk("R-737", status=State.APPROVED.value)
        self._drop("R-737", "补充信息", via="agent")
        actd.process_inbox()
        req = _reload("R-737")
        self.assertEqual(req.status, State.APPROVED.value)
        self.assertIn("补充信息", req.notes)


# --------------------------------------------------------------------------- #
# approve + W17 强制扩写（§W17/§50）
# --------------------------------------------------------------------------- #
class TestApproveW17(WireBase):
    def _approve(self, req_id):
        path = config.INBOX_DIR / f"{uuid.uuid4()}.json"
        path.write_text(json.dumps({"id": req_id, "action": "approve",
                                    "comment": None, "ts": "t"}),
                        encoding="utf-8")
        actd.process_inbox()

    def test_external_unexpanded_approve_converts_to_raise(self):
        if actd.analyze is None:
            self.skipTest("analyze unavailable")
        _mk("R-740", sources=_SLACK_SRC, origin_trust="external",
            plan=None, definition_of_done=None)
        self._approve("R-740")
        req = _reload("R-740")
        self.assertEqual(req.status, State.RAISING.value)
        self.assertIn("[W17]", req.notes)

    def test_external_expanded_approve_passes(self):
        _mk("R-741", sources=_SLACK_SRC, origin_trust="external",
            plan=["已扩写的 plan"], definition_of_done=["DoD"])
        self._approve("R-741")
        self.assertEqual(_reload("R-741").status, State.APPROVED.value)

    def test_hand_card_approve_unaffected(self):
        _mk("R-742", origin_trust="hand", plan=None)
        self._approve("R-742")
        self.assertEqual(_reload("R-742").status, State.APPROVED.value)

    def test_stampless_external_sources_approve_converts_to_raise(self):
        # F2（v0.48.1 §50 修订）：缺章（手编/pre-v0.48 存量 YAML）但 sources
        # 是 slack → 出身现算为 external，approve 同样转扩写——绝不裸批
        if actd.analyze is None:
            self.skipTest("analyze unavailable")
        _mk("R-743", sources=_SLACK_SRC, plan=None, definition_of_done=None)
        self._approve("R-743")
        req = _reload("R-743")
        self.assertEqual(req.status, State.RAISING.value)
        self.assertIn("[W17]", req.notes)


# --------------------------------------------------------------------------- #
# reconcile：blocked 窗口 flush / done 晋升丢弃（§44.3-S 接线 2）
# --------------------------------------------------------------------------- #
def _executing_with_steer(req_id="R-750", steers=("先修 bug",)):
    req = _mk(req_id, status=State.EXECUTING.value,
              execution={"session_id": "sid-1"})
    for i, text in enumerate(steers):
        steer.enqueue_steer(req, text, ts=f"2026-08-30T0{i}:00:00Z")
    registry.save(req)
    return req


class TestSteerFlush(WireBase):
    def _reconcile(self, ex_mock, state="blocked"):
        roster = [{"id": "sid-1", "sessionId": "sid-1", "state": state,
                   "pid": 42}]
        with mock.patch.object(actd, "executor", ex_mock), \
                mock.patch.object(actd, "_run_claude_agents",
                                  return_value=roster):
            actd.reconcile_executing(config.Config(), set())

    def test_blocked_window_flush_delivers(self):
        _executing_with_steer()
        ex_mock = mock.MagicMock()
        ex_mock.resume.return_value = True
        ex_mock.stop_session.return_value = True
        # v0.47 适配：blocked 分支先经 _promote_if_delivered 探 FINAL DRAFT，
        # 空收割 = 未交付（MagicMock 默认值会被当成交付内容误升 review）。
        ex_mock.harvest_delivery.return_value = {}
        self._reconcile(ex_mock)
        req = _reload("R-750")
        self.assertEqual(steer.pending_steers(req), [])
        entries = steer.delivered_entries(req)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["text"], "先修 bug")
        prompt = ex_mock.resume.call_args.kwargs["prompt"]
        self.assertTrue(prompt.startswith(steer.STEER_PREFIX))
        self.assertIn("先修 bug", prompt)

    def test_flush_failure_counts_attempt_then_drops_at_cap(self):
        _executing_with_steer("R-751")
        ex_mock = mock.MagicMock()
        ex_mock.resume.return_value = False
        ex_mock.stop_session.return_value = True
        ex_mock.harvest_delivery.return_value = {}   # v0.47 适配（同上）
        for want in (1, 2, 3):
            self._reconcile(ex_mock)
            req = _reload("R-751")
            self.assertEqual(req.execution.get("steer_attempts"), want)
        self._reconcile(ex_mock)          # 第 4 轮：give_up → drop 留痕 + 通知
        req = _reload("R-751")
        self.assertEqual(steer.pending_steers(req), [])
        self.assertIn("追加指令未送达", req.notes)
        self.notify.assert_called()

    def test_working_session_never_interrupted(self):
        _executing_with_steer("R-752")
        ex_mock = mock.MagicMock()
        self._reconcile(ex_mock, state="working")
        ex_mock.stop_session.assert_not_called()
        ex_mock.resume.assert_not_called()
        self.assertEqual(len(steer.pending_steers(_reload("R-752"))), 1)

    def test_done_promotion_drops_pending_honestly(self):
        _executing_with_steer("R-753")
        ex_mock = mock.MagicMock()
        ex_mock.harvest_delivery.return_value = {}
        self._reconcile(ex_mock, state="done")
        req = _reload("R-753")
        self.assertEqual(req.status, State.REVIEW.value)
        self.assertEqual(steer.pending_steers(req), [])
        self.assertIn("追加指令未送达", req.notes)

    def test_dead_resume_carries_steer_prompt(self):
        _executing_with_steer("R-754")
        ex_mock = mock.MagicMock()
        ex_mock.resume.return_value = True
        # v0.47 适配：dead 路径 resume 前也经 _promote_if_delivered 探交付
        ex_mock.harvest_delivery.return_value = {}
        with mock.patch.object(actd, "executor", ex_mock), \
                mock.patch.object(actd, "_run_claude_agents",
                                  return_value=[]):   # 会话消失 → dead-resume
            actd.reconcile_executing(config.Config(), set())
        req = _reload("R-754")
        self.assertEqual(steer.pending_steers(req), [])
        self.assertEqual(len(steer.delivered_entries(req)), 1)
        self.assertIn("先修 bug", ex_mock.resume.call_args.kwargs["prompt"])


# --------------------------------------------------------------------------- #
# archive_stale（W1.c：默认 30 天）+ archive/unarchive 往返
# --------------------------------------------------------------------------- #
def _iso_days_ago(days):
    return (_dt.datetime.now(_dt.timezone.utc)
            - _dt.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


class TestArchive(WireBase):
    def test_cold_delivered_sealed_by_default_30(self):
        _mk("R-760", status=State.DELIVERED.value,
            execution={"accepted_at": _iso_days_ago(40)})
        _mk("R-761", status=State.DELIVERED.value,
            execution={"accepted_at": _iso_days_ago(10)})
        cfg = config.Config()
        self.assertEqual(cfg.archive_after_days, 30)   # W1.c 默认值
        n = actd.archive_stale(cfg)
        self.assertEqual(n, 1)
        sealed = registry.load("R-760")
        self.assertEqual(sealed.status, State.ARCHIVED.value)
        self.assertEqual(sealed.prev_status, State.DELIVERED.value)
        # RELOCATE：热路径不见、include_archived 可见、id 不复用
        self.assertNotIn("R-760", [r.id for r in registry.load_all()])
        self.assertIn("R-760", [r.id for r in
                                registry.load_all(include_archived=True)])
        self.assertEqual(registry.load("R-761").status, State.DELIVERED.value)

    def test_24h_gate_and_protections(self):
        cfg = config.Config()
        _mk("R-762", status=State.DELIVERED.value,
            execution={"accepted_at": _iso_days_ago(40)},
            deadline=(_dt.date.today() + _dt.timedelta(days=5)).isoformat())
        _mk("R-763", status=State.DELIVERED.value)   # 无时间戳 → 永不自动封
        self.assertEqual(actd.archive_stale(cfg), 0)
        _mk("R-764", status=State.DELIVERED.value,
            execution={"accepted_at": _iso_days_ago(40)})
        self.assertEqual(actd.archive_stale(cfg), 0)  # 24h 门挡第二次 sweep

    def test_unarchive_roundtrip_and_archived_gate(self):
        req = _mk("R-765", status=State.DELIVERED.value)
        registry.archive(req, reason="user")
        # 归档卡上除 unarchive 外的动作全部 no-op（中央闸）
        actd._apply_decision(registry.load("R-765"), "approve", None)
        self.assertEqual(registry.load("R-765").status, State.ARCHIVED.value)
        actd._apply_decision(registry.load("R-765"), "unarchive", None)
        back = registry.load("R-765")
        self.assertEqual(back.status, State.DELIVERED.value)
        self.assertIsNone(back.archived_at)


# --------------------------------------------------------------------------- #
# origin_trust 盖章（铸卡漏斗 registry.merge_or_new，M1.a/C-1）
# --------------------------------------------------------------------------- #
class TestOriginStamp(WireBase):
    def test_mint_stamps_hand_and_roundtrips(self):
        saved = registry.merge_or_new(Requirement(
            id="", title="手打卡片测试标题", sources=list(_HAND_SRC)))
        self.assertEqual(saved.origin_trust, "hand")
        self.assertEqual(registry.load(saved.id).origin_trust, "hand")

    def test_fold_of_external_source_downgrades_stamp(self):
        saved = registry.merge_or_new(Requirement(
            id="", title="手打卡片测试标题", sources=list(_HAND_SRC)))
        registry.merge_or_new(Requirement(
            id="", title="手打卡片测试标题", sources=list(_SLACK_SRC)))
        self.assertEqual(registry.load(saved.id).origin_trust, "external")

    def test_sourceless_ai_card_stamped_proposed(self):
        saved = registry.merge_or_new(Requirement(
            id="", title="AI 自提卡片测试", sources=[]))
        self.assertEqual(saved.origin_trust, "proposed")

    def test_capture_path_stamps_hand(self):
        # owner-ingress-only（T-28）：HAND 只属于 Mac 文件形（无 via）与
        # localhost 看板（via:"web"）两个 owner ingress
        actd._apply_capture("给下周的评审准备材料")
        actd._apply_capture("web 看板上手打的活", via="web")
        reqs = registry.load_all()
        self.assertEqual(len(reqs), 2)
        for req in reqs:
            self.assertEqual(req.origin_trust, "hand", req.title)
            self.assertEqual(req.sources[0]["channel"], "quick_capture")


# --------------------------------------------------------------------------- #
# T-28 ingress 落款：via → 捕获源 channel → 信任裁决（结构性关死旁路自跑）
# --------------------------------------------------------------------------- #
class TestIngressMarker(WireBase):
    def _capture_inbox(self, text, via=None):
        rec = {"action": "capture", "text": text, "ts": "t"}
        if via is not None:
            rec["via"] = via
        path = config.INBOX_DIR / f"capture-{uuid.uuid4()}.json"
        path.write_text(json.dumps(rec), encoding="utf-8")
        actd.process_inbox()
        reqs = registry.load_all()
        self.assertEqual(len(reqs), 1)
        return reqs[0]

    def _assert_stays_after_a_pass(self, req_id, status):
        """跑一整个 actd.run_once（executor 为 mock）：卡不动、不被派发。"""
        ex_mock = mock.MagicMock()
        with mock.patch.object(actd, "executor", ex_mock), \
                mock.patch.object(actd, "_housekeeping_phase"), \
                mock.patch.object(actd, "_store2_tick"), \
                mock.patch.object(actd, "_refresh_model_knobs"), \
                mock.patch.object(actd, "reconcile_executing", return_value=0), \
                mock.patch.object(actd, "write_dashboard"), \
                mock.patch.object(actd, "build_dashboard", return_value={}), \
                mock.patch.object(actd, "detect_transitions", return_value=[]):
            actd.run_once(_cfg(), None, set())
        ex_mock.dispatch.assert_not_called()
        self.assertEqual(_reload(req_id).status, status)

    def test_agent_capture_lands_agent_channel_not_dispatchable(self):
        # boardctl 形捕获（via:"agent"）：agent_capture 通道 + PROPOSED 章，
        # 照旧进 triage 扩写；D86 起没有免批 lane，一整个 pass 之后也不会被推进
        req = self._capture_inbox("agent 发现的 follow-up", via="agent")
        self.assertEqual(req.sources[0]["channel"], "agent_capture")
        self.assertEqual(req.origin_trust, "proposed")
        self.assertEqual(req.status, State.RAISING.value)  # 照旧进 triage 扩写
        self._assert_stays_after_a_pass(req.id, State.RAISING.value)

    def test_remote_capture_lands_remote_channel_not_dispatchable(self):
        req = self._capture_inbox("远程投的活", via="remote")
        self.assertEqual(req.sources[0]["channel"], "remote_capture")
        self.assertEqual(req.origin_trust, "proposed")
        self._assert_stays_after_a_pass(req.id, State.RAISING.value)

    def test_unknown_via_fails_closed_to_remote_channel(self):
        req = self._capture_inbox("伪造 via 的捕获", via="owner")
        self.assertEqual(req.sources[0]["channel"], "remote_capture")
        self.assertEqual(req.origin_trust, "proposed")

    def test_owner_web_capture_keeps_hand_stamp_but_waits(self):
        """owner ingress 的章还是 hand，但 §78 后它不自跑（D86 起任何卡都不自跑）。

        原判例钉的是「via:"web" 保住 HAND ⇒ 免批自动派发」，与上面两条
        （agent/remote → origin:proposed）成对。§78/D80.4 退役了 hand 车道的入口，
        D86 删掉了最后一条免批 lane：这里仍钉章是 hand（对照组的 proposed 才有
        意义），同时钉真实接线上跑完一整个 pass 它还留在潜在任务等 owner 点。
        """
        req = self._capture_inbox("web 手打的活", via="web")
        self.assertEqual(req.origin_trust, "hand")
        req.set_status(State.DETECTED)
        req.cost_estimate_usd = 1.0
        req.target_repo = TMP_HOME
        registry.save(req)
        self._assert_stays_after_a_pass(req.id, State.DETECTED.value)

    def test_agent_capture_never_auto_dispatches_in_the_backlog(self):
        # 不批、不上 block 痕，留在潜在任务等 owner（§78 前是待审批列）
        req = self._capture_inbox("agent 投的候选", via="agent")
        req.set_status(State.DETECTED)
        req.cost_estimate_usd = 1.0
        req.target_repo = TMP_HOME
        registry.save(req)
        self._assert_stays_after_a_pass(req.id, State.DETECTED.value)
        after = _reload(req.id)
        self.assertEqual(after.status, State.DETECTED.value)
        self.assertNotIn("auto_dispatch_block", after.execution or {})


# --------------------------------------------------------------------------- #
# dashboard 投影（C-2 结构化 queued_reason / C-3·C-4 steers / add-only 字段）
# --------------------------------------------------------------------------- #
class TestDashboardProjection(WireBase):
    def test_queued_reason_concurrency(self):
        reqs = [
            _mk(f"R-77{i}", status=State.EXECUTING.value,
                execution={"session_id": f"sid-{i}"}) for i in range(3)
        ] + [_mk("R-779", status=State.APPROVED.value)]
        dash = build_dashboard(reqs=reqs, agents=[], cfg=config.Config())
        queued = [r for r in dash["running"] if r["state"] == "queued"]
        self.assertEqual(queued[0]["queued_reason"], {"kind": "concurrency"})

    def test_queued_reason_never_waiting_budget_d9(self):
        # 原判例 test_queued_reason_budget_only_for_auto_cards 钉「auto 卡台账
        # 3+4 > 5 → {kind: waiting_budget}，人批卡无 chip」。D9 retired v0.48.7：
        # 槽位有空时 auto 卡与人批卡都无 queued_reason——哪怕磁盘上还躺着一份
        # 升级前的台账；waiting_budget 这个 kind 不再由任何生产端投出。
        (config.STATE_DIR / _LEGACY_LEDGER).write_text(json.dumps({
            "date": _dt.date.today().isoformat(),
            "cards": {"R-other": 3.0, "R-780": 4.0}}), encoding="utf-8")
        auto = _mk("R-780", status=State.APPROVED.value, cost_estimate_usd=4.0,
                   execution={"auto_dispatched": True})
        manual = _mk("R-781", status=State.APPROVED.value,
                     cost_estimate_usd=4.0)
        dash = build_dashboard(reqs=[auto, manual], agents=[],
                               cfg=config.Config())
        by_id = {r["id"]: r for r in dash["running"]}
        self.assertNotIn("queued_reason", by_id["R-780"])
        self.assertNotIn("queued_reason", by_id["R-781"])
        self.assertNotIn("waiting_budget", json.dumps(dash))

    def test_steers_projection_delivered_then_queued(self):
        req = _mk("R-782", status=State.EXECUTING.value,
                  execution={"session_id": "sid-1"})
        first = steer.enqueue_steer(req, "第一条", ts="2026-08-30T01:00:00Z")
        steer.mark_delivered(req, [first],
                             delivered_at="2026-08-30T01:05:00Z")
        steer.enqueue_steer(req, "第二条", ts="2026-08-30T02:00:00Z")
        registry.save(req)
        agents = [{"id": "sid-1", "sessionId": "sid-1",
                   "state": "working", "pid": 42}]
        dash = build_dashboard(reqs=[req], agents=agents, cfg=config.Config())
        rows = [r for r in dash["running"] if r["id"] == "R-782"]
        steers = rows[0]["steers"]
        self.assertEqual(
            steers,
            [{"text": "第一条", "ts": "2026-08-30T01:00:00Z",
              "status": "delivered", "delivered_at": "2026-08-30T01:05:00Z"},
             {"text": "第二条", "ts": "2026-08-30T02:00:00Z",
              "status": "queued", "delivered_at": None}])

    def test_backlog_row_carries_trust_and_block(self):
        """§78：待决卡的那三个投影字段整体搬到 debt[]（原 needs_approval[]）。

        钉的行为一字未改：owner 在点「促成运行」之前必须看得见出身章、W17 算出
        的生效档位、以及免批闸拦下它的原因——只是这些如今长在潜在任务的卡面上
        （D80.2；needs_approval[] 按 D80.1 保留在线上但永远是空表）。
        """
        _mk("R-783", origin_trust="external",
            execution={"auto_dispatch_block": "t2_confirm"})
        plain = _mk("R-784")
        plain.origin_trust = None
        registry.save(plain)
        dash = build_dashboard(reqs=[registry.load("R-783"),
                                     registry.load("R-784")],
                               agents=[], cfg=config.Config())
        self.assertEqual(dash["needs_approval"], [])       # D80.1：留键，恒空
        by_id = {r["id"]: r for r in dash["debt"]}
        self.assertEqual(by_id["R-783"]["origin_trust"], "external")
        self.assertEqual(by_id["R-783"]["effective_tier"], "T2")  # W17 联动
        self.assertEqual(by_id["R-783"]["auto_dispatch_block"],
                         "t2_confirm")
        self.assertNotIn("origin_trust", by_id["R-784"])   # add-only：缺省不出
        self.assertNotIn("auto_dispatch_block", by_id["R-784"])


if __name__ == "__main__":
    unittest.main()
