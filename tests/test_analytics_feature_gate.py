"""features.analytics 死开关修复判例（CONTRACT §16 追记）。

Settings 窗口的 analytics 开关写 features.analytics；gate 拦在三个环节
（§16 追记）：Python 写者 log_event/log_first、Swift 写者 Analytics.log/
firstReach（判例在 mac/LogicTests AnalyticsGateTests）、上传端 sync_once
（判例在 tests/test_analytics_sync.py）。本文件钉 Python 写者 + 隐私
fail-closed 特例 + GATE_TTL 缓存：

- flag off ⇒ 本地 events.jsonl 一行不写；log_first 连 once-per-install
  marker 也不写（里程碑留到重开后再发，绝不被吞）。
- 隐私特例：配置读不到 / 存在但损坏 ⇒ gate 按 off 处理（fail-closed，
  与 §16 其它 flag 的 fail-open 惯例相反）；判定自身绝不 raise（宪法第
  11 条）。文件不存在不算损坏——默认 on。
- gate 结果带进程内缓存：缓存键含两份配置源的 mtime+size 指纹——配置
  文件一变下一条事件即重判（关闭后不存在「TTL 内照记」的盲窗），TTL 只
  兜指纹失灵的底（可 reset，判例不许 flaky）。
- feature_gate_fresh：上传端每 batch 送出前的单快照判定——每份配置源只
  读一次 bytes，值与损坏判定同快照，不吃缓存。
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
    def setUp(self):
        # 缓存是进程级的：每条判例前后都清——既不吃上一条的结果，也不把
        # 自己缓存的 False 泄漏给同进程的后续 suite（TTL 5s > 全程跑时）
        analytics.reset_feature_gate_cache()
        self.addCleanup(analytics.reset_feature_gate_cache)
        self.addCleanup(lambda: config.CONFIG_PATH.unlink(missing_ok=True))
        self.addCleanup(
            lambda: config.SETTINGS_OVERRIDES_PATH.unlink(missing_ok=True))

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

    def test_flag_off_log_first_leaves_no_marker(self):
        # §16：off 期间连 marker 也不写——重开后里程碑仍能发出（一次）
        self._patch_features(analytics=False)
        analytics.log_first("gate_probe_milestone")
        self.assertFalse((analytics.FIRST_DIR / "gate_probe_milestone").exists())
        # 重新开启：同一里程碑此刻才第一次落盘 + 落 marker
        analytics.reset_feature_gate_cache()
        self._patch_features(analytics=True)
        analytics.log_first("gate_probe_milestone")
        self.assertTrue((analytics.FIRST_DIR / "gate_probe_milestone").exists())
        events = [e for e in analytics.read_events()
                  if e.get("event") == "gate_probe_milestone"]
        self.assertEqual(len(events), 1)

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
        before = _events_lines()
        analytics.log_event("gate_probe_yaml_off")
        self.assertEqual(_events_lines(), before)

    def test_nested_features_beats_stale_flat_key_regardless_of_order(self):
        # 嵌套形 vs 平铺形同文件冲突（App 只写嵌套形、且不清理手写/历史
        # 遗留的平铺键）：嵌套形优先、与 JSON 键序无关——镜像 Swift
        # Analytics.featureEnabled 的读取顺序（嵌套 → 平铺），两侧对同一份
        # overrides 必须给出同一个 gate 答案，否则 Swift 判关停写、Python
        # 判开继续把积压传出本机
        config.SETTINGS_OVERRIDES_PATH.parent.mkdir(parents=True, exist_ok=True)
        config.SETTINGS_OVERRIDES_PATH.write_text(
            '{"features": {"analytics": false}, "features.analytics": true}',
            encoding="utf-8")
        self.assertFalse(config.load_config().feature("analytics"))
        # 平铺键在前、嵌套块在后：结果一致（与文件键序无关）
        config.SETTINGS_OVERRIDES_PATH.write_text(
            '{"features.analytics": true, "features": {"analytics": false}}',
            encoding="utf-8")
        self.assertFalse(config.load_config().feature("analytics"))
        # 平铺形单独存在时照常生效（合法的历史拼写不作废）
        config.SETTINGS_OVERRIDES_PATH.write_text(
            '{"features.analytics": false}', encoding="utf-8")
        self.assertFalse(config.load_config().feature("analytics"))
        # 嵌套块存在但没写同名 flag：平铺键照常生效（只让位给真冲突）
        config.SETTINGS_OVERRIDES_PATH.write_text(
            '{"features.analytics": false, "features": {"digest": false}}',
            encoding="utf-8")
        cfg = config.load_config()
        self.assertFalse(cfg.feature("analytics"))
        self.assertFalse(cfg.feature("digest"))

    # ------------------------------------------------------------------ #
    # 隐私 fail-closed 特例（§16 追记）：读不到配置 = 不记，绝不 raise
    # ------------------------------------------------------------------ #

    def test_gate_failure_fails_closed_and_never_raises(self):
        # 判定崩了 ⇒ 按 off（隐私特例，与其它 flag 的 fail-open 相反）：
        # 显式退出可能就藏在读不到的配置里。零输出，异常绝不外溢。
        p = mock.patch.object(config, "load_config",
                              side_effect=RuntimeError("boom"))
        p.start()
        self.addCleanup(p.stop)
        before = _events_lines()
        analytics.log_event("gate_probe_fail_closed")
        self.assertEqual(_events_lines(), before)

    def test_corrupt_config_yaml_zero_output(self):
        # config.yaml 存在但不是 yaml dict（损坏/手改坏）⇒ log_event 零输出。
        # load_config 自己会静默退回默认 on，所以 gate 必须单独探测损坏。
        config.CONFIG_PATH.write_text(
            "\t:{ this is not yaml ][", encoding="utf-8")
        before = _events_lines()
        analytics.log_event("gate_probe_corrupt_yaml")
        self.assertEqual(_events_lines(), before)

    def test_corrupt_overrides_json_zero_output(self):
        # settings_overrides.json 存在但解析不了 ⇒ 同样 fail-closed：
        # 用户在 UI 里记录的退出正躺在这份读不懂的文件里
        config.SETTINGS_OVERRIDES_PATH.parent.mkdir(parents=True, exist_ok=True)
        config.SETTINGS_OVERRIDES_PATH.write_text("{broken", encoding="utf-8")
        before = _events_lines()
        analytics.log_event("gate_probe_corrupt_overrides")
        self.assertEqual(_events_lines(), before)

    def test_unparseable_flag_value_zero_output(self):
        # flag 值写了但判不动布尔（"banana"）⇒ 按损坏 fail-closed：
        # load_config 会把它静默退回默认 on，但用户写下它时想表达的很可能
        # 是退出（Swift 侧 featureEnabled 同一保守探测）
        config.CONFIG_PATH.write_text(
            "features:\n  analytics: banana\n", encoding="utf-8")
        before = _events_lines()
        analytics.log_event("gate_probe_bad_value")
        self.assertEqual(_events_lines(), before)

    def test_absent_config_files_default_on(self):
        # 不存在 ≠ 损坏：全新 checkout 没写过任何配置，按 §16 默认 on
        config.CONFIG_PATH.unlink(missing_ok=True)
        config.SETTINGS_OVERRIDES_PATH.unlink(missing_ok=True)
        analytics.log_event("gate_probe_fresh_checkout")
        self.assertIsNotNone(next(
            (e for e in analytics.read_events()
             if e.get("event") == "gate_probe_fresh_checkout"), None))

    # ------------------------------------------------------------------ #
    # GATE_TTL 缓存：高频 emit 不逐条付 config parse；reset 缝防 flaky
    # ------------------------------------------------------------------ #

    def test_gate_result_cached_within_ttl(self):
        cfg = config.Config()
        with mock.patch.object(config, "load_config",
                               return_value=cfg) as loader:
            self.assertTrue(analytics.feature_gate())
            self.assertTrue(analytics.feature_gate())
            self.assertTrue(analytics.feature_gate())
        self.assertEqual(loader.call_count, 1)

    def test_gate_cache_reset_reevaluates(self):
        self.assertTrue(analytics.feature_gate())  # 缓存 on
        analytics.reset_feature_gate_cache()
        self._patch_features(analytics=False)
        self.assertFalse(analytics.feature_gate())

    def test_gate_cache_expires_after_ttl(self):
        self.assertTrue(analytics.feature_gate())  # 缓存 on
        self._patch_features(analytics=False)
        # 拨快时钟越过 TTL（不 sleep，判例不许 flaky）
        expiry = analytics._gate_cache[0]
        with mock.patch.object(analytics._time, "monotonic",
                               return_value=expiry + 0.1):
            self.assertFalse(analytics.feature_gate())

    def test_injected_cfg_bypasses_cache(self):
        # 上传端注入缝：显式传 cfg 时不读缓存也不写缓存
        self.assertTrue(analytics.feature_gate())  # 缓存 on
        off = config.Config()
        off.features["analytics"] = False
        self.assertFalse(analytics.feature_gate(off))
        self.assertTrue(analytics.feature_gate())  # 缓存未被注入调用污染

    def test_config_change_invalidates_cache_immediately(self):
        # 缓存键含配置源指纹：关闭开关（写真实 config.yaml）后**下一条事件
        # 就停**——不 reset、不拨钟、不等 TTL。否则「关闭后 5s 内 Ask 写入
        # 的问题文本照记、日后重开随积压上传」就是隐私洞
        self.assertTrue(analytics.feature_gate())  # 预热缓存：True 且未过期
        config.CONFIG_PATH.write_text(
            "features:\n  analytics: false\n", encoding="utf-8")
        before = _events_lines()
        analytics.log_event("gate_probe_flip_no_wait")
        self.assertEqual(_events_lines(), before)
        self.assertFalse(analytics.feature_gate())

    # ------------------------------------------------------------------ #
    # feature_gate_fresh：上传端每 batch 送出前的单快照判定
    # ------------------------------------------------------------------ #

    def test_fresh_gate_ignores_warm_cache(self):
        self.assertTrue(analytics.feature_gate())  # 预热缓存：True
        config.CONFIG_PATH.write_text(
            "features:\n  analytics: false\n", encoding="utf-8")
        self.assertFalse(analytics.feature_gate_fresh())

    def test_fresh_gate_reads_files_not_load_config(self):
        # 单快照语义的钉子：值来自配置文件本身的一次读取，而不是
        # load_config 的另一次读取——旧实现「load 到旧值 on + intact 确认
        # 新文件语法有效」的两读混用窗口在这里不存在
        stale_on = config.Config()  # 默认 analytics on（模拟陈旧快照）
        config.CONFIG_PATH.write_text(
            "features:\n  analytics: false\n", encoding="utf-8")
        with mock.patch.object(config, "load_config", return_value=stale_on):
            self.assertFalse(analytics.feature_gate_fresh())

    def test_fresh_gate_fail_closed_and_precedence(self):
        # 缺省 on；损坏/坏值 off；overrides 嵌套形压平铺形（与键序无关）
        self.assertTrue(analytics.feature_gate_fresh())
        config.CONFIG_PATH.write_text("\t:{ not yaml ][", encoding="utf-8")
        self.assertFalse(analytics.feature_gate_fresh())
        config.CONFIG_PATH.write_text(
            "features:\n  analytics: banana\n", encoding="utf-8")
        self.assertFalse(analytics.feature_gate_fresh())
        config.CONFIG_PATH.unlink()
        config.SETTINGS_OVERRIDES_PATH.parent.mkdir(parents=True, exist_ok=True)
        config.SETTINGS_OVERRIDES_PATH.write_text("{broken", encoding="utf-8")
        self.assertFalse(analytics.feature_gate_fresh())
        config.SETTINGS_OVERRIDES_PATH.write_text(
            '{"features.analytics": true, "features": {"analytics": false}}',
            encoding="utf-8")
        self.assertFalse(analytics.feature_gate_fresh())
        config.SETTINGS_OVERRIDES_PATH.write_text(
            '{"features.analytics": false, "features": {"digest": true}}',
            encoding="utf-8")
        self.assertFalse(analytics.feature_gate_fresh())

    # ------------------------------------------------------------------ #
    # log_first：write-success-then-mark（镜像 Swift firstReach）
    # ------------------------------------------------------------------ #

    def test_log_first_marks_only_after_successful_write(self):
        # 事件写入失败（这里用「events.jsonl 位置被目录占住」逼 open 失败）
        # ⇒ marker 不落笔，里程碑留到下次；恢复后同一里程碑恰好发一次
        marker = analytics.FIRST_DIR / "gate_probe_write_fail"
        marker.unlink(missing_ok=True)
        analytics.EVENTS_PATH.unlink(missing_ok=True)
        analytics.EVENTS_PATH.mkdir(parents=True, exist_ok=True)
        try:
            analytics.log_first("gate_probe_write_fail")
            self.assertFalse(marker.exists())
        finally:
            analytics.EVENTS_PATH.rmdir()
        analytics.log_first("gate_probe_write_fail")
        self.assertTrue(marker.exists())
        events = [e for e in analytics.read_events()
                  if e.get("event") == "gate_probe_write_fail"]
        self.assertEqual(len(events), 1)


if __name__ == "__main__":
    unittest.main()
