"""Unit tests for scripts/demo_seed.py.

Verifies:
- Default behavior (zh) is unchanged and validates cleanly.
- --english and --lang en flags generate all-English demo data.
- All scenes validate cleanly in both zh and en modes.
- --check mode works on generated dashboard.json files.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from tests import TMP_HOME  # noqa: F401 - hermetic sandbox HOME

REPO_ROOT = Path(__file__).resolve().parent.parent
DEMO_SEED_PATH = REPO_ROOT / "scripts" / "demo_seed.py"


def _load_demo_seed():
    spec = importlib.util.spec_from_file_location("_zai_demo_seed_test", DEMO_SEED_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestDemoSeed(unittest.TestCase):

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="demo-seed-test-"))
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        self.demo_seed = _load_demo_seed()

    def test_default_seed_is_chinese(self):
        rc = self.demo_seed.main([str(self.home)])
        self.assertEqual(rc, 0)
        path = self.home / "state" / "dashboard.json"
        self.assertTrue(path.exists())
        dash = json.loads(path.read_text(encoding="utf-8"))
        problems = self.demo_seed.validate(dash)
        self.assertEqual(problems, [])

        r101 = next(c for c in dash["needs_approval"] if c["id"] == "R-101")
        self.assertIn("leaderboard", r101["title"])
        self.assertIn("一键导出评测报告", r101["title"])

    def test_english_flag_generates_english_data(self):
        rc = self.demo_seed.main([str(self.home), "--english"])
        self.assertEqual(rc, 0)
        path = self.home / "state" / "dashboard.json"
        dash = json.loads(path.read_text(encoding="utf-8"))
        problems = self.demo_seed.validate(dash)
        self.assertEqual(problems, [])

        # R-101
        r101 = next(c for c in dash["needs_approval"] if c["id"] == "R-101")
        self.assertEqual(r101["title"], "example-bench: one-click leaderboard eval report export")
        self.assertEqual(r101["tier_hint"], "One-click approval")
        self.assertIn("Export Report", r101["summary"])
        self.assertEqual(r101["sources"][0]["quote"],
                         "Can we add a button to export the leaderboard into a report with one click?")

        # R-102
        r102 = next(c for c in dash["needs_approval"] if c["id"] == "R-102")
        self.assertEqual(r102["title"], "inkweld: set up public demo environment + seed data")
        self.assertEqual(r102["tier_hint"], "Written confirmation required")

        # R-103
        r103 = next(c for c in dash["needs_approval"] if c["id"] == "R-103")
        self.assertEqual(r103["title"], "Draft Q3 planning one-pager (bilingual)")

        # R-104
        r104 = next(c for c in dash["needs_approval"] if c["id"] == "R-104")
        self.assertEqual(r104["title"], "Unify lint configurations across example-bench and inkweld")
        self.assertEqual(r104["tier_hint"], "AI researching")

        # Running cards: R-105, R-106, R-107
        r105 = next(c for c in dash["running"] if c["id"] == "R-105")
        self.assertEqual(r105["name"], "example-bench: fix flaky e2e tests (retry logic)")

        r106 = next(c for c in dash["running"] if c["id"] == "R-106")
        self.assertEqual(r106["name"], "inkweld: rewrite README quickstart section")

        r107 = next(c for c in dash["running"] if c["id"] == "R-107")
        self.assertEqual(r107["name"], "example-bench: dataset v2 loader compatibility shim")

        # Needs input: R-108
        r108 = next(c for c in dash["needs_input"] if c["id"] == "R-108")
        self.assertEqual(r108["name"], "Connect Supabase auth to inkweld (service key needed)")

        # Review: R-109, R-110
        r109 = next(c for c in dash["review"] if c["id"] == "R-109")
        self.assertIn("eval caching layer", r109["name"])
        self.assertIn("Draft PR #87 opened", r109["delivered_summary"])

        r110 = next(c for c in dash["review"] if c["id"] == "R-110")
        self.assertEqual(r110["name"], "Write this week's weekly report (review before sending)")

        # Completed: R-111, R-112
        r111 = next(c for c in dash["completed"] if c["id"] == "R-111")
        self.assertEqual(r111["name"], "example-bench: add lint gate to CI (ruff + prettier)")

        r112 = next(c for c in dash["completed"] if c["id"] == "R-112")
        self.assertEqual(r112["name"], "Automatically organize weekly meeting action items into checklist")

        # Debt: R-113, R-114, R-115
        r113 = next(c for c in dash["debt"] if c["id"] == "R-113")
        self.assertEqual(r113["title"], "example-bench README installation section is outdated")

        # Trash: R-116
        r116 = next(c for c in dash["trash"] if c["id"] == "R-116")
        self.assertEqual(r116["title"], "Add auto-reply bot to Slack")

    def test_lang_flag_en_and_zh(self):
        rc_en = self.demo_seed.main([str(self.home), "--lang", "en"])
        self.assertEqual(rc_en, 0)
        path = self.home / "state" / "dashboard.json"
        dash_en = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(dash_en["needs_approval"][0]["title"],
                         "example-bench: one-click leaderboard eval report export")

        rc_zh = self.demo_seed.main([str(self.home), "--lang", "zh"])
        self.assertEqual(rc_zh, 0)
        dash_zh = json.loads(path.read_text(encoding="utf-8"))
        self.assertIn("一键导出评测报告", dash_zh["needs_approval"][0]["title"])

    def test_all_scenes_in_english(self):
        for scene in self.demo_seed.SCENES:
            with self.subTest(scene=scene):
                dash = self.demo_seed.build(scene, lang="en")
                problems = self.demo_seed.validate(dash)
                self.assertEqual(problems, [], f"Validation failed for scene {scene}")

    def test_build_english_kwarg(self):
        dash = self.demo_seed.build("initial", english=True)
        self.assertEqual(dash["needs_approval"][0]["title"],
                         "example-bench: one-click leaderboard eval report export")

    def test_check_mode(self):
        self.demo_seed.main([str(self.home), "--english"])
        path = self.home / "state" / "dashboard.json"
        rc = self.demo_seed.main([str(path), "--check"])
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
