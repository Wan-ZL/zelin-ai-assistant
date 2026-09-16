"""maintenance — dev 列车改动行上的变异幸存体判例（CONTRACT §70.2 追记 / D74）。

一条契约：**待验收老化两阶段之间的闸门是 20 小时**。第一遍盖 `review_stale_notified_at`
戳 + 发整轮一条汇总通知，戳满 20 小时的下一遍才归档。20 是相对 24 选的：循环一天只跑
一次，`20 < 24` 保证「今天通知、明天归档」不被时钟漂移吃掉；调到 21 会让漂移把第二遍
推到后天（通知与归档脱钩），调到 19 则让同一天的第二次触发就能归档（owner 根本没收到过
那条「明天归档」的预告）。所以这里按**字面量**钉边界，不引用模块常量。

Runs entirely inside the sandbox AIASSISTANT_HOME (tests/__init__.py); no LLM.
"""
import datetime as _dt
import unittest

from tests import TMP_HOME  # noqa: F401 - sandbox env before act imports

from act.lib import config, maintenance
from act.lib.registry import Requirement, State

TODAY = _dt.date(2026, 9, 15)
NOW = _dt.datetime(2026, 9, 15, 3, 30, tzinfo=_dt.timezone.utc)
REVIEW_STALE_DAYS = 14


def _days_ago(n: int) -> str:
    return (TODAY - _dt.timedelta(days=n)).isoformat()


def _review(rid, *, notified, age=30):
    """闲置 `age` 天的待验收卡，通知戳盖在 `notified` 那一刻。"""
    stamp = notified.strftime("%Y-%m-%dT%H:%M:%SZ")
    return Requirement(id=rid, title=f"draft {rid} with enough length",
                       status=State.REVIEW.value,
                       sources=[{"channel": "meeting", "date": _days_ago(age), "quote": "q"}],
                       execution={"review_at": _days_ago(age) + "T09:00:00Z",
                                  maintenance.REVIEW_NOTICE_STAMP: stamp})


class ReviewNoticeGateTestCase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()

    def verdict(self, req):
        return maintenance.stale_verdict(req, [req], TODAY, 45, REVIEW_STALE_DAYS, NOW)

    def test_a_stamp_one_minute_short_of_twenty_hours_is_not_archived_yet(self):
        req = _review("P-1", notified=NOW - _dt.timedelta(hours=19, minutes=59))
        self.assertIsNone(self.verdict(req))

    def test_a_stamp_exactly_twenty_hours_old_archives(self):
        req = _review("P-2", notified=NOW - _dt.timedelta(hours=20))
        self.assertEqual(self.verdict(req), maintenance.RULE_REVIEW_STALE)

    def test_a_stamp_a_full_day_old_archives(self):
        req = _review("P-3", notified=NOW - _dt.timedelta(hours=24))
        self.assertEqual(self.verdict(req), maintenance.RULE_REVIEW_STALE)


if __name__ == "__main__":   # pragma: no cover
    unittest.main()
