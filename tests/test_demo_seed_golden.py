"""scripts/demo_seed — build() output and validate() verdicts pinned (§2 dashboard
wire; the demo data every UI test / video seeds from).

``tests/fixtures/demo_seed/build.golden.json`` holds the full initial/zh
dashboard for a frozen clock plus a sha256 per scene × language;
``validate.golden.json`` holds the exact problem lists for a deliberately broken
dashboard and for the queued_reason / steers edge cases. Both were captured from
the pre-P3b script, so any drift in the seed data, the localisation table, the
id stamping or the validator's wording flips this test.

§78（D80）提案车道退役：demo 板不再种 ``needs_approval`` 行（该键恒空，
``counts.needs_approval`` 恒 0），原来的提案卡与 ``raising`` 灰占位都搬进
``debt``（潜在任务）。两条被点名的不变量在这里钉着，因为 §66 parity vitest 只能
通过它们看到对应 UI：debt 里必须仍有至少一行 T2（§50 打字确认弹窗的文案）与
至少一行 ``processing: true``（灰占位文案）。
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
    # §78：提案列退役后完整卡面住在 debt[0]（带 tier 的机器卡）——「数字成本 /
    # bool show_cost / 必有 plan」这三条复验跟着卡面搬过来，一条都不能少
    bad["debt"][0]["cost_usd"] = "free"
    bad["debt"][0]["show_cost"] = "yes"
    bad["debt"][0].pop("plan")
    bad["running"][0]["queued_reason"] = {"kind": "waiting_card"}
    bad["running"][0]["steers"] = [{"text": "", "ts": 5, "status": "delivered"}, "junk",
                                   {"text": "t", "ts": "x", "status": "pending", "delivered_at": "d"}]
    bad["review"][0]["delivery_mode"] = "fax"
    bad["review"][0]["final_draft"] = 7
    bad["trash"][0]["permanent"] = "no"
    bad["trash"][0]["trashed_at"] = None
    # 潜在任务列同时住只有一句话的低置信度捕获——那种行仍只走 id / title /
    # sources 串检，两种行的复验必须同列共存（_check_debt 按 tier 分流）
    bad["debt"] = [bad["debt"][0], {"id": 5, "title": "", "sources": "x"}]
    bad["counts"]["debt"] = 2
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

    def test_needs_approval_is_present_and_empty_in_every_scene(self):
        """§78 / D80.1：提案列退役，但 wire 键 add-only 永不删——demo 板每个 scene
        都发 ``needs_approval: []`` 且 ``counts.needs_approval == 0``。种子若回填
        了一行，web 就会画回那一列，整场退役静默失效。"""
        for scene in demo_seed.SCENES:
            for lang in demo_seed.LANGS:
                with self.subTest(scene=scene, lang=lang):
                    dash = demo_seed.build(scene, now=NOW, lang=lang)
                    self.assertIn("needs_approval", dash)
                    self.assertEqual(dash["needs_approval"], [])
                    self.assertEqual(dash["counts"]["needs_approval"], 0)

    def test_debt_keeps_a_t2_row_and_a_processing_row(self):
        """§78 把机器卡搬进潜在任务后，两段 UI 只能从这一列走到：§50 的打字确认
        弹窗（需要一行 effective_tier=T2）与 §78/D80.6 的灰色占位文案（需要一行
        ``processing: true``）。demo 板是 §66 parity vitest 的唯一数据源，种子
        掉了这两种行 = 那两段界面在 parity 里彻底不被渲染，静默失去覆盖。"""
        for scene in demo_seed.SCENES:
            with self.subTest(scene=scene):
                debt = demo_seed.build(scene, now=NOW, lang="zh")["debt"]
                self.assertTrue([r for r in debt if r.get("effective_tier") == "T2"],
                                f"scene={scene}: 潜在任务列里没有 T2 行")
                self.assertTrue([r for r in debt if r.get("processing") is True],
                                f"scene={scene}: 潜在任务列里没有 processing 灰占位行")


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
