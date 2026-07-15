"""Audit regression tests — quick_capture never-lose fixes.

Covers four confirmed audit findings:
  * relates_to with an unknown/hallucinated R-id must fall through to a
    minimal new_proposal (never drop the captured thought — the self-DM reply
    surface is gone since v0.21);
  * relates_to hitting a REJECTED/TRASHED/ARCHIVED card must RE-CARD (决策6)
    instead of burying the note inside a sealed card;
  * the media-capture LLM-failure fallback must title the card with the
    user's TYPED text, never the synthetic "Read these images…" prompt;
  * _fold_into must not duplicate an identical [radar] note on retry.

Everything runs inside the sandbox AIASSISTANT_HOME (tests/__init__.py); no
LLM is ever invoked (extractor injected / deterministic paths only).
"""
import unittest

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act.lib import config, quick_capture, registry


def _clear_registry():
    config.ensure_state_dirs()
    for p in config.REGISTRY_DIR.rglob("*.yaml"):
        p.unlink()


class RelatesToMissTestCase(unittest.TestCase):
    def setUp(self):
        _clear_registry()

    def test_unknown_id_falls_through_to_new_card(self):
        res = {"action": "relates_to", "req": "R-099", "note": "跟进一下那件事",
               "_text": "跟进一下 R-099 那件事", "_typed": "跟进一下 R-099 那件事"}
        reply = quick_capture.apply_result(res)
        cards = registry.load_all()
        self.assertEqual(len(cards), 1)
        card = cards[0]
        # the captured thought landed as a proposal, not dropped on the floor
        self.assertEqual(card.status, registry.State.CARD_SENT.value)
        self.assertEqual(card.title, "跟进一下 R-099 那件事")
        self.assertIn("relates_to miss", card.notes)
        # the reply is honest about what actually happened (a card was filed)
        self.assertIn(card.id, reply)
        self.assertNotIn("先没动注册表", reply)

    def test_missing_req_key_still_files_a_card(self):
        res = {"action": "relates_to", "note": "记一下这个想法",
               "_text": "记一下这个想法", "_typed": "记一下这个想法"}
        quick_capture.apply_result(res)
        self.assertEqual(len(registry.load_all()), 1)


class SealedTargetTestCase(unittest.TestCase):
    def setUp(self):
        _clear_registry()

    def test_sealed_target_recards_instead_of_burying(self):
        sealed = registry.Requirement(
            id=registry.next_id(), title="旧任务", type="other", tier="T1",
            hardness="soft", status=registry.State.TRASHED.value, summary="旧任务")
        registry.save(sealed)
        res = {"action": "relates_to", "req": sealed.id, "note": "把这个捞回来再做",
               "_text": "把它捞回来再做", "_typed": "把它捞回来再做"}
        reply = quick_capture.apply_result(res)
        # the sealed card is untouched — no note folded in, status unchanged
        again = registry.load(sealed.id)
        self.assertEqual(again.status, registry.State.TRASHED.value)
        self.assertNotIn("[quick]", again.notes or "")
        # and the restated ask got a NEW visible card (决策6: 拒绝≠已办完)
        others = [r for r in registry.load_all() if r.id != sealed.id]
        self.assertEqual(len(others), 1)
        self.assertEqual(others[0].status, registry.State.CARD_SENT.value)
        self.assertIn(others[0].id, reply)


class MediaFallbackTestCase(unittest.TestCase):
    def setUp(self):
        _clear_registry()

    @staticmethod
    def _boom(prompt):
        raise RuntimeError("claude down")

    def test_llm_failure_uses_typed_text_not_synthetic_prompt(self):
        desc = ("Read these images first (use the Read tool on each absolute "
                "path below)\n/Users/z/state/media/img-1.png\n\n用户附言:看看这张截图")
        data = quick_capture.capture(desc, extractor=self._boom,
                                     typed_text="看看这张截图")
        self.assertEqual(data["action"], "new_proposal")
        self.assertEqual(data["title"], "看看这张截图")
        self.assertNotIn("Read these images", data["summary"])
        # what apply_result quotes into sources is the typed text, not paths
        self.assertEqual(data["_text"], "看看这张截图")
        # the media pointer survives in plan so the images are not lost
        self.assertTrue(any("img-1.png" in step for step in data["plan"]))

    def test_text_only_fallback_unchanged(self):
        data = quick_capture.capture("修一下 CI", extractor=self._boom,
                                     typed_text="修一下 CI")
        self.assertEqual(data["title"], "修一下 CI")
        self.assertTrue(data.get("_fallback"))


class FoldDedupTestCase(unittest.TestCase):
    def setUp(self):
        _clear_registry()

    def test_refold_same_note_does_not_duplicate(self):
        target = registry.Requirement(
            id=registry.next_id(), title="目标卡", type="other", tier="T1",
            hardness="soft", status=registry.State.CARD_SENT.value, summary="目标卡")
        registry.save(target)
        child = registry.Requirement(
            id="", title="同一条", type="other", tier="T1",
            sources=[{"who": "boss", "channel": "slack",
                      "date": "2026-07-15", "quote": "同一条"}])

        quick_capture._fold_into(target, child, "同一条进展")
        first = registry.load(target.id)
        mentions_after_first = int(first.repeated_mentions or 1)

        # the radar failed-note retry queue re-folds the identical hit
        quick_capture._fold_into(first, child, "同一条进展")
        second = registry.load(target.id)
        self.assertEqual(second.notes.count("[radar] 同一条进展"), 1)
        self.assertEqual(int(second.repeated_mentions or 1), mentions_after_first)


if __name__ == "__main__":
    unittest.main()
