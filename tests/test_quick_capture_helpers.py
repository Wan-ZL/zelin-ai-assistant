"""quick_capture — the helpers split out of capture / triage / apply_triage /
apply_result in P3b (CONTRACT §13 / §37 / §38 / §40 / §44 / §45).

Characterization net: the LLM ask (non-zero rc, crash, bare proc), the
capture fallback (typed vs media text), decision validity, the inventory line
tails, the relates_to canonicalisation + sealed hit, the dead-end fall-through
that still logs relates_to_miss, CORROBORATE blocking on resolved targets,
fold promotion vs §45 block, low-confidence status reset, the silent
pre-filing fold's TOCTOU guard / urgent promotion / executing briefing, the
separate_from pair ledger, and every apply_result reply/shape helper.
"""
import unittest
from types import SimpleNamespace
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first
from tests import store2_testkit

from act.lib import analytics, provenance, quick_capture as qc, registry
from act.lib.registry import Requirement, State


class _Proc:
    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.returncode = returncode


class LlmAskTestCase(unittest.TestCase):
    def test_ask_llm_outcomes(self):
        self.assertEqual(qc._ask_llm(lambda p: _Proc('{"action": "ignore"}'), "p"), {"action": "ignore"})
        self.assertIsNone(qc._ask_llm(lambda p: _Proc('{"action": "ignore"}', returncode=1), "p"))
        self.assertIsNone(qc._ask_llm(lambda p: _Proc(None), "p"))

        def boom(_p):
            raise RuntimeError("down")

        self.assertIsNone(qc._ask_llm(boom, "p"))
        self.assertEqual(qc._ask_llm(lambda p: SimpleNamespace(stdout='{"a": 1}'), "p"), {"a": 1})

    def test_valid_decision(self):
        self.assertTrue(qc._valid_decision({"action": "relates_to"}))
        self.assertFalse(qc._valid_decision({"action": "dance"}))
        self.assertFalse(qc._valid_decision(["action"]))
        self.assertFalse(qc._valid_decision(None))

    def test_capture_fallback(self):
        media = "Read these images: /tmp/a.png"
        data = qc._capture_fallback(media, "my words")
        self.assertEqual((data["title"], data["_text"]), ("my words", "my words"))
        self.assertEqual(data["plan"], ["my words", media])
        same = qc._capture_fallback("text", "  text ")
        self.assertEqual(same["title"], "text")
        self.assertNotIn("_text", same)
        none = qc._capture_fallback("text", None)
        self.assertTrue(none["_fallback"])

    def test_capture_and_triage_fallbacks_end_to_end(self):
        cfg = SimpleNamespace(owner_name="Z")
        with mock.patch.object(qc, "build_capture_prompt", return_value="p"), \
                mock.patch.object(qc, "build_triage_prompt", return_value="p"):
            out = qc.capture("hello", cfg, extractor=lambda p: _Proc("not json"))
            self.assertEqual((out["action"], out["_text"], out["_typed"]), ("new_proposal", "hello", "hello"))
            out = qc.capture("hello", cfg, extractor=lambda p: _Proc('{"action": "ignore"}'), typed_text="t")
            self.assertEqual((out["action"], out["_typed"]), ("ignore", "t"))
            self.assertEqual(qc.triage("d", cfg, extractor=lambda p: _Proc("", 2)),
                             {"action": "new_proposal", "_fallback": True})
            self.assertEqual(qc.triage("d", cfg, extractor=lambda p: _Proc('{"action": "ignore"}')),
                             {"action": "ignore"})


class InventoryLineTestCase(unittest.TestCase):
    def test_display_tail_and_line(self):
        r = Requirement(id="P-1", title="https://x.y/z", status="card_sent", improvement_of="P-0")
        self.assertTrue(qc._display_tail(r).startswith(" | 显示名: "))
        r2 = Requirement(id="P-2", title="plain", status="detected", display_title="plain")
        self.assertEqual(qc._display_tail(r2), "")
        with mock.patch.object(qc.match_corpus, "derive_aliases", return_value=["k1", "k2"]):
            line = qc._inventory_line(r, {}, None)
        self.assertTrue(line.startswith("P-1 | card_sent | https://x.y/z（P-0 的后续） | 显示名: "))
        self.assertTrue(line.endswith(" | 关键词: k1 k2"))
        with mock.patch.object(qc.match_corpus, "derive_aliases", return_value=[]):
            self.assertEqual(qc._inventory_line(r2, {}, None), "P-2 | detected | plain")


class TriageHelpersTestCase(unittest.TestCase):
    def setUp(self):
        store2_testkit.use_backend(self, "yaml")
        self.events = []
        patcher = mock.patch.object(analytics, "log_event",
                                    side_effect=lambda ev, **kw: self.events.append((ev, kw)))
        patcher.start()
        self.addCleanup(patcher.stop)

    def _card(self, rid, title, status, **kw):
        req = Requirement(id=rid, title=title, status=status, **kw)
        registry.save(req)
        return req

    def test_canonical_hit(self):
        self.assertEqual(qc._canonical_hit(""), (None, False))
        self.assertEqual(qc._canonical_hit("P-404"), (None, False))
        self._card("P-1", "primary", State.CARD_SENT.value)
        self._card("P-2", "dup", State.MERGED.value, merged_into="P-1")
        target, rejected = qc._canonical_hit("P-2")
        self.assertEqual((target.id, rejected), ("P-1", False))
        self._card("P-3", "sealed", State.TRASHED.value)
        self.assertEqual(qc._canonical_hit("P-3"), (None, True))
        self.assertEqual(self.events[-1], ("radar_triage", {"action": "relates_to_rejected", "req": "P-3"}))

    def test_dead_end_falls_through_and_logs_miss(self):
        self._card("P-10", "done", State.DELIVERED.value)
        cand = Requirement(id="", title="done")
        with mock.patch.object(registry, "reraise_or_followup", return_value=(None, None)):
            out = qc._triage_relates_to({"req": "P-10"}, cand, None, True)
        self.assertIsNone(out)
        self.assertIn(("radar_triage", {"action": "relates_to_miss", "req": "P-10"}), self.events)
        self.assertIsNone(qc._triage_relates_to({"req": ""}, cand, None, True))
        self.assertIn(("radar_triage", {"action": "relates_to_miss", "req": None}), self.events)

    def test_resolved_corroborate_blocked_and_fold_when_no_action(self):
        target = self._card("P-20", "done", State.DELIVERED.value)
        cand = Requirement(id="", title="done", sources=[{"channel": "screen", "date": "d", "quote": "q"}])
        self.assertEqual(qc._relate_to_resolved({}, cand, target, "n", provenance.CORROBORATE, False),
                         ("ignored", None))
        self.assertEqual(self.events[-1][1]["stage"], "filing")
        kind, saved = qc._relate_to_resolved({"needs_action": False}, cand, target, "note", None, True)
        self.assertEqual((kind, saved.id, saved.status), ("folded", "P-20", "delivered"))
        self.assertIn("[radar] note", registry.load("P-20").notes)

    def test_fold_into_open_promotion_matrix(self):
        target = self._card("P-30", "debt", State.DETECTED.value)
        cand = Requirement(id="", title="debt", status=State.CARD_SENT.value)
        kind, saved = qc._fold_into_open({}, cand, target, "n", None, False)
        self.assertEqual((kind, registry.load("P-30").status), ("folded", "detected"))
        self.assertEqual(self.events[-2][1]["stage"], "fold_promotion")
        kind, saved = qc._fold_into_open({}, cand, target, "n", None, True)
        self.assertEqual(registry.load("P-30").status, "card_sent")
        other = self._card("P-31", "other", State.DETECTED.value)
        quiet = Requirement(id="", title="other", status=State.DETECTED.value)
        qc._fold_into_open({"needs_action": "no"}, quiet, other, "n", None, True)
        self.assertEqual(registry.load("P-31").status, "detected")

    def test_triage_note_and_low_confidence(self):
        req = Requirement(id="", title="T", summary="S")
        self.assertEqual(qc._triage_note({"note": " n "}, req), "n")
        self.assertEqual(qc._triage_note({"note": ""}, req), "S")
        self.assertEqual(qc._triage_note({}, Requirement(id="", title="T")), "T")
        req = Requirement(id="", title="x", status=State.CARD_SENT.value)
        self.assertTrue(qc._apply_low_confidence({"confidence": "HIGH"}, req, True))
        self.assertEqual(req.status, "card_sent")
        self.assertFalse(qc._apply_low_confidence({"confidence": " Low "}, req, True))
        self.assertEqual(req.status, "detected")
        self.assertFalse(qc._apply_low_confidence({"confidence": "low"}, req, False))

    def test_silent_fold_helpers(self):
        req = Requirement(id="", title="T")
        self.assertEqual(qc._silent_note(req), "T")
        req._silent_brief = "无新增信息"
        self.assertEqual(qc._silent_note(req), "T")
        req._silent_brief = "多了截止日"
        self.assertEqual(qc._silent_note(req), "T（多了截止日）")
        target = self._card("P-40", "T", State.DETECTED.value)
        qc._promote_if_urgent(target, Requirement(id="", title="T", status=State.DETECTED.value))
        self.assertEqual(registry.load("P-40").status, "detected")
        qc._promote_if_urgent(target, Requirement(id="", title="T", status=State.CARD_SENT.value))
        self.assertEqual(registry.load("P-40").status, "card_sent")
        running = self._card("P-41", "R", State.EXECUTING.value)
        with mock.patch("act.lib.silent_merge.queue_briefing") as qb:
            qc._brief_if_executing(running, "note")
            qc._brief_if_executing(target, "note")
        qb.assert_called_once_with(running, "新信息并入：note（无需行动）")

    def test_pre_filing_fold_toctou_and_success(self):
        req = Requirement(id="", title="dup ask", status=State.CARD_SENT.value)
        with mock.patch.object(qc, "_silent_fold_target", return_value=None):
            self.assertIsNone(qc._pre_filing_fold(req, None))
        gone = Requirement(id="P-50", title="gone")
        with mock.patch.object(qc, "_silent_fold_target", return_value=gone):
            self.assertIsNone(qc._pre_filing_fold(req, None))       # target vanished
        trashed = self._card("P-51", "trashed", State.TRASHED.value)
        with mock.patch.object(qc, "_silent_fold_target", return_value=trashed):
            self.assertIsNone(qc._pre_filing_fold(req, None))       # no longer open
        open_card = self._card("P-52", "open", State.DETECTED.value)
        with mock.patch.object(qc, "_silent_fold_target", return_value=open_card):
            kind, saved = qc._pre_filing_fold(req, None)
        self.assertEqual((kind, saved.id, registry.load("P-52").status), ("folded", "P-52", "card_sent"))
        self.assertEqual(self.events[-1], ("silent_merge", {"primary": "P-52", "secondary": None,
                                                            "outcome": "pre_filing_fold"}))

    def test_file_candidate_ledger_and_receipt(self):
        req = Requirement(id="", title="fresh", status=State.CARD_SENT.value)
        with mock.patch("act.lib.auto_merge.record_pair_final") as rpf:
            kind, saved = qc._file_candidate(req, True, True, "P-9")
        self.assertEqual(kind, "proposed")
        rpf.assert_called_once_with(saved.id, "P-9")
        with mock.patch("act.lib.auto_merge.record_pair_final", side_effect=OSError("ledger")):
            kind, _ = qc._file_candidate(Requirement(id="", title="other"), True, True, "P-9")
        self.assertEqual(kind, "proposed")
        with mock.patch.object(registry, "merge_or_new_with_kind", return_value=("folded", saved)), \
                mock.patch("act.lib.fold_receipts.record") as rec:
            self.assertEqual(qc._file_candidate(req, True, True, None), ("proposed", saved))
        rec.assert_called_once_with(saved.id, "radar", "fresh")

    def test_triage_early_exit_table(self):
        req = Requirement(id="", title="x")
        self.assertEqual(qc._triage_early_exit({"action": "ignore"}, req, None, True), ("ignored", None))
        self.assertIsNone(qc._triage_early_exit({"action": "new_proposal"}, req, None, True))
        self.assertIsNone(qc._triage_early_exit({}, req, None, True))
        with mock.patch.object(qc, "_triage_relates_to", return_value=("folded", "T")) as tr:
            self.assertEqual(qc._triage_early_exit({"action": "relates_to"}, req, "g", False), ("folded", "T"))
        tr.assert_called_once()

    def test_apply_triage_none_decision_files_a_card(self):
        kind, saved = qc.apply_triage(None, Requirement(id="", title="nothing decided"),
                                      cfg=SimpleNamespace())
        self.assertEqual((kind, saved.status), ("proposed", "detected"))
        with mock.patch.object(qc, "_silent_fold_target") as sft:
            qc.apply_triage({"_fallback": True}, Requirement(id="", title="llm down"),
                            cfg=SimpleNamespace())
        sft.assert_not_called()


class ApplyResultHelpersTestCase(unittest.TestCase):
    def setUp(self):
        store2_testkit.use_backend(self, "yaml")

    def test_tele_text_and_ignore_reason(self):
        with mock.patch.object(analytics, "content_gate", return_value=False):
            self.assertIsNone(qc._tele_text({"_typed": "t"}, None))
        with mock.patch.object(analytics, "content_gate", return_value=True), \
                mock.patch.object(analytics, "clip_content", side_effect=lambda v: f"<{v}>"):
            self.assertEqual(qc._tele_text({"_typed": "t", "_text": "x"}, None), "<t>")
            self.assertEqual(qc._tele_text({"_text": "x"}, None), "<x>")
        self.assertEqual(qc._ignore_reason({"reason": " why "}), "why")
        self.assertEqual(qc._ignore_reason({}), "看起来不需要行动")

    def test_proposal_field_helpers(self):
        self.assertEqual(qc._capture_quote({"_text": " q ", "summary": "s"}), "q")
        self.assertEqual(qc._capture_quote({"summary": "s"}), "s")
        self.assertEqual(qc._capture_quote({}), "")
        self.assertEqual(qc._capture_title({}, ""), "quick capture")
        self.assertEqual(qc._capture_title({}, "q"), "q")
        self.assertEqual(qc._capture_title({"title": "t", "summary": "s"}, "q"), "t")
        self.assertTrue(qc._is_low_conf({"confidence": " LOW "}))
        self.assertFalse(qc._is_low_conf({"confidence": None}))
        self.assertEqual(qc._proposal_tier({"tier": "t2"}), "T2")
        self.assertEqual(qc._proposal_tier({"tier": "T9"}), "T1")
        self.assertEqual(qc._proposal_target_kind({"target_kind": "NEW"}), "new")
        self.assertIsNone(qc._proposal_target_kind({"target_kind": "maybe"}))
        self.assertEqual(qc._proposal_target_repo({"target_repo": " /r "}), "/r")
        self.assertIsNone(qc._proposal_target_repo({"target_repo": "  "}))
        self.assertIsNone(qc._proposal_target_repo({"target_repo": ["/r"]}))
        self.assertEqual(qc._proposal_dod({"definition_of_done": [" a ", "", "b", "c", "d"]}), ["a", "b", "c"])
        self.assertIsNone(qc._proposal_dod({"definition_of_done": ["", " "]}))
        self.assertIsNone(qc._proposal_dod({"definition_of_done": "a"}))
        self.assertEqual(qc._proposal_notes({"_fallback": True, "_relates_to_miss": "R-9"}),
                         "from Slack self-DM quick capture (quick-capture LLM failed, needs manual)"
                         " (relates_to miss: R-9)")
        self.assertEqual(qc._proposal_summary({"summary": ""}, "t" * 130), "t" * 120)
        self.assertEqual(qc._proposal_type({"type": " "}), "other")
        self.assertEqual(qc._proposal_type({"type": "code"}), "code")
        self.assertEqual(qc._delivery_mode({"delivery_mode": "CHAT"}), "chat")
        self.assertEqual(qc._delivery_mode({}), "repo")

    def test_new_proposal_req_and_reply(self):
        res = {"title": "T", "summary": "S", "type": "code", "tier": "T0", "plan": None,
               "cost_estimate_usd": "3", "target_repo": "/r", "target_kind": "existing",
               "delivery_mode": "chat", "display_title": "看板名", "confidence": "low"}
        req = qc._new_proposal_req(res, "q", "T", True)
        self.assertEqual((req.status, req.plan, req.cost_estimate_usd, req.target_repo, req.target_kind,
                          req.delivery_mode, req.display_title, req.sources[0]["quote"]),
                         ("detected", ["T"], 3.0, "/r", "existing", "chat", "看板名", "q"))
        saved = Requirement(id=req.id, title="T", summary="", status="detected")
        self.assertEqual(qc._proposal_reply(saved, req, True),
                         f"已记入潜在任务 {req.id}：T（不紧急，先存着不打扰）/ parked in backlog {req.id}")
        merged = Requirement(id="P-0", title="parent", summary="ps")
        self.assertEqual(qc._proposal_reply(merged, req, False), "已并入已有条目 P-0（parent），提及次数 +1")
        child = Requirement(id="P-9", title="c", summary="cs", improvement_of="P-0")
        self.assertEqual(qc._proposal_reply(child, req, False), "已建卡 P-9：cs（进待审批）")
        self.assertEqual(qc._saved_label(Requirement(id="x", title="only")), "only")

    def test_relates_to_helpers(self):
        self.assertEqual(qc._canonical_capture_target("", None), (None, False))
        self.assertEqual(qc._canonical_capture_target("P-404", None), (None, False))
        registry.save(Requirement(id="P-60", title="sealed", status=State.ARCHIVED.value,
                                  prev_status="delivered"))
        with mock.patch.object(analytics, "log_event"):
            self.assertEqual(qc._canonical_capture_target("P-60", None), (None, True))
        registry.save(Requirement(id="P-61", title="open", status=State.CARD_SENT.value))
        req, sealed = qc._canonical_capture_target("P-61", None)
        self.assertEqual((req.id, sealed), ("P-61", False))
        self.assertEqual(qc._relates_note({"note": " n ", "_text": "t"}), "n")
        self.assertEqual(qc._relates_note({"_text": " t "}), "t")
        self.assertEqual(qc._miss_text({"_typed": "", "note": "", "_text": "x"}), "x")
        self.assertEqual(qc._miss_text({"_typed": "ty", "note": "n"}), "ty")
        self.assertEqual(qc._field_text({"k": None}, "k"), "")

    def test_relates_to_miss_replies(self):
        with mock.patch.object(analytics, "log_event"):
            kind, saved, reply = qc._relates_to_miss({"_typed": "remember this"}, "", False, None)
            self.assertEqual(kind, "proposed")
            self.assertTrue(reply.startswith("没找到条目 ?；为了不丢先按新卡记下——"))
            self.assertIn("relates_to miss: ?", saved.notes)
            kind, saved, reply = qc._relates_to_miss({"note": "again"}, "P-60", True, None)
            self.assertTrue(reply.startswith("P-60 已封存（拒绝/回收站/归档），重述按新卡处理——"))

    def test_capture_child_and_resolved_reply(self):
        parent = Requirement(id="P-70", title="PT", type="code", tier="T2")
        child = qc._capture_child(parent, "")
        self.assertEqual((child.title, child.type, child.tier, child.sources[0]["quote"]),
                         ("PT", "code", "T2", "PT"))
        self.assertEqual(qc._capture_child(parent, "n" * 100).title, "n" * 80)
        saved = Requirement(id="P-71", title="c")
        self.assertEqual(qc._resolved_reply("reraised", parent, saved)[0], "reraised")
        self.assertIn("P-71", qc._resolved_reply("follow_up", parent, saved)[2])
        self.assertEqual(qc._resolved_reply("folded", parent, saved)[0], "folded")
        self.assertIn("open follow-up P-71", qc._resolved_reply("other", parent, saved)[2])

    def test_fold_note_into_phrases(self):
        with mock.patch.object(analytics, "log_event"):
            for status, phrase in qc._FOLD_PHRASES.items():
                req = Requirement(id=f"P-8{status[:2]}", title="t", status=status)
                registry.save(req)
                kind, saved, reply = qc._fold_note_into(req, "note", None, None)
                self.assertEqual((kind, reply), ("folded", f"已关联 {req.id}：{phrase}"))
                self.assertIn("[quick] note", registry.load(req.id).notes)
            odd = Requirement(id="P-89", title="t", status="raising")
            registry.save(odd)
            self.assertEqual(qc._fold_note_into(odd, "n", None, None)[2], "已关联 P-89：状态 raising，备注已追加")
            debt = Requirement(id="P-90", title="t", status=State.DETECTED.value)
            registry.save(debt)
            with mock.patch.object(qc.analyze, "expand_debt") as ed:
                kind, saved, reply = qc._fold_note_into(debt, "n", "cfg", None)
            ed.assert_called_once_with(debt, "cfg")
            self.assertEqual(reply, "已关联 P-90，已提案（扩成完整建议，进待审批）")


class NewProposalFoldReceiptTestCase(unittest.TestCase):
    def test_folded_outcome_records_a_board_receipt(self):
        store2_testkit.use_backend(self, "yaml")
        existing = Requirement(id="P-95", title="same ask", status=State.CARD_SENT.value)
        registry.save(existing)
        with mock.patch.object(registry, "merge_or_new_with_kind", return_value=("folded", existing)), \
                mock.patch("act.lib.fold_receipts.record") as rec, \
                mock.patch.object(analytics, "log_event"):
            kind, saved, reply = qc._apply_new_proposal({"title": "same ask", "_text": "q"}, None)
        self.assertEqual((kind, saved.id), ("folded", "P-95"))
        rec.assert_called_once_with("P-95", "quick", "q")
        self.assertTrue(reply.startswith("已并入已有条目 P-95"))


if __name__ == "__main__":
    unittest.main()
