"""auto-dispatch 天花板行为测试——每一条 ceiling 在 actd 里的真实回落
（CONTRACT §51 / §65 / §78；locked：over-ceiling => needs-approval + 陈述原因）。

test_policy.py 钉 may_auto_dispatch 纯函数词表；test_actd_wire.py 钉了
t2_confirm 的一次性留痕。这里补齐**全部**还够得着的 ceiling 在
auto_dispatch_pass 的回落形态：

  outbound（comms 卡永不自动开跑）｜repo:new（绝不建新 repo）｜repo:missing
  （existing target_repo only）｜self_improve:repo_mismatch（落点不是本仓库 /
  压根没落点）｜t2_confirm（§7/§41 文字确认语义：T2 / green_sign / 超文字确认
  线）——每条都：留在潜在任务 + execution.auto_dispatch_block=<token> + notes
  一次性留痕 + 不发观察通知。

另钉：token 换因重盖｜解除后 token 清除并放行｜并发上限 = 排队不是拒绝
（槽位空出即派发）。

**§78（issue #447 / owner decision D80.4）重新锚点**：提案车道退役，§51 的
hand 免批车道随之 tombstone——hand 出身卡连资格闸都不进（唯一喂料口是被删掉
的提案捕获框；owner 亲笔起跑走 §34 直跑框，出生即 approved）。存活的免批车道
只剩 §65 self_improve，它照旧过**每一条**天花板，所以本文件的判例全部改钉
`detected` 里的 §65 lane 卡。policy.may_auto_dispatch 的裁决表一个字没改（闸在
act/lib/actd/dispatch.py 的 is_self_improve_sources 那一行），下面两条 ceiling
因此在 actd 这一层够不着了，改由别处继续钉住，绝不静默丢失：

* `repo:none`（卡上没有 target_repo）—— §65 lane gate 的 same_repo 比对先一步
  报 `self_improve:repo_mismatch`。行为本身（没有落点 = 永不自动开跑 + 原因上卡）
  由 test_no_landing_site_blocked 继续钉；`repo:none` 这个 token 本身由
  tests/test_policy.py 的纯函数判例钉住。
* `cost:unknown`（估价缺失）—— §65 lane 没有审批步骤也没有预算（D9），
  _cost_verdict 对 lane 卡直接放行。反面（估价缺失照跑）由
  test_unknown_cost_never_blocks_the_lane 钉；token 本身同样在 test_policy.py。

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
from act.lib import config, policy, registry, self_improve
from act.lib.registry import Requirement, State

# §65 lane 的出身：sources 每一条都是 self_improve 渠道（producer 硬编码，
# 无 LLM 参与）——这是 §78 之后唯一还能进免批资格闸的形状。
_LANE_SRC = [{"who": "loop", "channel": "self_improve", "date": "2026-09-02",
              "ref": "proposal:abc", "quote": "让 doctor 多一行"}]
# hand 出身：§51 车道 retired（D80.4）之后它只用来钉「连闸都不进」。
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
    self_improve.lane_state_path().unlink(missing_ok=True)   # §65.4 暂停态不串场


def _plant_legacy_ledger(cards: dict) -> None:
    """伪造一份升级前的当日花费台账——D9 之后没有任何代码读它。"""
    (config.STATE_DIR / _LEGACY_LEDGER).write_text(
        json.dumps({"date": _dt.date.today().isoformat(), "cards": cards}),
        encoding="utf-8")


def _cfg(**raw) -> config.Config:
    """§65 通道开着的 cfg——车道关着的判决另有判例（test_self_improve_*）。"""
    return config.Config(self_improve_enabled=True, raw=dict(raw))


def _mk(req_id="R-800", **kw):
    """§78 之后的受审卡形状：潜在任务（detected）里的 §65 lane 卡。"""
    base = dict(id=req_id, title=f"ceiling 测试 {req_id}", type="other",
                tier="T1", status=State.DETECTED.value,
                sources=list(_LANE_SRC), target_repo=str(config.HOME),
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

    def assert_blocked(self, req_id, token):
        """over-ceiling 统一形态：留潜在任务 + token 上卡 + notes 留痕 + 不通知。"""
        req = registry.load(req_id)
        self.assertEqual(req.status, State.DETECTED.value)
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
        self.assertEqual(actd.auto_dispatch_pass(_cfg()), 0)
        self.assert_blocked("R-800", "outbound")

    def test_new_repo_blocked(self):
        _mk("R-801", target_kind="new")
        actd.auto_dispatch_pass(_cfg())
        self.assert_blocked("R-801", "repo:new")

    def test_no_landing_site_blocked(self):
        # §78：卡上没有 target_repo 时 §65 lane gate 的 same_repo 比对先报
        # self_improve:repo_mismatch（hand lane 退役前这里是 repo:none，那个
        # token 的纯函数判决仍在 tests/test_policy.py）。钉的行为不变：**没有
        # 落点的卡永不自动开跑，原因上卡**——配置里的 default_target_repo
        # 也救不了它（lane gate 只认卡上的 target_repo）。
        _mk("R-802", target_repo=None)
        cfg = _cfg()
        cfg.default_target_repo = TMP_HOME     # 配置给得出落点也不算数
        actd.auto_dispatch_pass(cfg)
        self.assert_blocked("R-802", "self_improve:repo_mismatch")

    def test_missing_repo_blocked(self):
        # existing target_repo only：通道配置指向的 checkout 被搬走/删掉之后，
        # 卡与配置仍然「同一路径」（过 lane gate），但盘上不存在 → repo:missing。
        gone = TMP_HOME + "/definitely-not-there"
        _mk("R-803", target_repo=gone)
        actd.auto_dispatch_pass(_cfg(self_improve={"repo_path": gone}))
        self.assert_blocked("R-803", "repo:missing")

    def test_unknown_cost_never_blocks_the_lane(self):
        # §65 lane 没有审批步骤、也没有预算（D9）——估价缺失不再是拒绝理由
        # （_cost_verdict 的 lane 分支）。原判例钉的是 hand 卡「不可证明 <= 文字
        # 确认线 → cost:unknown」；hand lane 退役（D80.4）后那条路在 actd 里够
        # 不着了，token 本身由 tests/test_policy.py 的纯函数判例钉住，这里钉
        # 其反面：lane 卡照跑，且不留任何 block 痕。
        _mk("R-804", cost_estimate_usd=None)
        self.assertEqual(actd.auto_dispatch_pass(_cfg()), 1)
        req = registry.load("R-804")
        self.assertEqual(req.status, State.APPROVED.value)
        self.assertNotIn("auto_dispatch_block", req.execution)
        self.assertIn("self_improve 通道免批自动派发", req.notes)

    def test_hand_card_never_reaches_a_ceiling(self):
        # §78 / D80.4：hand 出身卡连资格闸都不进——同一张卡换成 lane 出身会被
        # t2_confirm 拦下并留痕，hand 出身则**什么都不发生**（不批、不上 token、
        # 不留痕、不通知），等 owner 在潜在任务里点「促成运行」。
        _mk("R-805", sources=list(_HAND_SRC), cost_estimate_usd=60.0,
            execution={"auto_dispatch_block": "t2_confirm"})   # 上一轮的残留
        self.assertEqual(actd.auto_dispatch_pass(_cfg()), 0)
        req = registry.load("R-805")
        self.assertEqual(req.status, State.DETECTED.value)
        self.assertNotIn("auto_dispatch_block", req.execution or {})  # 过期即清
        self.assertNotIn("auto-dispatch 拦下", req.notes or "")
        self.notify.assert_not_called()

    def test_no_single_card_ceiling_d9(self):
        # 原判例 test_five_dollar_boundary_exact/_over 钉「5.0 过、5.5 拦
        # （cost:over_ceiling）」。D9 retired v0.48.7：5.0 / 5.5 / 9 / 49.99 全部
        # 放行——只剩 §7/§41 的文字确认线（默认 $50）还看金额。
        for i, cost in enumerate((5.0, 5.5, 9.0, 49.99)):
            _mk(f"R-81{i}", cost_estimate_usd=cost)
        self.assertEqual(actd.auto_dispatch_pass(_cfg()), 4)
        for i in range(4):
            self.assertEqual(registry.load(f"R-81{i}").status,
                             State.APPROVED.value)
        self.assertEqual(self.notify.call_count, 4)

    def test_no_daily_budget_regardless_of_accumulated_spend_d9(self):
        # 原判例 test_daily_budget_exhausted_from_ledger 钉「台账 $4 + 本卡 $2 >
        # $5 → budget:exhausted」。D9 retired v0.48.7：过得了资格闸的卡 + 任意
        # 估价一律自动派发，不管当天已经派了多少钱——五张 $40 的卡（合计 $200，
        # 远超旧 $5 预算）同一 pass 全部批准；升级前残留的台账文件（记着 $999）
        # 无人读，也不会被写。
        _plant_legacy_ledger({"R-earlier": 999.0})
        for i in range(5):
            _mk(f"R-82{i}", cost_estimate_usd=40.0)
        self.assertEqual(actd.auto_dispatch_pass(_cfg()), 5)
        for i in range(5):
            req = registry.load(f"R-82{i}")
            self.assertEqual(req.status, State.APPROVED.value)
            self.assertNotIn("auto_dispatch_block", req.execution)
        stale = json.loads((config.STATE_DIR / _LEGACY_LEDGER)
                           .read_text(encoding="utf-8"))
        self.assertEqual(stale["cards"], {"R-earlier": 999.0})  # 未被改写

    def test_t2_typed_confirm_semantics_block(self):
        # §7/§41 三个触发面：T2 声明档、green_sign、高过文字确认线的估价。
        # §65 lane 一样吃这一条——免批不等于免确认。
        _mk("R-808", tier="T2")
        _mk("R-809", green_sign_required=True)
        actd.auto_dispatch_pass(_cfg())
        self.assert_blocked("R-808", "t2_confirm")
        self.assert_blocked("R-809", "t2_confirm")

    def test_typed_confirm_line_is_the_only_money_gate(self):
        # cost 60 > $50 文字确认线 → t2_confirm：审批语义（要人敲确认词）是
        # D9 之后唯一还看金额的闸（原判例 test_typed_confirm_outranks_cost_ceiling
        # 钉的是它压过 cost:over_ceiling——后者已退役）。
        _mk("R-815", cost_estimate_usd=60.0)
        actd.auto_dispatch_pass(_cfg())
        self.assert_blocked("R-815", "t2_confirm")

    def test_confirm_threshold_from_config(self):
        cfg = _cfg()
        cfg.require_text_confirm_above_usd = 3.0
        _mk("R-816", cost_estimate_usd=4.0)    # > 确认线
        actd.auto_dispatch_pass(cfg)
        self.assert_blocked("R-816", "t2_confirm")

    def test_retired_ceiling_tokens_still_exist_for_hand_cards(self):
        # 词表 tombstone 防线：hand lane 的**入口**退役了（D80.4），裁决表没退役
        # ——policy 仍是纯资格函数，repo:none / cost:unknown 逐字还在。哪天有人
        # 给 hand 卡接回一个入口，这两条天花板必须还在那儿等着。
        cfg = _cfg()
        cfg.default_target_repo = ""           # 配置也给不出落点
        hand = Requirement(id="R-817", title="纯函数判决用的手打卡", type="other",
                           tier="T1", status=State.DETECTED.value,
                           sources=list(_HAND_SRC), target_kind="existing")
        self.assertEqual(policy.may_auto_dispatch(hand, cfg),
                         (False, "repo:none"))
        hand.target_repo = TMP_HOME
        self.assertEqual(policy.may_auto_dispatch(hand, cfg),
                         (False, "cost:unknown"))


# --------------------------------------------------------------------------- #
# block token 生命周期
# --------------------------------------------------------------------------- #
class TestBlockTokenLifecycle(CeilingBase):
    def test_reason_change_restamps_with_new_trace(self):
        _mk("R-830", target_repo=TMP_HOME + "/nope")
        cfg = _cfg()
        actd.auto_dispatch_pass(cfg)
        self.assert_blocked("R-830", "self_improve:repo_mismatch")
        # owner 修好落点，但估价高过文字确认线——token 必须换因重盖 + 第二条
        # 留痕（D9 前这里用 $9 触发 cost:over_ceiling；该 token 已退役）
        req = registry.load("R-830")
        req.target_repo = str(config.HOME)
        req.cost_estimate_usd = 60.0
        registry.save(req)
        actd.auto_dispatch_pass(cfg)
        req = self.assert_blocked("R-830", "t2_confirm")
        self.assertEqual(req.notes.count("auto-dispatch 拦下"), 2)

    def test_unblock_clears_token_and_approves(self):
        _mk("R-831", cost_estimate_usd=60.0)
        cfg = _cfg()
        actd.auto_dispatch_pass(cfg)
        self.assert_blocked("R-831", "t2_confirm")
        req = registry.load("R-831")
        req.cost_estimate_usd = 9.0            # owner 调低估价（D9 前 $9 仍会被 $5 上限拦）
        registry.save(req)
        self.assertEqual(actd.auto_dispatch_pass(cfg), 1)
        req = registry.load("R-831")
        self.assertEqual(req.status, State.APPROVED.value)
        self.assertNotIn("auto_dispatch_block", req.execution)  # 过期 token 清除

    def test_legacy_budget_tokens_clear_on_upgrade(self):
        # 升级路径：v0.48 留在卡上的 cost:over_ceiling / budget:exhausted token
        # 在 D9 之后第一个 pass 就按「解除即清」清掉并放行——卡不会因为一个已
        # 退役的原因永远躺在潜在任务里。
        _mk("R-832", cost_estimate_usd=9.0,
            execution={"auto_dispatch_block": "cost:over_ceiling"})
        _mk("R-833", cost_estimate_usd=2.0,
            execution={"auto_dispatch_block": "budget:exhausted"})
        self.assertEqual(actd.auto_dispatch_pass(_cfg()), 2)
        for rid in ("R-832", "R-833"):
            req = registry.load(rid)
            self.assertEqual(req.status, State.APPROVED.value)
            self.assertNotIn("auto_dispatch_block", req.execution)


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
    def test_auto_card_dispatches_whatever_the_ledger_says(self):
        # 原判例钉「台账只有本卡 $4 预留 → 复核 0+4 <= 5 放行」。D9 retired
        # v0.48.7：派发时刻根本不看钱——即便残留台账说今天已经花了 $999、本卡
        # 估价 $40，auto 卡照常派发，并发是唯一的排队原因。
        _plant_legacy_ledger({"R-other": 999.0, "R-850": 40.0})
        _mk("R-850", status=State.APPROVED.value, cost_estimate_usd=40.0,
            execution={"auto_dispatched": True})
        ex_mock = mock.MagicMock()
        with mock.patch.object(actd, "executor", ex_mock):
            actd.dispatch_approved(_cfg())
        ex_mock.dispatch.assert_called_once()
        self.assertEqual(ex_mock.dispatch.call_args.args[0].id, "R-850")


if __name__ == "__main__":
    unittest.main()
