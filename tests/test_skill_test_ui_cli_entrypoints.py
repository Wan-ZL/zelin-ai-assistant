"""test-ui skill · 四个传感器脚本的 CLI 入口判例（负控制齐全）：parity.py 坏清单 → exit 2、红 → exit 1、绿 → exit 0 且
parity.md 列出非 PRESENT 行与账本问题；inventory_a11y.py --from-source / --native / 无适配器 → 2 / --runtime 走 runner；
tokens.py --css 与 --design-tokens；visual.py diff / manifest。全部走 main(argv)，零真子进程（--runtime 的 driver 用 fake
runner），零网络。

法典：docs/CONTRACT.md §58（fail closed）/ §UI-parity；设计 vnext2-plan R2.8。
"""
import contextlib
import io
import json
import os
import tempfile
import unittest

from tests import skill_test_ui_testkit as kit

import inventory_a11y as inv  # noqa: E402
import parity  # noqa: E402
import tokens as tk  # noqa: E402
import visual  # noqa: E402

tc = kit.tc


def _quiet(fn, *args):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = fn(*args)
    return rc, out.getvalue(), err.getvalue()


class ParityCliTestCase(unittest.TestCase):
    def _write(self, tmp, name, obj):
        path = os.path.join(tmp, name)
        tc.write_text(path, tc.dump_json(obj))
        return path

    def test_bad_inventory_is_exit_2_and_writes_nothing(self):
        """坏清单（key 是字符串）→ exit 2「inventory unreadable — fail closed」，不是 traceback，也不落任何产物。"""
        with tempfile.TemporaryDirectory() as tmp:
            good = self._write(tmp, "good.json", kit.make_inventory([kit.make_item("board", "button", "Approve")]))
            bad_inv = kit.make_inventory([kit.make_item("board", "button", "Approve")])
            bad_inv["items"][0]["key"] = "oops"
            bad = self._write(tmp, "bad.json", bad_inv)
            out = os.path.join(tmp, "out")
            rc, _stdout, stderr = _quiet(parity.main, ["--subject", good, "--reference", bad, "--out", out])
            self.assertEqual(rc, 2)
            self.assertIn("fail closed", stderr)
            self.assertIn("items[0].key", stderr)
            self.assertFalse(os.path.exists(out))

    def test_red_and_green_exit_codes_with_markdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            ref = self._write(tmp, "ref.json", kit.make_inventory([kit.make_item("board", "button", "Approve"),
                                                                    kit.make_item("board", "button", "Later")], role="reference"))
            sub = self._write(tmp, "sub.json", kit.make_inventory([kit.make_item("board", "button", "Later")]))
            kit.make_repo(tmp, {"ledgers/waivers.txt": "control:board:button:nope\n"})  # reasonless → ledger problem
            thr = self._write(tmp, "thr.json", {"similarity_floor": 0.9})
            out = os.path.join(tmp, "out")
            rc, stdout, _stderr = _quiet(parity.main, ["--subject", sub, "--reference", ref, "--out", out, "--ledgers",
                                                       os.path.join(tmp, "ledgers"), "--thresholds", thr])
            self.assertEqual(rc, 1)
            self.assertIn("parity:", stdout)
            result = tc.read_json(os.path.join(out, "parity.json"))
            self.assertEqual(result["thresholds"]["similarity_floor"], 0.9)  # --thresholds merged over the defaults
            self.assertEqual(parity.red_reasons(result), ["items", "ledger"])
            with open(os.path.join(out, "parity.md"), encoding="utf-8") as fh:
                md = fh.read()
            self.assertIn("- MISSING `control:board:button:approve`", md)
            self.assertIn("- ledger problem: reasonless_waiver control:board:button:nope", md)
            self.assertNotIn("control:board:button:later", md)  # PRESENT rows are not listed
            rc, _stdout, _stderr = _quiet(parity.main, ["--subject", ref, "--reference", ref, "--out", os.path.join(tmp, "green")])
            self.assertEqual(rc, 0)

    def test_render_md_lists_rule_hits(self):
        result = {"counts": {"items": {"PRESENT": 1}, "tokens": {}}, "items": [], "ledger_problems": [],
                  "rules": [{"rule_id": "wcag.contrast.text", "id": "a/b", "measured": 3.1, "threshold": 4.5}]}
        self.assertIn("- rule wcag.contrast.text `a/b` 3.1 < 4.5", parity.render_md(result))

    def test_compare_assembles_tokens_theme_geometry_and_dedupes_problems(self):
        """parity.compare：有参照 tokens 才有 tokens / theme 行；有几何映射 + 观察值才有 geometry 行；同一账本问题
        （waivers 无理由既被 ledger_lint 又被 shrink 检查报）只列一次。"""
        ref = kit.make_inventory([kit.make_item("board", "button", "Approve")], role="reference")
        sub = kit.make_inventory([kit.make_item("board", "button", "Approve")])
        ledgers = dict(parity.load_ledgers(None), waivers={"x:y:z:w": ""})
        tokens = kit.make_tokens({"light": {"layout.lane.width": "400px", "color.bg": "#fff"}})
        bare = parity.compare(sub, ref, ledgers, dict(parity.DEFAULT_THRESHOLDS))
        self.assertEqual((bare["tokens"], bare["theme_default"], bare["geometry"]), ([], None, []))
        full = parity.compare(sub, ref, ledgers, dict(parity.DEFAULT_THRESHOLDS), subject_tokens=tokens, reference_tokens=tokens,
                              geometry_map={"layout.lane.width": {"screen": "board", "role": "list", "measure": "width"}},
                              geometry_observed={"layout.lane.width": [400]}, base_texts={"pending": "", "waivers": ""})
        self.assertEqual([t["status"] for t in full["tokens"]], ["PRESENT", "PRESENT"])
        self.assertEqual(full["theme_default"]["status"], "PRESENT")
        self.assertEqual([g["status"] for g in full["geometry"]], ["PRESENT"])
        self.assertEqual([p["kind"] for p in full["ledger_problems"]], ["reasonless_waiver", "waiver_grew"])
        self.assertEqual(parity._dedupe([{"kind": "a", "line": "1"}, {"kind": "a", "line": "1"}, {"kind": "b", "line": "1"}]),
                         [{"kind": "a", "line": "1"}, {"kind": "b", "line": "1"}])
        self.assertEqual(parity.red_reasons(full), ["ledger"])


class InventoryCliTestCase(unittest.TestCase):
    def test_from_source_native_and_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit.make_repo(tmp, {"web/board.html": "<main><button>批准</button></main>",
                                "cfg.json": json.dumps({"screens": [{"id": "kanban", "source": ["*board.html"]}]})})
            out = os.path.join(tmp, "src.json")
            rc, stdout, _err = _quiet(inv.main, ["--from-source", os.path.join(tmp, "web"), "--screen-map", os.path.join(tmp, "cfg.json"), "--out", out])
            self.assertEqual(rc, 0)
            self.assertIn("2 item(s)", stdout)  # the <main> landmark + the button
            self.assertEqual({i["screen"] for i in tc.read_json(out)["items"]}, {"kanban"})  # --screen-map honoured
            self.assertIsNone(inv._load_screens(None))
            self.assertEqual(inv._load_screens(os.path.join(tmp, "cfg.json"))[0]["id"], "kanban")
            tc.write_text(os.path.join(tmp, "empty.json"), "{}")
            self.assertIsNone(inv._load_screens(os.path.join(tmp, "empty.json")))
            rc, _stdout, stderr = _quiet(inv.main, ["--native", tmp, "--out", os.path.join(tmp, "native.json")])
            self.assertEqual(rc, 2)  # no ui/parity/native-inventory.json and no producer script
            self.assertIn("adapter unavailable", stderr)
            kit.make_repo(tmp, {"ui/parity/native-inventory.json": json.dumps({"controls": [
                {"id": "control:board:button:approve", "zh": "批准", "en": "Approve", "role": "button", "screen": "board", "owner": "web", "gated": True}]})})
            rc, _stdout, _stderr = _quiet(inv.main, ["--native", tmp, "--out", os.path.join(tmp, "native.json")])
            self.assertEqual(rc, 0)
            self.assertEqual(tc.read_json(os.path.join(tmp, "native.json"))["producer"]["mode"], "frozen")
            rc, _stdout, _stderr = _quiet(inv.main, ["--out", os.path.join(tmp, "none.json")])
            self.assertEqual(rc, 2)

    def test_runtime_dispatch_uses_the_driver_output(self):
        """--runtime：run_driver 跑 node（这里 fake runner 直接写 driver-output.json）→ parse_runtime；rc≠0 → None → exit 2。"""
        output = {"tool": "playwright 1.0", "dims": {}, "runs": [{"screen": "board", "theme": "light", "viewport": "d", "language": "zh",
                                                                    "nodes": [{"idx": 0, "role": "button", "name": "Approve", "visible": True, "focusable": True}],
                                                                    "landmarks": [], "focus_walk": [0]}]}

        def respond(argv, cwd):
            cfg = tc.read_json(argv[2])
            tc.write_text(cfg["out"], json.dumps(output))
            return kit.lc.RunResult(0, "", "")
        with tempfile.TemporaryDirectory() as tmp:
            cfg = os.path.join(tmp, "driver-config.json")
            tc.write_text(cfg, json.dumps({"url": "http://127.0.0.1:1", "screens": []}))
            parsed, res = inv.run_driver(tc.read_json(cfg), "/pw", tmp, kit.FakeRunner([("driver.cjs", respond)]), node="node")
            self.assertTrue(res.ok)
            self.assertEqual(parsed["runs"][0]["screen"], "board")
            self.assertEqual(inv.parse_runtime(parsed, {"role": "subject"})["inventory"]["items"][0]["id"], "control:board:button:approve")
            failed, res = inv.run_driver(tc.read_json(cfg), "/pw", tmp, kit.FakeRunner(default=(1, "", "boom")), node="node")
            self.assertEqual((failed, res.rc), (None, 1))
            tc.write_text(os.path.join(tmp, "driver-output.json"), "not json")
            broken, _res = inv.run_driver(tc.read_json(cfg), "/pw", tmp, kit.FakeRunner(default=(0, "", "")), node="node")
            self.assertIsNone(broken)


class TokensCliTestCase(unittest.TestCase):
    def test_css_and_design_tokens_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit.make_repo(tmp, {"tokens.css": ":root { --bg: #fff; --radius-md: 6px; }",
                                "native.json": json.dumps({"color": {"bg": {"$type": "color", "$value": "#ffffff"}}, "theme": {"default": {"$type": "string", "$value": "light"}}})})
            rc, stdout, _err = _quiet(tk.main, ["--repo", tmp, "--css", "tokens.css", "--out", os.path.join(tmp, "css.json")])
            self.assertEqual(rc, 0)
            self.assertIn("tokens:", stdout)
            doc = tc.read_json(os.path.join(tmp, "css.json"))
            self.assertEqual((doc["producer"]["mode"], doc["families"]), ("source", {"color": 1, "radius": 1}))
            rc, _stdout, _err = _quiet(tk.main, ["--design-tokens", os.path.join(tmp, "native.json"), "--out", os.path.join(tmp, "dt.json")])
            self.assertEqual(rc, 0)
            frozen = tc.read_json(os.path.join(tmp, "dt.json"))
            self.assertEqual((frozen["producer"]["mode"], frozen["default_theme"]["declared"]["fallback"]), ("frozen", "light"))

    def test_load_native_tokens_regenerates_into_out_dir_only(self):
        """ui/tokens/native-tokens.json 缺席但 scripts/ui/extract_native_tokens.py 在 → 跑 producer 写到 out_dir
        （--css /dev/null：不让它顺手改项目的 tokens.css）；producer 失败 → None；两者都没有 → None。"""
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(tk.load_native_tokens(tmp, None, kit.FakeRunner(), out_dir=tmp))
            kit.make_repo(tmp, {"scripts/ui/extract_native_tokens.py": "# producer"})

            def produce(argv, cwd):
                self.assertIn("--css", argv)
                self.assertEqual(argv[argv.index("--css") + 1], os.devnull)
                tc.write_text(argv[argv.index("--out") + 1], json.dumps({"layout": {"lane": {"width": {"$type": "dimension", "$value": "400px"}}}}))
                return kit.lc.RunResult(0, "", "")
            out_dir = os.path.join(tmp, "report", "tokens")
            doc = tk.load_native_tokens(tmp, None, kit.FakeRunner([("extract_native_tokens", produce)]), out_dir=out_dir)
            self.assertEqual(doc["themes"]["light"]["layout.lane.width"]["$value"], "400px")
            self.assertTrue(os.path.exists(os.path.join(out_dir, "native-tokens.json")))
            self.assertFalse(os.path.exists(os.path.join(tmp, "ui", "tokens", "native-tokens.json")))  # never into the project tree
            self.assertIsNone(tk.load_native_tokens(tmp, None, kit.FakeRunner(default=(1, "", "boom")), out_dir=os.path.join(tmp, "r2")))
            self.assertIsNone(tk.load_native_tokens(tmp, None, kit.FakeRunner(), out_dir=None))  # no out_dir → no regeneration


class VisualCliTestCase(unittest.TestCase):
    def test_diff_and_manifest_exit_codes(self):
        with tempfile.TemporaryDirectory() as tmp:
            a, b = os.path.join(tmp, "a.png"), os.path.join(tmp, "b.png")
            with open(a, "wb") as fh:
                fh.write(kit.make_png(10, 10))
            with open(b, "wb") as fh:
                fh.write(kit.make_png(10, 10, blocks=[[0, 0, 1, 1, (0, 0, 0)]]))
            self.assertEqual(_quiet(visual.main, ["diff", a, a])[0], 0)
            rc, stdout, _err = _quiet(visual.main, ["diff", a, b, "--heatmap", os.path.join(tmp, "heat.png")])
            self.assertEqual(rc, 1)
            self.assertEqual(json.loads(stdout)["changed_pixels"], 1)
            with open(os.path.join(tmp, "heat.png"), "rb") as fh:
                self.assertEqual(tc.decode_png(fh.read())[:2], (10, 10))
            rc, _stdout, stderr = _quiet(visual.main, ["diff", a, os.path.join(tmp, "missing.png")])
            self.assertEqual((rc, "fail closed" in stderr), (1, True))
            golden = os.path.join(tmp, "g")
            os.makedirs(golden)
            self.assertEqual(_quiet(visual.main, ["manifest", golden])[0], 0)  # empty dir, nothing unreviewed
            with open(os.path.join(golden, "x.png"), "wb") as fh:
                fh.write(kit.make_png(2, 2))
            self.assertEqual(_quiet(visual.main, ["manifest", golden])[0], 1)  # unreviewed golden is red


if __name__ == "__main__":
    unittest.main()
