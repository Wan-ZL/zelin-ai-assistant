"""test-ui skill · 参照产物的「在场 / 现跑 / 缺席」三态判例：`native` 别名的清单或 tokens 文件缺席但 producer 在 →
现跑 scripts/ui/extract_native_*.py 到报告目录（绝不写进 ui/）；producer 也没有 → pair_structure UNAVAILABLE、pair_tokens
UNAVAILABLE（design-system 才是 N-A，别名缺表不冒充 N-A）；坏参照（形状不对）→ pair_structure FAIL reference_unreadable。
git: 参照读源码时才建 detached worktree，跑完 run_ui._cleanup_reference 拆掉并收走空 cache 目录。零真子进程。

法典：docs/CONTRACT.md §66（native 参照 = 契约产物；skill 只读项目树）/ §58；设计 vnext2-plan R2.8。
"""
import json
import os
import tempfile
import unittest

from tests import skill_test_ui_testkit as kit

import checks_ui  # noqa: E402
import reference as refmod  # noqa: E402
import run_ui  # noqa: E402
import sensors  # noqa: E402

tc = kit.tc
NATIVE = {"controls": [{"id": "control:board:button:approve", "zh": "批准", "en": "Approve", "role": "button", "screen": "board",
                        "owner": "web", "gated": True}]}
NATIVE_TOKENS = {"layout": {"lane": {"width": {"$type": "dimension", "$value": "400px"}}}, "theme": {"default": {"$type": "string", "$value": "light"}}}


def _alias_ctx(tmp, runner, produced_by=("scripts/ui/extract_native_inventory.py", "scripts/ui/extract_native_tokens.py")):
    det = kit.fake_det(["board.html"], repo=tmp, reference=kit.side("reference", "alias", "native", mode={"structure": "na", "tokens": "na", "visual": "na"},
                                                                    inventory=None, tokens=None, produced_by=list(produced_by)))
    ctx = checks_ui.make_ctx(tmp, det, sel={}, out=os.path.join(tmp, "report"), runner=runner)
    ctx["state"]["subject_source"] = kit.make_inventory([kit.make_item("board", "button", "Approve")])
    ctx["state"]["subject_tokens"] = kit.make_tokens({"light": {"layout.lane.width": "400px"}})
    return ctx


class FrozenReferenceRegenTestCase(unittest.TestCase):
    def test_missing_native_files_are_regenerated_into_the_report_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit.make_repo(tmp, {"scripts/ui/extract_native_inventory.py": "#", "scripts/ui/extract_native_tokens.py": "#"})

            def produce(payload):
                def run(argv, cwd):
                    tc.write_text(argv[argv.index("--out") + 1], json.dumps(payload))
                    return kit.lc.RunResult(0, "", "")
                return run
            runner = kit.FakeRunner([("extract_native_inventory", produce(NATIVE)), ("extract_native_tokens", produce(NATIVE_TOKENS))])
            ctx = _alias_ctx(tmp, runner)
            self.assertEqual(sensors.check_pair_structure(ctx)["status"], "pass")
            self.assertEqual(sensors.check_pair_tokens(ctx)["status"], "pass")
            self.assertTrue(os.path.exists(os.path.join(tmp, "report", "inventory", "native-inventory.json")))
            self.assertTrue(os.path.exists(os.path.join(tmp, "report", "tokens", "native-tokens.json")))
            self.assertFalse(os.path.exists(os.path.join(tmp, "ui")))  # nothing written into the project tree
            self.assertEqual(len([c for c in runner.commands() if "extract_native" in c]), 2)

    def test_no_producer_is_unavailable_not_na(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = _alias_ctx(tmp, kit.FakeRunner(default=(1, "", "no such file")))
            structure = sensors.check_pair_structure(ctx)
            self.assertEqual(structure["status"], "na")  # no inventory at all → nothing to pair (a11y_static measures structure)
            tokens = sensors.check_pair_tokens(ctx)
            self.assertEqual(tokens["status"], "unavailable")
            self.assertIn("no reference tokens", tokens["summary"])
            self.assertNotIn("design-system", tokens["summary"])
            ctx["det"]["sides"]["reference"]["hint"] = "regenerate with: python3 scripts/ui/extract_native_tokens.py --out <report>/inventory/"
            del ctx["state"]["reference_tokens"]
            self.assertIn("regenerate with", sensors.check_pair_tokens(ctx)["summary"])

    def test_design_system_stays_na_and_inventory_kind_without_tokens_is_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            design = kit.fake_det(["board.html"], repo=tmp, reference=kit.side("reference", "design-system", "design-system",
                                                                                mode={"structure": "na", "tokens": "source", "visual": "na"}))
            ctx = checks_ui.make_ctx(tmp, design, sel={}, out=os.path.join(tmp, "r"))
            ctx["state"]["subject_tokens"] = kit.make_tokens({"light": {"layout.lane.width": "400px"}})
            self.assertEqual(sensors.check_pair_tokens(ctx)["status"], "na")
            inventory_path = os.path.join(tmp, "inv.json")
            tc.write_text(inventory_path, tc.dump_json(kit.make_inventory([kit.make_item("board", "button", "Approve")], mode="frozen", role="reference")))
            frozen = kit.fake_det(["board.html"], repo=tmp, reference=kit.side("reference", "inventory", inventory_path, inventory=inventory_path,
                                                                                mode={"structure": "frozen", "tokens": "na", "visual": "na"}))
            ctx = checks_ui.make_ctx(tmp, frozen, sel={}, out=os.path.join(tmp, "r2"))
            ctx["state"]["subject_source"] = kit.make_inventory([kit.make_item("board", "button", "Approve")])
            ctx["state"]["subject_tokens"] = kit.make_tokens({"light": {}})
            self.assertEqual(sensors.check_pair_structure(ctx)["status"], "pass")
            self.assertEqual(sensors.check_pair_tokens(ctx)["status"], "unavailable")

    def test_malformed_reference_file_is_reference_unreadable(self):
        """参照文件是合法 JSON 但不是清单形状（列表 / producer 是字符串）→ pair_structure FAIL `reference_unreadable`，
        不是一条 AttributeError。"""
        with tempfile.TemporaryDirectory() as tmp:
            for payload in ([1, 2], {"schemaVersion": 1, "producer": "source", "side": {}, "items": [{"id": "x", "key": "oops"}]}):
                path = os.path.join(tmp, "ref.json")
                tc.write_text(path, json.dumps(payload))
                det = kit.fake_det(["board.html"], repo=tmp, reference=kit.side("reference", "inventory", path, inventory=path,
                                                                                 mode={"structure": "frozen", "tokens": "na", "visual": "na"}))
                ctx = checks_ui.make_ctx(tmp, det, sel={}, out=os.path.join(tmp, "r"))
                ctx["state"]["subject_source"] = kit.make_inventory([])
                res = run_ui.execute("pair_structure", {"kind": "internal", "fn": sensors.check_pair_structure, "tool": "internal", "steps": []}, ctx, None, 1)
                self.assertEqual(res["status"], "fail")
                self.assertIn("reference_unreadable", res["summary"])
                self.assertNotIn("AttributeError", res["summary"])


class KnownNamesTestCase(unittest.TestCase):
    def test_unreadable_reference_does_not_break_the_name_filter(self):
        """参照读不懂是 pair_structure 的 FAIL，不是过滤器的事：_known_names 仍返回 subject 源字符串集合（不抛）。"""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "ref.json")
            tc.write_text(path, json.dumps([1, 2]))
            det = kit.fake_det(["board.html"], repo=tmp, reference=kit.side("reference", "inventory", path, inventory=path,
                                                                             mode={"structure": "frozen", "tokens": "na", "visual": "na"}))
            ctx = checks_ui.make_ctx(tmp, det, sel={}, out=os.path.join(tmp, "r"))
            ctx["state"]["subject_source"] = kit.make_inventory([kit.make_item("board", "button", "Approve")])
            self.assertEqual(sensors._known_names(ctx), {"Approve"})
            ctx["state"]["subject_source"] = None
            self.assertEqual(sensors._known_names(ctx), set())  # empty set, never None: the filter stays fail closed


class GitReferenceCleanupTestCase(unittest.TestCase):
    def test_cleanup_tolerates_a_non_empty_cache_dir(self):
        """worktree 拆掉后 cache 目录里还有别的东西（另一参照的 worktree）→ rmdir 失败被吞，目录留着。"""
        with tempfile.TemporaryDirectory() as tmp:
            cache = os.path.join(tmp, ".test-ui", "cache")
            kit.make_repo(tmp, {".test-ui/cache/ref-other/keep.txt": "x"})
            side = dict(kit.side("reference", "git", "git:x"), worktree=os.path.join(cache, "ref-abc"), sha="abc", worktree_ready=True)
            det = kit.fake_det(["b.html"], repo=tmp, reference=side)
            run_ui._cleanup_reference(tmp, det, kit.FakeRunner(default=(0, "", "")))
            self.assertTrue(os.path.isdir(os.path.join(cache, "ref-other")))

    def test_worktree_is_created_on_read_and_removed_after_the_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = os.path.join(tmp, ".test-ui", "cache")
            side = refmod.git_side(tmp, "origin/main", kit.FakeRunner(kit.git_ok_rules()), cache_dir=cache)
            self.assertFalse(os.path.isdir(cache))  # detection has no side effect

            def add(argv, cwd):
                kit.make_repo(argv[-2], {"web/board.html": "<div><button>Approve</button></div>"})
                return kit.lc.RunResult(0, "", "")

            def remove(argv, cwd):
                import shutil
                shutil.rmtree(argv[-1])
                return kit.lc.RunResult(0, "", "")
            runner = kit.FakeRunner([("worktree add", add), ("worktree remove", remove)])
            det = kit.fake_det(["board.html"], repo=tmp, reference=side)
            ctx = checks_ui.make_ctx(tmp, det, sel={}, out=os.path.join(tmp, "r"), runner=runner)
            ctx["state"]["subject_source"] = kit.make_inventory([kit.make_item("board", "button", "Approve")])
            self.assertEqual(sensors.check_pair_structure(ctx)["status"], "pass")
            self.assertTrue(side["worktree_ready"])
            run_ui._cleanup_reference(tmp, det, runner)
            self.assertFalse(os.path.exists(side["worktree"]))
            self.assertFalse(os.path.exists(cache))  # empty cache dir collected too
            calls = len(runner.commands())
            run_ui._cleanup_reference(tmp, kit.fake_det(["b.html"]), runner)  # dir: reference → nothing to do
            self.assertEqual(len(runner.commands()), calls)


class TopologyRuntimeTestCase(unittest.TestCase):
    def test_runtime_pairing_feeds_topology(self):
        """有 runtime bundle：topology_runtime 读 pair_runtime 的行——地标 side 变 → fail；没变 → pass（真仪器，不是替代物）；
        pairing 没跑 → unavailable。"""
        ctx = checks_ui.make_ctx("/r", kit.fake_det(["board.html"]))
        ctx["state"]["runtime"] = {"inventory": kit.make_inventory([], landmarks=[{"id": "l"}])}
        self.assertEqual(sensors.check_topology_runtime(ctx)["status"], "unavailable")
        ctx["state"]["pair_runtime"] = {"rows": [{"id": "landmark:board:navigation:rail", "fields_changed": ["topology:side"]}]}
        res = sensors.check_topology_runtime(ctx)
        self.assertEqual((res["status"], res["details"]["rows"][0]["id"]), ("fail", "landmark:board:navigation:rail"))
        ctx["state"]["pair_runtime"] = {"rows": [{"id": "landmark:board:navigation:rail", "fields_changed": ["name"]}]}
        res = sensors.check_topology_runtime(ctx)
        self.assertEqual((res["status"], res["details"]["landmarks"]), ("pass", 1))


class FixFirstProvenanceTestCase(unittest.TestCase):
    def test_item_rows_name_the_pairing_that_produced_them(self):
        """fix-first 的条目行标出处：runtime 配对跑了 → `pair_runtime`，否则 `pair_structure`。"""
        rows = [{"id": "control:board:button:approve", "status": "MISSING", "ledger": None, "kind": "interactive", "fields_changed": [], "screen": "board"}]
        ctx = checks_ui.make_ctx("/r", kit.fake_det(["board.html"]))
        ctx["state"]["pair_source"] = {"rows": rows}
        self.assertEqual(run_ui.fix_first([], ctx, {"screens": []})[0]["check"], "pair_structure")
        ctx["state"]["pair_runtime"] = {"rows": rows}
        self.assertEqual(run_ui.fix_first([], ctx, {"screens": []})[0]["check"], "pair_runtime")


class MaskCapFixFirstTestCase(unittest.TestCase):
    def test_mask_over_cap_is_rank_6_and_red(self):
        results = [{"id": "visual_diff", "status": "fail", "summary": "", "details": {"rows": [
            {"id": "shot:board", "item_status": "PRESENT", "status": "same", "over_mask_cap": True, "masked_ratio": 0.6},
            {"id": "shot:settings", "item_status": "CHANGED", "changed_pct": 0.02, "threshold": 0.0}]}}]
        fixes = run_ui._fix_visual(results)
        self.assertEqual([(f["rank"], f["item"]) for f in fixes], [(6, "shot:board"), (4, "shot:settings")])
        self.assertIn("60.0% masked", fixes[0]["kind"])


if __name__ == "__main__":
    unittest.main()
