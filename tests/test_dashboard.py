"""dashboard.build_dashboard — v0.10 additions (CONTRACT §2).

build_dashboard is pure/injectable: requirements, agents and cfg are all passed
in, so no ``claude agents`` subprocess and no real registry is touched. $HOME
is pointed at an empty dir per test because the executing/review branch may
consult ``~/.claude/projects`` (via executor._transcript_info) to build the
copy command.

Covered:
- status=approved surfaces as a "queued" item inside running[] — no
  session_id/copy_cmd KEYS (absent, not null), dispatch_error passthrough;
- review[] passes delivered_summary/final_draft through and converts
  review_at/dispatched_at from registry ISO strings to epoch ints;
- attach ≠ 打回（§30）：status=review + roster working -> the item STAYS in
  review[] with session_active=true (user attach / organic activity — a real
  rework verdict flips the card to executing, so it never presents this way);
  roster done / blocked / absent keeps it in review[] with
  session_active=false (counts follow the lists).
"""
import datetime as _dt
import os
import tempfile
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - ensures the sandbox env is set first

from act.lib import config, dashboard
from act.lib.registry import Requirement


def _utc_epoch(*args) -> int:
    return int(_dt.datetime(*args, tzinfo=_dt.timezone.utc).timestamp())


class BuildDashboardV010TestCase(unittest.TestCase):
    def setUp(self):
        self.cfg = config.Config()
        home = tempfile.mkdtemp(prefix="dash-home-")
        patcher = mock.patch.dict(os.environ, {"HOME": home})
        patcher.start()
        self.addCleanup(patcher.stop)

    def _build(self, reqs):
        return dashboard.build_dashboard(reqs=reqs, agents=[], cfg=self.cfg)

    # -- approved -> queued item in running[] ---------------------------------- #
    def test_approved_shows_as_queued_in_running(self):
        req = Requirement.from_dict({
            "id": "R-100",
            "title": "排队中的任务",
            "status": "approved",
            "summary": "先排队等派发",
            "plan": "第一步\n第二步",          # string plan -> split into list
            "definition_of_done": ["能跑", "有测试"],
            "delivery_mode": "chat",
            "execution": {"last_error": "spawn failed: boom"},
        })
        dash = self._build([req])

        self.assertEqual(len(dash["running"]), 1)
        item = dash["running"][0]
        self.assertEqual(item["state"], "queued")
        self.assertEqual(item["id"], "R-100")
        self.assertEqual(item["name"], "排队中的任务")
        self.assertEqual(item["summary"], "先排队等派发")
        self.assertEqual(item["plan"], ["第一步", "第二步"])
        self.assertEqual(item["dod"], ["能跑", "有测试"])
        self.assertEqual(item["delivery_mode"], "chat")
        self.assertEqual(item["dispatch_error"], "spawn failed: boom")
        # queued items have NO session to attach: keys absent, not null (§2)
        self.assertNotIn("session_id", item)
        self.assertNotIn("copy_cmd", item)
        self.assertNotIn("started_at", item)
        # counts.running naturally includes queued items
        self.assertEqual(dash["counts"]["running"], 1)

    def test_approved_without_failure_has_null_dispatch_error(self):
        req = Requirement.from_dict(
            {"id": "R-101", "title": "还没试过派发", "status": "approved"})
        item = self._build([req])["running"][0]
        self.assertEqual(item["state"], "queued")
        self.assertIsNone(item["dispatch_error"])
        self.assertEqual(item["delivery_mode"], "repo")  # missing == repo (§20)

    # -- review[] passthrough + epoch conversion -------------------------------- #
    def test_review_passes_delivery_fields_and_epoch_timestamps(self):
        req = Requirement.from_dict({
            "id": "R-102",
            "title": "待验收任务",
            "status": "review",
            "summary": "任务概述",
            "plan": ["步骤一"],
            "sources": [{"who": "zelin", "channel": "slack",
                         "date": "2026-07-01", "quote": "原话引用"}],
            "delivery_mode": "chat",
            "execution": {
                "session_id": "feedc0de",
                "log": "/tmp/executor-R-102.log",
                "dispatched_at": "2026-07-08T00:00:00Z",
                "review_at": "2026-07-08T01:02:03Z",
                "delivered_summary": "交付了 X，改了三个文件",
                "final_draft": "这里是完整成稿全文",
            },
        })
        dash = self._build([req])

        self.assertEqual(len(dash["review"]), 1)
        item = dash["review"][0]
        self.assertEqual(item["delivered_summary"], "交付了 X，改了三个文件")
        self.assertEqual(item["final_draft"], "这里是完整成稿全文")
        # registry stores ISO strings; the dashboard emits epoch ints (§2)
        self.assertIsInstance(item["review_at"], int)
        self.assertEqual(item["review_at"], _utc_epoch(2026, 7, 8, 1, 2, 3))
        self.assertEqual(item["dispatched_at"], _utc_epoch(2026, 7, 8, 0, 0, 0))
        self.assertEqual(item["plan"], ["步骤一"])
        self.assertEqual(item["log"], "/tmp/executor-R-102.log")
        self.assertEqual(item["delivery_mode"], "chat")
        # sources keep the approval-card shape {who, channel, date, quote}
        self.assertEqual(item["sources"], [{
            "who": "zelin", "channel": "slack",
            "date": "2026-07-01", "quote": "原话引用",
        }])

    def test_review_missing_new_fields_stay_none_not_crash(self):
        req = Requirement.from_dict({
            "id": "R-103", "title": "老格式任务", "status": "review",
            "execution": {"session_id": "feedc0de"},
        })
        item = self._build([req])["review"][0]
        self.assertIsNone(item["delivered_summary"])
        self.assertIsNone(item["final_draft"])
        self.assertIsNone(item["review_at"])
        self.assertIsNone(item["dispatched_at"])

    def test_epoch_helper_unparsable_returns_none(self):
        self.assertIsNone(dashboard._epoch(None))
        self.assertIsNone(dashboard._epoch("not-a-date"))
        self.assertEqual(dashboard._epoch("2026-07-08T00:00:00Z"),
                         _utc_epoch(2026, 7, 8))

    # -- review + attach 活动 -> stays in review[] with session_active (§30) --- #
    def _review_req(self, rid="R-200"):
        return Requirement.from_dict({
            "id": rid,
            "title": "被 attach 回去聊天的任务",
            "status": "review",
            "summary": "任务概述",
            "plan": ["步骤一"],
            "definition_of_done": ["能跑"],
            "delivery_mode": "chat",
            "execution": {
                "session_id": "feedc0de",
                "log": "/tmp/executor-R-200.log",
                "dispatched_at": "2026-07-08T00:00:00Z",
                "review_at": "2026-07-08T01:00:00Z",
            },
        })

    def _agent(self, state, pid=4242):
        a = {
            "id": "feedc0de",
            "sessionId": "feedc0de-0000-0000-0000-000000000000",
            "state": state,
            "cwd": "/tmp/worktree",
            "name": "agent-list-name",
        }
        if pid is not None:
            a["pid"] = pid
        return a

    def test_review_with_working_agent_stays_in_review_session_active(self):
        # §30 attach ≠ 打回：working agent on a review card = user attach /
        # organic activity；卡留在待验收列，只标 session_active，绝不冒充返工。
        dash = dashboard.build_dashboard(
            reqs=[self._review_req()], agents=[self._agent("working")], cfg=self.cfg)

        self.assertEqual(dash["running"], [])
        self.assertEqual(len(dash["review"]), 1)
        item = dash["review"][0]
        self.assertEqual(item["state"], "review")
        self.assertTrue(item["session_active"])
        self.assertEqual(item["id"], "R-200")
        self.assertEqual(item["name"], "被 attach 回去聊天的任务")
        # 常规 review 字段照常：roster 数据 + attach 命令（pid 在场 -> attach）
        self.assertEqual(item["short_id"], "feedc0de")
        self.assertEqual(item["session_id"],
                         "feedc0de-0000-0000-0000-000000000000")
        self.assertEqual(item["copy_cmd"], "claude attach feedc0de")
        self.assertEqual(item["agent_name"], "agent-list-name")
        self.assertEqual(item["cwd"], "/tmp/worktree")
        self.assertEqual(item["plan"], ["步骤一"])
        self.assertEqual(item["dod"], ["能跑"])
        self.assertEqual(item["log"], "/tmp/executor-R-200.log")
        self.assertEqual(item["dispatched_at"], _utc_epoch(2026, 7, 8, 0, 0, 0))
        self.assertEqual(item["review_at"], _utc_epoch(2026, 7, 8, 1, 0, 0))
        self.assertEqual(item["delivery_mode"], "chat")
        # counts 跟着列表走：review 1，running 0
        self.assertEqual(dash["counts"]["review"], 1)
        self.assertEqual(dash["counts"]["running"], 0)

    def test_executing_after_rework_verdict_projects_as_working(self):
        # §30 真返工轮不变：打回 verdict（executor.rework）写 rework_count/
        # last_rework_at 并同步回 executing —— 照常走 running[]，state="working"。
        req = Requirement.from_dict({
            "id": "R-201",
            "title": "被真打回的任务",
            "status": "executing",
            "execution": {
                "session_id": "feedc0de",
                "rework_count": 1,
                "last_rework_at": "2026-07-08T02:00:00Z",
            },
        })
        dash = dashboard.build_dashboard(
            reqs=[req], agents=[self._agent("working")], cfg=self.cfg)

        self.assertEqual(dash["review"], [])
        self.assertEqual(len(dash["running"]), 1)
        self.assertEqual(dash["running"][0]["state"], "working")

    def test_review_with_done_agent_stays_in_review(self):
        dash = dashboard.build_dashboard(
            reqs=[self._review_req()], agents=[self._agent("done", pid=None)],
            cfg=self.cfg)
        self.assertEqual(dash["running"], [])
        self.assertEqual(len(dash["review"]), 1)
        self.assertEqual(dash["review"][0]["state"], "review")
        self.assertFalse(dash["review"][0]["session_active"])
        self.assertEqual(dash["counts"]["running"], 0)
        self.assertEqual(dash["counts"]["review"], 1)

    def test_review_with_absent_agent_stays_in_review(self):
        dash = dashboard.build_dashboard(
            reqs=[self._review_req()], agents=[], cfg=self.cfg)
        self.assertEqual(dash["running"], [])
        self.assertEqual(len(dash["review"]), 1)
        self.assertEqual(dash["review"][0]["state"], "review")
        self.assertFalse(dash["review"][0]["session_active"])

    def test_review_with_blocked_agent_stays_in_review(self):
        # blocked = 会话中途等用户输入，还没收工 —— 照旧留在待验收列
        dash = dashboard.build_dashboard(
            reqs=[self._review_req()], agents=[self._agent("blocked")], cfg=self.cfg)
        self.assertEqual(dash["running"], [])
        self.assertEqual(len(dash["review"]), 1)
        self.assertFalse(dash["review"][0]["session_active"])


class CompletedCapTestCase(unittest.TestCase):
    """§2 completed cap: newest COMPLETED_CAP by accepted_at, true total in counts."""

    def setUp(self):
        self.cfg = config.Config()
        home = tempfile.mkdtemp(prefix="dash-home-")
        patcher = mock.patch.dict(os.environ, {"HOME": home})
        patcher.start()
        self.addCleanup(patcher.stop)

    def _delivered(self, i, accepted_at):
        execution = {"session_id": f"sid-{i}"}
        if accepted_at is not None:
            execution["accepted_at"] = accepted_at
        return Requirement.from_dict({
            "id": f"R-{300 + i}",
            "title": f"已交付 {i}",
            "status": "delivered",
            "execution": execution,
        })

    @staticmethod
    def _iso(i):
        base = _dt.datetime(2026, 1, 1, tzinfo=_dt.timezone.utc)
        return (base + _dt.timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M:%SZ")

    def test_over_cap_truncates_newest_first_but_counts_stay_total(self):
        n = dashboard.COMPLETED_CAP + 10
        reqs = [self._delivered(i, self._iso(i)) for i in range(n)]
        dash = dashboard.build_dashboard(reqs=reqs, agents=[], cfg=self.cfg)

        self.assertEqual(len(dash["completed"]), dashboard.COMPLETED_CAP)
        # counts.completed = TRUE total (may exceed len(completed), §2 v0.11)
        self.assertEqual(dash["counts"]["completed"], n)
        # newest accepted_at first; the 10 oldest fell off the end
        ids = [item["id"] for item in dash["completed"]]
        self.assertEqual(ids[0], f"R-{300 + n - 1}")
        self.assertEqual(ids[-1], "R-310")
        self.assertNotIn("R-300", ids)
        accepted = [item["accepted_at"] for item in dash["completed"]]
        self.assertEqual(accepted, sorted(accepted, reverse=True))

    def test_under_cap_keeps_all_missing_accepted_at_sinks_last(self):
        reqs = [
            self._delivered(0, self._iso(0)),
            self._delivered(1, None),          # legacy item without accepted_at
            self._delivered(2, self._iso(2)),
        ]
        dash = dashboard.build_dashboard(reqs=reqs, agents=[], cfg=self.cfg)

        self.assertEqual(len(dash["completed"]), 3)
        self.assertEqual(dash["counts"]["completed"], 3)
        self.assertEqual([item["id"] for item in dash["completed"]],
                         ["R-302", "R-300", "R-301"])
        self.assertIsNone(dash["completed"][-1]["accepted_at"])


class EmptySidNoGlobBindTestCase(unittest.TestCase):
    """例4a regression: a card with NO session id must never glob-bind an
    unrelated transcript.

    Root cause (production, 2026-07): executor._transcript_info("") built
    short="" and glob("*/*.jsonl") matched EVERY transcript, returning the
    alphabetically-first one — on Zelin's disk an Obsidian-ingest session —
    so completed cards without a session_id got a bogus
    ``cd '/Users/zelin/Documents/Obsidian Vault' && claude --resume 008b…``
    copy_cmd. Now: _transcript_info rejects empty/too-short sids and the
    dashboard emits copy_cmd=None instead of guessing.
    """

    def setUp(self):
        self.cfg = config.Config()
        self.home = tempfile.mkdtemp(prefix="dash-home-")
        patcher = mock.patch.dict(os.environ, {"HOME": self.home})
        patcher.start()
        self.addCleanup(patcher.stop)
        # plant an unrelated transcript that the old glob("*/*.jsonl") matched
        proj = os.path.join(self.home, ".claude", "projects",
                            "-Users-zelin-Documents-Obsidian-Vault")
        os.makedirs(proj)
        self.decoy_sid = "008b4c28-0000-0000-0000-000000000000"
        with open(os.path.join(proj, f"{self.decoy_sid}.jsonl"), "w",
                  encoding="utf-8") as fh:
            fh.write('{"type": "user", "cwd": '
                     '"/Users/zelin/Documents/Obsidian Vault"}\n')

    def test_transcript_info_rejects_empty_and_short_sids(self):
        from act import executor
        for bad in ("", None, "ab", "sid", "feedc0d"):  # < 8-hex short segment
            self.assertIsNone(executor._transcript_info(bad),
                              f"sid {bad!r} must not bind a transcript")
        # a real short id still resolves against the planted transcript
        info = executor._transcript_info("008b4c28")
        self.assertIsNotNone(info)
        self.assertEqual(info[0], self.decoy_sid)

    def test_delivered_card_without_session_id_gets_no_copy_cmd(self):
        req = Requirement.from_dict({
            "id": "R-400", "title": "没有 session 的已交付卡",
            "status": "delivered", "execution": {},
        })
        dash = dashboard.build_dashboard(reqs=[req], agents=[], cfg=self.cfg)
        item = dash["completed"][0]
        self.assertIsNone(item["copy_cmd"])

    def test_review_card_without_session_id_gets_no_copy_cmd(self):
        req = Requirement.from_dict({
            "id": "R-401", "title": "没有 session 的待验收卡",
            "status": "review", "execution": {},
        })
        dash = dashboard.build_dashboard(reqs=[req], agents=[], cfg=self.cfg)
        item = dash["review"][0]
        self.assertIsNone(item["copy_cmd"])

    def test_card_with_real_session_id_still_gets_resume_cmd(self):
        req = Requirement.from_dict({
            "id": "R-402", "title": "有 session 的已交付卡",
            "status": "delivered",
            "execution": {"session_id": self.decoy_sid},
        })
        dash = dashboard.build_dashboard(reqs=[req], agents=[], cfg=self.cfg)
        item = dash["completed"][0]
        self.assertEqual(
            item["copy_cmd"],
            "cd '/Users/zelin/Documents/Obsidian Vault' "
            f"&& claude --resume {self.decoy_sid}")


if __name__ == "__main__":
    unittest.main()
