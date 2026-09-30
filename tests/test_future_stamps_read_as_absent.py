"""未来时间戳一律当缺席，绝不当「刚刚才跑过」（CONTRACT §82.4，issue #452）。

2026-09-18 泄漏到 live 安装的那次测试跑留下一批假时钟戳，其中
`state/slack_mcp.marker` = `2027-10-23T11:32:23Z`。它把两个判决同时弄坏：

- `_mcp_not_due`：`now - marker` 变成**负**的 timedelta，`< interval` 恒真，
  于是 Slack MCP 雷达直到 2027 年都答「还没到点」——整条来源静默停工；
- `_mcp_since`：窗口起点落到未来，连 `_MCP_LOOKBACK_CAP_H` 的地板都夹不住它。

同一把尺还治两处：`slack_mcp_present.marker` 的**文件 mtime** 缓存（未来 mtime =
永不过期），以及 doctor 的 dashboard 行（未来 `generated_at` 让一块死看板被报成
`fresh (generated 0s ago)`——宪法第 3 条不许虚报 ok）。

「缺席」是这些读者本来就有的 fail-open 分支，所以本条法把未来值**接回那条已被
判例钉住的老路**，不是新造一条。
"""
import datetime as _dt
import json
import os
import time
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first

from act import radar_slack
from act.lib import config, maintenance
from act.lib.checks import pipeline
from act.lib.checks.core import FAIL, OK

NOW = _dt.datetime(2026, 9, 30, 12, 0, 0, tzinfo=_dt.timezone.utc)
FUTURE = "2027-10-23T11:32:23Z"            # 判例里那一枚，逐字
PAST = "2026-09-30T11:40:00Z"              # 20 分钟前 = 30 分钟节流内


class ParseIsoFutureKnobTestCase(unittest.TestCase):
    """`parse_iso(..., reject_future=True)`：add-only，出厂关。"""

    def test_default_still_accepts_a_future_stamp(self):
        """出厂行为一字不变——既有的 1500+ 判例建在这上面。"""
        self.assertIsNotNone(maintenance.parse_iso(FUTURE))

    def test_reject_future_drops_it(self):
        self.assertIsNone(maintenance.parse_iso(FUTURE, reject_future=True, now=NOW))

    def test_a_past_stamp_survives_either_way(self):
        self.assertEqual(maintenance.parse_iso(PAST, reject_future=True, now=NOW),
                         maintenance.parse_iso(PAST))

    def test_the_skew_window_is_honoured(self):
        """NTP 一跳 / 夏令时边界不该被当成假时钟。"""
        inside = (NOW + _dt.timedelta(seconds=maintenance.FUTURE_SKEW_S - 10)
                  ).strftime("%Y-%m-%dT%H:%M:%SZ")
        outside = (NOW + _dt.timedelta(seconds=maintenance.FUTURE_SKEW_S + 10)
                   ).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.assertIsNotNone(maintenance.parse_iso(inside, reject_future=True, now=NOW))
        self.assertIsNone(maintenance.parse_iso(outside, reject_future=True, now=NOW))

    def test_unparseable_is_still_none(self):
        self.assertIsNone(maintenance.parse_iso("不是时间", reject_future=True, now=NOW))

    def test_in_future_takes_a_naive_now(self):
        naive = NOW.replace(tzinfo=None)
        self.assertTrue(maintenance.in_future(
            _dt.datetime(2027, 1, 1, tzinfo=_dt.timezone.utc), naive))


class SlackMcpMarkerTestCase(unittest.TestCase):
    """节流戳（`state/slack_mcp.marker`）。"""

    def setUp(self):
        config.ensure_state_dirs()
        self.path = config.STATE_DIR / radar_slack.MCP_MARKER_FILE
        self.addCleanup(lambda: self.path.unlink(missing_ok=True))

    def _write(self, text):
        self.path.write_text(text, encoding="utf-8")

    def test_a_past_marker_reads_back(self):
        self._write(PAST)
        self.assertEqual(radar_slack._read_mcp_marker(NOW),
                         _dt.datetime(2026, 9, 30, 11, 40, tzinfo=_dt.timezone.utc))

    def test_a_future_marker_reads_as_absent(self):
        self._write(FUTURE)
        self.assertIsNone(radar_slack._read_mcp_marker(NOW))

    def test_a_missing_marker_is_absent(self):
        self.assertIsNone(radar_slack._read_mcp_marker(NOW))

    def test_a_garbage_marker_is_absent(self):
        self._write("not a timestamp at all")
        self.assertIsNone(radar_slack._read_mcp_marker(NOW))

    def test_a_non_utf8_marker_is_absent_instead_of_crashing_the_pass(self):
        """被撕坏成非 UTF-8 的 marker 只是「读不出」，不许崩掉整个 pass。

        `read_text(encoding="utf-8")` 抛的 `UnicodeDecodeError` 是 `ValueError`
        的子类、**不是** `OSError`——把解析挪进 `parse_iso` 时若顺手把兜底收窄成
        `except OSError`，这一份坏字节就会一路穿到 launchd 的 3 分钟 tick 上
        （宪法第 11 条：一条坏记录不许崩 pass）。
        """
        self.path.write_bytes(b"\xff\xfe2027-10-23T11:32:23Z")
        self.assertIsNone(radar_slack._read_mcp_marker(NOW))

    def test_the_2027_marker_no_longer_suppresses_the_pass(self):
        """这一条就是 #452 的伤害面：未来戳曾让节流永远答「不到点」。"""
        cfg = config.Config()
        poisoned = maintenance.parse_iso(FUTURE)
        self.assertTrue(radar_slack._mcp_not_due(poisoned, NOW, cfg),
                        "前提：未来 marker 直接喂进节流判决时确实答『不到点』")
        self._write(FUTURE)
        self.assertFalse(
            radar_slack._mcp_not_due(radar_slack._read_mcp_marker(NOW), NOW, cfg),
            "读者把未来戳当缺席之后，这一轮必须照跑")

    def test_the_window_start_falls_back_to_the_default_lookback(self):
        self._write(FUTURE)
        since = radar_slack._mcp_since(radar_slack._read_mcp_marker(NOW), NOW)
        self.assertLess(since, NOW)


class SlackMcpPresenceCacheTestCase(unittest.TestCase):
    """存在性缓存用的是**文件 mtime**，所以要单独钉一遍。"""

    def setUp(self):
        config.ensure_state_dirs()
        self.path = config.STATE_DIR / radar_slack.MCP_PRESENT_MARKER_FILE
        self.path.write_text("0", encoding="utf-8")
        self.addCleanup(lambda: self.path.unlink(missing_ok=True))

    def _set_mtime(self, offset_s):
        stamp = time.time() + offset_s
        os.utime(self.path, (stamp, stamp))

    def test_a_fresh_cache_is_trusted_without_probing(self):
        self._set_mtime(-60)
        with mock.patch.object(radar_slack, "_probe_slack_mcp",
                               side_effect=AssertionError("must not probe")):
            self.assertEqual(radar_slack._slack_mcp_present(), (False, False))

    def test_a_future_mtime_forces_a_fresh_probe(self):
        """未来 mtime 曾让 age 变成负数 → `< TTL` 恒真 → 缓存永不过期。"""
        self._set_mtime(+3600 * 24 * 365)
        with mock.patch.object(radar_slack, "_probe_slack_mcp", return_value=True):
            self.assertEqual(radar_slack._slack_mcp_present(), (True, True))
        self.assertEqual(self.path.read_text(encoding="utf-8"), "1")

    def test_an_expired_cache_still_reprobes(self):
        self._set_mtime(-(radar_slack._MCP_PRESENT_TTL_S + 60))
        with mock.patch.object(radar_slack, "_probe_slack_mcp", return_value=True):
            self.assertEqual(radar_slack._slack_mcp_present(), (True, True))

    def test_a_non_utf8_cache_reprobes_instead_of_raising(self):
        """docstring 写着「Never raises」，坏字节也得算数（与 marker 同款）。"""
        self.path.write_bytes(b"\xff\xfe1")
        self._set_mtime(-60)
        with mock.patch.object(radar_slack, "_probe_slack_mcp", return_value=True):
            self.assertEqual(radar_slack._slack_mcp_present(), (True, True))


class DoctorDashboardHonestyTestCase(unittest.TestCase):
    """doctor 的 dashboard 行：未来 generated_at 必须分类报错，不许答 ok。"""

    def setUp(self):
        config.ensure_state_dirs()
        self.addCleanup(lambda: config.DASHBOARD_PATH.unlink(missing_ok=True))

    def _write(self, generated_at):
        config.DASHBOARD_PATH.write_text(
            json.dumps({"generated_at": generated_at}), encoding="utf-8")

    def _row(self, now_epoch):
        probes = mock.Mock()
        probes.now.return_value = now_epoch
        return pipeline.check_dashboard(probes)

    def test_a_fresh_dashboard_is_ok(self):
        self._write("2026-09-30T12:00:00Z")
        row = self._row(NOW.timestamp() + 10)
        self.assertEqual(row.status, OK)
        self.assertIn("fresh", row.detail)

    def test_a_future_generated_at_fails_with_its_own_classification(self):
        self._write(FUTURE)
        row = self._row(NOW.timestamp())
        self.assertEqual(row.status, FAIL)
        self.assertEqual(row.failure_id, "dashboard_stale")
        self.assertIn("2027", row.detail)

    def test_a_stale_dashboard_still_reports_stale(self):
        self._write("2026-09-30T10:00:00Z")
        row = self._row(NOW.timestamp())
        self.assertEqual(row.status, FAIL)
        self.assertEqual(row.failure_id, "dashboard_stale")


if __name__ == "__main__":
    unittest.main()
