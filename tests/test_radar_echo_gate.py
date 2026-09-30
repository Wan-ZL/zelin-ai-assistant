"""§45 出生资格闸在 radar._process_note 里的接线 — 回声环的行为契约.

钉住的行为（Zelin 2026-07-25 拍板「屏幕 OCR 不发起卡片」）：

- screen 来源 + triage 判 new_proposal（含 triage 失败的宁可打扰回退）
  -> 拦截：零新卡、``echo_blocked`` 计数、marker 照常推进（不是失败）；
- screen 来源 + triage 判 relates_to 且目标卡还开着 -> fold 放行（佐证是
  屏幕的正职），不出新卡；
- screen 来源 + relates_to 命中已完结卡 -> 拦截（re-raise/follow-up 也是
  新卡，屏幕无此权力）；
- audio×human 的硬 deadline 紧急项照旧**响一声**（回归：FULL 行为不变）；
- 缺 provenance/speaker 的老式提取输出 -> LIMITED：落潜在任务但**安静**。

**§78（owner 决策 D80.7，issue #447；法条正文 = §45 §78 修法 + §78.6）改判之后
本文件钉的是什么**：提案车道
退役，FULL 与 LIMITED 的**落点**都变成 ``detected``（潜在任务）——若只断言
status，这个文件里半数用例会同时为真、§45 从此无人执法（回声环那一刀就成了
装饰）。所以分界按 §45 §78 修法平移到**打扰面**，本文件跟着平移：

- **观察口径 = 守护进程真实的通知差分**（``dashboard.build_dashboard`` →
  ``alerts.detect_transitions``），不是读一个字段。安静出生 = 卡在潜在任务列
  里看得见（没有一张卡因为静默而隐形）、但 ``detect_transitions`` 一条不发；
- **FULL** = 落潜在任务 **并**响一次新卡/回锅通知；
- **LIMITED / CORROBORATE** = 落同一条车道、盖 add-only ``quiet_birth``、零通知；
- **CORROBORATE 的一刀一字不变**：屏幕永不发起卡片，唯一放行形态仍是 fold 进
  开着的卡。

PR80 审查加固（闸门跟着候选走完全程，triage LLM 输出不是豁免通道）：

- P1-1：非 FULL 来源 fold 进 detected 卡时，``needs_action=true`` 不得把目标
  卡提升——§78 之后**谁都无处可升**（提升这一步整条删掉，连带它唯一的
  ``radar_echo_blocked{stage=fold_promotion}`` 发射点），法条的残值改由「fold
  不得把一张安静的卡变成一次打扰」承担：目标卡的 status 与 ``quiet_birth``
  都不许被这次 fold 改写；
- P1-2：LIMITED 命中完结卡的 re-raise/follow-up（relates_to 路径与
  merge_or_new 内部路径）天花板 = detected **且安静**；FULL 零回归（照响）；
- P1-3：``radar_echo_blocked`` 事件纯元数据——绝不携带 title/note 等屏幕内容
  （宪法第 9 条 / docs/TELEMETRY.md 红线）。

Runs entirely inside the sandbox AIASSISTANT_HOME (tests/__init__.py).
"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import radar
from act.lib import (analytics, config, dashboard, provenance, quick_capture,
                     registry)
from act.lib.actd import alerts

BASE = 1_760_000_000.0


def _item(title, provenance=None, speaker=None, hardness="hard",
          deadline="2026-07-30", urgent=True):
    d = {"title": title, "type": "action", "tier": "T1", "hardness": hardness,
         "deadline": deadline, "cost_estimate_usd": None, "urgent": urgent,
         "quote": "please do the thing"}
    if provenance is not None:
        d["provenance"] = provenance
    if speaker is not None:
        d["speaker"] = speaker
    return d


def _triager_for(decision: dict):
    def triager(prompt):
        return subprocess.CompletedProcess(
            args=["triage"], returncode=0,
            stdout=json.dumps(decision, ensure_ascii=False))
    return triager


class EchoGateBase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        self._cleanup()
        self.tmp = tempfile.TemporaryDirectory(prefix="radar-echo-")
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self._cleanup)
        self.raw = Path(self.tmp.name) / "2 - raw"
        self.raw.mkdir(parents=True)
        config.CONFIG_PATH.write_text(
            f'sources:\n  obsidian_raw: "{self.raw.as_posix()}"\n', encoding="utf-8")
        # events.jsonl 是共享沙箱里的累积文件——记住水位，只断言本测试新增的。
        self._events_offset = len(self._event_lines())

    @staticmethod
    def _event_lines() -> list:
        try:
            return [ln for ln in analytics.EVENTS_PATH.read_text(
                encoding="utf-8").splitlines() if ln.strip()]
        except OSError:
            return []

    def _new_events(self, name=None) -> list:
        out = []
        for ln in self._event_lines()[self._events_offset:]:
            ev = json.loads(ln)
            if name is None or ev.get("event") == name:
                out.append(ev)
        return out

    @staticmethod
    def _cleanup():
        if config.CONFIG_PATH.exists():
            config.CONFIG_PATH.unlink()
        for p in (config.STATE_DIR / radar.MARKER_PATH_NAME,
                  config.STATE_DIR / radar.FAILED_QUEUE_NAME):
            if p.exists():
                p.unlink()
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()

    def _note(self, name="2026-07-25-screenpipe-x.md", text="note body",
              mtime=BASE):
        p = self.raw / name
        p.write_text(text, encoding="utf-8")
        os.utime(p, (mtime, mtime))
        return p

    def _scan(self, items, decision):
        runner = lambda text: json.dumps(items, ensure_ascii=False)  # noqa: E731
        return radar.scan(runner=runner, triager=_triager_for(decision))

    @staticmethod
    def _board_msgs(before: list, after: list) -> list:
        """§45 §78 的唯一诚实观察口径：守护进程自己的通知差分.

        提案列退役后（§78，D80.7）「FULL vs LIMITED」不再是两个 status，而是
        「响不响」。所以这里跑的是真的投影（§2 ``debt[]``）+ 真的差分
        （§40.6 ``alerts.detect_transitions``），而不是读一个 ``quiet_birth``
        字段——字段是实现，打扰面才是法条要保的东西。``agents=[]`` 掐掉投影
        里的 ``claude agents`` 探针（测试绝不 spawn 真 claude）。
        """
        prev = dashboard.build_dashboard(reqs=before, agents=[])
        curr = dashboard.build_dashboard(reqs=after, agents=[])
        return alerts.detect_transitions(prev, curr)

    def _scan_and_diff(self, items, decision):
        """(summary, notifications)：扫描前后各取一次盘面，跑一次真的通知差分。"""
        before = registry.load_all()
        summary = self._scan(items, decision)
        return summary, self._board_msgs(before, registry.load_all())


class ScreenOriginationBlockedTestCase(EchoGateBase):
    def test_screen_new_proposal_is_blocked_not_filed(self):
        self._note()
        summary = self._scan(
            [_item("走之前把 G-1650 从出纸口拿走", provenance="screen",
                   speaker="assistant")],
            {"action": "new_proposal", "confidence": "high"})
        self.assertEqual(summary["echo_blocked"], 1)
        self.assertEqual(summary["reconciled"], 0)
        self.assertEqual(summary["cards"], 0)
        self.assertEqual(registry.load_all(), [])          # 零新卡
        self.assertEqual(radar._read_marker(), BASE)       # 拦截不是失败

    def test_screen_blocks_even_the_triage_fallback(self):
        # triage 挂了 -> quick_capture 的宁可打扰回退是 new_proposal；对
        # screen 来源，这个回退同样无出生权。
        self._note()
        runner = lambda text: json.dumps(  # noqa: E731
            [_item("看板卡片标题被拍了回来", provenance="screen", speaker="zelin")])
        def broken_triager(prompt):
            return subprocess.CompletedProcess(args=["triage"], returncode=1,
                                               stdout="boom")
        summary = radar.scan(runner=runner, triager=broken_triager)
        self.assertEqual(summary["echo_blocked"], 1)
        self.assertEqual(registry.load_all(), [])

    def test_screen_relates_to_open_card_still_folds(self):
        # 目标卡刻意留在退役的 card_sent 上：§78 说这个值**合法但没人再写**，
        # 盘上的存量卡照样读得出、照样投影进潜在任务列。屏幕佐证 fold 进一张
        # 存量卡时，既不出新卡也不替 owner 搬卡（归并扫描才是唯一的搬运工）。
        target = registry.Requirement(
            id="R-100", title="季度报告", status="card_sent",
            sources=[{"who": "boss", "channel": "slack",
                      "date": "2026-07-20", "quote": "季度报告"}])
        registry.save(target)
        self._note()
        summary = self._scan(
            [_item("季度报告又被提了一次", provenance="screen", speaker="human",
                   hardness="soft", deadline=None, urgent=False)],
            {"action": "relates_to", "req": "R-100",
             "note": "屏幕上又见到一次", "needs_action": False})
        self.assertEqual(summary["echo_blocked"], 0)
        reqs = registry.load_all()
        self.assertEqual(len(reqs), 1)                     # fold，不出新卡
        self.assertIn("[radar] 屏幕上又见到一次", reqs[0].notes or "")
        # §45/§78：屏幕佐证不得借 act-now 改写目标卡的状态（退役值也不例外）
        self.assertEqual(reqs[0].status, "card_sent")

    def test_screen_ignore_is_not_counted_as_echo_blocked(self):
        # P2-8：triage 判 ignore 的项本来就不会成卡——混进 echo_blocked 会
        # 虚高拦截率的审计口径。它走常规 ignore 路径与留痕。
        self._note()
        summary = self._scan(
            [_item("纯信息性的屏幕内容", provenance="screen", speaker="human")],
            {"action": "ignore", "reason": "纯信息"})
        self.assertEqual(summary["echo_blocked"], 0)
        self.assertEqual(summary["reconciled"], 0)
        self.assertEqual(registry.load_all(), [])
        self.assertEqual(self._new_events("radar_echo_blocked"), [])
        self.assertEqual(
            [e.get("action") for e in self._new_events("radar_triage")],
            ["ignore"])

    def test_screen_relates_to_resolved_card_is_blocked(self):
        done = registry.Requirement(
            id="R-101", title="已发布的 blog", status="delivered",
            sources=[{"who": "boss", "channel": "slack",
                      "date": "2026-07-20", "quote": "blog"}])
        registry.save(done)
        self._note()
        summary = self._scan(
            [_item("blog 相关又出现在屏幕上", provenance="screen",
                   speaker="assistant", hardness="soft", deadline=None)],
            {"action": "relates_to", "req": "R-101",
             "note": "助手在汇报完成", "needs_action": True})
        self.assertEqual(summary["echo_blocked"], 1)
        # 没有 follow-up 卡出生；已完结卡原样
        self.assertEqual(len(registry.load_all()), 1)
        self.assertEqual(registry.load("R-101").status, "delivered")


class NonScreenLanesTestCase(EchoGateBase):
    """FULL 出生响一声、LIMITED 出生安静——§45 §78 修法的两档（D80.7）."""

    def test_audio_human_hard_deadline_births_loud(self):
        # §78 之前这条断言的是「直达提案列」（status=card_sent）。车道退役后
        # 落点对两档都是 detected，所以 FULL 的可观测特权平移到**通知**：
        # 卡进潜在任务列 **且** 守护进程响一次新卡通知。
        self._note()
        summary, msgs = self._scan_and_diff(
            [_item("给 Arash 交评审结论", provenance="audio", speaker="human")],
            {"action": "new_proposal", "confidence": "high"})
        self.assertEqual(summary["echo_blocked"], 0)
        self.assertEqual(summary["cards"], 1)
        (req,) = registry.load_all()
        self.assertEqual(req.status, "detected")
        self.assertFalse(getattr(req, "quiet_birth", False))
        self.assertEqual([m[2] for m in msgs], [req.id])   # 响了，且只响这一张

    def test_missing_provenance_fields_park_in_backlog_quietly(self):
        # 老式提取输出（无两字段）= unknown×unknown = LIMITED：即使
        # hard+deadline+urgent 也只是**安静的安全网**——§78 之前靠「只落备选、
        # 不进提案列」承担，退役后落点与 FULL 相同，唯一还分得开两档的观察面
        # 就是这条差分：卡看得见（潜在任务列里有它），但一声不响。
        self._note()
        summary, msgs = self._scan_and_diff(
            [_item("来历不明的硬任务")],
            {"action": "new_proposal", "confidence": "high"})
        self.assertEqual(summary["echo_blocked"], 0)
        self.assertEqual(summary["cards"], 0)
        (req,) = registry.load_all()
        self.assertEqual(req.status, "detected")
        self.assertTrue(req.quiet_birth)
        self.assertEqual(msgs, [])                         # 安静出生
        # 「安静」绝不等于「隐身」：这张卡照常在潜在任务列里（宪法第 3 条）。
        board = dashboard.build_dashboard(reqs=registry.load_all(), agents=[])
        self.assertEqual([r["id"] for r in board["debt"]], [req.id])

    def test_audio_assistant_voice_is_corroborate_only(self):
        self._note()
        summary = self._scan(
            [_item("TTS 播报里的行动项", provenance="audio", speaker="assistant")],
            {"action": "new_proposal", "confidence": "high"})
        self.assertEqual(summary["echo_blocked"], 1)
        self.assertEqual(registry.load_all(), [])


class FoldPromotionRetiredTestCase(EchoGateBase):
    """P1-1 在 §78 之后：fold 的 ``needs_action`` 通道**对谁都不再提升**.

    原法条：非 FULL 来源 fold 进一张 detected 卡时，``needs_action=true`` 不得
    把目标卡推进提案列（推不动才留痕 ``radar_echo_blocked{stage=
    fold_promotion}``）。§78 把提案列整条退役，那次提升的代码
    （``quick_capture._fold_into_open`` 的分支）与它**唯一的**
    ``fold_promotion`` 发射点一起删了——所以「提升被压平」现在是结构性的，
    不再需要一次运行期判决，那个 stage 的事件自此永不出现（词表按 add-only
    留在 §45 里，只为读得懂历史事件行）。

    法条的残值——「屏幕/来历不明的回声不许把一张安静的卡变成一次打扰」——
    平移到这里钉：fold 只并信息，目标卡的 status 与 ``quiet_birth`` 都不许被
    这次 fold 改写，守护进程一声不响。目标卡刻意种成安静出生的（LIMITED 出身），
    这样「fold 偷偷给它解除静默」这种回归会当场翻红。
    """

    def _seed_detected(self):
        target = registry.Requirement(
            id="R-200", title="季度报告", status="detected", quiet_birth=True,
            sources=[{"who": "boss", "channel": "slack",
                      "date": "2026-07-20", "quote": "季度报告"}])
        registry.save(target)
        return target

    def _assert_folded_silently(self, msgs, note_text):
        (req,) = registry.load_all()
        self.assertIn(f"[radar] {note_text}", req.notes or "")
        self.assertEqual(req.status, "detected")       # 没有提升这回事了
        self.assertTrue(req.quiet_birth)               # 也没被解除静默
        self.assertEqual(msgs, [])                     # 守护进程一声不响
        # 提升那一步没了，它唯一的留痕点也就没了——不是被静默吞掉，是不存在。
        self.assertEqual(
            [e for e in self._new_events("radar_echo_blocked")
             if e.get("stage") == "fold_promotion"], [])
        return req

    def test_screen_needs_action_fold_cannot_promote(self):
        self._seed_detected()
        self._note()
        summary, msgs = self._scan_and_diff(
            [_item("季度报告的屏幕回声", provenance="screen", speaker="human",
                   hardness="soft", deadline=None, urgent=False)],
            {"action": "relates_to", "req": "R-200",
             "note": "屏幕上又见到一次", "needs_action": True})
        self.assertEqual(summary["echo_blocked"], 0)   # fold 本身放行
        self._assert_folded_silently(msgs, "屏幕上又见到一次")

    def test_limited_needs_action_fold_cannot_promote(self):
        # 缺 provenance/speaker（老式提取）= LIMITED：同样无提升权。
        self._seed_detected()
        self._note()
        _summary, msgs = self._scan_and_diff(
            [_item("季度报告的来历不明回声", hardness="soft", deadline=None,
                   urgent=False)],
            {"action": "relates_to", "req": "R-200",
             "note": "来源判不出", "needs_action": True})
        self._assert_folded_silently(msgs, "来源判不出")

    def test_audio_human_needs_action_fold_no_longer_promotes(self):
        # §78 的行为改动落点：FULL 来源**也**不再提升（提案列没了，无处可升）。
        # 这条留着不是装饰——它钉的是「提升这一步被删干净了」：任何人日后想
        # 给 fold 重新接一条改状态/解除静默的通道，都会先在这里翻红。
        self._seed_detected()
        self._note()
        _summary, msgs = self._scan_and_diff(
            [_item("季度报告要加速", provenance="audio", speaker="human",
                   hardness="soft", deadline=None, urgent=False)],
            {"action": "relates_to", "req": "R-200",
             "note": "会上催了", "needs_action": True})
        self._assert_folded_silently(msgs, "会上催了")
        self.assertEqual(self._new_events("radar_echo_blocked"), [])


class ResolvedCardCeilingTestCase(EchoGateBase):
    """P1-2：LIMITED 命中完结卡的 re-raise/follow-up 天花板 = detected **且安静**.

    §78 之前这条天花板的全部含义是「只许落 detected，不许落 card_sent」——车道
    退役后 detected 是所有人的落点，光断言它等于什么都没断言。天花板因此按
    §45 §78 修法读作「落潜在任务 **且不通知**」：R-020/R-093 那个回声环（完结卡
    在屏幕/来历不明的来源里再现一次 → 生一张新卡 + 响一声）里真正伤人的是**那
    一声**，所以这里钉的就是那一声。
    """

    def _seed_delivered(self, req_id="R-101", title="发布 Q2 博客文章",
                        **kw):
        done = registry.Requirement(
            id=req_id, title=title, status="delivered",
            sources=[{"who": "boss", "channel": "slack",
                      "date": "2026-07-20", "quote": title}], **kw)
        registry.save(done)
        return done

    def test_limited_relates_to_resolved_followup_capped_and_quiet(self):
        # (a) 无 provenance 字段 relates_to delivered 卡 + needs_action=true
        # —— 修复前生出 card_sent follow-up + 通知（R-020/R-093 回声环）。
        self._seed_delivered()
        self._note()
        summary, msgs = self._scan_and_diff(
            [_item("准备开发者大会演讲稿", hardness="soft", deadline=None)],
            {"action": "relates_to", "req": "R-101",
             "note": "同一线程的新事项", "needs_action": True})
        self.assertEqual(summary["cards"], 0)          # 不是一张要 owner 看的卡
        self.assertEqual(registry.load("R-101").status, "delivered")
        (child,) = [r for r in registry.load_all() if r.improvement_of == "R-101"]
        self.assertEqual(child.status, "detected")     # 天花板：潜在任务
        self.assertTrue(child.quiet_birth)             # …且安静出生
        self.assertIn("既往卡 R-101 的后续", child.summary or "")
        self.assertEqual(msgs, [])                     # 回声环那一声被掐掉

    def test_limited_relates_to_resolved_reraise_capped_and_quiet(self):
        # 同题重述（same_task）走的是原卡翻回。翻回 = 卡从 completed 列跳进
        # 潜在任务列 = ``debt[]`` 里冒出一行新的，所以它**本来会**触发 §40 的
        # 「回锅」通知；LIMITED 出身的重述不配打断 owner，这里钉住它不响。
        self._seed_delivered(title="写周报自动化脚本")
        self._note()
        summary, msgs = self._scan_and_diff(
            [_item("写周报自动化脚本")],   # hard+deadline = 有增量的重述
            {"action": "relates_to", "req": "R-101", "needs_action": True})
        self.assertEqual(summary["cards"], 0)
        self.assertEqual(len(registry.load_all()), 1)  # 翻回原卡，不出新卡
        parent = registry.load("R-101")
        self.assertEqual(parent.status, "detected")
        self.assertTrue(parent.quiet_birth)
        self.assertEqual(msgs, [])

    def test_limited_new_proposal_hitting_resolved_title_capped_and_quiet(self):
        # (b) new_proposal 撞完结卡标题 -> merge_or_new 内部 re-raise：
        # cap_detected 必须一路跟进（修复前无视 high_confidence=False）。
        self._seed_delivered(req_id="R-102", title="写周报自动化脚本")
        self._note()
        summary, msgs = self._scan_and_diff(
            [_item("写周报自动化脚本")],
            {"action": "new_proposal", "confidence": "high"})
        self.assertEqual(summary["cards"], 0)
        self.assertEqual(len(registry.load_all()), 1)
        parent = registry.load("R-102")
        self.assertEqual(parent.status, "detected")
        self.assertTrue(parent.quiet_birth)
        self.assertEqual(msgs, [])

    def test_full_relates_to_resolved_followup_is_loud(self):
        # FULL 零回归：真人语音命中完结卡照旧生 follow-up 子卡，**并且照旧
        # 响一声**（§78 之前这条断言的是 child.status == "card_sent"——那正是
        # 「会响」在旧模型里的写法）。hard+deadline+urgent = 提取层也判紧急，
        # 两道判据都为 FULL 时才有资格打断 owner。
        self._seed_delivered()
        self._note()
        summary, msgs = self._scan_and_diff(
            [_item("准备开发者大会演讲稿", provenance="audio", speaker="human")],
            {"action": "relates_to", "req": "R-101",
             "note": "会上追加的新事项", "needs_action": True})
        self.assertEqual(summary["cards"], 1)
        (child,) = [r for r in registry.load_all() if r.improvement_of == "R-101"]
        self.assertEqual(child.status, "detected")
        self.assertFalse(getattr(child, "quiet_birth", False))
        self.assertEqual([m[2] for m in msgs], [child.id])


class FilingSideCapTestCase(EchoGateBase):
    """P1-2 的落库侧半边：``apply_triage(gate=…)`` 自己就得扣住天花板.

    radar 在闸门口已经按 ``hc`` 给候选盖了一次 ``quiet_birth``，但那是**预判**
    ——预判与落库之间目标卡可能换状态（TOCTOU），而 quick_capture 还有 radar
    之外的调用方（Slack/Gmail/self-DM）。所以这里绕开 radar 直接调
    ``apply_triage``，候选**不带** ``quiet_birth``，证明天花板是落库侧自己扣的。
    """

    def _seed_delivered(self, req_id="R-400", title="发布 Q2 博客文章"):
        done = registry.Requirement(
            id=req_id, title=title, status="delivered",
            sources=[{"who": "boss", "channel": "slack",
                      "date": "2026-07-20", "quote": title}])
        registry.save(done)
        return done

    def _followup(self, gate):
        req = registry.Requirement(id="", title="开发者大会演讲稿",
                                   status="detected")
        kind, saved = quick_capture.apply_triage(
            {"action": "relates_to", "req": "R-400",
             "note": "同一线程的新事项", "needs_action": True},
            req, config.Config(), gate=gate)
        self.assertEqual(kind, "follow_up")
        return saved

    def test_limited_gate_caps_the_followup_quiet(self):
        self._seed_delivered()
        child = self._followup(provenance.LIMITED)
        self.assertEqual(child.status, "detected")
        self.assertTrue(child.quiet_birth)

    def test_full_gate_leaves_the_followup_loud(self):
        self._seed_delivered()
        child = self._followup(provenance.FULL)
        self.assertEqual(child.status, "detected")
        self.assertFalse(getattr(child, "quiet_birth", False))


class CorroborateFilingBackstopTestCase(EchoGateBase):
    """§45 落库侧执法：radar 预判后世界变了（TOCTOU）也拦得住."""

    def test_apply_triage_blocks_corroborate_on_resolved_target(self):
        done = registry.Requirement(id="R-300", title="已交付的事", status="delivered")
        registry.save(done)
        req = registry.Requirement(id="", title="屏幕回声", status="detected")
        kind, saved = quick_capture.apply_triage(
            {"action": "relates_to", "req": "R-300", "needs_action": True},
            req, config.Config(), gate=provenance.CORROBORATE)
        self.assertEqual((kind, saved), ("ignored", None))
        self.assertEqual(len(registry.load_all()), 1)
        self.assertEqual(registry.load("R-300").status, "delivered")
        (ev,) = self._new_events("radar_echo_blocked")
        self.assertEqual(ev.get("stage"), "filing")

    def test_apply_triage_blocks_corroborate_fallthrough_to_new_proposal(self):
        # relates_to 目标消失（未知 id）会 fall through 到 new_proposal——
        # 屏幕来源同样无出生权。
        req = registry.Requirement(id="", title="屏幕回声", status="detected")
        kind, saved = quick_capture.apply_triage(
            {"action": "relates_to", "req": "R-999", "needs_action": True},
            req, config.Config(), gate=provenance.CORROBORATE)
        self.assertEqual((kind, saved), ("ignored", None))
        self.assertEqual(registry.load_all(), [])


class EchoEventPrivacyTestCase(EchoGateBase):
    """P1-3：radar_echo_blocked 纯元数据——analytics_sync 默认整条上传."""

    def test_block_event_carries_no_screen_content(self):
        self._note()
        title = "走之前把 G-1650 从出纸口拿走"
        self._scan([_item(title, provenance="screen", speaker="assistant")],
                   {"action": "new_proposal", "confidence": "high"})
        (ev,) = self._new_events("radar_echo_blocked")
        self.assertNotIn("title", ev)
        self.assertNotIn("note", ev)
        self.assertEqual(ev.get("stage"), "birth")
        self.assertEqual(ev.get("gate"), provenance.CORROBORATE)
        self.assertEqual(ev.get("provenance"), "screen")
        self.assertEqual(ev.get("speaker"), "assistant")
        self.assertEqual(ev.get("action"), "new_proposal")
        # 整条事件里不许出现任何屏幕文本（含 note 文件名里的主题词）。
        line = json.dumps(ev, ensure_ascii=False)
        self.assertNotIn(title, line)
        self.assertNotIn("screenpipe-x", line)


if __name__ == "__main__":
    unittest.main()
