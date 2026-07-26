"""§45 出生资格闸在 radar._process_note 里的接线 — 回声环的行为契约.

钉住的行为（Zelin 2026-07-25 拍板「屏幕 OCR 不发起卡片」）：

- screen 来源 + triage 判 new_proposal（含 triage 失败的宁可打扰回退）
  -> 拦截：零新卡、``echo_blocked`` 计数、marker 照常推进（不是失败）；
- screen 来源 + triage 判 relates_to 且目标卡还开着 -> fold 放行（佐证是
  屏幕的正职），不出新卡；
- screen 来源 + relates_to 命中已完结卡 -> 拦截（re-raise/follow-up 也是
  新卡，屏幕无此权力）；
- audio×human 的硬 deadline 紧急项照旧直达提案列（回归：FULL 行为不变）；
- 缺 provenance/speaker 的老式提取输出 -> LIMITED：落备选，绝不 card_sent。

PR80 审查加固（闸门跟着候选走完全程，triage LLM 输出不是豁免通道）：

- P1-1：非 FULL 来源 fold 进 detected 卡时，``needs_action=true`` 不得把目标
  卡提升进提案列——fold 照常、提升压平、``radar_echo_blocked{stage=
  fold_promotion}`` 留痕；FULL 来源同场景照常提升；
- P1-2：LIMITED 命中完结卡的 re-raise/follow-up（relates_to 路径与
  merge_or_new 内部路径）天花板 = detected，不通知；FULL 零回归；
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
from act.lib import analytics, config, provenance, quick_capture, registry

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

    def test_screen_triage_ignore_stays_out_of_echo_blocked(self):
        # triage 判 ignore 的项本来就不会成卡——混进 echo_blocked 会抬高政策
        # 审计口径。它走原有 ignore 路径（照旧零卡），拦截计数保持 0。
        self._note()
        summary = self._scan(
            [_item("纯 FYI 的屏幕回声", provenance="screen",
                   speaker="assistant")],
            {"action": "ignore", "reason": "纯 FYI"})
        self.assertEqual(summary["echo_blocked"], 0)
        self.assertEqual(summary["cards"], 0)
        self.assertEqual(registry.load_all(), [])

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
        # §45：屏幕佐证不得借 act-now 把目标卡提升进提案列以外的状态
        self.assertEqual(reqs[0].status, "card_sent")

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
    def test_audio_human_hard_deadline_still_reaches_card_sent(self):
        self._note()
        summary = self._scan(
            [_item("给 Arash 交评审结论", provenance="audio", speaker="human")],
            {"action": "new_proposal", "confidence": "high"})
        self.assertEqual(summary["echo_blocked"], 0)
        self.assertEqual(summary["cards"], 1)
        (req,) = registry.load_all()
        self.assertEqual(req.status, "card_sent")

    def test_missing_provenance_fields_park_in_backlog(self):
        # 老式提取输出（无两字段）= unknown×unknown = LIMITED：即使
        # hard+deadline+urgent 也只落备选——安静的安全网。
        self._note()
        summary = self._scan(
            [_item("来历不明的硬任务")],
            {"action": "new_proposal", "confidence": "high"})
        self.assertEqual(summary["echo_blocked"], 0)
        self.assertEqual(summary["cards"], 0)
        (req,) = registry.load_all()
        self.assertEqual(req.status, "detected")

    def test_audio_assistant_voice_is_corroborate_only(self):
        self._note()
        summary = self._scan(
            [_item("TTS 播报里的行动项", provenance="audio", speaker="assistant")],
            {"action": "new_proposal", "confidence": "high"})
        self.assertEqual(summary["echo_blocked"], 1)
        self.assertEqual(registry.load_all(), [])


class FoldPromotionGateTestCase(EchoGateBase):
    """P1-1：fold 的 needs_action 提升通道不得绕过出生资格闸门."""

    def _seed_detected(self):
        target = registry.Requirement(
            id="R-200", title="季度报告", status="detected",
            sources=[{"who": "boss", "channel": "slack",
                      "date": "2026-07-20", "quote": "季度报告"}])
        registry.save(target)
        return target

    def test_screen_needs_action_fold_cannot_promote(self):
        self._seed_detected()
        self._note()
        summary = self._scan(
            [_item("季度报告的屏幕回声", provenance="screen", speaker="human",
                   hardness="soft", deadline=None, urgent=False)],
            {"action": "relates_to", "req": "R-200",
             "note": "屏幕上又见到一次", "needs_action": True})
        self.assertEqual(summary["echo_blocked"], 0)   # fold 本身放行
        (req,) = registry.load_all()
        self.assertIn("[radar] 屏幕上又见到一次", req.notes or "")
        self.assertEqual(req.status, "detected")       # 提升被压平
        (ev,) = self._new_events("radar_echo_blocked")
        self.assertEqual(ev.get("stage"), "fold_promotion")
        self.assertEqual(ev.get("gate"), provenance.CORROBORATE)
        self.assertEqual(ev.get("req"), "R-200")

    def test_limited_needs_action_fold_cannot_promote(self):
        # 缺 provenance/speaker（老式提取）= LIMITED：同样无提升权。
        self._seed_detected()
        self._note()
        self._scan(
            [_item("季度报告的来历不明回声", hardness="soft", deadline=None,
                   urgent=False)],
            {"action": "relates_to", "req": "R-200",
             "note": "来源判不出", "needs_action": True})
        (req,) = registry.load_all()
        self.assertEqual(req.status, "detected")
        (ev,) = self._new_events("radar_echo_blocked")
        self.assertEqual(ev.get("stage"), "fold_promotion")
        self.assertEqual(ev.get("gate"), provenance.LIMITED)

    def test_audio_human_needs_action_fold_still_promotes(self):
        # FULL 零回归：真人语音的 act-now 关联照旧把备选卡推进提案列——
        # 特意用 soft/无 deadline（hc=False），证明走的是 needs_action 通道。
        self._seed_detected()
        self._note()
        self._scan(
            [_item("季度报告要加速", provenance="audio", speaker="human",
                   hardness="soft", deadline=None, urgent=False)],
            {"action": "relates_to", "req": "R-200",
             "note": "会上催了", "needs_action": True})
        (req,) = registry.load_all()
        self.assertEqual(req.status, "card_sent")
        self.assertEqual(self._new_events("radar_echo_blocked"), [])


class ResolvedCardCeilingTestCase(EchoGateBase):
    """P1-2：LIMITED 的 re-raise/follow-up 天花板 = detected（不通知）."""

    def _seed_delivered(self, req_id="R-101", title="发布 Q2 博客文章",
                        **kw):
        done = registry.Requirement(
            id=req_id, title=title, status="delivered",
            sources=[{"who": "boss", "channel": "slack",
                      "date": "2026-07-20", "quote": title}], **kw)
        registry.save(done)
        return done

    def test_limited_relates_to_resolved_followup_capped_at_detected(self):
        # (a) 无 provenance 字段 relates_to delivered 卡 + needs_action=true
        # —— 修复前生出 card_sent follow-up + 通知（R-020/R-093 回声环）。
        self._seed_delivered()
        self._note()
        summary = self._scan(
            [_item("准备开发者大会演讲稿", hardness="soft", deadline=None)],
            {"action": "relates_to", "req": "R-101",
             "note": "同一线程的新事项", "needs_action": True})
        self.assertEqual(summary["cards"], 0)          # 不是一张提案卡
        self.assertEqual(registry.load("R-101").status, "delivered")
        (child,) = [r for r in registry.load_all() if r.improvement_of == "R-101"]
        self.assertEqual(child.status, "detected")     # 天花板：备选
        self.assertIn("既往卡 R-101 的后续", child.summary or "")

    def test_limited_relates_to_resolved_reraise_capped_at_detected(self):
        # 同题重述（same_task）走的是原卡翻回——LIMITED 只许翻到备选。
        self._seed_delivered(title="写周报自动化脚本")
        self._note()
        summary = self._scan(
            [_item("写周报自动化脚本")],   # hard+deadline = 有增量的重述
            {"action": "relates_to", "req": "R-101", "needs_action": True})
        self.assertEqual(summary["cards"], 0)
        self.assertEqual(len(registry.load_all()), 1)  # 翻回原卡，不出新卡
        self.assertEqual(registry.load("R-101").status, "detected")

    def test_limited_new_proposal_hitting_resolved_title_capped(self):
        # (b) new_proposal 撞完结卡标题 -> merge_or_new 内部 re-raise：
        # cap_detected 必须一路跟进（修复前无视 high_confidence=False）。
        self._seed_delivered(req_id="R-102", title="写周报自动化脚本")
        self._note()
        summary = self._scan(
            [_item("写周报自动化脚本")],
            {"action": "new_proposal", "confidence": "high"})
        self.assertEqual(summary["cards"], 0)
        self.assertEqual(len(registry.load_all()), 1)
        self.assertEqual(registry.load("R-102").status, "detected")

    def test_full_relates_to_resolved_followup_still_card_sent(self):
        # FULL 零回归：真人语音命中完结卡照旧生 card_sent follow-up。
        self._seed_delivered()
        self._note()
        summary = self._scan(
            [_item("准备开发者大会演讲稿", provenance="audio", speaker="human",
                   hardness="soft", deadline=None)],
            {"action": "relates_to", "req": "R-101",
             "note": "会上追加的新事项", "needs_action": True})
        self.assertEqual(summary["cards"], 1)
        (child,) = [r for r in registry.load_all() if r.improvement_of == "R-101"]
        self.assertEqual(child.status, "card_sent")


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
