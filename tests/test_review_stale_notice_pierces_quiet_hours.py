"""§70.6 追记 / §28 追记（issue #312 / D74）：归档前那一次告知穿透安静时段。

两阶段老化（§70.2 追记二）承诺「归档前发一次通知」，而发它的每日整理**出厂就在
03:30 跑**（`config.DEFAULT_DAILY_LOOP_TIME`），正落在出厂安静窗 22:00 → 08:00
（`quiet_hours_start` / `quiet_hours_end`）里。所以如果这条通知守安静时段：owner
勾上一个复选框，横幅从此每一轮都被写方吃掉，而闸门是戳不是横幅——卡照样在第二天
被归档。那样 issue #312 的「归档前发一次通知」在这类安装上**永远**不成立。

钉的行为：
  - `sweep_review_notices` 发的那一条打 `notify.KIND_REVIEW_STALE`；
  - 该 kind 在 `QUIET_HOURS_EXEMPT` 里：安静窗正中（含出厂 03:30）照发；
  - 它没有分类开关（不在 `CATEGORY_PREFERENCE`）——要静音就把规则整条关掉
    （`review_stale_days = 0`，那时卡也不再被归档）；
  - 新 kind 同 PR 登记进 `server/notify_catalog.KINDS`（§66.2 探针的判卷面），
    help 是纯文本（设置页渲成一个文本节点，Markdown `**` 会被逐字看见）。
Runs entirely inside the sandbox AIASSISTANT_HOME (tests/__init__.py).
"""
import datetime as _dt
import time
import unittest

from tests import TMP_HOME  # noqa: F401 - sandbox env before act imports

from act.lib import config, maintenance, notify, registry
from act.lib.registry import Requirement, State
from server import notify_catalog

TODAY = _dt.date(2026, 9, 15)
NOW = _dt.datetime(2026, 9, 15, 3, 30, tzinfo=_dt.timezone.utc)


def _cfg(**kw) -> config.Config:
    cfg = config.Config()
    for key, value in kw.items():
        setattr(cfg, key, value)
    return cfg


def _at(hour: int, minute: int = 0) -> time.struct_time:
    return time.struct_time((2026, 9, 15, hour, minute, 0, 0, 258, -1))


_QUIET = _cfg(quiet_hours_enabled=True)   # 出厂窗 22:00 → 08:00


class NoticePiercesQuietHoursTestCase(unittest.TestCase):
    def test_the_loop_default_hour_is_inside_the_default_quiet_window(self):
        """前提本身要成立：不比字面量，比三个常量。"""
        hour, minute = (int(x) for x in config.DEFAULT_DAILY_LOOP_TIME.split(":"))
        self.assertTrue(notify.in_quiet_hours(_QUIET.quiet_hours_start,
                                              _QUIET.quiet_hours_end,
                                              hour * 60 + minute))

    def test_the_notice_is_exempt_at_every_hour_in_the_window(self):
        for hour in (22, 23, 0, 3, 7):
            with self.subTest(hour=hour):
                self.assertIsNone(notify.suppression_reason(
                    notify.KIND_REVIEW_STALE, _QUIET, now=_at(hour)))
        # 对照：无 kind 的同一时刻被吃掉——本 PR 之前这条通知就是无 kind 的
        self.assertEqual(notify.suppression_reason(None, _QUIET, now=_at(3, 30)),
                         "quiet_hours")

    def test_it_has_no_category_switch_of_its_own(self):
        self.assertNotIn(notify.KIND_REVIEW_STALE, notify.CATEGORY_PREFERENCE)
        cfg = _cfg(quiet_hours_enabled=True, notify_failures=False,
                   notify_proposals=False, notify_needs_input=False)
        self.assertIsNone(notify.suppression_reason(
            notify.KIND_REVIEW_STALE, cfg, now=_at(3, 30)))


class WriterTagsTheKindTestCase(unittest.TestCase):
    """写方半边：第一阶段发的那一条真打上了这个 kind（抑制逻辑的格子在上面）。"""

    def setUp(self):
        config.ensure_state_dirs()
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()
        self.calls = []

    def _notifier(self, title, body, *a, **kw):
        self.calls.append((title, body, kw.get("kind")))
        return True

    def test_the_aggregated_notice_carries_the_review_stale_kind(self):
        old = (TODAY - _dt.timedelta(days=30)).isoformat()
        registry.save(Requirement(id="P-1", title="a draft old enough to age out",
                                  status=State.REVIEW.value,
                                  sources=[{"channel": "meeting", "date": old, "quote": "q"}],
                                  execution={"review_at": old + "T09:00:00Z"}))
        rows = maintenance.sweep_review_notices(_cfg(), today=TODAY, now=NOW,
                                               notifier=self._notifier)
        self.assertEqual([r["id"] for r in rows], ["P-1"])
        self.assertEqual([kind for _t, _b, kind in self.calls], [notify.KIND_REVIEW_STALE])


class CatalogRegistrationTestCase(unittest.TestCase):
    def test_the_kind_is_in_the_vocabulary_without_a_preference(self):
        entry = next(k for k in notify_catalog.KINDS
                     if k["kind"] == notify.KIND_REVIEW_STALE)
        self.assertIsNone(entry["preference"])
        for half in ("title", "help"):
            self.assertTrue(entry[half]["zh"] and entry[half]["en"], half)
        for lang, sentence in entry["help"].items():
            with self.subTest(lang=lang):
                self.assertNotIn("**", sentence)

    def test_every_exempt_kind_is_registered(self):
        names = notify_catalog.kind_names()
        for kind in notify.QUIET_HOURS_EXEMPT:
            with self.subTest(kind=kind):
                self.assertIn(kind, names)


if __name__ == "__main__":
    unittest.main()
