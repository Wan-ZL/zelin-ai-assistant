"""§9 追记 / D73 —— `registry.restore` 的时钟是注入缝，不许是墙上钟。

`restore` 盖 `execution.restored_at`，而它是 `maintenance._EXECUTION_STAMPS` 之一
（= 「最近一次活动」），待验收两阶段老化（§70.2 追记二第 4 条）正是从这枚戳量
`daily_loop.review_stale_days` 天的窗口。戳与窗口一旦不同源——戳走墙上钟、窗口走
判例注入的钟——判例就随真日期漂：2026-09-17 那天 `test_review_stale_sweep` 因此在
每个分支上自己变红（闲置从 15 天算成 13 天），修复是 ad4f0b71 给 `restore` 加 `now=`。
那次只补了实例，没留判例；这一条把缝本身钉住——缝被删、或戳又回到墙上钟，下面三条
里至少一条立刻红，而且红得与今天是哪天无关（第三条把同一结论在三个纪元上各验一次）。
Runs entirely inside the sandbox AIASSISTANT_HOME (tests/__init__.py).
"""
import datetime as _dt
import unittest

from tests import TMP_HOME  # noqa: F401 - sandbox env before act imports

from act.lib import config, maintenance, registry
from act.lib.registry import Requirement, State

# 三个纪元：远古 / 炸弹当初用的那个 / 远未来。结论必须三处相同——任何一处的
# 「现在」偷偷来自墙上钟，三条里至少一条的算术就崩。
EPOCHS = (
    _dt.datetime(2020, 1, 15, 3, 30, tzinfo=_dt.timezone.utc),
    _dt.datetime(2026, 9, 15, 3, 30, tzinfo=_dt.timezone.utc),
    _dt.datetime(2099, 6, 1, 12, 0, tzinfo=_dt.timezone.utc),
)


def _iso(dt: _dt.datetime) -> str:
    return dt.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _review_card(rid: str, epoch: _dt.datetime, *, age: int, notified: _dt.datetime):
    """一张相对 `epoch` 闲置 `age` 天的待验收卡，戳 = `notified`。

    卡上每个日期都从 `epoch` 派生，卡本身没有一处墙上钟——所以第三条测出来的漂移
    只可能来自 `restore` 的戳。
    """
    day = (epoch - _dt.timedelta(days=age)).date().isoformat()
    return Requirement(
        id=rid,
        title=f"draft {rid} with enough length",
        status=State.REVIEW.value,
        sources=[{"channel": "meeting", "date": day, "quote": "q"}],
        execution={"review_at": day + "T09:00:00Z",
                   maintenance.REVIEW_NOTICE_STAMP: _iso(notified)},
    )


class _Notifier:
    """注入缝（防腐 #3：参数注入，绝不 module-global）。"""

    def __init__(self):
        self.calls = []

    def __call__(self, title, body, *a, **kw):
        self.calls.append((title, body))
        return True


class TheRestoreClockIsInjectableTestCase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()
        self.cfg = config.Config()   # review_stale_days 出厂 14

    def _trashed(self, rid: str, epoch: _dt.datetime):
        """把一张卡走到回收站里，好让 `restore` 有事可做。"""
        registry.save(_review_card(rid, epoch, age=30,
                                   notified=epoch - _dt.timedelta(hours=21)))
        self.assertEqual([r["id"] for r in maintenance.sweep_stale(
            self.cfg, today=epoch.date(), now=epoch)], [rid])
        return registry.load(rid)

    def test_the_stamp_is_the_injected_instant_not_the_wall_clock(self):
        """给了 `now=` 就必须逐字用它——而且带偏移的时刻要归一到 UTC 的 Z 形。"""
        when = _dt.datetime(2020, 1, 15, 3, 30, tzinfo=_dt.timezone.utc)
        restored = registry.restore(self._trashed("P-1", EPOCHS[0]), now=when)
        self.assertEqual(restored.execution["restored_at"], "2020-01-15T03:30:00Z")
        self.assertEqual(registry.load("P-1").execution["restored_at"],
                         "2020-01-15T03:30:00Z")   # 落盘的也是它，不是墙上钟

        # 同一时刻换个偏移写：戳必须仍是那个 UTC 瞬间（`astimezone` 那一步）。
        east8 = _dt.timezone(_dt.timedelta(hours=8))
        again = registry.restore(self._trashed("P-2", EPOCHS[0]),
                                 now=when.astimezone(east8))
        self.assertEqual(again.execution["restored_at"], "2020-01-15T03:30:00Z")

    def test_no_clock_still_means_the_wall_clock(self):
        """缝是 add-only 的可选参数：不给时行为与加缝之前逐字一致（现有调用方一个没改）。"""
        before = _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0)
        restored = registry.restore(self._trashed("P-1", EPOCHS[1]))
        after = _dt.datetime.now(_dt.timezone.utc)

        stamp = restored.execution["restored_at"]
        parsed = _dt.datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=_dt.timezone.utc)
        self.assertGreaterEqual(parsed, before)
        self.assertLessEqual(parsed, after)

    def test_the_review_window_after_a_restore_does_not_drift_with_the_wall_clock(self):
        """恢复后满 15 天那一轮，仍是「先通知、不直接归档」——三个纪元同一结论。

        这就是 2026-09-17 那颗炸弹的判例化：戳若回到墙上钟，远古纪元那一轮立刻算出
        负的闲置天数（戳在 `later` 之后），断言当场红——与今天是哪天无关。
        """
        for epoch in EPOCHS:
            with self.subTest(epoch=epoch.date().isoformat()):
                self.setUp()
                registry.restore(self._trashed("P-1", epoch), now=epoch)
                self.assertEqual(registry.load("P-1").status, State.REVIEW.value)

                later = epoch + _dt.timedelta(days=15)
                notifier = _Notifier()
                # 第二阶段还没轮到（新的活动作废了旧的通知戳）……
                self.assertEqual(maintenance.sweep_stale(
                    self.cfg, today=later.date(), now=later), [])
                # ……而第一阶段该说话了。
                self.assertEqual([r["id"] for r in maintenance.sweep_review_notices(
                    self.cfg, today=later.date(), now=later, notifier=notifier)], ["P-1"])
                self.assertEqual(len(notifier.calls), 1)


if __name__ == "__main__":
    unittest.main()
