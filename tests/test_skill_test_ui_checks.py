"""test-ui skill · 目录 / 计划判例：CATALOG 形状（id 唯一、tier 1–5、phase 1–3、sensor 归属）；default_checks
（核心圈 tier ≤ 选定 + 触发器加挂 + always）；core_skipped 只算可跑的核心层；builders 的 na / unavailable /
internal / cmd 走向（runtime 缺席 → unavailable 带安装提示；项目仪器在场 → cmd 逐字；seed_probe 两步）；菜单每行有
kind / sensor / mode / est_seconds。零子进程。

法典：docs/CONTRACT.md §58 / §UI-parity；设计 vnext2-plan R2.8。
"""
import json
import os
import tempfile
import unittest

from tests import skill_test_ui_testkit as kit

import checks_ui as checks  # noqa: E402


class CatalogShapeTestCase(unittest.TestCase):
    def test_ids_unique_tiers_and_phases(self):
        ids = [e["id"] for e in checks.CATALOG]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(e["tier"] in (1, 2, 3, 4, 5) for e in checks.CATALOG))
        self.assertTrue(all(e["phase"] in (1, 2, 3) for e in checks.CATALOG))
        self.assertTrue(all(e["circle"] in ("core", "extended") for e in checks.CATALOG))
        self.assertEqual(checks.TIER_TIMEOUTS, {1: 300, 2: 1800, 3: 3600, 4: 7200, 5: None})
        for trigger, add_ons in checks.TRIGGER_CHECKS.items():
            for cid in add_ons:
                self.assertIn(cid, checks.BY_ID, "%s → %s" % (trigger, cid))
        self.assertEqual(checks.sensor_of("visual_diff"), "visual")
        self.assertEqual(checks.sensor_of("seed_guard"), "ladder")

    def test_default_checks_by_tier_and_triggers(self):
        det = kit.fake_det(["b.html"])
        tier1 = checks.default_checks(det, 1)
        self.assertIn("pair_structure", tier1)
        self.assertIn("seed_guard", tier1)          # always trigger
        self.assertIn("ledger_lint", tier1)
        self.assertNotIn("structure_runtime", tier1)
        self.assertNotIn("opinion", tier1)          # extended never pre-selected
        det["triggers"] = [{"id": "tokens_changed", "evidence": [], "hits": 1}]
        with_trigger = checks.default_checks(det, 1)
        self.assertIn("visual_diff", with_trigger)  # tier-3 check pulled in by a trigger at tier 1
        self.assertIn("geometry_runtime", with_trigger)
        tier5 = checks.default_checks(kit.fake_det(["b.html"]), 5)
        self.assertIn("clean_machine_ui", tier5)
        self.assertNotIn("project_visual", tier5)

    def test_core_skipped_counts_runnable_only(self):
        det = kit.fake_det(["b.html"])
        det["menu"] = [{"id": "pair_structure", "kind": "internal"}, {"id": "structure_runtime", "kind": "unavailable"},
                       {"id": "golden_manifest", "kind": "na"}, {"id": "opinion", "kind": "internal"}, {"id": "visual_diff", "kind": "internal"}]
        skipped = checks.core_skipped(det, 2, ["surface_detect"])
        self.assertEqual(skipped, ["pair_structure"])  # unavailable / na / extended / higher tier do not count
        self.assertEqual(checks.core_skipped(det, 3, ["pair_structure"]), ["visual_diff"])


class BuildersTestCase(unittest.TestCase):
    def test_runtime_unavailable_carries_hint(self):
        ctx = checks.make_ctx("/r", kit.fake_det(["b.html"]))
        plan = checks.BY_ID["structure_runtime"]["build"](ctx)
        self.assertEqual(plan["kind"], "unavailable")
        self.assertIn("playwright", plan["reason"])
        self.assertEqual(checks.BY_ID["visual_diff"]["build"](ctx)["kind"], "unavailable")
        self.assertEqual(checks.BY_ID["pair_structure"]["build"](ctx)["kind"], "internal")

    def test_no_surface_is_na(self):
        ctx = checks.make_ctx("/r", kit.fake_det(["x.py"]))
        self.assertEqual(checks.BY_ID["structure_source"]["build"](ctx)["kind"], "na")
        self.assertEqual(checks.BY_ID["structure_runtime"]["build"](ctx)["kind"], "na")

    def test_swift_surface_without_project_adapter_is_a_blind_spot(self):
        """只有 Swift 面、没有 scripts/ui/extract_native_inventory.py → structure_source UNAVAILABLE「needs project adapter」
        （skill 不带 Swift 启发式）；Swift + web 并存 → web 清单 pass，Swift 作为 blind spot 记在 details 里。"""
        import sensors
        with tempfile.TemporaryDirectory() as tmp:
            kit.make_repo(tmp, {"mac/Sources/App.swift": "// swift", "web/board.html": "<main><button>Go</button></main>"})
            swift_only = kit.fake_det(["mac/Sources/App.swift"], repo=tmp, surfaces=[{"kind": "swift-source", "root": "mac/Sources", "files": 1}])
            res = sensors.check_structure_source(checks.make_ctx(tmp, swift_only, out=os.path.join(tmp, "o1")))
            self.assertEqual(res["status"], "unavailable")
            self.assertIn("needs project adapter", res["summary"])
            both = kit.fake_det(["mac/Sources/App.swift", "web/board.html"], repo=tmp,
                                surfaces=[{"kind": "static-html", "root": "web", "files": 1}, {"kind": "swift-source", "root": "mac/Sources", "files": 1}])
            res = sensors.check_structure_source(checks.make_ctx(tmp, both, out=os.path.join(tmp, "o2")))
            self.assertEqual(res["status"], "pass")
            self.assertEqual(len(res["details"]["blind_spots"]), 1)
            both["adapters"] = {"extract_native_inventory": "scripts/ui/extract_native_inventory.py"}
            res = sensors.check_structure_source(checks.make_ctx(tmp, both, out=os.path.join(tmp, "o3")))
            self.assertEqual(res["details"]["blind_spots"], [])  # the project adapter speaks for Swift

    def test_project_adapters_called_verbatim(self):
        det = kit.fake_det(["b.html"], adapters={"parity_check": "scripts/ui/parity_check.py", "visual_spec": "web/e2e/visual.spec.ts"},
                           tools={"npx": "/bin/npx", "playwright_bin": True, "node": "/bin/node"}, web_dir="web")
        ctx = checks.make_ctx("/r", det, out="/o")
        parity_plan = checks.BY_ID["project_parity"]["build"](ctx)
        self.assertEqual(parity_plan["kind"], "cmd")
        self.assertEqual(parity_plan["steps"][0]["argv"][1:3], ["scripts/ui/parity_check.py", "--check"])
        # 门的三个产物全部落到 <report>/project_parity/：report.json / report.md 的默认路径在项目树 ui/parity/——skill 不重写它
        self.assertEqual(parity_plan["steps"][0]["argv"][3:], ["--report", "/o/project_parity", "--report-json", "/o/project_parity/report.json",
                                                            "--report-md", "/o/project_parity/report.md"])
        visual_plan = checks.BY_ID["project_visual"]["build"](ctx)
        self.assertEqual(visual_plan["steps"][0]["argv"], ["npx", "--no-install", "playwright", "test", "e2e/visual.spec.ts"])
        self.assertEqual(visual_plan["steps"][0]["cwd"], "/r/web")
        det["tools"]["playwright_bin"] = False
        self.assertEqual(checks.BY_ID["project_visual"]["build"](ctx)["kind"], "unavailable")
        det["adapters"] = {}
        self.assertEqual(checks.BY_ID["project_parity"]["build"](ctx)["kind"], "na")

    def test_project_parity_post_reads_the_gate_report(self):
        """post hook：<report>/project_parity/report.json 缺席 → fail closed；在场 → items 进 ctx.state、summary 带 counts。"""
        with tempfile.TemporaryDirectory() as tmp:
            det = kit.fake_det(["board.html"], adapters={"parity_check": "scripts/ui/parity_check.py"},
                               tools={"npx": "/bin/npx", "node": "/bin/node"}, web_dir="web")
            ctx = checks.make_ctx("/r", det, out=tmp)
            plan = checks.BY_ID["project_parity"]["build"](ctx)
            self.assertEqual(plan["post"](ctx, plan, [])["status"], "fail")
            report = {"counts": {"PRESENT": 1, "PENDING": 2}, "items": {"a": "PRESENT", "b": "PENDING", "c": "PENDING"}}
            kit.tc.write_text(os.path.join(tmp, "project_parity", "report.json"), json.dumps(report))
            extra = plan["post"](ctx, plan, [])
            self.assertEqual(ctx["state"]["project_parity"]["items"]["b"], "PENDING")
            self.assertIn('"PENDING": 2', extra["summary"])
            self.assertEqual(extra["details"]["counts"], report["counts"])

    def test_seed_probe_two_steps(self):
        launch = {"server": ["{py}", "-m", "server"], "seed": ["{py}", "scripts/demo_seed.py", "{home}", "--scene", "{scene}"]}
        det = kit.fake_det(["b.html"])
        det["sides"]["subject"]["launch"] = launch
        plan = checks.BY_ID["seed_probe"]["build"](checks.make_ctx("/r", det, out="/o", py="py3"))
        self.assertEqual(plan["kind"], "cmd")
        self.assertEqual(plan["steps"][0]["argv"], ["py3", "scripts/demo_seed.py", "/o/seed-probe", "--scene", "initial"])
        self.assertEqual(plan["steps"][1]["argv"], ["py3", "scripts/demo_seed.py", "/o/seed-probe", "--check"])
        self.assertEqual(checks.BY_ID["seed_probe"]["build"](checks.make_ctx("/r", kit.fake_det(["b.html"])))["kind"], "na")

    def test_menu_rows(self):
        menu = checks.build_menu(checks.make_ctx("/r", kit.fake_det(["b.html"])))
        by = {m["id"]: m for m in menu}
        self.assertEqual(len(menu), len(checks.CATALOG))
        self.assertEqual(by["structure_runtime"]["mode"], "unavailable")
        self.assertEqual(by["pair_structure"]["mode"], "source")
        self.assertEqual(by["structure_runtime"]["sensor"], "structure")
        self.assertTrue(all("est_seconds" in m and "command" in m for m in menu))
        self.assertEqual(checks.preview({"kind": "cmd", "steps": [{"argv": ["a", "b c"]}]}), "a 'b c'")


if __name__ == "__main__":
    unittest.main()
