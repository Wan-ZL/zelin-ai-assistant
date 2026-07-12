"""统一雷达三选一判定 (v0.17) — act/lib/quick_capture.{triage,apply_triage}.

所有雷达候选（Slack 原生 + Slack MCP 兜底 + Obsidian 提取项）落库前必经同一个
三选一闸门。全部用注入的 fake runner/extractor（绝不 spawn 真 claude），跑在
sandbox AIASSISTANT_HOME（tests/__init__.py）里。钉住的契约：

(a) 纯信息性消息 -> ignore，不成卡（例1/2/3 的"Quinton 说今早会改权限"类）；
(b) 关联已 delivered/merged 卡且 needs_action=true -> 生成挂 improvement_of
    血缘的 follow-up 卡，摘要 "既往卡 X 的后续：…"，不发孤立新卡；
(c) needs_action=false（未来条件性进展）-> 折叠为既往卡备注，不成卡；
(d) 跨 pass/跨源去重：同一父卡已有未决 follow-up 时，第二个来源（如 MCP 路径）
    并入该卡，不出第二张（registry.find_open_follow_up 机制）；
(e) obsidian 的 hard+deadline 分流保留（new_proposal 不满足 -> detected/备选）；
(f) 原有路径回归：只注入 legacy runner（回 JSON 数组）时 triage 走兜底
    new_proposal，行为与三选一落地前完全一致（宁可多建，不丢候选）；
(g) self-DM quick capture 的 relates_to 命中已交付卡 -> 同一 follow-up 机制。
"""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import radar, radar_slack
from act.lib import config, quick_capture, registry, sanitize

BASE = 1_760_000_000.0  # fixed epoch — deterministic note mtimes


def _proc(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["claude"], returncode=returncode, stdout=stdout, stderr="")


class _FakeLLM:
    """One injectable for BOTH calls of a radar pass.

    Extraction prompts get ``extraction`` (a JSON-able list); triage prompts
    (recognized by the shared gate's marker text) get ``decision``. Records
    every prompt for assertions.
    """

    def __init__(self, extraction=None, decision=None, exc_on_triage=None):
        self.extraction = extraction if extraction is not None else []
        self.decision = decision
        self.exc_on_triage = exc_on_triage
        self.calls: list[str] = []
        self.triage_calls: list[str] = []

    def __call__(self, prompt: str):
        self.calls.append(prompt)
        if "入库把关" in prompt:  # the shared triage prompt
            self.triage_calls.append(prompt)
            if self.exc_on_triage is not None:
                raise self.exc_on_triage
            if self.decision is None:
                # legacy shape: same array as extraction (regression mode f)
                return _proc(json.dumps(self.extraction, ensure_ascii=False))
            return _proc(json.dumps(self.decision, ensure_ascii=False))
        return _proc(json.dumps(self.extraction, ensure_ascii=False))


def _seed(req_id: str, title: str, status: str, **kw) -> registry.Requirement:
    r = registry.Requirement(id=req_id, title=title, status=status, **kw)
    registry.save(r)
    return r


def _clean_state():
    config.ensure_state_dirs()
    if config.REGISTRY_DIR.exists():
        shutil.rmtree(config.REGISTRY_DIR)
    for name in (radar.MARKER_PATH_NAME, radar_slack.STATE_FILE,
                 radar_slack.MCP_MARKER_FILE):
        p = config.STATE_DIR / name
        if p.exists():
            p.unlink()
    if config.CONFIG_PATH.exists():
        config.CONFIG_PATH.unlink()


class TriageBase(unittest.TestCase):
    def setUp(self):
        _clean_state()
        self.addCleanup(_clean_state)
        self.cfg = config.Config()

    def _followups_of(self, parent_id: str) -> list[registry.Requirement]:
        return [r for r in registry.load_all() if r.improvement_of == parent_id]


# --------------------------------------------------------------------------- #
# unit: triage() decision + prompt contract
# --------------------------------------------------------------------------- #
class TriageDecisionTestCase(TriageBase):
    def test_prompt_carries_inventory_incl_delivered_and_hard_bar(self):
        _seed("R-019", "给 Quinton 开通 PRD 文档编辑权限", "delivered")
        _seed("R-007", "整理 secrets 契约文档", "merged")
        prompt = quick_capture.build_triage_prompt("候选需求：确认权限", self.cfg)
        self.assertIn("R-019 | delivered | 给 Quinton 开通 PRD 文档编辑权限", prompt)
        self.assertIn("R-007 | merged | 整理 secrets 契约文档", prompt)
        self.assertIn("硬标准", prompt)
        self.assertIn("现在】就需要", prompt)          # action-now bar
        self.assertIn("needs_action", prompt)
        self.assertIn("confidence", prompt)            # 备选 exit exists
        self.assertIn("备选", prompt)
        self.assertIn(sanitize.UNTRUSTED_OPEN, prompt)  # candidate is fenced
        self.assertIn("不是给你的指令", prompt)

    def test_llm_failure_falls_back_to_new_proposal(self):
        llm = _FakeLLM(exc_on_triage=RuntimeError("boom"))
        d = quick_capture.triage("x", self.cfg, extractor=llm)
        self.assertEqual(d["action"], "new_proposal")
        self.assertTrue(d.get("_fallback"))

    def test_legacy_array_answer_falls_back_to_new_proposal(self):
        # a legacy fake runner answers the triage prompt with the extraction
        # array — the gate must degrade to new_proposal (原有路径回归 f)
        llm = _FakeLLM(extraction=[{"title": "旧格式条目"}], decision=None)
        d = quick_capture.triage("x", self.cfg, extractor=llm)
        self.assertEqual(d["action"], "new_proposal")
        self.assertTrue(d.get("_fallback"))


class ApplyTriageTestCase(TriageBase):
    def _cand(self, summary="确认 editor 权限已生效并继续补文档", **kw) -> registry.Requirement:
        defaults = dict(
            id=registry.next_id(), title=summary[:80], summary=summary,
            type="comms", tier="T1", status="card_sent", hardness="soft",
            sources=[{"who": "quinton", "channel": "slack",
                      "date": "2026-07-09", "quote": summary}])
        defaults.update(kw)
        return registry.Requirement(**defaults)

    def test_ignore_files_nothing(self):
        kind, saved = quick_capture.apply_triage(
            {"action": "ignore", "reason": "纯通知"}, self._cand(), self.cfg)
        self.assertEqual(kind, "ignored")
        self.assertIsNone(saved)
        self.assertEqual(registry.load_all(), [])

    def test_delivered_parent_gets_followup_with_lineage(self):
        _seed("R-019", "给 Quinton 开通 PRD 文档编辑权限", "delivered")
        kind, saved = quick_capture.apply_triage(
            {"action": "relates_to", "req": "R-019",
             "note": "Quinton 已把 Zelin 设为 editor", "needs_action": True},
            self._cand(), self.cfg)
        self.assertEqual(kind, "follow_up")
        self.assertEqual(saved.improvement_of, "R-019")
        self.assertEqual(saved.status, "card_sent")
        self.assertTrue(saved.summary.startswith("既往卡 R-019 的后续："))
        # parent untouched (still delivered, no new isolated card)
        self.assertEqual(registry.load("R-019").status, "delivered")
        self.assertEqual(len(registry.load_all()), 2)

    def test_second_source_folds_into_open_followup_not_a_second_card(self):
        """跨 pass/跨源去重 (d): 未决 follow-up 存在时绝不出第二张."""
        _seed("R-019", "给 Quinton 开通 PRD 文档编辑权限", "delivered")
        decision = {"action": "relates_to", "req": "R-019",
                    "note": "权限已改", "needs_action": True}
        kind1, first = quick_capture.apply_triage(decision, self._cand(), self.cfg)
        self.assertEqual(kind1, "follow_up")
        other_src = self._cand(summary="确认 editor 权限（MCP 路径看到的同一事件）",
                               sources=[{"who": "quinton", "channel": "DM",
                                         "date": "2026-07-09", "quote": "set you as editor"}])
        kind2, second = quick_capture.apply_triage(decision, other_src, self.cfg)
        self.assertEqual(kind2, "folded")
        self.assertEqual(second.id, first.id)
        self.assertEqual(len(self._followups_of("R-019")), 1)
        folded = registry.load(first.id)
        self.assertEqual(len(folded.sources), 2)   # second source rode along
        self.assertIn("[radar]", folded.notes)

    def test_resolved_followup_reopens_the_window(self):
        _seed("R-019", "给 Quinton 开通 PRD 文档编辑权限", "delivered")
        decision = {"action": "relates_to", "req": "R-019",
                    "note": "后续一", "needs_action": True}
        _, first = quick_capture.apply_triage(decision, self._cand(), self.cfg)
        first.set_status(registry.State.DELIVERED)   # follow-up itself closes
        registry.save(first)
        kind, second = quick_capture.apply_triage(
            {"action": "relates_to", "req": "R-019",
             "note": "后续二（新一轮）", "needs_action": True},
            self._cand(summary="下一轮真正的新后续"), self.cfg)
        self.assertEqual(kind, "follow_up")
        self.assertNotEqual(second.id, first.id)

    def test_future_conditional_folds_as_note_no_card(self):
        """(c) needs_action=false —— '对方说稍后会做 X' 折叠为备注."""
        _seed("R-019", "给 Quinton 开通 PRD 文档编辑权限", "delivered")
        kind, saved = quick_capture.apply_triage(
            {"action": "relates_to", "req": "R-019",
             "note": "Quinton 说今早会改权限", "needs_action": False},
            self._cand(), self.cfg)
        self.assertEqual(kind, "folded")
        self.assertEqual(saved.id, "R-019")
        self.assertIn("[radar] Quinton 说今早会改权限", registry.load("R-019").notes)
        self.assertEqual(len(registry.load_all()), 1)   # no new card at all

    def test_relates_to_open_card_folds_note_and_source(self):
        _seed("R-030", "写周报", "card_sent",
              sources=[{"who": "manager", "channel": "meeting",
                        "date": "2026-07-01", "quote": "写周报"}])
        kind, saved = quick_capture.apply_triage(
            {"action": "relates_to", "req": "R-030",
             "note": "周报要加 eval 数字", "needs_action": True},
            self._cand(summary="周报要加 eval 数字"), self.cfg)
        self.assertEqual(kind, "folded")
        r = registry.load("R-030")
        self.assertIn("[radar] 周报要加 eval 数字", r.notes)
        self.assertEqual(len(r.sources), 2)
        self.assertEqual(r.repeated_mentions, 2)
        self.assertEqual(len(registry.load_all()), 1)

    def test_relates_to_unknown_id_degrades_to_new_proposal(self):
        kind, saved = quick_capture.apply_triage(
            {"action": "relates_to", "req": "R-999", "needs_action": True},
            self._cand(), self.cfg)
        self.assertEqual(kind, "proposed")
        self.assertEqual(len(registry.load_all()), 1)   # candidate not lost

    def test_relates_to_rejected_card_recards_instead_of_folding(self):
        """决策6: 拒绝 ≠ 已办完——重述绝不折叠进 rejected 卡，必须重新成卡."""
        _seed("R-001", "给团队开通 staging 权限", "rejected")
        kind, saved = quick_capture.apply_triage(
            {"action": "relates_to", "req": "R-001",
             "note": "manager 又问了一遍", "needs_action": True},
            self._cand(summary="manager 重提：开通 staging 权限"), self.cfg)
        self.assertEqual(kind, "proposed")
        self.assertNotEqual(saved.id, "R-001")
        rejected = registry.load("R-001")
        self.assertEqual(rejected.status, "rejected")       # untouched
        self.assertNotIn("[radar]", rejected.notes or "")   # nothing buried
        open_cards = [r for r in registry.load_all()
                      if r.status == "card_sent"]
        self.assertEqual(len(open_cards), 1)

    def test_relates_to_trashed_card_recards_instead_of_folding(self):
        _seed("R-002", "清理旧 launchd agent", "trashed")
        kind, saved = quick_capture.apply_triage(
            {"action": "relates_to", "req": "R-002", "needs_action": True},
            self._cand(summary="再来一遍：清理旧 launchd agent"), self.cfg)
        self.assertEqual(kind, "proposed")
        self.assertNotEqual(saved.id, "R-002")
        self.assertEqual(registry.load("R-002").status, "trashed")

    def test_merged_duplicate_and_primary_share_one_followup(self):
        """merge cluster 归一：两个雷达命中同一事件、分别指向主卡与副卡时，
        只产生一张 follow-up（不再复刻 R-028/R-029 双卡）."""
        _seed("R-010", "给 Quinton 开通 PRD 权限", "delivered")
        _seed("R-011", "Quinton PRD 权限（重复卡）", "merged",
              merged_into="R-010")
        kind1, first = quick_capture.apply_triage(
            {"action": "relates_to", "req": "R-010",
             "note": "权限已改", "needs_action": True},
            self._cand(), self.cfg)
        self.assertEqual(kind1, "follow_up")
        self.assertEqual(first.improvement_of, "R-010")
        kind2, second = quick_capture.apply_triage(
            {"action": "relates_to", "req": "R-011",
             "note": "同一事件的另一路命中", "needs_action": True},
            self._cand(summary="MCP 路径看到的同一事件"), self.cfg)
        self.assertEqual(kind2, "folded")
        self.assertEqual(second.id, first.id)
        fus = [r for r in registry.load_all()
               if r.improvement_of in ("R-010", "R-011")]
        self.assertEqual(len(fus), 1)

    def test_hit_on_merged_duplicate_first_dedupes_later_primary_hit(self):
        # reverse order: the duplicate is related first, the primary second
        _seed("R-010", "给 Quinton 开通 PRD 权限", "delivered")
        _seed("R-011", "Quinton PRD 权限（重复卡）", "merged",
              merged_into="R-010")
        _, first = quick_capture.apply_triage(
            {"action": "relates_to", "req": "R-011",
             "note": "先命中副卡", "needs_action": True},
            self._cand(), self.cfg)
        self.assertEqual(first.improvement_of, "R-010")   # canonicalized
        kind2, second = quick_capture.apply_triage(
            {"action": "relates_to", "req": "R-010",
             "note": "后命中主卡", "needs_action": True},
            self._cand(summary="同一事件第二路"), self.cfg)
        self.assertEqual(kind2, "folded")
        self.assertEqual(second.id, first.id)

    def test_actionable_fold_promotes_detected_card_to_proposal(self):
        """act-now 候选折叠进 detected/备选卡时，卡必须升入提案列."""
        _seed("R-020", "把周报模板双语化", "detected")
        kind, saved = quick_capture.apply_triage(
            {"action": "relates_to", "req": "R-020",
             "note": "manager 现在就要", "needs_action": True},
            self._cand(summary="manager 现在就要双语周报"), self.cfg)
        self.assertEqual(kind, "folded")
        self.assertEqual(registry.load("R-020").status, "card_sent")

    def test_non_actionable_fold_keeps_detected_card_in_backlog(self):
        _seed("R-021", "有空整理 wiki", "detected")
        kind, saved = quick_capture.apply_triage(
            {"action": "relates_to", "req": "R-021",
             "note": "又有人顺嘴提了一句", "needs_action": False},
            self._cand(summary="顺嘴一提", status="detected"), self.cfg)
        self.assertEqual(kind, "folded")
        self.assertEqual(registry.load("R-021").status, "detected")

    def test_missing_needs_action_on_resolved_parent_defaults_to_followup(self):
        """无损原则：LLM 漏掉 needs_action 时，resolved 父卡按 true 处理."""
        _seed("R-019", "给 Quinton 开通 PRD 文档编辑权限", "delivered")
        kind, saved = quick_capture.apply_triage(
            {"action": "relates_to", "req": "R-019", "note": "有后续"},
            self._cand(), self.cfg)
        self.assertEqual(kind, "follow_up")
        self.assertEqual(saved.improvement_of, "R-019")

    def test_string_false_needs_action_folds_as_note(self):
        """JSON-as-string 兜底："false" 不得走 truthy 的 follow-up 分支."""
        _seed("R-019", "给 Quinton 开通 PRD 文档编辑权限", "delivered")
        kind, saved = quick_capture.apply_triage(
            {"action": "relates_to", "req": "R-019",
             "note": "纯进展", "needs_action": "false"},
            self._cand(), self.cfg)
        self.assertEqual(kind, "folded")
        self.assertEqual(saved.id, "R-019")
        self.assertEqual(len(registry.load_all()), 1)   # no follow-up card

    def test_low_confidence_new_proposal_lands_in_backlog(self):
        """统一口径第四出口：真实但不紧急 -> 备选（清掉调用方预设的 card_sent）."""
        kind, saved = quick_capture.apply_triage(
            {"action": "new_proposal", "confidence": "low"},
            self._cand(summary="下季度想做 X"), self.cfg)
        self.assertEqual(kind, "proposed")
        self.assertEqual(saved.status, "detected")

    def test_high_confidence_new_proposal_keeps_caller_status(self):
        kind, saved = quick_capture.apply_triage(
            {"action": "new_proposal", "confidence": "high"},
            self._cand(summary="现在就要回 manager 的确认"), self.cfg)
        self.assertEqual(kind, "proposed")
        self.assertEqual(saved.status, "card_sent")


class RegistryFollowUpLookupTestCase(TriageBase):
    def test_find_open_follow_up_skips_closed_rejected_trashed(self):
        _seed("R-001", "父卡", "delivered")
        _seed("R-002", "已交付的后续", "delivered", improvement_of="R-001")
        _seed("R-003", "被拒的后续", "rejected", improvement_of="R-001")
        _seed("R-004", "回收站的后续", "trashed", improvement_of="R-001")
        self.assertIsNone(registry.find_open_follow_up("R-001"))
        _seed("R-005", "未决后续", "card_sent", improvement_of="R-001")
        found = registry.find_open_follow_up("R-001")
        self.assertEqual(found.id, "R-005")
        self.assertIsNone(registry.find_open_follow_up(""))

    def test_find_open_follow_up_matches_across_merge_cluster(self):
        _seed("R-001", "父卡（主）", "delivered")
        _seed("R-002", "父卡（并入的副卡）", "merged", merged_into="R-001")
        _seed("R-003", "挂在副卡名下的未决后续", "card_sent",
              improvement_of="R-002")
        # a later hit on EITHER cluster member finds the same open follow-up
        self.assertEqual(registry.find_open_follow_up("R-001").id, "R-003")
        self.assertEqual(registry.find_open_follow_up("R-002").id, "R-003")

    def test_canonical_hops_legacy_merged_into_status(self):
        _seed("R-001", "主卡", "delivered")
        legacy = _seed("R-004", "旧式合并副卡", "merged_into:R-001")
        self.assertEqual(registry.canonical(legacy).id, "R-001")
        # non-merged cards canonicalize to themselves
        plain = registry.load("R-001")
        self.assertEqual(registry.canonical(plain).id, "R-001")


# --------------------------------------------------------------------------- #
# slack native path (radar_slack.scan) through the gate
# --------------------------------------------------------------------------- #
class SlackNativeTriageTestCase(TriageBase):
    """scan() with token/auth/fetcher monkeypatched — no network, no subprocess."""

    MSG = [{"channel": "D1", "channel_type": "im", "ts": "1", "user": "UQ",
            "text": "hey, set you as editor on the PRD doc",
            "permalink": "https://x.slack.com/archives/D1/p1"}]

    def setUp(self):
        super().setUp()
        self._orig = (radar_slack.get_token, radar_slack.verify_token)
        radar_slack.get_token = lambda cfg=None: "xoxp-test"
        radar_slack.verify_token = lambda token: {"ok": True, "user_id": "U1"}

    def tearDown(self):
        (radar_slack.get_token, radar_slack.verify_token) = self._orig

    def _scan(self, llm) -> int:
        return radar_slack.scan(
            self.cfg, fetcher=lambda t, m, c, mk: list(self.MSG), extractor=llm)

    def test_informational_message_files_no_card(self):
        """例1 根治：扫描时刻不需要行动的消息 -> ignore，不成卡."""
        llm = _FakeLLM(
            extraction=[{"summary": "Quinton 说今早会改权限", "type": "comms",
                         "tier": "T1", "needs_reply": False, "plan": [],
                         "permalink": "https://x"}],
            decision={"action": "ignore", "reason": "未来条件性，现在无需行动"})
        self.assertEqual(self._scan(llm), 0)
        self.assertEqual(registry.load_all(), [])
        self.assertEqual(len(llm.triage_calls), 1)   # gate WAS consulted

    def test_delivered_R019_gets_followup_not_duplicate_cards(self):
        """例2/3 根治：同一事件关联既往 delivered 卡，不再连发孤立新卡."""
        _seed("R-019", "给 Quinton 开通 PRD 文档编辑权限", "delivered")
        llm = _FakeLLM(
            extraction=[{"summary": "Quinton 已把你设为 editor，确认后继续补文档",
                         "type": "comms", "tier": "T1", "needs_reply": False,
                         "plan": [], "permalink": "https://x"}],
            decision={"action": "relates_to", "req": "R-019",
                      "note": "editor 权限已给", "needs_action": True})
        self.assertEqual(self._scan(llm), 1)
        fus = self._followups_of("R-019")
        self.assertEqual(len(fus), 1)
        self.assertTrue(fus[0].summary.startswith("既往卡 R-019 的后续："))
        self.assertEqual(fus[0].status, "card_sent")

    def test_dual_source_same_event_yields_one_card(self):
        """(d) 双源同事件：原生路径建 follow-up 后，MCP 路径命中同一父卡只并入."""
        _seed("R-019", "给 Quinton 开通 PRD 文档编辑权限", "delivered")
        decision = {"action": "relates_to", "req": "R-019",
                    "note": "editor 权限已给", "needs_action": True}
        native = _FakeLLM(
            extraction=[{"summary": "Quinton 已把你设为 editor，确认权限",
                         "type": "comms", "tier": "T1", "plan": [],
                         "permalink": "https://x"}],
            decision=decision)
        self.assertEqual(self._scan(native), 1)

        # second source: the MCP fallback sees the same Slack event
        mcp = _FakeLLM(
            extraction=[{"title": "确认 Quinton 给的 editor 权限",
                         "summary": "同一事件（MCP 路径）", "who": "quinton",
                         "channel": "DM", "date": "2026-07-09",
                         "quote": "set you as editor"}],
            decision=decision)
        self.assertEqual(radar_slack.mcp_scan(self.cfg, runner=mcp), 0)
        fus = self._followups_of("R-019")
        self.assertEqual(len(fus), 1)                      # 只此一张
        self.assertGreaterEqual(len(fus[0].sources), 2)    # 两个来源都在卡上

    def test_legacy_extractor_regression_still_files_card(self):
        """(f) 原有路径回归：老式 fake（对一切 prompt 回数组）行为不变."""
        llm = _FakeLLM(
            extraction=[{"summary": "回复 manager 的 eval 数字确认", "type": "comms",
                         "tier": "T1", "needs_reply": True, "plan": [],
                         "permalink": "https://x"}],
            decision=None)   # triage answered with the legacy array -> fallback
        self.assertEqual(self._scan(llm), 1)
        reqs = registry.load_all()
        self.assertEqual(len(reqs), 1)
        self.assertEqual(reqs[0].status, "card_sent")
        self.assertEqual(reqs[0].title, "回复 manager 的 eval 数字确认")

    def test_extract_prompt_keeps_non_urgent_requests(self):
        # 统一口径：提取层只滤纯信息/闲聊/已解决——非紧急真实请求照常输出并标
        # urgent（不再在抽取阶段被硬标准双重过滤掉）
        self.assertIn("urgent", radar_slack._EXTRACT_PROMPT)
        self.assertIn("不要跳过", radar_slack._EXTRACT_PROMPT)
        self.assertIn("urgent", radar_slack._MCP_SCAN_PROMPT)
        self.assertIn("不要跳过", radar_slack._MCP_SCAN_PROMPT)

    def test_non_urgent_extraction_lands_in_backlog(self):
        """非紧急真实请求：即使 triage 兜底成 new_proposal，也落 detected/备选."""
        llm = _FakeLLM(
            extraction=[{"summary": "manager 说下季度想做 X，先记着",
                         "type": "other", "tier": "T1", "urgent": False,
                         "needs_reply": False, "plan": [], "permalink": "https://x"}],
            decision={"action": "new_proposal", "confidence": "low"})
        self._scan(llm)
        (req,) = registry.load_all()
        self.assertEqual(req.status, "detected")   # 备选, not lost, not 提案


class SlackMcpTriageTestCase(TriageBase):
    def test_mcp_informational_item_files_no_card_but_marker_advances(self):
        llm = _FakeLLM(
            extraction=[{"title": "Quinton 说今早会改权限",
                         "summary": "纯通知", "who": "quinton", "channel": "DM",
                         "date": "2026-07-09", "quote": "will fix perms this morning"}],
            decision={"action": "ignore", "reason": "纯信息性"})
        self.assertEqual(radar_slack.mcp_scan(self.cfg, runner=llm), 0)
        self.assertEqual(registry.load_all(), [])
        self.assertIsNotNone(radar_slack._read_mcp_marker())  # pass succeeded


# --------------------------------------------------------------------------- #
# obsidian path (radar.scan) through the gate
# --------------------------------------------------------------------------- #
class ObsidianTriageTestCase(TriageBase):
    def setUp(self):
        super().setUp()
        import tempfile
        self.tmp = tempfile.TemporaryDirectory(prefix="radar-triage-vault-")
        self.addCleanup(self.tmp.cleanup)
        self.raw = Path(self.tmp.name) / "2 - raw"
        self.raw.mkdir(parents=True)
        config.CONFIG_PATH.write_text(
            f'sources:\n  obsidian_raw: "{self.raw}"\n', encoding="utf-8")

    def _note(self, name, text, mtime=BASE):
        import os
        p = self.raw / name
        p.write_text(text, encoding="utf-8")
        os.utime(p, (mtime, mtime))
        return p

    @staticmethod
    def _items(*items):
        return lambda text: json.dumps(list(items), ensure_ascii=False)

    def test_ignored_item_files_nothing_and_marker_advances(self):
        self._note("2026-07-09 sync.md", "quinton will fix perms later")
        runner = self._items({"title": "Quinton 说稍后会改权限", "type": "comms",
                              "tier": "T1", "hardness": "soft", "deadline": None,
                              "cost_estimate_usd": None, "quote": "will fix later"})
        triager = _FakeLLM(decision={"action": "ignore", "reason": "未来条件性"})
        summary = radar.scan(runner=runner, triager=triager)
        self.assertEqual(summary["extracted"], 1)
        self.assertEqual(summary["reconciled"], 0)
        self.assertEqual(summary["cards"], 0)
        self.assertEqual(registry.load_all(), [])
        self.assertEqual(radar._read_marker(), BASE)   # processed, not lost

    def test_hard_deadline_split_preserved_for_new_proposals(self):
        """(e) hard+deadline -> card_sent；soft -> detected/备选."""
        self._note("2026-07-09 sync.md", "two asks")
        runner = self._items(
            {"title": "Ship the Q3 quarterly report", "type": "report",
             "tier": "T1", "hardness": "hard", "deadline": "2026-07-20",
             "cost_estimate_usd": None, "quote": "ship by July 20"},
            {"title": "Maybe tidy the team wiki sometime", "type": "other",
             "tier": "T1", "hardness": "soft", "deadline": None,
             "cost_estimate_usd": None, "quote": "tidy the wiki"})
        triager = _FakeLLM(decision={"action": "new_proposal"})
        summary = radar.scan(runner=runner, triager=triager)
        self.assertEqual(summary["reconciled"], 2)
        self.assertEqual(summary["cards"], 1)
        by_title = {r.title: r for r in registry.load_all()}
        self.assertEqual(by_title["Ship the Q3 quarterly report"].status, "card_sent")
        self.assertEqual(by_title["Maybe tidy the team wiki sometime"].status, "detected")

    def test_obsidian_hit_on_delivered_card_becomes_followup(self):
        _seed("R-019", "给 Quinton 开通 PRD 文档编辑权限", "delivered")
        self._note("2026-07-09 1on1.md", "quinton set zelin as editor")
        runner = self._items({"title": "确认 editor 权限并继续补文档", "type": "comms",
                              "tier": "T1", "hardness": "soft", "deadline": None,
                              "cost_estimate_usd": None, "quote": "set you as editor"})
        triager = _FakeLLM(decision={"action": "relates_to", "req": "R-019",
                                     "note": "权限已开", "needs_action": True})
        summary = radar.scan(runner=runner, triager=triager)
        self.assertEqual(summary["cards"], 1)   # follow-up 按统一口径直接进提案列
        fus = self._followups_of("R-019")
        self.assertEqual(len(fus), 1)
        self.assertEqual(fus[0].status, "card_sent")
        self.assertTrue(fus[0].summary.startswith("既往卡 R-019 的后续："))
        # the meeting source landed on the follow-up card
        self.assertEqual(fus[0].sources[0]["channel"], "meeting")

    def test_runner_only_injection_keeps_legacy_behavior(self):
        """(f) 老测试的注入方式（只有 runner）不 spawn 真 claude、行为不变."""
        self._note("2026-07-09 sync.md", "ship it")
        item = {"title": "Ship the Q3 quarterly report", "type": "report",
                "tier": "T1", "hardness": "hard", "deadline": "2026-07-20",
                "cost_estimate_usd": None, "quote": "ship by July 20"}
        summary = radar.scan(runner=lambda text: json.dumps([item]))
        self.assertEqual(summary["reconciled"], 1)
        self.assertEqual(summary["cards"], 1)
        self.assertEqual(registry.load_all()[0].status, "card_sent")

    def test_extract_prompt_no_longer_drops_non_urgent_asks(self):
        # 提取层双重过滤已拆除：只滤纯信息/闲聊/已完成，非紧急真实需求带
        # urgent 标记进入下游三选一闸门
        self.assertNotIn("include ONLY items", radar.EXTRACT_PROMPT)
        self.assertIn("urgent", radar.EXTRACT_PROMPT)
        self.assertIn("NOT urgent", radar.EXTRACT_PROMPT)

    def test_non_urgent_hard_deadline_item_parks_in_backlog(self):
        """urgent:false 的项即使 hard+deadline 也不进提案列（现在不需要行动）."""
        self._note("2026-07-09 sync.md", "next quarter X, hard date already known")
        runner = self._items(
            {"title": "Prepare the Q4 launch checklist", "type": "other",
             "tier": "T1", "hardness": "hard", "deadline": "2026-10-15",
             "urgent": False, "cost_estimate_usd": None,
             "quote": "we'll need the Q4 checklist by Oct 15"})
        triager = _FakeLLM(decision={"action": "new_proposal",
                                     "confidence": "low"})
        summary = radar.scan(runner=runner, triager=triager)
        self.assertEqual(summary["reconciled"], 1)
        self.assertEqual(summary["cards"], 0)
        (req,) = registry.load_all()
        self.assertEqual(req.status, "detected")   # 备选停车，raise 可升级


# --------------------------------------------------------------------------- #
# self-DM quick capture relates_to on a resolved card (g)
# --------------------------------------------------------------------------- #
class QuickCaptureFollowUpTestCase(TriageBase):
    def test_relates_to_delivered_creates_followup_with_lineage(self):
        _seed("R-019", "给 Quinton 开通 PRD 文档编辑权限", "delivered")
        reply = quick_capture.apply_result(
            {"action": "relates_to", "req": "R-019",
             "note": "Quinton 已设我为 editor，继续把文档补完"}, self.cfg)
        fus = [r for r in registry.load_all() if r.improvement_of == "R-019"]
        self.assertEqual(len(fus), 1)
        self.assertEqual(fus[0].status, "card_sent")
        self.assertTrue(fus[0].summary.startswith("既往卡 R-019 的后续："))
        self.assertIn(fus[0].id, reply)
        self.assertIn("后续卡", reply)
        self.assertIn("follow-up", reply)   # 双语回执
        # parent delivered card is NOT reopened and got no [quick] note
        parent = registry.load("R-019")
        self.assertEqual(parent.status, "delivered")
        self.assertNotIn("[quick]", parent.notes or "")

    def test_second_mention_folds_into_open_followup(self):
        _seed("R-019", "给 Quinton 开通 PRD 文档编辑权限", "delivered")
        quick_capture.apply_result(
            {"action": "relates_to", "req": "R-019", "note": "权限已开"}, self.cfg)
        reply = quick_capture.apply_result(
            {"action": "relates_to", "req": "R-019", "note": "再补一句"}, self.cfg)
        fus = [r for r in registry.load_all() if r.improvement_of == "R-019"]
        self.assertEqual(len(fus), 1)                     # 不出第二张
        self.assertIn("已并入", reply)
        self.assertIn("[radar] 再补一句", fus[0].notes)

    def test_relates_to_open_card_behavior_unchanged(self):
        _seed("R-030", "写周报", "card_sent")
        reply = quick_capture.apply_result(
            {"action": "relates_to", "req": "R-030", "note": "加 eval 数字"}, self.cfg)
        self.assertIn("已在待审批", reply)
        self.assertEqual(len(registry.load_all()), 1)
        self.assertIn("[quick] 加 eval 数字", registry.load("R-030").notes)

    def test_relates_to_merged_duplicate_hangs_followup_on_primary(self):
        _seed("R-010", "给 Quinton 开通 PRD 权限", "delivered")
        _seed("R-011", "Quinton PRD 权限（重复卡）", "merged",
              merged_into="R-010")
        quick_capture.apply_result(
            {"action": "relates_to", "req": "R-011", "note": "有后续"}, self.cfg)
        fus = [r for r in registry.load_all() if r.improvement_of]
        self.assertEqual(len(fus), 1)
        self.assertEqual(fus[0].improvement_of, "R-010")   # canonicalized


# --------------------------------------------------------------------------- #
# self-DM quick capture 无损原则（例：非紧急备忘不得被 ignore/丢失）
# --------------------------------------------------------------------------- #
class QuickCaptureLosslessTestCase(TriageBase):
    def test_capture_prompt_keeps_lossless_bar_not_radar_hard_bar(self):
        prompt = quick_capture.build_capture_prompt("X 说今晚会改权限，记一下", self.cfg)
        # option 3 narrowed to pure chit-chat; the radar action-now hard bar
        # must NOT be copied into the explicit self-capture path
        self.assertIn("闲聊", prompt)
        self.assertNotIn("硬标准", prompt)
        self.assertIn("无损原则", prompt)
        self.assertIn("confidence", prompt)   # low => 备选, not ignore
        self.assertIn("备选", prompt)

    def test_low_confidence_capture_lands_in_backlog_not_ignored(self):
        reply = quick_capture.apply_result(
            {"action": "new_proposal", "confidence": "low",
             "summary": "X 说今晚会改权限，到时候确认一下",
             "title": "确认 X 改完权限", "type": "other", "tier": "T0",
             "plan": ["等 X 改完", "确认权限生效"],
             "_text": "X 说今晚会改权限，记一下"}, self.cfg)
        (req,) = registry.load_all()
        self.assertEqual(req.status, "detected")       # 储备, not lost
        self.assertIn("储备", reply)
        self.assertIn(req.id, reply)

    def test_default_capture_still_files_card_sent(self):
        quick_capture.apply_result(
            {"action": "new_proposal", "summary": "现在就回 manager",
             "title": "回 manager", "type": "comms", "tier": "T1",
             "plan": ["回复"], "_text": "回 manager"}, self.cfg)
        (req,) = registry.load_all()
        self.assertEqual(req.status, "card_sent")


if __name__ == "__main__":
    unittest.main()
