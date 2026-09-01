"""投影一致性判例：卡 YAML status × roster state -> dashboard 分区的映射表。

v0.48.8（#119，CONTRACT §2/§13/§46.3）：「需输入」的会话语义退役——受阻/放弃
救活的 executing 卡不再投影 needs_input（reconcile 会在下一 pass 收割进待验收；
投影间隙里诚实地留在 running，state 原样）。needs_input[] 从此只承载 §4 派发
刹车行（dispatch_halted，见 tests/test_dispatch_storm_brake.py）。

    YAML status      roster state            -> 分区
    ---------------  ----------------------  -----------
    executing        working (live)          running
    executing        blocked (live)          running（state=blocked，待 reconcile 收割）
    executing        done                    review
    executing        缺席 (roster 无此 sid)   running (state unknown)
    executing        缺席 + resume_exhausted  running（待 reconcile 收割进 review）
    approved         —                       running (queued)
    approved         dispatch_halted         needs_input（§4 刹车行，唯一住户）
    review           缺席/idle               review
    review           working (live)          running (from_review)
    review           interrupted_reason      review（行带 interrupted: true）
    delivered        —                       completed

外加不变量：一张卡恰好出现在一个分区里，counts 与分区长度一致。

Runs entirely inside the sandbox AIASSISTANT_HOME (tests/__init__.py).
"""
import unittest

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act.lib import config, dashboard
from act.lib.registry import Requirement, State

SID = "eeee5555-0000-4000-8000-000000000001"

# 一张卡最多进这些「看板泳道」分区之一（trash/archived/debt 等由其他状态覆盖）
_LANES = ("needs_approval", "running", "needs_input", "review", "completed",
          "debt", "trash")


def _agent(state, pid=1234):
    return {"id": SID.split("-")[0], "sessionId": SID, "pid": pid,
            "state": state, "cwd": "/tmp/x", "name": "agent"}


def _build(req, agents):
    cfg = config.Config()
    return dashboard.build_dashboard(reqs=[req], agents=agents, cfg=cfg,
                                     archived=[])


def _lanes_of(dash, rid):
    return [k for k in _LANES if any(r.get("id") == rid for r in dash.get(k, []))]


class StatusProjectionMatrixTestCase(unittest.TestCase):
    def _assert_single_lane(self, dash, rid, lane):
        self.assertEqual(_lanes_of(dash, rid), [lane])
        # counts 与分区长度一致（completed/archived 的 cap 情形单卡不触及）
        for k in _LANES:
            self.assertEqual(dash["counts"][k], len(dash[k]), msg=k)

    def test_executing_working_projects_running(self):
        req = Requirement(id="R-1", title="t", status=State.EXECUTING.value,
                          execution={"session_id": SID})
        dash = _build(req, [_agent("working")])
        self._assert_single_lane(dash, "R-1", "running")

    def test_executing_blocked_projects_running_no_needs_input(self):
        # #119：受阻会话不再投影「需输入」——reconcile 在下一 pass 把它收割进
        # 待验收；投影间隙里留在 running（state 原样，诚实呈现），无「回答」入口
        req = Requirement(id="R-2", title="t", status=State.EXECUTING.value,
                          execution={"session_id": SID})
        dash = _build(req, [_agent("blocked")])
        self._assert_single_lane(dash, "R-2", "running")
        row = dash["running"][0]
        self.assertEqual(row["state"], "blocked")
        self.assertNotIn("question", row)

    def test_executing_done_projects_review(self):
        req = Requirement(id="R-3", title="t", status=State.EXECUTING.value,
                          execution={"session_id": SID})
        dash = _build(req, [_agent("done", pid=None)])
        self._assert_single_lane(dash, "R-3", "review")

    def test_executing_vanished_session_projects_running_unknown(self):
        req = Requirement(id="R-4", title="t", status=State.EXECUTING.value,
                          execution={"session_id": SID})
        dash = _build(req, [])
        self._assert_single_lane(dash, "R-4", "running")
        row = dash["running"][0]
        self.assertEqual(row["state"], "unknown")

    def test_executing_exhausted_dead_projects_running(self):
        # #119：放弃救活的死会话卡不再挂「需输入」——投影 running（等 reconcile
        # 收割进待验收），needs_input 恒空
        req = Requirement(id="R-5", title="t", status=State.EXECUTING.value,
                          execution={"session_id": SID, "resume_exhausted": True})
        dash = _build(req, [])
        self._assert_single_lane(dash, "R-5", "running")
        self.assertEqual(dash["needs_input"], [])

    def test_executing_exhausted_but_live_agent_stays_running(self):
        # 降级标记残留 + agent 实际活着在干活 -> 以 roster 事实为准，照常 running
        req = Requirement(id="R-6", title="t", status=State.EXECUTING.value,
                          execution={"session_id": SID, "resume_exhausted": True})
        dash = _build(req, [_agent("working")])
        self._assert_single_lane(dash, "R-6", "running")

    def test_approved_projects_running_queued(self):
        req = Requirement(id="R-7", title="t", status=State.APPROVED.value)
        dash = _build(req, [])
        self._assert_single_lane(dash, "R-7", "running")
        self.assertEqual(dash["running"][0]["state"], "queued")

    def test_review_settled_projects_review(self):
        req = Requirement(id="R-8", title="t", status=State.REVIEW.value,
                          execution={"session_id": SID, "done": True,
                                     "review_at": "2026-08-07T00:00:00Z"})
        dash = _build(req, [])
        self._assert_single_lane(dash, "R-8", "review")

    def test_review_working_projects_running_from_review(self):
        req = Requirement(id="R-9", title="t", status=State.REVIEW.value,
                          execution={"session_id": SID, "done": True})
        dash = _build(req, [_agent("working")])
        self._assert_single_lane(dash, "R-9", "running")
        self.assertIs(dash["running"][0].get("from_review"), True)

    def test_interrupted_harvest_marks_review_row(self):
        # #119：中断收割行带 add-only interrupted: true（detect_transitions 据此
        # 不发「AI 已交付草稿」）；正常交付行不带该键
        req = Requirement(id="R-9b", title="t", status=State.REVIEW.value,
                          execution={"session_id": SID, "done": True,
                                     "review_at": "2026-08-07T00:00:00Z",
                                     "interrupted_reason": "blocked"})
        dash = _build(req, [])
        self._assert_single_lane(dash, "R-9b", "review")
        self.assertIs(dash["review"][0].get("interrupted"), True)

    def test_normal_review_row_has_no_interrupted_key(self):
        req = Requirement(id="R-9c", title="t", status=State.REVIEW.value,
                          execution={"session_id": SID, "done": True,
                                     "review_at": "2026-08-07T00:00:00Z"})
        dash = _build(req, [])
        self.assertNotIn("interrupted", dash["review"][0])

    def test_delivered_projects_completed(self):
        req = Requirement(id="R-10", title="t", status=State.DELIVERED.value,
                          execution={"session_id": SID,
                                     "accepted_at": "2026-08-07T00:00:00Z"})
        dash = _build(req, [])
        self._assert_single_lane(dash, "R-10", "completed")


if __name__ == "__main__":
    unittest.main()
