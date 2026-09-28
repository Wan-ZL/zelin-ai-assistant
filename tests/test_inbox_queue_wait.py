"""§80.4 先测量：每笔 owner 动作的排队秒数（点下 -> 被 drain）落账。

issue #450 的 Direction 第一条是「Measure first」。这一段是全链路里最贵、又
从来没人记过的一跳：`ts` 是 server 落 inbox 文件时盖的（owner 点下那一刻），
`approved_at` 之类的执行戳盖的是**被 drain 那一刻**——两个数一直都在，只是
从没有人把它们相减。§80.1 早醒之前这个差的期望值 = interval/2、最坏 =
interval；早醒之后应当塌到一次轮询粒度。

判例：
  - 口径：`ts` -> now 的秒数，`queue_wait_s` 是唯一真源；
  - 负数夹到 0：`ts` 由另一个进程盖，时钟差一秒不许算出「排队了 -1 秒」
    （宪法第 3 条诚实报告）；
  - 没有 `ts` / 解析不动 -> None，什么都不记（绝不瞎编一个数）；
  - drain 一笔动作 -> actd.log 一行（1MB 自压缩，防腐 #4）+ analytics
    `inbox_queue_wait`，打点只有动词名 + 秒数（TELEMETRY 红线：不带用户内容）；
  - 测量崩不掉 drain：`queue_wait_s` 判不动就静默退场，动作照样落账。

Runs entirely inside the sandbox AIASSISTANT_HOME (tests/__init__.py).
"""
import datetime as _dt
import json
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import actd
from act.lib import analytics, config
from act.lib.actd import inbox


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _now():
    return _dt.datetime.now(_dt.timezone.utc)


class QueueWaitMathTestCase(unittest.TestCase):
    def test_measures_the_gap_between_the_click_and_the_drain(self):
        # 整秒的 at：`ts` 的线上格式是秒级（inbox_writer._iso_now），拿带小数的
        # now() 当基准会把被截掉的那点零头算进排队时间
        at = _dt.datetime(2026, 9, 28, 12, 0, 8, tzinfo=_dt.timezone.utc)
        waited = inbox.queue_wait_s({"ts": _iso(at - _dt.timedelta(seconds=8))},
                                    now=at)
        self.assertEqual(waited, 8.0)

    def test_a_clock_skewed_future_stamp_is_clamped_to_zero(self):
        # 「排队了 -1 秒」是假话；两个进程的时钟差一秒就会算出来
        at = _dt.datetime(2026, 9, 28, 12, 0, 0, tzinfo=_dt.timezone.utc)
        waited = inbox.queue_wait_s({"ts": _iso(at + _dt.timedelta(seconds=5))},
                                    now=at)
        self.assertEqual(waited, 0.0)

    def test_no_stamp_measures_nothing_rather_than_guessing(self):
        self.assertIsNone(inbox.queue_wait_s({}))
        self.assertIsNone(inbox.queue_wait_s({"ts": None}))
        self.assertIsNone(inbox.queue_wait_s({"ts": "not-a-timestamp"}))
        self.assertIsNone(inbox.queue_wait_s({"ts": 1234}))


class DrainLedgerTestCase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        self._clear_inbox()
        # 收尾也清：`state/inbox/` 是 suite 共用的沙箱目录，留下的文件会让
        # `test_actd_inbox_drain_accounting` 的 rmdir 报「Directory not empty」
        self.addCleanup(self._clear_inbox)
        analytics.EVENTS_PATH.unlink(missing_ok=True)
        self.addCleanup(lambda: analytics.EVENTS_PATH.unlink(missing_ok=True))

    @staticmethod
    def _clear_inbox():
        for p in config.INBOX_DIR.glob("*"):
            p.unlink(missing_ok=True)

    def _events(self):
        if not analytics.EVENTS_PATH.exists():
            return []
        return [json.loads(x) for x in
                analytics.EVENTS_PATH.read_text(encoding="utf-8").splitlines() if x]

    def _drain_one(self, decision):
        (config.INBOX_DIR / "act-1.json").write_text(
            json.dumps(decision), encoding="utf-8")
        logged = []
        d = mock.Mock()
        d.log = logged.append
        d.write_applied_ack = mock.Mock()
        d.safe_unlink = lambda p: p.unlink(missing_ok=True)
        with mock.patch.object(inbox, "_route", return_value=("ok", 1)):
            inbox.process_inbox(d)
        return logged

    def test_a_drained_action_lands_in_the_log_and_in_analytics(self):
        click = _iso(_now() - _dt.timedelta(seconds=7))
        logged = self._drain_one({"action": "approve", "id": "R-1", "ts": click})
        self.assertTrue(any("排队" in line and "approve" in line for line in logged),
                        logged)
        hits = [e for e in self._events() if e["event"] == "inbox_queue_wait"]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["verb"], "approve")
        self.assertGreaterEqual(hits[0]["waited_s"], 6.0)
        # TELEMETRY 红线：打点只有动词名 + 秒数，不带卡 id / 用户内容
        self.assertEqual(set(hits[0]) - {"ts", "event", "v"}, {"verb", "waited_s"})

    def test_an_unstamped_action_records_nothing_and_still_drains(self):
        logged = self._drain_one({"action": "approve", "id": "R-1"})
        self.assertFalse(any("排队" in line for line in logged), logged)
        self.assertEqual([e for e in self._events()
                          if e["event"] == "inbox_queue_wait"], [])
        # 动作本身照样落账、文件照样删掉——测量绝不挡 drain
        self.assertEqual(list(config.INBOX_DIR.glob("*.json")), [])

    def test_the_measurement_never_wedges_the_drain(self):
        # 温度计炸了也不许改 drain 的结局——尤其不许让 process_inbox 的 except
        # 再写一次 bad_json，把一笔已经正确落账的动作覆盖成「毒文件」
        (config.INBOX_DIR / "act-2.json").write_text(
            json.dumps({"action": "approve", "id": "R-1", "ts": _iso(_now())}),
            encoding="utf-8")
        d = mock.Mock()
        d.log = mock.Mock()
        d.write_applied_ack = mock.Mock()
        d.safe_unlink = lambda p: p.unlink(missing_ok=True)
        with mock.patch.object(inbox, "queue_wait_s",
                               side_effect=RuntimeError("thermometer exploded")), \
             mock.patch.object(inbox, "_route", return_value=("ok", 1)):
            self.assertEqual(inbox.process_inbox(d), 1)
        # 回执恰好一次、且是真实处置，不是 bad_json
        d.write_applied_ack.assert_called_once_with("act-2", "ok")
        self.assertEqual(list(config.INBOX_DIR.glob("*.json")), [])

    def test_a_hand_written_verb_cannot_ride_an_unbounded_string_into_telemetry(self):
        # server 入站面有白名单，syncd / 手写进来的文件没有；analytics 是可上传面
        logged = self._drain_one({"action": "x" * 500, "id": "R-1",
                                  "ts": _iso(_now() - _dt.timedelta(seconds=2))})
        hits = [e for e in self._events() if e["event"] == "inbox_queue_wait"]
        self.assertEqual(len(hits), 1)
        self.assertEqual(len(hits[0]["verb"]), 40)
        self.assertTrue(all(len(line) < 200 for line in logged), logged)


class FacadeTestCase(unittest.TestCase):
    def test_the_facade_re_exports_nothing_new_by_accident(self):
        # 新 helper 住在 lib 层，入口层不需要再开一个 patch seam（防腐 #2 的
        # P3b 分层；不在这里引法条号——引号就等于给那节法条签一份证据，而这
        # 条判例证的是 §80.4 的排队秒数，不是分层账本那一节）
        self.assertTrue(hasattr(inbox, "queue_wait_s"))
        self.assertFalse(hasattr(actd, "queue_wait_s"))
