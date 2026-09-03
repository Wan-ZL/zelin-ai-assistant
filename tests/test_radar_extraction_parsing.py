"""act/radar — extraction-output parsing and item sanitising (§47 / 宪法第 11 条)
plus loop mode of the CLI.

Pinned (P3 mutation net):
- ``_find_json_array``: prose brackets before/after the array do not break
  it; an array WITH dicts beats an earlier ``[]``; only the first legal
  array is the fallback; no array -> None;
- ``_salvage_truncated_array``: whole prefix objects recovered from a
  stream cut mid-object; non-dict items skipped; text without ``[`` -> [];
  a clean array yields every object;
- ``_parse_extraction``: fence stripped, ``[]`` is valid-empty, an all-string
  array is malformed (None), a mixed array keeps its dicts, non-list -> None;
- ``_to_requirement``: tier/hardness whitelists, bool cost is not a cost,
  numeric title is dropped to "", non-string quote -> None, who = note stem;
- ``_clean_deadline`` / ``_extractor_urgent`` edge values;
- ``_is_transient_error`` / ``_is_note_level_error`` classification;
- ``_dump_debug_raw`` keeps only the newest _DEBUG_KEEP files;
- loop mode: ``_loop_forever`` prints each summary, one line per failed scan,
  sleeps ``interval``; ``main`` without --once enters it.
"""
import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import radar
from act.lib import config


class FindJsonArrayTestCase(unittest.TestCase):
    def test_prose_brackets_around_the_array(self):
        text = 'Note [from the meeting]: [{"title": "x"}] see [1].'
        self.assertEqual(radar._find_json_array(text), [{"title": "x"}])

    def test_dict_array_beats_earlier_empty_array(self):
        self.assertEqual(radar._find_json_array('[] then [{"a": 1}]'), [{"a": 1}])

    def test_first_legal_array_is_the_fallback(self):
        self.assertEqual(radar._find_json_array("[1] and [2]"), [1])
        self.assertEqual(radar._find_json_array("[oops] and []"), [])
        self.assertIsNone(radar._find_json_array("nothing here"))
        self.assertIsNone(radar._find_json_array("[unterminated"))


class SalvageTestCase(unittest.TestCase):
    def test_truncated_stream(self):
        text = '[{"title": "a"}, {"title": "b"}, {"title": "c", "quo'
        self.assertEqual(radar._salvage_truncated_array(text),
                         [{"title": "a"}, {"title": "b"}])

    def test_whole_array_and_edge_inputs(self):
        self.assertEqual(radar._salvage_truncated_array('[{"t": 1}, 5, {"t": 2}]'),
                         [{"t": 1}, {"t": 2}])
        self.assertEqual(radar._salvage_truncated_array("no array"), [])
        self.assertEqual(radar._salvage_truncated_array(""), [])
        self.assertEqual(radar._salvage_truncated_array(None), [])
        self.assertEqual(radar._salvage_truncated_array("[]"), [])
        self.assertEqual(radar._salvage_truncated_array('[{"t": 1}] trailing {"t": 9}'),
                         [{"t": 1}])           # stops at the closing bracket


class ParseExtractionTestCase(unittest.TestCase):
    def test_shapes(self):
        self.assertEqual(radar._parse_extraction('```json\n[{"title": "x"}]\n```'),
                         [{"title": "x"}])
        self.assertEqual(radar._parse_extraction("[]"), [])
        self.assertEqual(radar._parse_extraction("  \n"), None)
        self.assertIsNone(radar._parse_extraction('["do X by friday"]'))
        self.assertEqual(radar._parse_extraction('[{"title": "x"}, "str"]'), [{"title": "x"}])
        self.assertIsNone(radar._parse_extraction('{"title": "x"}'))
        self.assertIsNone(radar._parse_extraction("prose without arrays"))
        self.assertEqual(radar._parse_extraction('Sure: [{"title": "x"}]'), [{"title": "x"}])


class ToRequirementTestCase(unittest.TestCase):
    NOTE = Path("/vault/2026-07-13-standup.md")

    def test_field_sanitising(self):
        req = radar._to_requirement({
            "title": 12345, "type": None, "tier": "T9", "hardness": "firm",
            "deadline": True, "cost_estimate_usd": True, "quote": {"x": 1},
        }, self.NOTE)
        self.assertEqual(req.title, "")
        self.assertEqual(req.type, "")
        self.assertEqual(req.tier, "T1")
        self.assertEqual(req.hardness, "soft")
        self.assertIsNone(req.deadline)
        self.assertIsNone(req.cost_estimate_usd)
        self.assertIsNone(req.sources[0]["quote"])
        self.assertEqual(req.sources[0]["who"], "2026-07-13-standup")
        self.assertEqual(req.sources[0]["date"], "2026-07-13")
        self.assertEqual(req.sources[0]["channel"], "meeting")
        self.assertEqual(req.status, "detected")
        self.assertEqual(req.repeated_mentions, 1)

    def test_clean_values_pass_through(self):
        req = radar._to_requirement({
            "title": "  Send the deck  " + "x" * 100, "type": " code ", "tier": "T0",
            "hardness": "hard", "deadline": "2026-07-20", "cost_estimate_usd": 2.5,
            "quote": "please send",
        }, Path("undated-note.md"))
        self.assertEqual(len(req.title), 80)
        self.assertEqual(req.type, "code")
        self.assertEqual(req.tier, "T0")
        self.assertEqual(req.hardness, "hard")
        self.assertEqual(req.deadline, "2026-07-20")
        self.assertEqual(req.cost_estimate_usd, 2.5)
        self.assertIsNone(req.sources[0]["date"])
        self.assertTrue(radar._is_high_confidence(req))

    def test_deadline_and_urgent_edges(self):
        for bad in (None, True, 5, "next friday", "2026-13-99", " null ", "None", ""):
            self.assertIsNone(radar._clean_deadline(bad), bad)
        self.assertEqual(radar._clean_deadline(" 2026-07-20 "), "2026-07-20")
        self.assertTrue(radar._extractor_urgent({}))
        self.assertTrue(radar._extractor_urgent({"urgent": None}))
        self.assertTrue(radar._extractor_urgent({"urgent": "yes"}))
        self.assertTrue(radar._extractor_urgent({"urgent": 1}))
        for falsy in ("false", " No ", "0", "none", "NULL", "", False, 0):
            self.assertFalse(radar._extractor_urgent({"urgent": falsy}), falsy)


class ErrorClassTestCase(unittest.TestCase):
    def test_transient_and_note_level(self):
        self.assertTrue(radar._is_transient_error("claude exit 143: killed"))
        self.assertTrue(radar._is_transient_error("RuntimeError: exit -15"))
        self.assertTrue(radar._is_transient_error("getaddrinfo ENOTFOUND api"))
        self.assertFalse(radar._is_transient_error("claude exit 1: bad prompt"))
        self.assertFalse(radar._is_transient_error(None))
        self.assertTrue(radar._is_note_level_error("unparseable extraction on a.md"))
        self.assertTrue(radar._is_note_level_error("unreadable note a.md: boom"))
        self.assertTrue(radar._is_note_level_error("filing failed on a.md: X"))
        self.assertTrue(radar._is_note_level_error("claude -p failed on a.md: TimeoutExpired: x"))
        self.assertFalse(radar._is_note_level_error("claude -p failed on a.md: RuntimeError: exit 1"))
        self.assertFalse(radar._is_note_level_error(None))


class DebugDumpTestCase(unittest.TestCase):
    def test_rotation(self):
        config.ensure_state_dirs()
        debug_dir = config.STATE_DIR / radar.DEBUG_DIR_NAME
        for p in debug_dir.glob("*.txt") if debug_dir.exists() else []:
            p.unlink()
        with mock.patch.object(radar, "_DEBUG_KEEP", 3):
            for i in range(5):
                radar._dump_debug_raw(Path(f"/v/note-{i}.md"), f"raw {i}")
        kept = sorted(debug_dir.glob("*.txt"))
        self.assertEqual(len(kept), 3)
        self.assertTrue(any(p.name.endswith("note-4.txt") for p in kept))
        self.assertFalse(any(p.name.endswith("note-0.txt") for p in kept))
        for p in kept:
            p.unlink()


class LoopModeTestCase(unittest.TestCase):
    def test_loop_prints_and_sleeps_until_interrupted(self):
        summaries = iter([{"cards": 1}, RuntimeError("vault gone"), {"cards": 2}])

        def fake_scan():
            item = next(summaries)
            if isinstance(item, Exception):
                raise item
            return item

        sleeps = []

        def fake_sleep(secs):
            sleeps.append(secs)
            if len(sleeps) == 3:
                raise KeyboardInterrupt

        buf = io.StringIO()
        with mock.patch.object(radar, "scan", fake_scan), \
                mock.patch.object(radar.time, "sleep", fake_sleep), \
                redirect_stdout(buf), self.assertRaises(KeyboardInterrupt):
            radar._loop_forever(7)
        self.assertEqual(sleeps, [7, 7, 7])
        self.assertEqual(buf.getvalue().splitlines(),
                         ['{"cards": 1}', "radar scan failed: vault gone", '{"cards": 2}'])

    def test_main_once_and_loop_dispatch(self):
        buf = io.StringIO()
        with mock.patch.object(radar, "scan", return_value={"cards": 0}), \
                redirect_stdout(buf):
            self.assertEqual(radar.main(["--once"]), 0)
        self.assertEqual(json.loads(buf.getvalue()), {"cards": 0})
        cfg = config.Config()
        cfg.poll_interval_seconds = 42
        with mock.patch.object(radar, "_loop_forever") as loop, \
                mock.patch.object(config, "load_config", return_value=cfg):
            radar.main([])
            loop.assert_called_once_with(42)
            radar.main(["--interval", "5"])
            loop.assert_called_with(5)
        cfg.poll_interval_seconds = 0
        with mock.patch.object(radar, "_loop_forever") as loop, \
                mock.patch.object(config, "load_config", return_value=cfg):
            radar.main([])
            loop.assert_called_once_with(10)


if __name__ == "__main__":
    unittest.main()
