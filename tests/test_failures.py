"""AI Doctor (CONTRACT §25) — failure classification, cron probe, AI-fix bundle.

Covers:
- act/lib/failures: catalog integrity, classify() precision (known raw error
  texts map to ids, unknown text honestly returns None), bilingual copy;
- Swift drift guard: every failure id in FAILURES appears in
  mac/Sources/Doctor.swift's FailureCatalog (same pattern as
  tests/test_capture_exclusion.py);
- dashboard projection: dispatch_error_id / last_error_id ride alongside the
  raw error text;
- doctor: --json shape carries failure_id/action_id; the cron FDA probe check
  reads state/cron_probe.json honestly (missing / fresh-ok / fresh-blocked /
  stale);
- act/ai_fix: the generated .command embeds a SCRUBBED bundle (a planted
  sk-ant- key must not survive), documents the safety posture, instructs the
  GitHub-issue ending, and respects doctor.ai_fix_enabled: false;
- notify builders: bilingual per the §15 language override, and every body
  names a next step.

Everything runs inside the sandbox AIASSISTANT_HOME (tests/__init__.py).
"""
import contextlib
import io
import json
import os
import re
import tempfile
import unittest
from pathlib import Path

from tests import TMP_HOME

from act import ai_fix, doctor
from act.lib import config, dashboard, failures
from act.lib.registry import Requirement

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
        # names what breaks or what to do (the em-dash clause).
        for fid, entry in failures.FAILURES.items():
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


class EngineLogClassifyTestCase(unittest.TestCase):
    """classify_engine_log — the engine-death diagnosis (audit 2.3).

    Swift mirror: RecordingController.diagnoseEngine (Recording.swift)."""

    DOWNLOAD = ("npm warn exec The following package was not found and will "
                "be installed: screenpipe@0.3.349")

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


class SwiftDriftTestCase(unittest.TestCase):
    """FailureCatalog (Doctor.swift) must know every python-side id."""

    def test_swift_catalog_covers_every_failure_id(self):
        swift = (REPO_ROOT / "mac" / "Sources" / "Doctor.swift").read_text(
            encoding="utf-8")
        for fid in failures.FAILURES:
            self.assertIn('"%s"' % fid, swift,
                          "mac/Sources/Doctor.swift FailureCatalog is missing "
                          "failure id %r (drift with act/lib/failures.py)" % fid)


class DashboardClassificationTestCase(unittest.TestCase):
    def test_queued_item_carries_dispatch_error_id(self):
        req = Requirement.from_dict({
            "id": "R-900", "title": "t", "status": "approved",
            "execution": {"last_error": "zsh:1: command not found: claude"},
        })
        dash = dashboard.build_dashboard(reqs=[req], agents=[], cfg=config.Config())
        item = dash["running"][0]
        self.assertEqual(item["dispatch_error"], "zsh:1: command not found: claude")
        self.assertEqual(item["dispatch_error_id"], "claude_cli_missing")

    def test_unknown_error_projects_null_id(self):
        req = Requirement.from_dict({
            "id": "R-901", "title": "t", "status": "approved",
            "execution": {"last_error": "claude --bg exited 1 (no output)"},
        })
        dash = dashboard.build_dashboard(reqs=[req], agents=[], cfg=config.Config())
        self.assertIsNone(dash["running"][0]["dispatch_error_id"])


class DoctorJSONTestCase(unittest.TestCase):
    def test_render_json_carries_classification(self):
        rows = [doctor.CheckResult("claude CLI", doctor.FAIL, "not on PATH",
                                   "install it").with_failure("claude_cli_missing"),
                doctor.CheckResult("state dirs", doctor.OK, "writable")]
        data = json.loads(doctor.render_json(rows))
        self.assertEqual(data["checks"][0]["failure_id"], "claude_cli_missing")
        self.assertEqual(data["checks"][0]["action_id"], "install_claude")
        self.assertEqual(data["checks"][1]["failure_id"], "")
        for key in ("name", "status", "detail", "fix"):
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


class AIFixTestCase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        self.out_dir = Path(tempfile.mkdtemp(prefix="aifix-out-"))
        self.log = config.STATE_DIR / "actd.log"
        self.addCleanup(lambda: self.log.unlink(missing_ok=True))
        self.results = [doctor.CheckResult(
            "claude auth", doctor.FAIL, "live call failed",
            "re-paste the key").with_failure("claude_auth_failed")]

    def test_command_file_scrubs_secrets(self):
        self.log.write_text(
            "boom with key sk-ant-api03-SECRETSECRETSECRET123456\n" * 3,
            encoding="utf-8")
        path = ai_fix.build_command_file(results=self.results, out_dir=self.out_dir)
        text = path.read_text(encoding="utf-8")
        self.assertNotIn("sk-ant-api03-SECRETSECRETSECRET123456", text)
        self.assertIn("[脱敏]", text)   # sanitize.MASK survived into the bundle

    def test_command_file_is_executable_and_documents_safety(self):
        path = ai_fix.build_command_file(results=self.results, out_dir=self.out_dir)
        self.assertTrue(os.access(path, os.X_OK))
        text = path.read_text(encoding="utf-8")
        self.assertTrue(path.name.endswith(".command"))
        self.assertIn("SAFETY", text)
        self.assertIn("--dangerously-skip-permissions", text)  # stated as NOT used
        self.assertIn(ai_fix.ISSUES_URL, text)                 # issue-ending instruction
        self.assertIn(str(config.HOME), text)                  # cd into the repo

    def test_extra_context_lands_in_bundle(self):
        path = ai_fix.build_command_file(results=self.results,
                                         extra_context="banner said: pipeline down",
                                         out_dir=self.out_dir)
        self.assertIn("banner said: pipeline down",
                      path.read_text(encoding="utf-8"))

    def test_disabled_flag_exits_2_without_writing(self):
        cfg_path = config.CONFIG_PATH
        existed = cfg_path.exists()
        original = cfg_path.read_text(encoding="utf-8") if existed else None
        cfg_path.write_text("doctor:\n  ai_fix_enabled: false\n", encoding="utf-8")
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = ai_fix.main([])
            self.assertEqual(rc, 2)
            self.assertIn("ai_fix_enabled", buf.getvalue())
        finally:
            if existed:
                cfg_path.write_text(original, encoding="utf-8")
            else:
                cfg_path.unlink(missing_ok=True)


class NotifyCopyTestCase(unittest.TestCase):
    """§5 v0.14: builders follow the language override and name a next step."""

    def setUp(self):
        self.overrides = config.SETTINGS_OVERRIDES_PATH
        self.addCleanup(lambda: self.overrides.unlink(missing_ok=True))

    def _set_lang(self, lang: str):
        config.ensure_state_dirs()
        self.overrides.write_text(json.dumps({"language": lang}), encoding="utf-8")

    def test_builders_follow_language_override(self):
        from act.lib import notify
        self._set_lang("en")
        title, body = notify.msg_auto_resume_exhausted("Weekly report")
        self.assertNotRegex(title + body, re.compile(r"[一-鿿]"),
                            "English mode must not emit Chinese copy")
        self._set_lang("zh")
        title_zh, body_zh = notify.msg_auto_resume_exhausted("周报")
        self.assertIn("自动恢复已放弃", title_zh)

    def test_every_builder_body_names_a_next_step(self):
        from act.lib import notify
        self._set_lang("zh")
        builders = [notify.msg_new_card, notify.msg_done, notify.msg_needs_input,
                    notify.msg_auth, notify.msg_review_ready,
                    notify.msg_dispatch_failed, notify.msg_resuming,
                    notify.msg_auto_resume_exhausted]
        for fn in builders:
            _, body = fn("X")
            self.assertIn("——", body,
                          "%s body must carry a next step" % fn.__name__)


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


if __name__ == "__main__":
    unittest.main()
