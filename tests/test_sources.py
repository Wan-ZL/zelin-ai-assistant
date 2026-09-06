"""源开关真源 + 源死亡告警（CONTRACT §48）判例。

五组契约（全部注入缝 mock，不 spawn 真 claude、不触网）：

1. ``sources.enabled()`` 真值表 — feature flag 与 sources.<src>.enabled 合取；
   未知源 fail-closed。
2. 关闭真静默 — 关掉的源跑 radar：health 无条目（既有条目被清除）、无
   radar_skip analytics 事件；`disabled` skip_reason 已退役（deprecated）。
3. 源死亡告警 — 开着的源 last_ok/last_attempt（较新者）超 liveness 阈值 →
   notify 一次（anti-nag：不跨阈值不重复响），恢复自动出账；关掉的源天然
   不进循环且残留条目被清；配置每 pass 现读（App 翻开关立即生效）；睡醒
   宽限（wall-clock 跳变后一个最大雷达周期内不评判）。
4. dashboard ``radar_sources`` 投影形状（add-only 字段）+ 配置现读。
5. ``python3 -m act.lib.sources --enabled <src>`` CLI 出口码（install.sh 的
   plist 防复活闸门吃这个）：0 开 / 3 关（独占码 + stdout "off"）/ 2 未知；
   1 留给 python 崩溃 → 闸门 fail-open。
"""
import datetime as _dt
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import actd, radar_gmail, radar_slack
from act.lib import analytics, config, dashboard, radar_health, sources


def _iso(delta_s: float = 0.0) -> str:
    t = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(seconds=delta_s)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _cfg(**kw) -> config.Config:
    cfg = config.Config()
    feats = dict(config.DEFAULT_FEATURES)
    feats.update(kw.pop("features", {}))
    cfg.features = feats
    for k, v in kw.items():
        setattr(cfg, k, v)
    return cfg


def _clean_state():
    for p in (radar_health.HEALTH_PATH, analytics.EVENTS_PATH, config.CONFIG_PATH):
        if p.exists():
            p.unlink()


def _events() -> list:
    if not analytics.EVENTS_PATH.exists():
        return []
    return [json.loads(line) for line in
            analytics.EVENTS_PATH.read_text(encoding="utf-8").splitlines()
            if line.strip()]


# --------------------------------------------------------------------------- #
# 1 — enabled() truth table
# --------------------------------------------------------------------------- #
class EnabledTruthTableTestCase(unittest.TestCase):
    def test_default_all_on(self):
        cfg = _cfg()
        for src in sources.SOURCES:
            self.assertTrue(sources.enabled(cfg, src))

    def test_feature_flag_off_wins(self):
        cfg = _cfg(features={"gmail_radar": False})
        self.assertFalse(sources.enabled(cfg, "gmail"))
        self.assertTrue(sources.enabled(cfg, "slack"))

    def test_sources_enabled_off_wins(self):
        # sources.gmail.enabled=false 关源，即便 feature flag 开着（合取）
        cfg = _cfg(gmail_enabled=False)
        self.assertFalse(sources.enabled(cfg, "gmail"))

    def test_both_on_required(self):
        cfg = _cfg(features={"gmail_radar": False}, gmail_enabled=False)
        self.assertFalse(sources.enabled(cfg, "gmail"))
        cfg2 = _cfg(features={"gmail_radar": True}, gmail_enabled=True)
        self.assertTrue(sources.enabled(cfg2, "gmail"))

    def test_unknown_source_fail_closed(self):
        self.assertFalse(sources.enabled(_cfg(), "screenpipe"))

    def test_slack_obsidian_enabled_fields(self):
        # §48 三源对齐：sources.slack/obsidian.enabled 不再是半个开关
        self.assertFalse(sources.enabled(_cfg(slack_enabled=False), "slack"))
        self.assertFalse(sources.enabled(_cfg(obsidian_enabled=False), "obsidian"))
        self.assertTrue(sources.enabled(_cfg(slack_enabled=True), "slack"))

    def test_flat_override_wins_over_yaml_nested_enabled(self):
        # App 面板「打开」= 用户显式动作 → 写扁平 override（gmail_enabled /
        # slack_enabled），必须压过 yaml 的 sources.<src>.enabled:false——
        # 否则面板提示已开启+装了 agent，雷达却永远静默（§48.1 面板对齐）
        config.ensure_state_dirs()
        self.addCleanup(_clean_state)
        config.CONFIG_PATH.write_text(
            "sources:\n  slack:\n    enabled: false\n"
            "  gmail:\n    enabled: false\n", encoding="utf-8")
        config.SETTINGS_OVERRIDES_PATH.write_text(
            json.dumps({"slack_enabled": True, "gmail_enabled": True}),
            encoding="utf-8")
        self.addCleanup(
            lambda: config.SETTINGS_OVERRIDES_PATH.unlink(missing_ok=True))
        cfg = config.load_config()
        self.assertTrue(sources.enabled(cfg, "slack"))
        self.assertTrue(sources.enabled(cfg, "gmail"))

    def test_config_yaml_parses_slack_obsidian_enabled(self):
        # config.py 真的解析嵌套写法（此前 slack/obsidian 的同款写法静默无效）
        config.ensure_state_dirs()
        self.addCleanup(_clean_state)
        config.CONFIG_PATH.write_text(
            "sources:\n"
            "  slack:\n    enabled: false\n"
            "  obsidian:\n    enabled: false\n", encoding="utf-8")
        cfg = config.load_config()
        self.assertFalse(cfg.slack_enabled)
        self.assertFalse(cfg.obsidian_enabled)
        self.assertTrue(cfg.gmail_enabled)
        self.assertFalse(sources.enabled(cfg, "slack"))
        self.assertFalse(sources.enabled(cfg, "obsidian"))


# --------------------------------------------------------------------------- #
# 2 — a disabled source is TRULY silent (no health entry, no analytics)
# --------------------------------------------------------------------------- #
class DisabledSilenceTestCase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        _clean_state()
        self.addCleanup(_clean_state)

    def _fail_fetch(self, *a, **kw):
        self.fail("fetched while the source is off")

    def test_gmail_off_no_health_no_analytics(self):
        # 预埋一条历史条目：关源后必须被清除（僵尸 last_attempt 不再冒充活）
        radar_health.update_radar_health("gmail", ok=False, skip_reason="auth_failed")
        cfg = _cfg(gmail_enabled=False)
        created = radar_gmail.scan(cfg, fetcher=self._fail_fetch)
        self.assertEqual(created, 0)
        data = json.loads(radar_health.HEALTH_PATH.read_text(encoding="utf-8"))
        self.assertNotIn("gmail", data)                      # entry removed
        self.assertEqual(_events(), [])                      # no radar_skip beacon

    def test_slack_off_no_health_no_analytics(self):
        radar_health.update_radar_health("slack", ok=True)
        cfg = _cfg(features={"slack_radar": False})
        created = radar_slack.scan(cfg, fetcher=self._fail_fetch)
        self.assertEqual(created, 0)
        data = json.loads(radar_health.HEALTH_PATH.read_text(encoding="utf-8"))
        self.assertNotIn("slack", data)
        self.assertEqual(_events(), [])

    def test_no_health_file_stays_absent(self):
        # 条目本就不存在时连文件都不写（mtime 语义：只有真实雷达活动才动文件）
        cfg = _cfg(gmail_enabled=False)
        radar_gmail.scan(cfg, fetcher=self._fail_fetch)
        self.assertFalse(radar_health.HEALTH_PATH.exists())

    def test_obsidian_off_skips_before_lock_analytics(self):
        # disabled 早退必须先于锁竞争的 radar_skip 信标——锁被别的 pass 占着
        # 时，关掉的源也不许发 lock_held analytics（真静默无竞争窗）
        from act import radar
        config.CONFIG_PATH.write_text(
            "features:\n  obsidian_radar: false\n", encoding="utf-8")
        held = radar._acquire_pass_lock()   # 模拟另一个 pass 正持锁
        if held is not None:
            self.addCleanup(held.close)
        summary = radar.scan(runner=lambda t: self.fail("scanned while off"))
        self.assertIn("source obsidian is off (act.lib.sources)",
                      summary["skipped"])
        self.assertEqual(_events(), [])                  # 无 lock_held 信标
        self.assertFalse(radar_health.HEALTH_PATH.exists())


# --------------------------------------------------------------------------- #
# 3 — dead-source alert (liveness) + anti-nag + recovery
# --------------------------------------------------------------------------- #
class LivenessAlertTestCase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        _clean_state()
        self.addCleanup(_clean_state)
        # 睡醒/冷启动宽限的进程内时钟：默认判例在「已跑热的 daemon」形态下
        # 走（last_pass = 现在，无宽限）；冷启动判例自己把 last_pass 归 None
        actd._wake_state.update(
            {"last_pass": _dt.datetime.now(_dt.timezone.utc).timestamp(),
             "last_mono": None, "grace_until": 0.0})
        self.addCleanup(actd._wake_state.update,
                        {"last_pass": None, "last_mono": None,
                         "grace_until": 0.0})
        # 无基线首见台账（§48.3 兜底）也是进程内状态：跨判例串味会让「首见」
        # 时刻提前、把别的判例误推过阈值
        actd._no_baseline_since.clear()
        self.addCleanup(actd._no_baseline_since.clear)

    @staticmethod
    def _seed(source: str, last_ok: str, last_attempt: str = None):
        data = {}
        if radar_health.HEALTH_PATH.exists():
            data = json.loads(radar_health.HEALTH_PATH.read_text(encoding="utf-8"))
        data[source] = {"last_attempt": last_attempt or last_ok,
                        "last_ok": last_ok, "skip_reason": None}
        radar_health.HEALTH_PATH.write_text(json.dumps(data), encoding="utf-8")

    def test_stale_source_alerts_once(self):
        self._seed("gmail", _iso(7 * 3600))           # 7h > 6h threshold
        notified: set = set()
        msgs = actd._check_radar_liveness(notified)
        self.assertEqual(len(msgs), 1)
        self.assertIn("Gmail", msgs[0][0] + msgs[0][1])
        # anti-nag：第二个 pass 不重复响
        self.assertEqual(actd._check_radar_liveness(notified), [])

    def test_recovery_clears_and_rearms(self):
        notified: set = set()
        self._seed("gmail", _iso(7 * 3600))
        self.assertEqual(len(actd._check_radar_liveness(notified)), 1)
        self._seed("gmail", _iso(60))                 # 恢复：出账
        self.assertEqual(actd._check_radar_liveness(notified), [])
        self.assertNotIn("gmail", notified)
        self._seed("gmail", _iso(7 * 3600))           # 再死：再响一次
        self.assertEqual(len(actd._check_radar_liveness(notified)), 1)

    def test_fresh_source_is_quiet(self):
        self._seed("gmail", _iso(60))
        self.assertEqual(actd._check_radar_liveness(set()), [])

    def test_disabled_source_never_alerts_and_gets_pruned(self):
        # 配置现读：磁盘上的 config.yaml 说了算（App 翻开关立即生效）
        self._seed("gmail", _iso(7 * 3600))
        config.CONFIG_PATH.write_text(
            "sources:\n  gmail:\n    enabled: false\n", encoding="utf-8")
        notified = {"gmail"}
        self.assertEqual(actd._check_radar_liveness(notified), [])
        self.assertNotIn("gmail", notified)           # 出账
        data = json.loads(radar_health.HEALTH_PATH.read_text(encoding="utf-8"))
        self.assertNotIn("gmail", data)               # 残留条目被清（防僵尸）

    def test_alert_recheck_silences_just_disabled_source(self):
        # TOCTOU 收窄（§48.3）：巡检开头读到「开」、告警落笔前用户关掉本源
        # ——落笔前的 enabled 复核必须拦下这条通知（关掉的源全静默 §48.2）。
        # 注入缝：包一层 load_config，首读之后立刻把开关写成关。
        self._seed("gmail", _iso(7 * 3600))
        orig = actd.config.load_config
        calls = {"n": 0}

        def flipping(*a, **kw):
            cfg = orig(*a, **kw)
            calls["n"] += 1
            if calls["n"] == 1:     # 首读（巡检开头）后模拟用户翻开关
                config.CONFIG_PATH.write_text(
                    "sources:\n  gmail:\n    enabled: false\n",
                    encoding="utf-8")
            return cfg

        actd.config.load_config = flipping
        try:
            notified: set = set()
            self.assertEqual(actd._check_radar_liveness(notified), [])
            self.assertNotIn("gmail", notified)   # 没落台账：之后重开再死会响
            self.assertGreaterEqual(calls["n"], 2)  # 复核真的读了第二次
        finally:
            actd.config.load_config = orig

    def test_config_reloaded_every_call(self):
        # 冻结 cfg 回归：同一进程内翻开关，下一次巡检立刻改判——
        # 开→关：不再对刚关的源发死亡告警；关→开：恢复巡检不清活雷达的 health
        self._seed("gmail", _iso(7 * 3600))
        notified: set = set()
        self.assertEqual(len(actd._check_radar_liveness(notified)), 1)   # 开着:响
        config.CONFIG_PATH.write_text(
            "sources:\n  gmail:\n    enabled: false\n", encoding="utf-8")
        self.assertEqual(actd._check_radar_liveness(notified), [])       # 关了:静默
        config.CONFIG_PATH.unlink()                                      # 再打开
        self._seed("gmail", _iso(60))
        self.assertEqual(actd._check_radar_liveness(notified), [])
        data = json.loads(radar_health.HEALTH_PATH.read_text(encoding="utf-8"))
        self.assertIn("gmail", data)                  # 活雷达的 health 没被误清

    def test_no_baseline_no_alert(self):
        # 从未跑过（无条目/无时间戳）首个阈值窗内不能宣布死亡 —— 静默
        # （持续无基线超窗的告警见 test_no_baseline_overdue_alarms）
        self.assertEqual(actd._check_radar_liveness(set()), [])
        radar_health.HEALTH_PATH.write_text(
            json.dumps({"gmail": {"last_attempt": None, "last_ok": None,
                                  "skip_reason": None}}), encoding="utf-8")
        self.assertEqual(actd._check_radar_liveness(set()), [])

    def test_no_baseline_overdue_alarms(self):
        # §48.3 无基线兜底：plist 写成但 launchctl load 失败（install.sh 吞
        # stderr、面板修复回执不覆盖脚本路径）→ 雷达从未落笔。开着 + 持续
        # 无基线超 liveness 阈值 = 死亡告警（首见台账注入缝 missing_since）
        now = _dt.datetime.now(_dt.timezone.utc)
        missing = {"gmail": now.timestamp() - 7 * 3600}   # 首见于 7h 前 > 6h
        notified: set = set()
        msgs = actd._check_radar_liveness(notified, now=now,
                                          missing_since=missing)
        self.assertEqual(len(msgs), 1)
        self.assertIn("Gmail", msgs[0][0] + msgs[0][1])
        # anti-nag：第二个 pass 不重复响
        self.assertEqual(actd._check_radar_liveness(
            notified, now=now, missing_since=missing), [])

    def test_no_baseline_ledger_clears_when_radar_writes(self):
        # 雷达终于落笔（哪怕只有 last_attempt）→ 出无基线台账，改走
        # is_stale 正常判据；新鲜时间戳 = 静默，台账里的旧首见时刻作废
        now = _dt.datetime.now(_dt.timezone.utc)
        missing = {"gmail": now.timestamp() - 7 * 3600}
        self._seed("gmail", _iso(60))
        self.assertEqual(actd._check_radar_liveness(
            set(), now=now, missing_since=missing), [])
        self.assertNotIn("gmail", missing)

    def test_no_baseline_disabled_source_stays_silent(self):
        # 关掉的源不吃无基线兜底（§48.2 真静默优先），台账条目一并清掉
        now = _dt.datetime.now(_dt.timezone.utc)
        missing = {"gmail": now.timestamp() - 7 * 3600}
        config.CONFIG_PATH.write_text(
            "sources:\n  gmail:\n    enabled: false\n", encoding="utf-8")
        self.assertEqual(actd._check_radar_liveness(
            set(), now=now, missing_since=missing), [])
        self.assertNotIn("gmail", missing)

    def test_never_ok_falls_back_to_last_attempt(self):
        # 配好后一直失败到停摆（last_attempt 也超期、没 last_ok）算死亡基线
        radar_health.HEALTH_PATH.write_text(
            json.dumps({"gmail": {"last_attempt": _iso(7 * 3600),
                                  "last_ok": None,
                                  "skip_reason": "auth_failed"}}),
            encoding="utf-8")
        msgs = actd._check_radar_liveness(set())
        self.assertEqual(len(msgs), 1)

    def test_failing_but_attempting_radar_is_not_dead(self):
        # last_ok 超期但 last_attempt 新鲜 = 雷达活着、只是一直失败——
        # 那是诊断卡（skip_reason）的辖区，不是死亡告警（取较新时间戳判据）
        self._seed("gmail", last_ok=_iso(7 * 3600), last_attempt=_iso(120))
        self.assertEqual(actd._check_radar_liveness(set()), [])

    # ---- 睡醒宽限（wall-clock 跳变） ----

    def _now(self, offset_s: float = 0.0) -> _dt.datetime:
        return _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(seconds=offset_s)

    def test_wake_from_sleep_does_not_false_alarm(self):
        # 合盖 8h：唤醒后第一个 pass 的 health 必然整体超期，但这是睡醒不是
        # 死亡 —— 宽限期内静默，让雷达先补跑
        notified: set = set()
        self._seed("gmail", _iso(60))
        t0 = self._now()
        self.assertEqual(actd._check_radar_liveness(notified, now=t0), [])
        t1 = self._now(8 * 3600)                       # 醒来：跳变 8h
        self._seed("gmail", _iso(-8 * 3600 + 7 * 3600))  # 现在看已超 6h 阈值
        self.assertEqual(actd._check_radar_liveness(notified, now=t1), [])
        self.assertNotIn("gmail", notified)            # 台账没被污染

    def test_wake_grace_expires_then_real_death_still_alarms(self):
        # 宽限只有一个最大雷达周期 + 余量：醒来后雷达真不补跑 → 照样告警。
        # 本判例只考 gmail：时间推进超过 8h，从未落笔的 slack 会吃 §48.3
        # 无基线兜底（6h）跟着响——关掉它，别让两条告警混在一起数
        config.CONFIG_PATH.write_text(
            "sources:\n  slack:\n    enabled: false\n", encoding="utf-8")
        notified: set = set()
        actd._check_radar_liveness(notified, now=self._now())
        self._seed("gmail", _iso(3600))                # 睡前 1h 就停摆了
        t = 8 * 3600                                   # 跳变 8h → 进入宽限
        self.assertEqual(
            actd._check_radar_liveness(notified, now=self._now(t)), [])
        collected: list = []
        while t < 8 * 3600 + actd._WAKE_GRACE_SECONDS + 300:
            t += 250                                   # 正常节奏推进，无新跳变
            collected += actd._check_radar_liveness(notified, now=self._now(t))
        self.assertEqual(len(collected), 1)            # 宽限一过恢复评判、只响一次

    def test_cold_start_first_pass_does_not_false_alarm(self):
        # actd 重启（关机 ≥ 阈值后开机 RunAtLoad / 升级重启）：_wake_state 是
        # 进程内存，首 pass 没有跳变可测，但雷达同样还没落笔——首 pass 视同
        # 睡醒种一次宽限，不评判、不污染台账
        actd._wake_state.update({"last_pass": None, "grace_until": 0.0})
        self._seed("gmail", _iso(7 * 3600))
        notified: set = set()
        self.assertEqual(
            actd._check_radar_liveness(notified, now=self._now()), [])
        self.assertNotIn("gmail", notified)

    def test_cold_start_grace_expires_then_real_death_alarms(self):
        # 冷启动宽限过后（正常节奏推进，无新跳变）真死亡照样告警、只响一次
        actd._wake_state.update({"last_pass": None, "grace_until": 0.0})
        self._seed("gmail", _iso(7 * 3600))
        notified: set = set()
        t = 0
        self.assertEqual(
            actd._check_radar_liveness(notified, now=self._now(t)), [])
        collected: list = []
        while t < actd._WAKE_GRACE_SECONDS + 300:
            t += 250
            collected += actd._check_radar_liveness(notified, now=self._now(t))
        self.assertEqual(len(collected), 1)

    def test_slow_interval_passes_are_not_wake_jumps(self):
        # 跳变判定必须吃主循环的**真实** interval（--interval 优先于 config）
        # ——否则 `--interval 600` 形态下每个正常 pass 都被判睡醒、宽限永不
        # 结束、liveness 被静默饿死
        notified: set = set()
        actd._check_radar_liveness(notified, now=self._now(), interval=600)
        self._seed("gmail", _iso(7 * 3600))
        msgs = actd._check_radar_liveness(
            notified, now=self._now(600), interval=600)   # 600s 是常态节奏
        self.assertEqual(len(msgs), 1)                    # 照常评判、照常告警

    def test_long_pass_is_not_a_wake_jump(self):
        # 长 pass（如 process_raising 连续吃满 420s 的 claude 超时）：wall 与
        # monotonic 两个时钟**同步前进**、差值 ≈ 0 ——不是睡醒，不得续宽限。
        # 只看 wall 跳变的旧判据会把每轮长 pass 都判成睡醒、grace_until 每轮
        # 重置、真死亡的源永远不告警（liveness 被静默饿死）
        notified: set = set()
        t, m = 0.0, 1000.0
        actd._check_radar_liveness(notified, now=self._now(t), mono=m)
        self._seed("gmail", _iso(7 * 3600))
        collected: list = []
        for _ in range(5):                     # 连续 5 轮 ~430s 的慢 pass
            t += 430.0
            m += 430.0
            collected += actd._check_radar_liveness(
                notified, now=self._now(t), mono=m)
        self.assertEqual(len(collected), 1)    # 第一轮就照常评判、只响一次

    def test_real_sleep_mono_frozen_is_still_graced(self):
        # 真睡眠：wall 前进而 monotonic 停摆（macOS mach_absolute_time 睡眠
        # 不计时）——差值 = 真实挂起时长，照旧进宽限、不污染台账
        notified: set = set()
        self._seed("gmail", _iso(60))
        actd._check_radar_liveness(notified, now=self._now(), mono=500.0)
        self._seed("gmail", _iso(7 * 3600))
        msgs = actd._check_radar_liveness(
            notified, now=self._now(8 * 3600), mono=510.0)  # mono 只走 10s
        self.assertEqual(msgs, [])
        self.assertNotIn("gmail", notified)

    def test_plist_deleted_death_still_alarms(self):
        # 真死亡形态（plist 被删/调度停摆）：pass 以正常节奏推进（无跳变），
        # last_ok 与 last_attempt 一起停摆 → 照样告警，宽限不误伤
        notified: set = set()
        t0 = self._now()
        self.assertEqual(actd._check_radar_liveness(notified, now=t0), [])
        self._seed("gmail", _iso(7 * 3600))            # 双时间戳都 7h 前
        t1 = self._now(10)                             # 正常 10s pass 间隔
        msgs = actd._check_radar_liveness(notified, now=t1)
        self.assertEqual(len(msgs), 1)


# --------------------------------------------------------------------------- #
# 4 — dashboard radar_sources projection shape
# --------------------------------------------------------------------------- #
class RadarSourcesProjectionTestCase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        _clean_state()
        self.addCleanup(_clean_state)

    def _build(self, cfg):
        return dashboard.build_dashboard(reqs=[], agents=[], cfg=cfg,
                                         archived=[])

    def test_shape_all_sources_present(self):
        dash = self._build(_cfg())
        rs = dash["radar_sources"]
        self.assertEqual(set(rs), set(sources.SOURCES))
        for entry in rs.values():
            # §48.7 add-only：last_attempt / test_round；§48.4 add-only（2026-09-05）：
            # intent / secret_present（意愿信号，判例 tests/test_dashboard_source_intent.py）
            self.assertEqual(set(entry),
                             {"enabled", "last_ok", "skip_reason", "stale",
                              "last_attempt", "test_round", "intent", "secret_present"})
            self.assertIsInstance(entry["enabled"], bool)
            self.assertIsInstance(entry["stale"], bool)
            self.assertIsInstance(entry["intent"], bool)
            self.assertIsInstance(entry["secret_present"], bool)

    def test_disabled_source_projects_enabled_false(self):
        # 投影配置现读：开关状态以磁盘 config.yaml 为准
        config.CONFIG_PATH.write_text(
            "sources:\n  gmail:\n    enabled: false\n", encoding="utf-8")
        dash = self._build(_cfg())
        gm = dash["radar_sources"]["gmail"]
        self.assertFalse(gm["enabled"])
        self.assertFalse(gm["stale"])                 # 关着永不 stale
        self.assertIsNone(gm["last_ok"])

    def test_projection_ignores_frozen_cfg_snapshot(self):
        # actd 启动时冻结的 cfg 说「关」、用户已把磁盘配置翻回「开」——
        # 投影必须跟磁盘走（App 翻开关立即在看板生效）
        dash = self._build(_cfg(gmail_enabled=False))   # 冻结快照：关
        self.assertTrue(dash["radar_sources"]["gmail"]["enabled"])  # 磁盘：开

    def test_stale_flag_appears_and_clears(self):
        LivenessAlertTestCase._seed("gmail", _iso(7 * 3600))
        self.assertTrue(self._build(_cfg())["radar_sources"]["gmail"]["stale"])
        LivenessAlertTestCase._seed("gmail", _iso(60))  # 恢复 → 自动消
        gm = self._build(_cfg())["radar_sources"]["gmail"]
        self.assertFalse(gm["stale"])
        self.assertIsNotNone(gm["last_ok"])

    def test_skip_reason_projection_is_vocabulary_gated(self):
        # §48.4 词表投影纪律：radar 写进 health 的自由文本（错误摘录/本机
        # 路径）不许随 dashboard 出机——mcp_failed:<detail> 去尾留裸码
        radar_health.update_radar_health(
            "slack", ok=False,
            skip_reason="mcp_failed: OSError: /Users/zelin/secret/token.txt")
        dash = self._build(_cfg())
        self.assertEqual(dash["radar_sources"]["slack"]["skip_reason"],
                         "mcp_failed")
        self.assertNotIn("zelin", json.dumps(dash))       # 路径没跟着出去
        # 词表外的任意奇串一律折叠为 "error"
        radar_health.update_radar_health(
            "slack", ok=False, skip_reason="weird /private/tmp fragment")
        dash = self._build(_cfg())
        self.assertEqual(dash["radar_sources"]["slack"]["skip_reason"], "error")
        self.assertNotIn("fragment", json.dumps(dash))

    def test_disabled_source_hides_stale_health_same_pass(self):
        # 关源后的第一个 pass：liveness 清理还没跑（它在 dashboard 构建之
        # 后），投影就必须已经不带健康摘要——「关着 = null」当 pass 生效
        radar_health.update_radar_health("gmail", ok=False, skip_reason="auth_failed")
        config.CONFIG_PATH.write_text(
            "sources:\n  gmail:\n    enabled: false\n", encoding="utf-8")
        gm = self._build(_cfg())["radar_sources"]["gmail"]
        self.assertFalse(gm["enabled"])
        self.assertIsNone(gm["last_ok"])
        self.assertIsNone(gm["skip_reason"])

    def test_stale_ignores_wake_grace(self):
        # §48.4：stale 不吃通知侧的睡醒/冷启动宽限——投影是无状态的磁盘
        # 真值函数（一次性 `python -m act.lib.dashboard` 进程也在产出它），
        # 即便 actd 侧正处于冷启动宽限，投影照报 stale
        actd._wake_state.update(
            {"last_pass": None, "last_mono": None, "grace_until": 0.0})
        self.addCleanup(actd._wake_state.update,
                        {"last_pass": None, "last_mono": None,
                         "grace_until": 0.0})
        LivenessAlertTestCase._seed("gmail", _iso(7 * 3600))
        self.assertEqual(
            actd._check_radar_liveness(set()), [])        # 通知侧：宽限静默
        self.assertTrue(
            self._build(_cfg())["radar_sources"]["gmail"]["stale"])  # 投影照报

    def test_health_entry_fields_flow_through(self):
        radar_health.update_radar_health("gmail", ok=False, skip_reason="auth_failed")
        gm = self._build(_cfg())["radar_sources"]["gmail"]
        self.assertTrue(gm["enabled"])
        self.assertEqual(gm["skip_reason"], "auth_failed")


# --------------------------------------------------------------------------- #
# 4b — skip_reason 出机清洗（§48.4 词表投影纪律）
# --------------------------------------------------------------------------- #
class PublicSkipReasonTestCase(unittest.TestCase):
    def test_vocabulary_codes_pass_through(self):
        for code in ("auth_failed", "no_credentials", "vault_empty",
                     "command_bad_output", "mcp_not_configured"):
            self.assertEqual(radar_health.public_skip_reason(code), code)

    def test_mcp_failed_detail_is_stripped(self):
        self.assertEqual(
            radar_health.public_skip_reason("mcp_failed: exit 1: /Users/x/y"),
            "mcp_failed")
        self.assertEqual(radar_health.public_skip_reason("mcp_failed"), "mcp_failed")

    def test_out_of_vocab_folds_to_error(self):
        self.assertEqual(radar_health.public_skip_reason("OSError: boom"), "error")
        self.assertEqual(radar_health.public_skip_reason("/Users/zelin/p"), "error")

    def test_empty_and_non_string_are_none(self):
        self.assertIsNone(radar_health.public_skip_reason(None))
        self.assertIsNone(radar_health.public_skip_reason(""))
        self.assertIsNone(radar_health.public_skip_reason(42))


# --------------------------------------------------------------------------- #
# 5 — CLI entry (install.sh plist gate)
# --------------------------------------------------------------------------- #
class CliTestCase(unittest.TestCase):
    def setUp(self):
        _clean_state()
        self.addCleanup(_clean_state)

    def test_enabled_exits_zero(self):
        self.assertEqual(sources.main(["--enabled", "gmail"]), 0)

    def test_disabled_exits_three(self):
        # 「off」独占 exit 3——exit 1 是 python 崩溃的环境码，两者若同码，
        # pkg postinstall 里探针一崩就等于「每次升级静默退役雷达」
        config.CONFIG_PATH.write_text(
            "sources:\n  gmail:\n    enabled: false\n", encoding="utf-8")
        self.assertEqual(sources.main(["--enabled", "gmail"]), 3)

    def test_feature_flag_off_exits_three(self):
        config.CONFIG_PATH.write_text(
            "features:\n  gmail_radar: false\n", encoding="utf-8")
        self.assertEqual(sources.main(["--enabled", "gmail"]), 3)

    def test_unknown_source_exits_two(self):
        self.assertEqual(sources.main(["--enabled", "nope"]), 2)

    def test_no_args_exits_two(self):
        self.assertEqual(sources.main([]), 2)


# --------------------------------------------------------------------------- #
# 5b — install.sh plist gate (textual drift-guard; a full install.sh run
#      touches launchd/crontab and can't happen in a sandbox)
# --------------------------------------------------------------------------- #
class InstallGateDriftGuardTestCase(unittest.TestCase):
    REPO_ROOT = Path(__file__).resolve().parent.parent

    def test_install_sh_gates_radar_plists_on_the_source_switch(self):
        text = (self.REPO_ROOT / "install.sh").read_text(encoding="utf-8")
        # 闸门函数存在且吃的是 act.lib.sources 的 CLI
        self.assertIn("radar_source_enabled()", text)
        self.assertIn("-m act.lib.sources", text)
        # 探针必须从 $REPO_ROOT 跑（pkg postinstall 的 cwd 是 Installer 临时
        # 目录，不 cd 的话 -m 直接 ModuleNotFoundError）——对齐同文件里其余
        # `-m act.*` 调用的 (cd "$REPO_ROOT" && ...) 先例
        gate = text[text.index("radar_source_enabled()"):]
        gate = gate[:gate.index("\n}")]   # 函数体（闭括号在行首）
        self.assertIn('(cd "$REPO_ROOT"', gate)
        # 「off」双重校验：独占 exit 3 且 stdout 字面量 off；其余 fail-open
        self.assertIn('"$rc" -eq 3', gate)
        self.assertIn('"$out" = "off"', gate)
        # 两个 radar plist 都映射到了源名；关着 = unload + rm（防复活）
        self.assertIn('*.gmailradar) plist_source="gmail"', text)
        self.assertIn('*.slackradar) plist_source="slack"', text)
        self.assertIn('! radar_source_enabled "$plist_source"', text)

    def _probe(self, cwd, config_yaml: str = "") -> subprocess.CompletedProcess:
        """按 install.sh 闸门的方式跑真探针（子进程，沙箱 HOME）。"""
        env = dict(os.environ)
        env["AIASSISTANT_HOME"] = os.environ["AIASSISTANT_HOME"]
        env.pop("PYTHONPATH", None)   # install.sh 不设它；import 只靠 cwd
        if config_yaml:
            config.ensure_state_dirs()
            config.CONFIG_PATH.write_text(config_yaml, encoding="utf-8")
            self.addCleanup(_clean_state)
        return subprocess.run(
            [sys.executable, "-m", "act.lib.sources", "--enabled", "gmail"],
            cwd=str(cwd), env=env, capture_output=True, text=True, timeout=30)

    def test_probe_from_non_repo_cwd_fails_open_not_off(self):
        # pkg postinstall 形态：cwd 不在 repo → -m 崩（exit≠3 / stdout≠off）
        # → 闸门判「fail-open 照装」，绝不误判成「源已关」
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            proc = self._probe(cwd=tmp)
        self.assertNotEqual(proc.returncode, 3)
        self.assertNotEqual(proc.stdout.strip(), "off")

    def test_probe_from_repo_root_reports_off(self):
        # 同一探针在 repo root + 关掉的源：exit 3 + stdout "off"（闸门唯一
        # 认「关」的组合）
        proc = self._probe(cwd=self.REPO_ROOT,
                           config_yaml="sources:\n  gmail:\n    enabled: false\n")
        self.assertEqual(proc.returncode, 3)
        self.assertEqual(proc.stdout.strip(), "off")


if __name__ == "__main__":
    unittest.main()
