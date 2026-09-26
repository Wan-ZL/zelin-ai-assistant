"""`quiet_birth` 的唯一验收尺：这张卡在 main 上会不会响，现在就得响不响一样。

契约：CONTRACT **§78** / **§78.6**「安静出生」条 / **§45 §78 修法**（FULL 与
LIMITED 的分界从「落哪一列」改判成「响不响」）/ §40 + §40.6（新卡与回锅通知）/
§1 `quiet_birth` 字段 / §47.2（解析失败降级卡「不通知」）/ §40.3（give-up 诊断卡）
/ §16（digest 进化建议卡）/ §70（每日整理合成卡）。

**为什么要再开一个文件**：提案车道退役前，「这张卡配不配打断 owner」这个事实
不是一个字段，而是**出生落哪一列**——`card_sent`（提案）会被 `alerts` 差分到、
会响；`detected`（潜在任务）不参与差分、天生安静。两列合一之后这个事实无处
可依，只能搬到 add-only 的 `quiet_birth` 上。搬运途中每漏一处，产品就在那一处
**没有报错地**变哑（或变吵）：测试全绿、盘面照旧、只是从此不说话。

所以本文件的每一条都只钉一句话：

    `quiet_birth` 为真，当且仅当**同一张卡在 origin/main 上会出生/落进
    `detected` 而不是 `card_sent`**。

观察口径逐条照 §45 的判例（tests/test_radar_echo_gate.py）：跑真投影
（`dashboard.build_dashboard`）+ 真差分（`actd.detect_transitions`），而不是读
一个字段——字段是实现，打扰面才是法条保的东西。候选也**不手搓**：radar 那几条
走 `radar.scan`（候选由 `radar._file_item` 亲手盖章），这样「真实的 radar 形状」
不会像旧判例那样被一个裸 `Requirement(...)` 绕过去。

零子进程、零网络：LLM 全走注入缝，registry 住在沙箱 AIASSISTANT_HOME。
"""
import datetime as _dt
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests import TMP_HOME  # noqa: F401 - sandbox env before act imports

from act import actd, digest, radar
from act.lib import config, dashboard, maintenance, notify, quick_capture, registry
from act.lib.registry import Requirement, State

BASE = 1_760_000_000.0


def _item(title, *, provenance="audio", speaker="human", hardness="hard",
          deadline="2026-07-30", urgent=True, cost=None):
    """一条提取器输出。`provenance`/`speaker` 缺席 = §45 的 LIMITED 老式输出。"""
    d = {"title": title, "type": "action", "tier": "T1", "hardness": hardness,
         "deadline": deadline, "cost_estimate_usd": cost, "urgent": urgent,
         "quote": "please do the thing"}
    if provenance is not None:
        d["provenance"] = provenance
    if speaker is not None:
        d["speaker"] = speaker
    return d


def _triager_for(decision: dict):
    def triager(_prompt):
        return subprocess.CompletedProcess(
            args=["triage"], returncode=0,
            stdout=json.dumps(decision, ensure_ascii=False))
    return triager


class QuietBirthBase(unittest.TestCase):
    """沙箱 + 「盘面差分」观察口径。"""

    def setUp(self):
        config.ensure_state_dirs()
        self._clear_registry()
        self.addCleanup(self._clear_registry)
        self.cfg = config.Config()

    @staticmethod
    def _clear_registry():
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()

    def _board(self, reqs):
        return dashboard.build_dashboard(reqs=reqs, agents=[], cfg=self.cfg,
                                         archived=[])

    def _msgs_of(self, before):
        """扫描前的盘面 `before` → 现在的盘面，跑一次守护进程真实的通知差分。"""
        return actd.detect_transitions(self._board(before),
                                       self._board(registry.load_all()))

    def _debt_row(self, rid):
        rows = [r for r in self._board(registry.load_all())["debt"]
                if r["id"] == rid]
        self.assertEqual(len(rows), 1, f"{rid} 不在潜在任务列里")
        return rows[0]

    def assertRings(self, before, rid):
        """这张卡响了一声，而且点名的就是它。"""
        msgs = self._msgs_of(before)
        self.assertEqual([m[2] for m in msgs], [rid], f"{rid} 应当响一声")
        # 卡永远看得见：安静与否都不许把一行从潜在任务列里藏掉
        self.assertIsNotNone(self._debt_row(rid))
        return msgs

    def assertSilent(self, before, rid):
        """一声不响——但卡照样在潜在任务列里看得见（§45：静默不等于隐形）。"""
        self.assertEqual(self._msgs_of(before), [], f"{rid} 不该打扰 owner")
        row = self._debt_row(rid)
        self.assertTrue(row.get("quiet_birth"), "行上得带 quiet_birth（投影面）")
        return row


class RadarBirthAndReRaiseTestCase(QuietBirthBase):
    """radar 的候选亲手盖章那一路（`radar._file_item` → `apply_triage`）。"""

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory(prefix="quiet-birth-")
        self.addCleanup(self.tmp.cleanup)
        self.raw = Path(self.tmp.name) / "2 - raw"
        self.raw.mkdir(parents=True)
        config.CONFIG_PATH.write_text(
            f'sources:\n  obsidian_raw: "{self.raw.as_posix()}"\n', encoding="utf-8")
        self.addCleanup(self._clear_radar_state)

    @staticmethod
    def _clear_radar_state():
        if config.CONFIG_PATH.exists():
            config.CONFIG_PATH.unlink()
        for p in (config.STATE_DIR / radar.MARKER_PATH_NAME,
                  config.STATE_DIR / radar.FAILED_QUEUE_NAME):
            if p.exists():
                p.unlink()

    def _note(self, name="2026-07-09 1on1.md", text="note body", mtime=BASE):
        p = self.raw / name
        p.write_text(text, encoding="utf-8")
        os.utime(p, (mtime, mtime))
        return p

    def _scan(self, items, decision):
        return radar.scan(runner=lambda _t: json.dumps(items, ensure_ascii=False),
                          triager=_triager_for(decision))

    def _seed(self, rid, title, status):
        req = Requirement(id=rid, title=title, status=status,
                          sources=[{"who": "boss", "channel": "slack",
                                    "date": "2026-07-01", "quote": title}])
        registry.save(req)
        return req

    def test_a_non_urgent_full_reraise_still_rings(self):
        """**回锅必须响**——即使这一次重述本身不紧急。

        main 上回锅是 `set_status(DETECTED if cap_detected else CARD_SENT)`：
        判据**只有** §45 的来源天花板，提取层的 `urgent` 从来管不到它（它管的
        是新卡出生在哪一列）。所以一条 FULL 来源、`urgent:false` 的重述在 main
        上照样把卡送回提案列、照样响「回锅」。把候选那枚（记紧急度的）
        `quiet_birth` 并进这条路，后果是：老板已经验收过的事被人再提一次，而
        助手从此一声不吭——没有报错、测试全绿。
        """
        self._seed("R-019", "写周报自动化脚本", State.DELIVERED.value)
        self._note()
        before = registry.load_all()
        summary = self._scan(
            [_item("写周报自动化脚本", urgent=False)],
            {"action": "relates_to", "req": "R-019", "needs_action": True})
        self.assertEqual(len(registry.load_all()), 1)     # 翻回原卡，不出新卡
        parent = registry.load("R-019")
        self.assertEqual(parent.status, State.DETECTED.value)
        self.assertFalse(getattr(parent, "quiet_birth", False))
        msgs = self.assertRings(before, "R-019")
        self.assertEqual((msgs[0][0], msgs[0][1]),           # 「回锅」文案，不是新卡文案
                         notify.msg_reraised("写周报自动化脚本", "写周报自动化脚本"))
        self.assertEqual(summary["cards"], 1)

    def test_a_non_urgent_full_follow_up_child_still_rings(self):
        """同理的另一半：同线程、不同事的后续子卡（`_open_follow_up`）。

        main 上它的出生态也是 `_birth_state(cap_detected)`——FULL = `card_sent`
        = 响。
        """
        self._seed("R-020", "准备开发者大会演讲稿", State.DELIVERED.value)
        self._note()
        before = registry.load_all()
        self._scan([_item("会后补一版中文讲稿", urgent=False)],
                   {"action": "relates_to", "req": "R-020",
                    "note": "会上追加的新事项", "needs_action": True})
        (child,) = [r for r in registry.load_all() if r.improvement_of == "R-020"]
        self.assertFalse(getattr(child, "quiet_birth", False))
        self.assertRings(before, child.id)

    def test_a_limited_birth_is_silent(self):
        """§45 LIMITED（缺 provenance 的老式提取输出）= 落列但不响。

        main 上这半条法条由「最高落 detected」承担；退役后只剩这枚出生章。
        """
        self._note()
        before = registry.load_all()
        self._scan([_item("把季度复盘整理成文档", provenance=None, speaker=None)],
                   {"action": "new_proposal", "confidence": "high"})
        (card,) = registry.load_all()
        self.assertEqual(card.status, State.DETECTED.value)
        self.assertSilent(before, card.id)

    def test_a_full_urgent_birth_still_rings(self):
        """反向对照：FULL + hard + deadline + urgent，main 上就是 `card_sent`。"""
        self._note()
        before = registry.load_all()
        self._scan([_item("周五前把季度报告发出去")],
                   {"action": "new_proposal", "confidence": "high"})
        (card,) = registry.load_all()
        self.assertFalse(getattr(card, "quiet_birth", False))
        self.assertRings(before, card.id)

    def test_a_non_urgent_increment_child_is_silent(self):
        """增量子卡：main 上 `CARD_SENT if high_confidence else DETECTED`。

        父卡还开着、候选带来一个更早的 deadline = `_carries_increment` 为真 →
        `_reconcile_open` 生一张 `improvement_of` 子卡。`urgent:false` 让 radar
        的 `hc` 为假，main 上这张子卡就落 `detected`、一声不响。
        """
        self._seed("R-030", "把周报模板双语化", State.DETECTED.value)
        self._note()
        before = registry.load_all()
        self._scan([_item("把周报模板双语化", deadline="2026-07-02", urgent=False)],
                   {"action": "new_proposal", "confidence": "high"})
        (child,) = [r for r in registry.load_all() if r.improvement_of == "R-030"]
        self.assertEqual(child.status, State.DETECTED.value)
        self.assertSilent(before, child.id)

    def test_an_urgent_increment_child_still_rings(self):
        """同一条路的 FULL + 紧急档：main 上落 `card_sent`，照旧响。"""
        self._seed("R-031", "把周报模板双语化", State.DETECTED.value)
        self._note()
        before = registry.load_all()
        self._scan([_item("把周报模板双语化", deadline="2026-07-02")],
                   {"action": "new_proposal", "confidence": "high"})
        (child,) = [r for r in registry.load_all() if r.improvement_of == "R-031"]
        self.assertFalse(getattr(child, "quiet_birth", False))
        self.assertRings(before, child.id)


class QuietCandidateIncrementChildTestCase(QuietBirthBase):
    """radar 之外的调用方（Slack / Gmail / self-DM）走 `apply_triage` 的那一路。

    它们**不传** `high_confidence`，所以 main 上它们的增量子卡恒落 `detected`
    ——恒安静。子卡的字段表里漏掉 `quiet_birth` 的后果就长在这里：一张本该
    安静的子卡会响。
    """

    def test_a_quiet_candidate_carrying_an_increment_stays_quiet(self):
        parent = Requirement(id="R-040", title="给客户回一封正式邮件",
                             status=State.DETECTED.value,
                             sources=[{"who": "boss", "channel": "gmail",
                                       "date": "2026-07-01", "quote": "回信"}])
        registry.save(parent)
        before = registry.load_all()
        # Gmail 雷达的候选形状：落潜在任务 + triage 判 confidence=low →
        # `_apply_low_confidence` 盖 quiet_birth（main 上是「清掉预设的
        # card_sent」）。带一个父卡没有的 deadline = 真增量。
        cand = Requirement(id=registry.next_id(), title="给客户回一封正式邮件",
                           status=State.DETECTED.value, deadline="2026-08-01",
                           sources=[{"who": "boss", "channel": "gmail",
                                     "date": "2026-07-09", "quote": "催一下"}])
        kind, saved = quick_capture.apply_triage(
            {"action": "new_proposal", "confidence": "low"}, cand, self.cfg)
        self.assertEqual(kind, "proposed")
        self.assertEqual(saved.improvement_of, "R-040")   # 走的是增量子卡这条路
        self.assertSilent(before, saved.id)

    def test_a_limited_gated_candidate_carrying_an_increment_stays_quiet(self):
        """§45 的天花板在 `apply_triage` 入口就盖在候选身上，靠它搭车到子卡。

        `_reconcile_open` 逐字照 main 不收 `cap_detected`（main 上 §45 也从不
        管开着的父卡长出来的子卡），所以这条路的静默事实只能从候选身上来。
        """
        from act.lib import provenance

        parent = Requirement(id="R-041", title="整理会议纪要模板",
                             status=State.DETECTED.value,
                             sources=[{"who": "zelin", "channel": "quick",
                                       "date": "2026-07-01", "quote": "模板"}])
        registry.save(parent)
        before = registry.load_all()
        cand = Requirement(id=registry.next_id(), title="整理会议纪要模板",
                           status=State.DETECTED.value, cost_estimate_usd=3.0,
                           sources=[{"who": "unknown", "channel": "meeting",
                                     "date": "2026-07-09", "quote": "又提了一次"}])
        _kind, saved = quick_capture.apply_triage(
            {"action": "new_proposal", "confidence": "high"}, cand, self.cfg,
            gate=provenance.LIMITED)
        self.assertEqual(saved.improvement_of, "R-041")
        self.assertSilent(before, saved.id)


class DedupSynthesisTestCase(QuietBirthBase):
    """§70 每日整理的合成卡：main 上 `card_sent if any(member is card_sent)`。"""

    def _card(self, rid, title, *, quiet):
        req = Requirement(id=rid, title=title, status=State.DETECTED.value,
                          quiet_birth=quiet,
                          sources=[{"who": "boss", "channel": "slack",
                                    "date": "2026-07-01", "quote": title}])
        registry.save(req)
        return req

    def test_an_all_quiet_cluster_synthesizes_a_quiet_card(self):
        """全簇安静 → 合成卡安静。

        不盖章的话，一批 owner 从没被打扰过的卡会在某个夜里被并成一张，然后
        突然响一声——「每日整理」从此变成一个通知源。
        """
        olds = [self._card("R-050", "把入职文档补完", quiet=True),
                self._card("R-051", "把入职文档补完整", quiet=True)]
        before = registry.load_all()
        res = maintenance.apply_merge(olds)
        new = registry.load(res["new"])
        self.assertTrue(new.quiet_birth)
        self.assertEqual(new.status, State.DETECTED.value)
        self.assertSilent(before, new.id)

    def test_a_mixed_cluster_synthesizes_a_loud_card(self):
        """簇里有一张当初响过的卡 → 合成卡照旧响（它接的是同一件事）。"""
        olds = [self._card("R-060", "把入职文档补完", quiet=True),
                self._card("R-061", "把入职文档补完整", quiet=False)]
        before = registry.load_all()
        res = maintenance.apply_merge(olds)
        new = registry.load(res["new"])
        self.assertFalse(new.quiet_birth)
        self.assertRings(before, new.id)


class SilentByConstructionProducersTestCase(QuietBirthBase):
    """退役前「靠落 `detected` 而天生不响」的几个生产者，退役后必须盖章。

    它们在 main 上一次通知都没发过——`alerts` 当年只差分 `card_sent`。车道合一
    之后 diff 源换成 `debt[]`，不补章就等于给 owner 新开了三个通知源。
    """

    def test_the_digest_suggestion_card_is_silent(self):
        """§16 周期自省的进化建议卡（`digest.file_suggestion_cards`）。

        注意它**不走** `alerts._from_weekly_digest` 那道跳过：那道跳过认的是
        `sources[].channel == "weekly-digest"`，而建议卡的来源通道是
        `analytics`。所以这里只剩 `quiet_birth` 一道闸。
        """
        before = registry.load_all()
        (card,) = digest.file_suggestion_cards(
            [("建议修自动恢复（近 30 天失败频繁）", "近 30 天失败 42 次")],
            today=_dt.date(2026, 7, 9))
        self.assertEqual(card.status, State.DETECTED.value)
        self.assertEqual(card.sources[0]["channel"], "analytics")  # 非 weekly-digest
        self.assertSilent(before, card.id)

    def test_the_parse_degraded_card_is_silent(self):
        """§47.2 的法条原文：「`status=detected`（备选列，**不通知**）」。

        本 PR 没有修订 §47.2，所以让它响就是代码与法条分家。
        """
        before = registry.load_all()
        with tempfile.TemporaryDirectory(prefix="degrade-") as d:
            note = Path(d) / "2026-07-09 sync.md"
            note.write_text("两条待办，模型没能解析出来", encoding="utf-8")
            card = radar.file_parse_degraded_card(note, note.read_text(encoding="utf-8"))
        self.assertIsNotNone(card)
        self.assertEqual(card.status, State.DETECTED.value)
        self.assertSilent(before, card.id)

    def test_the_give_up_diagnostic_card_is_silent(self):
        """§40.3 give-up 诊断卡：「a fact to act on, not a proposal to approve」
        —— main 上那句话的实现就是「落 detected」。"""
        before = registry.load_all()
        with tempfile.TemporaryDirectory(prefix="giveup-") as d:
            note = Path(d) / "2026-07-09 broken.md"
            note.write_text("unreadable", encoding="utf-8")
            card = radar.file_give_up_card(
                note, {"attempts": 5, "last_error": "unparseable extraction"})
        self.assertIsNotNone(card)
        self.assertEqual(card.status, State.DETECTED.value)
        self.assertSilent(before, card.id)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
