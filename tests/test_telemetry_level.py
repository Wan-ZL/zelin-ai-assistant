"""Typed-text capture gating (docs/TELEMETRY.md, CONTRACT §15 telemetry keys).

User-typed content fields (instruction summaries, capture text,
Ask questions) require BOTH `telemetry.capture_input: true` AND
level="detailed" — SHIPPED DEFAULTS since v0.18, which is why the disclosure
copy must say typed text is included (DisclosureCopyHonestyTestCase guards
that). The double gate lives at the EMIT site: with either switch off the
fields never reach events.jsonl, so they can never be uploaded either.
Content fields are clipped to analytics.CONTENT_CLIP (500 chars). Scope
boundary (RadarContentBoundaryTestCase): only text the user types into this
app — radar-extracted third-party content never enters telemetry.
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
    # tests here simulate an EXPLICIT user choice — the v2-consent-marker
    # path is covered separately (ConsentV2GateTestCase)
    cfg.telemetry_capture_input_explicit = True
    return cfg


# user-origin provenance for cards whose instruction may be captured
# (executor._USER_ORIGIN_CHANNELS)
_QUICK_SOURCES = [{"who": "zelin", "channel": "quick_capture",
                   "date": "2026-07-11", "quote": "typed"}]


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
    """The capture_input switch: shipped default TRUE (v0.18, with
    level=detailed — a vanilla install collects typed text and the docs say
    so); explicit config values honored; §15 override plumbing (nested +
    flat forms)."""

    def _load_with_yaml(self, body: str) -> config.Config:
        path = Path(tempfile.mkdtemp(prefix="cfg-capture-")) / "config.yaml"
        path.write_text(body, encoding="utf-8")
        with mock.patch.object(config, "CONFIG_PATH", path):
            return config.load_config()

    def test_dataclass_default_is_true(self):
        self.assertTrue(config.Config().telemetry_capture_input)
        self.assertTrue(config.Config().capture_input_active())

    def test_missing_key_resolves_true(self):
        cfg = self._load_with_yaml("telemetry:\n  level: detailed\n")
        self.assertTrue(cfg.telemetry_capture_input)

    def test_explicit_yaml_false_is_honored(self):
        cfg = self._load_with_yaml(
            "telemetry:\n  level: detailed\n  capture_input: false\n")
        self.assertFalse(cfg.telemetry_capture_input)
        self.assertFalse(cfg.capture_input_active())

    def test_basic_level_disables_content_even_with_capture_on(self):
        cfg = self._load_with_yaml("telemetry:\n  level: basic\n")
        self.assertTrue(cfg.telemetry_capture_input)  # switch untouched
        self.assertFalse(cfg.capture_input_active())  # but the gate is shut

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
    """Provenance-gated (docs/TELEMETRY.md scope red line): the approved
    TITLE only (the model-drafted plan stays out), and only when every
    source is the user's own capture."""

    def test_user_origin_title_only_no_plan(self):
        req = Requirement(id="R-1", title="写周报",
                          plan=["收集数据", "起草", "润色"],
                          sources=list(_QUICK_SOURCES))
        s = executor._instruction_summary(req)
        self.assertIn("写周报", s)
        self.assertNotIn("收集数据", s)  # plan is model-drafted — never sent
        self.assertLessEqual(len(s), analytics.CONTENT_CLIP)

    def test_long_title_truncated(self):
        req = Requirement(id="R-1", title="长" * 900,
                          sources=list(_QUICK_SOURCES))
        self.assertEqual(len(executor._instruction_summary(req)),
                         analytics.CONTENT_CLIP)

    def test_third_party_sources_yield_none(self):
        # radar cards summarize OTHER PEOPLE's emails/messages/screen —
        # any non-allowlisted channel (or a mix) kills the field entirely
        for chan in ("gmail", "slack", "meeting", "claude_code", "future_x"):
            req = Requirement(id="R-1", title="来自第三方的标题",
                              sources=[{"channel": chan, "quote": "x"},
                                       *_QUICK_SOURCES])
            self.assertIsNone(executor._instruction_summary(req), chan)

    def test_no_sources_yield_none(self):
        self.assertIsNone(executor._instruction_summary(
            Requirement(id="R-2", title="孤儿卡")))


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

    def _dispatch(self, req_id: str, level: str, capture: bool = False,
                  sources=None):
        cfg = _cfg(level, capture)
        cfg.memory_inject = False  # don't read the real MEMORY.md
        req = Requirement(id=req_id, title="机密任务标题", plan=["step one"],
                          status=State.APPROVED.value,
                          target_repo=str(self.target),
                          sources=(list(_QUICK_SOURCES) if sources is None
                                   else sources))
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

    def test_radar_origin_card_never_records_instruction(self):
        # scope red line: an approved RADAR card's title summarizes someone
        # else's email/message/screen — both gates open, still no instruction
        ev = self._dispatch(
            "R-974", "detailed", capture=True,
            sources=[{"who": "boss", "channel": "gmail",
                      "date": "2026-07-11", "quote": "email body extract"}])
        self.assertIsNotNone(ev)
        self.assertNotIn("instruction", ev)


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

    def test_summary_is_model_output_and_never_uploads(self):
        # red line (docs/TELEMETRY.md): delivered_summary is MODEL OUTPUT —
        # retired from telemetry entirely in v0.18, even with BOTH gates open.
        ev = self._promote("R-982", "detailed", capture=True)
        self.assertIsNotNone(ev)
        self.assertNotIn("summary", ev)

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
    chars is always-on metadata; the DEFAULT config (no config.yaml, v0.18
    shipped defaults) has both gates open, so text IS recorded; an explicit
    capture_input: false stops it."""

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

    def _show_v2_disclosure(self):
        analytics.CONSENT_V2_PATH.parent.mkdir(parents=True, exist_ok=True)
        analytics.CONSENT_V2_PATH.write_text("2026-07-11T00:00:00Z\n",
                                             encoding="utf-8")
        self.addCleanup(lambda: analytics.CONSENT_V2_PATH.unlink(
            missing_ok=True))

    def test_default_config_records_chars_and_text(self):
        self._show_v2_disclosure()  # the new disclosure has been seen
        ev = self._capture("默认配置下打的字")
        self.assertIsNotNone(ev)
        self.assertEqual(ev.get("chars"), len("默认配置下打的字"))
        self.assertIn("默认配置下打的字", ev.get("text", ""))

    def test_upgraded_install_without_v2_disclosure_drops_text(self):
        # FIX 2 (CONTRACT §15 v0.18): a pre-v0.18 install has only the OLD
        # consent marker, written when the copy said "no personal text" —
        # default-on content must NOT flow until the new disclosure renders
        # (behavior fields like chars keep flowing on the old marker).
        from act.lib import analytics_sync
        analytics_sync.CONSENT_MARKER_PATH.parent.mkdir(
            parents=True, exist_ok=True)
        analytics_sync.CONSENT_MARKER_PATH.write_text(
            "2026-07-01T00:00:00Z\n", encoding="utf-8")
        self.addCleanup(lambda: analytics_sync.CONSENT_MARKER_PATH.unlink(
            missing_ok=True))
        ev = self._capture("升级用户打的字")
        self.assertIsNotNone(ev)
        self.assertEqual(ev.get("chars"), len("升级用户打的字"))
        self.assertNotIn("text", ev)

    def test_capture_off_keeps_chars_but_drops_text(self):
        config.CONFIG_PATH.write_text(
            "telemetry:\n  capture_input: false\n", encoding="utf-8")
        self.addCleanup(config.CONFIG_PATH.unlink)
        ev = self._capture("关掉开关后打的字")
        self.assertIsNotNone(ev)
        self.assertEqual(ev.get("chars"), len("关掉开关后打的字"))
        self.assertNotIn("text", ev)

    def test_basic_level_also_drops_text(self):
        config.CONFIG_PATH.write_text(
            "telemetry:\n  level: basic\n", encoding="utf-8")
        self.addCleanup(config.CONFIG_PATH.unlink)
        ev = self._capture("基础档下打的字")
        self.assertIsNotNone(ev)
        self.assertNotIn("text", ev)


class ExampleConfigDefaultsTestCase(unittest.TestCase):
    """The SHIPPED example config must resolve to content collection ON —
    and actually produce content on an emit path (audit requirement: the
    vanilla download collects the full set out of the box)."""

    REPO_EXAMPLE = Path(__file__).resolve().parent.parent / "config.example.yaml"

    def _example_cfg(self) -> config.Config:
        missing = Path(tempfile.mkdtemp(prefix="cfg-none-")) / "config.yaml"
        with mock.patch.object(config, "CONFIG_PATH", missing), \
             mock.patch.object(config, "CONFIG_EXAMPLE_PATH",
                               self.REPO_EXAMPLE):
            return config.load_config()

    def test_example_defaults_collect_content(self):
        cfg = self._example_cfg()
        self.assertTrue(cfg.telemetry_enabled)
        self.assertEqual(cfg.telemetry_level, "detailed")
        self.assertTrue(cfg.telemetry_capture_input)
        self.assertTrue(cfg.capture_input_active())

    def test_example_defaults_produce_content_on_ask(self):
        # the v2 disclosure has rendered (fresh installs see it on first
        # run) — then the shipped defaults really do produce content
        analytics.CONSENT_V2_PATH.parent.mkdir(parents=True, exist_ok=True)
        analytics.CONSENT_V2_PATH.write_text("2026-07-11T00:00:00Z\n",
                                             encoding="utf-8")
        self.addCleanup(lambda: analytics.CONSENT_V2_PATH.unlink(
            missing_ok=True))
        cfg = self._example_cfg()
        runner = mock.Mock(return_value=subprocess.CompletedProcess(
            ["claude"], 0,
            stdout='{"answer": "好的", "citation": null}', stderr=""))
        res = ask.answer("出厂默认下打的问题", runner=runner, cfg=cfg)
        self.assertTrue(res["ok"])
        last = None
        for e in analytics.read_events():
            if e.get("event") == "ask_answered" and e.get("ok"):
                last = e
        self.assertIn("出厂默认下打的问题", last.get("question", ""))


class ConsentV2GateTestCase(unittest.TestCase):
    """FIX 2 (CONTRACT §15 v0.18): the built-in default-on only arms after
    the v2 disclosure marker exists; an EXPLICIT capture_input (config.yaml /
    overrides) is its own informed choice and needs no marker."""

    def tearDown(self):
        analytics.CONSENT_V2_PATH.unlink(missing_ok=True)

    def _default_cfg(self) -> config.Config:
        cfg = config.Config()  # shipped defaults: detailed + capture on
        self.assertFalse(cfg.telemetry_capture_input_explicit)
        return cfg

    def test_defaults_without_marker_gate_closed(self):
        self.assertFalse(analytics.content_gate(self._default_cfg()))

    def test_defaults_with_v2_marker_gate_open(self):
        analytics.CONSENT_V2_PATH.parent.mkdir(parents=True, exist_ok=True)
        analytics.CONSENT_V2_PATH.write_text("x\n", encoding="utf-8")
        self.assertTrue(analytics.content_gate(self._default_cfg()))

    def test_explicit_capture_input_needs_no_marker(self):
        cfg = self._default_cfg()
        cfg.telemetry_capture_input_explicit = True
        self.assertTrue(analytics.content_gate(cfg))

    def test_explicit_flag_set_by_yaml_and_overrides(self):
        path = Path(tempfile.mkdtemp(prefix="cfg-v2-")) / "config.yaml"
        path.write_text("telemetry:\n  capture_input: true\n",
                        encoding="utf-8")
        with mock.patch.object(config, "CONFIG_PATH", path):
            self.assertTrue(config.load_config().telemetry_capture_input_explicit)
        config.SETTINGS_OVERRIDES_PATH.parent.mkdir(parents=True, exist_ok=True)
        config.SETTINGS_OVERRIDES_PATH.write_text(
            json.dumps({"telemetry": {"capture_input": True}}),
            encoding="utf-8")
        try:
            self.assertTrue(config.load_config().telemetry_capture_input_explicit)
        finally:
            config.SETTINGS_OVERRIDES_PATH.unlink()

    def test_absent_key_leaves_explicit_false(self):
        missing = Path(tempfile.mkdtemp(prefix="cfg-v2-none-")) / "config.yaml"
        with mock.patch.object(config, "CONFIG_PATH", missing):
            cfg = config.load_config()
        self.assertTrue(cfg.telemetry_capture_input)      # default on…
        self.assertFalse(cfg.telemetry_capture_input_explicit)  # …but not consent


class SecretMaskTestCase(unittest.TestCase):
    """FIX 3: every content field passes clip_content — secret shapes are
    masked UNCONDITIONALLY before hitting events.jsonl (the copy promises
    keys never ride in telemetry at any setting)."""

    def test_clip_content_masks_secret_shapes(self):
        s = analytics.clip_content(
            "我的 key 是 sk-ant-abcdefgh12345678 和 xoxb-1234-abcdefgh "
            "还有 AKIAABCDEFGHIJKLMNOP")
        self.assertNotIn("sk-ant-abcdefgh12345678", s)
        self.assertNotIn("xoxb-1234-abcdefgh", s)
        self.assertNotIn("AKIAABCDEFGHIJKLMNOP", s)
        self.assertIn("[脱敏]", s)

    def test_clip_content_masks_before_clipping(self):
        # a key sitting right at the cap must not survive half-masked
        s = analytics.clip_content("x" * 490 + " sk-ant-abcdefgh12345678")
        self.assertNotIn("sk-ant-", s)

    def test_masked_secret_never_reaches_the_event(self):
        cfg = _cfg("detailed", True)
        runner = mock.Mock(return_value=subprocess.CompletedProcess(
            ["claude"], 0,
            stdout='{"answer": "好的", "citation": null}', stderr=""))
        res = ask.answer("帮我看看这个 key: sk-ant-abcdefgh12345678 行不行",
                         runner=runner, cfg=cfg)
        self.assertTrue(res["ok"])
        last = None
        for e in analytics.read_events():
            if e.get("event") == "ask_answered" and e.get("ok"):
                last = e
        self.assertIn("question", last)
        self.assertNotIn("sk-ant-abcdefgh12345678", last["question"])
        self.assertIn("[脱敏]", last["question"])


class RadarContentBoundaryTestCase(unittest.TestCase):
    """Scope red line: radar candidates originate from third-party content
    (screen OCR / emails / Slack messages) — radar_triage events must stay
    metadata-only even with BOTH gates open."""

    def setUp(self):
        config.ensure_state_dirs()
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()

    def test_radar_triage_never_carries_content(self):
        cfg = _cfg("detailed", True)  # gates fully open on purpose
        req = Requirement(id=registry.next_id(), title="第三方邮件里提取的事项",
                          summary="来自邮件正文的敏感内容",
                          status=State.DETECTED.value)
        decision = {"action": "new_proposal",
                    "note": "note derived from third-party mail"}
        quick_capture.apply_triage(decision, req, cfg)
        evs = [e for e in analytics.read_events()
               if e.get("event") == "radar_triage"]
        self.assertTrue(evs)
        for e in evs:
            for banned in ("text", "note", "quote", "comment", "summary",
                           "title", "instruction", "question"):
                self.assertNotIn(banned, e)


class DisclosureCopyHonestyTestCase(unittest.TestCase):
    """Truth-in-labeling drift-guard (CONTRACT §15 v0.18): while
    capture_input defaults ON, the first-run disclosure must SAY typed text
    is included, and no consent/settings copy may claim no personal text is
    collected. Checked against the Swift sources like the
    tests/test_capture_exclusion.py drift-guard."""

    ROOT = Path(__file__).resolve().parent.parent
    PERMISSIONS = ROOT / "mac" / "Sources" / "Permissions.swift"
    SETTINGS = ROOT / "mac" / "Sources" / "Settings.swift"
    ASK = ROOT / "mac" / "Sources" / "Ask.swift"
    UTILS = ROOT / "mac" / "Sources" / "Utils.swift"
    INSTALL = ROOT / "docs" / "INSTALL.md"
    EXAMPLE = ROOT / "config.example.yaml"

    def test_first_run_disclosure_mentions_typed_text(self):
        src = self.PERMISSIONS.read_text(encoding="utf-8")
        self.assertIn("你输入的文本", src)
        self.assertIn("text you type", src)

    def test_no_copy_denies_text_collection(self):
        banned = ("绝不含屏幕内容、对话或任何个人文本",
                  "不含屏幕内容或你输入的文字",
                  "不含任何内容文字",
                  "never any personal text",
                  "never screen content, conversations, or any personal",
                  "never .* anything you type")
        import re
        for path in (self.PERMISSIONS, self.SETTINGS, self.ASK, self.INSTALL):
            src = path.read_text(encoding="utf-8")
            for phrase in banned:
                self.assertIsNone(re.search(phrase, src),
                                  f"{path.name} still claims: {phrase!r}")

    def test_install_doc_current(self):
        # FIX 4: the retired first-run checkbox must not be described, and
        # the typed-text default must be disclosed
        src = self.INSTALL.read_text(encoding="utf-8")
        self.assertNotIn("checkbox", src.lower())
        self.assertNotIn("取消勾选匿名", src)
        self.assertIn("text you type", src)
        self.assertIn("你输入", src)

    def test_ask_tooltips_current(self):
        # FIX 4: pre-capture_input tooltip wording ("Detailed attaches the
        # question") must be gone — the gate is the Settings text toggle
        src = self.ASK.read_text(encoding="utf-8")
        self.assertNotIn("Basic carries no question text", src)
        self.assertNotIn("基础级不含问题内容", src)
        self.assertIn("上传我输入的文本", src)

    def test_example_config_documents_default_on(self):
        src = self.EXAMPLE.read_text(encoding="utf-8")
        self.assertIn("capture_input: true", src)

    def test_settings_never_writes_the_v2_marker_passively(self):
        # HOLE 2 regression guard: SettingsFormView is a non-lazy VStack —
        # .onAppear fires on page INSERTION, not section visibility, so a
        # marker writer there would arm content for upgraded installs that
        # never saw the disclosure. Only the first-run disclosure block
        # (Permissions.swift TelemetryBlockView) may write it; Settings
        # opts in via the explicit capture_input key (captureTouched).
        self.assertNotIn("markSurfaceShownV2",
                         self.SETTINGS.read_text(encoding="utf-8"))
        self.assertIn("markSurfaceShownV2",
                      self.PERMISSIONS.read_text(encoding="utf-8"))

    def test_swift_secret_patterns_mirror_python(self):
        # FIX 3 drift-guard: the Swift Analytics.clip masker must carry every
        # pattern sanitize._SECRET_PATTERNS has (source-literal comparison —
        # a pattern added in python without the Swift port fails here)
        from act.lib import sanitize
        src = self.UTILS.read_text(encoding="utf-8")
        for pat in sanitize._SECRET_PATTERNS:
            literal = pat.pattern.replace("\\", "\\\\")
            self.assertIn(literal, src,
                          f"Utils.swift missing secret pattern {pat.pattern!r}")


if __name__ == "__main__":
    unittest.main()
