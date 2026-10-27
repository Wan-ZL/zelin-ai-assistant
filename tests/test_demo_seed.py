"""scripts/demo_seed.py — the fictional meeting-recap fixture (v2.0.0; CONTRACT §63 / §66.4).

The seeder feeds the web visual goldens, the e2e specs, scripts/dev-preview.sh and
readme_audit ``--render``. Pinned behavior:
  - every scene builds clean (its own validator) and the files it writes round-trip
    through ``act/lib/recap_store.snapshot`` (= GET /api/recaps): every recap key and
    the open meeting are listed, the lanes count what the marks say;
  - the vocabularies it repeats (qualities, open/closed) equal the real constants, and
    the paste-ready ``copy_*`` / ``problems`` are exactly what ``recap_text`` renders
    and finds, so the fixture cannot drift from the daemon's own output;
  - ``quality == "ok"`` records pass the daemon's gate; the needs_review draft fails it;
  - every meeting starts in the past; a fixed ``--now`` gives byte-identical files;
  - seeding replaces the recap files it owns (a stale file from an older run is gone);
  - the data is fictional: no e-mail address, no real company or person.
In-process (module imported from scripts/) except the one CLI round trip.
"""
from __future__ import annotations

import datetime as dt
import io
import json
import sys
import tempfile
import unittest
import unittest.mock
from contextlib import redirect_stdout
from pathlib import Path

from tests import TMP_HOME  # noqa: F401 - hermetic sandbox HOME

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import demo_seed  # noqa: E402

from act.lib import recap_sessions, recap_store, recap_text  # noqa: E402

NOW = dt.datetime(2026, 9, 2, 12, 0, 0, tzinfo=dt.timezone.utc)


def _recaps(files):
    return [doc for rel, doc in files.items() if rel.startswith("recap/recaps/")]


class VocabularyTestCase(unittest.TestCase):
    def test_qualities_equal_recap_store(self):
        self.assertEqual(demo_seed.QUALITIES, recap_store.QUALITIES)

    def test_status_words_equal_recap_sessions(self):
        self.assertEqual((demo_seed.STATUS_OPEN, demo_seed.STATUS_CLOSED),
                         (recap_sessions.OPEN, recap_sessions.CLOSED))

    def test_scenes(self):
        self.assertEqual(demo_seed.SCENES, ("initial", "empty", "generating"))
        self.assertEqual(demo_seed.DEFAULT_SCENE, "initial")
        with self.assertRaises(ValueError):
            demo_seed.build("captured", now=NOW)


class BuildTestCase(unittest.TestCase):
    def test_every_scene_is_clean(self):
        for scene in demo_seed.SCENES:
            with self.subTest(scene=scene):
                self.assertEqual(demo_seed.validate(demo_seed.build(scene, now=NOW)), [])

    def test_initial_covers_every_page_state(self):
        files = demo_seed.build("initial", now=NOW)
        recaps = _recaps(files)
        self.assertEqual(sorted({r["quality"] for r in recaps}),
                         ["needs_review", "no_audio", "ok", "thin_transcript"])
        self.assertEqual({r["shape"] for r in recaps if r["shape"]},
                         {recap_text.SHAPE_LINES, recap_text.SHAPE_SECTIONS})
        self.assertTrue(any(r["history"] for r in recaps), "one recap must have a prior version")
        self.assertTrue(any(r["baseline"] for r in recaps), "one recap must keep its baseline")
        self.assertEqual(len(files["recap/sessions.json"]["open"]), 1)
        marks = files["recap/marks.json"]
        self.assertEqual(sum(1 for m in marks.values() if m.get("sent_at")), 1)
        self.assertEqual(sum(1 for m in marks.values() if m.get("dismissed_at")), 1)
        self.assertNotIn("recap_requests.json", files)

    def test_empty_scene_has_nothing(self):
        files = demo_seed.build("empty", now=NOW)
        self.assertEqual(_recaps(files), [])
        self.assertEqual(files["recap/sessions.json"]["open"], [])
        self.assertEqual(files["recap/marks.json"], {})

    def test_generating_scene_has_one_request_in_flight(self):
        files = demo_seed.build("generating", now=NOW)
        requests = files["recap_requests.json"]
        self.assertEqual(len(requests), 1)
        (key, entry), = requests.items()
        self.assertEqual(entry["launch"], "running")
        self.assertIn(key, {r["key"] for r in _recaps(files)})

    def test_copy_and_problems_come_from_recap_text(self):
        for rec in _recaps(demo_seed.build("initial", now=NOW)):
            if not rec["shape"]:
                continue
            with self.subTest(key=rec["key"]):
                if rec["shape"] == recap_text.SHAPE_SECTIONS:
                    en, zh = rec["sections_en"], rec["sections_zh"]
                else:
                    en, zh = rec["en"], rec["zh"]
                self.assertEqual(rec["copy_en"], recap_text.render_for(rec["shape"], en, "en"))
                self.assertEqual(rec["copy_zh"], recap_text.render_for(rec["shape"], zh, "zh"))
                problems = recap_text.validate_detail_for(rec["shape"], {"en": en, "zh": zh})
                self.assertEqual(rec["problems"], problems)
                self.assertEqual(rec["quality"], "needs_review" if problems else "ok")

    def test_exactly_one_needs_review_draft(self):
        drafts = [r for r in _recaps(demo_seed.build("initial", now=NOW))
                  if r["quality"] == "needs_review"]
        self.assertEqual(len(drafts), 1)
        self.assertEqual([p["code"] for p in drafts[0]["problems"]], ["reported_speech"])

    def test_keys_are_valid_and_meetings_are_in_the_past(self):
        files = demo_seed.build("initial", now=NOW)
        rows = _recaps(files) + files["recap/sessions.json"]["open"]
        for rec in rows:
            with self.subTest(key=rec["key"]):
                self.assertTrue(recap_store.valid_key(rec["key"]), rec["key"])
                self.assertLess(recap_sessions.parse_ts(rec["start"]), NOW.timestamp())
                self.assertLessEqual(recap_sessions.parse_ts(rec["end"]), NOW.timestamp())

    def test_the_data_is_fictional(self):
        text = json.dumps(demo_seed.build("generating", now=NOW), ensure_ascii=False).lower()
        for banned in ("@", "postman", "zelin", "anthropic", "openai"):
            self.assertNotIn(banned, text)

    def test_validator_reports_bad_records(self):
        files = demo_seed.build("initial", now=NOW)
        rel = next(rel for rel in files if rel.startswith("recap/recaps/"))
        files[rel] = dict(files[rel], quality="great", shape="poem", app="")
        problems = demo_seed.validate(files)
        self.assertTrue(any("quality 'great'" in p for p in problems), problems)
        self.assertTrue(any("shape 'poem'" in p for p in problems), problems)
        self.assertTrue(any("app must be" in p for p in problems), problems)

    def test_validator_reports_an_ok_record_the_gate_rejects(self):
        files = demo_seed.build("initial", now=NOW)
        rel, rec = next((rel, doc) for rel, doc in files.items()
                        if rel.startswith("recap/recaps/") and doc["shape"] == recap_text.SHAPE_LINES
                        and doc["quality"] == "ok")
        files[rel] = dict(rec, en=["Decided: alex.doe said so."] + rec["en"][1:])
        self.assertTrue(any("quality ok but the gate says" in p for p in demo_seed.validate(files)))

    def test_validator_reports_a_misnamed_file(self):
        files = demo_seed.build("initial", now=NOW)
        rel = next(rel for rel in files if rel.startswith("recap/recaps/"))
        files["recap/recaps/wrong.json"] = files.pop(rel)
        self.assertTrue(any("does not match key" in p for p in demo_seed.validate(files)))


class SeedRoundTripTestCase(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="demo-seed-"))

    def test_initial_round_trips_through_get_api_recaps(self):
        files = demo_seed.seed(self.home, "initial", NOW)
        snap = demo_seed.snapshot(self.home)
        listed = {row["key"] for row in snap["recaps"]}
        expected = {r["key"] for r in _recaps(files)} | {
            r["key"] for r in files["recap/sessions.json"]["open"]}
        self.assertEqual(listed, expected)
        self.assertEqual(snap["recap_counts"], {"active": 7, "archived": 1, "dismissed": 1})
        self.assertEqual(demo_seed.check_home(self.home), [])

    def test_generating_shows_a_running_request(self):
        demo_seed.seed(self.home, "generating")      # real clock: the request ledger has a 24 h TTL
        rows = demo_seed.snapshot(self.home)["recaps"]
        running = [r for r in rows if (r.get("generate_request") or {}).get("state") == "running"]
        self.assertEqual(len(running), 1)

    def test_seed_replaces_the_files_it_owns(self):
        demo_seed.seed(self.home, "generating", NOW)
        stale = self.home / "state" / "recap" / "recaps" / "meeting_2020-01-01T0000-zoom.json"
        stale.write_text("{}", encoding="utf-8")
        demo_seed.seed(self.home, "empty", NOW)
        self.assertEqual(list((self.home / "state" / "recap" / "recaps").glob("*.json")), [])
        self.assertFalse((self.home / "state" / "recap_requests.json").exists())
        self.assertEqual(demo_seed.snapshot(self.home)["recaps"], [])

    def test_check_flags_a_recap_the_projection_does_not_list(self):
        demo_seed.seed(self.home, "initial", NOW)
        bad = self.home / "state" / "recap" / "recaps" / "meeting_bad.json"
        bad.write_text(json.dumps({"key": "meeting:bad", "app": "zoom", "start": "x", "end": "y",
                                   "status": "closed", "quality": "ok", "shape": None}),
                       encoding="utf-8")
        self.assertTrue(any("not in GET /api/recaps" in p for p in demo_seed.check_home(self.home)))

    def test_cli_fixed_now_is_byte_identical(self):
        def run():
            out = io.StringIO()
            with redirect_stdout(out):
                rc = demo_seed.main([str(self.home), "--now", "2026-09-02T12:00:00Z"])
            self.assertEqual(rc, 0)
            return {p.name: p.read_bytes() for p in sorted((self.home / "state").rglob("*.json"))}
        self.assertEqual(run(), run())

    def test_cli_check_and_summary(self):
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(demo_seed.main([str(self.home), "--scene", "generating"]), 0)
            self.assertEqual(demo_seed.main([str(self.home), "--check"]), 0)
        lines = out.getvalue().splitlines()
        self.assertTrue(lines[0].startswith(
            "demo recaps: 8 closed, 1 open, 1 archived, 1 dismissed, 1 generating -> "), lines[0])
        self.assertEqual(lines[1], "demo recaps: ok")

    def test_cli_rejects_a_bad_now(self):
        with self.assertRaises(SystemExit):
            with redirect_stdout(io.StringIO()), unittest.mock.patch("sys.stderr", io.StringIO()):
                demo_seed.main([str(self.home), "--now", "yesterday"])


if __name__ == "__main__":
    unittest.main()
