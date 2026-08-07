"""§46 投影一致性判例：卡 YAML status × roster state -> dashboard 分区的映射表.

生产摩擦（2026-08-07）：「dashboard 显示 needs_input 而卡 YAML 是 executing」
曾被当作不一致上报——按 §2 这正是设计本身：needs_input 分区 = status=executing
的卡 join roster blocked 状态，registry 没有 needs_input 这个状态。本文件把这
张映射表钉成判例，任何一格漂移测试就红：

    YAML status      roster state          -> 分区
    ---------------  --------------------  -----------
    executing        working (live)        running
    executing        blocked (live)        needs_input   ← 设计使然，非 bug
    executing        done                  review
    executing        缺席 (roster 无此 sid)  running (state unknown)
    executing        缺席 + resume_exhausted needs_input   ← §46 降级卡
    executing        死条目(无 pid) + exhausted needs_input ← 死 = 无活 pid，
                                              不是「不在 roster」（--all 留死条目）
    approved         —                     running (queued)
    review           缺席/idle             review
    review           working (live)        running (from_review)
    delivered        —                     completed

外加不变量：一张卡恰好出现在一个分区里，counts 与分区长度一致。

Runs entirely inside the sandbox AIASSISTANT_HOME (tests/__init__.py).
"""
import unittest
from unittest import mock

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
    # question 抽取读真 transcript —— 判例只看分区路由，钉死为 None 保持 hermetic
    with mock.patch.object(dashboard, "_question_cached", return_value=None):
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

    def test_executing_blocked_projects_needs_input_by_design(self):
        # ← 这格就是「dashboard needs_input / YAML executing」的判例：设计使然
        req = Requirement(id="R-2", title="t", status=State.EXECUTING.value,
                          execution={"session_id": SID})
        dash = _build(req, [_agent("blocked")])
        self._assert_single_lane(dash, "R-2", "needs_input")

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

    def test_executing_exhausted_dead_projects_needs_input(self):
        # §46：auto-resume 已放弃（含 resume 风暴降级）+ 会话不在 roster 上 =
        # 需要人才能推进 —— 投影进 needs_input，不再在 running 里装忙（宪法 3）
        req = Requirement(id="R-5", title="t", status=State.EXECUTING.value,
                          execution={"session_id": SID, "resume_exhausted": True})
        dash = _build(req, [])
        self._assert_single_lane(dash, "R-5", "needs_input")
        row = dash["needs_input"][0]
        self.assertIs(row.get("resume_exhausted"), True)   # add-only 标记
        self.assertEqual(row["state"], "blocked")

    def test_executing_exhausted_dead_roster_entry_projects_needs_input(self):
        # §46：roster --all 会给 failed/stopped 留死条目（无 pid）——死的判据
        # 是「无活 pid」（copy_cmd 的既有活性判据），不是「不在 roster」；
        # 按缺席判会让降级卡顶着死条目继续在 running 里装忙
        req = Requirement(id="R-5b", title="t", status=State.EXECUTING.value,
                          execution={"session_id": SID, "resume_exhausted": True})
        dash = _build(req, [_agent("failed", pid=None)])
        self._assert_single_lane(dash, "R-5b", "needs_input")
        self.assertIs(dash["needs_input"][0].get("resume_exhausted"), True)

    def test_exhausted_row_question_is_fixed_copy_not_transcript_text(self):
        # §46：降级卡的 question 用固定文案——死 transcript 的最后一条
        # assistant 文本不是提问（agent 并没有在等答案），拿来展示是误导
        req = Requirement(id="R-5c", title="t", status=State.EXECUTING.value,
                          execution={"session_id": SID, "resume_exhausted": True})
        cfg = config.Config()
        sentinel = "请问下一步我该改哪个文件？"   # 死会话的最后一句话
        with mock.patch.object(dashboard, "_question_cached",
                               return_value=sentinel):
            dash = dashboard.build_dashboard(reqs=[req], agents=[], cfg=cfg,
                                             archived=[])
        row = dash["needs_input"][0]
        q = row.get("question") or ""
        self.assertNotIn(sentinel, q)             # transcript 文本不冒充提问
        self.assertTrue("停止" in q or "Stop" in q)   # 固定文案指现存出口

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

    def test_delivered_projects_completed(self):
        req = Requirement(id="R-10", title="t", status=State.DELIVERED.value,
                          execution={"session_id": SID,
                                     "accepted_at": "2026-08-07T00:00:00Z"})
        dash = _build(req, [])
        self._assert_single_lane(dash, "R-10", "completed")


if __name__ == "__main__":
    unittest.main()
