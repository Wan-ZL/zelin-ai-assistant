"""派发时刻的闸行为测试（CONTRACT §51 / §78；D9 / D86）。

D86（2026-09-30）：§51 的最后一条免批 lane（§65 self_improve）随通道整条删除，
``policy.may_auto_dispatch`` 与它的全部天花板（outbound / repo:* / t2_confirm /
self_improve:*）一起删掉——原本在这里钉「每条 ceiling 在 auto_dispatch_pass 的
回落形态」的 TestEachCeiling / TestBlockTokenLifecycle 两组判例随之删除，词表
tombstone 在 act/lib/policy.py。留下的是与免批无关、只管 owner 已批准的卡的两条：

* 并发上限 = 排队不是拒绝（槽位空出即派发）；
* 派发时刻没有预算复核（D9：残留台账是死数据）。

沙箱 AIASSISTANT_HOME；executor/notify 全 mock，绝不 spawn 真 claude。
"""
import datetime as _dt
import json
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import actd
from act.lib import config, registry
from act.lib.registry import Requirement, State

_HAND_SRC = [{"who": "zelin", "channel": "quick_capture",
              "date": "2026-08-30", "quote": "手打的活"}]

# v0.48 台账文件名（retired v0.48.7，D9）：测试只用它伪造「升级前残留」。
_LEGACY_LEDGER = "autodispatch_spend.json"


def _clean():
    config.ensure_state_dirs()
    for p in config.REGISTRY_DIR.glob("*.yaml"):
        p.unlink()
    ledger = config.STATE_DIR / _LEGACY_LEDGER
    if ledger.exists():
        ledger.unlink()


def _plant_legacy_ledger(cards: dict) -> None:
    """伪造一份升级前的当日花费台账——D9 之后没有任何代码读它。"""
    (config.STATE_DIR / _LEGACY_LEDGER).write_text(
        json.dumps({"date": _dt.date.today().isoformat(), "cards": cards}),
        encoding="utf-8")


def _cfg(**raw) -> config.Config:
    return config.Config(raw=dict(raw))


def _mk(req_id="R-800", **kw):
    base = dict(id=req_id, title=f"ceiling 测试 {req_id}", type="other",
                tier="T1", status=State.DETECTED.value,
                sources=list(_HAND_SRC), target_repo=str(config.HOME),
                target_kind="existing", cost_estimate_usd=1.0)
    base.update(kw)
    req = Requirement(**base)
    registry.save(req)
    return req


class CeilingBase(unittest.TestCase):
    def setUp(self):
        _clean()
        self.notify = mock.patch.object(actd.notify, "notify").start()
        self.addCleanup(mock.patch.stopall)


# --------------------------------------------------------------------------- #
# 并发上限 = 排队不是拒绝（合并运行列 queued 子状态）
# --------------------------------------------------------------------------- #
class TestConcurrencyQueue(CeilingBase):
    def _capped(self):
        return _cfg(autodispatch={"max_concurrent": 1})

    def test_queued_card_dispatches_when_slot_frees(self):
        _mk("R-840", status=State.EXECUTING.value,
            execution={"session_id": "sid-1"})
        _mk("R-841", status=State.APPROVED.value)
        ex_mock = mock.MagicMock()
        with mock.patch.object(actd, "executor", ex_mock):
            actd.dispatch_approved(self._capped())      # 槽满：排队，非拒绝
            ex_mock.dispatch.assert_not_called()
            self.assertEqual(registry.load("R-841").status,
                             State.APPROVED.value)   # 卡还在 approved（queued）
            occupant = registry.load("R-840")        # 槽位释放
            occupant.set_status(State.REVIEW)
            registry.save(occupant)
            actd.dispatch_approved(self._capped())      # 下一 pass 即派发
        ex_mock.dispatch.assert_called_once()
        self.assertEqual(ex_mock.dispatch.call_args.args[0].id, "R-841")


# --------------------------------------------------------------------------- #
# dispatch 时刻没有预算复核（D9；原 M1.c「排除本卡预留」判例的反面）
# --------------------------------------------------------------------------- #
class TestDispatchNoBudgetRecheck(CeilingBase):
    def test_approved_card_dispatches_whatever_the_ledger_says(self):
        # 原判例钉「台账只有本卡 $4 预留 → 复核 0+4 <= 5 放行」。D9 retired
        # v0.48.7：派发时刻根本不看钱——即便残留台账说今天已经花了 $999、本卡
        # 估价 $40，owner 批过的卡照常派发，并发是唯一的排队原因。（D86 起没有
        # auto 卡：带 auto_dispatched 痕的存量卡被退役护栏撤回，另有判例。）
        _plant_legacy_ledger({"R-other": 999.0, "R-850": 40.0})
        _mk("R-850", status=State.APPROVED.value, cost_estimate_usd=40.0)
        ex_mock = mock.MagicMock()
        with mock.patch.object(actd, "executor", ex_mock):
            actd.dispatch_approved(_cfg())
        ex_mock.dispatch.assert_called_once()
        self.assertEqual(ex_mock.dispatch.call_args.args[0].id, "R-850")


if __name__ == "__main__":
    unittest.main()
