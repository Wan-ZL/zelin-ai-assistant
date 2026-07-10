"""telemetry.level gating (docs/TELEMETRY.md, CONTRACT §15 telemetry keys).

level="basic" (the default) must keep the dispatch/delivery events
metadata-only; the opt-in "detailed" level adds the <=200-char instruction /
delivery summaries. The gate lives at the EMIT site: at basic the fields never
reach events.jsonl, so they can never be uploaded either.
"""
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import actd, executor
from act.lib import analytics, config, registry
from act.lib.registry import Requirement, State

SID = "aaaa1111-0000-4000-8000-000000000001"  # short id = aaaa1111


def _event(req_id: str, name: str):
    for e in analytics.read_events():
        if e.get("req") == req_id and e.get("event") == name:
            return e
    return None


class ClipTestCase(unittest.TestCase):
    def test_clip_collapses_whitespace_and_truncates(self):
        self.assertEqual(analytics.clip("  a\n b\t c  "), "a b c")
        self.assertEqual(len(analytics.clip("x" * 500)), 200)
        self.assertIsNone(analytics.clip(""))
        self.assertIsNone(analytics.clip(None))


class InstructionSummaryTestCase(unittest.TestCase):
    def test_title_plus_plan_head_capped_at_200(self):
        req = Requirement(id="R-1", title="写周报",
                          plan=["收集数据", "起草", "润色", "第四步"])
        s = executor._instruction_summary(req)
        self.assertIn("写周报", s)
        self.assertIn("收集数据", s)
        self.assertNotIn("第四步", s)  # only the plan head, never the full plan
        self.assertLessEqual(len(s), 200)

    def test_long_title_truncated_and_string_plan_ok(self):
        req = Requirement(id="R-1", title="长" * 300, plan="一句话计划")
        s = executor._instruction_summary(req)
        self.assertEqual(len(s), 200)
        empty = Requirement(id="R-2")
        self.assertIsNone(executor._instruction_summary(empty))


class DispatchLevelGateTestCase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        # existing non-empty target dir -> target_kind=existing, no ensure_repo
        self.target = Path(tempfile.mkdtemp(prefix="tele-target-"))
        (self.target / "keep.txt").write_text("x", encoding="utf-8")
        for patcher in (
            mock.patch.object(executor, "has_remote", return_value=False),
            mock.patch.object(executor.notify, "notify",
                              new=mock.Mock(return_value=True)),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _dispatch(self, req_id: str, level: str):
        cfg = config.Config()
        cfg.memory_inject = False  # don't read the real MEMORY.md
        cfg.telemetry_level = level
        req = Requirement(id=req_id, title="机密任务标题", plan=["step one"],
                          status=State.APPROVED.value,
                          target_repo=str(self.target))
        registry.save(req)
        runner = mock.Mock(return_value=subprocess.CompletedProcess(
            ["claude"], 0, stdout="backgrounded · e88561e5\n", stderr=""))
        executor.dispatch(req, cfg, runner=runner)
        return _event(req_id, "dispatch")

    def test_basic_never_records_instruction(self):
        ev = self._dispatch("R-970", "basic")
        self.assertIsNotNone(ev)
        self.assertNotIn("instruction", ev)

    def test_detailed_records_clipped_instruction(self):
        ev = self._dispatch("R-971", "detailed")
        self.assertIsNotNone(ev)
        self.assertIn("机密任务标题", ev.get("instruction", ""))
        self.assertLessEqual(len(ev["instruction"]), 200)


class ReviewPromotedLevelGateTestCase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()
        p = mock.patch.object(actd.notify, "notify",
                              mock.Mock(return_value=True))
        p.start()
        self.addCleanup(p.stop)

    @staticmethod
    def _agent(state: str) -> dict:
        return {"id": "aaaa1111", "sessionId": SID, "state": state,
                "cwd": "/tmp/wt", "name": "bg agent",
                "startedAt": "2026-07-08T00:00:00Z"}

    def _promote(self, req_id: str, level: str):
        cfg = config.Config()
        cfg.telemetry_level = level
        req = Requirement(id=req_id, title="状态机测试",
                          status=State.EXECUTING.value,
                          execution={"session_id": "aaaa1111"})
        registry.save(req)
        harvest = mock.Mock(
            return_value={"delivered_summary": "已交付：机密摘要 " * 40})
        with mock.patch.object(actd, "_run_claude_agents",
                               return_value=[self._agent("done")]), \
             mock.patch.object(actd.executor, "harvest_delivery", harvest), \
             mock.patch.object(actd.executor, "resume",
                               mock.Mock(return_value=True)):
            actd.reconcile_executing(cfg, set())
        return _event(req_id, "review_promoted")

    def test_basic_never_records_summary(self):
        ev = self._promote("R-980", "basic")
        self.assertIsNotNone(ev)
        self.assertNotIn("summary", ev)

    def test_detailed_records_clipped_summary(self):
        ev = self._promote("R-981", "detailed")
        self.assertIsNotNone(ev)
        self.assertIn("机密摘要", ev.get("summary", ""))
        self.assertLessEqual(len(ev["summary"]), 200)


if __name__ == "__main__":
    unittest.main()
