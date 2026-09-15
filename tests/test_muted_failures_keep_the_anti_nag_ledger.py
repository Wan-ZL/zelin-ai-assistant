"""失败通知被开关静音时，anti-nag 台账不许被花掉（CONTRACT §28 追记 2026-09-12 / §48）。

issue #29 的「失败通知」开关关掉时，`act/lib/notify.notify` 在写方吃掉这一类。
两道失败扫描（凭证失效 / §48 源死亡）**照跑**——僵尸 health 清理、恢复出账、
无基线首见台账都住在扫描里，跳过它们会让这些副作用在开关关着期间停摆，开关
翻回来时无基线时钟还从头起算、真告警又被饿死一个 liveness 窗。照跑但不落
anti-nag 台账：一条没人看见的通知不许把「报过了」花掉，否则开关翻回来时这条
告警在本进程余生里都不会再响。
"""
import datetime as _dt
import json
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env before act imports

from act import actd
from act.lib import config, radar_health, registry
from act.lib.registry import Requirement, State

_AUTH_LOG = ("# dispatch R-1 @ 2026-09-12T09:00:00\n=== STDERR ===\n"
             "authentication_error: invalid api key\n")


def _iso(delta_s: float = 0.0) -> str:
    t = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(seconds=delta_s)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


class MutedFailuresKeepTheLedgerTestCase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()
        for p in (radar_health.HEALTH_PATH, config.CONFIG_PATH):
            p.unlink(missing_ok=True)
        self.addCleanup(radar_health.HEALTH_PATH.unlink, True)
        self.addCleanup(config.CONFIG_PATH.unlink, True)
        # 进程内时钟/台账：跑热的 daemon 形态（无睡醒宽限），跨判例不串味
        actd._wake_state.update({"last_pass": _dt.datetime.now(_dt.timezone.utc).timestamp(),
                                 "last_mono": None, "grace_until": 0.0})
        self.addCleanup(actd._wake_state.update,
                        {"last_pass": None, "last_mono": None, "grace_until": 0.0})
        actd._no_baseline_since.clear()
        self.addCleanup(actd._no_baseline_since.clear)

    # -- 凭证失效（写方被静音时的一整条链：_alerts_phase 现读偏好） --------- #
    def _auth_card(self):
        log = config.STATE_DIR / "muted-auth.log"
        log.write_text(_AUTH_LOG, encoding="utf-8")
        registry.save(Requirement(id="R-1", title="登录坏了", status=State.EXECUTING.value,
                                  execution={"session_id": "sid-1", "log": str(log)}))

    def _alerts_pass(self, notified: set, muted: bool):
        with mock.patch.object(actd.notify, "suppressed_now", return_value=muted), \
                mock.patch.object(actd, "_check_radar_liveness", return_value=[]), \
                mock.patch.object(actd.notify, "notify") as notify:
            actd._alerts_phase(None, {}, notified, None, interval=30)
        return notify

    def test_a_muted_auth_alert_re_fires_when_the_switch_comes_back_on(self):
        self._auth_card()
        notified: set = set()
        muted = self._alerts_pass(notified, True)
        muted.assert_called_once()            # 消息照样交给写方（由 notify 自己吃掉）
        self.assertEqual(notified, set(), "静音的告警不许把 anti-nag 台账花掉")
        back_on = self._alerts_pass(notified, False)
        back_on.assert_called_once()          # 开关翻回来：这一 pass 就重报
        self.assertEqual(notified, {"R-1"})
        self._alerts_pass(notified, False).assert_not_called()   # 之后照旧只报一次

    # -- §48 源死亡（扫描照跑：清理 / 出账 / 记账都不许因静音停摆） --------- #
    @staticmethod
    def _seed(source: str, last_ok: str):
        data = {}
        if radar_health.HEALTH_PATH.exists():
            data = json.loads(radar_health.HEALTH_PATH.read_text(encoding="utf-8"))
        data[source] = {"last_attempt": last_ok, "last_ok": last_ok, "skip_reason": None}
        radar_health.HEALTH_PATH.write_text(json.dumps(data), encoding="utf-8")

    def test_a_muted_radar_death_re_fires_when_the_switch_comes_back_on(self):
        self._seed("gmail", _iso(7 * 3600))   # 7h > 6h 阈值
        notified: set = set()
        self.assertEqual(len(actd._check_radar_liveness(notified, suppressed=True)), 1)
        self.assertEqual(notified, set(), "静音的告警不许把 anti-nag 台账花掉")
        self.assertEqual(len(actd._check_radar_liveness(notified, suppressed=False)), 1)
        self.assertEqual(notified, {"gmail"})
        self.assertEqual(actd._check_radar_liveness(notified, suppressed=False), [])

    def test_the_scan_still_prunes_zombie_health_rows_while_muted(self):
        """§48 的副作用住在扫描里——静音不是跳过扫描，关掉的源照样被清。"""
        self._seed("gmail", _iso(7 * 3600))
        config.CONFIG_PATH.write_text("sources:\n  gmail:\n    enabled: false\n",
                                      encoding="utf-8")
        notified = {"gmail"}
        self.assertEqual(actd._check_radar_liveness(notified, suppressed=True), [])
        self.assertNotIn("gmail", notified, "恢复/关闭出账照做")
        data = json.loads(radar_health.HEALTH_PATH.read_text(encoding="utf-8"))
        self.assertNotIn("gmail", data, "僵尸条目照清")


if __name__ == "__main__":
    unittest.main()
