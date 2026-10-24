"""Doctor (CONTRACT §25) — failure classification and the cron probe.

Covers:
- act/lib/failures: catalog integrity, classify() precision (known raw error
  texts map to ids, unknown text honestly returns None), bilingual copy;
- v2.0.0 tombstones: ids only the Act pipeline produced stay in the catalog
  (add-only) marked ``retired``, nothing classifies into them; the two §78 ids
  are live;
- mirror: server/failure_catalog.py (``GET /api/failures``) projects every id —
  the web and the shell read the sentences from there (the mac/ Doctor.swift
  mirror retired with the menu-bar app);
- doctor: --json shape carries failure_id/action_id; the cron FDA probe check
  reads state/cron_probe.json honestly (missing / fresh-ok / fresh-blocked /
  stale).
(The dashboard projection, the AI-fix bundle and the card notification builders
retired with the Act pipeline — full text in tag ``full``.)

Everything runs inside the sandbox AIASSISTANT_HOME (tests/__init__.py).
"""
import contextlib
import io
import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests import TMP_HOME  # noqa: F401 - ensures the sandbox env is set first
from tests.test_launchd_render import install_sh_prelude

from act import doctor
from act.lib import config, failures

REPO_ROOT = Path(__file__).resolve().parent.parent
NOW = 1_700_000_000.0


def _iso(ts: float) -> str:
    import datetime as _dt
    return _dt.datetime.fromtimestamp(ts, _dt.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


class CatalogTestCase(unittest.TestCase):
    def test_every_entry_is_complete(self):
        for fid, entry in failures.FAILURES.items():
            for key in ("plain_zh", "plain_en", "action_id"):
                self.assertTrue(str(entry.get(key, "")).strip(),
                                "%s missing %s" % (fid, key))

    def test_plain_sentences_carry_a_consequence_or_next_step(self):
        # audit Theme 11: a bare problem statement is banned — every sentence
        # names what breaks or what to do (the em-dash clause). Tombstones (§25
        # v2.0.0) only say what they used to mean — nothing produces them.
        for fid, entry in failures.FAILURES.items():
            if fid in failures.RETIRED:
                continue
            self.assertIn("——", entry["plain_zh"], fid)
            self.assertIn("—", entry["plain_en"], fid)

    def test_describe_and_helpers_tolerate_unknown_ids(self):
        self.assertIsNone(failures.describe("no_such_id"))
        self.assertIsNone(failures.describe(None))
        self.assertIsNone(failures.user_message("no_such_id"))
        self.assertIsNone(failures.action_id(None))

    def test_user_message_is_bilingual(self):
        self.assertEqual(failures.user_message("engine_dead", lang="zh"),
                         failures.FAILURES["engine_dead"]["plain_zh"])
        self.assertEqual(failures.user_message("engine_dead", lang="en"),
                         failures.FAILURES["engine_dead"]["plain_en"])


class ClassifyTestCase(unittest.TestCase):
    def test_claude_missing_variants(self):
        for raw in ("zsh:1: command not found: claude",
                    "/bin/sh: claude: command not found",
                    "[Errno 2] No such file or directory: 'claude'"):
            self.assertEqual(failures.classify(raw), "claude_cli_missing", raw)

    def test_auth_failures(self):
        for raw in ('{"type":"error","error":{"type":"authentication_error",'
                    '"message":"invalid x-api-key"}}',
                    "API Error: 401 Unauthorized",
                    "OAuth token has expired. Please run /login",
                    "api key invalid or revoked"):
            self.assertEqual(failures.classify(raw), "claude_auth_failed", raw)

    def test_node_missing(self):
        for raw in ("zsh:1: command not found: npx",
                    "env: node: No such file or directory"):
            self.assertEqual(failures.classify(raw), "node_missing", raw)

    def test_outdated_cli_signatures_no_longer_classify(self):
        # v2.0.0: the --bg / --name / --resume / agents signatures belonged to the
        # retired dispatch path (§25); the id itself lives on for the `daemon claude`
        # version-mismatch row, which attaches it directly
        for raw in ("error: unknown option '--bg'",
                    'Error: Unknown option "--name"',
                    "error: unknown option '--resume'",
                    "error: unknown command 'agents'"):
            self.assertIsNone(failures.classify(raw), raw)

    def test_generic_unknown_option_stays_unclassified(self):
        # precision contract: only the dispatch-critical flags/subcommands are
        # version signatures — a task's own "unknown option" must not classify.
        self.assertIsNone(failures.classify("error: unknown option '--verbose'"))
        self.assertIsNone(failures.classify("make: unknown command 'agentsmith'"))

    def test_network_errors(self):
        for raw in ("curl: (7) Connection refused",
                    "fetch failed: getaddrinfo ENOTFOUND api.anthropic.com",
                    "read tcp: connection timed out"):
            self.assertEqual(failures.classify(raw), "network_error", raw)

    def test_unknown_text_returns_none(self):
        self.assertIsNone(failures.classify("claude --bg exited 1 (no output)"))
        self.assertIsNone(failures.classify("some totally novel explosion"))
        self.assertIsNone(failures.classify(""))
        self.assertIsNone(failures.classify(None))

    def test_authorized_is_not_unauthorized(self):
        # substring trap: "authorized" contains "authorized" but not the
        # word "unauthorized" — must stay unclassified.
        self.assertIsNone(failures.classify("user is authorized; proceeding"))

    def test_npm_download_banner_variants(self):
        for raw in ("npm warn exec The following package was not found and "
                    "will be installed: screenpipe@0.3.349",
                    "Need to install the following packages:\n"
                    "screenpipe@0.3.349\nOk to proceed? (y)"):
            self.assertEqual(failures.classify(raw), "engine_npm_download", raw)

    def test_download_that_died_on_network_is_network_not_download(self):
        # rule order contract: network_error outranks engine_npm_download —
        # a download killed by the network must never read "in progress".
        raw = ("npm warn exec The following package was not found and will be "
               "installed: screenpipe@0.3.349\n"
               "npm error network ETIMEDOUT registry.npmjs.org")
        self.assertEqual(failures.classify(raw), "network_error")

    def test_ffmpeg_failures_never_classify_outside_the_engine_log(self):
        # engine-log-context-only contract: classify() runs on dispatch
        # errors and card/ask output, where "failed to install ffmpeg-python"
        # or prose about ffmpeg is the TASK's business — mapping it to the
        # recording-engine sentence (with an Install-ffmpeg action) would
        # mislabel an unrelated failure. classify_engine_log handles the
        # engine-log context (EngineLogClassifyTestCase).
        for raw in ("ERROR: failed to install ffmpeg-python==0.2.0",
                    "I tried brew but failed to install ffmpeg because the "
                    "sandbox blocks network",
                    "ERROR screenpipe_core::ffmpeg: failed to install ffmpeg: "
                    "No such file or directory (os error 2)",
                    "converted the clip with ffmpeg and uploaded it",
                    "ffmpeg not found. installing..."):
            self.assertIsNone(failures.classify(raw), raw)


class EngineLogClassifyTestCase(unittest.TestCase):
    """classify_engine_log — the engine-death diagnosis (audit 2.3).

    Swift mirror: RecordingController.diagnoseEngine (Recording.swift)."""

    DOWNLOAD = ("npm warn exec The following package was not found and will "
                "be installed: screenpipe@0.3.349")

    FFMPEG = ("2026-07-13T19:24:44 ERROR screenpipe_core::ffmpeg: failed to "
              "install ffmpeg: No such file or directory (os error 2)\n"
              "ffmpeg not found and installation failed. "
              "please install ffmpeg manually.")

    def test_missing_npx_wins_over_everything(self):
        self.assertEqual(
            failures.classify_engine_log(self.DOWNLOAD, npx_present=False,
                                         engine_alive=True),
            "node_missing")

    def test_alive_download_is_progress_not_error(self):
        fid = failures.classify_engine_log(self.DOWNLOAD, engine_alive=True)
        self.assertEqual(fid, "engine_npm_download")
        # the catalog copy must read as calm progress, not as a failure
        msg = failures.user_message(fid, lang="zh")
        self.assertIn("下载中", msg)
        for bad in ("失败", "错误", "崩溃"):
            self.assertNotIn(bad, msg)

    def test_alive_and_quiet_is_healthy(self):
        # locked screens legitimately go silent — never classify silence
        self.assertIsNone(failures.classify_engine_log(
            "2026-07-08T01:00:00 INFO capturing frame", engine_alive=True))
        self.assertIsNone(failures.classify_engine_log("", engine_alive=True))

    def test_dead_with_output_is_crashed(self):
        self.assertEqual(
            failures.classify_engine_log(
                "thread 'main' panicked at src/core.rs:42", engine_alive=False),
            "engine_crashed")

    def test_engine_is_presumed_dead_unless_told_otherwise(self):
        # the defaults are the fail-closed shape: npx present, engine NOT
        # alive — a caller that forgets engine_alive gets a diagnosis, never
        # a silent "healthy".
        self.assertEqual(
            failures.classify_engine_log("thread 'main' panicked at src/core.rs:42"),
            "engine_crashed")
        self.assertEqual(failures.classify_engine_log(self.DOWNLOAD), "engine_crashed")
        self.assertEqual(failures.classify_engine_log(""), "engine_dead")

    def test_dead_on_ffmpeg_is_the_specific_fix_not_generic_crash(self):
        # 2026-07-13: screen_audio dying on the missing ffmpeg must name the
        # dependency, not read as a generic crash (let alone "permissions").
        self.assertEqual(
            failures.classify_engine_log(self.FFMPEG, engine_alive=False),
            "engine_ffmpeg_missing")

    def test_alive_with_stale_ffmpeg_lines_is_healthy(self):
        # an engine that recovered (ffmpeg installed later) keeps the old
        # error lines in its tail — alive always wins over stale text.
        self.assertIsNone(
            failures.classify_engine_log(self.FFMPEG, engine_alive=True))

    def test_missing_npx_outranks_ffmpeg(self):
        self.assertEqual(
            failures.classify_engine_log(self.FFMPEG, npx_present=False,
                                         engine_alive=False),
            "node_missing")

    def test_dead_ffmpeg_with_auth_flavored_error_is_still_ffmpeg(self):
        # mirror contract with Swift's direct substring check: the install
        # error's own "401 Unauthorized" must not classify as an API-key
        # problem — the fix is installing ffmpeg either way.
        self.assertEqual(
            failures.classify_engine_log(
                "failed to install ffmpeg: HTTP 401 Unauthorized",
                engine_alive=False),
            "engine_ffmpeg_missing")

    def test_dead_ffmpeg_that_died_on_network_is_still_ffmpeg(self):
        self.assertEqual(
            failures.classify_engine_log(
                "failed to install ffmpeg: getaddrinfo ENOTFOUND "
                "www.osxexperts.net", engine_alive=False),
            "engine_ffmpeg_missing")

    def test_alive_redownload_with_stale_ffmpeg_lines_is_download(self):
        # banner + old ffmpeg failure lines while ALIVE = a re-download in
        # progress (mirror contract: Swift's `downloading` branch outranks
        # its ffmpeg substrings the same way).
        self.assertEqual(
            failures.classify_engine_log(self.DOWNLOAD + "\n" + self.FFMPEG,
                                         engine_alive=True),
            "engine_npm_download")

    def test_ffmpeg_installing_first_line_alone_is_not_the_failure(self):
        # "installing..." is progress; only the failure phrasing classifies
        # (a process killed mid-install reads as a plain crash).
        self.assertEqual(
            failures.classify_engine_log("ffmpeg not found. installing...",
                                         engine_alive=False),
            "engine_crashed")

    def test_dead_download_banner_is_crashed_not_in_progress(self):
        self.assertEqual(
            failures.classify_engine_log(self.DOWNLOAD, engine_alive=False),
            "engine_crashed")

    def test_dead_and_silent_is_plain_engine_dead(self):
        self.assertEqual(failures.classify_engine_log("", engine_alive=False),
                         "engine_dead")
        self.assertEqual(failures.classify_engine_log(None, engine_alive=False),
                         "engine_dead")

    def test_apps_own_breadcrumbs_do_not_count_as_engine_output(self):
        tail = ("[app 2026-07-08 01:00:00] autostart mode=screen running=false\n"
                "[app 2026-07-08 01:00:01] spawn mode=screen\n")
        self.assertEqual(failures.classify_engine_log(tail, engine_alive=False),
                         "engine_dead")

    def test_node_missing_in_log_text_trusted_even_if_probe_disagrees(self):
        self.assertEqual(
            failures.classify_engine_log("env: node: No such file or directory",
                                         npx_present=True, engine_alive=False),
            "node_missing")


class CatalogMirrorTestCase(unittest.TestCase):
    """server/failure_catalog.py (GET /api/failures) must project every id verbatim."""

    def test_server_catalog_covers_every_failure_id(self):
        from server import failure_catalog
        doc = failure_catalog.catalog()["failures"]
        self.assertEqual(set(doc), set(failures.FAILURES))
        for fid, entry in failures.FAILURES.items():
            self.assertEqual(doc[fid], {"zh": entry["plain_zh"], "en": entry["plain_en"],
                                        "action_id": entry["action_id"]}, fid)


class TombstoneTestCase(unittest.TestCase):
    """§25 v2.0.0: retired ids stay (add-only) and say so; the §78 ids are live."""

    RETIRED = {"agent_unloaded", "dashboard_stale", "actd_stalled", "deploy_blind_tcc",
               "fd_limit", "claude_bypass_disclaimer", "launchd_orphan"}

    def test_the_act_only_ids_are_tombstones(self):
        self.assertLessEqual(self.RETIRED, failures.RETIRED)
        for fid in failures.RETIRED:
            entry = failures.FAILURES[fid]
            self.assertEqual(entry["retired"], "v2.0.0", fid)
            self.assertTrue(entry["plain_zh"].startswith("（已退役）"), fid)
            self.assertTrue(entry["plain_en"].startswith("(retired)"), fid)
            self.assertEqual(failures.row_class(fid), "", fid)

    def test_the_isolation_ids_are_live(self):
        for fid in ("claude_bare_unsupported", "isolation_footprint_violation"):
            self.assertIn(fid, failures.FAILURES)
            self.assertNotIn(fid, failures.RETIRED)
            self.assertTrue(failures.FAILURES[fid]["plain_zh"])

    def test_the_kept_lite_ids_are_live(self):
        for fid in ("claude_cli_missing", "claude_auth_failed", "node_missing", "engine_dead",
                    "screen_tcc_lost", "cron_missing", "cron_fda_blocked", "cron_tcc_blocked",
                    "config_invalid", "network_error",
                    # still produced by surviving server-agent / stable-copy rows
                    "claude_blind", "interpreter_blind", "claude_cli_outdated"):
            self.assertNotIn(fid, failures.RETIRED, fid)


class RowClassTestCase(unittest.TestCase):
    """§25 row class `owner_action` — the §56.3 deploy verdict skips these rows
    (2026-09-03: v1.0.7 rolled back on `stable claude` FAILing for a Full Disk
    Access grant the owner had yet to click)."""

    # the in-app one-click repairs: a row whose remedy is one of these is NOT
    # an owner action — code (a reload, a re-render, a restart) fixes it
    _CODE_REPAIRS = {"install_claude", "install_node", "install_ffmpeg", "restart_engine",
                     "reload_agent", "repair_cron", "restart_actd", "fix_config", "retry",
                     "show_engine_log", "open_settings_key"}

    def test_tcc_grant_ids_are_owner_action(self):
        for fid in ("claude_blind", "cron_fda_blocked", "cron_tcc_blocked", "ui_build_tcc_blocked",
                    "screen_tcc_lost"):
            self.assertEqual(failures.row_class(fid), failures.OWNER_ACTION, fid)

    def test_code_fixable_ids_and_unknown_ids_have_no_class(self):
        for fid in ("dashboard_stale", "agent_unloaded", "claude_cli_missing",
                    "claude_cli_outdated", "deploy_blind_tcc",
                    "interpreter_blind", "config_invalid", "no_such_id", "", None):
            self.assertEqual(failures.row_class(fid), "", repr(fid))

    def test_every_owner_action_id_is_in_the_catalog_with_a_manual_remedy(self):
        for fid in failures.OWNER_ACTION_IDS:
            self.assertIn(fid, failures.FAILURES, fid)
            self.assertNotIn(failures.FAILURES[fid]["action_id"], self._CODE_REPAIRS,
                             "%s is owner_action yet maps to an in-app code repair" % fid)

    def test_owner_action_is_a_strict_subset_of_the_fresh_install_human_bucket(self):
        # §69's "human" bucket = grants + credentials + tool installs; the deploy
        # verdict skips only the grants — a deploy that newly loses a tool or a
        # credential is a regression, so those must stay out of OWNER_ACTION_IDS
        from act.lib import fresh_install
        self.assertTrue(failures.OWNER_ACTION_IDS < fresh_install.HUMAN_FAILURE_IDS)
        for fid in ("claude_cli_missing", "claude_auth_failed", "node_missing", "engine_dead"):
            self.assertIn(fid, fresh_install.HUMAN_FAILURE_IDS)
            self.assertEqual(failures.row_class(fid), "", fid)

    def test_with_failure_stamps_the_class_on_the_row(self):
        owner = doctor.CheckResult("cron", doctor.FAIL, "blind", "grant").with_failure("cron_fda_blocked")
        code = doctor.CheckResult("dashboard", doctor.FAIL, "stale", "restart").with_failure("dashboard_stale")
        plain = doctor.CheckResult("dashboard", doctor.FAIL, "stale", "restart")
        self.assertEqual(owner.row_class, "owner_action")
        self.assertEqual(code.row_class, "")
        self.assertEqual(plain.row_class, "")


class DoctorJSONTestCase(unittest.TestCase):
    def test_render_json_carries_classification(self):
        rows = [doctor.CheckResult("claude CLI", doctor.FAIL, "not on PATH",
                                   "install it").with_failure("claude_cli_missing"),
                doctor.CheckResult("state dirs", doctor.OK, "writable"),
                doctor.CheckResult("cron", doctor.FAIL, "blind",
                                   "grant").with_failure("cron_fda_blocked")]
        data = json.loads(doctor.render_json(rows))
        self.assertEqual(data["checks"][0]["failure_id"], "claude_cli_missing")
        self.assertEqual(data["checks"][0]["action_id"], "install_claude")
        self.assertEqual(data["checks"][0]["row_class"], "")
        self.assertEqual(data["checks"][1]["failure_id"], "")
        self.assertEqual(data["checks"][1]["row_class"], "")
        self.assertEqual(data["checks"][2]["row_class"], "owner_action",
                         "§69 fresh-install summary buckets owner-action FAILs by this key")
        for key in ("name", "status", "detail", "fix", "row_class"):
            self.assertIn(key, data["checks"][0])

    def test_json_flag_prints_parseable_output(self):
        buf = io.StringIO()
        probes = doctor.Probes(which=lambda _n: None, launchd_labels=[],
                               now=lambda: NOW)
        with contextlib.redirect_stdout(buf):
            doctor.main(["--fast", "--json"], probes=probes)
        data = json.loads(buf.getvalue())
        self.assertTrue(any(c["failure_id"] == "claude_cli_missing"
                            for c in data["checks"]))


class CronProbeCheckTestCase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        self.probe_path = doctor.CRON_PROBE_PATH
        self.addCleanup(lambda: self.probe_path.unlink(missing_ok=True))
        self.probes = doctor.Probes(now=lambda: NOW)

    def _write(self, ts: float, read_ok: bool):
        self.probe_path.write_text(json.dumps({
            "ts": _iso(ts), "protected_path": "/tmp/vault", "read_ok": read_ok,
        }), encoding="utf-8")

    def test_missing_probe_is_warn_not_fail(self):
        self.probe_path.unlink(missing_ok=True)
        res = doctor._check_cron_probe(self.probes, cron_installed=True)
        self.assertEqual(res.status, doctor.WARN)

    def test_fresh_probe_with_read_ok_passes(self):
        self._write(NOW - 600, True)
        res = doctor._check_cron_probe(self.probes, cron_installed=True)
        self.assertEqual(res.status, doctor.OK)

    def test_fresh_probe_blocked_is_fail_with_fda_classification(self):
        self._write(NOW - 600, False)
        res = doctor._check_cron_probe(self.probes, cron_installed=True)
        self.assertEqual(res.status, doctor.FAIL)
        self.assertEqual(res.failure_id, "cron_fda_blocked")
        self.assertEqual(res.action_id, "grant_cron_fda")
        self.assertIn("/usr/sbin/cron", res.fix)

    def test_stale_probe_warns_chain_stopped(self):
        self._write(NOW - 3 * 3600, True)
        res = doctor._check_cron_probe(self.probes, cron_installed=True)
        self.assertEqual(res.status, doctor.WARN)
        self.assertEqual(res.failure_id, "cron_missing")

    def test_corrupt_probe_degrades_to_warn(self):
        self.probe_path.write_text("{not json", encoding="utf-8")
        res = doctor._check_cron_probe(self.probes, cron_installed=True)
        self.assertEqual(res.status, doctor.WARN)


class CronChainProbeWiringTestCase(unittest.TestCase):
    """install.sh arms the probe; the export script writes it atomically."""

    def test_install_sh_cron_line_sets_the_probe_env(self):
        text = (REPO_ROOT / "install.sh").read_text(encoding="utf-8")
        m = re.search(r'INGEST_CHAIN="([^"]+)"', text)
        self.assertIsNotNone(m)
        self.assertIn("AIASSISTANT_CRON=1", m.group(1))
        self.assertIn("screenpipe-export.sh", m.group(1))

    def test_export_script_probe_block_gated_on_cron_env(self):
        text = (REPO_ROOT / "ingest" / "screenpipe-export.sh").read_text(
            encoding="utf-8")
        self.assertIn("AIASSISTANT_CRON", text)
        self.assertIn("cron_probe.json", text)
        # atomic write: tmp file then mv
        self.assertIn("cron_probe.json.tmp", text)


class ClaudePathRenderTestCase(unittest.TestCase):
    """install.sh puts the login shell's claude dir FIRST on every daemon PATH
    (2026-07-08: an outdated /opt/homebrew/bin/claude shadowing ~/.local/bin
    in the rendered plists broke every dispatch with "unknown option '--bg'")."""

    PATH_RE = re.compile(r"<key>PATH</key>\s*<string>([^<]+)</string>")

    def test_every_plist_template_leads_with_the_claude_placeholder(self):
        for plist in sorted((REPO_ROOT / "act" / "launchd").glob("*.plist")):
            m = self.PATH_RE.search(plist.read_text(encoding="utf-8"))
            self.assertIsNotNone(m, "%s has no PATH" % plist.name)
            self.assertTrue(
                m.group(1).startswith("/Users/YOURUSERNAME/.claude-bin:"),
                "%s PATH must lead with the .claude-bin placeholder (got %s)"
                % (plist.name, m.group(1)))

    def test_cron_chain_pins_the_claude_dir_first(self):
        text = (REPO_ROOT / "install.sh").read_text(encoding="utf-8")
        m = re.search(r'INGEST_CHAIN="([^"]+)"', text)
        self.assertIsNotNone(m)
        self.assertIn("export PATH=$CRON_CLAUDE_DIR:\\$PATH", m.group(1))

    def _render(self, claude_login_bin):
        out = Path(tempfile.mkdtemp(prefix="render-")) / "out.plist"
        # run the REAL render function, extracted verbatim from install.sh
        script = (
            'set -eu\n'
            'cd "$REPO"\n'
            + install_sh_prelude()
            + 'REPO_ROOT="$REPO"\n'
            # unvalidated on purpose: render_launchd_plist must swap in a
            # PyYAML-capable interpreter of its own (§55)
            'RUNTIME_PY=/fake/py/bin/python3\n'
            + ("CLAUDE_LOGIN_BIN=%s\n" % claude_login_bin
               if claude_login_bin else "")
            + 'render_launchd_plist act/launchd/com.zelin.aiassistant.server.plist "$OUT"\n'
        )
        proc = subprocess.run(
            ["bash", "-c", script], capture_output=True, text=True, timeout=30,
            env={**os.environ, "REPO": str(REPO_ROOT), "OUT": str(out)})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        m = self.PATH_RE.search(out.read_text(encoding="utf-8"))
        self.assertIsNotNone(m)
        return m.group(1)

    def test_render_puts_the_resolved_claude_dir_first(self):
        path = self._render("/fake/claude-home/claude")
        self.assertTrue(path.startswith("/fake/claude-home:"), path)
        self.assertNotIn(".claude-bin", path)
        self.assertIn("/opt/homebrew/bin", path)  # the rest of the PATH survives

    def test_render_falls_back_to_local_bin_without_a_resolved_claude(self):
        path = self._render(None)
        expected = str(Path.home() / ".local" / "bin")
        self.assertTrue(path.startswith(expected + ":"), path)


if __name__ == "__main__":
    unittest.main()
