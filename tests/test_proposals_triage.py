"""registry 写入护栏判例：高权限直跑会话的起止快照比对（CONTRACT §34bis / §34 / §78）。

§78（owner 决策 **D80.11**，issue #447）退役了提案车道，连同长在提案泳道头上的
「提案积压清理」按钮、它的 ``preset`` 词表值、那份固定 plan 与 preset 键上的在途
判重（§78.9 墓碑）。**护栏机械本体一起都不退**：起止快照、侧文件
``state/triage_snapshots/<id>.json``、跨进程写入台账、收割三条路的比对与
``[§34bis 护栏]`` 告警、每 pass 的孤儿快照清扫、以及「只检测告警、不回滚、绝不
阻塞提升」的检测型纪律——自此改锚在 owner 的**普通直跑卡**（§34 ``mode:"run"``）
上。那才是真正危险的那一类：没有 plan 预览、没有审批闸，会话带
``--dangerously-skip-permissions`` 且拿得到 REGISTRY_DIR 绝对路径，物理上写得进。
本文件的护栏判例因此逐条改锚到一次普通直跑（覆盖是资产，一条都不许掉）。

文件名保留不改：CONTRACT §34bis 墓碑块与 D80.11 都逐字点名
``tests/test_proposals_triage.py``，改名会让法条与 qa/coverage_inventory.json、
qa/mutation_targets.toml 的指针同时失真。

Runs entirely inside the sandbox AIASSISTANT_HOME (tests/__init__.py).
"""
import json
import subprocess
import unittest
import uuid
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import actd, executor
from act.lib import config, registry
from act.lib.registry import State


def _drop(body: dict) -> str:
    config.ensure_state_dirs()
    aid = str(uuid.uuid4())
    (config.INBOX_DIR / f"{aid}.json").write_text(
        json.dumps(body), encoding="utf-8")
    return aid


def _run_payload(**overrides) -> dict:
    """运行中列直跑框的载荷（§34 ``mode:"run"``）—— §78 之后护栏的锚点卡出生地。"""
    body = {"action": "capture", "text": "直接开跑：把这件事现在做掉",
            "mode": "run", "ts": "2026-08-07T00:00:00Z"}
    body.update(overrides)
    return body


def _fake_executor(session_id: str, before_launch=None):
    """注入的假 executor（判例纪律：绝不 spawn 真 claude）。

    忠实复刻 ``executor._record_launch_success``：成功派发**整体重建**
    ``execution``（只留 session_id / dispatched_at / log），再逐个**显式**
    捎上必须活过重建的键——``inbox_stem``（§34.1 crash-replay 幂等键）与
    ``direct_run``（§78 护栏认卡痕，§30 复活轮靠它重拍基线）。假件比真件更
    丢键的话，判例考的就是一个不存在的世界：复活轮护栏会在这里「绿着坏掉」。
    ``before_launch`` = 会话起跑瞬间的副作用（TOCTOU 判例用）。
    """
    class _FakeExecutor:
        DispatchError = RuntimeError

        @staticmethod
        def dispatch(req, cfg):
            if before_launch is not None:
                before_launch(req)
            ex = dict(req.execution or {})
            req.execution = {"session_id": session_id,
                             "dispatched_at": "2026-08-07T00:00:00Z"}
            for carried in ("inbox_stem", "direct_run"):
                if ex.get(carried):
                    req.execution[carried] = ex[carried]
            req.set_status(State.EXECUTING)
            registry.save(req)
            return req

    return _FakeExecutor


class TriageBase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()
        for p in config.INBOX_DIR.glob("*.json"):
            p.unlink()

    def _only_card(self):
        cards = registry.load_all()
        self.assertEqual(len(cards), 1, [c.id for c in cards])
        return cards[0]

    def _direct_run_card(self):
        """一次普通直跑（§34 ``mode:"run"``）—— §78 之后的护栏锚点卡。"""
        _drop(_run_payload())
        actd.process_inbox()
        return self._only_card()


class RetiredPresetKeyTests(TriageBase):
    """§78.9 墓碑（D80.11）：``preset`` 这个 inbox 键 add-only 留着，词表空了。

    任何 preset 值都不再注入固定 plan、不再改变 capture 的去路——垃圾/存量
    preset 绝不静默替换任务内容（§34 fail-safe 哲学，退役后一字不变）。
    """

    def test_retired_preset_value_is_a_plain_direct_run(self):
        # 老客户端（冻结的 Mac app 仍带着按钮）发来的词表值命中不了任何东西：
        # 卡照 §34 直跑铁律落地——approved + chat 交付 + 不进任何 repo——
        # 但既没有固定 plan，也没有 preset 标记。
        _drop(_run_payload(preset="proposals_triage"))
        actd.process_inbox()
        card = self._only_card()
        self.assertEqual(str(card.status), State.APPROVED.value)
        self.assertEqual(card.delivery_mode, "chat")   # §34 direct-run 铁律
        self.assertIsNone(card.target_repo)
        self.assertFalse(card.plan)                    # 固定 plan 已随按钮删除
        self.assertIsNone(card.preset)

    def test_retired_preset_machinery_is_gone_from_actd(self):
        # 墓碑判例（§78.9）：固定 plan 构造与 preset 在途判重两个 helper 随
        # 按钮一起删——哪一天有人把它们原样接回来，这里先红一次。
        self.assertFalse(hasattr(actd, "_proposals_triage_plan"))
        self.assertFalse(hasattr(actd, "_proposals_triage_in_flight"))

    def test_preset_without_run_mode_still_takes_the_capture_path(self):
        # fail-safe 的性质一字不变：缺 mode:"run" 的 capture 掉进「需要 owner
        # 再点一下」的那条路（§78 之后它的名字是潜在任务：raising → 扩写 →
        # detected），绝不静默启动 agent。
        _drop(_run_payload(preset="proposals_triage", mode=None))
        actd.process_inbox()
        card = self._only_card()
        self.assertEqual(str(card.status), State.RAISING.value)
        self.assertFalse(card.plan)

    def test_unknown_preset_is_plain_direct_run(self):
        _drop(_run_payload(preset="garbage_preset"))
        actd.process_inbox()
        card = self._only_card()
        self.assertEqual(str(card.status), State.APPROVED.value)
        self.assertFalse(card.plan)

    def test_non_string_preset_is_plain_direct_run(self):
        _drop(_run_payload(preset=42))
        actd.process_inbox()
        card = self._only_card()
        self.assertEqual(str(card.status), State.APPROVED.value)
        self.assertFalse(card.plan)


class GuardAnchorTests(TriageBase):
    """§78/D80.11 改锚判例：护栏认哪张卡（``triage_guard.guarded_card``）。

    锚点从退役的 ``preset`` 词表换成「这是一次直跑」——认错卡有两个方向的
    代价：认多了 = 每张 approved 卡都白拍一次全 registry 快照；认少了 =
    最危险的那一类会话无人取证。
    """

    def test_direct_run_capture_marks_a_guarded_card(self):
        card = self._direct_run_card()
        self.assertTrue((card.execution or {}).get("direct_run"))
        self.assertTrue(actd._guarded_card(card))

    def test_ordinary_capture_is_not_a_guarded_card(self):
        # 普通 capture 要经潜在任务列的一次人审提升才开跑（§78 模型），
        # 那一类卡不拍快照——护栏有针对性，不是每张卡的固定开销。
        _drop({"action": "capture", "text": "记一件事", "ts": "2026-08-07T00:00:00Z"})
        actd.process_inbox()
        self.assertFalse(actd._guarded_card(self._only_card()))

    def test_legacy_preset_card_is_still_guarded(self):
        # 存量在途卡（按钮退役当天还在跑的清理会话）不能在退役那一刻失去
        # 护栏：卡 YAML 里写着的词表字面量仍被认（常量与字面量同口径）。
        self.assertEqual(actd.PROPOSALS_TRIAGE_PRESET, "proposals_triage")
        legacy = registry.Requirement(id="R-legacy", title="存量清理卡",
                                      status=State.EXECUTING.value,
                                      preset="proposals_triage")
        registry.save(legacy)
        self.assertTrue(actd._guarded_card(registry.load("R-legacy")))

    def test_guard_anchor_survives_dispatch_execution_rebuild(self):
        # 判例（§34bis 原判的核心，改锚后逐字继承）：认卡标记必须活过派发。
        # executor._record_launch_success 把 execution **整体重建**成
        # {session_id, dispatched_at, log(+inbox_stem)}——旧锚点 `preset` 是
        # 卡**顶层**字段正是为了这一点。标记若只活在 execution 里，卡一派出
        # 去护栏就不再认得它，attach 复活轮永远重拍不出基线（那一轮全盲）。
        # 真 executor.dispatch（runner 注入，绝不 spawn claude）。
        card = self._direct_run_card()
        cfg = config.Config()
        cfg.memory_inject = False
        runner = mock.Mock(return_value=subprocess.CompletedProcess(
            ["claude"], 0, stdout="backgrounded · e88561e5\n", stderr=""))
        with mock.patch.object(executor, "has_remote", return_value=False), \
             mock.patch.object(executor.notify, "notify",
                               mock.Mock(return_value=True)):
            executor.dispatch(card, cfg, runner=runner)
        dispatched = registry.load(card.id)
        self.assertEqual(str(dispatched.status), State.EXECUTING.value)
        self.assertTrue(actd._guarded_card(dispatched))


class RegistryGuardTests(TriageBase):
    """机械护栏判例：只读红线不止 prompt 级 —— 起止快照比对兜底（§34bis/§78）。

    直跑会话带 --dangerously-skip-permissions 且拿得到 registry 绝对路径，
    物理上写得进；护栏 = dispatch 拍快照、收割比对，非 actd 写入 → notes
    警告 + notify 告警（检测型，不改会话权限模型、不阻塞提升）。
    """

    def _dispatched_card(self, session_id="sid-guard", before_launch=None):
        """经真实 ``dispatch_approved`` 起跑（注入假 executor）的直跑卡。"""
        card = self._direct_run_card()
        with mock.patch.object(actd, "executor",
                               _fake_executor(session_id, before_launch)):
            actd.dispatch_approved(config.Config())
        return registry.load(card.id)

    def test_dispatch_stamps_registry_snapshot(self):
        # P2-1：快照落 state/triage_snapshots/ 侧文件，卡 YAML 只留引用
        # （全 registry 清单进卡会膨胀且用户直接看见账本）。
        card = self._dispatched_card()
        ref = (card.execution or {}).get("registry_snapshot_ref")
        self.assertTrue(ref)
        snap_file = Path(ref)
        self.assertTrue(snap_file.is_file())
        payload = json.loads(snap_file.read_text(encoding="utf-8"))
        self.assertRegex(payload["at"], r"^\d{4}-\d{2}-\d{2}T")
        snap = payload["files"]
        self.assertIsInstance(snap, dict)
        self.assertIn(f"{card.id}.yaml", snap)
        for v in snap.values():                    # 形状 "size:mtime_ns"
            self.assertRegex(str(v), r"^\d+:\d+$")

    def test_plain_direct_run_gets_a_snapshot(self):
        # §78/D80.11 改锚后的反转判例（原判「普通直跑不拍快照——护栏只属于
        # 清理卡」）：普通直跑**就是**新的护栏锚点，起跑必拍快照。
        card = self._dispatched_card(session_id="sid-plain")
        self.assertIn("registry_snapshot_ref", card.execution or {})
        self.assertTrue(actd._triage_snapshot_path(card.id).exists())

    def test_approved_non_direct_run_card_gets_no_snapshot(self):
        # 原判保住的那一半：护栏有针对性。经潜在任务列人审提升的普通
        # approved 卡起跑不拍快照——否则每张卡每次派发都白拍一次全 registry。
        req = registry.Requirement(id=registry.next_id(),
                                   title="人审提升的普通卡",
                                   status=State.APPROVED.value)
        registry.save(req)
        with mock.patch.object(actd, "executor", _fake_executor("sid-approved")):
            actd.dispatch_approved(config.Config())
        card = registry.load(req.id)
        self.assertNotIn("registry_snapshot_ref", card.execution or {})
        self.assertFalse(actd._triage_snapshot_path(req.id).exists())

    def test_snapshot_mismatch_warns_and_notifies(self):
        card = self._dispatched_card()
        ex = dict(card.execution or {})
        snap_file = Path(ex["registry_snapshot_ref"])
        # 会话越权模拟：绕过 registry API 直接落盘（不进写入台账）
        rogue = config.REGISTRY_DIR / "R-rogue.yaml"
        rogue.write_text("id: R-rogue\ntitle: tampered\n", encoding="utf-8")
        with mock.patch.object(actd.notify, "notify",
                               mock.Mock(return_value=True)) as ntf:
            actd._check_triage_registry_guard(card, ex)
        self.assertIn("[§34bis 护栏]", card.notes)
        self.assertIn("R-rogue.yaml", card.notes)
        ntf.assert_called_once()
        # 快照用后即焚（引用 pop + 侧文件删）—— 同一轮不重复告警
        self.assertNotIn("registry_snapshot_ref", ex)
        self.assertFalse(snap_file.exists())

    def test_pipeline_writes_do_not_alarm(self):
        card = self._dispatched_card()
        ex = dict(card.execution or {})
        # 管线的合法写入：经 registry API 落盘（进写入台账）→ 不算嫌疑
        registry.upsert(registry.Requirement(
            id=registry.next_id(), title="直跑会话期间管线正常新卡"))
        with mock.patch.object(actd.notify, "notify",
                               mock.Mock(return_value=True)) as ntf:
            actd._check_triage_registry_guard(card, ex)
        ntf.assert_not_called()
        self.assertNotIn("[§34bis 护栏]", card.notes or "")

    def test_cross_process_pipeline_writes_do_not_alarm(self):
        # P1 假警面：radar（slack/gmail/obsidian cron）是独立进程直写
        # registry——台账必须跨进程（state/registry_writes.jsonl），否则
        # 直跑会话十几分钟里 radar 任何落卡都会假警。模拟 = 绕开本进程
        # 内存集合：直接落卡文件 + 手写台账行（另一个进程会这么留痕）。
        card = self._dispatched_card()
        ex = dict(card.execution or {})
        other = config.REGISTRY_DIR / "R-radar.yaml"
        other.write_text("id: R-radar\ntitle: radar 落卡\n", encoding="utf-8")
        self.assertNotIn("R-radar.yaml", registry._PROC_WRITES)
        ts = "2999-01-01T00:00:00Z"      # 必然 >= 快照起始 ts
        with registry._writes_journal_path().open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"f": "R-radar.yaml", "ts": ts}) + "\n")
        with mock.patch.object(actd.notify, "notify",
                               mock.Mock(return_value=True)) as ntf:
            actd._check_triage_registry_guard(card, ex)
        ntf.assert_not_called()
        self.assertNotIn("[§34bis 护栏]", card.notes or "")

    def test_journal_survives_actd_restart(self):
        # 台账持久化的另一半收益：actd 中途重启（内存集合清零）不再把
        # 重启前的管线合法写入误报成会话越权。
        card = self._dispatched_card()
        ex = dict(card.execution or {})
        registry.upsert(registry.Requirement(
            id=registry.next_id(), title="重启前的管线正常新卡"))
        registry._PROC_WRITES.clear()    # 模拟 actd 重启
        with mock.patch.object(actd.notify, "notify",
                               mock.Mock(return_value=True)) as ntf:
            actd._check_triage_registry_guard(card, ex)
        ntf.assert_not_called()
        self.assertNotIn("[§34bis 护栏]", card.notes or "")

    def test_proc_writes_do_not_exempt_pre_snapshot_history(self):
        # 判例（bot review P0）：本 actd 进程在快照**前**写过的卡被会话
        # 篡改，必须照常告警——内存兜底映射带 ts、与台账同按快照起始 ts
        # 过滤，绝不因「进程写过这个文件名」就永久豁免（直跑会话手边最
        # 现实的篡改目标正是这些老卡）。
        self._direct_run_card()
        # 快照前就存在的卡：本进程写过（内存映射有名字），但 ts 远早于快照
        victim = config.REGISTRY_DIR / "R-victim.yaml"
        victim.write_text("id: R-victim\ntitle: 老卡\n", encoding="utf-8")
        registry._PROC_WRITES["R-victim.yaml"] = "2000-01-01T00:00:00Z"
        with mock.patch.object(actd, "executor", _fake_executor("sid-hist")):
            actd.dispatch_approved(config.Config())
        card = next(c for c in registry.load_all()
                    if str(c.status) == State.EXECUTING.value)
        ex = dict(card.execution or {})
        victim.write_text("id: R-victim\ntitle: 被会话篡改的老卡内容\n",
                          encoding="utf-8")
        with mock.patch.object(actd.notify, "notify",
                               mock.Mock(return_value=True)) as ntf:
            actd._check_triage_registry_guard(card, ex)
        self.assertIn("R-victim.yaml", card.notes or "")
        ntf.assert_called_once()

    def test_snapshot_predates_session_launch(self):
        # 判例（bot review P1，TOCTOU）：快照必须先于会话启动——会话起跑
        # 瞬间（dispatch 返回前）的越权写不得被拍进基线。
        def _tamper(req):
            (config.REGISTRY_DIR / "R-early.yaml").write_text(
                "id: R-early\n", encoding="utf-8")

        card = self._dispatched_card(session_id="sid-toctou",
                                     before_launch=_tamper)
        ex = dict(card.execution or {})
        with mock.patch.object(actd.notify, "notify",
                               mock.Mock(return_value=True)) as ntf:
            actd._check_triage_registry_guard(card, ex)
        self.assertIn("R-early.yaml", card.notes or "")
        ntf.assert_called_once()

    def test_failed_dispatch_burns_the_orphan_snapshot(self):
        # 起跑崩了 → 预拍的快照无主即焚（重试下轮重拍），不留残留。
        card = self._direct_run_card()

        class _FailingExecutor:
            DispatchError = RuntimeError

            @staticmethod
            def dispatch(req, cfg):
                raise RuntimeError("launch boom")

        with mock.patch.object(actd, "executor", _FailingExecutor):
            actd.dispatch_approved(config.Config())
        self.assertFalse(actd._triage_snapshot_path(card.id).exists())

    def test_stop_to_review_runs_guard(self):
        # 判例（bot review P1）：手动「去待验收」也是收割提升——护栏同样
        # 比对，否则会话改卡后用户手点停出，快照永不检查、侧文件残留。
        card = self._dispatched_card()
        snap_file = actd._triage_snapshot_path(card.id)
        self.assertTrue(snap_file.exists())
        rogue = config.REGISTRY_DIR / "R-rogue3.yaml"
        rogue.write_text("id: R-rogue3\n", encoding="utf-8")
        harvest = mock.Mock(return_value={"delivered_summary": "半程交付",
                                          "final_draft": "FINAL DRAFT"})
        stop = mock.Mock(return_value=(True, True, "stopped"))
        with mock.patch.object(actd.executor, "harvest_delivery", harvest), \
             mock.patch.object(actd.executor, "stop_session_confirmed", stop), \
             mock.patch.object(actd.notify, "notify",
                               mock.Mock(return_value=True)) as ntf:
            actd._apply_decision(card, "stop_to_review", None)
        saved = registry.load(card.id)
        self.assertEqual(str(saved.status), State.REVIEW.value)  # 不阻塞落 review
        self.assertIn("R-rogue3.yaml", saved.notes or "")
        self.assertNotIn("registry_snapshot_ref", saved.execution or {})
        self.assertFalse(snap_file.exists())                     # 用后即焚
        ntf.assert_called()

    def test_sweep_clears_orphans_keeps_live_snapshots(self):
        # 判例（bot review P2）：没走到收割的卡（丢弃/打回废弃）留下的
        # 快照侧文件由每 pass 清扫兜底；在途卡（approved/executing）的
        # 快照绝不误删。
        card = self._dispatched_card()                   # executing，有快照
        live = actd._triage_snapshot_path(card.id)
        orphan = actd._triage_snapshot_path("R-gone")
        orphan.parent.mkdir(parents=True, exist_ok=True)
        orphan.write_text("{}", encoding="utf-8")
        actd._sweep_triage_snapshots()
        self.assertTrue(live.exists())
        self.assertFalse(orphan.exists())

    def test_attach_revival_restamps_snapshot(self):
        # 判例（bot review P2）：首轮快照随收割消费后，review 卡被 attach
        # 复活（§30 回流）→ actd 在标记 _review_active 的同一轮重拍快照
        # 挂回 registry_snapshot_ref —— 复活轮不再是护栏盲区。快照侧文件
        # 属 review 卡，每 pass 清扫不得误删（等复活轮收割消费）。
        card = self._dispatched_card()
        ex = dict(card.execution or {})
        ex.pop("registry_snapshot_ref", None)      # 模拟首轮已收割消费
        card.execution = ex
        card.set_status(State.REVIEW)
        registry.save(card)
        actd._triage_snapshot_path(card.id).unlink(missing_ok=True)
        actd._reconcile_review_attach(card, {"sid-guard": {"state": "working"}})
        saved = registry.load(card.id)
        ref = (saved.execution or {}).get("registry_snapshot_ref")
        self.assertTrue(ref)
        self.assertTrue(Path(ref).is_file())
        self.assertTrue((saved.execution or {}).get("_review_active"))
        actd._sweep_triage_snapshots()             # review 卡的快照受保护
        self.assertTrue(Path(ref).is_file())

    def test_revival_round_end_runs_guard(self):
        # 判例（bot review P2）：复活轮活动结束（会话 done）的重新收割同样
        # 过护栏 —— 复活期间的非 actd 写入进 notes 告警，快照用后即焚。
        card = self._dispatched_card()
        ex = dict(card.execution or {})
        ex.pop("registry_snapshot_ref", None)
        card.execution = ex
        card.set_status(State.REVIEW)
        registry.save(card)
        actd._reconcile_review_attach(card, {"sid-guard": {"state": "working"}})
        card = registry.load(card.id)
        snap_file = actd._triage_snapshot_path(card.id)
        self.assertTrue(snap_file.exists())
        rogue = config.REGISTRY_DIR / "R-rogue4.yaml"
        rogue.write_text("id: R-rogue4\n", encoding="utf-8")
        harvest = mock.Mock(return_value={"delivered_summary": "复活轮交付",
                                          "final_draft": "FINAL DRAFT"})
        with mock.patch.object(actd.executor, "harvest_delivery", harvest), \
             mock.patch.object(actd.notify, "notify",
                               mock.Mock(return_value=True)) as ntf:
            actd._reconcile_review_attach(card, {"sid-guard": {"state": "done"}})
        saved = registry.load(card.id)
        self.assertIn("R-rogue4.yaml", saved.notes or "")
        self.assertNotIn("registry_snapshot_ref", saved.execution or {})
        self.assertNotIn("_review_active", saved.execution or {})
        self.assertFalse(snap_file.exists())       # 用后即焚
        ntf.assert_called()

    def test_guard_fires_on_review_promotion_path(self):
        # 集成判例：收割提升（reconcile done 分支）真的挂着护栏 —— 越权
        # 差异在提升待验收的同一轮被写进 notes，卡照常进 review 不被阻塞。
        card = self._dispatched_card()
        rogue = config.REGISTRY_DIR / "R-rogue2.yaml"
        rogue.write_text("id: R-rogue2\n", encoding="utf-8")
        agent = {"id": "sid-guard", "sessionId": "sid-guard", "state": "done",
                 "cwd": "/tmp/wt", "name": "bg agent",
                 "startedAt": "2026-08-07T00:00:00Z"}
        fake_harvest = mock.Mock(return_value={"delivered_summary": "交付",
                                               "final_draft": "FINAL DRAFT"})
        with mock.patch.object(actd, "_run_claude_agents",
                               return_value=[agent]), \
             mock.patch.object(actd.executor, "harvest_delivery", fake_harvest), \
             mock.patch.object(actd.notify, "notify",
                               mock.Mock(return_value=True)) as ntf:
            actd.reconcile_executing(config.Config(), set())
        saved = registry.load(card.id)
        self.assertEqual(str(saved.status), State.REVIEW.value)   # 不阻塞提升
        self.assertIn("R-rogue2.yaml", saved.notes)
        ntf.assert_called()


if __name__ == "__main__":
    unittest.main()
