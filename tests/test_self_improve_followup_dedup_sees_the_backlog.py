"""一张 PR 一天一张跟进卡——跟进卡搬到潜在任务列之后，去重词表必须跟着搬。

契约：CONTRACT **§78** / **§78.3**（§65 PR 跟进卡那一行：落点改 ``detected``
**并且** ``_OPEN_STATUSES`` 必须同车加上 ``DETECTED``）/ §65.5（巡检：owner 评论
或红 required check → 跟进卡）/ §65.6。

§78.3 把这一行点名为**第二危险**的一条：落点改了、去重词表没改，症状是
「同一张 PR 每天被重铸一张跟进卡，还每张响一次通知」——功能看起来全好，只是
每天多一张重复卡，而 CI 全绿。所以这里钉的不是「跟进卡生在 detected」这一句
（那由产地网 tests/test_machine_cards_are_born_in_the_backlog.py 管），而是
**去重判据看不看得见新落点**：

- 跟进卡躺在 ``detected`` 里 → 第二天有新评论也不铸第二张；
- 把它扔进回收站（不再「有人在跟」）→ 第二天照常铸新的一张；
- 退役的 ``card_sent`` 上的存量跟进卡同样算「有人在跟」（add-only 容忍）。

沙箱 AIASSISTANT_HOME；gh 走 FakeGh 注入缝，notify mock——零子进程、零网络。
"""
import datetime as _dt
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env before act imports
from tests.self_improve_testkit import FakeGh, lane_card, pr_doc

from act.lib import config, notify, registry, self_improve
from act.lib.registry import State

BRANCH = "ai/self-improve/R-900"
PR = 447
NOW = _dt.datetime(2026, 9, 26, 10, 0, tzinfo=_dt.timezone.utc)
TOMORROW = NOW + _dt.timedelta(days=1)


def _comment(body, login="Wan-ZL", at="2026-09-26T09:00:00Z"):
    return {"author": {"login": login}, "body": body, "createdAt": at,
            "url": f"https://github.com/o/r/pull/{PR}#c1"}


class FollowupDedupTestCase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()
        for name in ("lane.json", "rejected.jsonl"):
            (self_improve.state_dir() / name).unlink(missing_ok=True)
        self.notify = mock.patch.object(notify, "notify").start()
        self.addCleanup(mock.patch.stopall)
        self.cfg = config.Config(self_improve_enabled=True)
        # 已交付的 lane 卡 = 巡检的入口（它带着 PR 坐标）
        delivery = {"verified": True, "reason": None, "pr_number": PR,
                    "pr_url": f"https://github.com/o/r/pull/{PR}", "branch": BRANCH}
        registry.save(lane_card("P-7", status=State.REVIEW.value,
                                execution={"session_id": "aaaa1111", "done": True,
                                           "delivery": delivery}))
        self.gh = FakeGh({PR: pr_doc(number=PR, branch=BRANCH)},
                         comments={PR: [_comment("补个测试")]})

    def _tick(self, now=NOW):
        return self_improve.tick(self.cfg, gh=self.gh, now=now, force=True)

    def _followups(self):
        return sorted(r.id for r in registry.load_all()
                      if (self_improve.pr_source(r) or {}).get("pr_number") == PR
                      and r.id != "P-7")

    def test_the_first_tick_mints_exactly_one_followup_in_the_backlog(self):
        minted = self._tick()["followups"]
        self.assertEqual(len(minted), 1)
        card = registry.load(minted[0])
        self.assertEqual(card.status, State.DETECTED.value)
        self.assertEqual(self._followups(), [card.id])

    def test_a_second_tick_the_same_day_mints_nothing(self):
        first = self._tick()["followups"]
        self.gh.comments[PR].append(_comment("还有一处", at="2026-09-26T09:30:00Z"))
        self.assertEqual(self._tick(now=NOW + _dt.timedelta(hours=2))["followups"], [])
        self.assertEqual(self._followups(), first)

    def test_the_next_day_mints_nothing_while_the_card_sits_in_detected(self):
        """**本文件的主判例**：跟进卡住在潜在任务列 = 这张 PR 已经有人在跟。

        去重词表里少了 ``detected``，这一条就变成「每天一张重复卡」——而且
        每张还响一次通知。"""
        first = self._tick()["followups"]
        self.assertEqual(registry.load(first[0]).status, State.DETECTED.value)
        self.gh.comments[PR].append(_comment("第二天又说了一句",
                                             at="2026-09-27T09:00:00Z"))
        self.assertEqual(self._tick(now=TOMORROW)["followups"], [])
        self.assertEqual(self._followups(), first)

    def test_a_third_day_still_mints_nothing(self):
        """不是「隔一天」的节流——只要卡还在那一列，多少天都不再铸。"""
        first = self._tick()["followups"]
        for day in (1, 2, 3):
            self.gh.comments[PR].append(
                _comment(f"第 {day} 天", at=f"2026-09-2{6 + day}T09:00:00Z"))
            self.assertEqual(
                self._tick(now=NOW + _dt.timedelta(days=day))["followups"], [])
        self.assertEqual(self._followups(), first)

    def test_disposing_of_the_card_lets_the_next_day_mint_again(self):
        """去重判据是**状态**，不是「这张 PR 永远只铸一次」：卡被扔掉 = 没人
        在跟了，新评论照常开新卡。"""
        first = self._tick()["followups"]
        registry.trash(registry.load(first[0]), "deleted")
        self.gh.comments[PR].append(_comment("扔了之后又说了一句",
                                             at="2026-09-27T09:00:00Z"))
        second = self._tick(now=TOMORROW)["followups"]
        self.assertEqual(len(second), 1)
        self.assertNotEqual(second, first)
        self.assertEqual(registry.load(second[0]).status, State.DETECTED.value)

    def test_an_open_followup_in_any_live_state_blocks_a_second_one(self):
        """整份 ``_OPEN_STATUSES`` 词表逐个走一遍——含退役的 ``card_sent``
        （add-only 容忍：存量跟进卡同样算「有人在跟」）。"""
        first = self._tick()["followups"][0]
        for i, status in enumerate((State.DETECTED.value, State.CARD_SENT.value,
                                    State.RAISING.value, State.APPROVED.value,
                                    State.EXECUTING.value), start=1):
            with self.subTest(status=status):
                card = registry.load(first)
                card.status = status
                registry.upsert(card)
                self.gh.comments[PR].append(
                    _comment(f"第 {i} 声", at=f"2026-10-0{i}T09:00:00Z"))
                self.assertEqual(
                    self._tick(now=NOW + _dt.timedelta(days=10 + i))["followups"], [])
                self.assertEqual(self._followups(), [first])
