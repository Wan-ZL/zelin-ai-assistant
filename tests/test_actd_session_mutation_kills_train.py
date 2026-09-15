"""actd/session — dev 列车改动行上的变异幸存体判例（CONTRACT §46 / §37 + §37.1）。

两条契约：

  * **§46 停不住的会话点名的是卡的显示名**：`msg_stop_failed` 收到的是 `title`，
    编号只是空标题时的回落——owner 在横幅上要认出是哪件事，不是一串 R-号。
  * **§37.1 中途改名只在真改了名时才落盘**：`apply_harvest_title` 的返回值是
    「名字真的变了吗」——同名重复（每 120 s 一次的探针）与根本没有 CARD TITLE 行
    都必须答 False，答 True 会把注册表写穿。

沙箱 AIASSISTANT_HOME；executor 用 mock 替身，绝不 spawn 真 claude。
"""
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env before act imports

from act import actd
from act.lib import config, notify
from act.lib.actd import session
from act.lib.registry import Requirement, State


def _executing(rid, title):
    return Requirement(id=rid, title=title, status=State.EXECUTING.value,
                       execution={"session_id": f"sid-{rid}"})


class StopFailureNoticeTestCase(unittest.TestCase):
    """§46：确认式停止失败 → 通知 + 卡面台账。"""

    def setUp(self):
        config.ensure_state_dirs()
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()
        self.notify = mock.patch.object(actd.notify, "notify").start()
        self.addCleanup(mock.patch.stopall)

    def _fail_stop(self, req):
        ex = dict(req.execution or {})
        executor = mock.MagicMock()
        executor.stop_session_confirmed.return_value = (False, True, "still alive")
        with mock.patch.object(actd, "executor", executor):
            return session.stop_session_tracked(actd._ctx(), req, ex,
                                                ex["session_id"], "reject"), ex

    def test_the_notice_names_the_card_by_its_title(self):
        (stopped, issued), ex = self._fail_stop(_executing("R-77", "整理保险合同"))
        self.assertEqual((stopped, issued), (False, True))
        self.assertEqual(self.notify.call_args.args, notify.msg_stop_failed("整理保险合同"))
        self.assertEqual(ex["stop_failed_error"], "still alive")

    def test_an_untitled_card_falls_back_to_its_id(self):
        self._fail_stop(_executing("R-78", ""))
        self.assertEqual(self.notify.call_args.args, notify.msg_stop_failed("R-78"))


class HarvestTitleAnswerTestCase(unittest.TestCase):
    """§37.1：返回值 = 「名字真的变了吗」，调用方据此决定落不落盘。"""

    def setUp(self):
        config.ensure_state_dirs()
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()
        self.d = actd._ctx()

    def test_a_real_rename_answers_true(self):
        req = Requirement(id="R-80", title="老名字")
        self.assertIs(session.apply_harvest_title(self.d, req, {"card_title": "新名字"}), True)
        self.assertEqual(req.display_title, "新名字")

    def test_the_same_name_again_answers_false(self):
        req = Requirement(id="R-81", title="老名字", display_title="新名字")
        self.assertIs(session.apply_harvest_title(self.d, req, {"card_title": "新名字"}), False)

    def test_no_card_title_line_answers_false(self):
        req = Requirement(id="R-82", title="老名字")
        self.assertIs(session.apply_harvest_title(self.d, req, {}), False)
        self.assertIs(session.apply_harvest_title(self.d, req, None), False)
        self.assertFalse(req.display_title)


if __name__ == "__main__":   # pragma: no cover
    unittest.main()
