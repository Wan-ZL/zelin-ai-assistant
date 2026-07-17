"""§38.3/§44 规则近重复检测 — deterministic rule → silent two-card check.

Pins: the rule fires on the two strong signals (high overlap; shared contact
+ moderate overlap) and now REQUESTS A SILENT CHECK (§44 — no suggestion
card, nobody is asked), plus all three throttles — one check per pair EVER,
max 3 pending checks, never across terminal/linked cards. The check's own
judge/execute logic lives in tests/test_silent_merge.py.
"""
import json
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import actd, merge_review
from act.lib import auto_merge, config, registry, silent_merge
from act.lib.dashboard import _merge_suggestions
from act.lib.registry import Requirement, State


def _clean():
    config.ensure_state_dirs()
    for p in config.REGISTRY_DIR.glob("*.yaml"):
        p.unlink()
    if merge_review.MERGE_DIR.exists():
        for p in merge_review.MERGE_DIR.glob("*.json"):
            p.unlink()
    if auto_merge.STATE_PATH.exists():
        auto_merge.STATE_PATH.unlink()
    if silent_merge.SILENT_DIR.exists():
        for p in silent_merge.SILENT_DIR.glob("*.json"):
            p.unlink()


def _seed(rid, summary, status=State.CARD_SENT.value, **kw):
    r = Requirement(id=rid, title=rid, status=status, summary=summary, **kw)
    registry.save(r)
    return r


def _jobs():
    return [json.loads(p.read_text(encoding="utf-8"))
            for p in sorted(merge_review.MERGE_DIR.glob("*.json"))]


def _silent_jobs():
    if not silent_merge.SILENT_DIR.exists():
        return []
    return [json.loads(p.read_text(encoding="utf-8"))
            for p in sorted(silent_merge.SILENT_DIR.glob("SM-*.json"))]


class _NoSpawn:
    """Patch for silent_merge's detached judge Popen — the job file is
    written for real (the budget counts pending files), the subprocess
    never launches (tests drive the judge directly)."""

    def __init__(self, *a, **k):
        pass


DUP_A = "整理 EB-1A 推荐信 recommendation letters 清单 wegreened"
DUP_B = "EB-1A 推荐信 recommendation letters wegreened 跟进"


class AutoSuggestTestCase(unittest.TestCase):
    def setUp(self):
        _clean()
        self.addCleanup(_clean)

    def test_high_overlap_requests_silent_check(self):
        # §44: rule hit → pending SM- check, NO §21 suggestion card, and
        # nothing reaches the board (the whole point).
        _seed("R-001", DUP_A)
        _seed("R-002", DUP_B)
        with mock.patch.object(silent_merge.subprocess, "Popen", _NoSpawn):
            created = auto_merge.scan_new_cards()
        self.assertEqual(created, 1)
        (job,) = _silent_jobs()
        self.assertEqual(job["status"], "pending")
        self.assertEqual(job["primary"], "R-001")     # older card wins
        self.assertEqual(job["secondary"], "R-002")
        self.assertTrue(str(job["id"]).startswith("SM-"))
        self.assertEqual(_jobs(), [])                 # no MS- suggestion card
        self.assertEqual(_merge_suggestions(), [])    # nothing on the board

    def test_unrelated_cards_do_not_fire(self):
        _seed("R-001", "修 login oauth bug")
        _seed("R-002", "订购 snowboard 装备")
        self.assertEqual(auto_merge.scan_new_cards(), 0)
        self.assertEqual(_jobs(), [])

    # moderate-overlap fixture: |B| = 6 tokens, 3 shared → score 0.5, which
    # sits inside the (0.4, 0.6) band where ONLY a shared contact fires.
    _MOD_A = "PRD 文档 编辑 权限 permissions dashboard alpha beta"
    _MOD_B = "PRD 权限 permissions 确认 followup"

    def test_shared_contact_plus_moderate_overlap_fires(self):
        src_q = [{"who": "Quinton", "channel": "slack", "date": "2026-07-16",
                  "quote": "q"}]
        _seed("R-001", self._MOD_A, sources=src_q)
        _seed("R-002", self._MOD_B, sources=src_q)
        with mock.patch.object(silent_merge.subprocess, "Popen", _NoSpawn):
            self.assertEqual(auto_merge.scan_new_cards(), 1)

    def test_moderate_overlap_without_contact_does_not_fire(self):
        _seed("R-001", self._MOD_A,
              sources=[{"who": "Quinton", "channel": "slack",
                        "date": "2026-07-16", "quote": "q"}])
        _seed("R-002", self._MOD_B,
              sources=[{"who": "someone-else", "channel": "slack",
                        "date": "2026-07-16", "quote": "q"}])
        self.assertEqual(auto_merge.scan_new_cards(), 0)

    def test_pair_only_ever_checked_once_whatever_the_outcome(self):
        _seed("R-001", DUP_A)
        _seed("R-002", DUP_B)
        with mock.patch.object(silent_merge.subprocess, "Popen", _NoSpawn):
            self.assertEqual(auto_merge.scan_new_cards(), 1)
            (job,) = _silent_jobs()
            # the check concludes "separate" and its file later TTL-purges
            silent_merge._finish(job["id"], "done", verdict="separate")
            silent_merge._job_path(job["id"]).unlink()
            # cards leave and re-enter the open set (delivered → re-raised)
            r2 = registry.load("R-002")
            r2.set_status(State.DELIVERED.value)
            registry.save(r2)
            auto_merge.scan_new_cards()
            r2.set_status(State.CARD_SENT.value)
            registry.save(r2)
            self.assertEqual(auto_merge.scan_new_cards(), 0)   # pair is final
            self.assertEqual(_silent_jobs(), [])

    # five near-dupe pairs with DISTINCT topics (no cross-pair overlap): the
    # budget tests below need "5 real pairs" to mean exactly 5 suggestions.
    _FIVE_PAIRS = [
        ("snowboard burton gear 滑雪板 对比", "snowboard burton gear 滑雪板 跟进"),
        ("taxreturn form1040 irs 报税材料 整理", "taxreturn form1040 irs 报税材料 补充"),
        ("visaappointment delta airline 签证 预约", "visaappointment delta airline 签证 提醒"),
        ("kubernetes ingress helm 部署脚本 修复", "kubernetes ingress helm 部署脚本 继续"),
        ("newsletter mailchimp campaign 邮件模板 草稿", "newsletter mailchimp campaign 邮件模板 终稿"),
    ]

    def test_max_three_pending_and_deferred_pairs_survive(self):
        # review blocker 5 semantics carried into §44: the budgeted resource
        # is now concurrent pending checks (detached LLM subprocesses) —
        # over-budget pairs survive until the checks drain, through the REAL
        # re-scan path, no ledger surgery.
        for i, (a, b) in enumerate(self._FIVE_PAIRS, start=1):
            _seed(f"R-{2 * i - 1:03d}", a)
            _seed(f"R-{2 * i:03d}", b)
        with mock.patch.object(silent_merge.subprocess, "Popen", _NoSpawn):
            self.assertEqual(auto_merge.scan_new_cards(), 3)     # cap
            self.assertEqual(auto_merge.scan_new_cards(), 0)     # still capped
            # the three checks conclude …
            for job in _silent_jobs():
                if job["status"] == "pending":
                    silent_merge._finish(job["id"], "done", verdict="separate")
            # … and the NEXT regular pass files the two deferred pairs.
            self.assertEqual(auto_merge.scan_new_cards(), 2)
            self.assertEqual(auto_merge.scan_new_cards(), 0)     # all pairs done
            self.assertEqual(len(_silent_jobs()), 5)

    def test_terminal_and_linked_cards_never_suggested(self):
        # three near-dupe pairs, DISTINCT topics (no cross-pair overlap) —
        # each fires on its own (asserted below), and each is blocked by one rule.
        a1 = _seed("R-001", "snowboard burton gear 滑雪板 对比",
                   status=State.DELIVERED.value)
        a2 = _seed("R-002", "snowboard burton gear 滑雪板 跟进")  # vs delivered → no
        b1 = _seed("R-003", "taxreturn form1040 irs 报税材料 整理",
                   improvement_of="R-004")
        b2 = _seed("R-004", "taxreturn form1040 irs 报税材料 补充")  # lineage → no
        c1 = _seed("R-005", "visaappointment delta airline 签证 预约",
                   thread_id="R-005")
        c2 = _seed("R-006", "visaappointment delta airline 签证 提醒",
                   thread_id="R-005")                              # same thread → no
        # the SIGNAL is present for every pair — only the guards block them
        for x, y in ((a1, a2), (b1, b2), (c1, c2)):
            self.assertTrue(auto_merge.is_near_dupe(x, y)[0])
        self.assertEqual(auto_merge.scan_new_cards(), 0)

    def test_shared_identifier_alone_never_suggests(self):
        # review blocker 6 repro: ONE shared identifier (EB-1A → eb1a/eb/1a
        # sub-tokens) + generic CJK bigram overlap must not read as a
        # duplicate — these are two distinct tasks on the same matter.
        _seed("R-001", "EB-1A 推荐信跟进")
        _seed("R-002", "EB-1A 推荐信初稿审阅")
        self.assertEqual(auto_merge.scan_new_cards(), 0)
        self.assertEqual(_jobs(), [])

    def test_both_invested_cards_are_left_alone(self):
        # §44: the folded-away secondary must be a LIGHT card. Two invested
        # cards (approved/executing/review) that trip the rule are simply
        # left alone — no check, no card, pair final.
        _seed("R-001", DUP_A, status=State.EXECUTING.value)
        _seed("R-002", DUP_B, status=State.REVIEW.value)
        with mock.patch.object(silent_merge.subprocess, "Popen", _NoSpawn):
            self.assertEqual(auto_merge.scan_new_cards(), 0)
            self.assertEqual(_silent_jobs(), [])
            self.assertEqual(auto_merge.scan_new_cards(), 0)   # pair is final

    def test_invested_primary_light_secondary_still_checks(self):
        # invested + light → the light card is the secondary regardless of id
        # order (the invested side is kept).
        _seed("R-001", DUP_A)                                  # light (card_sent)
        _seed("R-002", DUP_B, status=State.EXECUTING.value)    # invested
        with mock.patch.object(silent_merge.subprocess, "Popen", _NoSpawn):
            self.assertEqual(auto_merge.scan_new_cards(), 1)
        (job,) = _silent_jobs()
        self.assertEqual(job["primary"], "R-002")   # invested card is kept
        self.assertEqual(job["secondary"], "R-001") # light card folds away

    def test_split_card_never_suggested_against_origin(self):
        # review blocker 7: split a note → the new card's text ≈ the origin
        # note by construction; a same-pass scan suggesting the merge back
        # would undo the undo. split_from lineage blocks the pair.
        origin = _seed("R-001", "原卡 kubernetes ingress helm 部署")
        ts = registry.append_fold_note(
            origin, "newsletter mailchimp campaign 邮件模板 草稿", "radar")
        registry.save(origin)
        self.assertEqual(actd._apply_split_note("R-001", ts), "running")
        new = next(r for r in registry.load_all() if r.id != "R-001")
        self.assertEqual(new.split_from, "R-001")
        # the raw SIGNAL fires (non-vacuous)…
        self.assertTrue(auto_merge.is_near_dupe(origin, new)[0])
        # …but the lineage guard keeps the pair out, same pass and later.
        self.assertEqual(auto_merge.scan_new_cards(), 0)
        self.assertEqual(_jobs(), [])

    def test_same_colleague_two_different_asks_no_false_positive(self):
        # review blocker 2, reproduced verbatim: shared contact + zh function
        # words (帮我/我看/一下) must not manufacture a merge suggestion —
        # the contact path fires exactly on the population with two DIFFERENT
        # real asks from one person.
        src = [{"who": "colleague", "channel": "slack", "date": "2026-07-16",
                "quote": "q"}]
        _seed("R-001", "帮我看一下报销流程", sources=src)
        _seed("R-002", "帮我看简历", sources=src)
        self.assertEqual(auto_merge.scan_new_cards(), 0)
        self.assertEqual(_jobs(), [])

    def test_steady_state_pass_is_incremental(self):
        _seed("R-001", DUP_A)
        _seed("R-002", DUP_B)
        with mock.patch.object(silent_merge.subprocess, "Popen", _NoSpawn):
            auto_merge.scan_new_cards()
            # nothing new → no work, no extra checks
            self.assertEqual(auto_merge.scan_new_cards(), 0)
            self.assertEqual(len(_silent_jobs()), 1)

    def test_rule_hits_never_reach_the_board(self):
        # §44 headline behavior: whatever the rule finds, the board stays
        # clean — SM- checks live in their own directory, the §21
        # merge_suggestions projection never sees them, and no MS- job is
        # ever minted by the rule path (the manual multi-select §21 flow
        # is untouched and covered by test_merge_review.py).
        _seed("R-001", DUP_A)
        _seed("R-002", DUP_B)
        with mock.patch.object(silent_merge.subprocess, "Popen", _NoSpawn):
            auto_merge.scan_new_cards()
        self.assertEqual(len(_silent_jobs()), 1)
        self.assertEqual(_jobs(), [])
        self.assertEqual(_merge_suggestions(), [])
