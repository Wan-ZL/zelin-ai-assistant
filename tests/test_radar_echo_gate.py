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
from act.lib import config, registry

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


if __name__ == "__main__":
    unittest.main()
