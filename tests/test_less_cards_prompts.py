"""§38.1 少建卡 prompt surfaces — inventory display-corpus, deterministic
pre-pass flag, fold-first bias — AND the frozen pre-§38 anchors staying put.

The dashboard notes_text projection (§38.2's display surface) is pinned here
too — the Mac fold-note UI parses it.
"""
import unittest

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act.lib import config, quick_capture, registry
from act.lib.dashboard import build_dashboard
from act.lib.registry import Requirement, State


def _clean():
    config.ensure_state_dirs()
    for p in config.REGISTRY_DIR.glob("*.yaml"):
        p.unlink()


def _seed(rid, title, status=State.CARD_SENT.value, **kw):
    r = Requirement(id=rid, title=title, status=status, **kw)
    registry.save(r)
    return r


class InventoryCorpusTestCase(unittest.TestCase):
    def setUp(self):
        _clean()
        self.addCleanup(_clean)
        self.cfg = config.Config()

    def test_line_prefix_frozen_and_corpus_appended(self):
        # aliases mine title/display_title/summary ONLY (§38 privacy rule —
        # notes/quotes are untrusted and may carry pasted secrets)
        _seed("R-019", "给 Quinton 开通 PRD 文档编辑权限", "delivered",
              summary="give Quinton PRD permissions",
              sources=[{"who": "quinton", "channel": "slack",
                        "date": "2026-07-01", "quote": "quoteonlyword here"}])
        inv = quick_capture.registry_inventory_text()
        # pre-§38 anchor format survives as a literal prefix
        self.assertIn("R-019 | delivered | 给 Quinton 开通 PRD 文档编辑权限", inv)
        self.assertIn("关键词:", inv)
        self.assertIn("permissions", inv)
        self.assertNotIn("quoteonlyword", inv)

    def test_url_title_gets_display_name(self):
        _seed("R-020", "https://github.com/Wan-ZL/zelin-ai-assistant/issues/42")
        inv = quick_capture.registry_inventory_text()
        self.assertIn("显示名:", inv)
        self.assertIn("github.com", inv)

    def test_plain_short_title_gets_no_display_name_segment(self):
        _seed("R-021", "回 manager")
        line = next(ln for ln in
                    quick_capture.registry_inventory_text().split("\n")
                    if ln.startswith("R-021"))
        self.assertNotIn("显示名:", line)

    def test_stored_display_title_wins(self):
        r = _seed("R-022", "https://example.com/x/y")
        r.display_title = "整理示例清单"       # §37 field (attribute-set works pre-#55)
        registry.save(r)
        # reload drops unknown attrs pre-#55; feed the object list directly
        self.assertIn("显示名: 整理示例清单",
                      quick_capture.registry_inventory_text([r]))


class PrePassTestCase(unittest.TestCase):
    def setUp(self):
        _clean()
        self.addCleanup(_clean)
        self.cfg = config.Config()

    def test_triage_prompt_flags_likely_candidates(self):
        _seed("R-030", "整理 EB-1A 推荐信 recommendation letters 清单")
        _seed("R-031", "修 login oauth bug")
        prompt = quick_capture.build_triage_prompt(
            "候选需求：EB-1A 推荐信 recommendation letters 又有进展", self.cfg)
        self.assertIn("最可能相关的已有卡", prompt)
        self.assertIn("R-030", prompt.split("最可能相关的已有卡")[1][:400])
        self.assertIn("重合词", prompt)

    def test_capture_prompt_flags_likely_candidates(self):
        _seed("R-030", "整理 EB-1A 推荐信 recommendation letters 清单")
        prompt = quick_capture.build_capture_prompt(
            "EB-1A 推荐信 recommendation letters 进展记一下", self.cfg)
        self.assertIn("最可能相关的已有卡", prompt)

    def test_no_candidates_no_block(self):
        # the pre-pass BLOCK is absent (the bias bar's passing mention of the
        # pre-pass stays — that line is unconditional prompt text)
        _seed("R-030", "修 login oauth bug")
        prompt = quick_capture.build_triage_prompt("候选需求：订滑雪板", self.cfg)
        self.assertNotIn("最可能相关的已有卡", prompt)

    def test_scaffold_labels_never_rank(self):
        # review blocker 1, reproduced: a card whose corpus shares ONLY the
        # candidate_desc scaffold tokens (需求/来源) with an unrelated new ask
        # must not be flagged — label/metadata tokens are not evidence.
        _seed("R-050", "整理需求来源清单")
        desc = quick_capture.candidate_desc(
            "订一块新的滑雪板 burton", quote="想订一块 burton 的滑雪板",
            who="manager", channel="meeting", date="2026-07-16")
        prompt = quick_capture.build_triage_prompt(desc, self.cfg)
        self.assertNotIn("最可能相关的已有卡", prompt)

    def test_prepass_text_strips_scaffold_keeps_content(self):
        desc = quick_capture.candidate_desc(
            "修 oauth bug", quote="原话 oauth 挂了",
            who="quinton", channel="slack", date="2026-07-16",
            ref="https://example.com/t/1")
        t = quick_capture._prepass_text(desc)
        self.assertIn("修 oauth bug", t)
        self.assertIn("原话 oauth 挂了", t)
        self.assertNotIn("候选需求", t)
        self.assertNotIn("来源", t)
        self.assertNotIn("quinton", t)
        self.assertNotIn("2026-07-16", t)
        self.assertNotIn("example.com", t)
        # scaffold-free text (self-DM capture) passes through unchanged
        self.assertEqual(quick_capture._prepass_text("随手记一条"), "随手记一条")


class BiasAndFrozenAnchorsTestCase(unittest.TestCase):
    """§38.1 fold-first bias present; every pre-§38 anchor byte-identical."""

    def setUp(self):
        _clean()
        self.addCleanup(_clean)
        self.cfg = config.Config()

    def test_triage_bar_gains_fold_first_keeps_anchors(self):
        prompt = quick_capture.build_triage_prompt("候选需求：x", self.cfg)
        self.assertIn("入库把关", prompt)              # the FROZEN gate marker
        self.assertIn("硬标准", prompt)
        self.assertIn("现在】就需要", prompt)
        self.assertIn("needs_action", prompt)
        self.assertIn("潜在任务", prompt)
        # the new bias line
        self.assertIn("绝不为它新建卡", prompt)
        self.assertIn("折叠是可逆的", prompt)

    def test_capture_prompt_gains_fold_first_keeps_lossless(self):
        prompt = quick_capture.build_capture_prompt("记一下", self.cfg)
        self.assertIn("无损原则", prompt)
        self.assertNotIn("硬标准", prompt)              # radar bar still not copied
        self.assertIn("折叠优先", prompt)
        self.assertIn("拆成新卡", prompt)


class NotesTextProjectionTestCase(unittest.TestCase):
    def setUp(self):
        _clean()
        self.addCleanup(_clean)
        self.cfg = config.Config()

    def _dash(self):
        return build_dashboard(cfg=self.cfg, agents=[], merge_dir=None)

    def test_needs_approval_and_debt_carry_notes_text(self):
        a = _seed("R-040", "提案卡", State.CARD_SENT.value)
        registry.append_fold_note(a, "折进来的进展", "radar")
        registry.save(a)
        _seed("R-041", "备选卡", State.DETECTED.value, notes="[quick] 备注 [@t1]")
        dash = self._dash()
        row = next(r for r in dash["needs_approval"] if r["id"] == "R-040")
        self.assertIn("[radar] 折进来的进展", row["notes_text"])
        drow = next(r for r in dash["debt"] if r["id"] == "R-041")
        self.assertIn("[quick] 备注 [@t1]", drow["notes_text"])

    def test_empty_notes_key_omitted(self):
        _seed("R-042", "无备注卡", State.CARD_SENT.value)
        dash = self._dash()
        row = next(r for r in dash["needs_approval"] if r["id"] == "R-042")
        self.assertNotIn("notes_text", row)

    def test_notes_over_cap_keep_tail_fold_handles(self):
        # review blocker 4: fold lines append at the TAIL — a head clip would
        # silently drop the newest [@ts] handles, the exact thing 拆成新卡
        # needs. Over the cap the tail survives, line-aligned, with an honest
        # ellipsis marker for the dropped head.
        r = _seed("R-044", "多备注卡", State.CARD_SENT.value)
        r.notes = "\n".join(f"老备注填充行{i} " + "x" * 60 for i in range(40))
        ts = registry.append_fold_note(r, "最新折叠进展", "radar")
        registry.save(r)
        dash = self._dash()
        row = next(x for x in dash["needs_approval"] if x["id"] == "R-044")
        nt = row["notes_text"]
        self.assertIn(f"[@{ts}]", nt)
        self.assertIn("[radar] 最新折叠进展", nt)
        self.assertNotIn("老备注填充行0 ", nt)
        lines = nt.split("\n")
        self.assertIn("更早的备注已省略", lines[0])
        # line-aligned: every surviving line is intact (parses or is filler)
        self.assertTrue(lines[1].startswith("老备注填充行"))
        parsed = registry.parse_fold_notes(nt)
        self.assertEqual(parsed[-1]["ts"], ts)

    def test_split_marker_straddling_the_cap_survives_intact(self):
        # review blocker 9: with the OLD head clip, the 已拆出 flip could
        # straddle the [:2000] boundary and project as "[已拆出 R" — the Mac
        # real-signal never fired (false 180s timeout) and the line degraded
        # to an un-splittable legacy row. The tail-aligned projection must
        # keep the whole line.
        from unittest import mock
        r = _seed("R-045", "跨界卡", State.CARD_SENT.value)
        r.notes = "x" * 1950                       # one old filler line
        with mock.patch.object(registry, "_iso_now",
                               return_value="2026-07-16T08:00:00Z"):
            ts = registry.append_fold_note(r, "拆出去的那条进展", "radar")
        registry.mark_note_split(r, ts, "R-999")
        registry.save(r)
        notes = registry.load("R-045").notes
        # the repro shape really straddles: the 已拆出 tag spans offset 2000
        tag_at = notes.index(" [已拆出 R-999]")
        self.assertLess(tag_at, 2000)
        self.assertGreater(tag_at + len(" [已拆出 R-999]"), 2000)
        dash = self._dash()
        row = next(x for x in dash["needs_approval"] if x["id"] == "R-045")
        nt = row["notes_text"]
        self.assertIn("[已拆出 R-999]", nt)              # intact, not "[已拆出 R"
        entry = registry.parse_fold_notes(nt)[-1]        # Store's real-signal read
        self.assertEqual(entry["split_into"], "R-999")
        self.assertEqual(entry["ts"], ts)

    def test_review_row_carries_notes_text(self):
        _seed("R-043", "待验收卡", State.REVIEW.value,
              notes="[radar] 验收期折叠 [@t2]",
              execution={"session_id": "s-1", "review_at": "2026-07-16T00:00:00Z"})
        dash = self._dash()
        row = next(r for r in dash["review"] if r["id"] == "R-043")
        self.assertIn("[radar] 验收期折叠", row["notes_text"])
