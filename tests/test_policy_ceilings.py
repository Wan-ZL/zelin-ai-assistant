"""auto-dispatch 天花板行为测试——每一条 ceiling 在 actd 里的真实回落
（CONTRACT §51 / M1.b / C-6；locked：over-ceiling => needs-approval + 陈述原因）。

test_policy.py 钉 may_auto_dispatch 纯函数词表；test_actd_wire.py 钉了
t2_confirm 的一次性留痕。这里补齐**全部** ceiling 在 auto_dispatch_pass 的
回落形态：

  outbound（comms 卡永不自动开跑）｜repo:new（绝不建新 repo）｜repo:none /
  repo:missing（existing target_repo only）｜cost:unknown（无估价保守拒）｜
  t2_confirm（§7/§41 文字确认语义：T2 / green_sign / 超文字确认线）——每条都：
  留在待审批 + execution.auto_dispatch_block=<token> + notes 一次性留痕 +
  不发观察通知。

另钉：token 换因重盖｜解除后 token 清除并放行｜并发上限 = 排队不是拒绝
（槽位空出即派发）。

预算天花板 retired v0.48.7（owner decision D9，docs/design/vnext2-plan.md）：
原「$5 单卡精确边界（5.0 过、5.5 拦）」「budget:exhausted 台账累计」「dispatch
预算复核排除本卡预留」三组判例改钉其反面——任意估价（<= 文字确认线）、任意
当日累计都放行，残留的旧台账文件是死数据。

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

    def test_no_single_card_ceiling_d9(self):
        # 原判例 test_five_dollar_boundary_exact/_over 钉「5.0 过、5.5 拦
        # （cost:over_ceiling）」。D9 retired v0.48.7：5.0 / 5.5 / 9 / 49.99 全部
        # 放行——只剩 §7/§41 的文字确认线（默认 $50）还看金额。
        for i, cost in enumerate((5.0, 5.5, 9.0, 49.99)):
            _mk(f"R-80{5 + i}", cost_estimate_usd=cost)
        self.assertEqual(actd.auto_dispatch_pass(config.Config()), 4)
        for i in range(4):
            self.assertEqual(registry.load(f"R-80{5 + i}").status,
                             State.APPROVED.value)
        self.assertEqual(self.notify.call_count, 4)

    def test_no_daily_budget_regardless_of_accumulated_spend_d9(self):
        # 原判例 test_daily_budget_exhausted_from_ledger 钉「台账 $4 + 本卡 $2 >
        # $5 → budget:exhausted」。D9 retired v0.48.7：hand 出身 + 任意估价的卡
        # 一律自动派发，不管当天已经派了多少钱——五张 $40 的卡（合计 $200，
        # 远超旧 $5 预算）同一 pass 全部批准；升级前残留的台账文件（记着 $999）
        # 无人读，也不会被写。
        _plant_legacy_ledger({"R-earlier": 999.0})
        for i in range(5):
            _mk(f"R-81{i}", cost_estimate_usd=40.0)
        self.assertEqual(actd.auto_dispatch_pass(config.Config()), 5)
        for i in range(5):
            req = registry.load(f"R-81{i}")
            self.assertEqual(req.status, State.APPROVED.value)
            self.assertNotIn("auto_dispatch_block", req.execution)
        stale = json.loads((config.STATE_DIR / _LEGACY_LEDGER)
                           .read_text(encoding="utf-8"))
        self.assertEqual(stale["cards"], {"R-earlier": 999.0})  # 未被改写

    def test_t2_typed_confirm_semantics_block(self):
        # §7/§41 三个触发面：T2 声明档、green_sign、高过文字确认线的估价。
        _mk("R-808", tier="T2")
        _mk("R-809", green_sign_required=True)
        actd.auto_dispatch_pass(config.Config())
        self.assert_blocked("R-808", "t2_confirm")
        self.assert_blocked("R-809", "t2_confirm")

    def test_typed_confirm_line_is_the_only_money_gate(self):
        # cost 60 > $50 文字确认线 → t2_confirm：审批语义（要人敲确认词）是
        # D9 之后唯一还看金额的闸（原判例 test_typed_confirm_outranks_cost_ceiling
        # 钉的是它压过 cost:over_ceiling——后者已退役）。
        _mk("R-815", cost_estimate_usd=60.0)
        actd.auto_dispatch_pass(config.Config())
        self.assert_blocked("R-815", "t2_confirm")

    def test_confirm_threshold_from_config(self):
        cfg = config.Config()
        cfg.require_text_confirm_above_usd = 3.0
        _mk("R-816", cost_estimate_usd=4.0)    # > 确认线
        actd.auto_dispatch_pass(cfg)
        self.assert_blocked("R-816", "t2_confirm")


# --------------------------------------------------------------------------- #
# block token 生命周期
# --------------------------------------------------------------------------- #
class TestBlockTokenLifecycle(CeilingBase):
    def test_reason_change_restamps_with_new_trace(self):
        _mk("R-820", target_repo=TMP_HOME + "/nope")
        cfg = config.Config()
        actd.auto_dispatch_pass(cfg)
        self.assert_blocked("R-820", "repo:missing")
        # owner 修好 repo，但估价高过文字确认线——token 必须换因重盖 + 第二条
        # 留痕（D9 前这里用 $9 触发 cost:over_ceiling；该 token 已退役）
        req = registry.load("R-820")
        req.target_repo = TMP_HOME
        req.cost_estimate_usd = 60.0
        registry.save(req)
        actd.auto_dispatch_pass(cfg)
        req = self.assert_blocked("R-820", "t2_confirm")
        self.assertEqual(req.notes.count("auto-dispatch 拦下"), 2)

    def test_unblock_clears_token_and_approves(self):
        _mk("R-821", cost_estimate_usd=60.0)
        cfg = config.Config()
        actd.auto_dispatch_pass(cfg)
        self.assert_blocked("R-821", "t2_confirm")
        req = registry.load("R-821")
        req.cost_estimate_usd = 9.0            # owner 调低估价（D9 前 $9 仍会被 $5 上限拦）
        registry.save(req)
        self.assertEqual(actd.auto_dispatch_pass(cfg), 1)
        req = registry.load("R-821")
        self.assertEqual(req.status, State.APPROVED.value)
        self.assertNotIn("auto_dispatch_block", req.execution)  # 过期 token 清除

    def test_legacy_budget_tokens_clear_on_upgrade(self):
        # 升级路径：v0.48 留在卡上的 cost:over_ceiling / budget:exhausted token
        # 在 D9 之后第一个 pass 就按「解除即清」清掉并放行——卡不会因为一个已
        # 退役的原因永远躺在待审批。
        _mk("R-822", cost_estimate_usd=9.0,
            execution={"auto_dispatch_block": "cost:over_ceiling"})
        _mk("R-823", cost_estimate_usd=2.0,
            execution={"auto_dispatch_block": "budget:exhausted"})
        self.assertEqual(actd.auto_dispatch_pass(config.Config()), 2)
        for rid in ("R-822", "R-823"):
            req = registry.load(rid)
            self.assertEqual(req.status, State.APPROVED.value)
            self.assertNotIn("auto_dispatch_block", req.execution)


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
# dispatch 时刻没有预算复核（D9；原 M1.c「排除本卡预留」判例的反面）
# --------------------------------------------------------------------------- #
class TestDispatchNoBudgetRecheck(CeilingBase):
    def test_auto_card_dispatches_whatever_the_ledger_says(self):
        # 原判例钉「台账只有本卡 $4 预留 → 复核 0+4 <= 5 放行」。D9 retired
        # v0.48.7：派发时刻根本不看钱——即便残留台账说今天已经花了 $999、本卡
        # 估价 $40，auto 卡照常派发，并发是唯一的排队原因。
        _plant_legacy_ledger({"R-other": 999.0, "R-840": 40.0})
        _mk("R-840", status=State.APPROVED.value, cost_estimate_usd=40.0,
            execution={"auto_dispatched": True})
        ex_mock = mock.MagicMock()
        with mock.patch.object(actd, "executor", ex_mock):
            actd.dispatch_approved(config.Config())
        ex_mock.dispatch.assert_called_once()
        self.assertEqual(ex_mock.dispatch.call_args.args[0].id, "R-840")


if __name__ == "__main__":
    unittest.main()
