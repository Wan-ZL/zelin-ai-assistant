"""交付前的中途改名：FINAL DRAFT 探针顺带收割 CARD TITLE（CONTRACT §37.1 追记）。

§37.1 的收割点原本只有轮次边界——用户 attach 进去聊几个小时的交互式会话没有
边界，聊跑偏了卡名也不会动。reconcile 那个每 120 s 一次的 FINAL DRAFT 探针
（`promote_if_delivered`）本来就把 transcript 读回来了，`harvest_delivery` 也
本来就连 `card_title` 一起返回：这里只是在「还没交付」的那条出口上把标题应用掉，
**不**改变提升语义（仍返回 False、卡仍在执行中）。

落笔仍走唯一那一支（`apply_harvest_title` → `registry.set_display_title`）：
user_titled 钦定优先、same-value no-op、掩码拒收，所以重复探到同名不写盘。
"""
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env before act imports

from act import actd
from act.lib import config, registry
from act.lib.registry import Requirement, State


def _clean():
    config.ensure_state_dirs()
    for p in config.REGISTRY_DIR.glob("*.yaml"):
        p.unlink()


class TitleProbeTestCase(unittest.TestCase):
    def setUp(self):
        _clean()
        actd._HARVEST_PROBE_AT.clear()
        self.addCleanup(actd._HARVEST_PROBE_AT.clear)
        mock.patch.object(actd, "_log").start()
        self.addCleanup(mock.patch.stopall)

    def _card(self, **kw) -> Requirement:
        base = dict(id="R-900", title="原始的冻结标题", status=State.EXECUTING.value,
                    execution={"session_id": "sid-1"})
        base.update(kw)
        req = Requirement(**base)
        registry.save(req)
        return req

    def _probe(self, req, harvested):
        fake = mock.Mock()
        fake.harvest_delivery = mock.Mock(return_value=harvested)
        with mock.patch.object(actd, "executor", fake):
            return actd._promote_if_delivered(req, dict(req.execution), "sid-1")

    def test_card_title_without_a_final_draft_renames_the_card_mid_flight(self):
        req = self._card()
        self.assertFalse(self._probe(req, {"card_title": "改成整理保险合同"}))
        self.assertEqual(registry.load("R-900").display_title, "改成整理保险合同")
        self.assertEqual(registry.load("R-900").status, State.EXECUTING.value)

    def test_same_title_twice_does_not_write_again(self):
        """same-value no-op：每 120 s 一次的探针不许把注册表写穿、不污染曾用名。"""
        req = self._card(display_title="已经是这个名字")
        with mock.patch.object(actd.registry, "save") as save:
            self.assertFalse(self._probe(req, {"card_title": "已经是这个名字"}))
        save.assert_not_called()
        self.assertEqual(registry.load("R-900").former_titles or [], [])

    def test_user_pinned_title_still_wins(self):
        req = self._card(display_title="我起的名字", user_titled=True)
        self.assertFalse(self._probe(req, {"card_title": "LLM 想改的名字"}))
        self.assertEqual(registry.load("R-900").display_title, "我起的名字")

    def test_no_card_title_changes_nothing(self):
        req = self._card()
        with mock.patch.object(actd.registry, "save") as save:
            self.assertFalse(self._probe(req, {"delivered_summary": "干到一半"}))
        save.assert_not_called()
        self.assertFalse(registry.load("R-900").display_title)

    def test_a_delivered_round_still_promotes(self):
        """带 FINAL DRAFT 的那条出口不变：照旧提升进待验收并应用标题。"""
        req = self._card()
        self.assertTrue(self._probe(req, {"card_title": "交付时的名字",
                                          "final_draft": "成品"}))
        fresh = registry.load("R-900")
        self.assertEqual(fresh.status, State.REVIEW.value)
        self.assertEqual(fresh.display_title, "交付时的名字")


if __name__ == "__main__":
    unittest.main()
