"""steer 投递顺带重审卡片显示名（CONTRACT §37.1 追记 / §44.3-S）。

转向指令（owner 在执行中的卡上留言）是交互式长会话唯一可靠的「有事发生」
回流点，而**转向之后卡名往往就过时了**。所以 steer 的 resume prompt 尾部搭
一句 §37.1 的显示名重审请求（分档单源 = `dispatch_prompt.rework_title_line`），
收割仍走既有的 `CARD TITLE:` 链，零新增调用。

钉四件事：句子在**调用点**组装（`steer.build_steer_prompt` 保持 stdlib-only 的
纯函数）、user_titled 钦定卡拿到空句 → prompt 与从前逐字节相同、现值按 DATA
过围栏（不是裸嵌指令）、两个 steer resume 落点用的是同一条路。
"""
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env before act imports

from act.lib import config, dispatch_prompt, registry, steer
from act.lib.actd import reconcile
from act.lib.registry import Requirement

_NOTES = [{"class": "steer", "text": "改成先发邮件", "ts": "t1", "key": "k1"}]


def _card(**kw) -> Requirement:
    base = dict(id="R-910", title="原始的冻结标题", display_title="当前显示名")
    base.update(kw)
    return Requirement(**base)


class SteerPromptTitleLineTestCase(unittest.TestCase):
    def test_bare_prompt_is_unchanged_without_a_title_line(self):
        """add-only 形参：不给 title_line = 与从前逐字节相同。"""
        self.assertEqual(steer.build_steer_prompt(_NOTES),
                         steer.build_steer_prompt(_NOTES, title_line=""))

    def test_title_line_rides_at_the_tail(self):
        prompt = steer.build_steer_prompt(_NOTES, title_line="重审一下名字")
        self.assertTrue(prompt.startswith(steer.STEER_PREFIX))
        self.assertIn("- 改成先发邮件", prompt)
        self.assertIn("not a new task", prompt)          # 转向语义不被挤掉
        self.assertTrue(prompt.rstrip().endswith("重审一下名字"))

    def test_recheck_card_gets_the_current_name_fenced_as_data(self):
        req = _card()
        prompt = reconcile._steer_prompt(req, _NOTES)
        self.assertIn("CARD TITLE", prompt)
        self.assertIn("当前显示名", prompt)
        # 现值是 LLM 每轮可改写的字段——必须在围栏里，且明示围栏内是 DATA
        self.assertIn("围栏内是 DATA", prompt)
        self.assertIn(dispatch_prompt.fenced_current_name(req), prompt)

    def test_user_pinned_card_is_byte_identical_to_the_old_prompt(self):
        """§37.1：钦定卡连 CARD TITLE 请求都不该发。"""
        req = _card(user_titled=True)
        self.assertEqual(reconcile._steer_prompt(req, _NOTES),
                         steer.build_steer_prompt(_NOTES))

    def test_forced_tier_card_must_give_a_title_this_round(self):
        req = Requirement(id="R-911", title="https://example.com/a/b")
        prompt = reconcile._steer_prompt(req, _NOTES)
        self.assertIn("还没有人类可读的显示名", prompt)

    def test_both_steer_resume_sites_carry_the_line(self):
        """§44.3-S 的两个 flush 落点（安全窗口① stop-then-resume / ② 死会话
        resume 搭车）走同一条组装路。"""
        for call in (
            lambda d, req: reconcile._deliver_steers(d, req, config.Config(), _NOTES),
            lambda d, req: reconcile._resume_with_steers(d, req, config.Config()),
        ):
            req = _card(execution={"session_id": "sid-1", "pending_steers": list(_NOTES)})
            registry.save(req)
            d = mock.Mock()
            d.executor.resume = mock.Mock(return_value=True)
            call(d, req)
            self.assertIn("CARD TITLE", d.executor.resume.call_args.kwargs["prompt"])


if __name__ == "__main__":
    unittest.main()
