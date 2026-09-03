"""act/lib/fresh_install + `python3 -m act.doctor --fresh-install` (CONTRACT §69).

The owner's acceptance criterion made machine-readable: on a fresh machine the
summary must exit 0 when everything left is a human's job (TCC grants,
credentials, tool installs) and non-zero only for rows that are actually
broken. Pinned here:

  - bucket order: OK → wired; under --no-launchd the scheduler rows (agent /
    cron / dashboard / heartbeat / board server families) → unwired, never
    broken; HUMAN catalog (failure id or row name) → human; other FAIL →
    broken; other WARN → notes;
  - `report_says_no_launchd` keys on the §23 `launchd=skipped` step carrying
    the literal `--no-launchd` marker install.sh writes;
  - the manual steps carry THIS machine's paths (config/runtime.json python,
    the §23 claude_bin, the installed Board bundle) and appear only when the
    matching row is not OK (claude / key) or the run was --no-launchd;
  - exit code = len(broken), capped at 99; render names every bucket and ends
    with the exit line; render_json round-trips;
  - doctor main: `--fresh-install` implies --fast, prints the summary, returns
    the broken count; `--json` gives the dict.
"""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from tests import TMP_HOME  # noqa: F401 - sandbox env first
from tests import test_doctor

from act import doctor
from act.lib import fresh_install as fi

_WIN = sys.platform.startswith("win")


def row(name, status, failure_id="", detail="d", fix="f"):
    return {"name": name, "status": status, "detail": detail, "fix": fix,
            "failure_id": failure_id, "action_id": ""}


def report(launchd_status="ok", launchd_detail="3 agents loaded", extra=()):
    steps = [{"name": "config", "status": "ok", "detail": "created from config.example.yaml"},
             {"name": "launchd", "status": launchd_status, "detail": launchd_detail}]
    steps.extend(extra)
    return {"mode": "non-interactive", "steps": steps}


NO_LAUNCHD_REPORT = report("skipped", "--no-launchd: no agent loaded; one actd pass run instead")


class BucketTestCase(unittest.TestCase):
    def test_ok_rows_are_wired_whatever_else_they_carry(self):
        self.assertEqual(fi.bucket_for(row("actd", "ok", "agent_unloaded"), True), fi.WIRED)
        self.assertEqual(fi.bucket_for(row("anything", "OK"), False), fi.WIRED)

    def test_human_catalog_by_failure_id_and_by_name(self):
        self.assertEqual(fi.bucket_for(row("claude CLI", "fail", "claude_cli_missing"), False), fi.HUMAN)
        self.assertEqual(fi.bucket_for(row("launchd volume access", "fail", "deploy_blind_tcc"), False), fi.HUMAN)
        self.assertEqual(fi.bucket_for(row("anthropic key", "warn"), False), fi.HUMAN)
        self.assertEqual(fi.bucket_for(row("obsidian vault", "warn"), False), fi.HUMAN)

    def test_scheduler_rows_are_unwired_only_under_no_launchd(self):
        agent = row("actd", "fail", "agent_unloaded")
        self.assertEqual(fi.bucket_for(agent, True), fi.UNWIRED)
        self.assertEqual(fi.bucket_for(agent, False), fi.BROKEN)
        cron = row("cron ingest chain", "fail", "cron_missing")
        self.assertEqual(fi.bucket_for(cron, True), fi.UNWIRED)
        self.assertEqual(fi.bucket_for(cron, False), fi.BROKEN)
        # name-matched WARN rows without an id: unwired under --no-launchd, human otherwise
        dc = row("daemon claude", "warn")
        self.assertEqual(fi.bucket_for(dc, True), fi.UNWIRED)
        self.assertEqual(fi.bucket_for(dc, False), fi.HUMAN)
        stale = row("dashboard", "fail", "dashboard_stale")
        self.assertEqual(fi.bucket_for(stale, True), fi.UNWIRED)

    def test_unknown_fail_is_broken_and_unknown_warn_is_a_note(self):
        self.assertEqual(fi.bucket_for(row("store2", "fail", "store2_refused"), True), fi.BROKEN)
        self.assertEqual(fi.bucket_for(row("board app version", "warn"), True), fi.NOTES)

    def test_bucket_rows_partitions_everything_exactly_once(self):
        rows = [row("a", "ok"), row("claude CLI", "fail", "claude_cli_missing"),
                row("actd", "fail", "agent_unloaded"), row("store2", "fail"), row("x", "warn")]
        buckets = fi.bucket_rows(rows, True)
        self.assertEqual(sum(len(v) for v in buckets.values()), len(rows))
        self.assertEqual([r["name"] for r in buckets[fi.BROKEN]], ["store2"])
        self.assertEqual([r["name"] for r in buckets[fi.UNWIRED]], ["actd"])


class ReportTestCase(unittest.TestCase):
    def test_no_launchd_marker(self):
        self.assertTrue(fi.report_says_no_launchd(NO_LAUNCHD_REPORT))
        self.assertFalse(fi.report_says_no_launchd(report("ok")))
        self.assertFalse(fi.report_says_no_launchd(report("skipped", "no agents to load")))
        self.assertFalse(fi.report_says_no_launchd(None))
        self.assertFalse(fi.report_says_no_launchd({"steps": "not-a-list"}))

    def test_read_report_tolerates_missing_and_torn_files(self):
        tmp = Path(tempfile.mkdtemp(prefix="fi-report-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        self.assertIsNone(fi.read_report(tmp / "nope.json"))
        (tmp / "torn.json").write_text("{torn", encoding="utf-8")
        self.assertIsNone(fi.read_report(tmp / "torn.json"))
        (tmp / "list.json").write_text("[1]", encoding="utf-8")
        self.assertIsNone(fi.read_report(tmp / "list.json"))
        (tmp / "ok.json").write_text(json.dumps(NO_LAUNCHD_REPORT), encoding="utf-8")
        self.assertEqual(fi.read_report(tmp / "ok.json"), NO_LAUNCHD_REPORT)

    def test_runtime_python_reads_the_pin(self):
        home = Path(tempfile.mkdtemp(prefix="fi-home-"))
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        self.assertIsNone(fi.runtime_python(home))
        (home / "config").mkdir()
        (home / "config" / "runtime.json").write_text('{"python": "/usr/bin/python3"}', encoding="utf-8")
        self.assertEqual(fi.runtime_python(home), "/usr/bin/python3")
        (home / "config" / "runtime.json").write_text("{bad", encoding="utf-8")
        self.assertIsNone(fi.runtime_python(home))


class ManualStepsTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fi-steps-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.home_dir = self.tmp / "home"          # stands in for $HOME
        self.home = self.home_dir / "Projects" / "zai"   # the checkout
        (self.home / "config").mkdir(parents=True)
        (self.home / "config" / "runtime.json").write_text('{"python": "/pin/python3"}', encoding="utf-8")

    def _ids(self, steps):
        return [s["id"] for s in steps]

    def _steps(self, rows, rep, home=None, port=1):
        # hermetic: the machine-wide /Applications is replaced by an empty folder
        return fi.manual_steps(rows, rep, home or self.home, self.home_dir, which=lambda _n: None,
                               port=port, system_apps=self.tmp / "no-system-apps")

    def test_fresh_machine_lists_board_claude_key_and_the_three_fda_paths(self):
        rows = [row("claude CLI", "fail", "claude_cli_missing"), row("anthropic key", "warn")]
        rep = report(extra=[{"name": "claude_bin", "status": "missing", "detail": None}])
        steps = self._steps(rows, rep)
        self.assertEqual(self._ids(steps),
                         ["build_board", "install_claude", "api_key", "fda_python", "fda_claude", "fda_cron"])
        by = {s["id"]: s for s in steps}
        self.assertEqual(by["fda_python"]["command"], "/pin/python3")
        self.assertEqual(by["fda_claude"]["command"], fi.DEFAULT_CLAUDE)
        self.assertEqual(by["fda_cron"]["command"], "/usr/sbin/cron")
        self.assertIn(str(self.home / "config/secrets/anthropic-api-key.txt"), by["api_key"]["command"])
        self.assertIn("harmless otherwise", by["fda_python"]["why"])  # repo is inside $HOME

    def test_done_items_drop_out_and_paths_follow_the_report(self):
        rows = [row("claude CLI", "ok"), row("anthropic key", "ok")]
        rep = report(extra=[{"name": "claude_bin", "status": "ok", "detail": "/Users/x/.local/bin/claude"}])
        steps = self._steps(rows, rep)
        self.assertNotIn("install_claude", self._ids(steps))
        self.assertNotIn("api_key", self._ids(steps))
        self.assertEqual({s["id"]: s for s in steps}["fda_claude"]["command"], "/Users/x/.local/bin/claude")

    def test_board_step_prefers_the_installed_bundle_then_the_browser(self):
        (self.home / "web" / "dist").mkdir(parents=True)
        (self.home / "web" / "dist" / "index.html").write_text("<html>", encoding="utf-8")
        step = self._steps([], None, port=4711)[0]
        self.assertEqual(step["id"], "open_board")
        self.assertIn("http://127.0.0.1:4711/", step["command"])
        (self.home_dir / "Applications" / fi.BOARD_BUNDLE).mkdir(parents=True)
        step = self._steps([], None, port=4711)[0]
        self.assertEqual(step["command"], 'open "%s"' % (self.home_dir / "Applications" / fi.BOARD_BUNDLE))

    def test_no_launchd_run_adds_the_wire_step_last(self):
        steps = self._steps([], NO_LAUNCHD_REPORT)
        self.assertEqual(steps[-1]["id"], "wire_scheduler")
        self.assertEqual(steps[-1]["command"], "bash %s" % (self.home / "install.sh"))

    def test_repo_outside_home_marks_fda_required(self):
        other = self.tmp / "Volumes" / "External" / "zai"
        (other / "config").mkdir(parents=True)
        self.assertTrue(fi.repo_outside_home(other, self.home_dir))
        self.assertFalse(fi.repo_outside_home(self.home, self.home_dir))
        steps = self._steps([], None, home=other)
        self.assertIn("REQUIRED", {s["id"]: s for s in steps}["fda_python"]["why"])
        self.assertIn("run install.sh first", {s["id"]: s for s in steps}["fda_python"]["command"])

    def test_daemon_claude_path_order(self):
        self.assertEqual(fi.daemon_claude_path(
            report(extra=[{"name": "claude_bin", "status": "ok", "detail": "/a/claude"}]),
            which=lambda _n: "/b/claude"), "/a/claude")
        self.assertEqual(fi.daemon_claude_path(None, which=lambda _n: "/b/claude"), "/b/claude")
        self.assertEqual(fi.daemon_claude_path(None, which=lambda _n: None), fi.DEFAULT_CLAUDE)

    def test_stable_daemon_copy_wins_as_the_fda_subject(self):
        # §55 第五幕: the grant goes on install.sh's stable copy, never on the
        # versioned path that moves with every Claude Code update
        rep = report(extra=[{"name": "stable_claude", "status": "ok",
                             "detail": "unchanged: /x/bin/claude (2.1.259)"},
                            {"name": "claude_bin", "status": "ok", "detail": "/a/claude"}])
        self.assertEqual(fi.daemon_claude_path(rep, which=lambda _n: None),
                         str(fi.config.stable_claude_bin()))
        steps = fi.manual_steps([], rep, self.home, self.home_dir, which=lambda _n: None,
                                port=1, system_apps=self.tmp / "no-system-apps")
        fda = {s["id"]: s for s in steps}["fda_claude"]
        self.assertEqual(fda["command"], str(fi.config.stable_claude_bin()))
        self.assertNotIn("re-grant", fda["title"])
        self.assertIn("survives", fda["why"] + " " + fda["title"]) if "survives" in fda["why"] else None


class SummaryRenderTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fi-sum-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.home = self.tmp / "home" / "Projects" / "zai"
        self.home.mkdir(parents=True)

    def _summary(self, rows, rep):
        return fi.summarize(rows, rep, self.home, self.tmp / "home", which=lambda _n: None, port=1,
                            system_apps=self.tmp / "no-system-apps")

    def test_exit_code_counts_only_broken_rows(self):
        rows = [row("claude CLI", "fail", "claude_cli_missing"), row("actd", "fail", "agent_unloaded"),
                row("anthropic key", "warn"), row("store2", "fail", "store2_refused")]
        s = self._summary(rows, NO_LAUNCHD_REPORT)
        self.assertEqual(s["exit_code"], 1)
        self.assertTrue(s["no_launchd"])
        self.assertEqual([r["name"] for r in s["buckets"][fi.BROKEN]], ["store2"])
        # without the --no-launchd marker the unloaded agent IS broken
        s2 = self._summary(rows, report("ok"))
        self.assertEqual(s2["exit_code"], 2)
        self.assertFalse(s2["no_launchd"])

    def test_exit_code_is_capped(self):
        rows = [row("x%d" % i, "fail") for i in range(150)]
        self.assertEqual(self._summary(rows, None)["exit_code"], 99)

    def test_render_names_every_bucket_and_ends_with_the_verdict(self):
        rows = [row("config.yaml", "ok"), row("claude CLI", "fail", "claude_cli_missing"),
                row("actd", "fail", "agent_unloaded"), row("board app version", "warn"), row("store2", "fail")]
        text = fi.render(self._summary(rows, NO_LAUNCHD_REPORT))
        self.assertIn("wired by the installer (1):\n  config.yaml", text)
        self.assertIn("BROKEN", text)
        self.assertIn("[FAIL] store2", text)
        self.assertIn("waiting on you", text)
        self.assertIn("[FAIL] claude CLI", text)
        self.assertIn("not wired in this run (--no-launchd)", text)
        self.assertIn("notes (1)", text)
        self.assertIn("what is left for you, in order:", text)
        self.assertIn("1. Build the board UI", text)
        self.assertTrue(text.rstrip().endswith("exit 1 — 1 broken row(s) above"), text[-80:])

    def test_render_clean_machine(self):
        text = fi.render(self._summary([row("a", "ok")], report("ok")))
        self.assertIn("exit 0 — nothing broken: the rest is yours", text)
        self.assertNotIn("BROKEN", text)
        self.assertNotIn("--no-launchd", text)

    def test_render_json_round_trips(self):
        s = self._summary([row("a", "ok"), row("b", "fail")], None)
        back = json.loads(fi.render_json(s))
        self.assertEqual(back["exit_code"], 1)
        self.assertEqual(sorted(back["buckets"]), sorted(fi.BUCKETS))
        self.assertEqual(back["home"], str(self.home))


@unittest.skipIf(_WIN, "reuses the macOS doctor fixture (launchd/cron probes)")
class DoctorFlagTestCase(unittest.TestCase):
    """`python3 -m act.doctor --fresh-install` over the healthy doctor fixture."""

    def setUp(self):
        self.fx = test_doctor.DoctorTestCase("test_healthy_setup_has_no_fails_and_exits_zero")
        self.fx.setUp()
        self.addCleanup(self.fx.tearDown)
        self.report_path = fi.install_report.REPORT_PATH
        self._had_report = self.report_path.exists()
        self._old_report = self.report_path.read_text(encoding="utf-8") if self._had_report else None
        self.addCleanup(self._restore_report)

    def _restore_report(self):
        if self._had_report:
            self.report_path.write_text(self._old_report, encoding="utf-8")
        elif self.report_path.exists():
            os.unlink(self.report_path)

    def _main(self, argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = doctor.main(argv, probes=self.fx.make_probes())
        return code, buf.getvalue()

    def test_healthy_fixture_exits_zero_and_prints_the_summary(self):
        self.report_path.parent.mkdir(parents=True, exist_ok=True)
        self.report_path.write_text(json.dumps(report("ok")), encoding="utf-8")
        code, out = self._main(["--fresh-install"])
        self.assertEqual(code, 0, out)
        self.assertTrue(out.startswith("fresh install — "), out[:60])
        self.assertIn("wired by the installer", out)
        self.assertIn("what is left for you, in order:", out)
        # --fresh-install implies --fast: the live auth probe never runs
        self.assertNotIn("claude auth", out)

    def test_json_flag_returns_the_buckets(self):
        self.report_path.parent.mkdir(parents=True, exist_ok=True)
        self.report_path.write_text(json.dumps(NO_LAUNCHD_REPORT), encoding="utf-8")
        code, out = self._main(["--fresh-install", "--json"])
        doc = json.loads(out)
        self.assertEqual(code, doc["exit_code"])
        self.assertTrue(doc["no_launchd"])
        self.assertIn("manual_steps", doc)
        self.assertEqual(doc["manual_steps"][-1]["id"], "wire_scheduler")

    def test_missing_claude_is_the_humans_not_a_broken_row(self):
        self.report_path.parent.mkdir(parents=True, exist_ok=True)
        self.report_path.write_text(json.dumps(report("ok")), encoding="utf-8")
        probes = self.fx.make_probes(which_map={"npx": "/fake/bin/npx", "gh": "/fake/bin/gh"})
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = doctor.main(["--fresh-install", "--json"], probes=probes)
        doc = json.loads(buf.getvalue())
        human = [r["name"] for r in doc["buckets"][fi.HUMAN]]
        self.assertIn("claude CLI", human)
        self.assertEqual([r["name"] for r in doc["buckets"][fi.BROKEN]], [])
        self.assertEqual(code, 0)
        self.assertIn("install_claude", [s["id"] for s in doc["manual_steps"]])


if __name__ == "__main__":
    unittest.main()
