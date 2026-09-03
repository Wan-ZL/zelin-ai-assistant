"""test-ui skill · runner 判例：选择构造（默认勾选 / 未知 id / chosen_by）、每档超时（第 5 档无限）、phase 顺序、
报告三分「未跑」（N-A / UNAVAILABLE / SUBSTITUTED）、核心层跳过无理由 = INCOMPLETE、退出码 0/1/3/2、
fixture 对（subject vs dir:ref）真跑一遍 → RED 且 fix-first 顺序正确、每个植入缺陷都在报告里；ref vs ref → 只剩
UNAVAILABLE（runtime 缺席，INCOMPLETE 而非 GREEN——诚实）；report.md 的固定小节；parity_disagreement。
零真子进程（runner 全 fake；fixture 提取是纯 Python）。

法典：docs/CONTRACT.md §58 / §62；设计 vnext2-plan R2.8 / D14（R2.8.2 两种调用）。
"""
import contextlib
import io
import json
import os
import tempfile
import types
import unittest

from tests import skill_test_ui_testkit as kit

import checks_ui as checks  # noqa: E402
import detect_ui  # noqa: E402
import run_ui  # noqa: E402

STATIC_TIER1 = ["surface_detect", "structure_source", "tokens_source", "ledger_lint", "golden_manifest", "thresholds_unmoved",
                "pair_structure", "pair_tokens", "theme_default_declared", "off_token_literals", "contrast_pairs", "a11y_static", "seed_guard"]
CONFIG = {"tokens": {"contrast_pairs": [["color.text-tertiary", "color.bg"], ["color.text-primary", "color.bg"]]},
          "geometry": {"layout.lane.width": {"screen": "board", "role": "list", "measure": "width"}},
          "screens": [{"id": "board", "route": "board.html", "source": ["board.html"]}, {"id": "settings", "route": "settings.html", "source": ["settings.html"]}]}


def _sel(ids, tier=1, **extra):
    sel = {"tier": tier, "checks": ids, "against": "dir:/ref", "screens": [],
           "ask": {"recommended": 1, "reason": "t", "chosen": tier, "chosen_by": "user"}, "skip_reasons": {}}
    sel.update(extra)
    return sel


def _fixture_pair(tmp, config=CONFIG, base_pending=None):
    """subject + ref 拷进 tmp；返回 (subject_repo, det)。detect 走真文件系统 + FakeRunner（非 git）。"""
    subject, ref = os.path.join(tmp, "subject"), os.path.join(tmp, "ref")
    kit.copy_fixture("subject", subject)
    kit.copy_fixture("ref", ref)
    if config is not None:
        kit.make_repo(subject, {"ui/parity/config.json": json.dumps(config)})
    det = detect_ui.detect(subject, against="dir:%s" % ref, runner=kit.FakeRunner(default=(1, "", "not git")), which=lambda n: None)
    if base_pending is not None:
        det["ledgers"]["base_texts"] = {"pending": base_pending, "waivers": "", "aliases": ""}
    return subject, det


class SelectionTestCase(unittest.TestCase):
    def test_selection_from_args_and_validation(self):
        det = kit.fake_det(["b.html"])
        args = types.SimpleNamespace(tier=1, checks=None, skip="seed_guard", chosen_by="headless", screens="board", selection=None)
        sel = run_ui.build_selection(args, det)
        self.assertNotIn("seed_guard", sel["checks"])
        self.assertIn("pair_structure", sel["checks"])
        self.assertEqual((sel["ask"]["chosen_by"], sel["screens"], sel["against"]), ("recommended, not confirmed", ["board"], "dir:/ref"))
        with self.assertRaises(run_ui.SelectionError):
            run_ui.build_selection(types.SimpleNamespace(tier=None, checks=None, skip=None, chosen_by="user", screens=None, selection=None), det)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "sel.json")
            kit.make_repo(tmp, {"sel.json": json.dumps({"tier": 2, "checks": ["nope_check"]})})
            with self.assertRaises(run_ui.SelectionError):
                run_ui.build_selection(types.SimpleNamespace(selection=path), det)
            kit.make_repo(tmp, {"sel.json": json.dumps({"tier": 2, "checks": ["seed_guard"]})})
            loaded = run_ui.build_selection(types.SimpleNamespace(selection=path), det)
            self.assertEqual(loaded["ask"]["chosen_by"], "recommended, not confirmed")

    def test_timeouts_by_tier(self):
        entry_t1, entry_t3 = checks.BY_ID["surface_detect"], checks.BY_ID["visual_diff"]
        self.assertEqual(run_ui.timeout_for(entry_t1, 1, {}), 300)
        self.assertEqual(run_ui.timeout_for(entry_t3, 1, {}), 3600)   # a check keeps its own tier's budget
        self.assertIsNone(run_ui.timeout_for(entry_t1, 5, {}))         # tier 5 lifts everything
        self.assertEqual(run_ui.timeout_for(entry_t1, 1, {"timeout_seconds": 42}), 42.0)


class VerdictTestCase(unittest.TestCase):
    def test_three_way_split_and_verdict(self):
        results = [{"status": s, "id": s, "reason": "r-%s" % s, "summary": "s", "details": {}} for s in ("pass", "na", "unavailable", "substituted")]
        split = run_ui.not_run(results)
        self.assertEqual([i["id"] for i in split["na"]], ["na"])
        self.assertEqual([i["reason"] for i in split["unavailable"]], ["r-unavailable"])
        self.assertEqual([i["id"] for i in split["substituted"]], ["substituted"])
        self.assertEqual(run_ui.verdict(results), ("incomplete", 3))
        self.assertEqual(run_ui.verdict([results[0]]), ("green", 0))
        self.assertEqual(run_ui.verdict([results[0]], unexplained_core_skips=1), ("incomplete", 3))
        self.assertEqual(run_ui.verdict(results + [{"status": "fail"}]), ("red", 1))

    def test_core_skips_need_reasons(self):
        det = kit.fake_det(["b.html"], menu=[{"id": "pair_structure", "kind": "internal"}, {"id": "a11y_static", "kind": "internal"}])
        sel = _sel(["surface_detect"], skip_reasons={"a11y_static": "fixture has no a11y surface"})
        skips = run_ui.core_skips(det, sel)
        self.assertEqual([(s["id"], bool(s["reason"])) for s in skips], [("pair_structure", False), ("a11y_static", True)])


class FixturePairTestCase(unittest.TestCase):
    def test_subject_vs_ref_is_red_with_ordered_fix_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            subject, det = _fixture_pair(tmp, base_pending="heading:settings:heading:overrides\n")
            out = os.path.join(tmp, "report")
            report = run_ui.run(subject, det, _sel(STATIC_TIER1, screens=["board", "settings"]), out, runner=kit.FakeRunner(), jobs=2)
            by = {c["id"]: c for c in report["checks"]}
            self.assertEqual((report["verdict"], report["exit_code"]), ("red", 1))
            self.assertEqual(by["pair_structure"]["status"], "fail")
            self.assertEqual(by["theme_default_declared"]["status"], "fail")
            self.assertEqual(by["contrast_pairs"]["status"], "fail")
            self.assertEqual(by["a11y_static"]["status"], "fail")
            self.assertEqual(by["ledger_lint"]["status"], "fail")
            self.assertEqual(by["pair_tokens"]["status"], "fail")           # --text-tertiary changed
            self.assertEqual(by["golden_manifest"]["status"], "na")
            rows = {r["id"]: r for r in report["items"]["rows"]}
            self.assertEqual(rows["control:board:button:批准"]["status"], "MISSING")
            self.assertEqual(rows["control:board:button:steer"]["detail"]["hidden_by"], "display:none")
            self.assertIn("spoofed_pin", rows["control:board:button:rework"]["fields_changed"])
            self.assertIn("topology:side", rows["landmark:board:navigation:rail"]["fields_changed"])
            self.assertEqual(rows["heading:settings:heading:overrides"]["ledger"], "pending")
            self.assertEqual(rows["control:board:link:settings"]["detail"]["suggestions"][0]["subject_id"], "control:board:link:setting")
            ranks = [f["rank"] for f in report["fix_first"]]
            self.assertEqual(ranks, sorted(ranks))
            self.assertEqual(sorted(set(ranks)), [1, 2, 3, 5, 6])
            first = report["fix_first"][0]
            self.assertEqual(first["rank"], 1)
            self.assertIn(first["item"], {"control:board:button:批准", "control:board:button:close", "control:board:button:steer",
                                          "control:board:link:settings", "landmark:board:banner:banner", "landmark:board:main:main",
                                          "landmark:board:navigation:rail", "control:settings:link:settings", "landmark:settings:navigation:rail"})
            self.assertTrue(any(f["kind"].startswith("CHANGED theme_default_declared") for f in report["fix_first"]))
            self.assertTrue(any(f["kind"] == "rule wcag.contrast.text" for f in report["fix_first"]))
            self.assertTrue(any(f["kind"] == "ledger pending_grew" for f in report["fix_first"]))
            md = open(os.path.join(out, "report.md"), encoding="utf-8").read()
            for section in ("## Sensors", "## Layers", "## Items", "## Rules (hits)", "## Layers not run as specified",
                            "## Core checks skipped", "## Structural blind spots", "## Fix first", "## Ledger note", "## Rerun"):
                self.assertIn(section, md)
            self.assertIn("**Verdict: RED**", md)
            self.assertNotIn("## Opinion", md)
            self.assertTrue(os.path.exists(os.path.join(out, "inventory", "subject-source.json")))
            self.assertTrue(os.path.exists(os.path.join(out, "inventory", "reference.json")))
            self.assertTrue(os.path.exists(os.path.join(out, "selection.json")))
            self.assertFalse(os.path.exists(os.path.join(subject, ".test-ui")))  # out was explicit; nothing else written

    def test_ref_vs_ref_is_incomplete_not_green(self):
        """同一棵树对自己：没有 MISSING，但 runtime 缺席的层是 UNAVAILABLE → INCOMPLETE（永不假绿）。"""
        with tempfile.TemporaryDirectory() as tmp:
            ref = os.path.join(tmp, "ref")
            kit.copy_fixture("ref", ref)
            kit.make_repo(ref, {"ui/parity/config.json": json.dumps(CONFIG)})
            det = detect_ui.detect(ref, against="dir:%s" % ref, runner=kit.FakeRunner(default=(1, "", "")), which=lambda n: None)
            report = run_ui.run(ref, det, _sel(STATIC_TIER1 + ["geometry_runtime", "structure_runtime"]), os.path.join(tmp, "r"), runner=kit.FakeRunner())
            by = {c["id"]: c for c in report["checks"]}
            self.assertEqual(by["pair_structure"]["status"], "pass")
            self.assertEqual(by["pair_structure"]["details"]["counts"], {"PRESENT": by["pair_structure"]["details"]["counts"]["PRESENT"]})
            self.assertEqual(by["structure_runtime"]["status"], "unavailable")
            self.assertEqual(by["geometry_runtime"]["status"], "substituted")
            self.assertEqual((report["verdict"], report["exit_code"]), ("incomplete", 3))
            self.assertEqual([i["id"] for i in report["not_run"]["substituted"]], ["geometry_runtime"])
            self.assertIn("structure_runtime", [i["id"] for i in report["not_run"]["unavailable"]])
            self.assertEqual(report["fix_first"], [])

    def test_ledgers_turn_the_fixture_green_on_structure(self):
        """给足账本（pending 记 MISSING、waiver 带理由、alias 记改名）→ pair_structure 不再红。"""
        with tempfile.TemporaryDirectory() as tmp:
            subject, det = _fixture_pair(tmp)
            pending = "\n".join(["control:board:button:批准", "control:board:button:close", "control:board:button:steer",
                                 "heading:settings:heading:materials-box", "heading:settings:heading:overrides",
                                 "control:settings:label:create-github-repo", "control:settings:switch:create-github-repo", ""])
            waivers = "\n".join(["landmark:board:navigation:rail  rail moved to header on purpose (#1)",
                                 "landmark:settings:navigation:rail  rail moved to header on purpose (#1)",
                                 "landmark:board:banner:banner  order shift from the rail move (#1)",
                                 "landmark:board:main:main  order shift from the rail move (#1)",
                                 "control:board:button:rework  span keeps the parity id until #2 lands", ""])
            aliases = "control:board:link:settings  control:board:link:setting  renamed in #3\ncontrol:settings:link:settings  control:settings:link:setting  renamed in #3\n"
            kit.make_repo(subject, {"ui/parity/pending.txt": pending, "ui/parity/waivers.txt": waivers, "ui/parity/aliases.txt": aliases})
            det["ledgers"] = detect_ui.detect_ledgers(kit.FakeRunner(), subject, None)
            report = run_ui.run(subject, det, _sel(["pair_structure", "ledger_lint"]), os.path.join(tmp, "r"), runner=kit.FakeRunner())
            by = {c["id"]: c for c in report["checks"]}
            self.assertEqual(by["pair_structure"]["status"], "pass", by["pair_structure"]["summary"])
            self.assertEqual(by["ledger_lint"]["status"], "pass")
            self.assertEqual(by["pair_structure"]["details"]["counts"].get("WAIVED"), 5)
            self.assertEqual(by["pair_structure"]["details"]["pending"], 7)


class DisagreementTestCase(unittest.TestCase):
    def test_parity_disagreement_row(self):
        ctx = checks.make_ctx("/r", kit.fake_det(["b.html"]))
        self.assertIsNone(run_ui.parity_disagreement(ctx))
        ctx["state"]["pair_source"] = {"rows": [{"id": "a", "status": "MISSING"}, {"id": "b", "status": "PRESENT"}, {"id": "c", "status": "CHANGED"}]}
        ctx["state"]["project_parity"] = {"items": {"a": "PRESENT", "b": "PENDING", "c": "MISSING", "d": "WAIVED"}}
        row = run_ui.parity_disagreement(ctx)
        self.assertEqual(row["status"], "fail")
        self.assertEqual([c["id"] for c in row["details"]["conflicts"]], ["a", "b"])
        ctx["state"]["project_parity"] = {"items": {"a": "MISSING", "b": "PRESENT"}}
        self.assertEqual(run_ui.parity_disagreement(ctx)["status"], "pass")


class CliTestCase(unittest.TestCase):
    def test_exit_codes(self):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(run_ui.main(["--repo", "/nonexistent/x", "--tier", "1"]), 2)
            with tempfile.TemporaryDirectory() as tmp:
                subject, det = _fixture_pair(tmp)
                det_path = os.path.join(tmp, "det.json")
                kit.lc.write_json(det_path, det)
                self.assertEqual(run_ui.main(["--repo", subject, "--detect", det_path]), 2)  # no tier
                self.assertEqual(run_ui.main(["--repo", subject, "--detect", det_path, "--tier", "1", "--dry-run"]), 0)
                out = os.path.join(tmp, "o")
                self.assertEqual(run_ui.main(["--repo", subject, "--detect", det_path, "--tier", "1", "--chosen-by", "user", "--out", out,
                                              "--propose-pending"]), 1)
                self.assertTrue(os.path.exists(os.path.join(out, "proposed", "pending.txt")))
                self.assertTrue(os.path.exists(os.path.join(out, "report.json")))


if __name__ == "__main__":
    unittest.main()
