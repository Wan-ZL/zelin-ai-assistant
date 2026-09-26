"""raising→detected expansion stage (act/analyze.expand_debt +
actd.process_raising) — first real coverage (audit 2026-07-15).

Every quick capture (§10) and every raise action funnels through this stage;
until now the whole suite mocked the analyze module wholesale, so a regression
in JSON extraction, delivery_mode folding or the crash fallback would brick
the capture pipeline with a green suite. Uses expand_debt's injectable
``runner`` — no claude subprocess is ever launched.

**§8 §78 修法（owner 决策 D80，issue #447）**：「研究并提议」不再把卡提升进
提案列（那条车道退役了），它**就地把卡写厚**——`raising → detected`，卡留在
潜在任务列，开跑靠 owner 的一次「促成运行」。所以本文件的成功/失败两档不再
靠落点区分（两边都是 `detected`），改靠**卡上多了什么**：成功 = 拿到 LLM 的
summary/plan/DoD 且没有 `auto-expand failed` 面包屑；失败 = 最小卡兜底 + 诚实
的面包屑。欠账绝不丢失这条原判例一字未改。

Runs entirely inside the sandbox AIASSISTANT_HOME (tests/__init__.py).
"""
import json
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import actd, analyze
from act.lib import config, registry
from act.lib.registry import Requirement, State


class _Proc:
    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.returncode = returncode


def _mk_req(req_id="R-950", status=State.RAISING.value, **kw):
    kw.setdefault("title", "调研 carving 板的 sidecut 数据")
    req = Requirement(id=req_id, status=status, **kw)
    registry.save(req)
    return req


class ExpandBase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()


class ExpandDebtTestCase(ExpandBase):
    def test_json_in_prose_expands_the_card_in_place(self):
        req = _mk_req()
        payload = {
            "summary": "整理一份 carving 板参数对比，批准后我来跑调研并给出成稿。",
            "plan": ["收集官方 spec", "对比 sidecut/waist", "写成对比表"],
            "cost_estimate_usd": None,
            "target_repo": "~/Projects/your-workbench",
            "target_kind": "existing",
            "delivery_mode": "chat",
            "definition_of_done": ["会话中给出可直接粘贴的对比表"],
        }
        prose = ("Sure — here is the proposal you asked for:\n\n"
                 + json.dumps(payload, ensure_ascii=False)
                 + "\n\nLet me know if you need anything else.")
        out = analyze.expand_debt(req, runner=lambda p: _Proc(stdout=prose))

        # §78：扩写不跨列——卡回到潜在任务，只是变厚了
        self.assertEqual(str(out.status), State.DETECTED.value)
        self.assertEqual(out.summary, payload["summary"])
        self.assertEqual(out.plan, payload["plan"])
        self.assertEqual(out.delivery_mode, "chat")
        self.assertEqual(out.definition_of_done,
                         ["会话中给出可直接粘贴的对比表"])
        # persisted, not just in memory
        saved = registry.load("R-950")
        self.assertEqual(saved.status, State.DETECTED.value)
        self.assertEqual(saved.summary, payload["summary"])
        self.assertNotIn("auto-expand failed", out.notes or "")

    def test_illegal_delivery_mode_falls_back_to_repo(self):
        req = _mk_req()
        payload = {"summary": "s", "plan": ["p"],
                   "delivery_mode": "carrier-pigeon"}
        out = analyze.expand_debt(
            req, runner=lambda p: _Proc(stdout=json.dumps(payload)))
        self.assertEqual(out.delivery_mode, "repo")
        self.assertEqual(str(out.status), State.DETECTED.value)

    def test_runner_crash_falls_back_without_losing_debt(self):
        req = _mk_req()

        def boom(prompt):
            raise OSError("claude binary vanished")

        out = analyze.expand_debt(req, runner=boom)
        # the debt item is never lost: it comes back to 潜在任务 as a minimal
        # card (§78 起那是唯一的落点), flagged for manual attention — it must
        # never be left stuck in the grey ``raising`` placeholder.
        self.assertEqual(str(out.status), State.DETECTED.value)
        self.assertIn("(auto-expand failed, needs manual)", out.notes)
        self.assertEqual(out.summary, req.title)   # minimal-card fallback
        self.assertEqual(out.plan, [req.title])

    def test_nonzero_exit_falls_back_even_with_json_on_stdout(self):
        req = _mk_req()
        out = analyze.expand_debt(
            req, runner=lambda p: _Proc(
                stdout='{"summary": "half-written"}', returncode=1))
        self.assertEqual(str(out.status), State.DETECTED.value)
        self.assertIn("(auto-expand failed, needs manual)", out.notes)

    def test_unparseable_output_falls_back(self):
        req = _mk_req()
        out = analyze.expand_debt(
            req, runner=lambda p: _Proc(stdout="I could not produce JSON, sorry."))
        self.assertEqual(str(out.status), State.DETECTED.value)
        self.assertIn("(auto-expand failed, needs manual)", out.notes)


class ProcessRaisingTestCase(ExpandBase):
    def test_expander_crash_demotes_to_detected_not_stuck_raising(self):
        _mk_req(req_id="R-951")
        with mock.patch.object(analyze, "expand_debt",
                               side_effect=RuntimeError("expansion exploded")):
            n = actd.process_raising(config.Config())
        self.assertEqual(n, 1)
        req = registry.load("R-951")
        # never stuck in 'raising' (invisible spinner forever): back to the
        # backlog with an honest breadcrumb
        self.assertEqual(req.status, State.DETECTED.value)
        self.assertIn("退回欠账", req.notes)

    def test_happy_path_thickens_the_card_through_the_daemon(self):
        """§78：成功与失败两档同落 detected，所以成功由「卡变厚了」证明.

        原判例断言「守护进程跑完，卡到了提案列」——提案列退役后那个落点与
        上面的崩溃兜底一模一样，光断言 status 会让这条判例与它的邻居互相
        重合、两条一起失去分辨力。成功的真凭据是：LLM 的 summary/plan 真的
        写进了卡里，而且没有 ``auto-expand failed`` 面包屑。
        """
        _mk_req(req_id="R-952")
        payload = {"summary": "s", "plan": ["p"], "delivery_mode": "repo"}
        real_expand = analyze.expand_debt

        def fake_expand(req, cfg=None, runner=None):
            return real_expand(
                req, runner=lambda p: _Proc(stdout=json.dumps(payload)))

        with mock.patch.object(analyze, "expand_debt", side_effect=fake_expand):
            n = actd.process_raising(config.Config())
        self.assertEqual(n, 1)
        out = registry.load("R-952")
        self.assertEqual(out.status, State.DETECTED.value)
        self.assertEqual((out.summary, out.plan), ("s", ["p"]))
        self.assertNotIn("auto-expand failed", out.notes or "")
        self.assertNotIn("退回欠账", out.notes or "")


if __name__ == "__main__":
    unittest.main()
