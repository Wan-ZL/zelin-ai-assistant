"""交付前的中途改名：120 s 探针顺带收割 CARD TITLE（CONTRACT §37.1 追记）。

§37.1 的收割点原本只有轮次边界——用户 attach 进去聊几个小时的交互式会话没有
边界，聊跑偏了卡名也不会动。reconcile 那个每 120 s 一次的探针本来就把
transcript 读回来了，`harvest_delivery` 也本来就连 `card_title` 一起返回：
这里只是把标题当场应用掉，**不**改变任何提升语义（卡仍在执行中）。

两个触点各钉一个 class，因为一条会话只会落在其中一个 roster class 上：
  * `promote_if_delivered`（blocked / 已死待 resume）——「有标题没交付」那条出口；
  * `_note_alive`（working / idle，**issue #331 点名的 attach 长会话就在这里**）
    ——独立的 TITLE_PROBE_AT 节流，只改名、不判 FINAL DRAFT、不动状态机。

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


class AliveSessionTitleProbeTestCase(unittest.TestCase):
    """活着的会话（working / idle）——`_note_alive`，issue #331 的施工点 3。

    这条 roster class 既不受阻也不交付，`promote_if_delivered` 永远探不到它；
    没有这个触点，「用户 attach 进去聊很久」的长会话的卡名就只能等下一个轮次
    边界。节流台账与交付探针分开（TITLE_PROBE_AT），免得改名读把 blocked 那条
    路的 FINAL DRAFT 提升推迟最多 120 s。
    """

    def setUp(self):
        _clean()
        for ledger in (actd._HARVEST_PROBE_AT, actd._TITLE_PROBE_AT):
            ledger.clear()
            self.addCleanup(ledger.clear)
        mock.patch.object(actd, "_log").start()
        self.addCleanup(mock.patch.stopall)

    def _card(self, **kw) -> Requirement:
        base = dict(id="R-901", title="原始的冻结标题", status=State.EXECUTING.value,
                    execution={"session_id": "sid-2"})
        base.update(kw)
        req = Requirement(**base)
        registry.save(req)
        return req

    def _pass(self, state, harvested, resume=None):
        """One reconcile pass against a roster holding exactly one live session."""
        agents = [{"id": "sid", "sessionId": "sid-2", "state": state,
                   "cwd": "/tmp/wt", "name": "R-901 · 原始的冻结标题"}]
        fake = mock.Mock()
        fake.harvest_delivery = mock.Mock(return_value=harvested)
        fake.resume = resume if resume is not None else mock.Mock(return_value=True)
        with mock.patch.object(actd, "_run_claude_agents", return_value=agents), \
             mock.patch.object(actd, "executor", fake):
            actd.reconcile_executing(config.Config(), set())
        return fake

    def test_working_session_gets_renamed_mid_flight(self):
        """施工点 3 的正主：会话还在 working，卡名当场跟上聊天内容。"""
        self._card()
        self._pass("working", {"card_title": "改成整理保险合同"})
        fresh = registry.load("R-901")
        self.assertEqual(fresh.display_title, "改成整理保险合同")
        self.assertEqual(fresh.status, State.EXECUTING.value)

    def test_idle_session_too(self):
        self._card()
        self._pass("idle", {"card_title": "闲着也能改名"})
        self.assertEqual(registry.load("R-901").display_title, "闲着也能改名")

    def test_a_final_draft_on_a_live_session_does_not_promote(self):
        """只改名：活会话的提升仍然只由 blocked / done / dead 三条既有路判。"""
        self._card()
        self._pass("working", {"card_title": "名字", "final_draft": "成品"})
        fresh = registry.load("R-901")
        self.assertEqual(fresh.status, State.EXECUTING.value)
        self.assertEqual(fresh.display_title, "名字")
        self.assertIsNone((fresh.execution or {}).get("final_draft"))

    def test_throttled_to_one_read_per_window(self):
        """每会话 120 s 一次：同一 pass 连跑两次只读一次 transcript。"""
        self._card()
        self._pass("working", {"card_title": "第一次"})
        fake = self._pass("working", {"card_title": "第二次"})
        fake.harvest_delivery.assert_not_called()
        self.assertEqual(registry.load("R-901").display_title, "第一次")

    def test_the_delivery_probe_ledger_is_untouched(self):
        """两本台账分开——活会话这次读不许挡住 blocked 那条路的首次探针。"""
        self._card()
        self._pass("working", {"card_title": "改名"})
        self.assertIn("sid-2", actd._TITLE_PROBE_AT)
        self.assertNotIn("sid-2", actd._HARVEST_PROBE_AT)

    def test_user_pinned_title_still_wins(self):
        self._card(display_title="我起的名字", user_titled=True)
        self._pass("working", {"card_title": "LLM 想改的名字"})
        self.assertEqual(registry.load("R-901").display_title, "我起的名字")

    def test_no_card_title_writes_nothing(self):
        self._card()
        with mock.patch.object(actd.registry, "save") as save:
            self._pass("working", {"delivered_summary": "干到一半"})
        save.assert_not_called()
        self.assertFalse(registry.load("R-901").display_title)


if __name__ == "__main__":
    unittest.main()
