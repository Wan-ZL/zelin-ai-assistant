"""scripts/auto-deploy.sh 会话闸门——一个 episode 的边界（CONTRACT §56.3 第 4b 步，
review of #284）——真 bash + 真 git。

`deferred_since`（6 h WARN 与顶栏「已 X 小时」的起点）与镜像私账 `roster_unknown_ticks`
（fail-closed 的连续未知计数）只属于**连续 deferred 的那一串运行**。闸门只在上一轮
自己就是 `deferred` 时继承它们；任何别的结果（refused_dirty / fetch_failed / ci_pending /
ci_failed / 一次跳过闸门并回滚了的 --force……）夹在中间都关掉 episode。修前：闸门见键
就继承，而这些出口都不清键——review 用本夹具复现了「一天前的 stamp 被几秒钟前才开始的
延后继承，日志 WARN『已 7 小时』、doctor 行 WARN、顶栏警告色」，以及 `--force`+回滚后
`roster_unknown_ticks=2` 留在 `rolled_back` 之下、下一个 unknown 直接算第 3 次。

夹具复用 tests/integration/test_auto_deploy_script.py 的 ``AutoDeployFixture``（防腐 #7）。

钉住的行为：
  - deferred → refused_dirty（会话此时早已散了、闸门根本没问）→ 再 deferred：新 stamp、
    不 WARN、真读方渲 OK；
  - `--force` 的「忘掉」写入连 deferred_* / roster_unknown_ticks 一起清；随后回滚过、
    main 再前进、roster 再 unknown → 计数从 1 起、不让行；
  - 夹在中间的非闸门出口（ci_pending）同样让陈旧的 ticks 与 stamp 失效；
  - install_incomplete 的修补重跑被延后时 `reason` 保留失配 token（`… sessions_running`）、
    detail 带失配说明，真读方渲成 WARN「install incomplete (…)」而不是 OK；actd 死了
    （heartbeat_missing）同样受闸、token 如实；
  - 6 h 阈值只有一个真源——脚本读 `act.lib.deploy_state.DEFER_WARN_AFTER_S`，
    `AUTODEPLOY_DEFER_WARN_AFTER` 不再是旋钮。
"""
import json
import os
import time
import unittest
from unittest import mock

import tests.integration.test_auto_deploy_script as base
from act.lib import deploy_state

BUDGET_SECONDS = 150  # 13 runs of real bash+git: ~25 s on a 2024 Mac standalone; ~6x margin
_T0 = [0.0]


def setUpModule():
    _T0[0] = time.monotonic()  # clocked from when THIS module runs, not from import


def tearDownModule():
    elapsed = time.monotonic() - _T0[0]
    if elapsed > BUDGET_SECONDS:
        raise AssertionError("tests/integration/test_auto_deploy_defer_episode.py took %.0fs > %ds budget"
                             % (elapsed, BUDGET_SECONDS))


def _iso(seconds_ago):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - seconds_ago))


@unittest.skipIf(base._WIN, "bash + install.sh are POSIX-only")
class DeferEpisodeTestCase(base.AutoDeployFixture):

    def _projection(self):
        return deploy_state.read(self.live / "state" / "deploy_state.json")

    def _backdate_mirror(self, **over):
        """Age the stamp on file (the mirror is the script's own truth)."""
        m = self.mirror()
        m.update(over)
        (self.mirror_dir / "deploy_state.json").write_text(json.dumps(m), encoding="utf-8")

    def test_an_outcome_between_two_deferrals_starts_a_fresh_episode(self):
        self.push("0.48.4")
        self.run_script(doctor_plan=["-", "-"], roster=["1"])
        first = self.state()["deferred_since"]
        # the owner edits a tracked file; the sessions end meanwhile. This run is
        # refused_dirty — the dirty check precedes the gate, so the roster is
        # never asked and nothing clears the deferred_* keys
        readme = self.live / "README.md"
        readme.write_text("owner wip\n", encoding="utf-8")
        proc = self.run_script(doctor_plan=["-", "-"], roster=["0"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.state()["status"], "refused_dirty")
        self.assertEqual(self.roster_queries(), ["1"], "the gate did not run on the dirty tree")
        self.assertEqual(self.mirror()["deferred_since"], first, "the stale keys ARE still on file")
        # a day later (the stamp on file is made 7 h old), tree clean, one NEW session
        self._backdate_mirror(deferred_since=_iso(7 * 3600))
        readme.write_text("fixture\n", encoding="utf-8")
        time.sleep(1.1)  # second resolution: this run's stamp must differ from the first
        proc = self.run_script(doctor_plan=["-", "-"], roster=["1"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        st = self.state()
        self.assertEqual(st["status"], "deferred")
        self.assertEqual(st["deferred_since"], st["last_run"], "a fresh episode: the stamp is this run's")
        self.assertNotEqual(st["deferred_since"], first)
        self.assertEqual(self.head(), self.base_sha)
        log = self.log_text()
        self.assertNotIn("WARN deploy deferred", log, "seconds of waiting are not 7 hours")
        self.assertIn("episode since %s" % st["last_run"], log)
        with mock.patch.dict(os.environ, {"AIASSISTANT_UI_LANG": "en"}):
            row = deploy_state.auto_deploy_row(self._projection())
        self.assertEqual(row["status"], "ok", row)

    def test_force_closes_the_episode_it_overrides(self):
        self.push("0.48.4")
        self.run_script(doctor_plan=["-", "-"], roster=["unknown"])
        self.run_script(doctor_plan=["-", "-"], roster=["unknown"])
        self.assertEqual(self.mirror()["roster_unknown_ticks"], "2")
        self.assertEqual(self.mirror()["deferred_reason"], "roster_unknown")
        # --force skips the gate; the forced deploy is condemned by the doctor and rolls back
        proc = self.run_script("--force", doctor_plan=["-", "newfail", "newfail", "newfail"], roster=["1", "0"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        m = self.mirror()
        self.assertEqual(m["status"], "rolled_back")
        self.assertEqual(self.head(), self.base_sha)
        for key in ("deferred_reason", "deferred_sessions", "deferred_since", "roster_unknown_ticks"):
            self.assertNotIn(key, m, "%s must not survive the --force that skipped the gate" % key)
        self.assertIn("--force: forgetting failed/notified/incomplete shas and any deferral episode",
                      self.log_text())
        # main moves on and the roster is unreadable again: tick 1, not 3 — the
        # streak the --force interrupted is over, the gate does not yield
        self.push("0.48.5")
        proc = self.run_script(doctor_plan=["-", "-"], roster=["unknown"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.mirror()["roster_unknown_ticks"], "1")
        self.assertEqual(self.state()["deferred_reason"], "roster_unknown")
        self.assertEqual(self.head(), self.base_sha, "tick 1 of 3: still fail closed")
        self.assertEqual(len(self.installs()), 2, "the forced install + the rollback reinstall only")

    def test_stale_ticks_and_stamp_under_another_status_are_ignored(self):
        self.push("0.48.4")
        # what a run that exited between the CI gate and the session gate leaves
        # behind after an earlier episode: ci_pending with the old keys still on file
        self.mirror_dir.mkdir(parents=True, exist_ok=True)
        (self.mirror_dir / "deploy_state.json").write_text(json.dumps({
            "status": "ci_pending", "deferred_reason": "roster_unknown", "roster_unknown_ticks": "2",
            "deferred_since": _iso(7 * 3600), "repo": str(self.live)}), encoding="utf-8")
        proc = self.run_script(doctor_plan=["-", "-"], roster=["unknown"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), self.base_sha, "a stale 2 must not make this the yielding 3rd tick")
        st = self.state()
        self.assertEqual(st["status"], "deferred")
        self.assertEqual(self.mirror()["roster_unknown_ticks"], "1")
        self.assertEqual(st["deferred_since"], st["last_run"])
        self.assertIn("(1 so far)", st["detail"])
        self.assertNotIn("WARN deploy deferred", self.log_text())

    def test_deferred_repair_keeps_the_install_incomplete_tokens_and_the_row_stays_warn(self):
        # §56.3 step 2: HEAD == origin/main but the machine runs an older version
        self.seed_running("0.48.2")
        self.sighting()
        mismatch = self.state()["reason"]
        self.assertIn("install_report_version_mismatch", mismatch)
        proc = self.run_script(roster=["1"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.installs(), [])
        st = self.state()
        self.assertEqual(st["status"], "deferred")
        self.assertEqual(st["reason"], mismatch + " sessions_running",
                         "the install_incomplete tokens stay in front of the deferral's own")
        self.assertIn("install incomplete: ", st["detail"])
        self.assertIn("install_report.json", st["detail"])
        with mock.patch.dict(os.environ, {"AIASSISTANT_UI_LANG": "en"}):
            row = deploy_state.auto_deploy_row(self._projection())
        self.assertEqual(row["status"], "warn", "a deferred repair is never the plain OK「update waiting」")
        self.assertTrue(row["detail"].startswith("install incomplete (%s); the repair waits for 1 session(s)"
                                                 % mismatch), row["detail"])
        self.assertIn("--force", row["fix"])
        # actd dead outright (no heartbeat): the gate still holds — the owner's
        # rule has no exception — and the tokens say so
        self.clear_heartbeat()
        proc = self.run_script(roster=["1"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        st = self.state()
        self.assertEqual(st["status"], "deferred")
        self.assertIn("heartbeat_missing", st["reason"])
        self.assertTrue(st["reason"].endswith(" sessions_running"), st["reason"])
        self.assertEqual(self.installs(), [], "no restart under a live session, not even to revive actd")
        with mock.patch.dict(os.environ, {"AIASSISTANT_UI_LANG": "en"}):
            row = deploy_state.auto_deploy_row(self._projection())
        self.assertEqual(row["status"], "warn")
        self.assertIn("heartbeat_missing", row["detail"])
        self.assertIn("with actd down nothing automatic ends them", row["fix"])
        # the sessions are gone: the repair runs and the keys clear
        proc = self.run_script(roster=["0"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(len(self.installs()), 1)
        st = self.state()
        self.assertEqual(st["status"], "deployed")
        self.assertNotIn("deferred_reason", st)
        self.assertNotIn("reason", st)

    def test_the_warn_threshold_has_one_truth_in_deploy_state_py(self):
        self.push("0.48.4")
        # the live checkout's act/lib/deploy_state.py (untracked = not "dirty";
        # the fixture repo has none) says 60 s — what the doctor row would judge by
        fake = self.live / "act" / "lib" / "deploy_state.py"
        fake.write_text("DEFER_WARN_AFTER_S = 60\n", encoding="utf-8")
        since = _iso(120)
        self.mirror_dir.mkdir(parents=True, exist_ok=True)
        (self.mirror_dir / "deploy_state.json").write_text(json.dumps({
            "status": "deferred", "deferred_reason": "sessions_running", "deferred_sessions": "1",
            "deferred_since": since, "repo": str(self.live)}), encoding="utf-8")
        # …and the retired shell knob, set to a week, changes nothing
        proc = self.run_script(doctor_plan=["-", "-"], roster=["1"],
                               env={"AUTODEPLOY_DEFER_WARN_AFTER": "604800"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.state()["deferred_since"], since)
        self.assertIn("WARN deploy deferred for 0 h now (since %s)" % since, self.log_text(),
                      "2 min > the 60 s the python constant says: the log WARNs with the doctor")
        self.assertEqual(self.head(), self.base_sha)


if __name__ == "__main__":
    unittest.main()
