"""§37 living display titles — sanitizer, registry helper, projection, LLM keys.

Covers (v0.37 build brief):
- titles.sanitize_title table: URL → "domain ▸ segment", filesystem path →
  last component, overlong text → clause-clipped with …, whitespace collapse,
  short plain text passthrough — a raw URL/path can never render as a board
  title, with ZERO migration for legacy cards;
- registry.set_display_title: LLM vs user precedence (user_titled pin),
  former_titles append/dedup/cap(3), fail-closed on junk input;
- dashboard projection: display_title fallback chain per row + user_titled /
  former_titles / notes_text (capped) add-only fields;
- quick_capture / analyze piggyback: the optional display_title output key is
  stored when present and degrades silently when absent/malformed.
"""
import os
import tempfile
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import analyze
from act.lib import config, dashboard, quick_capture, registry, titles
from act.lib.registry import Requirement, State


class SanitizeTitleTestCase(unittest.TestCase):
    def test_table(self):
        cases = [
            # URL → domain ▸ last-meaningful-segment / video id
            ("https://www.youtube.com/watch?v=dQw4w9WgXcQ",
             "youtube.com ▸ dQw4w9WgXcQ"),
            ("https://github.com/Wan-ZL/zelin-ai-assistant/pull/42",
             "github.com ▸ 42"),
            ("https://example.com/", "example.com"),
            # filesystem path → last component
            ("/Users/zelin/Projects/zelin-ai-assistant/docs/CONTRACT.md",
             "CONTRACT.md"),
            ("~/Projects/zelin-ai-assistant/act/executor.py", "executor.py"),
            # whitespace collapse, short text passthrough
            ("整理  一下\n推荐信", "整理 一下 推荐信"),
            ("修复登录按钮不响应", "修复登录按钮不响应"),
            ("", ""),
            (None, ""),
        ]
        for raw, want in cases:
            self.assertEqual(titles.sanitize_title(raw), want, msg=repr(raw))

    def test_long_text_clips_at_first_sentence(self):
        t = ("把周报的三个亮点整理出来。然后再补一段下季度的计划，"
             "最后发给经理看一眼确认没问题，再抄送给两位同事备份留档存证，"
             "顺手把上周遗留的两个问题也标注清楚")
        self.assertGreater(len(t), 60)   # long enough to take the clip branch
        out = titles.sanitize_title(t)
        # review fix: the boundary branch appends "…" too — the result is a
        # truncation of a longer title, and the ellipsis says so honestly.
        self.assertEqual(out, "把周报的三个亮点整理出来…")

    def test_mid_word_ascii_dot_is_not_a_sentence_boundary(self):
        # review fix: the old regex treated a BARE mid-word dot as a sentence
        # end ("把 config" / "升级 v0" / "Follow up with Dr" garbage).
        cases = [
            ("把 config.json 里的三个字段改成新的命名并同步更新所有引用的地方，"
             "另外再检查部署脚本里的路径是否也需要一起调整", "config.json"),
            ("升级 v0.33.1 之后把所有旧的配置项逐一迁移到新的结构里，并验证"
             "每一台机器上的实际行为都保持一致、确认没有任何回归再收工",
             "v0.33.1"),
            ("看一下 domain.com 上面的三份报价，然后挑出最合适的一份写进对比"
             "表格里，发给团队里的所有人做最终确认之后再定下来", "domain.com"),
        ]
        for t, token in cases:
            self.assertGreater(len(t), 60, msg=token)
            out = titles.sanitize_title(t)
            # the dotted token survives intact — never split at its dot
            self.assertIn(token, out, msg=f"{token} split: {out!r}")
            self.assertTrue(out.endswith("…"), msg=out)

    def test_abbreviation_dot_is_not_a_sentence_boundary(self):
        t = ("Follow up with Dr. Smith about the three recommendation letters "
             "and the timeline for the EB-1A filing next month")
        out = titles.sanitize_title(t)
        # "Dr." must not terminate the clause ("Follow up with Dr" garbage)
        self.assertNotEqual(out, "Follow up with Dr")
        self.assertIn("Dr. Smith", out)
        self.assertTrue(out.endswith("…"))

    def test_real_ascii_sentence_boundary_still_clips(self):
        t = ("Ship the draft today. Then collect feedback from the reviewers "
             "and fold every actionable comment into the second revision")
        out = titles.sanitize_title(t)
        self.assertEqual(out, "Ship the draft today…")

    def test_long_text_without_sentence_boundary_clips_with_ellipsis(self):
        t = "a" * 200
        out = titles.sanitize_title(t)
        self.assertTrue(out.endswith("…"))
        self.assertLessEqual(len(out), 49)

    def test_never_exceeds_max(self):
        for raw in ("x" * 500, "https://example.com/" + "y" * 300,
                    "词" * 200):
            self.assertLessEqual(len(titles.sanitize_title(raw)),
                                 titles.MAX_DISPLAY_TITLE)

    def test_clip_title_fail_closed(self):
        self.assertIsNone(titles.clip_title(None))
        self.assertIsNone(titles.clip_title(123))
        self.assertIsNone(titles.clip_title("   "))
        out = titles.clip_title("t" * 100)
        self.assertEqual(len(out), titles.MAX_DISPLAY_TITLE)
        self.assertTrue(out.endswith("…"))


class SetDisplayTitleTestCase(unittest.TestCase):
    def test_llm_set_and_former_titles_cap(self):
        req = Requirement(id="R-900", title="t")
        self.assertTrue(registry.set_display_title(req, "名字一"))
        self.assertEqual(req.display_title, "名字一")
        self.assertIsNone(req.former_titles)
        for i, name in enumerate(["名字二", "名字三", "名字四", "名字五"]):
            self.assertTrue(registry.set_display_title(req, name))
        # cap 3, newest last, oldest dropped
        self.assertEqual(req.former_titles, ["名字二", "名字三", "名字四"])
        self.assertEqual(req.display_title, "名字五")

    def test_former_titles_dedup(self):
        req = Requirement(id="R-901", title="t")
        registry.set_display_title(req, "A")
        registry.set_display_title(req, "B")
        registry.set_display_title(req, "A")
        registry.set_display_title(req, "B")
        # each former name appears once, most recent occurrence kept
        self.assertEqual(req.former_titles, ["B", "A"])

    def test_user_pin_blocks_llm_overwrite(self):
        req = Requirement(id="R-902", title="t")
        self.assertTrue(registry.set_display_title(req, "用户的名字", by_user=True))
        self.assertTrue(req.user_titled)
        self.assertFalse(registry.set_display_title(req, "LLM 的名字"))
        self.assertEqual(req.display_title, "用户的名字")
        # the user can still rename
        self.assertTrue(registry.set_display_title(req, "用户的新名字", by_user=True))
        self.assertEqual(req.display_title, "用户的新名字")
        self.assertIn("用户的名字", req.former_titles)

    def test_fail_closed_inputs(self):
        req = Requirement(id="R-903", title="t")
        self.assertFalse(registry.set_display_title(req, None))
        self.assertFalse(registry.set_display_title(req, 42))
        self.assertFalse(registry.set_display_title(req, "   "))
        self.assertIsNone(req.display_title)
        # same value = no change
        registry.set_display_title(req, "同名")
        self.assertFalse(registry.set_display_title(req, "同名"))
        self.assertIsNone(req.former_titles)

    def test_round_trips_through_yaml(self):
        req = Requirement(id="R-904", title="t", status=State.CARD_SENT.value)
        registry.set_display_title(req, "旧名")
        registry.set_display_title(req, "看板名", by_user=True)
        registry.save(req)
        loaded = registry.load("R-904")
        self.assertEqual(loaded.display_title, "看板名")
        self.assertTrue(loaded.user_titled)
        self.assertEqual(loaded.former_titles, ["旧名"])


class ProjectionTestCase(unittest.TestCase):
    def setUp(self):
        self.cfg = config.Config()
        home = tempfile.mkdtemp(prefix="title-home-")
        patcher = mock.patch.dict(os.environ, {"HOME": home})
        patcher.start()
        self.addCleanup(patcher.stop)

    def _build(self, reqs):
        return dashboard.build_dashboard(reqs=reqs, agents=[], cfg=self.cfg,
                                         archived=[])

    def test_url_title_never_renders_raw(self):
        req = Requirement.from_dict({
            "id": "R-910", "status": "card_sent",
            "title": "https://www.youtube.com/watch?v=abc123",
        })
        row = self._build([req])["needs_approval"][0]
        self.assertEqual(row["display_title"], "youtube.com ▸ abc123")

    def test_stored_display_title_wins_and_optionals_project(self):
        req = Requirement.from_dict({
            "id": "R-911", "status": "card_sent", "title": "原始标题",
            "notes": "n" * 5000,
        })
        registry.set_display_title(req, "旧名")
        registry.set_display_title(req, "钉住的名字", by_user=True)
        row = self._build([req])["needs_approval"][0]
        self.assertEqual(row["display_title"], "钉住的名字")
        self.assertIs(row["user_titled"], True)
        self.assertEqual(row["former_titles"], ["旧名"])
        # §38 clip semantics: line-aligned TAIL behind an honest marker line
        # (fold-note [@ts] handles live at the tail — the head clip this
        # asserted originally silently dropped them; merge reconciliation
        # kept the tail clip, see CONTRACT §38.2).
        nt = row["notes_text"]
        self.assertTrue(nt.endswith("n" * 100))          # tail survives
        self.assertIn("更早的备注已省略", nt.split("\n")[0])
        self.assertLessEqual(len(nt), 2000 + 30)          # cap + marker line

    def test_optionals_omitted_when_empty(self):
        req = Requirement.from_dict({
            "id": "R-912", "status": "detected", "title": "短标题"})
        row = self._build([req])["debt"][0]
        self.assertEqual(row["display_title"], "短标题")
        self.assertNotIn("user_titled", row)
        self.assertNotIn("former_titles", row)
        self.assertNotIn("notes_text", row)

    def test_every_lane_carries_display_title(self):
        def mk(rid, status, **kw):
            return Requirement.from_dict(
                {"id": rid, "status": status, "title": "标题 " + rid, **kw})
        dash = self._build([
            mk("R-920", "card_sent"),
            mk("R-921", "detected"),
            mk("R-922", "approved"),
            mk("R-923", "review", execution={"review_at": "2026-07-16T00:00:00Z"}),
            mk("R-924", "delivered",
               execution={"accepted_at": "2026-07-16T00:00:00Z"}),
            mk("R-925", "trashed", prev_status="detected"),
            mk("R-926", "raising"),
        ])
        for lane in ("needs_approval", "debt", "running", "review",
                     "completed", "trash"):
            for row in dash[lane]:
                self.assertIn("display_title", row, msg=lane)


class LLMPiggybackTestCase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()

    def test_capture_new_proposal_stores_display_title(self):
        res = {"action": "new_proposal", "title": "做一份周报",
               "summary": "整理周报", "display_title": "整理这周的周报",
               "_text": "做一份周报", "_typed": "做一份周报"}
        quick_capture.apply_result(res, config.Config())
        cards = registry.load_all()
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0].display_title, "整理这周的周报")

    def test_capture_malformed_display_title_degrades(self):
        res = {"action": "new_proposal", "title": "做一份周报",
               "summary": "整理周报", "display_title": 12345,
               "_text": "做一份周报", "_typed": "做一份周报"}
        quick_capture.apply_result(res, config.Config())
        self.assertIsNone(registry.load_all()[0].display_title)

    def test_expand_debt_stores_display_title_and_honors_pin(self):
        req = Requirement(id="R-930", title="调研 X", status="detected")
        registry.save(req)

        class P:
            returncode = 0
            stdout = ('{"summary": "s", "plan": ["p"],'
                      ' "display_title": "调研 X 的三个方案"}')
        analyze.expand_debt(req, config.Config(), runner=lambda _p: P())
        self.assertEqual(req.display_title, "调研 X 的三个方案")

        pinned = Requirement(id="R-931", title="调研 Y", status="detected")
        registry.set_display_title(pinned, "我钉住的名字", by_user=True)
        registry.save(pinned)
        analyze.expand_debt(pinned, config.Config(), runner=lambda _p: P())
        self.assertEqual(pinned.display_title, "我钉住的名字")

    def test_prompts_mention_display_title(self):
        req = Requirement(id="R-932", title="t", status="detected")
        self.assertIn("display_title", analyze.build_expand_prompt(req))
        self.assertIn("display_title",
                      quick_capture.build_capture_prompt("一句话"))
        self.assertIn("display_title",
                      quick_capture.build_triage_prompt("候选"))

    def test_increment_child_inherits_display_title(self):
        # non-blocking review note: the LLM display_title of a candidate must
        # survive merge_or_new when it files an increment child of an OPEN
        # parent (the child is what lands on the board).
        parent = Requirement(id="R-933", title="同一件事的标题很长很长很长",
                             status=State.DETECTED.value, hardness="soft")
        registry.save(parent)
        cand = Requirement(id="", title="同一件事的标题很长很长很长",
                           hardness="hard", deadline="2026-08-01")
        registry.set_display_title(cand, "推进这件事的下一步")
        child = registry.merge_or_new(cand)
        self.assertNotEqual(child.id, parent.id)   # increment → child card
        self.assertEqual(child.display_title, "推进这件事的下一步")


class MergeCarriesDisplayNamesTestCase(unittest.TestCase):
    """§37 review fix: 采纳合并 must not orphan the secondary's display names —
    merged is TERMINAL (no un-merge), and the old name has to stay findable
    through the primary's searchable notes_text."""

    def setUp(self):
        config.ensure_state_dirs()
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()

    def test_merge_folds_secondary_display_names_into_primary_notes(self):
        from act import actd
        primary = Requirement(id="R-935", title="主卡", status="card_sent")
        registry.save(primary)
        sec = Requirement(id="R-936", title="副卡内部标题", status="card_sent")
        registry.set_display_title(sec, "LLM 起的旧名")
        registry.set_display_title(sec, "用户改的名", by_user=True)
        registry.save(sec)

        actd._merge_into_primary("R-935", ["R-936"])

        merged_primary = registry.load("R-935")
        self.assertIn("用户改的名", merged_primary.notes)
        self.assertIn("LLM 起的旧名", merged_primary.notes)
        # the searchable projection carries them (notes → notes_text)
        dash = dashboard.build_dashboard(
            reqs=[merged_primary], agents=[], cfg=config.Config(), archived=[])
        row = dash["needs_approval"][0]
        self.assertIn("用户改的名", row["notes_text"])
        # secondary is terminal merged, its names archived on the primary
        self.assertEqual(registry.load("R-936").status, State.MERGED.value)


if __name__ == "__main__":
    unittest.main()
