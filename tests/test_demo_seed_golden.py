"""scripts/demo_seed — build() output and validate() verdicts pinned (§2 dashboard
wire; the demo data every UI test / video seeds from).

``tests/fixtures/demo_seed/build.golden.json`` holds the full initial/zh
dashboard for a frozen clock plus a sha256 per scene × language;
``validate.golden.json`` holds the exact problem lists for a deliberately broken
dashboard and for the queued_reason / steers edge cases. Both were captured from
the pre-P3b script, so any drift in the seed data, the localisation table, the
id stamping or the validator's wording flips this test.
"""
import copy
import datetime as dt
import hashlib
import json
import sys
import unittest
from pathlib import Path

from tests import TMP_HOME  # noqa: F401 - sandbox env first

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import demo_seed  # noqa: E402

FIXTURES = REPO / "tests" / "fixtures" / "demo_seed"
NOW = dt.datetime(2026, 9, 2, 12, 0, 0, tzinfo=dt.timezone.utc)


def _broken(full: dict) -> dict:
    bad = copy.deepcopy(full)
    bad["counts"]["running"] = 99
    bad["needs_approval"][0]["cost_usd"] = "free"
    bad["needs_approval"][0]["show_cost"] = "yes"
    bad["needs_approval"][0].pop("plan")
    bad["running"][0]["queued_reason"] = {"kind": "waiting_card"}
    bad["running"][0]["steers"] = [{"text": "", "ts": 5, "status": "delivered"}, "junk",
                                   {"text": "t", "ts": "x", "status": "pending", "delivered_at": "d"}]
    bad["review"][0]["delivery_mode"] = "fax"
    bad["review"][0]["final_draft"] = 7
    bad["trash"][0]["permanent"] = "no"
    bad["trash"][0]["trashed_at"] = None
    bad["debt"] = [{"id": 5, "title": "", "sources": "x"}]
    bad["counts"]["debt"] = 1
    del bad["generated_at"]
    return bad


class BuildGoldenTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.golden = json.loads((FIXTURES / "build.golden.json").read_text(encoding="utf-8"))

    def test_every_scene_and_language_hash(self):
        for scene in demo_seed.SCENES:
            for lang in demo_seed.LANGS:
                with self.subTest(scene=scene, lang=lang):
                    dash = demo_seed.build(scene, now=NOW, lang=lang)
                    text = json.dumps(dash, ensure_ascii=False, sort_keys=True)
                    self.assertEqual(hashlib.sha256(text.encode("utf-8")).hexdigest(),
                                     self.golden["sha256"][f"{scene}/{lang}"])
                    self.assertEqual(demo_seed.validate(dash), [])

    def test_initial_zh_full_dashboard(self):
        self.assertEqual(demo_seed.build("initial", now=NOW, lang="zh"), self.golden["initial_zh"])


class ValidateGoldenTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.golden = json.loads((FIXTURES / "validate.golden.json").read_text(encoding="utf-8"))
        cls.full = json.loads((FIXTURES / "build.golden.json").read_text(encoding="utf-8"))["initial_zh"]

    def test_broken_dashboard_problem_list(self):
        self.assertEqual(demo_seed.validate(_broken(self.full)), self.golden["broken_initial_zh"])

    def test_top_level_shapes(self):
        self.assertEqual(demo_seed.validate(["nope"]), self.golden["top_level"])
        self.assertEqual(demo_seed.validate({"generated_at": "t"}), self.golden["no_counts"])

    def test_queued_reason_cases(self):
        q = {"kind": "queued", "state": "queued", "queued_reason": "  ", "id": "P-1", "name": "n"}
        cases = (q, dict(q, queued_reason=["list"]),
                 dict(q, queued_reason={"kind": "nope", "detail": 5, "blocking_id": 6}),
                 dict(q, state="running", queued_reason={"kind": "waiting_card", "blocking_id": "R-1"}))
        problems = []
        for item in cases:
            demo_seed._check_queued_reason(problems, "x", item)
        self.assertEqual(problems, self.golden["queued_reason_cases"])
        none = []
        demo_seed._check_queued_reason(none, "x", {"state": "queued"})
        demo_seed._check_queued_reason(none, "x", {"state": "queued", "queued_reason": "ok"})
        demo_seed._check_queued_reason(none, "x", {"state": "queued",
                                                  "queued_reason": {"kind": "waiting_card",
                                                                    "blocking_id": "R-1"}})
        self.assertEqual(none, [])

    def test_steer_cases(self):
        problems = []
        demo_seed._check_steers(problems, "y", {"steers": "nope"})
        demo_seed._check_steers(problems, "y", {"steers": [{"text": "a", "ts": "t", "status": "delivered",
                                                             "delivered_at": ""}]})
        self.assertEqual(problems, self.golden["steer_cases"])
        none = []
        demo_seed._check_steers(none, "y", {})
        demo_seed._check_steers(none, "y", {"steers": [{"text": "a", "ts": "t", "status": "queued",
                                                         "delivered_at": None}]})
        self.assertEqual(none, [])


if __name__ == "__main__":
    unittest.main()
