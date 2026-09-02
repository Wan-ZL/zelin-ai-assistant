"""§56 deploy_state 投影判例：state/deploy_state.json → dashboard 顶层键 + doctor 行。

写方是 scripts/auto-deploy.sh（判例在 tests/integration/test_auto_deploy_script.py）；
这里钉读方：逐字段类型消毒（宪法第 11 条：撕裂/手改的文件不许崩 dashboard pass）、
「缺章 = 整键不存在」的 add-only 约定（同 update_available / device_label）、doctor
的 auto-deploy 行只在文件存在时出现（healthy → OK，其余 → WARN 并给 --force 修法）、
以及 §31 F2 的 syncd 变更闸门把整键 deploy_state 视为易变（每 10 分钟的 last_run
改写不得推一次全量快照）。沙箱 AIASSISTANT_HOME（tests/__init__.py）。
"""
import json
import unittest

from act import doctor, syncd
from act.lib import config, dashboard, deploy_state


class DeployStateReaderTestCase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        self.path = deploy_state.PATH
        self.addCleanup(lambda: self.path.unlink(missing_ok=True))

    def _write(self, obj):
        self.path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")

    def test_absent_file_reads_none(self):
        self.path.unlink(missing_ok=True)
        self.assertIsNone(deploy_state.read())

    def test_torn_or_non_object_file_reads_none(self):
        self.path.write_text("{\"status\": \"depl", encoding="utf-8")
        self.assertIsNone(deploy_state.read())
        self._write(["deployed"])
        self.assertIsNone(deploy_state.read())

    def test_fields_are_type_checked_and_unknown_keys_dropped(self):
        self._write({"status": "deployed", "version": "0.48.4", "head": 12345,
                     "prev": "", "last_deployed": None, "detail": "ok",
                     "notified_sha": "abc", "surprise": "x"})
        self.assertEqual(deploy_state.read(),
                         {"status": "deployed", "version": "0.48.4", "detail": "ok"})

    def test_unknown_status_values_are_kept_verbatim(self):
        # add-only：读方容忍不认识的 status，交给 UI 按「需要人看」处理
        self._write({"status": "paused_by_owner", "version": "0.48.4"})
        self.assertEqual(deploy_state.read()["status"], "paused_by_owner")

    def test_v0_48_13_fields_are_projected_and_mirror_only_keys_are_not(self):
        # add-only：running_version / install_report_version / reason 进投影；
        # trigger / interpreter / volume / repo / unattended_* 只住 HOME 镜像
        self._write({"status": "install_incomplete", "version": "0.48.11",
                     "running_version": "0.48.8", "install_report_version": "0.48.8",
                     "reason": "heartbeat_version_mismatch", "trigger": "launchd",
                     "interpreter": "/usr/bin/python3", "volume": "/Volumes/Storage",
                     "repo": "/Volumes/Storage/repo", "unattended_status": "blocked_tcc",
                     "incomplete_runs": "2", "tcc_notified_day": "2026-09-02"})
        self.assertEqual(deploy_state.read(), {
            "status": "install_incomplete", "version": "0.48.11",
            "running_version": "0.48.8", "install_report_version": "0.48.8",
            "reason": "heartbeat_version_mismatch"})

    def test_read_mirror_is_a_superset_with_the_unattended_triple(self):
        mirror = self.path.parent / "mirror.json"
        self.addCleanup(lambda: mirror.unlink(missing_ok=True))
        mirror.write_text(json.dumps({
            "status": "deployed", "trigger": "terminal", "interpreter": "/usr/bin/python3",
            "volume": "/Volumes/Storage", "repo": "/Volumes/Storage/repo",
            "unattended_status": "blocked_tcc", "unattended_last_run": "2026-09-02T00:48:54Z",
            "unattended_detail": "volume_access=denied (errno 1)", "tcc_notified_day": "x",
            "incomplete_sha": "abc"}), encoding="utf-8")
        got = deploy_state.read_mirror(mirror)
        self.assertEqual(got["unattended_status"], "blocked_tcc")
        self.assertEqual(got["unattended_last_run"], "2026-09-02T00:48:54Z")
        self.assertEqual(got["interpreter"], "/usr/bin/python3")
        self.assertEqual(got["trigger"], "terminal")
        self.assertNotIn("tcc_notified_day", got, "private bookkeeping stays private")
        self.assertNotIn("incomplete_sha", got)
        self.assertIsNone(deploy_state.read_mirror(self.path.parent / "absent.json"))
        # parts, not a string suffix: the Windows leg spells the separators differently
        self.assertEqual(deploy_state.MIRROR_PATH.parts[-4:],
                         ("Library", "Application Support", "ZelinAIAssistant", "deploy_state.json"))

    def test_last_incident_is_projected(self):
        # v0.48.17 add-only（#135 review）：回滚判决独立于 status 存活到下一次 deployed
        self._write({"status": "up_to_date", "version": "0.48.11",
                     "last_incident": "2026-09-02T00:48:54Z rollback_failed: rollback refused (store2)"})
        self.assertEqual(deploy_state.read()["last_incident"],
                         "2026-09-02T00:48:54Z rollback_failed: rollback refused (store2)")
        self.assertIn("last_incident", deploy_state.FIELDS)

    def test_read_prefers_the_mirror_when_it_describes_this_checkout(self):
        # blocked_tcc is exactly the case where the job cannot rewrite the
        # projection: a stale `up_to_date` there must not outrank the mirror
        mirror = self.path.parent / "mirror.json"
        self.addCleanup(lambda: mirror.unlink(missing_ok=True))
        real = deploy_state.MIRROR_PATH
        deploy_state.MIRROR_PATH = mirror
        self.addCleanup(setattr, deploy_state, "MIRROR_PATH", real)
        self._write({"status": "up_to_date", "version": "0.48.11", "last_run": "2026-09-01T00:00:00Z"})
        # no mirror → projection
        self.assertEqual(deploy_state.read()["status"], "up_to_date")
        # another clone's mirror → projection
        mirror.write_text(json.dumps({"status": "blocked_tcc", "repo": str(self.path.parent / "elsewhere"),
                                      "last_run": "2026-09-02T00:00:00Z"}), encoding="utf-8")
        self.assertEqual(deploy_state.read()["status"], "up_to_date")
        # a mirror without `repo` (pre-mirror shape) is not trusted either
        mirror.write_text(json.dumps({"status": "blocked_tcc"}), encoding="utf-8")
        self.assertEqual(deploy_state.read()["status"], "up_to_date")
        # this checkout's mirror wins, and only FIELDS come through
        mirror.write_text(json.dumps({"status": "blocked_tcc", "repo": str(config.HOME),
                                      "last_run": "2026-09-02T00:00:00Z", "volume": "/Volumes/X",
                                      "interpreter": "/x/python3", "denied_path": "/Volumes/X/repo"}),
                          encoding="utf-8")
        got = deploy_state.read()
        self.assertEqual(got, {"status": "blocked_tcc", "last_run": "2026-09-02T00:00:00Z"})
        self.assertEqual(deploy_state.attach({})["deploy_state"]["status"], "blocked_tcc")
        # an explicit path is read as given (no mirror lookup)
        self.assertEqual(deploy_state.read(self.path)["status"], "up_to_date")

    def test_attach_adds_key_only_when_present(self):
        self.path.unlink(missing_ok=True)
        self.assertNotIn("deploy_state", deploy_state.attach({}))
        self._write({"status": "up_to_date", "version": "0.48.4"})
        dash = deploy_state.attach({})
        self.assertEqual(dash["deploy_state"], {"status": "up_to_date", "version": "0.48.4"})


class DashboardProjectionTestCase(unittest.TestCase):
    """build_dashboard 顶层 add-only 键 deploy_state（§2 兄弟字段）。"""

    def setUp(self):
        config.ensure_state_dirs()
        self.path = deploy_state.PATH
        self.addCleanup(lambda: self.path.unlink(missing_ok=True))
        self.cfg = config.Config()

    def _build(self):
        return dashboard.build_dashboard(reqs=[], agents=[], cfg=self.cfg, archived=[])

    def test_key_absent_without_file(self):
        self.path.unlink(missing_ok=True)
        self.assertNotIn("deploy_state", self._build())

    def test_state_lands_top_level(self):
        self.path.write_text(json.dumps({
            "status": "deployed", "version": "0.48.4", "head": "a" * 40,
            "prev": "b" * 40, "last_deployed": "2026-09-01T10:00:00Z",
            "last_run": "2026-09-01T10:10:00Z", "detail": "deployed bbbbbbb -> aaaaaaa",
        }), encoding="utf-8")
        ds = self._build()["deploy_state"]
        self.assertEqual(ds["version"], "0.48.4")
        self.assertEqual(ds["last_deployed"], "2026-09-01T10:00:00Z")
        self.assertEqual(ds["prev"], "b" * 40)

    def test_last_run_churn_does_not_move_the_syncd_gate_digest(self):
        """§56 × §31 F2：auto-deploy 每 10 分钟重写 last_run（up_to_date 也写），
        两次 build_dashboard 只差 deploy_state 时 syncd 的变更闸门摘要必须相同——
        否则 mode=cloud 的机器零看板活动也每 10 分钟推一次全量加密快照（L6 风暴回归）。
        """
        base = {"status": "up_to_date", "version": "0.48.6", "head": "a" * 40,
                "prev": "b" * 40, "last_deployed": "2026-09-01T10:00:00Z"}
        digests = []
        for last_run in ("2026-09-01T10:10:00Z", "2026-09-01T10:20:00Z"):
            self.path.write_text(json.dumps(dict(base, last_run=last_run)), encoding="utf-8")
            dash = self._build()
            self.assertEqual(dash["deploy_state"]["last_run"], last_run)
            digests.append(syncd._gate_digest(json.dumps(dash).encode("utf-8")))
        self.assertEqual(digests[0], digests[1])
        # 一次真部署（status/version 变）也不单独触发推送——整键易变
        self.path.write_text(json.dumps(dict(base, status="deployed", version="0.48.7",
                                             last_run="2026-09-01T10:30:00Z")),
                             encoding="utf-8")
        self.assertEqual(syncd._gate_digest(json.dumps(self._build()).encode("utf-8")),
                         digests[0])


class DoctorRowTestCase(unittest.TestCase):
    """doctor 的 auto-deploy 行：无文件无行；deployed/up_to_date OK；其余 WARN。"""

    def setUp(self):
        config.ensure_state_dirs()
        self.path = deploy_state.PATH
        self.addCleanup(lambda: self.path.unlink(missing_ok=True))

    def _row(self):
        res = doctor._check_auto_deploy(doctor.Probes())
        return res if isinstance(res, list) else [res]

    def test_no_file_no_row(self):
        self.path.unlink(missing_ok=True)
        self.assertEqual(self._row(), [])

    def test_deployed_is_ok_and_names_the_version(self):
        self.path.write_text(json.dumps({"status": "deployed", "version": "0.48.4",
                                         "last_deployed": "2026-09-01T10:00:00Z"}),
                             encoding="utf-8")
        (row,) = self._row()
        self.assertEqual(row.name, "auto-deploy")
        self.assertEqual(row.status, doctor.OK)
        self.assertIn("0.48.4", row.detail)
        self.assertIn("2026-09-01T10:00:00Z", row.detail)

    def test_up_to_date_is_ok(self):
        self.path.write_text(json.dumps({"status": "up_to_date", "version": "0.48.4"}),
                             encoding="utf-8")
        (row,) = self._row()
        self.assertEqual(row.status, doctor.OK)

    def test_rolled_back_warns_with_the_force_fix(self):
        self.path.write_text(json.dumps({
            "status": "rolled_back", "version": "0.48.3",
            "detail": "doctor new FAIL after v0.48.4: dashboard", "failed_sha": "c" * 40,
        }), encoding="utf-8")
        (row,) = self._row()
        self.assertEqual(row.status, doctor.WARN)
        self.assertIn("rolled_back", row.detail)
        self.assertIn("dashboard", row.detail)
        self.assertIn("auto-deploy.log", row.fix)
        self.assertIn("--force", row.fix)

    def test_every_non_healthy_status_warns(self):
        # ci_pending 也在这里（§56.4）：等待态必须看得见，卡住几小时的等待更要
        for status in ("refused_dirty", "refused_branch", "fetch_failed", "failed",
                       "rollback_failed", "ci_pending", "ci_failed", "install_incomplete",
                       "blocked_tcc", "something_new"):
            self.path.write_text(json.dumps({"status": status, "version": "0.48.4"}),
                                 encoding="utf-8")
            (row,) = self._row()
            self.assertEqual(row.status, doctor.WARN, status)
            self.assertIn(status, row.detail)

    def test_row_is_part_of_the_check_list(self):
        self.assertIn(doctor._check_auto_deploy, doctor._checks_for_platform())


if __name__ == "__main__":
    unittest.main()
