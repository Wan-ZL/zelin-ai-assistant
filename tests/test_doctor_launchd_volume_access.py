"""doctor `launchd volume access` 行（CONTRACT §56.3 step 0 / §56.4；live 事故 2026-09-02）。

launchd 起的自动部署任务读不到外置卷上的 repo（TCC 按 responsible executable 授权，
任务收不到弹窗），而 doctor 自己跑在 owner 的终端里、借着终端的授权什么都读得到——
所以这一行**不探**，只读无人值守那一轮留下的证据：HOME 镜像的 unattended_* 三元组
（scripts/auto-deploy.sh 只在非终端触发的运行里写它），其次 autodeploy.launchd.log
的尾部 + mtime（launchd 的 stderr 没时间戳；启动器连 act 都 import 不到时镜像里什么
都没有，这份日志是唯一见证）。全部经 Probes 注入，绝不读开发者的真 HOME。
"""
import os
import sys
import unittest
from unittest import mock

from act import doctor
from act.lib import config, deploy_state

NOW = 1_700_000_000.0
_WIN = sys.platform.startswith("win")

INTERP = "/Applications/Xcode.app/Contents/Developer/usr/bin/python3"


def _plist(home=None, interp=INTERP):
    return ("<plist><dict><key>Label</key><string>%s</string>"
            "<key>ProgramArguments</key><array><string>%s</string><string>-m</string>"
            "<string>act.auto_deploy</string></array>"
            "<key>EnvironmentVariables</key><dict><key>AIASSISTANT_HOME</key><string>%s</string>"
            "</dict></dict></plist>"
            % (doctor.AUTODEPLOY_LABEL, interp, home or str(config.HOME)))


def _iso(ts):
    import datetime as _dt
    return _dt.datetime.fromtimestamp(ts, _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# the 2026-09-02 launchd stderr, verbatim shape
LAUNCHD_LOG = (
    "%s: Error while finding module specification for 'act.auto_deploy' "
    "(ModuleNotFoundError: No module named 'act')\n"
    "Traceback (most recent call last):\n  File \"<stdin>\", line 17, in <module>\n"
    "PermissionError: [Errno 1] Operation not permitted: "
    "'/Volumes/Storage/Server/Projects/zelin-ai-assistant/state/deploy_state.json.tmp'\n"
    "rm: /Volumes/Storage/Server/Projects/zelin-ai-assistant/state/auto-deploy.lock: "
    "Operation not permitted\n" % INTERP)


@unittest.skipIf(_WIN, "launchd rows model a macOS install")
class LaunchdVolumeAccessRowTestCase(unittest.TestCase):
    def setUp(self):
        p = mock.patch("sys.platform", "darwin")
        p.start()
        self.addCleanup(p.stop)
        config.ensure_state_dirs()

    def _probes(self, plist=_plist(), mirror=None, log="", log_age=None):
        return doctor.Probes(
            installed_plist_text=lambda label: plist if label == doctor.AUTODEPLOY_LABEL else None,
            deploy_mirror_read=lambda: mirror,
            launchd_log_tail=lambda short: log if short == "autodeploy" else "",
            launchd_log_mtime=lambda short: (NOW - log_age) if (log_age is not None and short == "autodeploy") else None,
            now=lambda: NOW,
        )

    def _row(self, **kw):
        res = doctor._check_launchd_volume_access(self._probes(**kw))
        return res if isinstance(res, list) else [res]

    # -- no row ------------------------------------------------------------- #

    def test_no_installed_autodeploy_plist_means_no_row(self):
        self.assertEqual(self._row(plist=None), [])

    def test_a_plist_deploying_another_checkout_is_not_our_business(self):
        self.assertEqual(self._row(plist=_plist(home="/Volumes/Other/repo"),
                                   mirror={"unattended_status": "blocked_tcc"}), [])

    # -- the mirror's unattended verdict ------------------------------------- #

    def test_blocked_tcc_unattended_run_is_fail_with_the_exact_interpreter(self):
        mirror = {
            "status": "deployed", "trigger": "terminal",     # a green run from the terminal…
            "unattended_status": "blocked_tcc",              # …does not erase the job's own verdict
            "unattended_last_run": "2026-09-02T00:48:54Z",
            "unattended_detail": "volume_access=denied (errno 1) at /Volumes/Storage/…/install.sh: "
                                 "the launchd-started job cannot read/write /Volumes/Storage; "
                                 "grant Full Disk Access to " + INTERP,
            "volume": "/Volumes/Storage", "repo": str(config.HOME),
            "denied_path": "/Volumes/Storage/Server/Projects/zelin-ai-assistant/install.sh",
        }
        (row,) = self._row(mirror=mirror)
        self.assertEqual(row.name, "launchd volume access")
        self.assertEqual(row.status, doctor.FAIL)
        self.assertEqual(row.failure_id, "deploy_blind_tcc")
        self.assertEqual(row.action_id, "open_deps")
        self.assertIn("2026-09-02T00:48:54Z", row.detail)
        self.assertIn("/Volumes/Storage", row.detail)
        self.assertIn("zelin-ai-assistant/install.sh", row.detail, "the denied path comes from the mirror-only key")
        self.assertIn("errno 1", row.detail)
        self.assertNotIn("launchd paths", row.fix, "EPERM is unambiguous: straight to the grant")
        # the remediation names BOTH grants verbatim - the plist's ProgramArguments[0]
        # and the stable daemon copy of claude (§55 第五幕: a fixed path, never the
        # per-version ~/.local/share/claude/versions/<v> that dies with every
        # update) - and says plainly that a run started from a terminal (even a
        # kickstart typed there) proves nothing about timer-fired runs
        self.assertIn(INTERP, row.fix)
        self.assertIn(str(config.stable_claude_bin()), row.fix)
        self.assertNotIn("versions/<v>", row.fix)
        self.assertIn("survives claude updates", row.fix)
        self.assertIn("Full Disk Access", row.fix)
        self.assertIn("kickstart", row.fix)
        self.assertIn("terminal", row.fix)
        self.assertIn("timer", row.fix)
        self.assertIn("proves nothing", row.fix)

    def test_a_good_unattended_run_is_ok_even_if_the_last_terminal_run_failed(self):
        mirror = {"status": "refused_dirty", "trigger": "terminal",
                  "unattended_status": "up_to_date", "unattended_last_run": _iso(NOW - 300),
                  "repo": str(config.HOME)}
        (row,) = self._row(mirror=mirror)
        self.assertEqual(row.status, doctor.OK)
        self.assertIn("up_to_date", row.detail)
        self.assertIn(_iso(NOW - 300), row.detail)

    def test_mirror_about_another_repo_is_ignored(self):
        mirror = {"unattended_status": "blocked_tcc", "repo": "/somewhere/else"}
        (row,) = self._row(mirror=mirror)
        self.assertEqual(row.status, doctor.OK)
        self.assertIn("no unattended run recorded yet", row.detail)

    def test_no_mirror_and_no_log_is_ok_and_names_the_mirror_path(self):
        (row,) = self._row()
        self.assertEqual(row.status, doctor.OK)
        self.assertIn(str(deploy_state.MIRROR_PATH), row.detail)

    # -- launchd stderr evidence (the launcher died before the script) -------- #

    def test_recent_eperm_in_the_launchd_log_is_fail_without_any_mirror(self):
        (row,) = self._row(log=LAUNCHD_LOG, log_age=2 * 3600)
        self.assertEqual(row.status, doctor.FAIL)
        self.assertEqual(row.failure_id, "deploy_blind_tcc")
        self.assertIn("120 min ago", row.detail)
        self.assertIn(INTERP, row.detail)
        self.assertIn("no timestamps", row.detail)
        self.assertIn(INTERP, row.fix)

    def test_module_not_found_act_alone_counts_as_evidence_but_is_flagged_ambiguous(self):
        # §55: the import failure alone cannot tell a TCC-blind interpreter from a
        # mis-rendered PYTHONPATH — still FAIL (needs a human either way), but the
        # fix sends the reader to `launchd paths` first (Codex review P2 on #140)
        log = "%s: Error while finding module specification for 'act.auto_deploy' " \
              "(ModuleNotFoundError: No module named 'act')\n" % INTERP
        (row,) = self._row(log=log, log_age=600)
        self.assertEqual(row.status, doctor.FAIL)
        self.assertIn("No module named 'act'", row.detail)
        self.assertTrue(row.fix.startswith("check doctor's launchd paths row first") or
                        row.fix.startswith("先看 doctor 的 launchd paths 行"), row.fix)
        self.assertIn("install.sh", row.fix)
        self.assertIn(INTERP, row.fix)
        # an EPERM spelling in the same log is unambiguous → no detour
        (row,) = self._row(log=LAUNCHD_LOG, log_age=600)
        self.assertNotIn("launchd paths", row.fix)

    def test_stale_log_evidence_older_than_24h_is_not_a_current_failure(self):
        (row,) = self._row(log=LAUNCHD_LOG, log_age=25 * 3600)
        self.assertEqual(row.status, doctor.OK)

    def test_log_evidence_is_superseded_by_a_later_good_unattended_run(self):
        mirror = {"unattended_status": "up_to_date", "unattended_last_run": _iso(NOW - 600),
                  "repo": str(config.HOME)}
        (row,) = self._row(mirror=mirror, log=LAUNCHD_LOG, log_age=3600)
        self.assertEqual(row.status, doctor.OK, "the job reached the repo after that log line")

    def test_log_evidence_beats_an_older_good_unattended_run(self):
        mirror = {"unattended_status": "up_to_date", "unattended_last_run": _iso(NOW - 7200),
                  "repo": str(config.HOME)}
        (row,) = self._row(mirror=mirror, log=LAUNCHD_LOG, log_age=600)
        self.assertEqual(row.status, doctor.FAIL)

    def test_benign_launchd_log_is_not_evidence(self):
        (row,) = self._row(log="auto_deploy: nothing to do\n", log_age=60)
        self.assertEqual(row.status, doctor.OK)

    # -- the real log seams (the defaults behind the injected probes) ---------- #

    def test_launchd_log_mtime_reads_the_state_fallback_and_none_when_absent(self):
        # the default seam: ~/Library/Logs first, the pre-v0.48 state/ address as
        # fallback; neither present → None (never raises)
        short = "volume-access-fixture-%d" % os.getpid()
        self.assertIsNone(doctor._launchd_log_mtime(short))
        self.assertEqual(doctor._launchd_log_tail(short), "")
        legacy = config.HOME / "state" / ("%s.launchd.log" % short)
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_text("PermissionError: [Errno 1] Operation not permitted\n", encoding="utf-8")
        self.addCleanup(lambda: legacy.unlink(missing_ok=True))
        self.assertAlmostEqual(doctor._launchd_log_mtime(short), legacy.stat().st_mtime)
        self.assertIn("Errno 1", doctor._launchd_log_tail(short))
        self.assertEqual(doctor._launchd_log_paths(short)[1], legacy)

    # -- composition ---------------------------------------------------------- #

    def test_row_is_in_the_darwin_check_list_after_launchd_claude(self):
        checks = doctor._checks_for_platform()
        self.assertIn(doctor._check_launchd_volume_access, checks)
        self.assertGreater(checks.index(doctor._check_launchd_volume_access),
                           checks.index(doctor._check_launchd_claude))

    def test_failure_id_is_catalogued(self):
        from act.lib import failures
        self.assertIn("deploy_blind_tcc", failures.FAILURES)
        self.assertEqual(failures.action_id("deploy_blind_tcc"), "open_deps")


class AutoDeployRowNewStatusesTestCase(unittest.TestCase):
    """`auto-deploy` 行对 v0.48.20 两个新状态词的修法指向（§56.4）。"""

    def setUp(self):
        config.ensure_state_dirs()
        self.path = deploy_state.PATH
        self.addCleanup(lambda: self.path.unlink(missing_ok=True))

    def _row(self, **state):
        import json
        self.path.write_text(json.dumps(state), encoding="utf-8")
        res = doctor._check_auto_deploy(doctor.Probes())
        return res if isinstance(res, list) else [res]

    def test_blocked_tcc_warns_and_points_at_the_volume_access_row(self):
        (row,) = self._row(status="blocked_tcc", version="0.48.11",
                           detail="volume_access=denied (errno 1) …")
        self.assertEqual(row.status, doctor.WARN)
        self.assertIn("blocked_tcc", row.detail)
        self.assertIn("launchd volume access", row.fix)
        self.assertIn("Full Disk Access", row.fix)
        self.assertNotIn("--force", row.fix, "authorisation, not a retry, is the fix")

    def test_install_incomplete_warns_names_the_running_version_and_self_heals(self):
        (row,) = self._row(status="install_incomplete", version="0.48.11",
                           running_version="0.48.8", install_report_version="0.48.8",
                           reason="install_report_version_mismatch heartbeat_version_mismatch",
                           detail="install_report.json says v0.48.8, checkout is v0.48.11")
        self.assertEqual(row.status, doctor.WARN)
        self.assertIn("running v0.48.8", row.detail)
        self.assertIn("v0.48.11", row.detail)
        self.assertIn("install.sh", row.fix)

    def test_healthy_rows_do_not_mention_running_version(self):
        (row,) = self._row(status="up_to_date", version="0.48.20", running_version="0.48.20")
        self.assertEqual(row.status, doctor.OK)
        self.assertNotIn("running", row.detail)
        self.assertEqual(row.fix, "")

    def test_healthy_status_with_an_unresolved_incident_warns(self):
        # #135 review: a refused rollback left HEAD on the new sha; the next
        # interval wrote up_to_date. The verdict must stay visible until the
        # next successful deploy clears `last_incident`.
        (row,) = self._row(status="up_to_date", version="0.48.11", running_version="0.48.11",
                           last_incident="2026-09-02T00:48:54Z rollback_failed: rollback refused "
                                         "(store2 became the registry truth): doctor new FAIL dashboard")
        self.assertEqual(row.status, doctor.WARN)
        self.assertIn("up_to_date (v0.48.11)", row.detail)
        self.assertIn("unresolved deploy incident", row.detail)
        self.assertIn("rollback refused", row.detail)
        self.assertIn("next successful deploy", row.fix)
        (row,) = self._row(status="deployed", version="0.48.12", last_deployed="2026-09-02T01:00:00Z")
        self.assertEqual(row.status, doctor.OK, "a deployed with no incident on file is plain OK")


if __name__ == "__main__":
    unittest.main()
