"""源开关真源 + 源死亡告警（CONTRACT §46）判例。

五组契约（全部注入缝 mock，不 spawn 真 claude、不触网）：

1. ``sources.enabled()`` 真值表 — feature flag 与 sources.<src>.enabled 合取；
   未知源 fail-closed。
2. 关闭真静默 — 关掉的源跑 radar：health 无条目（既有条目被清除）、无
   radar_skip analytics 事件；`disabled` skip_reason 已退役（deprecated）。
3. 源死亡告警 — 开着的源 last_ok 超 liveness 阈值 → notify 一次（anti-nag：
   不跨阈值不重复响），恢复自动出账；关掉的源天然不进循环且残留条目被清。
4. dashboard ``radar_sources`` 投影形状（add-only 字段）。
5. ``python3 -m act.lib.sources --enabled <src>`` CLI 出口码（install.sh 的
   plist 防复活闸门吃这个）。
"""
import datetime as _dt
import json
import unittest

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import actd, radar_gmail, radar_slack
from act.lib import analytics, config, dashboard, health, sources


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
    for p in (health.HEALTH_PATH, analytics.EVENTS_PATH, config.CONFIG_PATH):
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
        health.update_radar_health("gmail", ok=False, skip_reason="auth_failed")
        cfg = _cfg(gmail_enabled=False)
        created = radar_gmail.scan(cfg, fetcher=self._fail_fetch)
        self.assertEqual(created, 0)
        data = json.loads(health.HEALTH_PATH.read_text(encoding="utf-8"))
        self.assertNotIn("gmail", data)                      # entry removed
        self.assertEqual(_events(), [])                      # no radar_skip beacon

    def test_slack_off_no_health_no_analytics(self):
        health.update_radar_health("slack", ok=True)
        cfg = _cfg(features={"slack_radar": False})
        created = radar_slack.scan(cfg, fetcher=self._fail_fetch)
        self.assertEqual(created, 0)
        data = json.loads(health.HEALTH_PATH.read_text(encoding="utf-8"))
        self.assertNotIn("slack", data)
        self.assertEqual(_events(), [])

    def test_no_health_file_stays_absent(self):
        # 条目本就不存在时连文件都不写（mtime 语义：只有真实雷达活动才动文件）
        cfg = _cfg(gmail_enabled=False)
        radar_gmail.scan(cfg, fetcher=self._fail_fetch)
        self.assertFalse(health.HEALTH_PATH.exists())


# --------------------------------------------------------------------------- #
# 3 — dead-source alert (liveness) + anti-nag + recovery
# --------------------------------------------------------------------------- #
class LivenessAlertTestCase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        _clean_state()
        self.addCleanup(_clean_state)

    @staticmethod
    def _seed(source: str, last_ok: str, last_attempt: str = None):
        data = {}
        if health.HEALTH_PATH.exists():
            data = json.loads(health.HEALTH_PATH.read_text(encoding="utf-8"))
        data[source] = {"last_attempt": last_attempt or last_ok,
                        "last_ok": last_ok, "skip_reason": None}
        health.HEALTH_PATH.write_text(json.dumps(data), encoding="utf-8")

    def test_stale_source_alerts_once(self):
        self._seed("gmail", _iso(7 * 3600))           # 7h > 6h threshold
        cfg = _cfg()
        notified: set = set()
        msgs = actd._check_radar_liveness(cfg, notified)
        self.assertEqual(len(msgs), 1)
        self.assertIn("Gmail", msgs[0][0] + msgs[0][1])
        # anti-nag：第二个 pass 不重复响
        self.assertEqual(actd._check_radar_liveness(cfg, notified), [])

    def test_recovery_clears_and_rearms(self):
        cfg = _cfg()
        notified: set = set()
        self._seed("gmail", _iso(7 * 3600))
        self.assertEqual(len(actd._check_radar_liveness(cfg, notified)), 1)
        self._seed("gmail", _iso(60))                 # 恢复：出账
        self.assertEqual(actd._check_radar_liveness(cfg, notified), [])
        self.assertNotIn("gmail", notified)
        self._seed("gmail", _iso(7 * 3600))           # 再死：再响一次
        self.assertEqual(len(actd._check_radar_liveness(cfg, notified)), 1)

    def test_fresh_source_is_quiet(self):
        self._seed("gmail", _iso(60))
        self.assertEqual(actd._check_radar_liveness(_cfg(), set()), [])

    def test_disabled_source_never_alerts_and_gets_pruned(self):
        self._seed("gmail", _iso(7 * 3600))
        cfg = _cfg(gmail_enabled=False)
        notified = {"gmail"}
        self.assertEqual(actd._check_radar_liveness(cfg, notified), [])
        self.assertNotIn("gmail", notified)           # 出账
        data = json.loads(health.HEALTH_PATH.read_text(encoding="utf-8"))
        self.assertNotIn("gmail", data)               # 残留条目被清（防僵尸）

    def test_no_baseline_no_alert(self):
        # 从未跑过（无条目/无时间戳）不能诚实宣布死亡 —— 静默
        self.assertEqual(actd._check_radar_liveness(_cfg(), set()), [])
        health.HEALTH_PATH.write_text(
            json.dumps({"gmail": {"last_attempt": None, "last_ok": None,
                                  "skip_reason": None}}), encoding="utf-8")
        self.assertEqual(actd._check_radar_liveness(_cfg(), set()), [])

    def test_never_ok_falls_back_to_last_attempt(self):
        # 配好后一直失败（有 last_attempt 没 last_ok）也算死亡基线
        health.HEALTH_PATH.write_text(
            json.dumps({"gmail": {"last_attempt": _iso(7 * 3600),
                                  "last_ok": None,
                                  "skip_reason": "auth_failed"}}),
            encoding="utf-8")
        msgs = actd._check_radar_liveness(_cfg(), set())
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
            self.assertEqual(set(entry),
                             {"enabled", "last_ok", "skip_reason", "stale"})
            self.assertIsInstance(entry["enabled"], bool)
            self.assertIsInstance(entry["stale"], bool)

    def test_disabled_source_projects_enabled_false(self):
        dash = self._build(_cfg(gmail_enabled=False))
        gm = dash["radar_sources"]["gmail"]
        self.assertFalse(gm["enabled"])
        self.assertFalse(gm["stale"])                 # 关着永不 stale
        self.assertIsNone(gm["last_ok"])

    def test_stale_flag_appears_and_clears(self):
        LivenessAlertTestCase._seed("gmail", _iso(7 * 3600))
        self.assertTrue(self._build(_cfg())["radar_sources"]["gmail"]["stale"])
        LivenessAlertTestCase._seed("gmail", _iso(60))  # 恢复 → 自动消
        gm = self._build(_cfg())["radar_sources"]["gmail"]
        self.assertFalse(gm["stale"])
        self.assertIsNotNone(gm["last_ok"])

    def test_health_entry_fields_flow_through(self):
        health.update_radar_health("gmail", ok=False, skip_reason="auth_failed")
        gm = self._build(_cfg())["radar_sources"]["gmail"]
        self.assertTrue(gm["enabled"])
        self.assertEqual(gm["skip_reason"], "auth_failed")


# --------------------------------------------------------------------------- #
# 5 — CLI entry (install.sh plist gate)
# --------------------------------------------------------------------------- #
class CliTestCase(unittest.TestCase):
    def setUp(self):
        _clean_state()
        self.addCleanup(_clean_state)

    def test_enabled_exits_zero(self):
        self.assertEqual(sources.main(["--enabled", "gmail"]), 0)

    def test_disabled_exits_one(self):
        config.CONFIG_PATH.write_text(
            "sources:\n  gmail:\n    enabled: false\n", encoding="utf-8")
        self.assertEqual(sources.main(["--enabled", "gmail"]), 1)

    def test_feature_flag_off_exits_one(self):
        config.CONFIG_PATH.write_text(
            "features:\n  gmail_radar: false\n", encoding="utf-8")
        self.assertEqual(sources.main(["--enabled", "gmail"]), 1)

    def test_unknown_source_exits_two(self):
        self.assertEqual(sources.main(["--enabled", "nope"]), 2)

    def test_no_args_exits_two(self):
        self.assertEqual(sources.main([]), 2)


# --------------------------------------------------------------------------- #
# 5b — install.sh plist gate (textual drift-guard; a full install.sh run
#      touches launchd/crontab and can't happen in a sandbox)
# --------------------------------------------------------------------------- #
class InstallGateDriftGuardTestCase(unittest.TestCase):
    def test_install_sh_gates_radar_plists_on_the_source_switch(self):
        from pathlib import Path
        script = (Path(__file__).resolve().parent.parent / "install.sh")
        text = script.read_text(encoding="utf-8")
        # 闸门函数存在且吃的是 act.lib.sources 的 CLI（exit 1 = off）
        self.assertIn("radar_source_enabled()", text)
        self.assertIn("-m act.lib.sources", text)
        # 两个 radar plist 都映射到了源名；关着 = unload + rm（防复活）
        self.assertIn('*.gmailradar) plist_source="gmail"', text)
        self.assertIn('*.slackradar) plist_source="slack"', text)
        self.assertIn('! radar_source_enabled "$plist_source"', text)


if __name__ == "__main__":
    unittest.main()
