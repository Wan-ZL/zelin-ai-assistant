"""test-ui skill · 反作弊判例（references/anti-gaming.md 的机械执法）：隐藏控件永不 PRESENT；data-parity-id 冒名
（角色不同 / 名字不像）= CHANGED spoofed_pin；ids 来自 role + 名，data-testid 不参与；阈值放宽 = FAIL
threshold_raised；未审 golden = FAIL；pending 长了 = FAIL；替代物永不 pass；超时永不被 post hook 翻案；
--propose-* 只落报告目录，项目树零字节。零子进程。

法典：docs/CONTRACT.md §58（阈值只读、账本只缩）/ §UI-parity；设计 vnext2-plan R2.8。
"""
import os
import tempfile
import unittest

from tests import skill_test_ui_testkit as kit

import checks_ui  # noqa: E402
import inventory_a11y as inv  # noqa: E402
import parity  # noqa: E402
import run_ui  # noqa: E402
import sensors  # noqa: E402

THR = dict(parity.DEFAULT_THRESHOLDS)
EMPTY = parity.load_ledgers(None)


def _items(html):
    items, marks = inv.SourceExtractor(html, "board", "board.html").run()
    return inv.finish_inventory(kit.make_inventory(items, landmarks=marks))


class HiddenAndSpoofTestCase(unittest.TestCase):
    def test_hidden_control_is_missing(self):
        ref = _items('<main><button>Steer</button></main>')
        sub = _items('<main><button style="display:none">Steer</button></main>')
        rows = {r["id"]: r for r in parity.compare_items(sub, ref, EMPTY, THR)["rows"]}
        row = rows["control:board:button:steer"]
        self.assertEqual((row["status"], row["detail"]["hidden_by"]), ("MISSING", "display:none"))

    def test_spoofed_pin_is_changed(self):
        """<span data-parity-id="control:board:button:rework"> 冒充按钮 → CHANGED spoofed_pin + role。"""
        ref = _items('<main><button>Rework</button></main>')
        sub = _items('<main><span data-parity-id="control:board:button:rework">Rework</span></main>')
        rows = {r["id"]: r for r in parity.compare_items(sub, ref, EMPTY, THR)["rows"]}
        row = rows["control:board:button:rework"]
        self.assertEqual(row["status"], "CHANGED")
        self.assertIn("spoofed_pin", row["fields_changed"])
        self.assertIn("role", row["fields_changed"])

    def test_pin_with_unlike_name_is_spoof(self):
        ref = [kit.make_item("board", "button", "Approve")]
        sub = [kit.make_item("board", "button", "Delete everything", pin="control:board:button:approve")]
        row = parity.compare_items(kit.make_inventory(sub), kit.make_inventory(ref), EMPTY, THR)["rows"][0]
        self.assertEqual(row["fields_changed"], ["spoofed_pin", "name"])

    def test_test_ids_do_not_form_identity(self):
        by = {i["id"]: i for i in _items('<main><button data-testid="approve-btn">批准</button></main>')["items"]}
        self.assertIn("control:board:button:批准", by)
        self.assertIsNone(by["control:board:button:批准"]["pin"])


class ThresholdsAndLedgersTestCase(unittest.TestCase):
    def test_raised_threshold_is_red(self):
        det = kit.fake_det(["b.html"], thresholds=dict(THR, pixel_tolerance=8), thresholds_base=dict(THR))
        res = sensors.check_thresholds_unmoved(checks_ui.make_ctx("/r", det))
        self.assertEqual((res["status"], res["details"]["loosened"]), ("fail", ["pixel_tolerance"]))

    def test_unreviewed_golden_is_red(self):
        with tempfile.TemporaryDirectory() as tmp:
            machine = os.path.join(tmp, "test-chromium-dpr1")
            os.makedirs(machine)
            with open(os.path.join(machine, "board.png"), "wb") as fh:
                fh.write(kit.make_png(4, 4))
            det = kit.fake_det(["b.html"], goldens={"dir": tmp, "machine_key": "test-chromium-dpr1", "machine_dir": machine})
            res = sensors.check_golden_manifest(checks_ui.make_ctx("/r", det))
            self.assertEqual((res["status"], res["details"]["unreviewed"]), ("fail", ["board.png"]))
            other = kit.fake_det(["b.html"], goldens={"dir": tmp, "machine_key": "other", "machine_dir": os.path.join(tmp, "other")})
            self.assertEqual(sensors.check_golden_manifest(checks_ui.make_ctx("/r", other))["status"], "unavailable")

    def test_grown_pending_is_red_and_nothing_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit.copy_fixture("subject", tmp)
            ledger_dir = os.path.join(tmp, "ui", "parity")
            before = sorted(os.listdir(ledger_dir))
            parsed = parity.load_ledgers(ledger_dir)
            det = kit.fake_det(["board.html"], repo=tmp, ledgers={"dir": ledger_dir, "parsed": parsed,
                                                                 "base_texts": {"pending": "heading:settings:heading:overrides\n", "waivers": "", "aliases": ""}})
            res = sensors.check_ledger_lint(checks_ui.make_ctx(tmp, det, sel={}))
            self.assertEqual(res["status"], "fail")
            kinds = {p["kind"] for p in res["details"]["problems"]}
            self.assertEqual(kinds, {"pending_grew", "reasonless_waiver", "waiver_grew"})
            self.assertEqual(sorted(os.listdir(ledger_dir)), before)


class RunnerDisciplineTestCase(unittest.TestCase):
    def test_substituted_never_pass_even_with_post_hook(self):
        plan = {"kind": "substituted", "steps": [{"argv": ["x"], "cwd": "/r", "env": None}], "tool": "x",
                "post": lambda ctx, plan, runs: {"status": "pass", "summary": "post says pass"}, "note": "weaker instrument"}
        ctx = checks_ui.make_ctx("/r", kit.fake_det(["b.html"]))
        res = run_ui.execute("pair_structure", plan, ctx, kit.FakeRunner(default=(0, "", "")), 5)
        self.assertEqual(res["status"], "substituted")

    def test_timeout_is_fail_and_post_cannot_override(self):
        timed = kit.lc.RunResult(-1, "", "", timed_out=True)
        plan = {"kind": "cmd", "steps": [{"argv": ["x"], "cwd": "/r", "env": None}], "tool": "x",
                "post": lambda ctx, plan, runs: {"status": "pass"}, "note": None}
        res = run_ui.execute("seed_probe", plan, checks_ui.make_ctx("/r", kit.fake_det(["b.html"])), kit.FakeRunner(default=timed), 7)
        self.assertEqual(res["status"], "fail")
        self.assertIn("timed out after 7s", res["summary"])

    def test_internal_crash_and_missing_fail_closed(self):
        def boom(ctx):
            raise RuntimeError("kaboom")
        ctx = checks_ui.make_ctx("/r", kit.fake_det(["b.html"]))
        res = run_ui.execute("a11y_static", {"kind": "internal", "fn": boom, "tool": "internal", "steps": []}, ctx, None, 1)
        self.assertEqual(res["status"], "fail")
        self.assertIn("kaboom", res["summary"])
        res = run_ui.execute("a11y_static", {"kind": "missing", "reason": "fired but no evidence", "steps": []}, ctx, None, 1)
        self.assertEqual((res["status"], res["summary"]), ("fail", "fired but no evidence"))

    def test_proposals_land_in_report_dir_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = os.path.join(tmp, "repo")
            kit.copy_fixture("subject", repo)
            out = os.path.join(tmp, "report")
            ctx = checks_ui.make_ctx(repo, kit.fake_det(["board.html"], repo=repo), out=out)
            ctx["state"]["pair_source"] = {"rows": [{"id": "control:board:button:批准", "status": "MISSING", "ledger": None},
                                                    {"id": "heading:settings:heading:overrides", "status": "MISSING", "ledger": "pending"}]}
            prop = run_ui.propose_pending(ctx, ctx["det"])
            self.assertEqual(prop["count"], 1)
            self.assertTrue(prop["path"].startswith(out))
            self.assertIn("control:board:button:批准", open(prop["path"], encoding="utf-8").read())
            self.assertNotIn("批准", open(os.path.join(repo, "ui", "parity", "pending.txt"), encoding="utf-8").read())
            goldens = run_ui.propose_goldens(ctx, ctx["det"])
            self.assertEqual(goldens["count"], 0)
            self.assertTrue(os.path.exists(os.path.join(goldens["path"], "manifest.json")))
            self.assertFalse(os.path.exists(os.path.join(repo, "ui", "parity", "goldens")))


def _node(idx, role, name, parent="window>main:main", **extra):
    node = {"idx": idx, "role": role, "name": name, "name_source": "text", "text": name, "parent": parent, "order": idx,
            "visible": True, "hidden_by": None, "focusable": role == "button"}
    node.update(extra)
    return node


def _runtime_output(nodes, landmarks=()):
    return {"tool": "playwright 1.0", "dims": {"themes": ["light"]},
            "runs": [{"screen": "board", "scene": "initial", "theme": "light", "viewport": "desktop", "language": "zh", "flags": "default",
                      "emulation": "light", "lang": "zh", "nodes": nodes, "landmarks": list(landmarks), "focus_walk": [n["idx"] for n in nodes],
                      "overflow": {"scrollWidth": 1, "clientWidth": 1}}]}


class StaticNameFilterTestCase(unittest.TestCase):
    def test_runtime_names_outside_source_become_dynamic(self):
        """SKILL.md：源字符串集合之外的 runtime 名字 → {dynamic}——名字、id 的 slug、可见文本一起脱敏（用户内容不进
        subject-runtime.json、不进报告）；pin 过的名字保留（要与参照比像不像）；参照的名字算已知。"""
        ctx = checks_ui.make_ctx("/r", kit.fake_det(["b.html"]))
        ctx["state"]["subject_source"] = kit.make_inventory([kit.make_item("board", "button", "Approve")])
        ctx["state"]["reference_inventory"] = kit.make_inventory([kit.make_item("board", "button", "Reject")], role="reference")
        known = sensors._known_names(ctx)
        self.assertEqual(known, {"Approve", "Reject"})
        nodes = [_node(0, "button", "Approve"), _node(1, "static", "Alice's private note"), _node(2, "button", "Reject"),
                 _node(3, "button", "Pinned", pin="control:board:button:x")]
        inventory = inv.parse_runtime(_runtime_output(nodes), {"role": "subject"}, known_names=known)["inventory"]
        by = {i["id"]: i for i in inventory["items"]}
        self.assertEqual(sorted(by), ["control:board:button:approve", "control:board:button:pinned", "control:board:button:reject",
                                      "control:board:static:dynamic"])
        self.assertEqual((by["control:board:static:dynamic"]["name"]["raw"], by["control:board:static:dynamic"]["dynamic"],
                          by["control:board:static:dynamic"]["visible_text"]), ("{dynamic}", True, None))
        self.assertEqual(by["control:board:button:pinned"]["name"]["raw"], "Pinned")
        self.assertEqual(inventory["names_filtered"], 1)
        self.assertNotIn("Alice", kit.tc.dump_json(inventory))
        self.assertEqual(inventory["focus_walk"]["board::light::desktop::zh::rest"][1], "control:board:static:dynamic")

    def test_empty_known_set_filters_everything_and_none_disables(self):
        """没有源集合 ≠ 不过滤：空集合 = 什么都不认识 = 全部 {dynamic}（fail closed）；known=None 才是显式关掉（CLI 调试）。"""
        nodes = [_node(0, "button", "Approve"), _node(1, "static", "Alice's private note")]
        strict = inv.parse_runtime(_runtime_output(nodes), {"role": "subject"}, known_names=set())["inventory"]
        self.assertEqual(sorted(i["id"] for i in strict["items"]), ["control:board:button:dynamic", "control:board:static:dynamic"])
        self.assertEqual(strict["names_filtered"], 2)
        loose = inv.parse_runtime(_runtime_output(nodes), {"role": "subject"})["inventory"]
        self.assertIn("control:board:static:alice-s-private-note", {i["id"] for i in loose["items"]})
        self.assertEqual(loose["names_filtered"], 0)

    def test_dynamic_landmark_names_leave_parent_paths_too(self):
        """<section aria-labelledby=cardTitle>：区块名是用户内容 → 它自己的 id、地标记录、以及后代 parent 路径里的那一段
        全部成 dynamic（driver.cjs 的 landmarkPath 用同一 slug 规则拼段）。"""
        nodes = [_node(0, "region", "Bob's card title", parent="window>main:main"),
                 _node(1, "button", "Approve", parent="window>main:main>region:bob-s-card-title")]
        landmarks = [{"role": "region", "name": "Bob's card title", "parent": "window>main:main", "order": 0, "side": "inside", "bbox": [0, 0, 1, 1]}]
        inventory = inv.parse_runtime(_runtime_output(nodes, landmarks), {"role": "subject"}, known_names={"Approve"})["inventory"]
        by = {i["id"]: i for i in inventory["items"]}
        self.assertEqual(by["control:board:button:approve"]["topology"]["parent"], "window>main:main>region:dynamic")
        self.assertIn("landmark:board:region:dynamic", by)
        self.assertEqual(inventory["landmarks"][0]["id"], "landmark:board:region:dynamic")
        self.assertNotIn("bob", kit.tc.dump_json(inventory).lower())


if __name__ == "__main__":
    unittest.main()
