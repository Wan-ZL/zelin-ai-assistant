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
- attach 回流（§30, v0.28.1）：status=review + roster WORKING -> the card
  reroutes to running[] with from_review=true while the session actively runs
  (real work resumed — not stranded in 待验收 behind a 0-count 运行中 lane);
  it is a presentation-only reroute (on-disk status stays review). roster
  done / blocked / absent keeps it in review[] with session_active=false, so
  it falls back the moment the session settles (counts follow the lists).
"""
import datetime as _dt
import json
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

    def test_review_with_working_agent_routes_to_running(self):
        # v0.28.1 §30 fix: a review card whose session is actively WORKING again
        # (user attach + real work) shows in 运行中 while it runs — not stranded
        # in 待验收 with a 0-count running lane. Registry status is untouched
        # (still review on disk); this is a presentation-only reroute that falls
        # back to review the moment the session settles (see the done/absent/
        # blocked tests below, which stay in review).
        dash = dashboard.build_dashboard(
            reqs=[self._review_req()], agents=[self._agent("working")], cfg=self.cfg)

        self.assertEqual(dash["review"], [])
        self.assertEqual(len(dash["running"]), 1)
        item = dash["running"][0]
        self.assertEqual(item["state"], "working")
        self.assertTrue(item["from_review"])
        self.assertEqual(item["id"], "R-200")
        self.assertEqual(item["name"], "被 attach 回去聊天的任务")
        # roster data + attach command still present (pid in roster -> attach)
        self.assertEqual(item["short_id"], "feedc0de")
        self.assertEqual(item["session_id"],
                         "feedc0de-0000-0000-0000-000000000000")
        self.assertEqual(item["copy_cmd"], "claude attach feedc0de")
        self.assertEqual(item["agent_name"], "agent-list-name")
        self.assertEqual(item["cwd"], "/tmp/worktree")
        self.assertEqual(item["plan"], ["步骤一"])
        self.assertEqual(item["dod"], ["能跑"])
        self.assertEqual(item["delivery_mode"], "chat")
        # counts follow the lists: running 1, review 0
        self.assertEqual(dash["counts"]["review"], 0)
        self.assertEqual(dash["counts"]["running"], 1)

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


class SingleCardCorruptionIsolationTestCase(unittest.TestCase):
    """单点损坏不倒全局（nightly audit batch）：一张手改坏的卡只降级/跳过，
    绝不冻结整个 dashboard pass；wire 类型归一保证 Swift 端的硬 String/Int
    decode 不会因单张卡把整列清空（CONTRACT §2）。"""

    def setUp(self):
        self.cfg = config.Config()
        home = tempfile.mkdtemp(prefix="dash-home-")
        patcher = mock.patch.dict(os.environ, {"HOME": home})
        patcher.start()
        self.addCleanup(patcher.stop)

    def _build(self, reqs, agents=None, **kw):
        return dashboard.build_dashboard(
            reqs=reqs, agents=agents or [], cfg=self.cfg, **kw)

    # -- 字段级损坏降级（不丢卡） -------------------------------------------- #
    def test_execution_string_on_card_sent_degrades_not_crash(self):
        req = Requirement.from_dict({
            "id": "R-101", "title": "bad exec", "status": "card_sent",
            "execution": "oops-a-string",
        })
        dash = self._build([req])
        item = dash["needs_approval"][0]
        self.assertEqual(item["id"], "R-101")
        self.assertFalse(item["reraised"])

    def test_bad_repeated_mentions_degrades_to_one(self):
        req = Requirement.from_dict({
            "id": "R-102", "title": "bad repeats", "status": "card_sent",
            "repeated_mentions": "abc",
        })
        dash = self._build([req])
        self.assertEqual(dash["needs_approval"][0]["repeated"], 1)

    def test_bad_cost_estimate_degrades_to_no_cost(self):
        req = Requirement.from_dict({
            "id": "R-103", "title": "bad cost", "status": "card_sent",
            "cost_estimate_usd": "cheap",
        })
        dash = self._build([req])
        item = dash["needs_approval"][0]
        self.assertIsNone(item["cost_usd"])
        self.assertFalse(item["show_cost"])

    # -- 整卡不可投影 -> 跳过这一张，兄弟卡与 counts 保持一致 ----------------- #
    def test_unprojectable_card_skipped_siblings_survive(self):
        bad = Requirement.from_dict({
            "id": "R-110", "title": "broken dod", "status": "card_sent",
            "definition_of_done": 42,           # list(42) -> TypeError
        })
        good = Requirement.from_dict({
            "id": "R-111", "title": "healthy sibling", "status": "card_sent",
        })
        dash = self._build([bad, good])
        self.assertEqual([i["id"] for i in dash["needs_approval"]], ["R-111"])
        # 徽章与列表一致：跳过的卡也不计数（诚实降级，不出现"有数没卡"）
        self.assertEqual(dash["counts"]["needs_approval"], 1)

    # -- wire 类型归一：int id/title/tier -> str ------------------------------ #
    def test_int_id_title_tier_emit_as_strings(self):
        req = Requirement.from_dict({
            "id": 300, "title": 456, "tier": 7, "status": "card_sent",
        })
        dash = self._build([req])
        item = dash["needs_approval"][0]
        self.assertEqual(item["id"], "300")
        self.assertEqual(item["title"], "456")
        self.assertEqual(item["tier"], "7")

    # -- started_at 归一 epoch int（§2；Swift 端 started_at: Int?） ------------ #
    def test_started_at_iso_string_normalized_to_epoch(self):
        req = Requirement.from_dict({
            "id": "R-005", "title": "run", "status": "executing",
            "execution": {"session_id": "aaaaaaaa-1111"},
        })
        agent = {"id": "aaaaaaaa", "sessionId": "aaaaaaaa-1111",
                 "state": "working", "pid": 1,
                 "startedAt": "2026-07-08T10:00:05Z"}
        dash = self._build([req], agents=[agent])
        self.assertEqual(dash["running"][0]["started_at"],
                         _utc_epoch(2026, 7, 8, 10, 0, 5))

    def test_started_at_already_epoch_passes_through(self):
        req = Requirement.from_dict({
            "id": "R-006", "title": "run2", "status": "executing",
            "execution": {"session_id": "bbbbbbbb-2222"},
        })
        agent = {"id": "bbbbbbbb", "sessionId": "bbbbbbbb-2222",
                 "state": "working", "pid": 1, "startedAt": 1783504800}
        dash = self._build([req], agents=[agent])
        self.assertEqual(dash["running"][0]["started_at"], 1783504800)

    def test_epoch_accepts_int_float_rejects_bool(self):
        self.assertEqual(dashboard._epoch(1783504800), 1783504800)
        self.assertEqual(dashboard._epoch(1783504800.9), 1783504800)
        self.assertIsNone(dashboard._epoch(True))   # bool 是 int 子类，不是时间戳
        self.assertIsNone(dashboard._epoch("not-a-date"))

    # -- archive() crash-mid-move 残件：同 id 只出现在 archived 一个分区 ------- #
    def test_active_residue_of_archived_card_deduped(self):
        residue = Requirement.from_dict({
            "id": "R-201", "title": "mid-move", "status": "delivered",
            "execution": {"accepted_at": "2026-07-08T12:00:00Z"},
        })
        sealed = Requirement.from_dict({
            "id": "R-201", "title": "mid-move", "status": "archived",
            "prev_status": "delivered",
            "archived_at": "2026-07-08T12:01:00Z", "archive_reason": "user",
        })
        dash = self._build([residue], archived=[sealed])
        self.assertEqual([i["id"] for i in dash["completed"]], [])
        self.assertEqual([i["id"] for i in dash["archived"]], ["R-201"])
        self.assertEqual(dash["counts"]["completed"], 0)
        self.assertEqual(dash["counts"]["archived"], 1)

    # -- final_draft 投影端兜底截断（契约 §16 ≤20000 字） ---------------------- #
    def test_review_final_draft_clipped_to_contract_cap(self):
        req = Requirement.from_dict({
            "id": "R-200", "title": "big", "status": "review",
            "execution": {"session_id": "aaaaaaaa-1",
                          "final_draft": "x" * 50000},
        })
        dash = self._build([req])
        self.assertEqual(len(dash["review"][0]["final_draft"]), 20000)

    def test_review_missing_final_draft_stays_none(self):
        req = Requirement.from_dict({
            "id": "R-202", "title": "no draft", "status": "review",
            "execution": {"session_id": "aaaaaaaa-2"},
        })
        dash = self._build([req])
        self.assertIsNone(dash["review"][0]["final_draft"])

    # -- 损坏的 archived 卡同样单卡隔离 --------------------------------------- #
    def test_corrupt_archived_card_skipped_others_survive(self):
        class _Exploding:
            id = "R-666"

            def __getattr__(self, name):
                raise RuntimeError("corrupt archived card")

        good = Requirement.from_dict({
            "id": "R-203", "title": "good archived", "status": "archived",
            "archived_at": "2026-07-08T12:00:00Z", "archive_reason": "user",
        })
        dash = self._build([], archived=[_Exploding(), good])
        self.assertEqual([i["id"] for i in dash["archived"]], ["R-203"])
        self.assertEqual(dash["counts"]["archived"], 1)


class DeviceLabelTestCase(unittest.TestCase):
    """v0.35 top-level ``device_label`` (CONTRACT §34) — add-only passthrough
    of the pairing label from state/sync.json so a paired phone can adopt a
    Mac rename without re-scanning. Absent (not null) when unpaired /
    unlabeled / unreadable."""

    def setUp(self):
        self.cfg = config.Config()
        config.ensure_state_dirs()
        self.sync_path = config.STATE_DIR / "sync.json"
        self.addCleanup(lambda: self.sync_path.unlink(missing_ok=True))

    def _build(self):
        return dashboard.build_dashboard(reqs=[], agents=[], cfg=self.cfg,
                                         archived=[])

    def test_label_from_sync_json_lands_top_level(self):
        self.sync_path.write_text(
            json.dumps({"mode": "cloud", "label": "书房的 Mac mini"}),
            encoding="utf-8")
        self.assertEqual(self._build()["device_label"], "书房的 Mac mini")

    def test_unpaired_no_sync_json_key_absent(self):
        self.sync_path.unlink(missing_ok=True)
        self.assertNotIn("device_label", self._build())

    def test_empty_or_missing_label_key_absent(self):
        for cfg in ({"mode": "cloud"},
                    {"mode": "cloud", "label": ""},
                    {"mode": "cloud", "label": "   "}):
            with self.subTest(cfg=cfg):
                self.sync_path.write_text(json.dumps(cfg), encoding="utf-8")
                self.assertNotIn("device_label", self._build())

    def test_corrupt_sync_json_key_absent_not_crash(self):
        for raw in ("{not json", "[1, 2]"):
            with self.subTest(raw=raw):
                self.sync_path.write_text(raw, encoding="utf-8")
                self.assertNotIn("device_label", self._build())


if __name__ == "__main__":
    unittest.main()
