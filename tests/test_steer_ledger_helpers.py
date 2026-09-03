"""steer — the ledger / queue helpers split out of enqueue, mark_delivered and
delivered_entries (§39 steer relay, M8.3 C-3 ledger shape).

Pins: text normalisation (non-str / blank / clip), the stamp fallback, the
two-sided duplicate check, ledger survival rules for dict vs bare-key entries,
in-batch dedupe of delivered rows (text clipped, ts defaulted), projection
rules (missing text/ts/delivered_at), and the dirty-entry parser.
"""
import unittest
from unittest import mock

from act.lib import steer
from act.lib.registry import Requirement


def _card(**kw):
    base = dict(id="R-901", title="steer helpers", status="executing")
    base.update(kw)
    return Requirement.from_dict(base)


class NormaliseTestCase(unittest.TestCase):
    def test_text_normalisation(self):
        self.assertIsNone(steer._normalize_steer_text(None))
        self.assertIsNone(steer._normalize_steer_text(42))
        self.assertIsNone(steer._normalize_steer_text("  \n"))
        self.assertEqual(steer._normalize_steer_text("  go  "), "go")
        long = "x" * (steer.MAX_STEER_CHARS + 5)
        self.assertEqual(steer._normalize_steer_text(long), "x" * steer.MAX_STEER_CHARS)
        exact = "y" * steer.MAX_STEER_CHARS
        self.assertEqual(steer._normalize_steer_text(exact), exact)

    def test_stamp_or_now(self):
        self.assertEqual(steer._stamp_or_now("2026-01-01T00:00:00Z"), "2026-01-01T00:00:00Z")
        with mock.patch.object(steer, "_iso_now", return_value="NOW"):
            self.assertEqual(steer._stamp_or_now(None), "NOW")
            self.assertEqual(steer._stamp_or_now("   "), "NOW")
            self.assertEqual(steer._stamp_or_now(123), "NOW")

    def test_is_duplicate_checks_both_sides(self):
        pend = [{"key": "p1"}]
        ex = {"delivered_steers": [{"key": "d1"}, "bare2"]}
        self.assertTrue(steer._is_duplicate("p1", pend, ex))
        self.assertTrue(steer._is_duplicate("d1", pend, ex))
        self.assertTrue(steer._is_duplicate("bare2", pend, ex))
        self.assertFalse(steer._is_duplicate("new", pend, ex))
        self.assertFalse(steer._is_duplicate("new", [], {}))

    def test_push_with_cap_evicts_oldest_with_trace(self):
        req = _card()
        pend = [{"key": f"k{i}", "text": f"t{i}", "ts": "t"} for i in range(steer.PENDING_CAP)]
        steer._push_with_cap(req, pend, {"key": "new", "text": "newest", "ts": "t"})
        self.assertEqual(len(pend), steer.PENDING_CAP)
        self.assertEqual(pend[0]["key"], "k1")
        self.assertEqual(pend[-1]["key"], "new")
        self.assertIn("队列已满", req.notes)
        self.assertIn("t0", req.notes)


class PendingParserTestCase(unittest.TestCase):
    def test_clean_note_shapes(self):
        self.assertIsNone(steer._clean_note("bare"))
        self.assertIsNone(steer._clean_note({"text": 12}))
        self.assertIsNone(steer._clean_note({"text": "   "}))
        note = steer._clean_note({"text": " body ", "ts": None, "key": ""})
        self.assertEqual(note, {"class": steer.STEER_CLASS, "text": "body", "ts": "",
                                "key": steer.steer_key("body", "")})
        kept = steer._clean_note({"text": "b", "ts": "t9", "key": "explicit"})
        self.assertEqual((kept["ts"], kept["key"]), ("t9", "explicit"))

    def test_note_key_prefers_explicit(self):
        self.assertEqual(steer._note_key({"key": "k"}, "b", "t"), "k")
        self.assertEqual(steer._note_key({"key": None}, "b", "t"), steer.steer_key("b", "t"))
        self.assertEqual(steer._note_key({}, "b", "t"), steer.steer_key("b", "t"))


class DeliveredLedgerTestCase(unittest.TestCase):
    def test_entry_key(self):
        self.assertEqual(steer._entry_key({"key": "k"}), "k")
        self.assertIsNone(steer._entry_key({}))
        self.assertEqual(steer._entry_key("bare"), "bare")

    def test_surviving_ledger(self):
        old = [{"key": "a"}, {"key": "sent"}, "bare", "sent", {"nokey": 1}, None, ""]
        self.assertEqual(steer._surviving_ledger(old, {"sent"}), [{"key": "a"}, "bare"])
        self.assertEqual(steer._surviving_ledger("oops", {"x"}), [])
        self.assertEqual(steer._surviving_ledger(None, set()), [])

    def test_delivered_rows_dedupe_clip_and_default_ts(self):
        notes = [
            {"key": "k1", "text": "x" * (steer.TRACE_CLIP + 10)},
            {"key": "k1", "text": "dup"},
            {"key": "k2", "text": "two", "ts": "t2"},
        ]
        rows = steer._delivered_rows(notes, "NOW")
        self.assertEqual([r["key"] for r in rows], ["k1", "k2"])
        self.assertEqual(rows[0]["text"], "x" * steer.TRACE_CLIP)
        self.assertEqual(rows[0]["ts"], "")
        self.assertEqual(rows[1], {"key": "k2", "text": "two", "ts": "t2", "delivered_at": "NOW"})

    def test_mark_delivered_empty_batch_is_noop(self):
        req = _card(execution={"steer_count": 3})
        steer.mark_delivered(req, [])
        self.assertEqual(req.execution, {"steer_count": 3})

    def test_mark_delivered_tolerates_dirty_count(self):
        req = _card(execution={"steer_count": None})
        a = steer.enqueue_steer(req, "one", ts="t1")
        steer.mark_delivered(req, [a], delivered_at="D")
        self.assertEqual(req.execution["steer_count"], 1)


class ProjectionTestCase(unittest.TestCase):
    def test_has_text_and_ts(self):
        self.assertTrue(steer._has_text_and_ts({"text": "a", "ts": "t"}))
        self.assertFalse(steer._has_text_and_ts({"text": "a", "ts": " "}))
        self.assertFalse(steer._has_text_and_ts({"text": "", "ts": "t"}))
        self.assertFalse(steer._has_text_and_ts({"text": 1, "ts": "t"}))
        self.assertFalse(steer._has_text_and_ts({"text": "a", "ts": 5}))

    def test_projectable(self):
        self.assertIsNone(steer._projectable("bare|key"))
        self.assertIsNone(steer._projectable({"text": "a"}))
        row = steer._projectable({"text": "a", "ts": "t", "key": None, "delivered_at": ""})
        self.assertEqual(row, {"key": "", "text": "a", "ts": "t", "delivered_at": None})
        row = steer._projectable({"text": "a", "ts": "t", "key": "k", "delivered_at": 1})
        self.assertEqual(row["delivered_at"], "1")

    def test_delivered_entries_non_list(self):
        self.assertEqual(steer.delivered_entries(_card(execution={"delivered_steers": {}})), [])
        self.assertEqual(steer.delivered_entries(_card(execution=None)), [])


if __name__ == "__main__":
    unittest.main()
