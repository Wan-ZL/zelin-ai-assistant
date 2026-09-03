"""golden_eval — the helpers split out in P3b (§45 provenance back-test).

Pins: label normalisation + noise rule, the card row's first-source fallback,
the JSONL reader on blank / broken / non-object lines, the tolerant JSON
array parser, verdict indexing (non-dict / id-less items dropped), the
meeting split stamping channel_api, the tally's four label paths, and the
CLI dispatch incl. ``all`` stopping at the first failing step.
"""
import io
import json
import unittest
from types import SimpleNamespace
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first

from act import golden_eval as ge
from act.lib import provenance


class LabelAndRowTestCase(unittest.TestCase):
    def test_label_helpers(self):
        self.assertEqual(ge._norm_state(None), "")
        self.assertEqual(ge._norm_state(" Trashed "), "trashed")
        self.assertTrue(ge._is_noise("trashed", ""))
        self.assertTrue(ge._is_noise("archived", "detected"))
        self.assertFalse(ge._is_noise("archived", "card_sent"))
        self.assertFalse(ge._is_noise("detected", ""))
        self.assertEqual(ge.label_card("archived", "delivered"), ge.LABEL_REAL)
        self.assertEqual(ge.label_card("card_sent", None), ge.LABEL_PENDING)
        self.assertEqual(ge.label_card("ARCHIVED", "DETECTED"), ge.LABEL_NOISE)

    def test_card_row_and_first_source(self):
        r = SimpleNamespace(id=7, title="t", status=None, prev_status="", sources=["junk"])
        self.assertEqual(ge._first_source(r), {})
        row = ge._card_row(r)
        self.assertEqual((row["id"], row["status"], row["channel"], row["label"]), ("7", "", None, ge.LABEL_PENDING))
        self.assertNotIn("prev_status", row)
        r = SimpleNamespace(id="P-1", title="t", status="trashed", prev_status="review",
                            sources=[{"channel": "meeting", "quote": "q", "who": "w", "date": "d"}])
        row = ge._card_row(r)
        self.assertEqual((row["channel"], row["quote"], row["who"], row["date"], row["prev_status"], row["label"]),
                         ("meeting", "q", "w", "d", "review", ge.LABEL_REAL))


class ParsersTestCase(unittest.TestCase):
    def test_parse_card_line(self):
        self.assertIsNone(ge._parse_card_line("   "))
        self.assertIsNone(ge._parse_card_line("{broken"))
        self.assertIsNone(ge._parse_card_line("[1, 2]"))
        self.assertEqual(ge._parse_card_line(' {"id": "a"} '), {"id": "a"})

    def test_load_cards_reads_only_objects(self):
        path = ge._cards_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"id": "a"}\n\nnot json\n[1]\n{"id": "b"}\n', encoding="utf-8")
        try:
            self.assertEqual([c["id"] for c in ge._load_cards()], ["a", "b"])
        finally:
            path.unlink()
        self.assertEqual(ge._load_cards(), [])

    def test_parse_json_array(self):
        self.assertEqual(ge._parse_json_array("noise [1, 2] tail"), [1, 2])
        self.assertEqual(ge._parse_json_array("no brackets"), [])
        self.assertEqual(ge._parse_json_array("] before ["), [])
        self.assertEqual(ge._parse_json_array("[broken"), [])
        self.assertEqual(ge._parse_json_array('{"a": [1]}'), [1])
        self.assertEqual(ge._json_list('{"a": 1}'), [])
        self.assertEqual(ge._json_list("nope"), [])

    def test_verdicts_by_id_and_extract_items(self):
        items = [{"id": 1, "p": "x"}, {"noid": 1}, "junk", {"id": "a"}]
        self.assertEqual(ge._verdicts_by_id(items), {"1": {"id": 1, "p": "x"}, "a": {"id": "a"}})
        self.assertEqual(ge._extract_items(lambda p: SimpleNamespace(stdout='[{"id": 1}]'), "p"), [{"id": 1}])
        self.assertEqual(ge._extract_items(lambda p: SimpleNamespace(stdout=None), "p"), [])

        def boom(_p):
            raise OSError("no claude")

        self.assertEqual(ge._extract_items(boom, "p"), [])
        self.assertEqual(ge._card_block({"id": "x", "title": "t", "quote": None, "who": "w"}),
                         "--- 卡 x ---\ntitle: t\nquote: None\nwho: w")


class ClassifyAndScoreTestCase(unittest.TestCase):
    def test_split_meeting_cards(self):
        cards = [{"channel": " MEETING "}, {"channel": "slack", "speaker": "x"}, {"channel": None}]
        meeting = ge._split_meeting_cards(cards)
        self.assertEqual(meeting, [cards[0]])
        self.assertEqual(cards[1], {"channel": "slack", "provenance": ge.CHANNEL_API})
        self.assertEqual(cards[2]["provenance"], ge.CHANNEL_API)

    def test_apply_verdict(self):
        c = {}
        ge._apply_verdict(c, {"provenance": "audio", "speaker": "human"})
        self.assertEqual(c, {"provenance": "audio", "speaker": "human"})
        ge._apply_verdict(c, "junk")
        self.assertEqual(c, {"provenance": "unknown", "speaker": "unknown"})

    def test_would_be_born(self):
        self.assertTrue(ge._would_be_born({"provenance": ge.CHANNEL_API}))
        self.assertTrue(ge._would_be_born({"provenance": "audio", "speaker": "human"}))
        self.assertFalse(ge._would_be_born({"provenance": "screen", "speaker": "assistant"}))
        self.assertEqual(provenance.verdict("screen", "assistant"), provenance.CORROBORATE)

    def test_tally_paths(self):
        t = ge._Tally()
        t.add({"id": 1, "label": ge.LABEL_REAL}, True)
        t.add({"id": 2, "label": ge.LABEL_REAL}, False)
        t.add({"id": 3, "label": ge.LABEL_NOISE}, True)
        t.add({"id": 4, "label": ge.LABEL_NOISE}, False)
        t.add({"id": 5, "label": ge.LABEL_PENDING}, True)
        t.add({"id": 6, "label": None}, False)
        rep = t.report(6)
        self.assertEqual((rep["born"], rep["blocked"], rep["real_born"]), (3, 3, 1))
        self.assertEqual(rep["real_blocked"]["cards"], [{"id": 2, "title": None}])
        self.assertEqual(rep["noise_born"]["count"], 1)
        self.assertEqual(rep["noise_blocked"]["count"], 1)
        self.assertEqual(rep["pending"], {"count": 2, "blocked": 1})
        self.assertEqual(ge.score_cards([])["total"], 0)


class CliTestCase(unittest.TestCase):
    def test_dispatch_and_all(self):
        with mock.patch.object(ge, "cmd_build", return_value=0) as b, \
                mock.patch.object(ge, "cmd_classify", return_value=1) as c, \
                mock.patch.object(ge, "cmd_score", return_value=0) as s:
            self.assertEqual(ge._main(["classify"]), 1)
            self.assertEqual(ge._main(["score"]), 0)
            self.assertEqual(ge._main(["all"]), 1)       # stops after classify fails
        self.assertEqual((b.call_count, c.call_count, s.call_count), (1, 2, 1))
        with mock.patch.object(ge, "cmd_build", return_value=0), \
                mock.patch.object(ge, "cmd_classify", return_value=0), \
                mock.patch.object(ge, "cmd_score", return_value=0):
            self.assertEqual(ge._run_all(), 0)
        with mock.patch.object(ge, "cmd_build", return_value=3):
            self.assertEqual(ge._run_all(), 3)
        with mock.patch("sys.stderr", io.StringIO()):
            with self.assertRaises(SystemExit):
                ge._main([])
        self.assertIsInstance(json.dumps({"ok": True}), str)


if __name__ == "__main__":
    unittest.main()
