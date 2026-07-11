"""Typed-text capture gating (docs/TELEMETRY.md, CONTRACT §15 telemetry keys).

Both telemetry levels are metadata-only. User-typed content fields
(instruction / delivery summaries, capture text, Ask questions) require BOTH
`telemetry.capture_input: true` (default false) AND level="detailed" — the
double gate lives at the EMIT site: at any other combination the fields never
reach events.jsonl, so they can never be uploaded either. Content fields are
clipped to analytics.CONTENT_CLIP (500 chars).
"""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import actd, ask, executor
from act.lib import analytics, config, quick_capture, registry
from act.lib.registry import Requirement, State

SID = "aaaa1111-0000-4000-8000-000000000001"  # short id = aaaa1111


def _event(req_id: str, name: str):
    for e in analytics.read_events():
        if e.get("req") == req_id and e.get("event") == name:
            return e
    return None


def _cfg(level: str = "basic", capture: bool = False) -> config.Config:
    cfg = config.Config()
    cfg.telemetry_level = level
    cfg.telemetry_capture_input = capture
    return cfg


class ClipTestCase(unittest.TestCase):
    def test_clip_collapses_whitespace_and_truncates(self):
        self.assertEqual(analytics.clip("  a\n b\t c  "), "a b c")
        self.assertEqual(len(analytics.clip("x" * 500)), 200)
        self.assertIsNone(analytics.clip(""))
        self.assertIsNone(analytics.clip(None))

    def test_content_clip_caps_at_500(self):
        self.assertEqual(analytics.CONTENT_CLIP, 500)
        self.assertEqual(
            len(analytics.clip("x" * 2000, analytics.CONTENT_CLIP)), 500)


class CaptureInputConfigTestCase(unittest.TestCase):
    """The capture_input switch: default FALSE everywhere; explicit config
    values honored; §15 override plumbing (nested + flat forms)."""

    def _load_with_yaml(self, body: str) -> config.Config:
        path = Path(tempfile.mkdtemp(prefix="cfg-capture-")) / "config.yaml"
        path.write_text(body, encoding="utf-8")
        with mock.patch.object(config, "CONFIG_PATH", path):
            return config.load_config()

    def test_dataclass_default_is_false(self):
        self.assertFalse(config.Config().telemetry_capture_input)

    def test_missing_key_resolves_false(self):
        cfg = self._load_with_yaml("telemetry:\n  level: detailed\n")
        self.assertFalse(cfg.telemetry_capture_input)

    def test_explicit_yaml_true_is_honored(self):
        cfg = self._load_with_yaml(
            "telemetry:\n  level: detailed\n  capture_input: true\n")
        self.assertTrue(cfg.telemetry_capture_input)

    def _load_with_overrides(self, data: dict) -> config.Config:
        config.SETTINGS_OVERRIDES_PATH.parent.mkdir(parents=True, exist_ok=True)
        config.SETTINGS_OVERRIDES_PATH.write_text(
            json.dumps(data), encoding="utf-8")
        try:
            return config.load_config()
        finally:
            config.SETTINGS_OVERRIDES_PATH.unlink()

    def test_override_nested_form(self):
        cfg = self._load_with_overrides(
            {"telemetry": {"level": "detailed", "capture_input": True}})
        self.assertTrue(cfg.telemetry_capture_input)
        self.assertEqual(cfg.telemetry_level, "detailed")

    def test_override_flat_form(self):
        cfg = self._load_with_overrides({"telemetry.capture_input": True})
        self.assertTrue(cfg.telemetry_capture_input)

    def test_override_can_turn_it_back_off(self):
        cfg = self._load_with_overrides(
            {"telemetry": {"capture_input": False}})
        self.assertFalse(cfg.telemetry_capture_input)

    def test_capture_input_active_requires_both(self):
        self.assertFalse(_cfg("basic", False).capture_input_active())
        self.assertFalse(_cfg("detailed", False).capture_input_active())
        self.assertFalse(_cfg("basic", True).capture_input_active())
        self.assertTrue(_cfg("detailed", True).capture_input_active())

    def test_content_gate_fails_closed(self):
        self.assertTrue(analytics.content_gate(_cfg("detailed", True)))
        self.assertFalse(analytics.content_gate(_cfg("detailed", False)))
        with mock.patch.object(config, "load_config",
                               side_effect=RuntimeError("boom")):
            self.assertFalse(analytics.content_gate())


class InstructionSummaryTestCase(unittest.TestCase):
    def test_title_plus_plan_head_capped(self):
        req = Requirement(id="R-1", title="写周报",
                          plan=["收集数据", "起草", "润色", "第四步"])
        s = executor._instruction_summary(req)
        self.assertIn("写周报", s)
        self.assertIn("收集数据", s)
        self.assertNotIn("第四步", s)  # only the plan head, never the full plan
        self.assertLessEqual(len(s), analytics.CONTENT_CLIP)

    def test_long_title_truncated_and_string_plan_ok(self):
        req = Requirement(id="R-1", title="长" * 900, plan="一句话计划")
        s = executor._instruction_summary(req)
        self.assertEqual(len(s), analytics.CONTENT_CLIP)
        empty = Requirement(id="R-2")
        self.assertIsNone(executor._instruction_summary(empty))


class DispatchCaptureGateTestCase(unittest.TestCase):
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

    def _dispatch(self, req_id: str, level: str, capture: bool = False):
        cfg = _cfg(level, capture)
        cfg.memory_inject = False  # don't read the real MEMORY.md
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

    def test_detailed_alone_never_records_instruction(self):
        # the new invariant: level=detailed WITHOUT capture_input is still
        # metadata-only — the behavior event itself fires normally.
        ev = self._dispatch("R-971", "detailed")
        self.assertIsNotNone(ev)
        self.assertNotIn("instruction", ev)

    def test_capture_at_basic_never_records_instruction(self):
        ev = self._dispatch("R-972", "basic", capture=True)
        self.assertIsNotNone(ev)
        self.assertNotIn("instruction", ev)

    def test_capture_plus_detailed_records_clipped_instruction(self):
        ev = self._dispatch("R-973", "detailed", capture=True)
        self.assertIsNotNone(ev)
        self.assertIn("机密任务标题", ev.get("instruction", ""))
        self.assertLessEqual(len(ev["instruction"]), analytics.CONTENT_CLIP)


class ReviewPromotedCaptureGateTestCase(unittest.TestCase):
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

    def _promote(self, req_id: str, level: str, capture: bool = False):
        cfg = _cfg(level, capture)
        req = Requirement(id=req_id, title="状态机测试",
                          status=State.EXECUTING.value,
                          execution={"session_id": "aaaa1111",
                                     "dispatched_at": "2026-07-08T00:00:00Z"})
        registry.save(req)
        harvest = mock.Mock(
            return_value={"delivered_summary": "已交付：机密摘要 " * 80})
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

    def test_detailed_alone_never_records_summary(self):
        ev = self._promote("R-981", "detailed")
        self.assertIsNotNone(ev)
        self.assertNotIn("summary", ev)

    def test_capture_plus_detailed_records_clipped_summary(self):
        ev = self._promote("R-982", "detailed", capture=True)
        self.assertIsNotNone(ev)
        self.assertIn("机密摘要", ev.get("summary", ""))
        self.assertLessEqual(len(ev["summary"]), analytics.CONTENT_CLIP)

    def test_exec_seconds_is_metadata_at_basic(self):
        # dispatch -> delivery wall time is a behavior field, NOT content:
        # it rides on the event at plain basic level.
        ev = self._promote("R-983", "basic")
        self.assertIsNotNone(ev)
        self.assertGreaterEqual(ev.get("exec_s", -1), 0)


class AskCaptureGateTestCase(unittest.TestCase):
    def _answer(self, level: str, capture: bool):
        cfg = _cfg(level, capture)
        runner = mock.Mock(return_value=subprocess.CompletedProcess(
            ["claude"], 0,
            stdout='{"answer": "好的", "citation": null}', stderr=""))
        res = ask.answer("这是我打的私密问题 " + "长" * 600, runner=runner, cfg=cfg)
        self.assertTrue(res["ok"])
        for e in analytics.read_events():
            if e.get("event") == "ask_answered" and e.get("ok"):
                last = e
        return last

    def test_detailed_alone_never_records_question(self):
        ev = self._answer("detailed", False)
        self.assertNotIn("question", ev)

    def test_capture_plus_detailed_records_clipped_question(self):
        ev = self._answer("detailed", True)
        self.assertIn("这是我打的私密问题", ev.get("question", ""))
        self.assertLessEqual(len(ev["question"]), analytics.CONTENT_CLIP)


class QuickCaptureGateTestCase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()

    def _apply(self, level: str, capture: bool):
        res = {"action": "new_proposal", "title": "买猫粮",
               "summary": "买猫粮", "_text": "记得买猫粮，皇家 K36"}
        quick_capture.apply_result(res, _cfg(level, capture))
        last = None
        for e in analytics.read_events():
            if e.get("event") == "quick_capture" and \
                    e.get("action") == "new_proposal":
                last = e
        return last

    def test_behavior_event_fires_at_basic_without_text(self):
        ev = self._apply("basic", False)
        self.assertIsNotNone(ev)
        self.assertNotIn("text", ev)

    def test_detailed_alone_never_records_text(self):
        ev = self._apply("detailed", False)
        self.assertIsNotNone(ev)
        self.assertNotIn("text", ev)

    def test_capture_plus_detailed_records_text(self):
        ev = self._apply("detailed", True)
        self.assertIsNotNone(ev)
        self.assertIn("皇家 K36", ev.get("text", ""))
        self.assertLessEqual(len(ev["text"]), analytics.CONTENT_CLIP)


class InboxCaptureGateTestCase(unittest.TestCase):
    """actd._apply_capture resolves the gate via config (no cfg argument) —
    chars is always-on metadata; text needs the sandbox config.yaml to open
    both gates."""

    def setUp(self):
        config.ensure_state_dirs()
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()

    def _capture(self, text: str):
        actd._apply_capture(text)
        last = None
        for e in analytics.read_events():
            if e.get("event") == "inbox_capture":
                last = e
        return last

    def test_default_config_records_chars_but_no_text(self):
        ev = self._capture("默认配置下打的字")
        self.assertIsNotNone(ev)
        self.assertEqual(ev.get("chars"), len("默认配置下打的字"))
        self.assertNotIn("text", ev)

    def test_gates_open_records_text(self):
        config.CONFIG_PATH.write_text(
            "telemetry:\n  level: detailed\n  capture_input: true\n",
            encoding="utf-8")
        self.addCleanup(config.CONFIG_PATH.unlink)
        ev = self._capture("双开关打开后打的字")
        self.assertIsNotNone(ev)
        self.assertIn("双开关打开后打的字", ev.get("text", ""))


if __name__ == "__main__":
    unittest.main()
