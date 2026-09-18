"""§70.2 追记 / D74（issue #312）——待验收列的两阶段老化：先一条汇总通知，再归档。

第一遍：闲置 ≥ `daily_loop.review_stale_days` 的待验收卡盖 add-only 执行戳
`review_stale_notified_at`，整轮**一条**汇总通知（§70.6 追记）；戳不算活动，闲置
天数不因此清零。第二遍（戳满 20 小时）：`registry.trash(req, "stale:review_stale")`
——prev_status=review、可恢复、循环卡 90 天保留期。待验收卡**只**见这一条规则：
deadline_passed / diagnostic_expired / superseded / idle 仍只认提案与潜在任务两列。
戳**只对「说完之后没再动过」的卡算数**（§70.2 追记二第 4 条）：打回→重新交付回到
待验收、或 owner 从回收站捞回来（`registry.restore` 盖 `restored_at`）之后，那枚旧
戳作废，老化从第一阶段重新走——否则一张卡一生只被通知一次，第二轮起无声归档。
Runs entirely inside the sandbox AIASSISTANT_HOME (tests/__init__.py).
"""
import datetime as _dt
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env before act imports

from act.lib import config, maintenance, registry
from act.lib.registry import Requirement, State

TODAY = _dt.date(2026, 9, 15)
NOW = _dt.datetime(2026, 9, 15, 3, 30, tzinfo=_dt.timezone.utc)


def _iso(dt: _dt.datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _days_ago(n: int) -> str:
    return (TODAY - _dt.timedelta(days=n)).isoformat()


def _review(rid, *, age=30, status=State.REVIEW.value, notified=None, **kw):
    """一张闲置 `age` 天的待验收卡；`notified` = 已盖的通知戳（datetime 或字面量）。"""
    execution = dict(kw.pop("execution", None) or {})
    execution.setdefault("review_at", _days_ago(age) + "T09:00:00Z")
    if notified is not None:
        execution[maintenance.REVIEW_NOTICE_STAMP] = (
            _iso(notified) if isinstance(notified, _dt.datetime) else notified)
    fields = dict(id=rid, title=f"draft {rid} with enough length", status=status,
                  sources=[{"channel": "meeting", "date": _days_ago(age), "quote": "q"}],
                  execution=execution)
    fields.update(kw)
    return Requirement(**fields)


class _Notifier:
    """注入缝（防腐 #3：参数注入，绝不 module-global）——记下每一次调用。"""

    def __init__(self):
        self.calls = []

    def __call__(self, title, body, *a, **kw):
        self.calls.append((title, body))
        return True


class _Case(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()
        self.cfg = config.Config()   # review_stale_days 出厂 14

    def notices(self, notifier=None, now=NOW, today=TODAY):
        return maintenance.sweep_review_notices(self.cfg, today=today, now=now,
                                                notifier=notifier or _Notifier())

    def sweep(self, now=NOW, today=TODAY):
        return maintenance.sweep_stale(self.cfg, today=today, now=now)


class TwoPassTestCase(_Case):
    def test_first_pass_only_stamps_and_says_it_once(self):
        for rid in ("P-1", "P-2", "P-3"):
            registry.save(_review(rid, age=30))
        notifier = _Notifier()
        rows = self.notices(notifier)

        self.assertEqual(sorted(r["id"] for r in rows), ["P-1", "P-2", "P-3"])
        # 一轮一条横幅，不是一卡一条（宪法第 10 条；owner 的板上有 19 张）
        self.assertEqual(len(notifier.calls), 1)
        title, body = notifier.calls[0]
        self.assertIn("3", title)
        self.assertIn("14", body)
        for rid in ("P-1", "P-2", "P-3"):
            req = registry.load(rid)
            self.assertEqual(req.status, State.REVIEW.value)      # 第一遍不动状态
            self.assertTrue(req.execution.get(maintenance.REVIEW_NOTICE_STAMP))
        self.assertEqual(self.sweep(), [])                        # 同一轮里不归档

    def test_second_pass_trashes_with_the_rule_token_and_stays_restorable(self):
        registry.save(_review("P-1", age=30, notified=NOW - _dt.timedelta(hours=21)))
        out = self.sweep()

        self.assertEqual(out, [{"id": "P-1", "rule": "review_stale", "display_id": "P-1"}])
        req = registry.load("P-1")
        self.assertEqual(req.status, State.TRASHED.value)
        self.assertEqual(req.trash_reason, "stale:review_stale")
        self.assertEqual(req.prev_status, State.REVIEW.value)
        self.assertEqual(maintenance.retention_days(req, self.cfg), 90)   # 循环卡的长保留期
        self.assertEqual(registry.restore(registry.load("P-1")).status, State.REVIEW.value)

    def test_a_young_stamp_is_not_yet_an_archive(self):
        registry.save(_review("P-1", age=30, notified=NOW - _dt.timedelta(hours=19)))
        self.assertEqual(self.sweep(), [])
        self.assertEqual(registry.load("P-1").status, State.REVIEW.value)

    def test_the_stamp_is_not_activity(self):
        """盖戳不许把闲置天数清零——否则第二遍永远到不了。"""
        req = _review("P-1", age=30)
        before = maintenance.last_activity(req)
        registry.save(req)
        self.notices()
        after = registry.load("P-1")
        self.assertEqual(maintenance.last_activity(after), before)
        self.assertNotIn(maintenance.REVIEW_NOTICE_STAMP, maintenance._EXECUTION_STAMPS)

    def test_a_notified_card_is_never_notified_twice(self):
        registry.save(_review("P-1", age=30, notified=NOW - _dt.timedelta(hours=2)))
        notifier = _Notifier()
        self.assertEqual(self.notices(notifier), [])
        self.assertEqual(notifier.calls, [])

    def test_an_unreadable_stamp_leaves_the_card_alone(self):
        """拿不准就不动：既不重盖（会永远重置 20 小时闸）也不归档（时刻读不懂）。"""
        registry.save(_review("P-1", age=30, notified="someday"))
        notifier = _Notifier()
        self.assertEqual(self.notices(notifier), [])
        self.assertEqual(notifier.calls, [])
        self.assertEqual(self.sweep(), [])
        self.assertEqual(registry.load("P-1").status, State.REVIEW.value)

    def test_nothing_to_say_is_silence(self):
        registry.save(_review("P-1", age=3))
        notifier = _Notifier()
        self.assertEqual(self.notices(notifier), [])
        self.assertEqual(notifier.calls, [])

    def test_the_defaults_read_the_registry_and_the_wall_clock(self):
        """不给 today / now / reqs 时读 registry + 真时钟（actd 之外的调用方走这条）。
        卡面的日期从**真 now** 派生——写死的日期会在它自己的第二天变成必红。"""
        real_stamp = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
        registry.save(Requirement(id="P-1", title="a draft old enough to age out",
                                  status=State.REVIEW.value,
                                  sources=[{"channel": "meeting", "date": real_stamp, "quote": "q"}],
                                  execution={"review_at": real_stamp}))
        notifier = _Notifier()
        self.assertEqual([r["id"] for r in maintenance.sweep_review_notices(self.cfg, notifier=notifier)],
                         ["P-1"])
        self.assertEqual(len(notifier.calls), 1)


class TheStampIsRearmedByLaterActivityTestCase(_Case):
    """第二轮起也必须「先说再做」：戳比最近活动旧 = 当没盖过（§70.2 追记二第 4 条）。"""

    def test_a_reworked_card_gets_a_fresh_notice_before_the_next_archive(self):
        """30 天前盖过戳 → 打回重做 → 20 天前重新交付回到待验收（戳之后的活动）：
        这一轮只许重新盖戳 + 通知，再下一轮才轮到归档。"""
        # age=20 → review_at 与来源日期都在 20 天前；戳比它们老 10 天
        registry.save(_review("P-1", age=20, notified=NOW - _dt.timedelta(days=30)))

        self.assertEqual(self.sweep(), [])              # 旧戳不算数 → 不归档
        notifier = _Notifier()
        self.assertEqual([r["id"] for r in self.notices(notifier)], ["P-1"])
        self.assertEqual(len(notifier.calls), 1)        # 说了第二次
        fresh = registry.load("P-1").execution[maintenance.REVIEW_NOTICE_STAMP]
        self.assertEqual(fresh, _iso(NOW))

        later = NOW + _dt.timedelta(hours=21)           # 新戳满 20 小时的那一轮
        self.assertEqual([r["id"] for r in self.sweep(now=later,
                                                      today=later.date())], ["P-1"])

    def test_a_restored_card_is_not_re_trashed_by_the_next_two_passes(self):
        """恢复即回待验收（宪法第 2 条）——捞回来算 owner 的活动，整个窗口重新起算。
        少了这一条，`restore` 在第二天就被同一条规则无声撤销。"""
        registry.save(_review("P-1", age=30, notified=NOW - _dt.timedelta(hours=21)))
        self.assertEqual([r["id"] for r in self.sweep()], ["P-1"])
        restored = registry.restore(registry.load("P-1"), now=NOW)   # 戳用注入时钟
        self.assertEqual(restored.status, State.REVIEW.value)
        self.assertTrue(restored.execution.get("restored_at"))   # 捞回来 = 活动
        self.assertIn("restored_at", maintenance._EXECUTION_STAMPS)

        for day in (1, 2):
            later = NOW + _dt.timedelta(days=day)
            notifier = _Notifier()
            with self.subTest(day=day):
                self.assertEqual(self.notices(notifier, now=later, today=later.date()), [])
                self.assertEqual(notifier.calls, [])
                self.assertEqual(self.sweep(now=later, today=later.date()), [])
                self.assertEqual(registry.load("P-1").status, State.REVIEW.value)

    def test_the_window_restarts_from_the_restore_not_from_the_old_stamp(self):
        """满窗之后才又轮到第一阶段——而且仍然是先通知，不是直接归档。"""
        registry.save(_review("P-1", age=30, notified=NOW - _dt.timedelta(hours=21)))
        self.sweep()
        registry.restore(registry.load("P-1"), now=NOW)   # 戳用注入时钟，判例不随真日期漂

        later = NOW + _dt.timedelta(days=15)
        notifier = _Notifier()
        self.assertEqual(self.sweep(now=later, today=later.date()), [])
        self.assertEqual([r["id"] for r in self.notices(notifier, now=later,
                                                        today=later.date())], ["P-1"])
        self.assertEqual(len(notifier.calls), 1)


class NothingCrashesTheRoundTestCase(_Case):
    """宪法第 11 条：一张坏卡 / 一条发不出去的通知，都只丢它自己。"""

    def test_a_card_that_blows_up_the_verdict_is_skipped(self):
        registry.save(_review("P-1", age=30))
        registry.save(_review("P-2", age=30))
        with mock.patch.object(maintenance, "_needs_review_notice",
                               side_effect=[RuntimeError("bad card"), True]):
            rows = self.notices()
        self.assertEqual([r["id"] for r in rows], ["P-2"])

    def test_a_card_that_cannot_be_saved_is_dropped_not_fatal(self):
        registry.save(_review("P-1", age=30))
        registry.save(_review("P-2", age=30))
        with mock.patch.object(registry, "save", side_effect=[OSError("disk"), None]):
            rows = self.notices()
        self.assertEqual([r["id"] for r in rows], ["P-2"])

    def test_a_notifier_that_raises_does_not_roll_back_the_stamp(self):
        """闸门是戳不是横幅（§70.6 追记）：通知发不出去，第二天照常归档。"""
        registry.save(_review("P-1", age=30))

        def boom(*_a, **_kw):
            raise RuntimeError("queue unwritable")

        self.assertEqual([r["id"] for r in self.notices(boom)], ["P-1"])
        self.assertTrue(registry.load("P-1").execution.get(maintenance.REVIEW_NOTICE_STAMP))


class GuardsTestCase(_Case):
    def verdict(self, req, others=(), *, review_days=14, now=NOW):
        return maintenance.stale_verdict(req, [req, *others], TODAY, 45, review_days, now)

    def _ripe(self, rid, **kw):
        kw.setdefault("age", 30)
        return _review(rid, notified=NOW - _dt.timedelta(hours=21), **kw)

    def test_within_the_window_is_untouched(self):
        self.assertIsNone(self.verdict(self._ripe("P-1", age=13)))
        self.assertEqual(self.verdict(self._ripe("P-2", age=14)), "review_stale")

    def test_zero_days_turns_the_rule_off(self):
        self.assertIsNone(self.verdict(self._ripe("P-1"), review_days=0))

    def test_owner_touched_cards_are_never_aged(self):
        self.assertIsNone(self.verdict(self._ripe("P-1", user_titled=True, display_title="x")))
        self.assertIsNone(self.verdict(self._ripe("P-2", preset="proposals_triage")))
        self.assertIsNone(self.verdict(self._ripe("P-3", deadline=(TODAY + _dt.timedelta(days=3)).isoformat())))

    def test_a_running_sibling_protects_but_a_review_twin_does_not(self):
        """两张同 thread 的待验收卡曾经互相保护——那让 review_stale 对线程孪生永不生效。"""
        running = _review("P-9", age=1, status=State.EXECUTING.value, thread_id="T-1")
        self.assertIsNone(self.verdict(self._ripe("P-1", thread_id="T-1"), [running]))
        twin = self._ripe("P-2", thread_id="T-2")
        self.assertEqual(self.verdict(self._ripe("P-3", thread_id="T-2"), [twin]), "review_stale")

    def test_unparseable_activity_is_left_alone(self):
        req = self._ripe("P-1", sources=[], execution={"review_at": "someday"})
        self.assertIsNone(self.verdict(req))


class LaneSplitTestCase(_Case):
    """待验收卡**只**见 review_stale：四条老规则仍绑在提案 / 潜在任务两列上。"""

    def verdict(self, req, others=()):
        return maintenance.stale_verdict(req, [req, *others], TODAY, 45, 14, NOW)

    def test_a_passed_deadline_does_not_archive_a_review_card_without_the_notice(self):
        past = (TODAY - _dt.timedelta(days=30)).isoformat()
        self.assertIsNone(self.verdict(_review("P-1", age=8, deadline=past)))
        # 同一张卡若在提案列，deadline_passed 七天就判了——差别正是这条闸门
        self.assertEqual(self.verdict(_review("P-2", age=8, deadline=past,
                                              status=State.CARD_SENT.value)), "deadline_passed")

    def test_a_delivered_twin_does_not_supersede_a_review_card(self):
        done = _review("P-9", age=1, title="ship the weekly export job",
                       status=State.DELIVERED.value)
        req = _review("P-1", age=3, title="ship the weekly export job")
        self.assertIsNone(self.verdict(req, [done]))

    def test_diagnostic_and_idle_rules_stay_off_the_review_lane(self):
        diag = _review("P-1", age=30, sources=[{"channel": "radar-diagnostic",
                                                "date": _days_ago(30)}])
        self.assertIsNone(self.verdict(diag))            # 没盖戳 = 第一遍还没走
        self.assertIsNone(self.verdict(_review("P-2", age=400)))


class AuditRowTestCase(_Case):
    def test_the_rule_lands_in_the_daily_loop_audit(self):
        """issue #312 第 1 条：`daily_loop` 的 trashed 审计里要认得出 review_stale。"""
        from act.lib import daily_loop

        registry.save(_review("P-1", age=30, notified=NOW - _dt.timedelta(hours=21)))
        registry.save(_review("P-2", age=30))
        result = daily_loop.run(self.cfg, now=NOW, gh=lambda args: "", doctor=lambda: [])

        self.assertEqual([row["rule"] for row in result["trashed_cards"]], ["review_stale"])
        self.assertEqual([row["id"] for row in result["review_notices"]], ["P-2"])
        self.assertEqual(result["trashed"], 1)


if __name__ == "__main__":
    unittest.main()
