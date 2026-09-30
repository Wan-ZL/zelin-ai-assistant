"""registry — the re-raise / follow-up / merge_or_new decision matrix
(卡片生命周期 §3.3–§3.5, §45 cap, §50 stamps, §60 ids, §78 提案车道退役).

Characterization net for the P3b split of ``reraise_or_followup`` and
``merge_or_new_with_kind``: every exit (dead-end, live-canonical fold, pure
restatement fold, open-follow-up fold, re-raise, follow-up child), the
``actionable`` override vs ``_carries_increment``, the §45 cap on both births,
thread_key vs title parent selection, the increment child's inherited fields,
and the brand-new card's birth status table.

§78（issue #447，owner 决策 D80）：每一次出生/翻回的落点从 ``card_sent``（提案）
改成 ``detected``（潜在任务）。**§45 的 LIMITED 天花板没有被删掉，它换了表达面**：
退役前「压进 detected 而不是 card_sent」就是那一刀，两列合一之后那一刀改由
``quiet_birth``（D80.7：落潜在任务但不响通知）承担——所以 ``cap_detected`` 的判例
在下面改钉 ``quiet_birth``，回声环那一刀仍然看得见、仍然钉得住。

**两条路，两把不同的尺**（评审 R-229 抓到的一刀切）：回锅 / follow-up 子卡只看
``cap_detected``（§45 来源天花板——main 上就是 ``DETECTED if cap_detected else
CARD_SENT``）；增量子卡看 ``high_confidence`` **或**候选自带的章（main 上是
``CARD_SENT if high_confidence else DETECTED``）。把候选那枚记「生产者紧急度」的
章并进前一条，回锅就会从此不响。端到端的打扰面判例在
tests/test_quiet_birth_mirrors_the_retired_lane.py。
"""
import unittest

from tests import TMP_HOME  # noqa: F401 - sandbox env first
from tests import store2_testkit

from act.lib import registry
from act.lib.registry import Requirement, State


def _card(rid, title, status, **kw):
    req = Requirement(id=rid, title=title, status=status, **kw)
    registry.save(req)
    return req


class ReraiseMatrixTestCase(unittest.TestCase):
    def setUp(self):
        store2_testkit.use_backend(self, "yaml")

    def test_dead_end_states_return_none(self):
        for st in (State.REJECTED.value, State.TRASHED.value, State.ARCHIVED.value):
            parent = _card(f"P-{st[:3]}", "dead", st)
            self.assertEqual(registry.reraise_or_followup(parent, Requirement(id="", title="x"),
                                                          same_task=True), (None, None))

    def test_merged_duplicate_hops_to_live_primary_and_folds(self):
        primary = _card("P-100", "primary task", State.EXECUTING.value)
        dup = _card("P-101", "primary task", State.MERGED.value, merged_into="P-100")
        cand = Requirement(id="", title="primary task", summary="more",
                           sources=[{"channel": "slack", "date": "2026-09-02", "quote": "q"}])
        kind, saved = registry.reraise_or_followup(dup, cand, same_task=True, note="progress")
        self.assertEqual((kind, saved.id, saved.status), ("folded", "P-100", "executing"))
        self.assertEqual(saved.repeated_mentions, 2)
        self.assertIn("[radar] progress", saved.notes)
        self.assertEqual(saved.thread_id, "P-100")
        self.assertEqual(saved.origin_trust, "external")
        self.assertEqual(registry.load("P-100").repeated_mentions, 2)
        self.assertIsNotNone(primary)

    def test_pure_restatement_of_resolved_bumps_by_one_without_flip(self):
        parent = _card("P-110", "done task", State.DELIVERED.value,
                       sources=[{"channel": "quick", "date": "d", "quote": "a"}])
        cand = Requirement(id="", title="done task",
                           sources=[{"channel": "slack", "date": "d", "quote": "b"},
                                    {"channel": "slack", "date": "d", "quote": "c"}])
        kind, saved = registry.reraise_or_followup(parent, cand, same_task=True, actionable=False)
        self.assertEqual((kind, saved.status, saved.repeated_mentions), ("folded", "delivered", 2))
        self.assertEqual(len(saved.sources), 3)
        self.assertEqual(registry.load("P-110").repeated_mentions, 2)

    def test_deterministic_actionable_uses_carries_increment(self):
        parent = _card("P-120", "shipped", State.DELIVERED.value)
        plain = Requirement(id="", title="shipped")
        kind, _ = registry.reraise_or_followup(parent, plain, same_task=True)
        self.assertEqual(kind, "folded")
        parent = registry.load("P-120")
        harder = Requirement(id="", title="shipped", hardness="hard")
        kind, saved = registry.reraise_or_followup(parent, harder, same_task=True, note="again")
        self.assertEqual((kind, saved.status), ("reraised", "detected"))  # §78 回锅落潜在任务
        self.assertFalse(saved.quiet_birth)     # FULL 档：回锅照常通知
        self.assertIn("[re-raised] again", saved.notes)
        self.assertIn("· 新增:again", saved.summary)
        self.assertEqual(saved.execution["reraised_note"], "again")

    def test_open_follow_up_absorbs_the_second_source(self):
        parent = _card("P-130", "closed thread", State.DELIVERED.value)
        child = _card("P-131", "follow", State.DETECTED.value, improvement_of="P-130")
        cand = Requirement(id="", title="closed thread",
                           sources=[{"channel": "gmail", "date": "d", "quote": "z"}])
        kind, saved = registry.reraise_or_followup(parent, cand, same_task=True, actionable=True,
                                                   note="second source")
        self.assertEqual((kind, saved.id), ("folded", "P-131"))
        self.assertEqual(saved.repeated_mentions, 2)
        self.assertIn("second source", saved.notes)
        self.assertIsNotNone(child)

    def test_different_task_opens_follow_up_with_lineage(self):
        parent = _card("P-140", "thread root", State.DELIVERED.value, type="code", tier="T2",
                       thread_key="gmail:abc", thread_id="P-140")
        cand = Requirement(id="", title="", type="", tier="", summary="", hardness="",
                           sources=[{"channel": "gmail", "date": "d", "quote": "q"}])
        kind, child = registry.reraise_or_followup(parent, cand, same_task=False,
                                                   actionable=True, note="new ask")
        self.assertEqual(kind, "follow_up")
        self.assertEqual((child.title, child.type, child.tier, child.hardness),
                         ("new ask", "code", "T2", "soft"))
        self.assertEqual((child.improvement_of, child.thread_id, child.thread_key),
                         ("P-140", "P-140", "gmail:abc"))
        self.assertEqual(child.summary, "既往卡 P-140 的后续：new ask")
        self.assertEqual(child.notes, "[radar] new ask")
        self.assertEqual(child.status, "detected")      # §78：子卡也落潜在任务
        self.assertEqual(child.plan, [])
        self.assertTrue(child.id.startswith("P-"))

    def test_cap_detected_quiets_both_births(self):
        """§45 LIMITED 天花板 + §78 D80.7：两种出生都安静，但都落潜在任务。

        退役前这条判例钉的是「LIMITED 把两种出生压进 detected 而不是 card_sent」。
        两列合一之后状态上已经没有差别，天花板若不换面就会**无声消失**——回声环
        那一刀（§45）正是靠它存在的。所以判据平移到 ``quiet_birth``：LIMITED 出生
        照样落潜在任务，但盖上「不响通知」的出生事实。
        """
        parent = _card("P-150", "capped", State.DELIVERED.value)
        cand = Requirement(id="", title="capped")
        kind, saved = registry.reraise_or_followup(parent, cand, same_task=True, actionable=True,
                                                   cap_detected=True)
        self.assertEqual((kind, saved.status), ("reraised", "detected"))
        self.assertTrue(saved.quiet_birth)          # 天花板 = 安静，不是换列
        parent2 = _card("P-151", "other root", State.DELIVERED.value)
        kind, child = registry.reraise_or_followup(parent2, Requirement(id="", title="diff"),
                                                   same_task=False, actionable=True,
                                                   cap_detected=True)
        self.assertEqual((kind, child.status), ("follow_up", "detected"))
        self.assertTrue(child.quiet_birth)
        # FULL 档（cap_detected=False）：同一条路落同一列，但**不**安静
        parent3 = _card("P-152", "loud root", State.DELIVERED.value)
        _kind, loud = registry.reraise_or_followup(parent3, Requirement(id="", title="loud diff"),
                                                   same_task=False, actionable=True,
                                                   cap_detected=False)
        self.assertEqual(loud.status, "detected")
        self.assertFalse(loud.quiet_birth)
        # 出生态本身对两档一视同仁（§78）
        self.assertEqual(registry._birth_state(True), "detected")
        self.assertEqual(registry._birth_state(False), "detected")

    def test_a_quiet_candidate_never_silences_the_reraise_or_the_follow_up(self):
        """候选自带的 ``quiet_birth`` 管不到这两条路——判据只有 ``cap_detected``。

        上面那条用的是裸 ``Requirement(id="", title=…)``，它永远不带
        ``quiet_birth``——真实的 radar 候选**带**：``radar._file_item`` 按
        `hc = FULL and hard and deadline and urgent` 给非高置信的候选盖章，
        那枚章记的是**生产者的紧急度**（退役前它挑的是「新卡出生在哪一列」）。
        main 上回锅与 follow-up 的落点是 ``DETECTED if cap_detected else
        CARD_SENT``，紧急度从来不参与；把候选那枚章并进来，一条 FULL 来源的
        不紧急重述就会让「回锅」从此不响——没有报错、卡还在、只是不说话。
        """
        quiet_cand = Requirement(id="", title="capped", quiet_birth=True)
        parent = _card("P-153", "capped", State.DELIVERED.value)
        kind, saved = registry.reraise_or_followup(parent, quiet_cand, same_task=True,
                                                   actionable=True, cap_detected=False)
        self.assertEqual((kind, saved.status), ("reraised", "detected"))
        self.assertFalse(saved.quiet_birth)          # FULL 来源 = 照响
        parent2 = _card("P-154", "other capped root", State.DELIVERED.value)
        _kind, child = registry.reraise_or_followup(
            parent2, Requirement(id="", title="different ask", quiet_birth=True),
            same_task=False, actionable=True, cap_detected=False)
        self.assertFalse(child.quiet_birth)
        # 反向：§45 天花板仍然扣得住，哪怕候选一声不吭
        parent3 = _card("P-155", "still capped", State.DELIVERED.value)
        _kind, capped = registry.reraise_or_followup(
            parent3, Requirement(id="", title="still capped"),
            same_task=True, actionable=True, cap_detected=True)
        self.assertTrue(capped.quiet_birth)

    def test_a_quiet_candidate_does_silence_the_increment_child(self):
        """同一枚候选章在**增量子卡**上是对的：那是一次新出生。

        main 上子卡的落点 = ``CARD_SENT if high_confidence else DETECTED``，
        所以两个判据都得看：不传 ``high_confidence`` 的调用方（Gmail/Slack/
        claude-sessions）恒安静，而非 FULL 来源在 ``apply_triage`` 入口盖的那
        枚章也得搭车过来（``_reconcile_open`` 逐字照 main 不收 ``cap_detected``）。
        """
        _card("P-156", "open parent card", State.DETECTED.value, hardness="soft")
        loud = Requirement(id="", title="open parent card", hardness="hard")
        _kind, child = registry.merge_or_new_with_kind(loud, high_confidence=True)
        self.assertEqual(child.improvement_of, "P-156")
        self.assertFalse(child.quiet_birth)
        # ① 调用方没说高置信 -> main 上落 detected -> 安静
        _card("P-157", "second open parent", State.DETECTED.value, hardness="soft")
        _kind, quiet_by_caller = registry.merge_or_new_with_kind(
            Requirement(id="", title="second open parent", hardness="hard"))
        self.assertTrue(quiet_by_caller.quiet_birth)
        # ② 候选自己盖了章（§45 非 FULL 来源）-> 子卡跟着安静
        _card("P-158", "third open parent", State.DETECTED.value, hardness="soft")
        _kind, quiet_by_cand = registry.merge_or_new_with_kind(
            Requirement(id="", title="third open parent", hardness="hard", quiet_birth=True),
            high_confidence=True)
        self.assertTrue(quiet_by_cand.quiet_birth)

    def test_reraise_without_note_leaves_text_alone(self):
        parent = _card("P-160", "quiet", State.DELIVERED.value, notes="", summary="s",
                       execution={"session_id": "sid-1", "done": True, "accepted_at": "t"})
        kind, saved = registry.reraise_or_followup(parent, Requirement(id="", title="quiet"),
                                                   same_task=True, actionable=True, note="")
        self.assertEqual(kind, "reraised")
        self.assertEqual((saved.notes, saved.summary), ("", "s"))
        ex = saved.execution
        self.assertEqual((ex["reraised_session_id"], ex["reraised_note"], ex["accepted_at"]),
                         ("sid-1", "", "t"))
        self.assertNotIn("session_id", ex)
        self.assertNotIn("done", ex)

    def test_reraised_execution_without_session(self):
        ex = registry._reraised_execution(None, "n")
        self.assertEqual(ex["reraised_note"], "n")
        self.assertNotIn("reraised_session_id", ex)
        self.assertTrue(ex["reraised_at"])


class CarriesIncrementTestCase(unittest.TestCase):
    def test_each_rule(self):
        parent = Requirement(id="p", deadline="2026-09-10", cost_estimate_usd=None, hardness="soft")
        self.assertTrue(registry._earlier_deadline(parent, Requirement(id="n", deadline="2026-09-01")))
        self.assertFalse(registry._earlier_deadline(parent, Requirement(id="n", deadline="2026-09-10")))
        self.assertFalse(registry._earlier_deadline(parent, Requirement(id="n")))
        self.assertTrue(registry._earlier_deadline(Requirement(id="p"), Requirement(id="n", deadline="x")))
        self.assertTrue(registry._adds_cost(parent, Requirement(id="n", cost_estimate_usd=0.0)))
        self.assertFalse(registry._adds_cost(Requirement(id="p", cost_estimate_usd=1.0),
                                             Requirement(id="n", cost_estimate_usd=2.0)))
        self.assertTrue(registry._escalates(parent, Requirement(id="n", hardness="hard")))
        self.assertFalse(registry._escalates(Requirement(id="p", hardness="hard"),
                                             Requirement(id="n", hardness="hard")))
        self.assertTrue(registry._carries_increment(parent, Requirement(id="n", improvement_of="p")))
        self.assertFalse(registry._carries_increment(parent, Requirement(id="n")))
        self.assertIs(registry._carries_increment(parent, Requirement(id="n", deadline="2026-01-01")), True)


class MergeOrNewMatrixTestCase(unittest.TestCase):
    def setUp(self):
        store2_testkit.use_backend(self, "yaml")

    def test_thread_key_match_without_title_match_is_a_different_task(self):
        _card("P-200", "invoice for March", State.DELIVERED.value, thread_key="gmail:t1")
        cand = Requirement(id="", title="unrelated ask", sources=[{"gmail_thread_id": "t1",
                                                                  "channel": "gmail",
                                                                  "date": "d", "quote": "q"}])
        kind, saved = registry.merge_or_new_with_kind(cand)
        self.assertEqual(kind, "follow_up")
        self.assertEqual(saved.improvement_of, "P-200")
        self.assertEqual(saved.thread_key, "gmail:t1")

    def test_thread_key_is_derived_from_the_first_source(self):
        req = Requirement(id="", title="a", sources=[{"slack_thread_ts": "9.1"}, {"gmail_thread_id": "g"}])
        registry._ensure_thread_key(req)
        self.assertEqual(req.thread_key, "slack:9.1")
        keep = Requirement(id="", title="a", thread_key="keep", sources=[{"gmail_thread_id": "g"}])
        registry._ensure_thread_key(keep)
        self.assertEqual(keep.thread_key, "keep")
        none = Requirement(id="", title="a")
        registry._ensure_thread_key(none)
        self.assertIsNone(none.thread_key)

    def test_match_helpers(self):
        a = Requirement(id="A", title="alpha task here", status="detected", thread_key="k")
        b = Requirement(id="B", title="beta", status="trashed", thread_key="k")
        self.assertTrue(registry._matchable(a))
        # §78 add-only：退役的 card_sent 落单卡仍要参与匹配/去重，否则同一件事
        # 会在潜在任务列里再铸一张新卡（§44.4 轻状态铁律看状态不看列）
        self.assertTrue(registry._matchable(Requirement(id="E", title="straggler",
                                                        status="card_sent")))
        self.assertFalse(registry._matchable(b))
        self.assertFalse(registry._matchable(Requirement(id="c", status="merged_into:A")))
        self.assertTrue(registry._matchable(Requirement(id="d", status="merged")))
        self.assertEqual(registry._match_by_thread([b, a], Requirement(id="", title="x", thread_key="k")),
                         (a, False))
        self.assertEqual(registry._match_by_thread([a], Requirement(id="", title="Alpha Task Here",
                                                                    thread_key="k")), (a, True))
        self.assertEqual(registry._match_by_thread([a], Requirement(id="", title="x")), (None, False))
        self.assertEqual(registry._match_by_title([b, a], Requirement(id="", title="alpha task here")),
                         (a, True))
        self.assertEqual(registry._match_by_title([a], Requirement(id="", title="zzz")), (None, False))
        self.assertEqual(registry._match_parent([a], Requirement(id="", title="alpha task here",
                                                                 thread_key="other")), (a, True))

    def test_increment_child_inherits_from_parent(self):
        parent = _card("P-210", "parent title", State.DETECTED.value, type="comms", tier="T0",
                       hardness="soft", deadline="2026-12-01", plan=["p1"], thread_key="slack:1")
        cand = Requirement(id="", title="PARENT  title", type="", tier="", hardness="",
                           deadline="2026-10-01", plan=None, display_title="disp", notes="",
                           sources=[])
        kind, child = registry.merge_or_new_with_kind(cand, high_confidence=True)
        self.assertEqual(kind, "proposed")
        self.assertEqual((child.title, child.type, child.tier, child.hardness, child.deadline,
                          child.plan), ("PARENT  title", "comms", "T0", "soft", "2026-10-01", ["p1"]))
        self.assertEqual(registry._increment_fields(parent, Requirement(id="", title="")),
                         {"title": "parent title", "hardness": "soft", "deadline": "2026-12-01",
                          "plan": ["p1"]})
        # §78：high_confidence 的增量子卡也落潜在任务（退役前它是 card_sent）
        self.assertEqual((child.improvement_of, child.thread_id, child.thread_key, child.status,
                          child.display_title, child.notes),
                         ("P-210", "P-210", "slack:1", "detected", "disp", ""))
        # …而「退役前它是 card_sent」= 它会响，所以退役后必须**不**安静
        self.assertFalse(child.quiet_birth)
        # the open parent itself is not rewritten on the increment path
        self.assertIsNone(registry.load("P-210").thread_id)

    def test_open_restatement_stamps_and_bumps_by_new_rows(self):
        _card("P-220", "same ask", State.DETECTED.value,
              sources=[{"channel": "quick", "date": "d", "quote": "a"}])
        cand = Requirement(id="", title="same ask",
                           sources=[{"channel": "slack", "date": "d", "quote": "b"},
                                    {"channel": "slack", "date": "d", "quote": "c"}])
        kind, saved = registry.merge_or_new_with_kind(cand)
        self.assertEqual((kind, saved.id, saved.repeated_mentions), ("folded", "P-220", 3))
        self.assertEqual(saved.origin_trust, "external")

    def test_brand_new_birth_status_is_always_detected(self):
        """§78：出生状态表塌成一格——四种组合一律 detected。

        退役前这张表的唯一 card_sent 格是「high_confidence + 硬 deadline」，那是
        提案列唯一的自动入口（机器卡自己把自己送进 owner 的审批列）。车道退役后
        那一格没有落点，四种组合必须全部落潜在任务；把这四行留在这里就是钉住
        「没有任何隐藏的旁路还在铸 card_sent」。
        """
        for hardness, deadline, hc in (("hard", "d", True), ("hard", "d", False),
                                       ("soft", "d", True), ("hard", None, True)):
            self.assertEqual(
                registry._brand_new_status(
                    Requirement(id="", hardness=hardness, deadline=deadline), hc),
                "detected", msg=(hardness, deadline, hc))
        self.assertTrue(registry._needs_birth_status(Requirement(id="", status="")))
        self.assertTrue(registry._needs_birth_status(Requirement(id="", status="detected")))
        # 显式预设的状态不被出生表覆写（capture[run] 出生即 approved 靠这条）
        self.assertFalse(registry._needs_birth_status(Requirement(id="", status="approved")))
        # ……**除了**退役的 card_sent：铸卡漏斗是「没有任何生产者再写提案列」
        # （§78.1 第 1 条）的最后一道闸。仓库里的生产者都已改掉，但重放的 inbox
        # 文件 / 云同步补发的旧候选 / 照抄旧片段的新调用方还可能带着它进来——
        # 不在这里钳住，就会有**崭新**的卡出生在一条没有面的车道上。
        self.assertTrue(registry._needs_birth_status(Requirement(id="", status="card_sent")))

    def test_file_new_keeps_preset_status_and_normalises_mentions(self):
        # §78：出生表只在 ""/detected 上生效，显式预设的状态原样保留——今天的活
        # 例子是 capture[run] 的「出生即 approved」（§34 直跑框）
        kind, saved = registry.merge_or_new_with_kind(
            Requirement(id="", title="fresh run", status="approved", repeated_mentions=0))
        self.assertEqual((kind, saved.status, saved.repeated_mentions, saved.thread_id),
                         ("proposed", "approved", 1, saved.id))
        self.assertEqual(saved.origin_trust, "proposed")
        # 退役值是**唯一**的例外：带着 card_sent 进来的候选在漏斗里就被钳成
        # detected，没有任何路径能让一张**新**卡出生在退役车道上（§78.1 第 1 条）。
        # 盘上已有的 card_sent 卡不受影响——那是历史事实，由 §78.5 扫描搬运。
        _k, straggler = registry.merge_or_new_with_kind(
            Requirement(id="", title="fresh straggler", status="card_sent"))
        self.assertEqual(straggler.status, "detected")
        kept = registry.merge_or_new_with_kind(Requirement(id="P-777", title="explicit id"))[1]
        self.assertEqual((kept.id, kept.thread_id), ("P-777", "P-777"))

    def test_dead_end_parent_files_a_fresh_card(self):
        _card("P-230", "archived one", State.ARCHIVED.value, prev_status="delivered")
        merged = _card("P-231", "archived one", State.MERGED.value, merged_into="P-230")
        kind, saved = registry.merge_or_new_with_kind(Requirement(id="", title="archived one"))
        self.assertEqual(kind, "proposed")
        self.assertNotIn(saved.id, ("P-230", "P-231"))
        self.assertIsNotNone(merged)

    def test_merge_or_new_delegates(self):
        saved = registry.merge_or_new({"title": "via dict"}, high_confidence=True)
        self.assertEqual(saved.title, "via dict")
        self.assertEqual(saved.status, "detected")


class FoldHelpersTestCase(unittest.TestCase):
    def test_pick_sources_and_bump(self):
        cand = Requirement(id="c", sources=[{"a": 1}])
        self.assertEqual(registry._pick_sources(cand, None), [{"a": 1}])
        self.assertEqual(registry._pick_sources(cand, []), [])
        self.assertIsNone(registry._pick_sources(None, None))
        req = Requirement(id="r", repeated_mentions=None)
        registry._bump_mentions(req, 0)
        self.assertIsNone(req.repeated_mentions)
        registry._bump_mentions(req, 2)
        self.assertEqual(req.repeated_mentions, 3)

    def test_absorb_sources_dedupes_and_stamps(self):
        req = Requirement(id="r", sources=[{"channel": "quick", "date": "d", "quote": "a"}])
        registry._absorb_sources(req, [{"channel": "Quick", "date": "d", "quote": "A"},
                                       {"channel": "slack", "date": "d", "quote": "b"}])
        self.assertEqual((len(req.sources), req.repeated_mentions, req.origin_trust), (2, 2, "external"))
        registry._absorb_sources(req, None)
        self.assertEqual(req.repeated_mentions, 2)

    def test_fold_note_helpers(self):
        existing = registry.parse_fold_notes("[radar] a [@2026-01-01T00:00:00Z]\n[quick] b")
        self.assertTrue(registry._has_fold_note(existing, "radar", "a"))
        self.assertFalse(registry._has_fold_note(existing, "quick", "a"))
        self.assertTrue(registry._has_fold_note(existing, "quick", "b"))
        with unittest.mock.patch.object(registry, "_iso_now", return_value="2026-01-01T00:00:00Z"):
            self.assertEqual(registry._unique_fold_ts(existing), "2026-01-01T00:00:00Z#2")
            existing.append({"kind": "radar", "text": "c", "ts": "2026-01-01T00:00:00Z#2",
                             "split_into": None})
            self.assertEqual(registry._unique_fold_ts(existing), "2026-01-01T00:00:00Z#3")
        self.assertTrue(registry._splittable_fold_line("[radar] a [@t1]", "t1"))
        self.assertFalse(registry._splittable_fold_line("[radar] a [@t1] [已拆出 R-1]", "t1"))
        self.assertFalse(registry._splittable_fold_line("plain [@t1]", "t1"))
        self.assertFalse(registry._splittable_fold_line("[radar] a [@t2]", "t1"))
        self.assertEqual(registry._note_lines(Requirement(id="x", notes=None)), [""])

    def test_open_child_and_containment(self):
        self.assertTrue(registry._is_open_child(Requirement(id="c", improvement_of="p", status="detected")))
        # §78 add-only：退役状态上的子卡仍然算「开着」（落单卡不许被当成死卡）
        self.assertTrue(registry._is_open_child(Requirement(id="c", improvement_of="p", status="card_sent")))
        self.assertFalse(registry._is_open_child(Requirement(id="c", status="detected")))
        self.assertFalse(registry._is_open_child(Requirement(id="c", improvement_of="p", status="delivered")))
        self.assertFalse(registry._is_open_child(Requirement(id="c", improvement_of="p", status="rejected")))
        self.assertFalse(registry._is_open_child(Requirement(id="c", improvement_of="p", status="trashed")))
        self.assertTrue(registry._contains_either("abcdefghijkl", "xx abcdefghijkl yy"))
        self.assertFalse(registry._contains_either("short", "short and more"))
        self.assertFalse(registry._contains_either("abcdefghijkl", "zzzzzzzzzzzzzz"))


import unittest.mock  # noqa: E402  (used above via unittest.mock)

if __name__ == "__main__":
    unittest.main()
