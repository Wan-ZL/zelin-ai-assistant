"""test-ui skill · 第 4 档自测留下的判例：2026-09-03 对 skills/test-ui/scripts 跑 scripts/qa/mutate.py
（ad-hoc 靶区映射，3,321 个变异体，60.1% 杀伤；1,326 存活体里 802 个是 ±1 整数常量——CATALOG 时间估计、
切片上限、`[:80]` 之类的干旱节点，180 个是纯搬运函数的 return None，另有大量 `x or {}` 默认值），这里逐个钉死
当时活下来的**逻辑**变异体——边界比较、and/or、fail-closed 分支、判红条件。每个测试的 docstring 写明它杀的是
哪个函数的哪个变异（file::function op）。零子进程。

设计 = docs/design/vnext2-plan.md R2.8；CONTRACT §57（存活变异体 = 补测试提案）。
"""
import os
import tempfile
import unittest

from tests import skill_test_ui_testkit as kit

import checks_ui  # noqa: E402
import detect_ui  # noqa: E402
import inventory_a11y as inv  # noqa: E402
import parity  # noqa: E402
import reference as refmod  # noqa: E402
import run_ui  # noqa: E402
import sensors  # noqa: E402
import tokens as tk  # noqa: E402
import visual  # noqa: E402

tc = kit.tc
THR = dict(parity.DEFAULT_THRESHOLDS)
EMPTY = parity.load_ledgers(None)


class ParityBoundariesTestCase(unittest.TestCase):
    def test_spoof_floor_is_inclusive(self):
        """parity::_match_pin `similarity >= SPOOF_FLOOR` — 恰在 0.5 的名字不算冒名（>= → > 会误报）。"""
        ref = kit.make_item("board", "button", "abcd")
        sub = kit.make_item("board", "button", "abxy", pin="control:board:button:abcd")  # ratio exactly 0.5
        self.assertEqual(parity.similarity("abxy", "abcd"), 0.5)
        self.assertEqual(parity._match_pin(ref, parity.index_subject([sub])), (sub, False))
        sub_far = kit.make_item("board", "button", "zzzz", pin="control:board:button:abcd")
        self.assertEqual(parity._match_pin(ref, parity.index_subject([sub_far]))[1], True)

    def test_suggest_requires_same_screen_and_role(self):
        """parity::_suggest `screen == … and role == …` — and → or 会跨屏/跨角色建议。"""
        ref = kit.make_item("board", "link", "Settings")
        other_screen = kit.make_item("trash", "link", "Setting")
        other_role = kit.make_item("board", "button", "Setting")
        self.assertEqual(parity._suggest(ref, [other_screen, other_role], 0.8), [])
        self.assertEqual(len(parity._suggest(ref, [kit.make_item("board", "link", "Setting")], 0.8)), 1)

    def test_token_changed_dimension_and_none(self):
        """parity::_token_changed：任一侧 dimension 走 px 比较；解析不出（None）= 变化；等值不变。"""
        dim = {"$type": "dimension", "$value": "400px"}
        self.assertFalse(parity._token_changed(dim, {"$type": "string", "$value": "400px"}, 1.0))
        self.assertTrue(parity._token_changed(dim, {"$type": "dimension", "$value": "auto"}, 1.0))
        self.assertTrue(parity._token_changed({"$type": "dimension", "$value": "x"}, dim, 1.0))
        self.assertTrue(parity._token_changed(dim, {"$type": "dimension", "$value": "402px"}, 1.0))
        self.assertFalse(parity._token_changed({"$type": "color", "$value": "#000000ff"}, {"$type": "color", "$value": "#000000FF"}, 1.0))

    def test_geometry_note_tolerance_inclusive(self):
        """parity::_geometry_note `abs(sub - declared) <= tolerance` — 正好等于容差仍算「声明一致」。"""
        self.assertIsNotNone(parity._geometry_note(400.0, 401.0, 1.0))
        self.assertIsNone(parity._geometry_note(400.0, 402.0, 1.0))
        self.assertIsNone(parity._geometry_note(400.0, None, 1.0))

    def test_pair_hit_strict_less_than(self):
        """parity::_pair_hit `ratio < floor` — 正好 4.5 不命中。"""
        table = {"fg": {"$value": "#767676ff"}, "bg": {"$value": "#ffffffff"}}  # 4.54
        self.assertIsNone(parity._pair_hit("light", table, ["fg", "bg"], parity.load_rules(), {"contrast_text": 4.54}))
        self.assertIsNotNone(parity._pair_hit("light", table, ["fg", "bg"], parity.load_rules(), {"contrast_text": 4.55}))
        self.assertIsNone(parity._pair_hit("light", table, ["fg", "missing"], parity.load_rules(), THR))

    def test_heading_order_skip_rule(self):
        """parity::rule_heading_order `level > prev + 1` — h1→h2 不算跳级，h1→h3 算；无 level 的 heading 跳过。"""
        inv_ok = kit.make_inventory([kit.make_item("s", "heading", "A", level=1), kit.make_item("s", "heading", "B", level=2)])
        self.assertEqual(parity.rule_heading_order(inv_ok, parity.load_rules(), THR), [])
        inv_skip = kit.make_inventory([kit.make_item("s", "heading", "A", level=1), kit.make_item("s", "heading", "B", level=None),
                                       kit.make_item("s", "heading", "C", level=3)])
        self.assertEqual(len(parity.rule_heading_order(inv_skip, parity.load_rules(), THR)), 1)

    def test_red_reasons_each_channel(self):
        """parity::_item_red / _rule_red / red_reasons — 每条判红通道独立，pending 与 WAIVED 不判红。"""
        base = {"items": [], "tokens": [], "theme_default": None, "geometry": [], "rules": [], "ledger_problems": []}
        self.assertEqual(parity.red_reasons(dict(base)), [])
        self.assertEqual(parity.red_reasons(dict(base, items=[{"status": "MISSING", "ledger": "pending"}])), [])
        self.assertEqual(parity.red_reasons(dict(base, items=[{"status": "MISSING", "ledger": None}])), ["items"])
        self.assertEqual(parity.red_reasons(dict(base, items=[{"status": "WAIVED", "ledger": "waived"}])), [])
        self.assertEqual(parity.red_reasons(dict(base, tokens=[{"status": "CHANGED"}])), ["tokens"])
        self.assertEqual(parity.red_reasons(dict(base, theme_default={"status": "CHANGED"})), ["theme"])
        self.assertEqual(parity.red_reasons(dict(base, theme_default={"status": "PRESENT"})), [])
        self.assertEqual(parity.red_reasons(dict(base, geometry=[{"status": "CHANGED"}])), ["geometry"])
        self.assertEqual(parity.red_reasons(dict(base, rules=[{"status": "hit", "severity": "minor"}])), [])
        self.assertEqual(parity.red_reasons(dict(base, rules=[{"status": "WAIVED", "severity": "serious"}])), [])
        self.assertEqual(parity.red_reasons(dict(base, rules=[{"status": "hit", "severity": "serious"}])), ["rules"])
        self.assertEqual(parity.red_reasons(dict(base, ledger_problems=[{"kind": "x", "line": "y"}])), ["ledger"])
        self.assertTrue(parity.is_red(dict(base, ledger_problems=[{"kind": "x", "line": "y"}])))

    def test_waiver_growth_acknowledged_by_full_key_or_id(self):
        """parity::ledger_shrink_check — acknowledged 可写整键或末段 id；无理由永远是问题。"""
        ledgers = dict(EMPTY, waivers={"wcag.lang::document": "ok"})
        base = {"pending": "", "waivers": ""}
        self.assertEqual(parity.ledger_shrink_check(ledgers, base, ["wcag.lang::document"]), [])
        self.assertEqual(parity.ledger_shrink_check(ledgers, base, ["document"]), [])
        self.assertEqual(len(parity.ledger_shrink_check(ledgers, base, ["other"])), 1)
        self.assertEqual(len(parity.ledger_shrink_check(dict(EMPTY, waivers={"a": ""}), base, ["a"])), 1)

    def test_extras_exclude_dynamic_and_hidden(self):
        """parity::compare_items extras `not matched and not dynamic and visible`。"""
        sub = [kit.make_item("b", "button", "Go"), kit.make_item("b", "button", "{dynamic}"), kit.make_item("b", "button", "Hid", visible=False)]
        result = parity.compare_items(kit.make_inventory(sub), kit.make_inventory([]), EMPTY, THR)
        self.assertEqual(result["extras"], ["control:b:button:go"])

    def test_visible_focusable_defaults_and_count(self):
        """parity::_visible / _focusable 无 states = True；_count_of 缺省 1；_both_differ 缺一侧不裁。"""
        bare = {"states": {}}
        self.assertTrue(parity._visible(bare) and parity._focusable(bare))
        self.assertFalse(parity._visible({"states": {"s": {"visible": False}}}))
        self.assertEqual(parity._count_of({"count": None}), 1)
        self.assertFalse(parity._both_differ(None, "left"))
        self.assertTrue(parity._both_differ(0, 1))
        self.assertFalse(parity._both_differ(0, 0))

    def test_waiver_for_theme_requires_rule(self):
        """parity::waiver_for `if rule_id and theme` — 只有 theme 没有 rule 不拼三段键。"""
        waivers = {"x::control:a::dark": "r"}
        self.assertIsNone(parity.waiver_for(waivers, "control:a", None, "dark"))
        self.assertIsNotNone(parity.waiver_for(waivers, "control:a", "x", "dark"))


class RunnerBoundariesTestCase(unittest.TestCase):
    def _plan(self, steps, kind="cmd", note=None):
        return {"kind": kind, "steps": [{"argv": list(s), "cwd": "/r", "env": None} for s in steps], "tool": "t", "post": None, "note": note}

    def test_status_from_runs_each_branch(self):
        """run_ui::_status_from_runs：rc -2 = could not start；rc ≠ 0 = exit code；substituted 记 note；0 = pass。"""
        rr = kit.lc.RunResult
        self.assertIn("could not start", run_ui._status_from_runs(self._plan([["a"]]), [rr(-2, "", "nope")], 5)[1])
        self.assertEqual(run_ui._status_from_runs(self._plan([["a"], ["b"]]), [rr(3, "", "")], 5), ("fail", "exit code 3 (step 1/2)"))
        self.assertEqual(run_ui._status_from_runs(self._plan([["a"]], "substituted", "weak"), [rr(0, "", "")], 5), ("substituted", "weak"))
        self.assertEqual(run_ui._status_from_runs(self._plan([["a"]], "substituted"), [rr(0, "", "")], 5), ("substituted", "substitute ran"))
        self.assertEqual(run_ui._status_from_runs(self._plan([["a"], ["b"]]), [rr(0, "", ""), rr(0, "", "")], 5), ("pass", "2 step(s) exit 0"))

    def test_run_steps_stops_at_first_failure(self):
        """run_ui::_run_steps `if not res.ok: break` — 第一步失败第二步不跑。"""
        runner = kit.FakeRunner([("first", (1, "", "")), ("second", (0, "", ""))])
        ctx = checks_ui.make_ctx("/r", kit.fake_det(["b.html"]))
        res = run_ui._run_steps("seed_probe", self._plan([["first"], ["second"]]), ctx, runner, 5)
        self.assertEqual((res["status"], res["steps_run"]), ("fail", 1))
        self.assertEqual(len(runner.calls), 1)

    def test_merge_post_only_demotes_pass(self):
        """run_ui::_merge_post `substituted and status == pass` — fail 不会被改成 substituted。"""
        self.assertEqual(run_ui._merge_post({"status": "fail"}, {}, True)["status"], "fail")
        self.assertEqual(run_ui._merge_post({"status": "pass"}, {}, True)["status"], "substituted")
        self.assertEqual(run_ui._merge_post({"status": "pass"}, {"status": "fail"}, False)["status"], "fail")

    def test_run_all_phase_order(self):
        """run_ui::run_all — phase 1 先于 2 先于 3（`phase_of[c] == phase`）。"""
        order = []

        def make(name):
            return {"kind": "internal", "tool": "internal", "steps": [], "fn": lambda ctx: (order.append(name), {"status": "pass", "summary": "", "details": {}})[1]}
        plans = {"pair_structure": make("p3"), "structure_runtime": make("p2"), "surface_detect": make("p1")}
        ctx = checks_ui.make_ctx("/r", kit.fake_det(["b.html"]))
        run_ui.run_all(plans, ctx, kit.FakeRunner(), {k: 5 for k in plans}, 1)
        self.assertEqual(order, ["p1", "p2", "p3"])

    def test_on_changed_screen_family(self):
        """run_ui::_on_changed_screen — 无 screens = 全算；子屏按 family 命中。"""
        self.assertTrue(run_ui._on_changed_screen({"screen": "board.card"}, set()))
        self.assertTrue(run_ui._on_changed_screen({"screen": "board.card"}, {"board"}))
        self.assertFalse(run_ui._on_changed_screen({"screen": "trash"}, {"board"}))

    def test_fix_rules_visual_ledger_filters(self):
        """run_ui::_fix_rules 只收 hit + serious/critical；_fix_visual 只收 CHANGED 的 shot；_fix_ledger 只在 fail 时收 golden/threshold。"""
        results = [{"id": "a11y_static", "status": "fail", "details": {"hits": [
            {"status": "hit", "severity": "minor", "rule_id": "r", "id": "x", "measured": 1, "threshold": 2},
            {"status": "WAIVED", "severity": "serious", "rule_id": "r", "id": "y", "measured": 1, "threshold": 2},
            {"status": "hit", "severity": "serious", "rule_id": "r", "id": "z", "measured": 1, "threshold": 2}]}},
            {"id": "visual_diff", "status": "fail", "details": {"rows": [{"id": "s1", "item_status": "PRESENT"}, {"id": "s2", "item_status": "CHANGED", "changed_pct": 0.02, "threshold": 0.0}]}},
            {"id": "golden_manifest", "status": "pass", "details": {"unreviewed": ["g.png"]}},
            {"id": "thresholds_unmoved", "status": "fail", "details": {"loosened": ["pixel_tolerance"]}}]
        self.assertEqual([f["item"] for f in run_ui._fix_rules(results)], ["z (1 < 2)"])
        self.assertEqual([f["item"] for f in run_ui._fix_visual(results)], ["s2"])
        self.assertEqual([f["item"] for f in run_ui._fix_ledger(results)], ["pixel_tolerance"])

    def test_sensors_table_ran_excludes_na_unavailable(self):
        """run_ui::sensors_table `status not in (na, unavailable)`。"""
        det = kit.fake_det(["b.html"])
        results = [{"sensor": "structure", "status": "pass"}, {"sensor": "tokens", "status": "unavailable"}, {"sensor": "visual", "status": "na"}]
        ran = {row["sensor"]: row["ran"] for row in run_ui.sensors_table(det, results)}
        self.assertEqual(ran, {"structure": True, "tokens": False, "visual": False})

    def test_items_summary_counts_pending(self):
        """run_ui::_items_summary `ledger == pending`。"""
        ctx = checks_ui.make_ctx("/r", kit.fake_det(["b.html"]))
        ctx["state"]["pair_source"] = {"rows": [{"id": "a", "status": "MISSING", "ledger": "pending"}, {"id": "b", "status": "PRESENT", "ledger": None}], "extras": ["e"]}
        summary = run_ui._items_summary(ctx)
        self.assertEqual((summary["pending"], summary["total"], len(summary["rows"]), summary["extras"]), (1, 2, 1, ["e"]))

    def test_tool_versions_skips_na_and_unknown(self):
        """run_ui::tool_versions / _version_line — na/unavailable 的工具不问版本；失败 = unknown。"""
        runner = kit.FakeRunner([("node --version", (0, "v22.0.0\n", "")), ("odiff", (1, "", ""))])
        results = [{"tool": "node", "status": "pass"}, {"tool": "odiff", "status": "pass"}, {"tool": "git", "status": "na"}, {"tool": None, "status": "pass"}]
        versions = run_ui.tool_versions(results, runner, "py")
        self.assertEqual(versions["node"], "v22.0.0")
        self.assertEqual(versions["odiff"], "unknown")
        self.assertNotIn("git", versions)

    def test_md_list_cap(self):
        """run_ui::_md_list `len(items) > _MD_CAP` — 正好 40 行不加省略行，41 行加。"""
        forty = run_ui._md_list("t", list(range(40)), str)
        forty_one = run_ui._md_list("t", list(range(41)), str)
        self.assertFalse(any("more" in line for line in forty))
        self.assertTrue(any("and 1 more" in line for line in forty_one))
        self.assertEqual(run_ui._md_list("t", [], str, empty="nothing")[2], "- nothing")

    def test_notes_gitignore_hint(self):
        """run_ui::_notes `".test-ui" not in .gitignore` → 提示；有则不提。"""
        with tempfile.TemporaryDirectory() as tmp:
            det = kit.fake_det(["b.html"], runtime_hint=None, config_source=None)
            ctx = checks_ui.make_ctx(tmp, det)
            self.assertTrue(any("gitignore" in n for n in run_ui._notes(det, ctx)))
            kit.make_repo(tmp, {".gitignore": ".test-ui/reports/\n"})
            self.assertEqual(run_ui._notes(det, ctx), [])


class SensorsBoundariesTestCase(unittest.TestCase):
    def test_rules_verdict_minor_count(self):
        """sensors::_rules_verdict minor = hit 且非 serious（and → or 会把 WAIVED 也算 minor）。"""
        hits = [{"status": "hit", "severity": "minor"}, {"status": "WAIVED", "severity": "minor"}, {"status": "hit", "severity": "moderate"}]
        res = sensors._rules_verdict(hits, "t")
        self.assertEqual((res["status"], res["details"]["minor"], res["details"]["serious"]), ("pass", 2, 0))

    def test_dims_for_tier(self):
        """sensors::_dims_for_tier — tier ≥ 3 全矩阵；否则默认主题优先 dims.default_theme。"""
        dims = {"themes": ["dark", "light"], "default_theme": "light", "viewports": [{"name": "a"}, {"name": "b"}], "languages": ["zh", "en"]}
        self.assertEqual(sensors._dims_for_tier(dims, True), (["dark", "light"], dims["viewports"], ["zh", "en"]))
        self.assertEqual(sensors._dims_for_tier(dims, False), (["light"], [{"name": "a"}], ["zh"]))
        self.assertEqual(sensors._dims_for_tier({}, False), (["light"], [{"name": "desktop", "w": 1440, "h": 900}], ["zh"]))
        self.assertEqual(sensors._dims_for_tier({"themes": ["dark"]}, False)[0], ["dark"])

    def test_off_token_literals_cap_boundary(self):
        """sensors::check_off_token_literals `len(hits) > cap` — 等于 cap 放行。"""
        det = kit.fake_det(["b.html"], thresholds=dict(THR, max_off_token_literals=2))
        ctx = checks_ui.make_ctx("/r", det)
        two = [{"file": "a.css", "line": i, "property": "color", "value": "#fff", "family": "color"} for i in range(2)]
        ctx["state"]["subject_tokens"] = kit.make_tokens({"light": {}}, literals=two)
        self.assertEqual(sensors.check_off_token_literals(ctx)["status"], "pass")
        ctx["state"]["subject_tokens"] = kit.make_tokens({"light": {}}, literals=two + [dict(two[0], line=9)])
        self.assertEqual(sensors.check_off_token_literals(ctx)["status"], "fail")

    def test_color_drift_conditions(self):
        """sensors::_color_drift — 非颜色 token 不算；解析不出不算；等值不算。"""
        tok = {"$type": "color", "$value": "#000000ff"}
        self.assertFalse(sensors._color_drift(None, "rgb(0,0,0)"))
        self.assertFalse(sensors._color_drift({"$type": "dimension", "$value": "1px"}, "2px"))
        self.assertFalse(sensors._color_drift(tok, "var(--x)"))
        self.assertFalse(sensors._color_drift(tok, "rgb(0, 0, 0)"))
        self.assertTrue(sensors._color_drift(tok, "rgb(1, 0, 0)"))

    def test_matrix_and_i18n_and_overflow(self):
        """sensors::check_matrix_themes_viewports `< 2`；check_i18n_parity 半翻译判红；_overflows 严格大于。"""
        ctx = checks_ui.make_ctx("/r", kit.fake_det(["b.html"]))
        ctx["state"]["runtime"] = {"inventory": kit.make_inventory([], dims={"themes": ["light", "dark"], "viewports": [{"name": "d"}], "languages": ["zh"]})}
        self.assertEqual(sensors.check_matrix_themes_viewports(ctx)["status"], "pass")
        ctx["state"]["runtime"]["inventory"]["dims"]["themes"] = ["light"]
        self.assertEqual(sensors.check_matrix_themes_viewports(ctx)["status"], "substituted")
        half = kit.make_item("b", "button", "Go")
        half["name"]["zh"] = "走"
        ctx["state"]["subject_source"] = kit.make_inventory([half])
        self.assertEqual(sensors.check_i18n_parity(ctx)["status"], "fail")
        half["name"]["en"] = "Go"
        self.assertEqual(sensors.check_i18n_parity(ctx)["status"], "pass")
        self.assertFalse(sensors._overflows({"scrollWidth": 900, "clientWidth": 900}))
        self.assertTrue(sensors._overflows({"scrollWidth": 901, "clientWidth": 900}))
        self.assertFalse(sensors._overflows(None))

    def test_mode_note_substituted_only_vs_runtime_reference(self):
        """sensors::_mode_note `ref_mode == runtime` — frozen/source 参照给 pass。"""
        det = kit.fake_det(["b.html"])
        self.assertEqual(sensors._mode_note(checks_ui.make_ctx("/r", det), "structure")[0], "pass")
        det["sides"]["reference"]["mode"]["structure"] = "runtime"
        self.assertEqual(sensors._mode_note(checks_ui.make_ctx("/r", det), "structure")[0], "substituted")

    def test_visual_verdict_and_shot_rows(self):
        """sensors::_visual_verdict `over or capped or not manifest.ok`；一张都没比（全 no_golden）= unavailable 不是 pass；
        _shot_row 没 golden = no_golden，坏 PNG = CHANGED。"""
        rows = [{"id": "s", "status": "no_golden", "item_status": None}]
        self.assertEqual(sensors._visual_verdict(rows, {"ok": True})["status"], "unavailable")  # nothing was compared
        self.assertEqual(sensors._visual_verdict(rows, {"ok": False})["status"], "fail")
        compared = rows + [{"id": "t", "status": "same", "item_status": "PRESENT"}]
        self.assertEqual(sensors._visual_verdict(compared, {"ok": True})["status"], "pass")
        self.assertIn("1 shot(s) within threshold (1 without golden)", sensors._visual_verdict(compared, {"ok": True})["summary"])
        self.assertEqual(sensors._visual_verdict([{"id": "s", "item_status": "CHANGED"}], {"ok": True})["status"], "fail")
        capped = sensors._visual_verdict([{"id": "s", "status": "same", "item_status": "PRESENT", "over_mask_cap": True}], {"ok": True})
        self.assertEqual((capped["status"], capped["details"]["over_mask_cap"]), ("fail", ["s"]))
        with tempfile.TemporaryDirectory() as tmp:
            ctx = checks_ui.make_ctx("/r", kit.fake_det(["b.html"]), out=tmp)
            shot = os.path.join(tmp, "board.png")
            with open(shot, "wb") as fh:
                fh.write(kit.make_png(4, 4))
            self.assertEqual(sensors._shot_row(ctx, {"id": "x", "path": shot, "screen": "board"}, os.path.join(tmp, "none"))["status"], "no_golden")
            os.makedirs(os.path.join(tmp, "g"))
            with open(os.path.join(tmp, "g", "board.png"), "wb") as fh:
                fh.write(b"not a png")
            self.assertEqual(sensors._shot_row(ctx, {"id": "x", "path": shot, "screen": "board"}, os.path.join(tmp, "g"))["item_status"], "CHANGED")
            with open(os.path.join(tmp, "g", "board.png"), "wb") as fh:
                fh.write(kit.make_png(4, 4))
            self.assertEqual(sensors._shot_row(ctx, {"id": "x", "path": shot, "screen": "board"}, os.path.join(tmp, "g"))["item_status"], "PRESENT")
            # odiff reports no mask area: the driver's masked_ratio on the shot record feeds the cap — over 0.2 red, exactly 0.2 not
            over = sensors._shot_row(ctx, {"id": "x", "path": shot, "screen": "board", "masked_ratio": 0.6}, os.path.join(tmp, "g"))
            self.assertEqual((over["masked_ratio"], over["over_mask_cap"]), (0.6, True))
            at_cap = sensors._shot_row(ctx, {"id": "x", "path": shot, "screen": "board", "masked_ratio": 0.2}, os.path.join(tmp, "g"))
            self.assertFalse(at_cap["over_mask_cap"])

    def test_wait_ready_deadline_strict(self):
        """sensors::wait_ready `clock() < deadline` — 到点即停。"""
        ticks = iter([0.0, 0.2, 0.9, 1.0])
        calls = []

        def fetch(url):
            calls.append(url)
            raise OSError()
        self.assertFalse(sensors.wait_ready("u", fetch, 1.0, sleep=lambda s: None, clock=lambda: next(ticks)))
        self.assertEqual(len(calls), 2)


class CommonBoundariesTestCase(unittest.TestCase):
    def test_linear_and_alpha_branches(self):
        """testui_common::_linear `c <= 0.03928`；contrast_ratio `fg[3] < 1.0` 才合成。"""
        self.assertAlmostEqual(tc._linear(10), (10 / 255.0) / 12.92)
        self.assertGreater(tc._linear(11), (11 / 255.0) / 12.92)
        opaque = tc.contrast_ratio((0, 0, 0, 1.0), (255, 255, 255, 1.0))
        half = tc.contrast_ratio((0, 0, 0, 0.5), (255, 255, 255, 1.0))
        self.assertEqual(opaque, 21.0)
        self.assertLess(half, opaque)

    def test_paeth_and_filters(self):
        """testui_common::_paeth 三分支；_unfilter_paeth / _unfilter_average `i >= bpp`。"""
        self.assertEqual(tc._paeth(10, 20, 15), 15)   # p=15 → pa 5, pb 5, pc 0 → c
        self.assertEqual(tc._paeth(10, 20, 5), 20)    # p=25 → pa 15, pb 5, pc 20 → b
        self.assertEqual(tc._paeth(10, 30, 25), 10)   # p=15 → pa 5, pb 15, pc 10 → a
        self.assertEqual(tc._paeth(30, 10, 25), 10)   # p=15 → pa 15, pb 5, pc 10 → b
        self.assertEqual(tc._paeth(30, 40, 5), 40)    # p=65 → pa 35, pb 25, pc 60 → b
        self.assertEqual(tc._paeth(5, 5, 5), 5)       # all equal → a
        self.assertEqual(tc._unfilter_paeth(bytearray([1, 1, 1, 1, 1, 1]), bytearray([2, 2, 2, 2, 2, 2]), 3), bytearray([3, 3, 3, 4, 4, 4]))
        self.assertEqual(tc._unfilter_average(bytearray([2, 2, 2]), bytearray([4, 4, 4]), 3), bytearray([4, 4, 4]))

    def test_ihdr_rejects_each_shape(self):
        """testui_common::_parse_ihdr `depth != 8 or color_type not in (2, 6) or interlace != 0`。"""
        import struct
        self.assertEqual(tc._parse_ihdr(struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)), (1, 1, 4))
        for depth, ctype, inter in ((16, 2, 0), (8, 3, 0), (8, 2, 1)):
            with self.assertRaises(ValueError):
                tc._parse_ihdr(struct.pack(">IIBBBBB", 1, 1, depth, ctype, 0, 0, inter))

    def test_token_value_text_and_validate_item(self):
        """testui_common::token_value_text color 归一只对字符串；_validate_item key 缺字段报 key（没有 key 就没有 role 可判，
        不重复报）；key 不是 dict（字符串 / 列表）也是错误路径而不是 AttributeError——坏参照必须落成 reference_unreadable。"""
        self.assertEqual(tc.token_value_text({"$type": "color", "$value": {"light": "#fff"}}), '{"light": "#fff"}')
        self.assertEqual(tc.token_value_text({"$type": "color", "$value": "#FFF"}), "#ffffffff")
        item = kit.make_item("b", "button", "Go")
        item["key"] = {"screen": "b"}
        self.assertEqual(tc._validate_item(0, item), ["items[0].key"])
        item["key"] = {"screen": "b", "role": "widget", "slug": "go"}
        self.assertEqual(tc._validate_item(0, item), ["items[0].role"])
        for bad_key in ("oops", ["screen", "role", "slug"], None):
            self.assertEqual(tc._validate_item(0, dict(item, key=bad_key)), ["items[0].key"])
        self.assertEqual(tc.validate_inventory({"schemaVersion": 1, "producer": [], "side": {}, "items": {"a": 1}}), ["producer.mode", "items"])


class TokensBoundariesTestCase(unittest.TestCase):
    def test_blocks_media_scope_and_native_path(self):
        """tokens::_blocks — 只有 dark 媒体块里的 :root 算 dark；非 dark 媒体块不算；_native_path 单段。"""
        css = "@media (max-width: 900px) { :root { --a: 1px; } } @media (prefers-color-scheme: dark) { :root { --b: 2px; } } :root { --c: 3px; }"
        themes = tk.parse_css_variables(css)
        self.assertEqual(sorted(themes["light"]), ["--a", "--c"])
        self.assertEqual(sorted(themes["dark"]), ["--b"])
        self.assertEqual(tk._native_path("--native-layout-strip"), "layout.strip")
        self.assertEqual(tk.token_path("--native-color-green-light", "color"), "color.green-light")

    def test_resolve_var_and_literal_rules(self):
        """tokens::_resolve_var 只解析同表内一层；_is_literal 排除 var/inherit/0。"""
        decls = {"--a": {"value": "#fff", "source": "x"}}
        self.assertEqual(tk._resolve_var("var(--a)", decls), "#fff")
        self.assertEqual(tk._resolve_var("var(--b)", decls), "var(--b)")
        self.assertFalse(tk._is_literal("color", "inherit"))
        self.assertFalse(tk._is_literal("radius", "0"))
        self.assertTrue(tk._is_literal("radius", "6px"))
        self.assertFalse(tk._is_literal("radius", "6em"))
        self.assertTrue(tk._is_literal("color", "rgba(0,0,0,.5)"))

    def test_under_roots_and_tables(self):
        """tokens::_under_roots `r == "." or startswith`；_is_named_table / _is_root_table 都要含变量。"""
        self.assertTrue(tk._under_roots("x/y.css", ()))
        self.assertTrue(tk._under_roots("x/y.css", ["."]))
        self.assertTrue(tk._under_roots("web/src/a.css", ["web"]))
        self.assertFalse(tk._under_roots("docs/a.css", ["web"]))
        with tempfile.TemporaryDirectory() as tmp:
            kit.make_repo(tmp, {"tokens.css": ".a { color: red; }", "b.css": ":root { --x: 1; }", "c.css": ":root { color: red; }"})
            self.assertFalse(tk._is_named_table(tmp, "tokens.css"))
            self.assertTrue(tk._is_root_table(tmp, "b.css"))
            self.assertFalse(tk._is_root_table(tmp, "c.css"))
            self.assertEqual(tk._token_tables(tmp, ["tokens.css", "b.css", "c.css"]), ["b.css"])


class VisualBoundariesTestCase(unittest.TestCase):
    def test_mask_edges_and_neighbours(self):
        """visual::_in_masks 半开区间；masked_ratio 空图 0；_neighbours 只走网格内的变化 tile。"""
        masks = [[10, 10, 5, 5]]
        self.assertTrue(visual._in_masks(10, 10, masks))
        self.assertTrue(visual._in_masks(14, 14, masks))
        self.assertFalse(visual._in_masks(15, 14, masks))
        self.assertFalse(visual._in_masks(9, 10, masks))
        self.assertEqual(visual.masked_ratio(0, 10, masks), 0.0)
        tiles = [1, 0, 1, 1]  # 2x2 grid
        self.assertEqual(sorted(visual._neighbours(tiles, 2, 2, 0, 0)), [2])
        self.assertEqual(sorted(visual._neighbours(tiles, 2, 2, 1, 1)), [2])

    def test_instrument_prefers_odiff_when_parseable(self):
        """visual::_instrument — odiff 可用且解析成功才用它；否则内置。"""
        with tempfile.TemporaryDirectory() as tmp:
            shot = os.path.join(tmp, "s.png")
            with open(shot, "wb") as fh:
                fh.write(kit.make_png(4, 4))
            good = kit.FakeRunner(default=(0, "", ""))
            self.assertEqual(visual._instrument(shot, shot, 0, [], good, tmp, {"odiff": "/bin/odiff"})["tool"], "odiff")
            self.assertEqual(visual._instrument(shot, shot, 0, [], good, tmp, {})["tool"], "internal")
            self.assertEqual(visual._instrument(shot, shot, 0, [], None, tmp, {"odiff": "/bin/odiff"})["tool"], "internal")


class DetectBoundariesTestCase(unittest.TestCase):
    def test_small_helpers(self):
        """detect_ui::_root_of `len(dirs) > 1`；_web_dir；_builtin_launch 两者缺一 = None；_docs_or_ledger；_shared_component。"""
        self.assertEqual(detect_ui._root_of(["a/b/x.tsx"], (".tsx",)), "a/b")
        self.assertEqual(detect_ui._root_of(["a/b/x.tsx", "a/c/y.tsx"], (".tsx",)), "a")
        self.assertEqual(detect_ui._root_of(["x.html"], (".html",)), ".")
        self.assertEqual(detect_ui._web_dir(["web/package.json", "web/src/a.ts", "other/package.json"]), "web")
        self.assertEqual(detect_ui._web_dir(["other/package.json"]), "other")
        self.assertIsNone(detect_ui._web_dir(["node_modules/x/package.json"]))
        self.assertIsNone(detect_ui._builtin_launch({"server_module": "s", "demo_seed": None}))
        self.assertIsNotNone(detect_ui._builtin_launch({"server_module": "s", "demo_seed": "d"}))
        self.assertTrue(detect_ui._docs_or_ledger("ui/parity/pending.txt"))
        self.assertTrue(detect_ui._docs_or_ledger("README.md"))
        self.assertFalse(detect_ui._docs_or_ledger("web/src/a.tsx"))
        self.assertTrue(detect_ui._shared_component("web/src/components/chrome/X.tsx"))
        self.assertFalse(detect_ui._shared_component("web/src/components/board/X.tsx"))

    def test_rec_rules_screen_threshold(self):
        """detect_ui::_rec_rules `len(screens) > 3` — 3 屏 tier 3、4 屏 tier 4。"""
        fired = {"tokens_changed"}
        self.assertEqual(detect_ui._rec_rules(["x.css"], fired, ["a", "b", "c"])[0], 3)
        self.assertEqual(detect_ui._rec_rules(["x.css"], fired, ["a", "b", "c", "d"])[0], 4)
        self.assertIsNone(detect_ui._rec_rules(["act/x.py"], set(), []))

    def test_node_modules_fallback_and_cache(self):
        """detect_ui::_node_modules `not playwright and test` → playwright-core；无 node 全 None。"""
        self.assertEqual(detect_ui._node_modules(kit.FakeRunner(), "/w", False), {"playwright": None, "playwright_test": None, "axe": None})
        runner = kit.FakeRunner([('"playwright"', (1, "", "")), ('"@playwright/test"', (0, "/t\n", "")), ("playwright-core", (0, "/core\n", "")), ("axe-core", (1, "", ""))])
        self.assertEqual(detect_ui._node_modules(runner, "/w", True)["playwright"], "/core")
        self.assertIsNone(detect_ui._resolve_node_module(kit.FakeRunner(default=(0, "\n", "")), "/w", "x"))

    def test_diff_parser_context_lines(self):
        """detect_ui::DiffParser._body — 上下文行推进行号，`-` 行与 `\\ No newline` 不推进。"""
        parser = detect_ui.DiffParser()
        for raw in ["diff --git a/x.tsx b/x.tsx", "+++ b/x.tsx", "@@ -1,2 +10,3 @@", " ctx", "-old", "\\ No newline at end of file", "+new"]:
            parser.feed(raw)
        self.assertEqual(parser.added_text["x.tsx"], [(11, "new")])
        parser.feed("+++ /dev/null")
        self.assertIsNone(parser.file) if parser.in_header else None

    def test_ledgers_base_texts_only_with_commit(self):
        """detect_ui::detect_ledgers `base_commit and has` — 没有 merge-base 就没有 base_texts。"""
        with tempfile.TemporaryDirectory() as tmp:
            kit.make_repo(tmp, {"ui/parity/pending.txt": "a:b:c:d\n"})
            runner = kit.FakeRunner([("git show", (0, "a:b:c:d\n", ""))])
            self.assertIsNone(detect_ui.detect_ledgers(runner, tmp, None)["base_texts"])
            with_base = detect_ui.detect_ledgers(runner, tmp, "cafe")
            self.assertEqual(with_base["base_texts"]["pending"], "a:b:c:d")
            self.assertIsNone(detect_ui.detect_ledgers(runner, os.path.join(tmp, "none"), "cafe")["dir"])


class InventoryBoundariesTestCase(unittest.TestCase):
    def test_quote_and_scan_helpers(self):
        """inventory_a11y::_Quote / _scan_expr / _scan_tag / _in_spans 边界。"""
        quote = inv._Quote()
        self.assertFalse(quote.feed("a"))
        self.assertTrue(quote.feed('"'))
        self.assertTrue(quote.feed("x"))
        self.assertTrue(quote.feed('"'))
        self.assertFalse(quote.feed("y"))
        self.assertEqual(inv._scan_expr("{a{b}c}rest", 0), 7)
        self.assertEqual(inv._scan_expr('{"}"}x', 0), 5)
        self.assertEqual(inv._scan_tag("<a b={() => 1} c='>'>text", 0), 21)
        self.assertTrue(inv._in_spans(5, [(0, 5)]))
        self.assertFalse(inv._in_spans(6, [(0, 5)]))
        self.assertEqual(inv.gated_spans("{flags.x && (<b/>)} {y}"), [(0, 18)])

    def test_close_and_push_rules(self):
        """inventory_a11y::_close 弹到同名；_pushes void/自闭合不入栈；_skip_state 只在同名闭合时退出。"""
        root = inv.Node("", {}, 0)
        stack = [inv.Node("div", {}, 0, root), inv.Node("span", {}, 0, root)]
        inv._close(stack, "div")
        self.assertEqual(stack, [])
        stack = [inv.Node("div", {}, 0, root)]
        inv._close(stack, "nope")
        self.assertEqual(len(stack), 1)
        self.assertFalse(inv._pushes("img", False))
        self.assertFalse(inv._pushes("div", True))
        self.assertTrue(inv._pushes("div", False))
        self.assertEqual(inv._skip_state(True, "script", "script"), None)
        self.assertEqual(inv._skip_state(True, "b", "script"), "script")
        self.assertEqual(inv._skip_state(False, "script", "script"), "script")

    def test_role_and_name_helpers(self):
        """inventory_a11y::_explicit_role / _input_role / _slug_for / _id_kind / _heading_level / _literal_disabled。"""
        self.assertEqual(inv._explicit_role(inv.Node("div", {"role": "switch x"}, 0)), "switch")
        self.assertEqual(inv._explicit_role(inv.Node("div", {"role": "widget"}, 0)), "generic")
        self.assertIsNone(inv._explicit_role(inv.Node("div", {"role": "  "}, 0)))
        self.assertIsNone(inv._input_role(inv.Node("input", {"type": "hidden"}, 0)))
        self.assertEqual(inv._input_role(inv.Node("input", {}, 0)), "textbox")
        self.assertEqual(inv._slug_for("", "button"), "unnamed")
        self.assertEqual(inv._slug_for("", "list"), "list")
        self.assertEqual(inv._slug_for(" Go ", "button"), "go")
        self.assertEqual((inv._id_kind("navigation"), inv._id_kind("heading"), inv._id_kind("button")), ("landmark", "heading", "control"))
        self.assertEqual(inv._heading_level(inv.Node("h3", {}, 0)), 3)
        self.assertIsNone(inv._heading_level(inv.Node("hr", {}, 0)))
        self.assertTrue(inv._literal_disabled(inv.Node("button", {"disabled": ""}, 0)))
        self.assertTrue(inv._literal_disabled(inv.Node("button", {"disabled": "true"}, 0)))
        self.assertFalse(inv._literal_disabled(inv.Node("button", {"disabled": "{dynamic}"}, 0)))
        self.assertFalse(inv._literal_disabled(inv.Node("button", {}, 0)))

    def test_emits_item_and_static_node(self):
        """inventory_a11y::_emits_item — generic 不成条目；static 需字面量；_static_text_node 排除组件。"""
        text_node = inv.Node("span", {}, 0)
        text_node.children.append("hello")
        empty = inv.Node("span", {}, 0)
        self.assertFalse(inv._emits_item(empty, "generic"))
        self.assertFalse(inv._emits_item(empty, None))
        self.assertTrue(inv._emits_item(empty, "button"))
        self.assertFalse(inv._emits_item(empty, "static"))
        self.assertTrue(inv._emits_item(text_node, "static"))
        comp = inv.Node("Badge", {}, 0)
        comp.children.append("x")
        self.assertFalse(inv._static_text_node(comp))
        self.assertTrue(inv._static_text_node(text_node))

    def test_merge_runtime_gated_and_ordinals(self):
        """inventory_a11y::_merge_runtime_item — 首见 all_on = gated；default 见到 → 不 gated；_assign_ordinals 幂等。"""
        items = {}
        a = kit.make_item("b", "button", "X")
        inv._merge_runtime_item(items, dict(a), "all_on")
        self.assertTrue(items[a["id"]]["gated"])
        inv._merge_runtime_item(items, dict(a, states={"dark::d::zh::rest": {}}), "default")
        self.assertFalse(items[a["id"]]["gated"])
        self.assertIn("dark::d::zh::rest", items[a["id"]]["states"])
        dup = [kit.make_item("b", "button", "X"), kit.make_item("b", "button", "X")]
        inv._assign_ordinals(dup)
        inv._assign_ordinals(dup)
        self.assertEqual([i["id"] for i in dup], ["control:b:button:x", "control:b:button:x#2"])
        self.assertEqual(dup[0]["count"], 2)

    def test_native_helpers(self):
        """inventory_a11y::_source_line / _native_rail side 默认 left / _theme_default / _rows。"""
        self.assertEqual(inv._source_line("Cards.swift:12"), 12)
        self.assertIsNone(inv._source_line("Cards.swift"))
        self.assertIsNone(inv._source_line(None))
        items, marks = inv._native_rail({"rail": {"items": []}})
        self.assertEqual(marks[0]["topology"]["side"], "left")
        self.assertIsNone(inv._theme_default({"theme_layout": [{"id": "layout:x"}]}))
        self.assertEqual(inv._rows({"a": None}, "a"), [])
        self.assertEqual(inv._common_dir(["x.html"]), ".")

    def test_run_driver_failure_paths(self):
        """inventory_a11y::run_driver — rc≠0 → None；输出缺席 → None；坏 JSON → None。"""
        with tempfile.TemporaryDirectory() as tmp:
            out, res = inv.run_driver({"url": "u"}, "/pw", tmp, kit.FakeRunner(default=(1, "", "boom")))
            self.assertIsNone(out)
            out, res = inv.run_driver({"url": "u"}, "/pw", tmp, kit.FakeRunner(default=(0, "", "")))
            self.assertIsNone(out)

            def write_bad(argv, cwd):
                cfg = kit.tc.read_json(argv[2])
                kit.tc.write_text(cfg["out"], "{bad")
                return kit.lc.RunResult(0, "", "")
            out, res = inv.run_driver({"url": "u"}, "/pw", tmp, kit.FakeRunner([("driver", write_bad)]))
            self.assertIsNone(out)


class ReferenceBoundariesTestCase(unittest.TestCase):
    def test_dig_and_subject_defaults(self):
        """reference::_dig 非 dict 中途 → None；subject_side 无 commit → unknown。"""
        self.assertIsNone(refmod._dig({"a": 1}, "a.b"))
        self.assertEqual(refmod._dig({"a": {"b": 2}}, ".a.b"), 2)
        self.assertEqual(refmod.subject_side("/r", "web-dom", False, None)["resolved"], "sha:unknown")
        self.assertFalse(refmod._loopback("http://10.0.0.1/"))
        self.assertTrue(refmod._loopback("http://localhost:8080/x"))


class ChecksBoundariesTestCase(unittest.TestCase):
    def test_runtime_ok_and_mode_for(self):
        """checks_ui::_runtime_ok `== runtime`；_mode_for runtime 层看 runtime 可用性，其余看 subject mode。"""
        det = kit.fake_det(["b.html"])
        ctx = checks_ui.make_ctx("/r", det)
        self.assertFalse(checks_ui._runtime_ok(ctx))
        self.assertEqual(checks_ui._mode_for(ctx, "visual_diff"), "unavailable")
        self.assertEqual(checks_ui._mode_for(ctx, "tokens_source"), "source")
        det["sides"]["subject"]["mode"] = {"structure": "runtime", "tokens": "runtime", "visual": "runtime"}
        self.assertTrue(checks_ui._runtime_ok(ctx))
        self.assertEqual(checks_ui._mode_for(ctx, "visual_diff"), "runtime")
        self.assertEqual(checks_ui.make_ctx("/r", det)["sel"], {})


if __name__ == "__main__":
    unittest.main()
