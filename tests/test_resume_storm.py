"""§46 resume 风暴降级（session 生命周期可靠性，2026-08-07）.

生产事故：R-187 4 分钟三连救、R-142 13 分钟四连救——每次 resume 成功后 session
短暂存活，reconcile「见到活着」把 resume_attempts 清零，退避永远从零开始，
卡死→救→再死的循环没有降级出口。判例：

- reconcile 每次自动救活启动（resume/brief、成功与否）都在
  execution.resume_history 记一条 ISO 时间戳（封顶 RESUME_HISTORY_CAP 条）；
- 30 分钟窗口（RESUME_STORM_WINDOW_S）内启动数达 RESUME_STORM_THRESHOLD=3
  次后 session 又死了 -> 置 resume_exhausted（复用既有放弃机制）+
  resume_storm_at + notes [resume-storm] 标签 + msg_resume_storm 通知 +
  analytics `resume_storm_degraded`，本 pass 不再发起 resume；
- executor.answer（owner 亲手救活）清 resume_history，正常 auto-resume
  不会撞上残留计数再次降级。

Runs entirely inside the sandbox AIASSISTANT_HOME (tests/__init__.py).
"""
import datetime as _dt
import os
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import actd, executor
from act.lib import analytics, config, registry
from act.lib.registry import Requirement, State

SID = "dddd4444-0000-4000-8000-000000000001"


def _iso_ago(seconds):
    t = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(seconds=seconds)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


class ResumeStormBase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()
        self.cfg = config.Config()
        p = mock.patch.object(actd.notify, "notify", mock.Mock(return_value=True))
        self.notify = p.start()
        self.addCleanup(p.stop)
        lang = mock.patch.dict(os.environ, {"AIASSISTANT_UI_LANG": "zh"})
        lang.start()
        self.addCleanup(lang.stop)
        # dead-path probe must stay hermetic (no transcript reads)
        p2 = mock.patch.object(actd, "_promote_if_delivered", return_value=False)
        p2.start()
        self.addCleanup(p2.stop)

    def _mk_req(self, execution=None, req_id="R-950"):
        req = Requirement(id=req_id, title="resume 风暴测试",
                          status=State.EXECUTING.value,
                          execution=execution or {"session_id": SID})
        registry.save(req)
        return req

    def _reconcile(self, resume):
        with mock.patch.object(actd, "_run_claude_agents", return_value=[]), \
             mock.patch.object(actd.executor, "resume", resume):
            return actd.reconcile_executing(self.cfg, set())


class StormLedgerTestCase(ResumeStormBase):
    def test_every_launch_lands_in_resume_history(self):
        self._mk_req()
        self._reconcile(mock.Mock(return_value=True))
        ex = registry.load("R-950").execution or {}
        self.assertEqual(len(ex.get("resume_history") or []), 1)

    def test_history_is_capped(self):
        old = [_iso_ago(99999)] * actd.RESUME_HISTORY_CAP   # 窗口外，不触发风暴
        self._mk_req(execution={"session_id": SID, "resume_history": old})
        self._reconcile(mock.Mock(return_value=True))
        ex = registry.load("R-950").execution or {}
        self.assertEqual(len(ex["resume_history"]), actd.RESUME_HISTORY_CAP)

    def test_threshold_recent_launches_degrade_instead_of_resuming(self):
        # 窗口内已救活 3 次、session 又死了 -> 降级，不再无限救活
        hist = [_iso_ago(1200), _iso_ago(600), _iso_ago(60)]
        self._mk_req(execution={"session_id": SID, "resume_history": hist})
        resume = mock.Mock(return_value=True)
        self._reconcile(resume)
        resume.assert_not_called()                     # 停止救活
        ex = registry.load("R-950").execution or {}
        self.assertTrue(ex.get("resume_exhausted"))    # 复用既有放弃机制
        self.assertTrue(ex.get("resume_storm_at"))
        req = registry.load("R-950")
        self.assertIn("[resume-storm]", req.notes or "")
        self.assertIn("需人工看一眼", req.notes or "")
        titles = [c.args[0] for c in self.notify.call_args_list]
        self.assertTrue(any("反复中断" in t for t in titles))
        events = [e.get("event") for e in analytics.read_events()]
        self.assertIn("resume_storm_degraded", events)

    def test_old_launches_outside_window_do_not_degrade(self):
        hist = [_iso_ago(7200), _iso_ago(5400), _iso_ago(3600)]  # 全在窗口外
        self._mk_req(execution={"session_id": SID, "resume_history": hist})
        resume = mock.Mock(return_value=True)
        self._reconcile(resume)
        resume.assert_called_once()                    # 照常救活
        ex = registry.load("R-950").execution or {}
        self.assertFalse(ex.get("resume_exhausted"))

    def test_degraded_card_is_skipped_on_later_passes(self):
        hist = [_iso_ago(1200), _iso_ago(600), _iso_ago(60)]
        self._mk_req(execution={"session_id": SID, "resume_history": hist})
        resume = mock.Mock(return_value=True)
        self._reconcile(resume)                        # 降级
        self.notify.reset_mock()
        self._reconcile(resume)                        # 后续 pass：exhausted 短路
        resume.assert_not_called()
        self.notify.assert_not_called()                # 不重复 ping

    def test_corrupt_history_entries_never_crash_the_pass(self):
        hist = [123, None, "not-a-date", _iso_ago(60)]
        self._mk_req(execution={"session_id": SID, "resume_history": hist})
        resume = mock.Mock(return_value=True)
        self._reconcile(resume)                        # 宪法 11：失败不外溢
        resume.assert_called_once()


class AnswerClearsStormTestCase(ResumeStormBase):
    def test_owner_answer_resets_resume_history(self):
        # answer() 清 resume_exhausted 的同时也清 resume_history —— 否则
        # owner 亲手救活的卡下一次正常 auto-resume 立刻再次降级。
        req = self._mk_req(execution={
            "session_id": SID, "resume_exhausted": True, "resume_storm_at":
            _iso_ago(60), "resume_history": [_iso_ago(600), _iso_ago(60)]})
        with mock.patch.object(executor, "_transcript_info",
                               return_value=(SID, config.STATE_DIR)), \
             mock.patch.object(executor, "_agent_info", return_value={}):
            ok = executor.answer(
                req, "继续吧", cfg=config.Config(),
                runner=mock.Mock(return_value=mock.Mock(
                    returncode=0, stdout="backgrounded · dddd4444", stderr="")))
        self.assertTrue(ok)
        ex = registry.load("R-950").execution or {}
        self.assertNotIn("resume_exhausted", ex)
        self.assertNotIn("resume_history", ex)


if __name__ == "__main__":
    unittest.main()
