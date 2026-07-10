"""radar.scan — Obsidian extraction loop against a tmpdir vault (P1-12).

The injectable ``runner`` replaces the headless ``claude -p`` call (the
manager action-items pack auto-skips when a runner is injected, so no other
subprocess can fire). Pinned here:

- strict-JSON output -> requirement reconciled through merge_or_new, hard +
  deadline routes straight to card_sent, marker advances to the note's mtime;
- the marker is a watermark of SUCCESSFULLY processed notes: a claude failure
  or unparseable output pins it before the failed note (silently dropping a
  note is the radar's worst failure), later notes still scan, and the re-scan
  after recovery is idempotent (no duplicate cards, no mention inflation);
- a valid ``[]`` is NOT a failure — the marker advances normally;
- notes at-or-before the marker are never re-read.

Runs entirely inside the sandbox AIASSISTANT_HOME (tests/__init__.py).
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import radar
from act import __version__ as act_version
from act.lib import analytics, config, registry

BASE = 1_760_000_000.0  # fixed epoch — deterministic mtimes


def _item(title, hardness="soft", deadline=None, quote="do the thing"):
    return {"title": title, "type": "report", "tier": "T1",
            "hardness": hardness, "deadline": deadline,
            "cost_estimate_usd": None, "quote": quote}


class RadarScanBase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        self._cleanup()
        self.tmp = tempfile.TemporaryDirectory(prefix="radar-vault-")
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self._cleanup)
        self.raw = Path(self.tmp.name) / "2 - raw"
        self.raw.mkdir(parents=True)
        config.CONFIG_PATH.write_text(
            f'sources:\n  obsidian_raw: "{self.raw}"\n', encoding="utf-8")

    @staticmethod
    def _cleanup():
        if config.CONFIG_PATH.exists():
            config.CONFIG_PATH.unlink()
        marker = config.STATE_DIR / radar.MARKER_PATH_NAME
        if marker.exists():
            marker.unlink()
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()

    def _note(self, name, text, mtime):
        p = self.raw / name
        p.write_text(text, encoding="utf-8")
        os.utime(p, (mtime, mtime))
        return p


# --------------------------------------------------------------------------- #
# happy path — extraction -> merge_or_new -> marker
# --------------------------------------------------------------------------- #
class ExtractionTestCase(RadarScanBase):
    def test_hard_deadline_item_becomes_card_and_marker_advances(self):
        note = self._note("2026-07-08 weekly sync.md",
                          "Boss: ship the Q3 report by July 20", BASE)
        runner = lambda text: json.dumps(  # noqa: E731
            [_item("Ship the Q3 quarterly report", hardness="hard",
                   deadline="2026-07-20", quote="ship the Q3 report by July 20")])
        summary = radar.scan(runner=runner)
        self.assertEqual(summary["files_scanned"], 1)
        self.assertEqual(summary["extracted"], 1)
        self.assertEqual(summary["reconciled"], 1)
        self.assertEqual(summary["cards"], 1)  # hard + deadline = high confidence

        reqs = registry.load_all()
        self.assertEqual(len(reqs), 1)
        req = reqs[0]
        self.assertEqual(req.status, "card_sent")
        self.assertEqual(req.deadline, "2026-07-20")
        src = req.sources[0]
        self.assertEqual(src["channel"], "meeting")
        self.assertEqual(src["who"], "manager")
        self.assertEqual(src["date"], "2026-07-08")  # from the filename
        self.assertEqual(src["ref"], str(note))

        self.assertEqual(radar._read_marker(), BASE)
        # nothing new -> second scan touches no file
        second = radar.scan(runner=lambda t: self.fail("re-read a scanned note"))
        self.assertEqual(second["files_scanned"], 0)

    def test_soft_item_lands_as_detected_debt(self):
        self._note("2026-07-08 note.md", "maybe tidy the wiki", BASE)
        summary = radar.scan(runner=lambda t: json.dumps([_item("Tidy the wiki")]))
        self.assertEqual(summary["cards"], 0)
        self.assertEqual(registry.load_all()[0].status, "detected")

    def test_null_deadline_string_is_not_high_confidence(self):
        self._note("n.md", "x", BASE)
        summary = radar.scan(runner=lambda t: json.dumps(
            [_item("Do the hard thing", hardness="hard", deadline="null")]))
        self.assertEqual(summary["cards"], 0)
        req = registry.load_all()[0]
        self.assertIsNone(req.deadline)
        self.assertEqual(req.status, "detected")

    def test_item_without_title_is_ignored(self):
        self._note("n.md", "x", BASE)
        summary = radar.scan(runner=lambda t: json.dumps([{"quote": "no title"}]))
        self.assertEqual(summary["extracted"], 1)
        self.assertEqual(summary["reconciled"], 0)
        self.assertEqual(registry.load_all(), [])
        self.assertEqual(radar._read_marker(), BASE)  # processed, just empty

    def test_fenced_json_is_parsed(self):
        self._note("n.md", "x", BASE)
        runner = lambda t: "```json\n" + json.dumps([_item("Fenced item")]) + "\n```"  # noqa: E731
        summary = radar.scan(runner=runner)
        self.assertEqual(summary["reconciled"], 1)


# --------------------------------------------------------------------------- #
# watermark semantics — failures never lose notes
# --------------------------------------------------------------------------- #
class WatermarkTestCase(RadarScanBase):
    def test_malformed_output_pins_marker_before_failed_note(self):
        self._note("a AAA.md", "AAA content", BASE)
        self._note("b BBB.md", "BBB content", BASE + 10)

        def flaky(text):
            if "AAA" in text:
                return "Sorry, I can't produce JSON for that."
            return json.dumps([_item("Requirement from note B")])

        summary = radar.scan(runner=flaky)
        self.assertEqual(summary["files_scanned"], 2)  # scan survives the bad note
        self.assertEqual(summary["reconciled"], 1)     # B still processed
        self.assertTrue(any("unparseable extraction on a AAA.md" in s
                            for s in summary["skipped"]))
        # marker must NOT pass the unprocessed note A
        self.assertLess(radar._read_marker(), BASE)

        # recovery scan: A now parses; B's re-extraction is idempotent
        def fixed(text):
            if "AAA" in text:
                return "[]"
            return json.dumps([_item("Requirement from note B")])

        second = radar.scan(runner=fixed)
        self.assertEqual(second["files_scanned"], 2)   # both retried
        self.assertEqual(radar._read_marker(), BASE + 10)
        reqs = registry.load_all()
        self.assertEqual(len(reqs), 1)                 # no duplicate card
        self.assertEqual(reqs[0].repeated_mentions, 1)  # identical source deduped

    def test_runner_exception_pins_marker_and_scan_survives(self):
        self._note("a AAA.md", "AAA content", BASE)
        self._note("b BBB.md", "BBB content", BASE + 10)

        def flaky(text):
            if "AAA" in text:
                raise RuntimeError("claude exit 1: transient")
            return json.dumps([_item("Requirement from note B")])

        summary = radar.scan(runner=flaky)
        self.assertEqual(summary["reconciled"], 1)
        self.assertTrue(any("claude -p failed on a AAA.md" in s
                            for s in summary["skipped"]))
        self.assertLess(radar._read_marker(), BASE)

    def test_watermark_stops_at_last_good_note_before_failure(self):
        self._note("a AAA.md", "AAA content", BASE)
        self._note("b BBB.md", "BBB content", BASE + 10)
        self._note("c CCC.md", "CCC content", BASE + 20)

        def flaky(text):
            if "BBB" in text:
                raise RuntimeError("boom")
            return "[]"

        radar.scan(runner=flaky)
        # A processed; B failed pins the watermark; C scanned but not marked
        self.assertEqual(radar._read_marker(), BASE)

    def test_valid_empty_array_advances_marker(self):
        # "[]" is the prompt's own no-requirements answer — NOT a failure
        self._note("quiet note.md", "nothing new here", BASE)
        summary = radar.scan(runner=lambda t: "[]")
        self.assertEqual(summary["extracted"], 0)
        self.assertEqual(summary["skipped"], [])
        self.assertEqual(radar._read_marker(), BASE)

    def test_notes_at_or_before_marker_are_skipped(self):
        self._note("old.md", "already seen", BASE)
        radar._write_marker(BASE)
        summary = radar.scan(
            runner=lambda t: self.fail("re-read a note behind the marker"))
        self.assertEqual(summary["files_scanned"], 0)


# --------------------------------------------------------------------------- #
# preflight guards
# --------------------------------------------------------------------------- #
class PreflightTestCase(RadarScanBase):
    def test_feature_flag_off_skips_scan(self):
        config.CONFIG_PATH.write_text(
            f'sources:\n  obsidian_raw: "{self.raw}"\n'
            "features:\n  obsidian_radar: false\n", encoding="utf-8")
        self._note("n.md", "x", BASE)
        summary = radar.scan(runner=lambda t: self.fail("scanned while off"))
        self.assertIn("features.obsidian_radar is off", summary["skipped"])
        self.assertEqual(summary["files_scanned"], 0)

    def test_missing_vault_dir_is_reported_not_fatal(self):
        gone = Path(self.tmp.name) / "no-such-dir"
        config.CONFIG_PATH.write_text(
            f'sources:\n  obsidian_raw: "{gone}"\n', encoding="utf-8")
        summary = radar.scan(runner=lambda t: self.fail("scanned a missing vault"))
        self.assertTrue(any(s.startswith("obsidian_raw not found")
                            for s in summary["skipped"]))


# --------------------------------------------------------------------------- #
# _parse_extraction — [] (valid empty) vs None (malformed, retry the note)
# --------------------------------------------------------------------------- #
class ParseExtractionTestCase(unittest.TestCase):
    def test_strict_array(self):
        self.assertEqual(radar._parse_extraction('[{"title": "x"}]'),
                         [{"title": "x"}])

    def test_valid_empty_array(self):
        self.assertEqual(radar._parse_extraction("[]"), [])

    def test_fenced_array(self):
        self.assertEqual(radar._parse_extraction('```json\n[{"title": "x"}]\n```'),
                         [{"title": "x"}])

    def test_array_embedded_in_prose(self):
        raw = 'Here you go:\n[{"title": "x"}]\nHope that helps!'
        self.assertEqual(radar._parse_extraction(raw), [{"title": "x"}])

    def test_non_dict_items_are_filtered(self):
        self.assertEqual(radar._parse_extraction('["junk", {"title": "x"}]'),
                         [{"title": "x"}])

    def test_empty_output_is_malformed(self):
        self.assertIsNone(radar._parse_extraction(""))
        self.assertIsNone(radar._parse_extraction("   \n"))

    def test_prose_without_array_is_malformed(self):
        self.assertIsNone(radar._parse_extraction("I could not find any."))

    def test_non_array_json_is_malformed(self):
        self.assertIsNone(radar._parse_extraction('{"title": "x"}'))


class ManagerActionItemsOutcomeTestCase(unittest.TestCase):
    """manager_action_items telemetry (docs/TELEMETRY.md): every real attempt
    (past the feature/keyword gates) logs ONE meeting_action_items event with
    outcome ok|fail (+ a failures.py id when the error classifies); the gate
    exits stay silent so skipped notes are never counted as attempts."""

    def setUp(self):
        config.ensure_state_dirs()
        if analytics.EVENTS_PATH.exists():
            analytics.EVENTS_PATH.unlink()
        self.tmp = tempfile.TemporaryDirectory(prefix="radar-meetings-")
        self.addCleanup(self.tmp.cleanup)
        patcher = mock.patch.object(radar.notify, "notify", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.cfg = config.Config()
        self.cfg.watch_people = ["boss"]
        # explicit workbench -> drafts land in the tmp dir, no fallback notice
        self.cfg.default_target_repo = self.tmp.name + "/workbench"
        self.cfg.default_target_repo_configured = True
        self.note = Path(self.tmp.name) / "2026-07-08-sync.md"

    def _events(self):
        return [e for e in analytics.read_events()
                if e.get("event") == "meeting_action_items"]

    def test_ok_outcome_logged_with_writer_version(self):
        path = radar.manager_action_items(
            self.note, "boss said do X", self.cfg, runner=lambda t: "- do X")
        self.assertIsNotNone(path)
        (ev,) = self._events()
        self.assertEqual(ev["outcome"], "ok")
        self.assertNotIn("failure", ev)
        self.assertEqual(ev["v"], act_version)  # writer-level version stamp

    def test_empty_result_logs_fail(self):
        out = radar.manager_action_items(
            self.note, "boss said do X", self.cfg, runner=lambda t: "")
        self.assertIsNone(out)
        (ev,) = self._events()
        self.assertEqual(ev["outcome"], "fail")

    def test_exception_logs_classified_failure(self):
        def runner(_):
            raise RuntimeError("Connection refused by api.anthropic.com")
        out = radar.manager_action_items(self.note, "boss said do X",
                                         self.cfg, runner=runner)
        self.assertIsNone(out)
        (ev,) = self._events()
        self.assertEqual(ev["outcome"], "fail")
        self.assertEqual(ev["failure"], "network_error")

    def test_gate_exits_stay_silent(self):
        # no manager keyword in the note
        radar.manager_action_items(self.note, "nothing relevant here",
                                   self.cfg, runner=lambda t: "- x")
        # feature flag off
        cfg_off = config.Config()
        cfg_off.watch_people = ["boss"]
        cfg_off.features = dict(cfg_off.features, manager_pack=False)
        radar.manager_action_items(self.note, "boss said do X",
                                   cfg_off, runner=lambda t: "- x")
        self.assertEqual(self._events(), [])


if __name__ == "__main__":
    unittest.main()
