"""auto-dispatch 天花板行为测试——每一条 ceiling 在 actd 里的真实回落
（vnext §51 / M1.b / C-6；locked：over-ceiling => needs-approval + 陈述原因）。

test_policy.py 钉 may_auto_dispatch 纯函数词表；test_actd_wire.py 钉了
cost:over_ceiling / budget:exhausted 两条。这里补齐**全部** ceiling 在
auto_dispatch_pass 的回落形态：

  outbound（comms 卡永不自动开跑）｜repo:new（绝不建新 repo）｜repo:none /
  repo:missing（existing target_repo only）｜cost:unknown（无估价保守拒）｜
  $5 单卡上限的精确边界（5.0 过、5.5 拦）｜budget:exhausted（台账累计）｜
  t2_confirm（§7/§41 文字确认语义压过便宜天花板）——每条都：留在待审批 +
  execution.auto_dispatch_block=<token> + notes 一次性留痕 + 不发观察通知。

另钉：token 换因重盖｜解除后 token 清除并放行｜并发上限 = 排队不是拒绝
（槽位空出即派发）｜dispatch 预算复核排除本卡自己的预留。

沙箱 AIASSISTANT_HOME；executor/notify 全 mock，绝不 spawn 真 claude。
"""
import datetime as _dt
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import actd
from act.lib import config, registry
from act.lib.registry import Requirement, State

_HAND_SRC = [{"who": "zelin", "channel": "quick_capture",
              "date": "2026-08-30", "quote": "手打的活"}]


def _clean():
    config.ensure_state_dirs()
    for p in config.REGISTRY_DIR.glob("*.yaml"):
        p.unlink()
    ledger = config.STATE_DIR / actd._SPEND_LEDGER_FILE
    if ledger.exists():
        ledger.unlink()


def _mk(req_id="R-800", **kw):
    base = dict(id=req_id, title=f"ceiling 测试 {req_id}", type="other",
                tier="T1", status=State.CARD_SENT.value,
                sources=list(_HAND_SRC), target_repo=TMP_HOME,
                cost_estimate_usd=1.0)
    base.update(kw)
    req = Requirement(**base)
    registry.save(req)
    return req


class CeilingBase(unittest.TestCase):
    def setUp(self):
        _clean()
        self.notify = mock.patch.object(actd.notify, "notify").start()
        self.addCleanup(mock.patch.stopall)

    def assert_blocked(self, req_id, token):
        """over-ceiling 统一形态：留待审批 + token 上卡 + notes 留痕 + 不通知。"""
        req = registry.load(req_id)
        self.assertEqual(req.status, State.CARD_SENT.value)
        self.assertEqual((req.execution or {}).get("auto_dispatch_block"), token)
        self.assertIn("auto-dispatch 拦下", req.notes)
        self.assertIn(token, req.notes)
        self.notify.assert_not_called()
        return req


# --------------------------------------------------------------------------- #
# 每条 ceiling 的回落
# --------------------------------------------------------------------------- #
class TestEachCeiling(CeilingBase):
    def test_outbound_comms_never_auto_runs(self):
        _mk("R-800", type="comms")
        self.assertEqual(actd.auto_dispatch_pass(config.Config()), 0)
        self.assert_blocked("R-800", "outbound")

    def test_new_repo_blocked(self):
        _mk("R-801", target_kind="new")
        actd.auto_dispatch_pass(config.Config())
        self.assert_blocked("R-801", "repo:new")

    def test_no_repo_blocked(self):
        _mk("R-802", target_repo=None)
        cfg = config.Config()
        cfg.default_target_repo = ""       # 配置也给不出落点
        actd.auto_dispatch_pass(cfg)
        self.assert_blocked("R-802", "repo:none")

    def test_missing_repo_blocked(self):
        _mk("R-803", target_repo=TMP_HOME + "/definitely-not-there")
        actd.auto_dispatch_pass(config.Config())
        self.assert_blocked("R-803", "repo:missing")

    def test_unknown_cost_blocked(self):
        _mk("R-804", cost_estimate_usd=None)
        actd.auto_dispatch_pass(config.Config())
        self.assert_blocked("R-804", "cost:unknown")

    def test_five_dollar_boundary_exact(self):
        # locked：cost estimate <= $5 —— 恰好 5.0 放行。
        _mk("R-805", cost_estimate_usd=5.0)
        self.assertEqual(actd.auto_dispatch_pass(config.Config()), 1)
        self.assertEqual(registry.load("R-805").status, State.APPROVED.value)

    def test_five_dollar_boundary_over(self):
        _mk("R-806", cost_estimate_usd=5.5)
        actd.auto_dispatch_pass(config.Config())
        self.assert_blocked("R-806", "cost:over_ceiling")

    def test_daily_budget_exhausted_from_ledger(self):
        # 台账已累计 $4：单卡 $2 本身 <= $5，但 4+2 > 5 → budget:exhausted。
        actd._save_spend_ledger({"date": _dt.date.today().isoformat(),
                                 "cards": {"R-earlier": 4.0}})
        _mk("R-807", cost_estimate_usd=2.0)
        actd.auto_dispatch_pass(config.Config())
        self.assert_blocked("R-807", "budget:exhausted")

    def test_t2_typed_confirm_semantics_block(self):
        # §7/§41 三个触发面：T2 声明档、green_sign、高过文字确认线的估价。
        _mk("R-808", tier="T2")
        _mk("R-809", green_sign_required=True)
        actd.auto_dispatch_pass(config.Config())
        self.assert_blocked("R-808", "t2_confirm")
        self.assert_blocked("R-809", "t2_confirm")

    def test_typed_confirm_outranks_cost_ceiling(self):
        # cost 60 同时超 $5 与 $50 文字确认线——报 t2_confirm 不报
        # cost:over_ceiling：审批语义（要人敲确认词）压过便宜天花板。
        _mk("R-810", cost_estimate_usd=60.0)
        actd.auto_dispatch_pass(config.Config())
        self.assert_blocked("R-810", "t2_confirm")

    def test_confirm_threshold_from_config(self):
        cfg = config.Config()
        cfg.require_text_confirm_above_usd = 3.0
        _mk("R-811", cost_estimate_usd=4.0)    # <= $5 但 > 确认线
        actd.auto_dispatch_pass(cfg)
        self.assert_blocked("R-811", "t2_confirm")


# --------------------------------------------------------------------------- #
# block token 生命周期
# --------------------------------------------------------------------------- #
class TestBlockTokenLifecycle(CeilingBase):
    def test_reason_change_restamps_with_new_trace(self):
        _mk("R-820", target_repo=TMP_HOME + "/nope")
        cfg = config.Config()
        actd.auto_dispatch_pass(cfg)
        self.assert_blocked("R-820", "repo:missing")
        # owner 修好 repo，但估价超限——token 必须换因重盖 + 第二条留痕
        req = registry.load("R-820")
        req.target_repo = TMP_HOME
        req.cost_estimate_usd = 9.0
        registry.save(req)
        actd.auto_dispatch_pass(cfg)
        req = self.assert_blocked("R-820", "cost:over_ceiling")
        self.assertEqual(req.notes.count("auto-dispatch 拦下"), 2)

    def test_unblock_clears_token_and_approves(self):
        _mk("R-821", cost_estimate_usd=9.0)
        cfg = config.Config()
        actd.auto_dispatch_pass(cfg)
        self.assert_blocked("R-821", "cost:over_ceiling")
        req = registry.load("R-821")
        req.cost_estimate_usd = 2.0            # owner 调低估价
        registry.save(req)
        self.assertEqual(actd.auto_dispatch_pass(cfg), 1)
        req = registry.load("R-821")
        self.assertEqual(req.status, State.APPROVED.value)
        self.assertNotIn("auto_dispatch_block", req.execution)  # 过期 token 清除


# --------------------------------------------------------------------------- #
# 并发上限 = 排队不是拒绝（合并运行列 queued 子状态）
# --------------------------------------------------------------------------- #
class TestConcurrencyQueue(CeilingBase):
    def _cfg(self):
        return config.Config(raw={"autodispatch": {"max_concurrent": 1}})

    def test_queued_card_dispatches_when_slot_frees(self):
        _mk("R-830", status=State.EXECUTING.value,
            execution={"session_id": "sid-1"})
        _mk("R-831", status=State.APPROVED.value)
        ex_mock = mock.MagicMock()
        with mock.patch.object(actd, "executor", ex_mock):
            actd.dispatch_approved(self._cfg())      # 槽满：排队，非拒绝
            ex_mock.dispatch.assert_not_called()
            self.assertEqual(registry.load("R-831").status,
                             State.APPROVED.value)   # 卡还在 approved（queued）
            occupant = registry.load("R-830")        # 槽位释放
            occupant.set_status(State.REVIEW)
            registry.save(occupant)
            actd.dispatch_approved(self._cfg())      # 下一 pass 即派发
        ex_mock.dispatch.assert_called_once()
        self.assertEqual(ex_mock.dispatch.call_args.args[0].id, "R-831")


# --------------------------------------------------------------------------- #
# dispatch 预算复核排除本卡自己的预留（M1.c）
# --------------------------------------------------------------------------- #
class TestDispatchBudgetRecheck(CeilingBase):
    def test_own_reservation_excluded_so_auto_card_dispatches(self):
        # 台账里只有本卡自己的 $4 预留：复核口径 = 其它花费(0) + 本卡(4) <= 5
        # → 必须放行（不排除自身会把每张 auto 卡都饿死在队里）。
        actd._save_spend_ledger({"date": _dt.date.today().isoformat(),
                                 "cards": {"R-840": 4.0}})
        _mk("R-840", status=State.APPROVED.value, cost_estimate_usd=4.0,
            execution={"auto_dispatched": True})
        ex_mock = mock.MagicMock()
        with mock.patch.object(actd, "executor", ex_mock):
            actd.dispatch_approved(config.Config())
        ex_mock.dispatch.assert_called_once()


if __name__ == "__main__":
    unittest.main()
