"""test-ui skill · 反作弊判例（references/anti-gaming.md 的机械执法）：隐藏控件永不 PRESENT；data-parity-id 冒名
（角色不同 / 名字不像）= CHANGED spoofed_pin；ids 来自 role + 名，data-testid 不参与；阈值放宽 = FAIL
threshold_raised；未审 golden = FAIL；pending 长了 = FAIL；替代物永不 pass；超时永不被 post hook 翻案；
--propose-* 只落报告目录，项目树零字节。零子进程。

法典：docs/CONTRACT.md §58（阈值只读、账本只缩）/ §62；设计 vnext2-plan R2.8。
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


class StaticNameFilterTestCase(unittest.TestCase):
    def test_runtime_names_outside_source_become_dynamic(self):
        ctx = checks_ui.make_ctx("/r", kit.fake_det(["b.html"]))
        ctx["state"]["subject_source"] = kit.make_inventory([kit.make_item("board", "button", "Approve")])
        runtime = kit.make_inventory([kit.make_item("board", "button", "Approve"), kit.make_item("board", "static", "Alice's private note"),
                                      kit.make_item("board", "button", "Pinned", pin="control:board:button:x")])
        self.assertEqual(sensors._static_name_filter(ctx, runtime), 1)
        names = {i["id"]: i["name"]["raw"] for i in runtime["items"]}
        self.assertEqual(names["control:board:static:alice-s-private-note"], "{dynamic}")
        self.assertEqual(names["control:board:button:approve"], "Approve")
        self.assertEqual(names["control:board:button:pinned"], "Pinned")
        ctx["state"]["subject_source"] = None
        self.assertEqual(sensors._static_name_filter(ctx, runtime), 0)


if __name__ == "__main__":
    unittest.main()
