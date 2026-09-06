"""store2.hot.derive — every hot-column rule as its own table (CONTRACT §53.2).

Pins the P3b split: legacy ``merged_into:`` normalisation (with/without a
parent id), vocabulary status + merged parent pointer, out-of-vocabulary
status = error, prev_status validation + trashed/archived backfill, tier
fallback, title/type str coercion, deadline shape, target_repo coercion,
work_id NULL-ing, and that warnings are emitted in column order.
"""
import unittest

from act.lib.store2 import hot


def _derive(**norm):
    base = {"id": "P-1", "status": "detected", "title": "t", "type": "code", "tier": "T1",
            "sources": []}
    base.update(norm)
    return hot.derive(base)


class StatusColumnsTestCase(unittest.TestCase):
    def test_legacy_merged_with_and_without_parent(self):
        h, w, e = _derive(status="merged_into: P-9 ")
        self.assertEqual((h["status"], h["merged_into_id"], e), ("merged", "P-9", []))
        self.assertTrue(any("legacy status" in x for x in w))
        h, w, e = _derive(status="merged_into:")
        self.assertEqual((h["status"], h["merged_into_id"]), ("merged", ""))
        self.assertIn("legacy merged_into: 串无父卡 id，schema 无法表达", e)

    def test_vocab_merged_needs_pointer(self):
        h, _w, e = _derive(status="merged", merged_into=7)
        self.assertEqual((h["status"], h["merged_into_id"], e), ("merged", "7", []))
        h, _w, e = _derive(status="merged", merged_into=None)
        self.assertEqual(h["merged_into_id"], None)
        self.assertIn("status=merged 但无 merged_into 父指针（CHECK 拒收）", e)
        h, _w, e = _derive(status="review", merged_into="ignored")
        self.assertEqual((h["status"], h["merged_into_id"], e), ("review", None, []))

    def test_out_of_vocab_status_is_an_error(self):
        h, _w, e = _derive(status="flying")
        self.assertIsNone(h["status"])
        self.assertEqual(e, ["status 'flying' 不在 schema 词表内"])
        h, _w, e = _derive(status=None)
        self.assertEqual(e, ["status None 不在 schema 词表内"])


class PrevStatusTestCase(unittest.TestCase):
    def test_validation_and_backfill(self):
        self.assertEqual(_derive(prev_status="review")[0]["prev_status"], "review")
        h, w, _e = _derive(prev_status="bogus")
        self.assertIsNone(h["prev_status"])
        self.assertTrue(any("prev_status 'bogus' 不在词表" in x for x in w))
        h, w, _e = _derive(status="trashed")
        self.assertEqual(h["prev_status"], "detected")
        self.assertIn("trashed 缺 prev_status，热列回填 detected", w)
        h, w, _e = _derive(status="archived", prev_status="bogus")
        self.assertEqual(h["prev_status"], "delivered")
        self.assertIn("archived 缺 prev_status，热列回填 delivered", w)
        h, w, _e = _derive(status="trashed", prev_status="approved")
        self.assertEqual((h["prev_status"], w), ("approved", []))
        self.assertIsNone(_derive(status="flying")[0]["prev_status"])


class ScalarColumnsTestCase(unittest.TestCase):
    def test_tier(self):
        self.assertEqual(_derive(tier="T2")[0]["tier"], "T2")
        h, w, _e = _derive(tier="T7")
        self.assertEqual(h["tier"], "T1")
        self.assertIn("tier 'T7' 越界，热列回落 T1（payload 保留原值）", w)
        self.assertEqual(_derive(tier=None)[0]["tier"], "T1")

    def test_title_and_type_coercion(self):
        h, w, _e = _derive(title=None, type=12)
        self.assertEqual((h["title"], h["type"]), ("", "12"))
        self.assertEqual(w, ["title None 非 str，热列存 str 兜底", "type 12 非 str，热列存 str 兜底"])
        self.assertEqual(_derive(title="x", type="")[0]["type"], "")

    def test_deadline(self):
        self.assertEqual(_derive(deadline="2026-09-02")[0]["deadline"], "2026-09-02")
        h, w, _e = _derive(deadline="2026/09/02")
        self.assertIsNone(h["deadline"])
        self.assertIn("deadline '2026/09/02' 不符 YYYY-MM-DD，热列置 NULL（payload 保留原值）", w)
        h, w, _e = _derive(deadline=None)
        self.assertEqual((h["deadline"], w), (None, []))
        self.assertIsNone(_derive(deadline="2026-09-02T10:00")[0]["deadline"])
        self.assertIsNone(_derive(deadline=20260902)[0]["deadline"])

    def test_target_repo(self):
        self.assertEqual(_derive(target_repo="/r")[0]["target_repo"], "/r")
        self.assertIsNone(_derive(target_repo=None)[0]["target_repo"])
        h, w, _e = _derive(target_repo=["/r"])
        self.assertEqual(h["target_repo"], "['/r']")
        self.assertIn("target_repo ['/r'] 非 str，热列存 str 兜底", w)

    def test_work_id_and_origin(self):
        self.assertIsNone(_derive(work_id="")[0]["work_id"])
        h, w, _e = _derive(work_id=12)
        self.assertEqual(h["work_id"], "12")
        self.assertIn("work_id 12 非 str，热列存 str 兜底", w)
        self.assertEqual(_derive(sources=[{"channel": "quick"}])[0]["origin_trust"], "hand")
        self.assertEqual(_derive(sources=None)[0]["origin_trust"], "proposed")

    def test_warning_order_follows_columns(self):
        _h, w, _e = _derive(prev_status="bad", tier="T9", title=1, deadline="x", target_repo=2, work_id=3)
        kinds = [x.split(" ")[0] for x in w]
        self.assertEqual(kinds, ["prev_status", "tier", "title", "deadline", "target_repo", "work_id"])


if __name__ == "__main__":
    unittest.main()
