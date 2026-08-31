"""steer relay 端到端行为补钉（§44.3-S 接线——inbox → 台账 → 投影 → 遥测）。

test_steer.py 钉纯函数记账、test_actd_wire.py 钉主要接线；这里补三类
从外部可观察的契约：

  * **遥测词表**：inbox_steer（入队）/ steer_delivered（送达）/ steer_dropped
    （丢弃，reason=done|attempts）——analytics 事件名与机读原因是 wire 契约；
  * **crash-replay 全链路**：同一 inbox 文件在 flush 送达**之后**重放（unlink
    失败的第二命），经 delivered 台账 dedup 不得二次入队/二次送达；
  * **诚实投影**：dropped steer 不进 dashboard steers[]（C-4），可见性由
    notes 的 `[追加指令未送达]` 痕承担；steer 绝不触发基线 fold（无
    `[修改方向]` 痕、不退 card_sent）；空评论 noop 不动卡。

沙箱 AIASSISTANT_HOME；executor/notify/roster 全 mock。
"""
import json
import unittest
import uuid
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import actd
from act.lib import config, registry, steer
from act.lib.dashboard import build_dashboard
from act.lib.registry import Requirement, State

_HAND_SRC = [{"who": "zelin", "channel": "quick_capture",
              "date": "2026-08-30", "quote": "手打的活"}]


def _clean():
    config.ensure_state_dirs()
    for p in config.REGISTRY_DIR.glob("*.yaml"):
        p.unlink()
    for p in config.INBOX_DIR.glob("*.json"):
        p.unlink()


def _executing(req_id="R-850", **kw):
    base = dict(id=req_id, title=f"steer relay {req_id}", type="other",
                tier="T1", status=State.EXECUTING.value,
                sources=list(_HAND_SRC), target_repo=TMP_HOME,
                plan=["原计划"], execution={"session_id": "sid-1"})
    base.update(kw)
    req = Requirement(**base)
    registry.save(req)
    return req


def _drop_comment(req_id, comment, ts="2026-08-30T01:00:00Z", name=None):
    path = config.INBOX_DIR / f"{name or uuid.uuid4()}.json"
    path.write_text(json.dumps({"id": req_id, "action": "comment",
                                "comment": comment, "ts": ts}),
                    encoding="utf-8")
    return path


class SteerRelayBase(unittest.TestCase):
    def setUp(self):
        _clean()
        self.notify = mock.patch.object(actd.notify, "notify").start()
        self.events = []
        real_log = actd.analytics.log_event

        def _spy(event, **meta):
            self.events.append((event, meta))
            return real_log(event, **meta)

        mock.patch.object(actd.analytics, "log_event", side_effect=_spy).start()
        self.addCleanup(mock.patch.stopall)

    def _reconcile(self, ex_mock, roster_state="blocked"):
        roster = [{"id": "sid-1", "sessionId": "sid-1",
                   "state": roster_state, "pid": 42}]
        with mock.patch.object(actd, "executor", ex_mock), \
                mock.patch.object(actd, "_run_claude_agents",
                                  return_value=roster):
            actd.reconcile_executing(config.Config(), set())

    def _named(self, event):
        return [meta for name, meta in self.events if name == event]


# --------------------------------------------------------------------------- #
# 遥测词表：inbox_steer / steer_delivered / steer_dropped(reason=…)
# --------------------------------------------------------------------------- #
class TestSteerTelemetry(SteerRelayBase):
    def test_enqueue_logs_inbox_steer(self):
        _executing("R-850")
        _drop_comment("R-850", "改用 B 方案")
        actd.process_inbox()
        self.assertEqual(len(self._named("inbox_steer")), 1)
        self.assertEqual(self._named("inbox_steer")[0].get("req"), "R-850")

    def test_flush_logs_steer_delivered_with_count(self):
        req = _executing("R-851")
        steer.enqueue_steer(req, "one", ts="t1")
        steer.enqueue_steer(req, "two", ts="t2")
        registry.save(req)
        ex_mock = mock.MagicMock()
        ex_mock.resume.return_value = True
        ex_mock.stop_session.return_value = True
        self._reconcile(ex_mock)
        delivered = self._named("steer_delivered")
        self.assertEqual(len(delivered), 1)
        self.assertEqual(delivered[0].get("n"), 2)

    def test_done_promotion_logs_steer_dropped_reason_done(self):
        req = _executing("R-852")
        steer.enqueue_steer(req, "来不及了", ts="t1")
        registry.save(req)
        ex_mock = mock.MagicMock()
        ex_mock.harvest_delivery.return_value = {}
        self._reconcile(ex_mock, roster_state="done")
        dropped = self._named("steer_dropped")
        self.assertEqual(len(dropped), 1)
        self.assertEqual(dropped[0].get("reason"), "done")

    def test_give_up_logs_steer_dropped_reason_attempts(self):
        req = _executing("R-853")
        steer.enqueue_steer(req, "卡死的指令", ts="t1")
        registry.save(req)
        ex_mock = mock.MagicMock()
        ex_mock.resume.return_value = False
        ex_mock.stop_session.return_value = True
        for _ in range(steer.MAX_STEER_ATTEMPTS + 1):
            self._reconcile(ex_mock)
        dropped = self._named("steer_dropped")
        self.assertEqual(len(dropped), 1)
        self.assertEqual(dropped[0].get("reason"), "attempts")


# --------------------------------------------------------------------------- #
# crash-replay 全链路：flush 之后重放同一 inbox 文件不得二次入队/送达
# --------------------------------------------------------------------------- #
class TestReplayAfterFlush(SteerRelayBase):
    def test_replay_after_delivery_is_noop(self):
        _executing("R-854")
        _drop_comment("R-854", "改标题", ts="2026-08-30T01:00:00Z",
                      name="replayed-file")
        actd.process_inbox()
        ex_mock = mock.MagicMock()
        ex_mock.resume.return_value = True
        ex_mock.stop_session.return_value = True
        self._reconcile(ex_mock)                       # flush 清队 + 上台账
        req = registry.load("R-854")
        self.assertEqual(req.execution.get("steer_count"), 1)
        # unlink 失败的第二命：**同一个文件**（同 stem 同文同 ts）再进 inbox
        # ——m1 起 dedup 键带 stem，只有真正的同文件重放才被台账去重
        _drop_comment("R-854", "改标题", ts="2026-08-30T01:00:00Z",
                      name="replayed-file")
        actd.process_inbox()
        req = registry.load("R-854")
        self.assertEqual(steer.pending_steers(req), [])     # 未再入队
        self.assertEqual(req.execution.get("steer_count"), 1)
        self.assertEqual(len(steer.delivered_entries(req)), 1)


# --------------------------------------------------------------------------- #
# 诚实投影与状态机零改动
# --------------------------------------------------------------------------- #
class TestHonestProjection(SteerRelayBase):
    def test_dropped_steer_not_projected_but_traced_in_notes(self):
        # C-4：drop 之后 steers[] 不再渲染这条（无法对账的行不投影），
        # §39 可见性由卡片 notes 的冻结行承担。
        req = _executing("R-855")
        steer.enqueue_steer(req, "被丢弃的指令", ts="t1")
        registry.save(req)
        ex_mock = mock.MagicMock()
        ex_mock.resume.return_value = False
        ex_mock.stop_session.return_value = True
        for _ in range(steer.MAX_STEER_ATTEMPTS + 1):
            self._reconcile(ex_mock)
        req = registry.load("R-855")
        self.assertIn("追加指令未送达", req.notes)
        self.assertIn("被丢弃的指令", req.notes)
        agents = [{"id": "sid-1", "sessionId": "sid-1",
                   "state": "working", "pid": 42}]
        dash = build_dashboard(reqs=[req], agents=agents, cfg=config.Config())
        row = [r for r in dash["running"] if r["id"] == "R-855"][0]
        self.assertNotIn("steers", row)                 # 空列表 = 字段不出现

    def test_steer_never_triggers_baseline_fold(self):
        # EXECUTING 卡上的评论走 steer——绝不折进 plan/notes（[修改方向]）、
        # 绝不退 card_sent 重批（那是 §44.3-S 要替换掉的旧语义）。
        _executing("R-856", notes="")
        _drop_comment("R-856", "调转方向")
        actd.process_inbox()
        req = registry.load("R-856")
        self.assertEqual(req.status, State.EXECUTING.value)
        self.assertEqual(req.plan, ["原计划"])
        self.assertNotIn("修改方向", req.notes or "")

    def test_blank_comment_on_executing_is_noop(self):
        _executing("R-857")
        _drop_comment("R-857", "   ")
        actd.process_inbox()
        req = registry.load("R-857")
        self.assertEqual(steer.pending_steers(req), [])
        self.assertNotIn("steer_queued", req.execution or {})
        self.assertEqual(self._named("inbox_steer"), [])


if __name__ == "__main__":
    unittest.main()
