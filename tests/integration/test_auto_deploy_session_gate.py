"""scripts/auto-deploy.sh 会话闸门判例（CONTRACT §56.3 第 4b 步）——真 bash + 真 git。

live 2026-09-07T05:35Z：v1.0.72 的自动部署在 P-029 派发 17 分钟后重启 actd，owner
正在终端里 attach 着那个会话，终端打出 `[worker crashed (exit 143) — respawning…]
Session f40f2001 has exited`（143 = SIGTERM）。法条：**只要 roster 上有活着的后台
claude 会话，就不重启 actd**——整轮部署 `deferred`，下一个 interval 再问。

夹具复用 tests/integration/test_auto_deploy_script.py 的 ``AutoDeployFixture``
（经模块名引用，unittest 不会把那边的 89 轮重新收进本模块）；假 act/executor.py 的
``live_session_count`` 按 FAKE_ROSTER_PLAN 逐轮作答（整数或 `unknown`）。

钉住的行为：
  - roster N>0 → `deferred`：HEAD 不动、install.sh 不跑、不通知；add-only 键
    deferred_reason=sessions_running / deferred_sessions=N / deferred_since=<首次>；
    CI 闸门在它之前照常问过（「新版本已就绪」说的是一个绿的目标）；
  - 连续几轮 deferred 共用同一个 deferred_since；roster 回 0 → 照常 deployed，
    deferred_* 与私账 roster_unknown_ticks 一起清掉；
  - roster 读不到 = fail closed：deferred_reason=roster_unknown、无 deferred_sessions、
    镜像私账 roster_unknown_ticks 计连续次数，第 AUTODEPLOY_ROSTER_UNKNOWN_LIMIT+1 轮
    闸门让行并在日志说明；中间任一轮读到数字就把计数清零；
  - 连续 deferred 满 AUTODEPLOY_DEFER_WARN_AFTER（默认 6 h）→ 日志 WARN，且真读方
    `act.lib.deploy_state.auto_deploy_row` 把这份投影渲染成 WARN（之前是 OK）；
  - `--force` 跳过闸门，日志记下会被打断的会话数；
  - install_incomplete 的修补重跑（同样重启全部 daemon）走同一道闸门；
  - 回滚**不**延后（闸门几分钟前刚以 0 放行；回滚是被 doctor 判死的部署的应急出口），
    但日志点名它打断了几个会话。
"""
import json
import os
import time
import unittest
from unittest import mock

import tests.integration.test_auto_deploy_script as base
from act.lib import deploy_state

BUDGET_SECONDS = 180  # 18 runs of real bash+git: ~31 s on a 2024 Mac standalone; ~6x margin like
                      # test_bootstrap_script (the parent file's 89 runs sit at 420 s)
_T0 = [0.0]


def setUpModule():
    # Clocked from when THIS module starts running, not from import: discovery
    # imports every test module up front, and the parent module's 89 runs
    # execute in between (an import-time stamp would bill them to us).
    _T0[0] = time.monotonic()


def tearDownModule():
    elapsed = time.monotonic() - _T0[0]
    if elapsed > BUDGET_SECONDS:
        raise AssertionError("tests/integration/test_auto_deploy_session_gate.py took %.0fs > %ds budget"
                             % (elapsed, BUDGET_SECONDS))


@unittest.skipIf(base._WIN, "bash + install.sh are POSIX-only")
class SessionGateTestCase(base.AutoDeployFixture):

    def _projection(self):
        """The repo projection through the REAL reader (what dashboard/doctor see)."""
        return deploy_state.read(self.live / "state" / "deploy_state.json")

    def test_live_sessions_defer_the_deploy_and_leave_head_alone(self):
        target = self.push("0.48.4")
        proc = self.run_script(doctor_plan=["-", "-"], roster=["2"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), self.base_sha, "HEAD must not move under live sessions")
        self.assertEqual(self.installs(), [], "install.sh (= every daemon restarted) never ran")
        self.assertEqual(self.notifications(), [], "a deferral is not an incident")
        self.assertEqual(self.roster_queries(), ["2"])
        self.assertEqual(self.queried_shas(), [target], "the CI gate ran first: the target IS green")
        st = self.state()
        self.assertEqual(st["status"], "deferred")
        self.assertEqual(st["deferred_reason"], "sessions_running")
        self.assertEqual(st["deferred_sessions"], "2")
        self.assertEqual(st["deferred_since"], st["last_run"])
        self.assertEqual(st["reason"], "sessions_running")
        self.assertEqual(st["head"], self.base_sha)
        self.assertEqual(st["version"], "0.48.3", "`version` stays the checkout's; the target is in detail")
        self.assertIn("v0.48.4", st["detail"])
        self.assertIn("2 live background claude session(s)", st["detail"])
        self.assertNotIn("roster_unknown_ticks", self.mirror())
        log = self.log_text()
        self.assertIn("DEFERRED (sessions_running)", log)
        self.assertNotIn("deploying", log)
        self.assertNotIn("WARN deploy deferred", log, "a fresh deferral is not overdue")
        # the real reader projects the new keys and renders an OK row (< 6 h)
        seen = self._projection()
        self.assertEqual(seen["deferred_sessions"], "2")
        row = deploy_state.auto_deploy_row(seen)
        self.assertEqual(row["status"], "ok", row)
        self.assertIn("waiting for 2 live claude session(s)", row["detail"])

    def test_deferral_episode_keeps_its_first_stamp_then_deploys_when_sessions_end(self):
        target = self.push("0.48.4")
        self.run_script(doctor_plan=["-", "-"], roster=["2"])
        since = self.state()["deferred_since"]
        time.sleep(1.1)  # the stamp has second resolution
        proc = self.run_script(doctor_plan=["-", "-"], roster=["1"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        st = self.state()
        self.assertEqual(st["status"], "deferred")
        self.assertEqual(st["deferred_sessions"], "1")
        self.assertEqual(st["deferred_since"], since, "one episode, the FIRST deferral's stamp")
        self.assertNotEqual(st["last_run"], since)
        self.assertEqual(self.head(), self.base_sha)
        proc = self.run_script(doctor_plan=["-", "-"], roster=["0"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), target)
        st = self.state()
        self.assertEqual(st["status"], "deployed")
        for key in ("deferred_reason", "deferred_sessions", "deferred_since"):
            self.assertNotIn(key, st, key)
        self.assertNotIn("roster_unknown_ticks", self.mirror())
        self.assertIn("session gate open again (roster: 0 live background sessions)", self.log_text())
        self.assertEqual(len(self.installs()), 1)
        self.assertEqual(self.roster_queries(), ["2", "1", "0"])

    def test_unreadable_roster_fails_closed_for_three_runs_then_yields(self):
        target = self.push("0.48.4")
        for tick in (1, 2, 3):
            proc = self.run_script(doctor_plan=["-", "-"], roster=["unknown"])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(self.head(), self.base_sha, "tick %d must not deploy blind" % tick)
            st = self.state()
            self.assertEqual(st["status"], "deferred")
            self.assertEqual(st["deferred_reason"], "roster_unknown")
            self.assertNotIn("deferred_sessions", st, "no count is known")
            self.assertIn("deferred_since", st)
            self.assertEqual(self.mirror()["roster_unknown_ticks"], str(tick))
            self.assertNotIn("roster_unknown_ticks", self._projection(),
                             "private bookkeeping never reaches dashboard/doctor readers")
            self.assertIn("(%d so far)" % tick, st["detail"])
        self.assertEqual(self.installs(), [])
        # the 4th consecutive unknown: the budget is spent, the gate yields and says why
        proc = self.run_script(doctor_plan=["-", "-"], roster=["unknown"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), target)
        self.assertEqual(self.state()["status"], "deployed")
        self.assertNotIn("roster_unknown_ticks", self.mirror())
        self.assertNotIn("deferred_reason", self.state())
        log = self.log_text()
        self.assertIn("roster unreadable for 3 consecutive runs — session gate yields", log)
        self.assertIn("AUTODEPLOY_ROSTER_UNKNOWN_LIMIT=3", log)
        self.assertEqual(len(self.installs()), 1)

    def test_a_readable_roster_resets_the_unknown_budget(self):
        self.push("0.48.4")
        self.run_script(doctor_plan=["-", "-"], roster=["unknown"])
        self.run_script(doctor_plan=["-", "-"], roster=["unknown"])
        self.assertEqual(self.mirror()["roster_unknown_ticks"], "2")
        # a real count (still deferring) is a readable roster: the unknown streak is over
        self.run_script(doctor_plan=["-", "-"], roster=["2"])
        st = self.state()
        self.assertEqual(st["status"], "deferred")
        self.assertEqual(st["deferred_reason"], "sessions_running")
        self.assertNotIn("roster_unknown_ticks", self.mirror())
        # …so the next unknown starts counting from 1 again and does not yield
        self.run_script(doctor_plan=["-", "-"], roster=["unknown"])
        self.assertEqual(self.mirror()["roster_unknown_ticks"], "1")
        self.assertEqual(self.state()["deferred_reason"], "roster_unknown")
        self.assertEqual(self.head(), self.base_sha)
        self.assertEqual(self.installs(), [])
        self.assertEqual(self.roster_queries(), ["unknown", "unknown", "2", "unknown"])

    def test_six_hours_of_deferral_warns_in_the_log_and_the_doctor_row(self):
        self.push("0.48.4")
        # an episode that began 7 h ago (the mirror is the script's own truth)
        since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 7 * 3600))
        self.mirror_dir.mkdir(parents=True, exist_ok=True)
        (self.mirror_dir / "deploy_state.json").write_text(json.dumps({
            "status": "deferred", "deferred_reason": "sessions_running",
            "deferred_sessions": "1", "deferred_since": since, "repo": str(self.live)}),
            encoding="utf-8")
        proc = self.run_script(doctor_plan=["-", "-"], roster=["1"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), self.base_sha, "no time cap ever ends a session")
        self.assertEqual(self.installs(), [])
        st = self.state()
        self.assertEqual(st["status"], "deferred")
        self.assertEqual(st["deferred_since"], since)
        self.assertIn("WARN deploy deferred for 7 h now (since %s)" % since, self.log_text())
        with mock.patch.dict(os.environ, {"AIASSISTANT_UI_LANG": "zh"}):
            row = deploy_state.auto_deploy_row(self._projection())
        self.assertEqual(row["status"], "warn", row)
        self.assertIn("更新已就绪，等待 1 个会话结束已 7 小时", row["detail"])
        self.assertIn("--force", row["fix"])
        with mock.patch.dict(os.environ, {"AIASSISTANT_UI_LANG": "en"}):
            row = deploy_state.auto_deploy_row(self._projection())
        self.assertIn("waiting for 1 session(s) to finish for 7 h", row["detail"])
        self.assertEqual(self.notifications(), [], "the row and the log carry it; no push")

    def test_force_skips_the_session_gate_and_logs_the_count(self):
        target = self.push("0.48.4")
        proc = self.run_script("--force", doctor_plan=["-", "-"], roster=["3"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), target)
        self.assertEqual(self.state()["status"], "deployed")
        self.assertIn("--force: session gate skipped — roster: 3 live background claude session(s)",
                      self.log_text())
        self.assertEqual(len(self.installs()), 1)

    def test_repair_install_rerun_is_gated_too(self):
        # §56.3 step 2: HEAD == origin/main but the machine runs an older version →
        # the confirming run re-runs install.sh, which restarts every daemon
        self.seed_running("0.48.2")
        self.sighting()
        proc = self.run_script(roster=["1"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.installs(), [], "no restart under a live session — not even a repair")
        st = self.state()
        self.assertEqual(st["status"], "deferred")
        self.assertEqual(st["deferred_sessions"], "1")
        self.assertIn("install.sh re-run of v0.48.3", st["detail"])
        self.assertEqual(self.mirror()["incomplete_seen"], self.base_sha, "the sighting is kept")
        proc = self.run_script(roster=["0"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(len(self.installs()), 1, "the repair ran once the sessions were gone")
        st = self.state()
        self.assertEqual(st["status"], "deployed")
        self.assertNotIn("deferred_since", st)
        self.assertIn("install completed on re-run", st["detail"])

    def test_rollback_is_not_deferred_but_names_the_interrupted_sessions(self):
        self.push("0.48.4")
        # gate: 0 (deploy proceeds); rollback-time roster: 2 (the new actd dispatched)
        proc = self.run_script(doctor_plan=["-", "newfail", "newfail", "newfail"], roster=["0", "2"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), self.base_sha, "rolled back")
        self.assertEqual(self.state()["status"], "rolled_back")
        self.assertEqual(self.roster_queries(), ["0", "2"])
        self.assertIn("WARN rollback restarts actd with 2 live background claude session(s)", self.log_text())
        self.assertIn("bootout gui/", " ".join(self.launchctl_calls()), "actd was still booted out")


if __name__ == "__main__":
    unittest.main()
