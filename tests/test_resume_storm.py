"""§46 resume 风暴降级（session 生命周期可靠性，2026-08-07）.

生产事故：R-187 4 分钟三连救、R-142 13 分钟四连救——每次 resume 成功后 session
短暂存活，reconcile「见到活着」把 resume_attempts 清零，退避永远从零开始，
卡死→救→再死的循环没有降级出口。判例：

- reconcile 每次**成功**的自动救活启动（resume/brief，ok=True）在
  execution.resume_history 记一条 ISO 时间戳（封顶 RESUME_HISTORY_CAP 条）；
  失败启动不入账——数「救活成功」不数「尝试」，网络抖动 3 连败走既有
  resume_attempts>=5 的连续失败老路，不永久降级（PR #97 review P1）；
- 30 分钟窗口（RESUME_STORM_WINDOW_S）内成功救活数达 RESUME_STORM_THRESHOLD=3
  次后 session 又死了 -> 置 resume_exhausted + resume_storm_at + notes
  [resume-storm] 标签 + msg_resume_storm 通知 + analytics
  `resume_storm_degraded`，并按 #119（§46.3 v0.48.8）收割进待验收（review），
  不再发起 resume；
- brief 内部已 _rebook 落盘（新 session_id/清队列）——reconcile 记账基于盘上
  重读的卡，绝不用启动前的旧 execution 快照覆盖回滚（PR #97 review P1）；
（executor.answer 已随 #119 退役——「回答」语义由待验收「打回」承接，
rework 起新一轮时 execution 整体重建，风暴账自然清零。）

Runs entirely inside the sandbox AIASSISTANT_HOME (tests/__init__.py).
"""
import datetime as _dt
import os
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import actd
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
    def test_successful_launch_lands_in_resume_history(self):
        self._mk_req()
        self._reconcile(mock.Mock(return_value=True))
        ex = registry.load("R-950").execution or {}
        self.assertEqual(len(ex.get("resume_history") or []), 1)

    def test_network_blip_three_failed_launches_do_not_degrade(self):
        # 失败启动不入风暴账：网络抖动 3 连败仍走 resume_attempts>=5 的
        # 连续失败老路（退避 + 5 败放弃），不三连即永久 resume_exhausted
        self._mk_req()
        resume = mock.Mock(return_value=False)
        for _ in range(3):
            self._reconcile(resume)
            req = registry.load("R-950")
            ex = dict(req.execution or {})
            ex["last_resume_at"] = _iso_ago(9999)   # 越过退避，模拟下一轮到点
            req.execution = ex
            registry.save(req)
        self.assertEqual(resume.call_count, 3)       # 每轮都还在尝试救
        ex = registry.load("R-950").execution or {}
        self.assertNotIn("resume_history", ex)       # 失败不入账
        self.assertFalse(ex.get("resume_exhausted"))
        self.assertEqual(int(ex.get("resume_attempts") or 0), 3)  # 老账照记

    def test_three_successful_revivals_then_death_degrades(self):
        # R-187/R-142 生产形态：救活成功→短命再死→再救——第 4 个 pass 降级
        self._mk_req()
        resume = mock.Mock(return_value=True)
        for _ in range(3):
            self._reconcile(resume)
        self.assertEqual(resume.call_count, 3)
        self._reconcile(resume)
        self.assertEqual(resume.call_count, 3)       # 第 4 次不再救
        ex = registry.load("R-950").execution or {}
        self.assertTrue(ex.get("resume_exhausted"))
        self.assertTrue(ex.get("resume_storm_at"))
        # #119：降级即收割进待验收
        self.assertEqual(registry.load("R-950").status, State.REVIEW.value)

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
        self.assertTrue(ex.get("resume_exhausted"))
        self.assertTrue(ex.get("resume_storm_at"))
        req = registry.load("R-950")
        self.assertIn("[resume-storm]", req.notes or "")
        # #119：降级不再指「需输入」，而是收割进待验收
        self.assertEqual(req.status, State.REVIEW.value)
        self.assertEqual(ex.get("interrupted_reason"), "resume_storm")
        self.assertTrue(ex.get("done"))
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
        self._reconcile(resume)                        # 降级（并收割进待验收）
        self.notify.reset_mock()
        self._reconcile(resume)                        # 后续 pass：review 卡短路
        resume.assert_not_called()
        self.notify.assert_not_called()                # 不重复 ping

    def test_corrupt_history_entries_never_crash_the_pass(self):
        hist = [123, None, "not-a-date", _iso_ago(60)]
        self._mk_req(execution={"session_id": SID, "resume_history": hist})
        resume = mock.Mock(return_value=True)
        self._reconcile(resume)                        # 宪法 11：失败不外溢
        resume.assert_called_once()


NEW_SID = "ffff6666-0000-4000-8000-000000000002"


class BriefRebookTestCase(ResumeStormBase):
    def test_brief_rebooked_state_survives_reconcile_save(self):
        # brief 内部 _rebook 重读卡片再落盘（新 session_id、清空队列）——
        # reconcile 的记账 save 必须基于盘上重读的新状态；用启动前的旧
        # execution 快照覆盖会把新账整个回滚（旧会话 id 复活，每个 pass
        # 重复起会话——PR #97 review P1）。
        self._mk_req(execution={"session_id": SID,
                                "pending_briefings": ["fyi 一条背景信息"]})

        def fake_brief(req, cfg):
            # 模拟 executor.brief 的 _rebook：重读、改账、落盘，不碰传入的 req
            fresh = registry.load(req.id)
            fex = dict(fresh.execution or {})
            fex.pop("pending_briefings", None)
            fex["session_id"] = NEW_SID
            fresh.execution = fex
            registry.save(fresh)
            return True

        with mock.patch.object(actd, "_run_claude_agents", return_value=[]), \
             mock.patch.object(actd.executor, "brief", fake_brief):
            actd.reconcile_executing(self.cfg, set())
        ex = registry.load("R-950").execution or {}
        self.assertEqual(ex.get("session_id"), NEW_SID)   # 新账不被回滚
        self.assertNotIn("pending_briefings", ex)          # 队列保持已清
        self.assertEqual(len(ex.get("resume_history") or []), 1)  # 成功启动入账

    def test_brief_reload_failure_skips_bookkeeping_not_rollback(self):
        # 重读失败（坏 yaml/竞态）不许拿旧快照垫底 save——那同样会复活旧
        # session_id/pending_briefings；正确姿势是本轮跳过记账、下 pass 重试
        self._mk_req(execution={"session_id": SID,
                                "pending_briefings": ["fyi 一条背景信息"]})

        def fake_brief(req, cfg):
            # 不经 registry.load（下面把它 patch 成 None）直接落盘新账
            fresh = Requirement(id=req.id, title=req.title, status=req.status,
                                execution={"session_id": NEW_SID})
            registry.save(fresh)
            return True

        with mock.patch.object(actd, "_run_claude_agents", return_value=[]), \
             mock.patch.object(actd.executor, "brief", fake_brief), \
             mock.patch.object(actd.registry, "load", return_value=None):
            actd.reconcile_executing(self.cfg, set())
        ex = registry.load("R-950").execution or {}
        self.assertEqual(ex.get("session_id"), NEW_SID)   # 盘上新账原样保留
        self.assertNotIn("pending_briefings", ex)          # 旧队列没有复活
        self.assertNotIn("resume_history", ex)             # 本轮没记账（下 pass 补）


if __name__ == "__main__":
    unittest.main()
