"""fold 进一张**已扩写**的卡绝不重跑扩写（CONTRACT §8 / §78 / §0 第 2 条 一切可逆）。

§78 之前「这张卡要不要扩写」是由车道回答的：完整的机器卡住在 ``card_sent``，
``detected`` 里只剩裸欠账，所以两处 fold 点写 ``status == detected`` 就等于写
「这是一条裸欠账」。提案车道退役后每张机器卡都是 ``detected``——同一行代码于是
会把 daily_loop 铸的 🤖 卡（plan/DoD/成本/target_repo 齐全）也送进
``analyze.expand_debt``，而那是**覆盖写**：owner 一句「顺带也看一下 Y」就能让
手写的计划、验收标准、成本估算被一次新的 LLM 输出顶掉，回执却只说「已关联」。
没有报错、测试全绿——正是 §0 第 2 条要堵的那类不可逆。

钉四条：

1. fold 进带 plan 的卡：不叫扩写器，plan/DoD/成本/target_repo/summary 一字不动，
   回执走平路文案（不许再说「已扩成完整建议」）；
2. fold 进裸欠账：照旧扩写——§8 的欠账→提议这条路不许被这次修复堵掉；
3. owner 显式「研究并提议」（``raise`` 动作 + ``process_raising``）对完整卡照旧
   生效：那是用户动作，``raising`` 就是那张明确的票据；
4. ``analyze.expand_debt`` 自己的二道防线：没票据（不在 ``raising``）又已带
   plan/DoD 的调用原样返回，runner 一次都不碰。

两处 fold 点是双生子，本文件两边各钉一次：``act/lib/quick_capture.py``
``_fold_note_into``（self-DM 的 relates_to）与 ``act/lib/actd/inbox.py``
``_capture_proposal``（capture 静默并入）。注入缝 = ``expand_debt`` 的 ``runner``
形参 + mock，绝不 spawn 真 ``claude``。
"""
import json
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import actd, analyze
from act.lib import analytics, config, quick_capture, registry
from act.lib.registry import Requirement, State


class _Proc:
    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.returncode = returncode


# 一张 daily_loop 形状的完整 🤖 卡（truth = act/lib/daily_loop.py build_card）
_PLANNED = dict(
    type="self-improvement", tier="T1", status=State.DETECTED.value, hardness="soft",
    summary="把 R-229 的双生 fold 点钉住", plan=["读两处 fold 点", "加判据", "补判例"],
    definition_of_done=["新判例过", "两处口径一致"], cost_estimate_usd=3.0,
    target_repo="~/Projects/zelin-ai-assistant", delivery_mode="repo",
)

# LLM 真跑起来会写下的东西——任何一项出现在卡上 = 覆盖写真的发生了
_FRESH = {"summary": "LLM 重写的 summary", "plan": ["LLM 的第一步"],
          "definition_of_done": ["LLM 的验收"], "cost_estimate_usd": 99.0,
          "target_repo": "~/Projects/somewhere-else", "delivery_mode": "chat"}


def _planned(req_id: str) -> Requirement:
    req = Requirement(id=req_id, title="🤖 把 R-229 的双生 fold 点钉住", **_PLANNED)
    registry.save(req)
    return req


def _stub(req_id: str) -> Requirement:
    """裸欠账：只有标题，没 plan 没 DoD（§8 扩写管线的唯一合法对象）。"""
    req = Requirement(id=req_id, title="顺带看一下滑雪板的 sidecut",
                      status=State.DETECTED.value)
    registry.save(req)
    return req


def _expansion(_prompt):
    return _Proc(stdout=json.dumps(_FRESH, ensure_ascii=False))


class FoldBase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()
        patcher = mock.patch.object(analytics, "log_event")   # 事件行不是本文件的判据
        patcher.start()
        self.addCleanup(patcher.stop)

    def assertUntouched(self, req_id: str):
        """卡上的 plan/DoD/成本/target_repo/summary 与铸卡那一刻逐字相同。"""
        out = registry.load(req_id)
        self.assertEqual(out.plan, _PLANNED["plan"])
        self.assertEqual(out.definition_of_done, _PLANNED["definition_of_done"])
        self.assertEqual(out.cost_estimate_usd, _PLANNED["cost_estimate_usd"])
        self.assertEqual(out.target_repo, _PLANNED["target_repo"])
        self.assertEqual(out.summary, _PLANNED["summary"])
        self.assertEqual(out.status, State.DETECTED.value)
        return out


class QuickCaptureFoldTestCase(FoldBase):
    """self-DM 的 relates_to → quick_capture._fold_note_into（§44.6 / §8）。"""

    def test_fold_into_a_planned_card_does_not_re_expand_it(self):
        req = _planned("P-9601")
        with mock.patch.object(quick_capture.analyze, "expand_debt") as ed:
            kind, saved, reply = quick_capture._fold_note_into(
                req, "顺带也看一下 Y", config.Config(), None)
        ed.assert_not_called()
        self.assertEqual(kind, "folded")
        # 回执走平路文案：卡在潜在任务里、备注追加了，没有第二句「已扩成完整建议」
        self.assertEqual(reply, "已关联 P-9601：已在潜在任务，备注已追加")
        out = self.assertUntouched("P-9601")
        self.assertIn("[quick] 顺带也看一下 Y", out.notes)

    def test_fold_into_a_bare_stub_still_expands(self):
        req = _stub("P-9602")
        cfg = config.Config()
        with mock.patch.object(quick_capture.analyze, "expand_debt") as ed:
            _kind, _saved, reply = quick_capture._fold_note_into(req, "n", cfg, None)
        ed.assert_called_once_with(req, cfg)
        self.assertEqual(
            reply,
            "已关联 P-9602：已扩成完整建议，留在潜在任务等你一次点名 / "
            "expanded in place, still in the backlog")


class CaptureFoldTestCase(FoldBase):
    """capture 静默并入 → inbox._capture_proposal 的双生判据（§10 / §44.6）。"""

    def test_capture_folded_into_a_planned_card_queues_no_expansion(self):
        req = _planned("P-9611")
        with mock.patch.object(registry, "merge_or_new_with_kind",
                               return_value=("folded", req)):
            ack = actd._apply_capture("顺带也看一下 Y")
        self.assertEqual(ack, "running")
        self.assertUntouched("P-9611")
        # 端到端的证据：扩写队列里一张卡都没有，下一个 pass 不会跑 claude -p
        self.assertEqual(actd.process_raising(config.Config()), 0)

    def test_capture_folded_into_a_bare_stub_still_queues_the_expansion(self):
        req = _stub("P-9612")
        with mock.patch.object(registry, "merge_or_new_with_kind",
                               return_value=("folded", req)):
            actd._apply_capture("顺带也看一下 sidecut")
        self.assertEqual(registry.load("P-9612").status, State.RAISING.value)

    def test_a_brand_new_capture_card_still_queues_the_expansion(self):
        actd._apply_capture("查一下 carving 板的 sidecut 数据")
        cards = registry.load_all()
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0].status, State.RAISING.value)


class OwnerRaiseTestCase(FoldBase):
    """「研究并提议」是用户动作：完整卡也照旧重扩写（§8 / decisions._raise）。"""

    def test_explicit_raise_re_expands_a_complete_card(self):
        req = _planned("P-9621")
        self.assertEqual(actd._apply_decision(req, "raise", None, None, None),
                         "running")
        self.assertEqual(registry.load("P-9621").status, State.RAISING.value)

        real_expand = analyze.expand_debt

        def fake_expand(r, cfg=None, runner=None):
            return real_expand(r, config.Config(), runner=_expansion)

        with mock.patch.object(analyze, "expand_debt", side_effect=fake_expand):
            self.assertEqual(actd.process_raising(config.Config()), 1)
        out = registry.load("P-9621")
        # owner 要的就是一份新的提议——覆盖写在这条路上是**意图**，不是事故
        self.assertEqual(out.plan, _FRESH["plan"])
        self.assertEqual(out.definition_of_done, _FRESH["definition_of_done"])
        self.assertEqual(out.summary, _FRESH["summary"])
        self.assertEqual(out.status, State.DETECTED.value)   # §78：原地写厚


class ExpandDebtGuardTestCase(FoldBase):
    """二道防线：没 raising 票据的完整卡，expand_debt 自己不动手（§0 第 2 条）。"""

    def test_expanding_a_planned_detected_card_is_a_no_op(self):
        req = _planned("P-9631")
        calls = []

        def runner(prompt):
            calls.append(prompt)
            return _Proc(stdout=json.dumps(_FRESH, ensure_ascii=False))

        out = analyze.expand_debt(req, config.Config(), runner=runner)
        self.assertEqual(calls, [])          # 没票据 = 一次模型调用都不发
        self.assertIs(out, req)
        out = self.assertUntouched("P-9631")
        # 也不许留兜底面包屑：什么都没发生，卡上就不该有「扩写失败」的痕迹
        self.assertNotIn("auto-expand failed", out.notes or "")

    def test_a_raising_card_is_always_expanded(self):
        req = _planned("P-9632")
        req.set_status(State.RAISING)
        registry.save(req)
        out = analyze.expand_debt(req, config.Config(), runner=_expansion)
        self.assertEqual(out.plan, _FRESH["plan"])
        self.assertEqual(out.status, State.DETECTED.value)


if __name__ == "__main__":
    unittest.main()
