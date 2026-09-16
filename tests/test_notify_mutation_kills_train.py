"""§28 追记（issue #29）通知偏好里夜报变异体活下来的那几格。

判例 tests/test_notify_preferences.py 钉住了这一节的主干（跨午夜、分类开关、
失败类穿透、fail-open、被吞返回 True），但有四类格子是它没碰到的——夜间变异
（§57）在 `act/lib/notify.py` 的偏好闸门上留了一串存活体，全落在这四类里：

* **端点的分钟位**：判例的窗都是整点（"22:00" / "08:00"），`HH:MM → 当日分钟
  数`（`_minute_of_day`，词法单源 = §70 的 `config.coerce_clock_time`）的算式
  改一个字也照样通过。这里的窗带分钟（"22:30"），并判半开区间 `[start, end)`
  的**两个端点**：start 那一分钟算在内、end 那一分钟不算。
* **当下时刻也要按分钟折算**：`_quiet_now` 把 `time.struct_time` 折成
  `tm_hour * 60 + tm_min`。判例的窗有 10 小时宽，折算差个几十分钟也还在窗里；
  这里用一个 30 分钟的窄窗，把「窗是按分钟判的，不是按整点判的」钉死。
* **两处 getattr 缺省 = fail-open 的构造**：`getattr(cfg, preference, True)`
  与 `getattr(cfg, "quiet_hours_enabled", False)`。判例喂的都是完整的
  `config.Config`，缺省值永远走不到；缺键的 cfg（老版本配置 / 鸭子类型的注入）
  必须是「分类默认开、安静时段默认关」——两把缺省都指向「宁可多一条也不静音」
  （宪法第 11 条 fail-open，§28 追记「配错一个字不许把所有通知静音」）。
* **谓词要答一个真 bool**：`in_quiet_hours` / `suppressed_now` 的返回值被
  `assertFalse` 一类的松断言接受成 None 也不报警；公开谓词答 None 就是把
  「判不了」和「判成假」混成一件事。这里逐个 `assertIs`。

一条都不许 mock 被测单元自己（`_native_notify` / `_queue_write` 照跑）：注入缝
只有 cfg、`now` 与 `config.load_config`（配置读 = 盘 IO 边界）。

**两个体判为等价（可达输入上无可观察差异，不强杀）**：
* `in_quiet_hours` 的 `if a < b`（`< → <=`）：`a == b`（零长窗）在上一行就已经
  `return False`，到这一行 `a != b` 恒成立——两个算子对所有可达输入同解。
* `_quiet_now` 的 `return False`（`→ return None`）：私名，唯一消费者是
  `suppression_reason` 里的 `if _quiet_now(...)`，None 与 False 同为假值。
  （`in_quiet_hours` / `suppressed_now` 是**公开**谓词，答 None 在公开面上看得
  见，所以上面那两条 `assertIs` 管它们、不管这个。）
"""
import time
import types
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first

from act.lib import config, notify


def _cfg(**kw) -> config.Config:
    cfg = config.Config()
    for key, value in kw.items():
        setattr(cfg, key, value)
    return cfg


def _at(hour: int, minute: int = 0) -> time.struct_time:
    """本地时间注入缝：只有 tm_hour / tm_min 被读。"""
    return time.struct_time((2026, 9, 12, hour, minute, 0, 5, 255, -1))


class QuietWindowEndpointTestCase(unittest.TestCase):
    """`in_quiet_hours` 的端点：分钟位进账，且区间是半开的 `[start, end)`。"""

    def test_endpoint_minutes_count(self):
        # "22:30" 不是 "22:00"——端点的分钟位必须折进当日分钟数，
        # 且 start 的那一分钟算在窗内（闭端）。
        self.assertIs(notify.in_quiet_hours("22:30", "23:00", 22 * 60 + 30), True)
        self.assertIs(notify.in_quiet_hours("22:30", "23:00", 22 * 60 + 29), False)

    def test_the_end_minute_is_already_outside(self):
        # end 的那一分钟不算（开端）——两端都不许重叠/漏。
        self.assertIs(notify.in_quiet_hours("22:30", "23:00", 22 * 60 + 59), True)
        self.assertIs(notify.in_quiet_hours("22:30", "23:00", 23 * 60), False)

    def test_a_wrapping_window_keeps_its_minutes_too(self):
        # 跨午夜的两段各自也按分钟切。
        self.assertIs(notify.in_quiet_hours("22:30", "08:15", 22 * 60 + 30), True)
        self.assertIs(notify.in_quiet_hours("22:30", "08:15", 22 * 60 + 29), False)
        self.assertIs(notify.in_quiet_hours("22:30", "08:15", 8 * 60 + 14), True)
        self.assertIs(notify.in_quiet_hours("22:30", "08:15", 8 * 60 + 15), False)

    def test_failing_open_still_answers_a_bool(self):
        """关掉的窗答 `False`，不答 `None`——「判不了」不许冒充「判成假」。"""
        for start, end in (("nope", "08:00"), ("22:00", ""), (None, None),
                           ("25:00", "08:00"), ("08:00", "08:00")):
            with self.subTest(start=start, end=end):
                self.assertIs(notify.in_quiet_hours(start, end, 3 * 60), False)


class QuietWindowIsJudgedToTheMinuteTestCase(unittest.TestCase):
    """当下时刻的折算：一个 30 分钟的窄窗，整点精度不够用。"""

    _CFG = dict(quiet_hours_enabled=True, quiet_hours_start="22:00",
                quiet_hours_end="22:30")

    def _reason(self, now):
        return notify.suppression_reason(notify.KIND_PROPOSAL, _cfg(**self._CFG), now=now)

    def test_inside_the_narrow_window(self):
        self.assertEqual(self._reason(_at(22, 0)), "quiet_hours")
        self.assertEqual(self._reason(_at(22, 20)), "quiet_hours")

    def test_outside_the_narrow_window(self):
        self.assertIsNone(self._reason(_at(22, 30)))    # 半开区间的开端
        self.assertIsNone(self._reason(_at(21, 59)))

    def test_the_exempt_classes_pierce_the_same_minute(self):
        """豁免是按**类**判的，与窗多窄无关（失败 / 手动回执 / 归档前的告知）。"""
        cfg = _cfg(**self._CFG)
        for kind in sorted(notify.QUIET_HOURS_EXEMPT):
            with self.subTest(kind=kind):
                self.assertIsNone(notify.suppression_reason(kind, cfg, now=_at(22, 20)))


class AbsentKeysFailOpenTestCase(unittest.TestCase):
    """缺键的 cfg（老配置 / 鸭子类型注入）走 getattr 缺省：分类默认开、安静时段默认关。"""

    def test_a_missing_category_key_leaves_that_class_on(self):
        cfg = types.SimpleNamespace(quiet_hours_enabled=False)
        for kind in sorted(notify.CATEGORY_PREFERENCE):
            with self.subTest(kind=kind):
                self.assertIsNone(notify.suppression_reason(kind, cfg, now=_at(12)))

    def test_a_missing_quiet_hours_switch_means_quiet_hours_are_off(self):
        """安静时段是**勾上才有**的：端点在、开关键不在 → 窗当关（照发）。"""
        cfg = types.SimpleNamespace(notify_proposals=True, quiet_hours_start="22:00",
                                    quiet_hours_end="08:00")
        self.assertIsNone(notify.suppression_reason(notify.KIND_PROPOSAL, cfg, now=_at(2, 30)))


class SuppressedNowTestCase(unittest.TestCase):
    """`suppressed_now` 现读一次配置，答一个真 bool；读炸了照发。"""

    def test_both_answers_are_real_bools(self):
        with mock.patch.object(config, "load_config", return_value=_cfg(notify_proposals=False)):
            self.assertIs(notify.suppressed_now(notify.KIND_PROPOSAL), True)
        with mock.patch.object(config, "load_config", return_value=config.Config()):
            self.assertIs(notify.suppressed_now(notify.KIND_PROPOSAL), False)

    def test_a_config_read_that_explodes_fails_open(self):
        with mock.patch.object(config, "load_config", side_effect=RuntimeError("boom")):
            self.assertIs(notify.suppressed_now(notify.KIND_PROPOSAL), False)
        with mock.patch.object(config, "load_config", side_effect=OSError("disk")):
            self.assertIs(notify.suppressed_now(notify.KIND_FAILURE), False)


class SuppressedNotifyReportsSuccessTestCase(unittest.TestCase):
    """被偏好吞掉 = 返回 True 且队列一个字节都没动（False 只表示「没能交给消费方」）。"""

    def _queued(self) -> int:
        qdir = config.NOTIFY_QUEUE_DIR
        return len(list(qdir.glob("*.json"))) if qdir.exists() else 0

    def test_a_suppressed_notification_is_not_a_failure(self):
        before = self._queued()
        with mock.patch.object(config, "load_config", return_value=_cfg(notify_proposals=False)):
            ok = notify.notify("t", "b", kind=notify.KIND_PROPOSAL)
        self.assertIs(ok, True)                     # 偏好吞掉 ≠ 失败
        self.assertEqual(self._queued(), before)    # §28 队列没被碰（连 sweep 都没跑）


if __name__ == "__main__":
    unittest.main()
