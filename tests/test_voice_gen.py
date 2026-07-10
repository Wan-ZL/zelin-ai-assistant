"""act/voice_gen.py — one-click voice-profile generation (docs/VOICE.md).

All passes run with an injected fake runner (no real ``claude`` subprocess is
ever spawned) inside the sandbox AIASSISTANT_HOME (tests/__init__.py).
Contract under test:
(a) success — the induced markdown is written atomically to
    state/voice-profile.md, an existing profile is first copied to
    ``voice-profile.md.bak-<ts>``, and a voice_gen{ok, chars} analytics event
    is logged;
(b) skeleton validation — output missing any of 全局铁律/桶/反面清单 (or a
    marker-only stub) is rejected: non-zero exit, nothing written;
(c) failure (non-zero claude exit / runner exception incl. timeout) NEVER
    overwrites the old profile and reports one plain line on stdout;
(d) the prompt carries the template structure + from:me collection ask, and
    the injected runner fully replaces the claude call.
"""
import contextlib
import io
import json
import subprocess
import unittest

from tests import TMP_HOME  # noqa: F401 - ensures the sandbox env is set first

from act import voice_gen
from act.lib import analytics, config


def _proc(stdout: str = "", returncode: int = 0,
          stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["claude"], returncode=returncode, stdout=stdout, stderr=stderr)


class _FakeRunner:
    """Injectable runner: records prompts, returns a canned result or raises."""

    def __init__(self, result=None, exc=None):
        self.calls: list = []
        self.result = result if result is not None else _proc("")
        self.exc = exc

    def __call__(self, prompt: str):
        self.calls.append(prompt)
        if self.exc is not None:
            raise self.exc
        return self.result


# a plausible induced profile: all three skeleton markers + enough body
_GOOD_PROFILE = (
    "# Voice Profile — Zelin\n\n"
    "## 全局铁律（所有语境）\n\n"
    "1. 短。默认 1–3 句。\n2. 句子简单直白，动词朴素。\n3. 链接直接贴。\n\n"
    "## 桶 A：请求/求助\n\n"
    "模式：一句背景 + 一句明确的 ask。\n\n"
    "- \"Can I get edit access to this board?\"\n"
    "- \"access requested, please take a look. Thank you.\"\n\n"
    "## 桶 B：中文闲聊\n\n"
    "模式：碎片化、常无句末标点。\n\n- \"就alex有回复\"\n- \"卧槽 这么快\"\n\n"
    "## 反面清单（草稿出现以下任何一条 = 重写）\n\n"
    "- 客套开场\n- 签名式收尾\n- em-dash\n"
) + "- 过度 hedging\n" * 10   # padding past the minimum-length floor


def _voice_gen_events() -> list:
    try:
        lines = analytics.EVENTS_PATH.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for ln in lines:
        try:
            rec = json.loads(ln)
        except json.JSONDecodeError:
            continue
        if rec.get("event") == "voice_gen":
            out.append(rec)
    return out


class VoiceGenTestCase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        self.dest = voice_gen.profile_path()
        self.dest.unlink(missing_ok=True)
        for bak in self.dest.parent.glob(self.dest.name + ".bak-*"):
            bak.unlink()
        analytics.EVENTS_PATH.unlink(missing_ok=True)
        self.cfg = config.Config()

    def tearDown(self):
        # other suites (voice-profile injection) also key off this file
        self.dest.unlink(missing_ok=True)
        for bak in self.dest.parent.glob(self.dest.name + ".bak-*"):
            bak.unlink()

    def _baks(self):
        return sorted(self.dest.parent.glob(self.dest.name + ".bak-*"))

    # -- (a) success -------------------------------------------------------- #
    def test_success_writes_profile_and_logs_analytics(self):
        runner = _FakeRunner(_proc(_GOOD_PROFILE))
        ok, msg = voice_gen.generate(self.cfg, runner=runner)
        self.assertTrue(ok, msg)
        self.assertEqual(len(runner.calls), 1)     # exactly one claude pass
        self.assertTrue(self.dest.exists())
        self.assertIn("全局铁律", self.dest.read_text(encoding="utf-8"))
        self.assertEqual(self._baks(), [])         # nothing to back up
        events = _voice_gen_events()
        self.assertEqual(len(events), 1)
        self.assertTrue(events[0]["ok"])
        self.assertGreater(events[0]["chars"], 0)

    def test_success_backs_up_existing_profile(self):
        old = "# old profile\n全局铁律 (old)\n"
        self.dest.write_text(old, encoding="utf-8")
        runner = _FakeRunner(_proc(_GOOD_PROFILE))
        ok, msg = voice_gen.generate(self.cfg, runner=runner)
        self.assertTrue(ok, msg)
        self.assertIn("桶 A", self.dest.read_text(encoding="utf-8"))  # replaced
        baks = self._baks()
        self.assertEqual(len(baks), 1)             # old content preserved aside
        self.assertEqual(baks[0].read_text(encoding="utf-8"), old)
        self.assertIn(baks[0].name, msg)           # the human line mentions it

    def test_fenced_output_is_tolerated(self):
        runner = _FakeRunner(_proc(f"```markdown\n{_GOOD_PROFILE}\n```"))
        ok, _ = voice_gen.generate(self.cfg, runner=runner)
        self.assertTrue(ok)
        text = self.dest.read_text(encoding="utf-8")
        self.assertNotIn("```", text)

    # -- (b) skeleton validation ------------------------------------------- #
    def test_missing_marker_is_rejected_unwritten(self):
        bad = _GOOD_PROFILE.replace("反面清单", "负面观察")
        runner = _FakeRunner(_proc(bad))
        ok, msg = voice_gen.generate(self.cfg, runner=runner)
        self.assertFalse(ok)
        self.assertFalse(self.dest.exists())       # nothing written
        self.assertEqual(self._baks(), [])
        self.assertIn("反面清单", msg)              # names the missing part
        events = _voice_gen_events()
        self.assertEqual(len(events), 1)
        self.assertFalse(events[0]["ok"])

    def test_marker_only_stub_is_rejected(self):
        runner = _FakeRunner(_proc("全局铁律 桶 反面清单"))
        ok, _ = voice_gen.generate(self.cfg, runner=runner)
        self.assertFalse(ok)
        self.assertFalse(self.dest.exists())

    # -- (c) failure never overwrites the old profile ----------------------- #
    def test_claude_error_keeps_old_profile(self):
        old = "# my precious profile\n"
        self.dest.write_text(old, encoding="utf-8")
        runner = _FakeRunner(_proc("", returncode=1, stderr="MCP server not found"))
        ok, msg = voice_gen.generate(self.cfg, runner=runner)
        self.assertFalse(ok)
        self.assertEqual(self.dest.read_text(encoding="utf-8"), old)
        self.assertEqual(self._baks(), [])
        self.assertTrue(msg.strip())               # one human line, non-empty

    def test_timeout_keeps_old_profile(self):
        old = "# my precious profile\n"
        self.dest.write_text(old, encoding="utf-8")
        runner = _FakeRunner(exc=subprocess.TimeoutExpired(cmd="claude", timeout=600))
        ok, _ = voice_gen.generate(self.cfg, runner=runner)
        self.assertFalse(ok)
        self.assertEqual(self.dest.read_text(encoding="utf-8"), old)

    def test_bad_skeleton_keeps_old_profile(self):
        old = "# my precious profile\n"
        self.dest.write_text(old, encoding="utf-8")
        runner = _FakeRunner(_proc("not a profile at all"))
        ok, _ = voice_gen.generate(self.cfg, runner=runner)
        self.assertFalse(ok)
        self.assertEqual(self.dest.read_text(encoding="utf-8"), old)

    # -- (d) prompt contract + CLI ------------------------------------------ #
    def test_prompt_asks_for_own_messages_with_template_skeleton(self):
        runner = _FakeRunner(_proc(_GOOD_PROFILE))
        voice_gen.generate(self.cfg, runner=runner)
        prompt = runner.calls[0]
        self.assertIn("from:me", prompt)
        self.assertIn("100–200", prompt)
        for marker in ("全局铁律", "桶", "反面清单"):
            self.assertIn(marker, prompt)

    def test_prompt_embeds_shipped_template_when_present(self):
        tpl = voice_gen.template_path()
        tpl.parent.mkdir(parents=True, exist_ok=True)
        tpl.write_text("## 全局铁律\nTEMPLATE-SENTINEL-XYZ\n## 反面清单\n",
                       encoding="utf-8")
        try:
            runner = _FakeRunner(_proc(_GOOD_PROFILE))
            voice_gen.generate(self.cfg, runner=runner)
            self.assertIn("TEMPLATE-SENTINEL-XYZ", runner.calls[0])
        finally:
            tpl.unlink(missing_ok=True)

    def test_cli_exit_codes_and_stdout(self):
        # success -> 0, one line naming the written file
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = voice_gen._main([], runner=_FakeRunner(_proc(_GOOD_PROFILE)))
        self.assertEqual(rc, 0)
        self.assertIn(str(voice_gen.profile_path()), buf.getvalue())
        # failure -> non-zero, one plain line on STDOUT (settings page shows it)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = voice_gen._main([], runner=_FakeRunner(_proc("", returncode=1)))
        self.assertEqual(rc, 1)
        self.assertTrue(buf.getvalue().strip())


if __name__ == "__main__":
    unittest.main()
