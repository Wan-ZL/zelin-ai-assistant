"""§71.1 睡眠感知派发闸：机器在睡时 approved 卡一张都不派，醒来第一个 tick 就派。

issue #311 的形态：04:10 的维护唤醒里给一台合着盖的 MacBook 派了三张卡，会话在
睡眠里挂 2–5 小时、各花 $2–3、零产出。本判例钉住这道闸的全部语义：
  - asleep → 一张卡都不派、**不写卡**（没有 note、没有 execution 变更）、
    判决变化时只记一行日志；
  - 醒来 → 同一批卡照常派发（闸不是永久拒绝，是排队）；
  - unknown（探不到）→ fail-open 照常派发（§71.1 的保守方向）；
  - `autodispatch.require_awake=false` → 闸整个关掉；
  - 闸按住的卡在看板上是 queued + `queued_reason {kind:"asleep"}`（§2/§51）；
  - 本 pass 只探一次（半 pass 睡半 pass 醒解释不了「为什么那张派了」）。
"""
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env before any act import

from act import actd
from act.lib import config, dashboard, power, registry
from act.lib.registry import Requirement, State


def _cfg(**auto) -> config.Config:
    return config.Config(raw={"autodispatch": dict(auto)})


def _mk(req_id: str, status: str = State.APPROVED.value, **kw) -> Requirement:
    req = Requirement(id=req_id, title=f"{req_id} 睡眠闸判例", status=status, **kw)
    registry.save(req)
    return req


def _probe(verdict_reading: dict):
    return lambda: dict(verdict_reading)


ASLEEP_READING = {"state": 1, "max_state": 4}
AWAKE_READING = {"state": 4, "max_state": 4}
UNREADABLE = {}


class SleepGateBase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        for path in config.REGISTRY_DIR.glob("*.yaml"):
            path.unlink()
        power.reset_probe_memo()
        self.addCleanup(power.reset_probe_memo)

    def _dispatch(self, reading: dict, cfg=None) -> tuple:
        """闸下跑一次 dispatch_approved → (派发数, 假 executor)。"""
        ex_mock = mock.MagicMock()
        with mock.patch.object(actd, "executor", ex_mock), \
                mock.patch.object(power, "read_power", _probe(reading)):
            n = actd.dispatch_approved(cfg or _cfg())
        return n, ex_mock


class GateHoldsWhileAsleepTestCase(SleepGateBase):
    def test_asleep_dispatches_nothing_and_leaves_the_cards_untouched(self):
        _mk("R-8100")
        _mk("R-8101")
        before = [registry.load(i).execution for i in ("R-8100", "R-8101")]
        n, ex_mock = self._dispatch(ASLEEP_READING)
        self.assertEqual(n, 0)
        ex_mock.dispatch.assert_not_called()
        for rid, was in zip(("R-8100", "R-8101"), before):
            card = registry.load(rid)
            self.assertEqual(card.status, State.APPROVED.value)
            self.assertEqual(card.execution, was)      # 闸不写卡（#311：零噪音）
            self.assertNotIn("睡", card.notes or "")

    def test_awake_dispatches_the_same_cards(self):
        _mk("R-8102")
        n, ex_mock = self._dispatch(AWAKE_READING)
        self.assertEqual(n, 1)
        ex_mock.dispatch.assert_called_once()

    def test_unreadable_probe_fails_open(self):
        # skeptic 的红线：一个永远解析不出的探针不许把整条闸变成静默 no-op，
        # 也不许把自动派发饿死——unknown 照常派发，日志里说得清清楚楚。
        _mk("R-8103")
        n, ex_mock = self._dispatch(UNREADABLE)
        self.assertEqual(n, 1)
        ex_mock.dispatch.assert_called_once()

    def test_knob_off_never_probes(self):
        _mk("R-8104")
        ex_mock = mock.MagicMock()
        with mock.patch.object(actd, "executor", ex_mock), \
                mock.patch.object(power, "read_power", _boom):
            n = actd.dispatch_approved(_cfg(require_awake=False))
        self.assertEqual(n, 1)

    def test_one_probe_per_pass_for_every_card(self):
        for i in range(3):
            _mk(f"R-811{i}")
        calls = []

        def probe():
            calls.append(1)
            return dict(ASLEEP_READING)

        ex_mock = mock.MagicMock()
        with mock.patch.object(actd, "executor", ex_mock), \
                mock.patch.object(power, "read_power", probe):
            actd.dispatch_approved(_cfg())
        self.assertEqual(len(calls), 1)

    def test_idle_pass_never_probes_at_all(self):
        # 没有待派发卡的 pass（绝大多数 pass）一个探针子进程都不该起
        _mk("R-8120", status=State.DETECTED.value)
        ex_mock = mock.MagicMock()
        with mock.patch.object(actd, "executor", ex_mock), \
                mock.patch.object(power, "read_power", _boom):
            self.assertEqual(actd.dispatch_approved(_cfg()), 0)

    def test_verdict_change_logs_exactly_once(self):
        _mk("R-8130")
        lines = []
        ex_mock = mock.MagicMock()
        with mock.patch.object(actd, "executor", ex_mock), \
                mock.patch.object(actd, "_log", lines.append), \
                mock.patch.object(power, "read_power", _probe(ASLEEP_READING)):
            actd.dispatch_approved(_cfg())
            actd.dispatch_approved(_cfg())      # 同一判决，memo 内：不再刷屏
        self.assertEqual([ln for ln in lines if ln.startswith("power:")],
                         ["power: machine asleep"])


class QueuedReasonProjectionTestCase(SleepGateBase):
    def test_held_card_shows_the_asleep_chip(self):
        _mk("R-8140")
        with mock.patch.object(power, "read_power", _probe(ASLEEP_READING)):
            power.current_verdict()
            dash = dashboard.build_dashboard(cfg=_cfg(), agents=[])
        row = [r for r in dash["running"] if r["id"] == "R-8140"][0]
        self.assertEqual(row["state"], "queued")
        self.assertEqual(row["queued_reason"], {"kind": "asleep"})

    def test_awake_queued_card_keeps_the_old_vocabulary(self):
        _mk("R-8141")
        with mock.patch.object(power, "read_power", _probe(AWAKE_READING)):
            power.current_verdict()
            dash = dashboard.build_dashboard(cfg=_cfg(), agents=[])
        row = [r for r in dash["running"] if r["id"] == "R-8141"][0]
        self.assertNotIn("queued_reason", row)   # 无阻塞 = 整键不出（§51）

    def test_projection_never_probes_on_its_own(self):
        # 投影只说观察到的（宪法第 3 条）：没观察过 = 没 chip，不为画 chip 起子进程
        _mk("R-8142")
        with mock.patch.object(power, "read_power", _boom):
            dash = dashboard.build_dashboard(cfg=_cfg(), agents=[])
        row = [r for r in dash["running"] if r["id"] == "R-8142"][0]
        self.assertNotIn("queued_reason", row)


def _boom() -> dict:
    raise AssertionError("这条路径不该探测电源")


if __name__ == "__main__":
    unittest.main()
