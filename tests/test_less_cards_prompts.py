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
        _seed("R-019", "给 Quinton 开通 PRD 文档编辑权限", "delivered",
              sources=[{"who": "quinton", "channel": "slack",
                        "date": "2026-07-01", "quote": "need PRD permissions"}])
        inv = quick_capture.registry_inventory_text()
        # pre-§38 anchor format survives as a literal prefix
        self.assertIn("R-019 | delivered | 给 Quinton 开通 PRD 文档编辑权限", inv)
        self.assertIn("关键词:", inv)
        self.assertIn("permissions", inv)

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

    def test_review_row_carries_notes_text(self):
        _seed("R-043", "待验收卡", State.REVIEW.value,
              notes="[radar] 验收期折叠 [@t2]",
              execution={"session_id": "s-1", "review_at": "2026-07-16T00:00:00Z"})
        dash = self._dash()
        row = next(r for r in dash["review"] if r["id"] == "R-043")
        self.assertIn("[radar] 验收期折叠", row["notes_text"])
