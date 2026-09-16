"""全覆盖跑者的 DSL 解析 + 三态报告 R（CONTRACT §58 QA 闸门；§77.2 跑者与三态报告）。

判例（全部用假执行器：不起子进程、不联网——防腐 #7 的 unit 层纪律）：
  - proof DSL：` && ` 串联、未知 kind、空 proof、waived 行；
  - 每种重工具的输出 → per-key map（unittest -v / vitest / playwright / run.sh）；
  - R 的行形与末三行 `PRESENT=` `MISSING=` `WAIVED=`（goal 就 grep 这三行）；
  - 退出码：MISSING=0 → 0、有 MISSING → 1、清单缺席 → 2；
  - `--only` 前缀过滤与 `--skip-ax`（axprobe 记 MISSING reason=skipped）。
"""
import importlib.util
import json
import os
import tempfile
import unittest
import urllib.parse
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "coverage_run_under_test", REPO / "scripts" / "qa" / "coverage_run.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cr = _load()


class FakeShell:
    """记账式假 shell：按 cmd 里的关键字挑一个 (rc, out)。"""

    def __init__(self, table=None, default=(0, "")):
        self.table = table or {}
        self.default = default
        self.calls = []

    def run(self, cmd, cwd=None, env=None, timeout=None, log_name=None):
        self.calls.append({"cmd": list(cmd), "cwd": cwd, "env": dict(env or {}),
                           "timeout": timeout, "log_name": log_name})
        joined = " ".join(cmd)
        for needle, reply in self.table.items():
            if needle in joined:
                return cr.Proc(*reply)
        return cr.Proc(*self.default)


class FakeHttp:
    def __init__(self, routes=None, default=(404, "{}")):
        self.routes = routes or {}
        self.default = default
        self.calls = []

    def request(self, method, url, body=None, headers=None, timeout=None):
        self.calls.append({"method": method, "url": url, "body": body,
                           "headers": dict(headers or {})})
        split = urllib.parse.urlsplit(url)
        path = split.path + (("?" + split.query) if split.query else "")
        for key in ("%s %s" % (method, path), "%s %s" % (method, split.path)):
            if key in self.routes:
                return cr.Resp(*self.routes[key])
        return cr.Resp(*self.default)


def fake_server(home="/tmp/zaa-cov-fake", base="http://127.0.0.1:1"):
    return cr.DemoServer(base, home, "fake-token", proc=None)


class Opts:
    """argparse 结果的替身（Tools/Judge 只读这几个键）。"""

    def __init__(self, **kw):
        self.logdir = None
        self.skip_ax = False
        self.unittest_scope = "listed"
        self.unittest_listed_max = 40
        self.__dict__.update(kw)


def build_judge(shell=None, http=None, routes=None, table=None, opts=None, server=None):
    shell = shell or FakeShell(table)
    http = http or FakeHttp(routes)
    opts = opts or Opts()
    homes = cr.TempHomes()
    tools = cr.Tools(shell, http, opts, homes,
                     server_factory=lambda: server if server is not None else fake_server(),
                     python_bin="/fake/python3", pythonpath="/fake/repo")
    return cr.Judge(tools, shell, http, homes, opts), shell, http


class ProofDslTestCase(unittest.TestCase):
    def test_clauses_split_on_double_ampersand(self):
        self.assertEqual(cr.parse_proof("http:GET /api/board expect=200 && unittest:tests.test_a"),
                         [("http", "GET /api/board expect=200"), ("unittest", "tests.test_a")])

    def test_empty_proof_has_no_clauses(self):
        self.assertEqual(cr.parse_proof(""), [])
        self.assertEqual(cr.parse_proof(None), [])

    def test_http_proof_tokens(self):
        spec = cr.parse_http_proof("POST /api/cards/{id}/x body=@f.json noauth expect=409 contains=ok")
        self.assertEqual(spec["method"], "POST")
        self.assertEqual(spec["path"], "/api/cards/{id}/x")
        self.assertEqual(spec["expect"], 409)
        self.assertTrue(spec["noauth"])
        self.assertEqual(spec["contains"], "ok")
        self.assertEqual(spec["body"], {"file": "f.json"})

    def test_unknown_kind_is_missing_not_a_crash(self):
        judge, _shell, _http = build_judge()
        verdict = judge.clause("telepathy", "whatever")
        self.assertEqual(verdict.state, cr.MISSING)
        self.assertIn("unknown proof kind", verdict.reason)

    def test_row_without_proof_is_missing_with_the_inventory_reason(self):
        judge, _shell, _http = build_judge()
        verdict = judge.row({"id": "contract:§1", "proof": "", "status": "todo"})
        self.assertEqual(verdict.state, cr.MISSING)
        self.assertEqual(verdict.reason, "no proof declared in inventory")

    def test_waived_row_copies_the_waive_reason_and_runs_nothing(self):
        judge, shell, http = build_judge()
        verdict = judge.row({"id": "contract:§45", "proof": "unittest:tests.test_x",
                             "status": "waived", "waive_reason": "tombstone"})
        self.assertEqual((verdict.state, verdict.reason, verdict.evidence),
                         (cr.WAIVED, "tombstone", "-"))
        self.assertEqual(shell.calls, [])
        self.assertEqual(http.calls, [])

    def test_and_chain_needs_every_clause(self):
        judge, _shell, _http = build_judge(
            table={"unittest": (0, "test_a (tests.test_a.C.test_a) ... ok")},
            routes={"GET /api/board": (500, "boom")})
        judge.collect([{"id": "x", "proof": "http:GET /api/board expect=200 && "
                                            "unittest:tests.test_a", "status": "todo"}])
        verdict = judge.row({"id": "x", "proof": "http:GET /api/board expect=200 && "
                                                 "unittest:tests.test_a", "status": "todo"})
        self.assertEqual(verdict.state, cr.MISSING)
        self.assertIn("expected 200, got 500", verdict.reason)


class HeavyToolParsersTestCase(unittest.TestCase):
    def test_unittest_verbose_maps_per_module(self):
        text = ("test_a (tests.test_one.Case.test_a) ... ok\n"
                "test_b (tests.test_one.Case.test_b) ... FAIL\n"
                "test_c (tests.test_two.Case) ... ok\n")
        mapping = cr.parse_unittest_verbose(text)
        self.assertFalse(mapping["tests.test_one"].ok)
        self.assertTrue(mapping["tests.test_two"].ok)

    def test_module_of_handles_both_unittest_formats(self):
        self.assertEqual(cr.module_of("tests.test_one.Case.test_a"), "tests.test_one")
        self.assertEqual(cr.module_of("tests.test_one.Case"), "tests.test_one")

    def test_vitest_parity_report_maps_ids(self):
        doc = {"testResults": [{"assertionResults": [
            {"title": "control:about:label:about", "status": "passed"},
            {"title": "control:about:label:repo", "status": "failed"}]}]}
        mapping = cr.parse_vitest_parity(json.dumps(doc))
        self.assertTrue(mapping["control:about:label:about"].ok)
        self.assertFalse(mapping["control:about:label:repo"].ok)

    def test_playwright_report_maps_file_and_title(self):
        doc = {"suites": [{"file": "e2e/coverage.spec.ts", "specs": [
            {"title": "rail pages walk", "tests": [{"results": [{"status": "passed"}]}]}]}]}
        mapping = cr.parse_playwright_json(json.dumps(doc))
        self.assertTrue(mapping[("coverage.spec.ts", "rail pages walk")].ok)

    def test_swift_run_blames_the_last_step_when_it_fails(self):
        text = "==> [6/9] Compile menu harness\n==> [7/9] Run menu harness\n"
        mapping = cr.parse_swift_run(text, 3, ["MenuHarness", "PolicyHarness"])
        self.assertFalse(mapping["MenuHarness"].ok)
        self.assertIn("rc=3", mapping["MenuHarness"].evidence)
        self.assertIn("not reached", mapping["PolicyHarness"].evidence)

    def test_each_heavy_tool_runs_at_most_once(self):
        judge, shell, _http = build_judge(
            table={"unittest": (0, "test_a (tests.test_a.C.test_a) ... ok\n"
                                   "test_b (tests.test_b.C.test_b) ... ok")})
        rows = [{"id": "a", "proof": "unittest:tests.test_a", "status": "todo"},
                {"id": "b", "proof": "unittest:tests.test_b", "status": "todo"}]
        judge.collect(rows)
        self.assertEqual([judge.row(r).state for r in rows], [cr.PRESENT, cr.PRESENT])
        self.assertEqual(len([c for c in shell.calls if "unittest" in " ".join(c["cmd"])]), 1)

    def test_listed_scope_runs_only_the_modules_the_inventory_names(self):
        judge, shell, _http = build_judge(
            table={"unittest": (0, "test_a (tests.test_a.C.test_a) ... ok")})
        rows = [{"id": "a", "proof": "unittest:tests.test_a", "status": "todo"}]
        judge.collect(rows)
        judge.row(rows[0])
        cmd = [c["cmd"] for c in shell.calls if "unittest" in " ".join(c["cmd"])][0]
        self.assertEqual(cmd[-1], "tests.test_a")
        self.assertNotIn("discover", cmd)


class ProbeAndFixtureTestCase(unittest.TestCase):
    def test_absent_probe_script_says_probe_script_absent(self):
        judge, _shell, _http = build_judge()
        verdict = judge.axprobe("dock_badge")
        if os.path.exists(REPO / "scripts" / "qa" / "shell_ui_probe.py"):
            self.skipTest("shell_ui_probe.py has landed — the absent-script branch needs a stub tree")
        self.assertEqual((verdict.state, verdict.reason), (cr.MISSING, "probe script absent"))

    def test_skip_ax_marks_probes_missing_with_reason_skipped(self):
        judge, shell, _http = build_judge(opts=Opts(skip_ax=True))
        verdict = judge.axprobe("hotkey_focus")
        self.assertEqual((verdict.state, verdict.reason), (cr.MISSING, "skipped"))
        self.assertEqual(shell.calls, [])

    def test_absent_fixture_script_names_the_expected_path(self):
        judge, _shell, _http = build_judge()
        verdict = judge.fixture("B-99-not-written-yet")
        self.assertEqual(verdict.state, cr.MISSING)
        self.assertIn("scripts/qa/fixtures_b/B-99-not-written-yet.py", verdict.reason)

    def test_probe_json_present_true_is_the_verdict(self):
        self.assertEqual(cr._probe_json('{"present": true, "evidence": "badge=3"}'),
                         (True, "badge=3"))
        self.assertEqual(cr._probe_json("no json here"), (False, ""))


class ReportTestCase(unittest.TestCase):
    def test_report_lines_and_final_three_counters(self):
        pairs = [({"id": "a"}, cr.Verdict(cr.PRESENT, "-", "GET /api/board -> 200")),
                 ({"id": "b"}, cr.Verdict(cr.MISSING, "boom", "rc=1")),
                 ({"id": "B-03-x"}, cr.Verdict(cr.PRESENT, "-", "fixture ok")),
                 ({"id": "c"}, cr.Verdict(cr.WAIVED, "covered-by-tests/test_x.py", "-"))]
        text, counts = cr.render_report(pairs)
        lines = text.splitlines()
        self.assertEqual(lines[0], "a PRESENT evidence=GET /api/board -> 200")
        self.assertEqual(lines[1], "b MISSING reason=boom evidence=rc=1")
        self.assertEqual(lines[2], "B-03-x PRESENT evidence=fixture ok")
        self.assertEqual(lines[3], "c WAIVED reason=covered-by-tests/test_x.py evidence=-")
        self.assertEqual(lines[-3:], ["PRESENT=2", "MISSING=1", "WAIVED=1"])
        self.assertEqual(counts[cr.PRESENT], 2)

    def test_evidence_is_one_line_and_capped(self):
        self.assertEqual(cr.one_line("a\nb   c"), "a b c")
        self.assertEqual(len(cr.one_line("x" * 500)), cr.EVIDENCE_MAX)
        self.assertEqual(cr.one_line(""), "-")

    def test_rows_are_written_in_id_order(self):
        path = Path(tempfile.mkdtemp(prefix="zaa-cov-inv-")) / "inv.json"
        path.write_text(json.dumps({"scenarios": [{"id": "z"}, {"id": "a"}, {"id": "m"}]}),
                        encoding="utf-8")
        self.assertEqual([r["id"] for r in cr.load_inventory(str(path))], ["a", "m", "z"])


class CliTestCase(unittest.TestCase):
    def _run(self, rows, argv_extra=(), table=None, routes=None):
        tmp = Path(tempfile.mkdtemp(prefix="zaa-cov-cli-"))
        inv = tmp / "inv.json"
        inv.write_text(json.dumps({"scenarios": rows}), encoding="utf-8")
        report = tmp / "report.md"
        shell = FakeShell(table)
        http = FakeHttp(routes)
        rc = cr.main(["--inventory", str(inv), "--report", str(report),
                      "--logdir", str(tmp / "logs")] + list(argv_extra),
                     shell=shell, http=http, homes=cr.TempHomes(),
                     server_factory=fake_server, python_bin="/fake/python3")
        return rc, report.read_text(encoding="utf-8"), shell

    def test_absent_inventory_exits_2(self):
        rc = cr.main(["--inventory", "/tmp/zaa-cov-nope/nope.json"],
                     shell=FakeShell(), http=FakeHttp(), homes=cr.TempHomes(),
                     server_factory=fake_server, python_bin="/fake/python3")
        self.assertEqual(rc, 2)

    def test_all_present_exits_0(self):
        rc, text, _shell = self._run(
            [{"id": "route:GET /api/board", "proof": "http:GET /api/board expect=200",
              "status": "todo"}], routes={"GET /api/board": (200, '{"lanes": []}')})
        self.assertEqual(rc, 0)
        self.assertIn("PRESENT=1", text)
        self.assertIn("MISSING=0", text)

    def test_any_missing_exits_1(self):
        rc, text, _shell = self._run([{"id": "x", "proof": "", "status": "todo"}])
        self.assertEqual(rc, 1)
        self.assertIn("MISSING=1", text)

    def test_only_prefix_filters_rows(self):
        rc, text, _shell = self._run(
            [{"id": "route:a", "proof": "", "status": "todo"},
             {"id": "shell:b", "proof": "", "status": "todo"}],
            argv_extra=["--only", "shell:"])
        self.assertEqual(rc, 1)
        self.assertIn("shell:b MISSING", text)
        self.assertNotIn("route:a", text)


if __name__ == "__main__":
    unittest.main()
