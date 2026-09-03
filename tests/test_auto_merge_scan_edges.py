"""§38.3/§44 scan_new_cards 的边界与循环流判例（夜间变异存活体逐个钉死）。

tests/test_auto_merge.py 钉的是规则火不火与三条节流的整体语义；这里钉的是
2026-09-02 夜报里 auto_merge.py 的 28 个存活变异体所对应的**边界行为**：
预算恰好为 0 / 卡中途耗尽预算 / 跳过自身与已判对、已链接的对 / 双方都已
投入 / 判官起不来 / 无命中也要落 scanned 台账 / 任何异常都返回整数 0 /
record_pair_final 不吞既有台账 / _linked 的 thread_key 分支 / 两条阈值
的等号边界与最少证据数。全部经注入缝（is_near_dupe / _request_silent_check /
_outstanding_auto / match_corpus.score_pair 打桩），绝不 spawn 判官子进程。

已认定的等价变异体（不补测试）：`_idnum(other.id) <= _idnum(new.id)` 的
`<=`→`<`（两张不同卡的排序键含 id 字面本身，永不相等）；`_save_state` 的
`ensure_ascii` / `indent` 翻转（纯格式，读回语义不变）。
"""
import json
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act.lib import auto_merge, config, match_corpus, registry, silent_merge
from act.lib.registry import Requirement, State


def _clean():
    config.ensure_state_dirs()
    for p in config.REGISTRY_DIR.glob("*.yaml"):
        p.unlink()
    if auto_merge.STATE_PATH.exists():
        auto_merge.STATE_PATH.unlink()
    if silent_merge.SILENT_DIR.exists():
        for p in silent_merge.SILENT_DIR.glob("*.json"):
            p.unlink()


def _seed(rid, status=State.CARD_SENT.value, **kw):
    r = Requirement(id=rid, title=rid, status=status, summary=rid, **kw)
    registry.save(r)
    return r


def _pre_scanned(*ids):
    """Only the cards NOT listed here count as new on the next pass."""
    auto_merge.STATE_PATH.write_text(
        json.dumps({"scanned": sorted(ids), "suggested": []}), encoding="utf-8")


def _state():
    return json.loads(auto_merge.STATE_PATH.read_text(encoding="utf-8"))


def _dupes(*pairs):
    """is_near_dupe stub: fires exactly on the given unordered id pairs and
    counts every call (loop-flow mutants are only visible in the call log)."""
    keys = {auto_merge.pair_key(a, b) for a, b in pairs}
    calls = []

    def fake(a, b, cfg=None):
        calls.append((str(a.id), str(b.id)))
        hit = auto_merge.pair_key(a.id, b.id) in keys
        return (hit, ["tok"] if hit else [], "high" if hit else "")
    fake.calls = calls
    return fake


class ScanBudgetEdgesTestCase(unittest.TestCase):
    def setUp(self):
        _clean()
        self.addCleanup(_clean)
        self.requests = []

        def request(a, b):
            self.requests.append((str(a.id), str(b.id)))
            return f"SM-{len(self.requests):08d}"
        self._req = mock.patch.object(auto_merge, "_request_silent_check", request)
        self._req.start()
        self.addCleanup(self._req.stop)

    def _outstanding(self, n):
        p = mock.patch.object(auto_merge, "_outstanding_auto", return_value=n)
        p.start()
        self.addCleanup(p.stop)

    def test_budget_exactly_zero_defers_without_comparing(self):
        # MAX_OUTSTANDING pending checks → budget 0: every new card is
        # deferred BEFORE any comparison (not after finding a dupe).
        _seed("R-001")
        _seed("R-002")
        self._outstanding(auto_merge.MAX_OUTSTANDING)
        fake = _dupes(("R-001", "R-002"))
        with mock.patch.object(auto_merge, "is_near_dupe", fake):
            self.assertEqual(auto_merge.scan_new_cards(), 0)
        self.assertEqual(fake.calls, [])
        self.assertEqual(self.requests, [])
        self.assertFalse(auto_merge.STATE_PATH.exists())   # nothing changed

    def test_mid_card_exhaustion_defers_the_card_and_stops_comparing(self):
        # budget 1, new card with three dupes: the first pair is requested,
        # the second hits the exhausted budget → whole card deferred and the
        # remaining pairs are NOT compared (break, not continue).
        for rid in ("R-001", "R-002", "R-003", "R-004"):
            _seed(rid)
        _pre_scanned("R-001", "R-002", "R-003")
        self._outstanding(auto_merge.MAX_OUTSTANDING - 1)
        fake = _dupes(("R-004", "R-001"), ("R-004", "R-002"), ("R-004", "R-003"))
        with mock.patch.object(auto_merge, "is_near_dupe", fake):
            self.assertEqual(auto_merge.scan_new_cards(), 1)
        self.assertEqual(self.requests, [("R-001", "R-004")])
        self.assertEqual(len(fake.calls), 2)          # R-001, R-002 — never R-003
        st = _state()
        self.assertNotIn("R-004", st["scanned"])      # deferred: re-enters as new
        self.assertEqual(st["suggested"], [auto_merge.pair_key("R-001", "R-004")])

    def test_new_card_listed_before_its_dupe_still_finds_it(self):
        # the self-skip is `continue`: a new card that sorts FIRST in the open
        # set must go on to compare against everything after itself.
        _seed("R-001")
        _seed("R-002")
        _pre_scanned("R-002")
        self._outstanding(0)
        with mock.patch.object(auto_merge, "is_near_dupe", _dupes(("R-001", "R-002"))):
            self.assertEqual(auto_merge.scan_new_cards(), 1)
        self.assertEqual(self.requests, [("R-001", "R-002")])

    def test_linked_pair_is_skipped_not_terminal(self):
        # a lineage-linked card earlier in the open set must not end the
        # new card's scan — the real dupe after it is still requested.
        _seed("R-001", thread_key="mail:abc")
        _seed("R-002")
        _seed("R-003", thread_key="mail:abc")
        _pre_scanned("R-001", "R-002")
        self._outstanding(0)
        fake = _dupes(("R-003", "R-001"), ("R-003", "R-002"))
        with mock.patch.object(auto_merge, "is_near_dupe", fake):
            self.assertEqual(auto_merge.scan_new_cards(), 1)
        self.assertEqual(self.requests, [("R-002", "R-003")])
        self.assertEqual(fake.calls, [("R-003", "R-002")])   # linked pair never scored

    def test_both_invested_pair_is_final_but_scan_continues(self):
        # invested new card vs invested dupe → pair final, no check; the
        # LIGHT dupe after it still gets its check (light side folds away).
        _seed("R-001", status=State.EXECUTING.value)
        _seed("R-002")
        _seed("R-003", status=State.REVIEW.value)
        _pre_scanned("R-001", "R-002")
        self._outstanding(0)
        with mock.patch.object(auto_merge, "is_near_dupe",
                               _dupes(("R-003", "R-001"), ("R-003", "R-002"))):
            self.assertEqual(auto_merge.scan_new_cards(), 1)
        self.assertEqual(self.requests, [("R-003", "R-002")])   # invested kept
        self.assertEqual(sorted(_state()["suggested"]),
                         sorted([auto_merge.pair_key("R-001", "R-003"),
                                 auto_merge.pair_key("R-002", "R-003")]))

    def test_failed_request_leaves_pair_open_and_continues(self):
        # judge spawn failure (request → None): the pair is NOT ledgered (a
        # later pass retries it) and the scan moves on to the next dupe.
        _seed("R-001")
        _seed("R-002")
        _seed("R-003")
        _pre_scanned("R-001", "R-002")
        self._outstanding(0)
        self._req.stop()
        answers = iter([None, "SM-ok"])
        with mock.patch.object(auto_merge, "_request_silent_check",
                               lambda a, b: next(answers)), \
                mock.patch.object(auto_merge, "is_near_dupe",
                                  _dupes(("R-003", "R-001"), ("R-003", "R-002"))):
            self.assertEqual(auto_merge.scan_new_cards(), 1)
        self._req.start()
        self.assertEqual(_state()["suggested"], [auto_merge.pair_key("R-002", "R-003")])

    def test_no_hit_still_retires_the_card_into_the_scanned_ledger(self):
        # zero requests but a changed scanned set → the ledger IS written
        # (otherwise every pass re-compares the same quiet card forever).
        _seed("R-001")
        _seed("R-002")
        self._outstanding(0)
        with mock.patch.object(auto_merge, "is_near_dupe", _dupes()):
            self.assertEqual(auto_merge.scan_new_cards(), 0)
        self.assertTrue(auto_merge.STATE_PATH.exists())
        self.assertEqual(_state()["scanned"], ["R-001", "R-002"])

    def test_judge_pair_answers_false_only_on_mid_card_exhaustion(self):
        a = Requirement(id="R-001", title="a", status=State.CARD_SENT.value)
        b = Requirement(id="R-002", title="b", status=State.CARD_SENT.value)
        scan = auto_merge._Scan(scanned=set(), suggested=set(), budget=0, cfg=None)
        with mock.patch.object(auto_merge, "is_near_dupe", _dupes(("R-001", "R-002"))):
            self.assertIs(scan.judge_pair(a, b), False)       # dupe, no budget → defer
            self.assertIs(scan.judge_pair(a, a), True)        # self: skipped
        with mock.patch.object(auto_merge, "is_near_dupe", _dupes()):
            self.assertIs(scan.judge_pair(a, b), True)        # not a dupe
        self.assertEqual(scan.deferred, {"R-001"})

    def test_any_failure_returns_integer_zero(self):
        with mock.patch.object(auto_merge.registry, "load_all",
                               side_effect=RuntimeError("registry down")):
            got = auto_merge.scan_new_cards()
        self.assertIs(type(got), int)
        self.assertEqual(got, 0)


class OutstandingAndLedgerTestCase(unittest.TestCase):
    def setUp(self):
        _clean()
        self.addCleanup(_clean)

    def test_outstanding_probe_failure_means_no_budget(self):
        with mock.patch.object(silent_merge, "pending_count",
                               side_effect=OSError("boom")):
            self.assertEqual(auto_merge._outstanding_auto(), auto_merge.MAX_OUTSTANDING)

    def test_outstanding_reads_pending_count(self):
        with mock.patch.object(silent_merge, "pending_count", return_value=2):
            self.assertEqual(auto_merge._outstanding_auto(), 2)

    def test_record_pair_final_keeps_existing_pairs(self):
        auto_merge.STATE_PATH.write_text(
            json.dumps({"scanned": ["R-001"], "suggested": ["R-001|R-002"]}),
            encoding="utf-8")
        auto_merge.record_pair_final("R-004", "R-003")
        st = _state()
        self.assertEqual(st["suggested"], ["R-001|R-002", "R-003|R-004"])
        self.assertEqual(st["scanned"], ["R-001"])   # untouched

    def test_record_pair_final_never_raises(self):
        with mock.patch.object(auto_merge, "_load_state", side_effect=RuntimeError):
            auto_merge.record_pair_final("R-001", "R-002")   # swallowed

    def test_state_write_failure_is_swallowed(self):
        with mock.patch.object(auto_merge.config, "ensure_state_dirs",
                               side_effect=OSError("read-only")):
            auto_merge._save_state({"scanned": ["R-001"]})   # no raise
        self.assertFalse(auto_merge.STATE_PATH.exists())

    def test_request_failure_is_none(self):
        a = Requirement(id="R-001", title="a", status=State.CARD_SENT.value)
        b = Requirement(id="R-002", title="b", status=State.CARD_SENT.value)
        with mock.patch.object(silent_merge, "request", side_effect=OSError("spawn")):
            self.assertIsNone(auto_merge._request_silent_check(a, b))
        with mock.patch.object(silent_merge, "request", return_value="SM-1"):
            self.assertEqual(auto_merge._request_silent_check(a, b), "SM-1")


class LinkedTestCase(unittest.TestCase):
    def _pair(self, **kw):
        a = Requirement(id="R-001", title="a", status=State.CARD_SENT.value,
                        **{k: v[0] for k, v in kw.items()})
        b = Requirement(id="R-002", title="b", status=State.CARD_SENT.value,
                        **{k: v[1] for k, v in kw.items()})
        return a, b

    def test_thread_key_match_links(self):
        a, b = self._pair(thread_key=("mail:x", "mail:x"))
        self.assertIs(auto_merge._linked(a, b), True)

    def test_thread_key_mismatch_or_absence_does_not_link(self):
        a, b = self._pair(thread_key=("mail:x", "mail:y"))
        self.assertIs(auto_merge._linked(a, b), False)
        a, b = self._pair(thread_key=(None, "mail:y"))
        self.assertIs(auto_merge._linked(a, b), False)
        a, b = self._pair()
        self.assertIs(auto_merge._linked(a, b), False)

    def test_thread_id_and_lineage_link_either_direction(self):
        a, b = self._pair(thread_id=("R-001", "R-001"))
        self.assertIs(auto_merge._linked(a, b), True)
        a, b = self._pair(improvement_of=(None, "R-001"))
        self.assertIs(auto_merge._linked(a, b), True)
        self.assertIs(auto_merge._linked(b, a), True)
        a, b = self._pair(split_from=("R-002", None))
        self.assertIs(auto_merge._linked(a, b), True)
        self.assertIs(auto_merge._linked(b, a), True)


class NearDupeThresholdTestCase(unittest.TestCase):
    """The two rules at their exact boundaries (match_corpus stubbed so the
    score and the strong-evidence count are chosen, not derived)."""

    def _judge(self, score, strong, who_a=None, who_b=None):
        a = Requirement(id="R-001", title="a", status=State.CARD_SENT.value,
                        sources=[{"who": who_a}] if who_a else [])
        b = Requirement(id="R-002", title="b", status=State.CARD_SENT.value,
                        sources=[{"who": who_b}] if who_b else [])
        matched = ["t%d" % i for i in range(strong)]
        with mock.patch.object(match_corpus, "score_pair",
                               return_value=(score, matched)), \
                mock.patch.object(match_corpus, "strong_evidence",
                                  side_effect=lambda m: list(m)):
            return auto_merge.is_near_dupe(a, b)

    # the minimums are spelled as literals on purpose: 3 / 2 strong tokens
    # ARE the §38 spec (one shared identifier must never carry a merge).
    def test_high_rule_fires_on_the_exact_threshold(self):
        dupe, matched, reason = self._judge(0.6, 3)
        self.assertEqual((dupe, reason), (True, "high"))
        self.assertEqual(len(matched), 3)

    def test_high_rule_needs_three_strong_tokens(self):
        self.assertEqual(self._judge(0.95, 2), (False, [], ""))

    def test_high_rule_beats_contact_rule_when_both_apply(self):
        dupe, _m, reason = self._judge(0.6, 3, who_a="Quinton", who_b="quinton")
        self.assertEqual((dupe, reason), (True, "high"))

    def test_contact_rule_fires_on_the_exact_threshold(self):
        dupe, _m, reason = self._judge(0.4, 2, who_a="Quinton", who_b="quinton")
        self.assertEqual((dupe, reason), (True, "contact"))

    def test_contact_rule_needs_two_strong_tokens(self):
        self.assertEqual(self._judge(0.5, 1, who_a="Quinton", who_b="Quinton"),
                         (False, [], ""))

    def test_contact_rule_needs_a_shared_non_owner_contact(self):
        self.assertEqual(self._judge(0.5, 2, who_a="Quinton", who_b="Alice"),
                         (False, [], ""))
        self.assertEqual(self._judge(0.5, 2, who_a="zelin", who_b="Zelin"),
                         (False, [], ""))
        self.assertEqual(self._judge(0.5, 2), (False, [], ""))

    def test_below_both_scores_never_fires(self):
        self.assertEqual(self._judge(0.39, 5, who_a="Quinton", who_b="Quinton"),
                         (False, [], ""))


if __name__ == "__main__":
    unittest.main()
