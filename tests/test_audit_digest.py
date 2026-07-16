"""act/digest.py + act/oneonone.py behavior.

* §40.7 digest unify: the Monday digest lands as a review-lane chat card
  (same filing pattern as act/weekly_digest — final_draft = full markdown,
  merge_or_new dedup on the per-Monday title) and writes NO workbench file;
  the 1:1 prep page (its own module) still lands via oneonone.output_root
  (configured repo, STATE_DIR fallback — never the example placeholder).
* §40 (#19): the rendered pages say lane display names (待审批/待验收/…),
  never raw registry status words; the promise ledger is owner-neutral
  (no 「manager 欠的」) and parameterized on owner.name.
* Stable suggestion titles: 进化建议 embedded live counts in the card title,
  defeating merge_or_new's title dedup — a near-duplicate self-improvement
  card stacked every Monday the number moved. Titles are now stable; volatile
  counts live only in summary/sources.quote. They land status=detected
  (潜在任务), never auto-card_sent.

Everything runs inside the sandbox AIASSISTANT_HOME (tests/__init__.py).
"""
import datetime as _dt
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act.* import

from act import digest, oneonone
from act.lib import config, registry


class DigestCardTestCase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        if config.CONFIG_PATH.exists():
            config.CONFIG_PATH.unlink()
        self.addCleanup(lambda: config.CONFIG_PATH.unlink(missing_ok=True))
        # v0.42 §15: copy follows AIASSISTANT_UI_LANG > persisted > system
        # locale — pin zh so the zh-copy assertions stay locale-independent.
        lang = mock.patch.dict(os.environ, {"AIASSISTANT_UI_LANG": "zh"})
        lang.start()
        self.addCleanup(lang.stop)
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()
        # quiet + deterministic: no notification queue writes from these tests
        patcher = mock.patch.object(digest.notify, "notify", return_value=True)
        self.notify = patcher.start()
        self.addCleanup(patcher.stop)

    def test_digest_lands_as_review_lane_chat_card_not_a_file(self):
        card = digest.publish_digest()
        self.assertEqual(card.status, registry.State.REVIEW.value)
        self.assertEqual(card.type, "digest")
        self.assertEqual(card.delivery_mode, "chat")
        ex = card.execution or {}
        self.assertIn("周一 digest", ex.get("final_draft", ""))
        self.assertTrue(ex.get("delivered_summary"))
        # §40.7: no workbench digest file anymore, anywhere
        self.assertFalse((config.STATE_DIR / "digests").exists())
        # the 1:1 prep page (its own surface) still lands
        preps = list((config.STATE_DIR / "oneonone").glob("prep-*.md"))
        self.assertTrue(preps, "1:1 prep page must still land under state/")
        # notification carries a next step, never a filesystem path
        (_title, body) = self.notify.call_args[0][:2]
        self.assertNotIn(str(config.STATE_DIR), body)

    def test_same_day_rerun_merges_instead_of_stacking(self):
        today = _dt.date(2026, 7, 13)
        first = digest.publish_digest(today)
        again = digest.publish_digest(today)
        self.assertEqual(first.id, again.id)
        cards = [r for r in registry.load_all() if r.type == "digest"]
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0].status, registry.State.REVIEW.value)

    def test_pages_say_lane_names_not_raw_status_words(self):
        req = registry.Requirement(id="R-101", title="写周报",
                                   status="card_sent")
        registry.save(req)
        md = digest.build_digest()
        # the item line says the lane display name, not the raw status word
        # (the folded analytics block legitimately contains event NAMES like
        # card_sent — that's telemetry vocabulary, not the item line).
        self.assertIn("- R-101 · 写周报（待审批", md)
        self.assertNotIn("（card_sent", md)
        prep = oneonone.build_prep()
        self.assertIn("R-101 · 写周报 （待审批", prep)
        self.assertNotIn("（card_sent", prep)

    def test_ledger_is_owner_neutral_and_parameterized(self):
        md = digest.build_digest()
        prep = oneonone.build_prep()
        for page in (md, prep):
            self.assertNotIn("manager 欠的", page)
            self.assertNotIn("他的承诺", page)
            self.assertIn("Zelin", page)   # cfg.owner_name default
        # the compat tag is still recognized and still discoverable
        req = registry.Requirement(id="R-102", title="账", status="card_sent",
                                   notes="[MANAGER-OWES] 下周给我数据")
        registry.save(req)
        md = digest.build_digest()
        self.assertIn("[MANAGER-OWES] 下周给我数据", md)

    def test_output_root_reads_config_at_call_time(self):
        self.assertEqual(oneonone.output_root(), config.STATE_DIR)
        tmp = tempfile.TemporaryDirectory(prefix="workbench-")
        self.addCleanup(tmp.cleanup)
        config.CONFIG_PATH.write_text(
            f"execution:\n  default_target_repo: {tmp.name}\n",
            encoding="utf-8")
        self.assertEqual(oneonone.output_root(), Path(tmp.name))


class SuggestionTitleStabilityTestCase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()
        self.cfg = config.load_config()

    @staticmethod
    def _events(resume_fails: int, rejects: int = 0, approves: int = 0) -> list:
        return ([{"event": "auto_resume", "ok": False}] * resume_fails
                + [{"event": "inbox_reject"}] * rejects
                + [{"event": "inbox_approve"}] * approves)

    def test_titles_stable_while_counts_move_to_detail(self):
        s1 = digest.build_suggestions(self.cfg, events=self._events(12, 3, 1))
        s2 = digest.build_suggestions(self.cfg, events=self._events(17, 6, 2))
        self.assertEqual(len(s1), 2)
        self.assertEqual([t for t, _ in s1], [t for t, _ in s2])
        # no live count leaks into a title
        self.assertNotIn("12", s1[0][0])
        self.assertNotIn("3/4", s1[1][0])
        # the live counts still surface — in the volatile detail
        self.assertIn("12", s1[0][1])
        self.assertIn("17", s2[0][1])
        self.assertIn("3/4", s1[1][1])
        self.assertIn("6/8", s2[1][1])

    def test_repeat_mondays_merge_into_one_card(self):
        filed1 = digest.file_suggestion_cards(
            digest.build_suggestions(self.cfg, events=self._events(12)))
        filed2 = digest.file_suggestion_cards(
            digest.build_suggestions(self.cfg, events=self._events(17)))
        self.assertEqual(len(filed1), 1)
        self.assertEqual(len(filed2), 1)
        self.assertEqual(filed1[0].id, filed2[0].id,
                         "week 2 must merge into week 1's card, not stack")
        cards = [r for r in registry.load_all() if r.type == "self-improvement"]
        self.assertEqual(len(cards), 1)
        # counts stayed out of the title but landed in the card content
        self.assertNotIn("12", cards[0].title)
        self.assertIn("12", cards[0].summary)

    def test_suggestions_land_detected_never_card_sent(self):
        # digest.py's stated rule (§16): suggestions go to 潜在任务 for the
        # owner to raise — never straight to 待审批.
        filed = digest.file_suggestion_cards(
            digest.build_suggestions(self.cfg, events=self._events(12, 6, 2)))
        self.assertTrue(filed)
        for card in filed:
            self.assertEqual(card.status, registry.State.DETECTED.value)


if __name__ == "__main__":
    unittest.main()
