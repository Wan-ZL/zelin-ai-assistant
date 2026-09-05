"""§70 循环运行：一天一次、先维护再提案、阶段失败隔离、审计行 + 投影键 maintenance；
D33：doctor 红灯等自检类信号成 advisory 行（last_result.advisories，带 first_seen），
不铸卡；owner 的 issue 照旧铸卡。

假时钟 + 假 gh + 假 doctor + fixture registry；actd.run_once 的挂点用 mock 钉住。
Runs entirely inside the sandbox AIASSISTANT_HOME (tests/__init__.py)。
"""
import datetime as _dt
import json
import os
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env before act imports

from act.lib import config, daily_loop, dashboard, heartbeat, maintenance, registry
from act.lib.registry import Requirement, State

TZ = _dt.timezone(_dt.timedelta(hours=-7))
NOW = _dt.datetime(2026, 9, 2, 3, 31, tzinfo=TZ)


def _gh_none(args):
    return None


def _gh_owner_issue(args):
    """一张 owner 开的 issue（CARD_KINDS 里唯一不需要素材台账就能铸的卡）；其余 gh 调用不可用。"""
    if args[:2] == ["issue", "list"] and "--search" not in args:
        return json.dumps([{"number": 5, "title": "make the loop quieter please", "body": "b",
                            "author": {"login": "Wan-ZL"}, "url": "https://github.com/o/r/issues/5", "labels": []}])
    return None


def _doctor_fail():
    return json.dumps([{"name": "launchd claude", "status": "FAIL", "detail": "TCC", "fix": "grant",
                        "failure_id": "claude_blind", "row_class": "owner_action"}])


def _card(rid, title, status=State.DETECTED.value, age=60, **kw):
    date = (NOW.date() - _dt.timedelta(days=age)).isoformat()
    fields = dict(id=rid, title=title, status=status,
                  sources=[{"channel": "meeting", "date": date, "quote": "q"}])
    fields.update(kw)
    req = Requirement(**fields)
    registry.save(req)
    return req


class _Sandbox(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()
        daily_loop.state_path().unlink(missing_ok=True)
        daily_loop.log_path().unlink(missing_ok=True)
        from act.lib import loop_inputs
        loop_inputs.materials_path().unlink(missing_ok=True)
        self.cfg = config.Config()


class TickTestCase(_Sandbox):
    def setUp(self):
        super().setUp()
        env = mock.patch.dict(os.environ, {daily_loop.DISABLE_ENV: "1"})   # 套件默认 "0"（tests/__init__）
        env.start()
        self.addCleanup(env.stop)

    def test_process_switch_off_short_circuits_before_any_io(self):
        with mock.patch.dict(os.environ, {daily_loop.DISABLE_ENV: "0"}), \
                mock.patch.object(daily_loop, "load_state") as load_state:
            self.assertIsNone(daily_loop.tick(self.cfg, now=NOW))
            load_state.assert_not_called()

    def test_runs_once_per_day_after_the_unlock_time(self):
        with mock.patch.object(daily_loop, "run", return_value={"ok": 1}) as run:
            self.assertIsNone(daily_loop.tick(self.cfg, now=NOW.replace(hour=3, minute=29)))
            self.assertEqual(daily_loop.tick(self.cfg, now=NOW), {"ok": 1})
        # run() is mocked → it did not write last_run_day; simulate the real write
        daily_loop._write_state({"last_run_day": NOW.date().isoformat(), "phase": "idle"})
        with mock.patch.object(daily_loop, "run") as run:
            self.assertIsNone(daily_loop.tick(self.cfg, now=NOW.replace(hour=23)))
            run.assert_not_called()
            self.assertIsNotNone(daily_loop.tick(self.cfg, now=NOW + _dt.timedelta(days=1)))

    def test_disabled_never_runs_and_tick_never_raises(self):
        self.cfg.daily_loop_enabled = False
        with mock.patch.object(daily_loop, "run") as run:
            self.assertIsNone(daily_loop.tick(self.cfg, now=NOW))
            run.assert_not_called()
        self.cfg.daily_loop_enabled = True
        with mock.patch.object(daily_loop, "due", side_effect=RuntimeError("boom")):
            self.assertIsNone(daily_loop.tick(self.cfg, now=NOW))


class RunTestCase(_Sandbox):
    def test_full_run_maintains_then_proposes_and_records(self):
        _card("P-1", "duplicated topic about invoices this month")
        _card("P-2", "duplicated topic about invoices this month", status=State.CARD_SENT.value)
        _card("P-3", "an idle backlog card nobody touched", age=70)
        _card("P-4", "fresh card stays", age=1)
        result = daily_loop.run(self.cfg, now=NOW, gh=_gh_owner_issue, doctor=_doctor_fail)
        self.assertEqual((result["merged"], result["trashed"], result["proposals"]), (1, 1, 1))
        self.assertEqual(result["errors"], [])
        reqs = {r.id: r for r in registry.load_all()}
        self.assertEqual(reqs["P-3"].trash_reason, "stale:idle")
        merged = [r for r in reqs.values() if r.merged_from]
        self.assertEqual(len(merged), 1)
        proposals = [r for r in reqs.values() if r.title.startswith("🤖 ")]
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0].sources[0]["ref"], "self_improve:issue:5")   # the owner issue
        # D33: the doctor FAIL became an advisory row, not a card
        self.assertEqual([a["kind"] for a in result["advisories"]], ["doctor_fail"])
        self.assertEqual(result["advisories"][0]["fingerprint"], "doctor_fail:launchd claude")
        self.assertEqual(result["advisories"][0]["first_seen"], "2026-09-02")
        self.assertEqual(result["advisories"][0]["ref"], "claude_blind")
        # state + projection
        state = daily_loop.load_state()
        self.assertEqual(state["phase"], "idle")
        self.assertEqual(state["last_run_day"], "2026-09-02")
        self.assertEqual(state["last_result"]["merged"], 1)
        self.assertEqual(state["last_result"]["advisories"], result["advisories"])
        self.assertIn("issue:5", state["fingerprints"])
        self.assertNotIn("doctor_fail:launchd claude", state["fingerprints"])
        # audit line
        lines = daily_loop.log_path().read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        entry = json.loads(lines[0])
        self.assertEqual(entry["day"], "2026-09-02")
        self.assertEqual([m["from"] for m in entry["merges"]], [["P-1", "P-2"]])
        self.assertEqual(entry["trashed"][0]["rule"], "idle")
        self.assertEqual(entry["inputs"]["doctor"], 1)          # advisory kinds still count as read
        self.assertEqual(entry["inputs"]["issues"], 1)
        self.assertEqual(entry["inputs"]["prs"], 0)             # gh unavailable → 0 signals, no crash
        self.assertEqual(entry["advisories"], result["advisories"])
        self.assertIn("gh_title", entry["skipped"])
        self.assertIn("advisory", entry["skipped"])

    def test_advisory_first_seen_survives_to_the_next_day(self):
        daily_loop.run(self.cfg, now=NOW, gh=_gh_none, doctor=_doctor_fail)
        again = daily_loop.run(self.cfg, now=NOW + _dt.timedelta(days=1), gh=_gh_none, doctor=_doctor_fail)
        self.assertEqual(again["proposals"], 0)
        self.assertEqual(again["advisories"][0]["first_seen"], "2026-09-02")      # inherited
        self.assertEqual(len([r for r in registry.load_all() if r.title.startswith("🤖 ")]), 0)
        # gone for a day → the row drops out; back the day after → a fresh first_seen
        daily_loop.run(self.cfg, now=NOW + _dt.timedelta(days=2), gh=_gh_none, doctor=lambda: "[]")
        back = daily_loop.run(self.cfg, now=NOW + _dt.timedelta(days=3), gh=_gh_none, doctor=_doctor_fail)
        self.assertEqual(back["advisories"][0]["first_seen"], "2026-09-05")

    def test_material_gets_proposed_and_the_ledger_is_written_back(self):
        from act.lib import loop_inputs, materials
        path = loop_inputs.materials_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.unlink(missing_ok=True)
        item = materials.add(path, url="https://example.com/talk", note="borrow the dedup idea")
        with mock.patch.object(materials, "fetch", return_value={"url": item["url"], "title": "Talk", "text": "t", "error": None}):
            result = daily_loop.run(self.cfg, now=NOW, gh=_gh_none, doctor=lambda: "[]")
        self.assertEqual(result["proposals"], 1)
        card = registry.load(result["filed"][0]["id"])
        self.assertEqual(card.sources[0]["ref"], f"self_improve:material:{item['id']}")
        rec = materials.get(path, item["id"])
        self.assertEqual(rec["status"], "proposal_created")
        self.assertEqual(rec["links"]["proposal_id"], card.id)
        entry = json.loads(daily_loop.log_path().read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual(entry["inputs"]["materials"], 1)
        path.unlink(missing_ok=True)

    def test_second_run_same_day_does_not_repropose(self):
        daily_loop.run(self.cfg, now=NOW, gh=_gh_owner_issue, doctor=_doctor_fail)
        again = daily_loop.run(self.cfg, now=NOW + _dt.timedelta(hours=1), gh=_gh_owner_issue, doctor=_doctor_fail)
        self.assertEqual(again["proposals"], 0)
        self.assertEqual(len([r for r in registry.load_all() if r.title.startswith("🤖 ")]), 1)

    def test_cap_counts_cards_already_minted_today(self):
        self.cfg.daily_loop_max_proposals_per_day = 1
        _card("P-9", "🤖 earlier proposal today", status=State.CARD_SENT.value, age=0,
              sources=[{"channel": "self_improve", "date": NOW.date().isoformat(), "ref": "self_improve:x:y"}])
        result = daily_loop.run(self.cfg, now=NOW, gh=_gh_owner_issue, doctor=_doctor_fail)
        self.assertEqual(result["proposals"], 0)

    def test_phase_failures_are_isolated_and_the_pass_survives(self):
        _card("P-3", "an idle backlog card nobody touched", age=70)
        with mock.patch.object(maintenance, "dedup_lanes", side_effect=RuntimeError("dedup boom")), \
                mock.patch.object(daily_loop, "collect_signals", side_effect=ValueError("gh boom")):
            result = daily_loop.run(self.cfg, now=NOW, gh=_gh_none, doctor=_doctor_fail)
        self.assertEqual(result["trashed"], 1)                       # the middle phase still ran
        self.assertEqual(len(result["errors"]), 2)
        self.assertTrue(result["errors"][0].startswith("dedup: RuntimeError"))
        self.assertEqual(daily_loop.load_state()["phase"], "idle")   # never stuck in a phase
        self.assertEqual(daily_loop.load_state()["last_run_day"], "2026-09-02")

    def test_heartbeat_phases_are_beaten(self):
        with mock.patch.object(heartbeat, "beat") as beat:
            daily_loop.run(self.cfg, now=NOW, gh=_gh_none, doctor=lambda: "[]", interval=10)
        phases = [c.args[0] for c in beat.call_args_list]
        for p in ("daily_loop:dedup", "daily_loop:stale_sweep", "daily_loop:proposals", "daily_loop:idle"):
            self.assertIn(p, phases)
        self.assertTrue(all(c.args[1] == 10 for c in beat.call_args_list))

    def test_audit_log_is_capped(self):
        big = {"ts": "x", "pad": "y" * 600_000}
        daily_loop._append_log(big)
        daily_loop._append_log(big)
        self.assertLessEqual(daily_loop.log_path().stat().st_size, daily_loop.LOG_MAX_BYTES)


class ProjectionTestCase(_Sandbox):
    def test_attach_is_absent_until_the_first_run_then_epoch_ints(self):
        dash = daily_loop.attach({}, self.cfg)
        self.assertNotIn("maintenance", dash)
        daily_loop.run(self.cfg, now=NOW, gh=_gh_none, doctor=lambda: "[]")
        dash = daily_loop.attach({}, self.cfg)
        m = dash["maintenance"]
        self.assertEqual(m["phase"], "idle")
        self.assertIsInstance(m["last_run_at"], int)
        self.assertIsInstance(m["started_at"], int)
        self.assertIsInstance(m["next_run_at"], int)
        self.assertGreater(m["next_run_at"], m["last_run_at"])
        self.assertEqual(set(m["last_result"]), {"merged", "trashed", "proposals", "summaries", "errors",
                                                 "advisories"})
        self.assertEqual(m["last_result"]["errors"], 0)
        self.assertEqual(m["last_result"]["advisories"], [])

    def test_projection_carries_advisories_verbatim(self):
        daily_loop.run(self.cfg, now=NOW, gh=_gh_none, doctor=_doctor_fail)
        m = daily_loop.attach({}, self.cfg)["maintenance"]
        self.assertEqual(len(m["last_result"]["advisories"]), 1)
        row = m["last_result"]["advisories"][0]
        self.assertEqual(set(row), set(daily_loop.ADVISORY_WIRE_KEYS))
        self.assertEqual((row["kind"], row["ref"], row["first_seen"]), ("doctor_fail", "claude_blind", "2026-09-02"))
        self.assertTrue(row["text"].startswith("doctor 红灯：launchd claude"))
        self.assertTrue(all(isinstance(v, str) for v in row.values()))

    def test_dashboard_carries_the_key_when_state_exists(self):
        daily_loop._write_state({"phase": "dedup", "started_at": "2026-09-02T10:31:00Z"})
        with mock.patch.object(dashboard, "_run_claude_agents", return_value=[]):
            dash = dashboard.build_dashboard(cfg=self.cfg)
        self.assertEqual(dash["maintenance"]["phase"], "dedup")
        self.assertEqual(dash["maintenance"]["last_run_at"], None)

    def test_projection_tolerates_garbage_state(self):
        m = daily_loop.projection({"phase": 7, "started_at": "nope", "last_result": "bad"}, self.cfg, NOW)
        self.assertEqual(m["phase"], "7")
        self.assertIsNone(m["started_at"])
        self.assertEqual(m["last_result"]["merged"], 0)
        self.assertEqual(m["last_result"]["advisories"], [])
        m = daily_loop.projection({"last_result": {"advisories": ["x", {"kind": 3}, {"text": None}]}}, self.cfg, NOW)
        self.assertEqual(m["last_result"]["advisories"],
                         [{"kind": "3", "text": "", "ref": "", "fingerprint": "", "first_seen": ""},
                          {"kind": "", "text": "", "ref": "", "fingerprint": "", "first_seen": ""}])


class PlanCliTestCase(_Sandbox):
    """`python3 -m act.lib.daily_loop --plan/--status`：只读报告，零写入。"""

    def test_plan_reports_without_writing(self):
        _card("P-1", "duplicated topic about invoices this month")
        _card("P-2", "duplicated topic about invoices this month", status=State.CARD_SENT.value)
        _card("P-3", "an idle backlog card nobody touched", age=70)
        before = sorted(p.name for p in config.REGISTRY_DIR.glob("*.yaml"))
        report = daily_loop.plan(self.cfg, now=NOW, gh=_gh_none)
        self.assertEqual(report["clusters"], [["P-1", "P-2"]])
        self.assertEqual(report["stale"], [{"id": "P-3", "rule": "idle"}])
        self.assertTrue(report["due"])
        self.assertIn("issues", report["inputs"])
        self.assertIn("advisories", report)
        self.assertEqual(sorted(p.name for p in config.REGISTRY_DIR.glob("*.yaml")), before)
        self.assertEqual(registry.load("P-3").status, State.DETECTED.value)
        self.assertFalse(daily_loop.state_path().exists())

    def test_main_prints_json_for_plan_and_status(self):
        import io
        from contextlib import redirect_stdout
        with mock.patch.object(daily_loop.loop_inputs, "default_gh", _gh_none):
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(daily_loop.main(["--plan"]), 0)
            self.assertIn("clusters", json.loads(out.getvalue()))
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(daily_loop.main(["--status"]), 0)
            self.assertEqual(json.loads(out.getvalue())["phase"], "idle")


class DefaultRunnersTestCase(unittest.TestCase):
    """默认 gh / doctor runner：子进程失败或缺席 = None（该输入不可用），绝不 raise。"""

    def test_gh_missing_or_failing_is_none(self):
        from act.lib import loop_inputs
        with mock.patch.object(loop_inputs.subprocess, "run", side_effect=FileNotFoundError("gh")):
            self.assertIsNone(loop_inputs.default_gh(["issue", "list"]))
        failed = mock.Mock(returncode=1, stdout="", stderr="auth")
        with mock.patch.object(loop_inputs.subprocess, "run", return_value=failed):
            self.assertIsNone(loop_inputs.default_gh(["issue", "list"]))
        ok = mock.Mock(returncode=0, stdout="[]", stderr="")
        with mock.patch.object(loop_inputs.subprocess, "run", return_value=ok) as run:
            self.assertEqual(loop_inputs.default_gh(["issue", "list"]), "[]")
        self.assertEqual(run.call_args.args[0][:2], ["gh", "issue"])
        self.assertEqual(run.call_args.kwargs["timeout"], loop_inputs.GH_TIMEOUT_S)

    def test_doctor_runner_returns_stdout_or_none(self):
        from act.lib import loop_inputs
        with mock.patch.object(loop_inputs.subprocess, "run", side_effect=OSError("no python")):
            self.assertIsNone(loop_inputs.default_doctor_runner())
        with mock.patch.object(loop_inputs.subprocess, "run", return_value=mock.Mock(stdout="[]")) as run:
            self.assertEqual(loop_inputs.default_doctor_runner(), "[]")
        self.assertEqual(run.call_args.args[0][1:], ["-m", "act.doctor", "--fast", "--json"])

    def test_launchd_log_dir_honours_the_sandbox_env(self):
        from act.lib import loop_inputs
        self.assertEqual(str(loop_inputs.launchd_log_dir()), os.environ["ZAI_LAUNCHD_LOG_DIR"])
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(loop_inputs.launchd_log_dir(), loop_inputs.LAUNCHD_LOG_DIR)


class ActdWiringTestCase(_Sandbox):
    def test_run_once_ticks_the_loop_with_the_pass_interval(self):
        from act import actd
        # 同 test_actd_heartbeat：housekeeping 的其它住户全部 mock 掉——尤其
        # update_check（真 GitHub releases 请求）与 auto_merge/feedback（子进程）。
        with mock.patch.object(actd.daily_loop, "tick") as tick, \
                mock.patch.object(actd, "process_inbox", return_value=0), \
                mock.patch.object(actd, "auto_dispatch_pass", return_value=0), \
                mock.patch.object(actd, "build_dashboard", return_value={"counts": {}}), \
                mock.patch.object(actd, "write_dashboard"), \
                mock.patch.object(actd, "reconcile_executing", return_value=0), \
                mock.patch.object(actd, "process_raising", return_value=0), \
                mock.patch.object(actd, "dispatch_approved", return_value=0), \
                mock.patch.object(actd, "purge_trash"), \
                mock.patch.object(actd, "archive_stale"), \
                mock.patch.object(actd, "cleanup_merge_jobs"), \
                mock.patch.object(actd, "auto_merge", None), \
                mock.patch.object(actd, "feedback", None), \
                mock.patch.object(actd, "update_check", None), \
                mock.patch.object(actd, "detect_transitions", return_value=[]), \
                mock.patch.object(actd, "_check_auth_failures", return_value=[]), \
                mock.patch.object(actd, "_check_radar_liveness", return_value=[]):
            actd.run_once(self.cfg, None, set(), set(), set(), interval=10)
        tick.assert_called_once_with(self.cfg, interval=10)


if __name__ == "__main__":
    unittest.main()
