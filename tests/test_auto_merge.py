"""§38.3 规则合并提示 — deterministic auto merge suggestions + throttles.

Pins: creation on the two strong signals (high overlap; shared contact +
moderate overlap), the §21 job shape (so the existing merge_apply path
consumes it unchanged), and all three throttles — one suggestion per pair
EVER, max 3 outstanding, never across terminal/linked cards.
"""
import json
import unittest

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import actd, merge_review
from act.lib import auto_merge, config, registry
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


def _seed(rid, summary, status=State.CARD_SENT.value, **kw):
    r = Requirement(id=rid, title=rid, status=status, summary=summary, **kw)
    registry.save(r)
    return r


def _jobs():
    return [json.loads(p.read_text(encoding="utf-8"))
            for p in sorted(merge_review.MERGE_DIR.glob("*.json"))]


DUP_A = "整理 EB-1A 推荐信 recommendation letters 清单 wegreened"
DUP_B = "EB-1A 推荐信 recommendation letters wegreened 跟进"


class AutoSuggestTestCase(unittest.TestCase):
    def setUp(self):
        _clean()
        self.addCleanup(_clean)

    def test_high_overlap_creates_21_shaped_done_job(self):
        _seed("R-001", DUP_A)
        _seed("R-002", DUP_B)
        created = auto_merge.scan_new_cards()
        self.assertEqual(created, 1)
        (job,) = _jobs()
        # exact §21 consumer contract: done + merge + primary ∈ ids
        self.assertEqual(job["status"], "done")
        self.assertEqual(job["verdict"], "merge")
        self.assertEqual(job["ids"], ["R-001", "R-002"])
        self.assertEqual(job["primary"], "R-001")     # older card wins
        self.assertEqual(job["confidence"], "deterministic")
        self.assertTrue(job["auto"])
        self.assertTrue(str(job["id"]).startswith("MS-"))
        self.assertIn("规则判定", job["rationale"])
        self.assertTrue(job.get("expires_at"))
        self.assertTrue(job.get("action_plan"))

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
        self.assertEqual(auto_merge.scan_new_cards(), 1)

    def test_moderate_overlap_without_contact_does_not_fire(self):
        _seed("R-001", self._MOD_A,
              sources=[{"who": "Quinton", "channel": "slack",
                        "date": "2026-07-16", "quote": "q"}])
        _seed("R-002", self._MOD_B,
              sources=[{"who": "someone-else", "channel": "slack",
                        "date": "2026-07-16", "quote": "q"}])
        self.assertEqual(auto_merge.scan_new_cards(), 0)

    def test_pair_only_ever_suggested_once_even_after_dismiss(self):
        _seed("R-001", DUP_A)
        _seed("R-002", DUP_B)
        self.assertEqual(auto_merge.scan_new_cards(), 1)
        (job,) = _jobs()
        # user dismisses; the job file later TTL-purges — simulate both
        merge_review.dismiss_job(job)
        merge_review.job_path(job["id"]).unlink()
        # cards leave and re-enter the open set (delivered → re-raised)
        r2 = registry.load("R-002")
        r2.set_status(State.DELIVERED.value)
        registry.save(r2)
        auto_merge.scan_new_cards()
        r2.set_status(State.CARD_SENT.value)
        registry.save(r2)
        self.assertEqual(auto_merge.scan_new_cards(), 0)   # pair is final
        self.assertEqual(_jobs(), [])

    def test_max_three_outstanding(self):
        for i in range(1, 6):
            _seed(f"R-{2 * i - 1:03d}", f"独立主题 tag{i} 推荐信 wegreened letters")
            _seed(f"R-{2 * i:03d}", f"独立主题 tag{i} 推荐信 wegreened letters 跟进")
        auto_merge.scan_new_cards()
        self.assertEqual(len([j for j in _jobs() if j.get("auto")]), 3)
        # a dismissed job frees a slot; an over-budget pair stays eligible
        job = next(j for j in _jobs() if j.get("auto"))
        merge_review.dismiss_job(job)
        state = json.loads(auto_merge.STATE_PATH.read_text(encoding="utf-8"))
        state["scanned"] = []          # force a rescan of the same cards
        auto_merge.STATE_PATH.write_text(json.dumps(state), encoding="utf-8")
        self.assertEqual(auto_merge.scan_new_cards(), 1)

    def test_terminal_and_linked_cards_never_suggested(self):
        # three near-dupe pairs, DISTINCT topics (no cross-pair overlap) —
        # each would fire on its own, and each is blocked by one rule.
        _seed("R-001", "滑雪板 snowboard 采购 对比", status=State.DELIVERED.value)
        _seed("R-002", "滑雪板 snowboard 采购 跟进")     # vs delivered → no
        _seed("R-003", "报税 taxreturn 材料 整理", improvement_of="R-004")
        _seed("R-004", "报税 taxreturn 材料 补充")       # lineage-linked → no
        _seed("R-005", "签证 visa 预约 delta", thread_id="R-005")
        _seed("R-006", "签证 visa 预约 提醒", thread_id="R-005")  # same thread → no
        self.assertEqual(auto_merge.scan_new_cards(), 0)

    def test_steady_state_pass_is_incremental(self):
        _seed("R-001", DUP_A)
        _seed("R-002", DUP_B)
        auto_merge.scan_new_cards()
        # nothing new → no work, no extra jobs
        self.assertEqual(auto_merge.scan_new_cards(), 0)
        self.assertEqual(len(_jobs()), 1)

    def test_accept_runs_existing_merge_apply_path(self):
        _seed("R-001", DUP_A)
        _seed("R-002", DUP_B)
        auto_merge.scan_new_cards()
        (job,) = _jobs()
        self.assertEqual(actd._apply_merge_decision("merge_apply", job["id"]),
                         "running")
        sec = registry.load("R-002")
        self.assertEqual(sec.status, State.MERGED.value)
        self.assertEqual(sec.merged_into, "R-001")
        self.assertIn("[merged] R-002", registry.load("R-001").notes)

    def test_dashboard_projects_auto_job_with_deterministic_confidence(self):
        _seed("R-001", DUP_A)
        _seed("R-002", DUP_B)
        auto_merge.scan_new_cards()
        (row,) = _merge_suggestions()
        self.assertEqual(row["status"], "done")
        self.assertEqual(row["verdict"], "merge")
        self.assertEqual(row["confidence"], "deterministic")
