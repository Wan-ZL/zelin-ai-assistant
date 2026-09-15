"""actd/reconcile — dev 列车改动行上的变异幸存体判例（CONTRACT §44.3-S / §39 / §37.1）。

两条契约：

  * **§39 诚实丢弃点名的是卡的显示名**：owner 打的字没送到时，横幅里要出现他认得
    的卡名，编号只是空标题时的回落。
  * **§44.3-S + §37.1：flush 真的把这一批 steer 连同显示名重审句送进会话**——
    `executor.resume` 拿到的 prompt 就是 `steer.build_steer_prompt(批, title_line=…)`，
    不是空、不是 None。steer 是交互式长会话唯一可靠的回流点；prompt 丢了就等于
    owner 的追加指令与本轮的 CARD TITLE 请求一起蒸发，而台账还记着「已送达」。

沙箱 AIASSISTANT_HOME；executor 用 mock 替身，绝不 spawn 真 claude。
"""
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env before act imports

from act import actd
from act.lib import config, dispatch_prompt, registry, steer
from act.lib.actd import reconcile
from act.lib.registry import Requirement, State


def _executing(rid, title):
    req = Requirement(id=rid, title=title, status=State.EXECUTING.value,
                      sources=[{"who": "zelin", "channel": "quick",
                                "date": "2026-09-10", "quote": "q"}],
                      target_repo=TMP_HOME, target_kind="existing",
                      execution={"session_id": f"sid-{rid}"})
    registry.save(req)
    return req


class SteerBase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()
        self.notify = mock.patch.object(actd.notify, "notify").start()
        mock.patch.object(actd.analytics, "log_event").start()
        self.addCleanup(mock.patch.stopall)


class DropNoticeTestCase(SteerBase):
    def _drop(self, rid, title):
        req = _executing(rid, title)
        steer.enqueue_steer(req, "改用 B 方案", ts="2026-09-10T01:00:00Z")
        registry.save(req)
        reconcile.drop_steers(actd._ctx(), req, steer.pending_steers(req),
                              "3 次注入尝试失败", "attempts")

    def test_the_undelivered_notice_names_the_card_by_its_title(self):
        self._drop("R-95", "整理保险合同")
        self.assertEqual(self.notify.call_args.args,
                         ("追加指令未送达", "整理保险合同：3 次注入尝试失败"))

    def test_an_untitled_card_falls_back_to_its_id(self):
        self._drop("R-96", "")
        self.assertEqual(self.notify.call_args.args,
                         ("追加指令未送达", "R-96：3 次注入尝试失败"))


class FlushPromptTestCase(SteerBase):
    def test_the_flushed_prompt_is_the_batch_plus_the_title_line(self):
        req = _executing("R-97", "整理保险合同")
        steer.enqueue_steer(req, "改用 B 方案", ts="2026-09-10T01:00:00Z")
        registry.save(req)
        expected = steer.build_steer_prompt(
            steer.pending_steers(req),
            title_line=dispatch_prompt.rework_title_line(req))
        executor = mock.MagicMock()
        executor.briefing_window_open.return_value = True
        executor.stop_session.return_value = True
        executor.resume.return_value = True
        with mock.patch.object(actd, "executor", executor):
            reconcile.flush_steers(actd._ctx(), req, config.Config())
        self.assertTrue(executor.resume.called)
        self.assertEqual(executor.resume.call_args.kwargs["prompt"], expected)
        self.assertIn("改用 B 方案", expected)
        self.assertIn("CARD TITLE", expected)


if __name__ == "__main__":   # pragma: no cover
    unittest.main()
