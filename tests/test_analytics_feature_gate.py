"""features.analytics 死开关修复判例（CONTRACT §16 追记）。

Settings 窗口的 analytics 开关写 features.analytics；gate 落在 emit 单点
analytics.log_event：flag off ⇒ 本地 events.jsonl 一行不写（上传侧
act.analytics_sync 读的正是这份文件，所以关 = 不落盘也不上报）。
gate 判定失败按 §16 默认 on 处理（宪法第 11 条：gate 自身绝不崩管线）。
"""
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act.lib import analytics, config


def _events_lines() -> int:
    try:
        with open(analytics.EVENTS_PATH, encoding="utf-8") as fh:
            return sum(1 for _ in fh)
    except OSError:
        return 0


class AnalyticsFeatureGateTestCase(unittest.TestCase):
    def _patch_features(self, **flags):
        cfg = config.Config()
        cfg.features.update(flags)
        p = mock.patch.object(config, "load_config", return_value=cfg)
        p.start()
        self.addCleanup(p.stop)

    def test_flag_off_log_event_writes_nothing(self):
        self._patch_features(analytics=False)
        before = _events_lines()
        analytics.log_event("gate_probe_off", req="R-000")
        self.assertEqual(_events_lines(), before)
        self.assertIsNone(next(
            (e for e in analytics.read_events()
             if e.get("event") == "gate_probe_off"), None))

    def test_flag_off_gates_log_first_too(self):
        # log_first 经 log_event 落盘，同一 gate 覆盖
        self._patch_features(analytics=False)
        before = _events_lines()
        analytics.log_first("gate_probe_first_off")
        self.assertEqual(_events_lines(), before)

    def test_flag_on_default_still_writes(self):
        self._patch_features()  # 默认全 on（§16）
        analytics.log_event("gate_probe_on", req="R-000")
        e = next((e for e in analytics.read_events()
                  if e.get("event") == "gate_probe_on"), None)
        self.assertIsNotNone(e)
        self.assertEqual(e.get("req"), "R-000")

    def test_config_yaml_flag_off_is_honored_end_to_end(self):
        # 不 mock：真的写 config.yaml，证明 Settings 拧的开关 actd 真能看见
        config.CONFIG_PATH.write_text(
            "features:\n  analytics: false\n", encoding="utf-8")
        self.addCleanup(lambda: config.CONFIG_PATH.unlink(missing_ok=True))
        before = _events_lines()
        analytics.log_event("gate_probe_yaml_off")
        self.assertEqual(_events_lines(), before)

    def test_gate_failure_fails_open_and_never_raises(self):
        # 判定崩了 ⇒ 按默认 on（§16），事件照写，绝不外溢异常
        p = mock.patch.object(config, "load_config",
                              side_effect=RuntimeError("boom"))
        p.start()
        self.addCleanup(p.stop)
        analytics.log_event("gate_probe_fail_open")
        self.assertIsNotNone(next(
            (e for e in analytics.read_events()
             if e.get("event") == "gate_probe_fail_open"), None))


if __name__ == "__main__":
    unittest.main()
