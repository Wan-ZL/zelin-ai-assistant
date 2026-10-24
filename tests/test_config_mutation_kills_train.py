"""出厂旋钮的**数字本身**就是契约（`act/lib/config.py`）。

这些常量与 dataclass 默认值全都有下游后果，但下游只会「读到什么用什么」——
把 60 改成 59、把 True 改成 False，没有一条既有判例会红。本文件钉的是那一层：
每一条都从**消费方**问一句「出厂装机上，这个数意味着什么行为」，答案写死成字面量
（不许写成 `== config.DEFAULT_*`——那样常量跟着改、判例跟着瞎）。

- §72.4 `recording.media_retention_minutes`：出厂 60 = `ingest/screenpipe-cleanup.sh`
  历来写死的 `-mmin +60`；区间 [5, 525600] 的两端各钉一格（区间外 = 坏值，
  不夹取——文件里写着的数必须就是 cron 用的数）；
- §72 `recording.retention_days`：出厂 0 = 永久保留，一行录制数据都不删；
- §28 通知偏好：出厂 = 安静时段关——新装机在夜里 3 点也一条通知都不少。

（v2.0.0：§76.2 mention_escalation、§44.6 fold_receipt_notices、§70.2 review_stale_days
与三把分类开关随 Act 管线退役，全文见 tag full。）

Runs entirely inside the sandbox AIASSISTANT_HOME (tests/__init__.py).
"""
import datetime as _dt
import time
import unittest

from tests import TMP_HOME  # noqa: F401 - sandbox env before act imports

from act.lib import config, notify
from act.lib import screenpipe_retention

TODAY = _dt.date(2026, 9, 15)


def _at(hour: int, minute: int = 0) -> time.struct_time:
    """本地时间注入缝（notify 只读 tm_hour / tm_min）。"""
    return time.struct_time((2026, 9, 15, hour, minute, 0, 1, 258, -1))


class MediaRetentionWindowTestCase(unittest.TestCase):
    """§72.4 原始媒体保留期：出厂值 + 区间两端。"""

    def test_factory_window_is_the_sixty_minutes_cleanup_used_to_hardcode(self):
        # 这把旋钮是把 `-mmin +60` 从 shell 里搬出来的；出厂装机上 cron 的行为
        # 必须与搬之前逐字相同，所以这个数是 60，不是「差不多一小时」。
        self.assertEqual(config.Config().screenpipe_media_retention_minutes, 60)
        # 配置读不出来时 `--print-value` 打的也是同一个数（cron 拿它当 find 的参数，
        # 绝不空行、绝不 traceback）。
        self.assertEqual(
            config._CLI_VALUE_KEYS["screenpipe_media_retention_minutes"], 60)

    def test_floor_is_five_minutes(self):
        # 链每 30 分钟一轮；比 5 分钟更短会削到同一轮里正在导出的那批帧。
        self.assertEqual(config.coerce_media_retention_minutes(5), 5)
        with self.assertRaises(ValueError):
            config.coerce_media_retention_minutes(4)

    def test_ceiling_is_one_year_of_minutes(self):
        # 上限 1 年（365 天 × 24 小时 × 60 分）；再长等于没有保留期。
        self.assertEqual(config.coerce_media_retention_minutes(525600), 525600)
        with self.assertRaises(ValueError):
            config.coerce_media_retention_minutes(525601)

    def test_out_of_range_yaml_falls_back_instead_of_clamping(self):
        # 夹取会让设置页显示的数与 cron 真用的数不是一个——回落，不夹。
        cfg = config.Config()
        cfg.screenpipe_media_retention_minutes = 240
        config._apply_recording(cfg, {"recording": {"media_retention_minutes": 4}})
        self.assertEqual(cfg.screenpipe_media_retention_minutes, 60)


class RecordingRetentionDefaultTestCase(unittest.TestCase):
    """§72 screenpipe DB 保留期：出厂 0 = 永久保留。"""

    def test_a_fresh_install_never_deletes_recorded_data(self):
        cfg = config.Config()
        # 0 = 关掉整条规则。任何非 0 出厂值都等于「装完就开始删用户的录制数据」。
        self.assertEqual(cfg.screenpipe_retention_days, 0)
        self.assertEqual(screenpipe_retention.retention_days_from_config(cfg), 0)

    def test_the_factory_value_is_one_the_loader_would_accept(self):
        # yaml 层拒收负数（`days if days >= 0 else ...`），所以出厂值本身也必须
        # 落在合法域里——负的哨兵值会让「回落到默认」回落成一个非法值。
        cfg = config.Config()
        config._apply_recording(cfg, {"recording": {"retention_days": -3}})
        self.assertEqual(cfg.screenpipe_retention_days, 0)


class FactoryNotificationPreferencesTestCase(unittest.TestCase):
    """§28 追记（issue #29）：出厂值 = 本改动前的行为，一条通知都不少。"""

    def test_a_fresh_install_suppresses_nothing_at_any_hour(self):
        cfg = config.Config()
        kinds = (notify.KIND_RECAP_READY, notify.KIND_FAILURE, None)
        # 3 点与 23 点都落在**出厂安静窗** 22:00–08:00 里：安静时段出厂是关的，
        # 所以这两个时刻与正午一样照发。
        for hour in (3, 12, 23):
            for kind in kinds:
                with self.subTest(hour=hour, kind=kind):
                    self.assertIsNone(
                        notify.suppression_reason(kind, cfg, now=_at(hour)))


if __name__ == "__main__":   # pragma: no cover
    unittest.main()
