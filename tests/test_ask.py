"""问问助手 in-app Q&A (CONTRACT §27) — act/ask.py.

Covered:
- bundle assembly is SAFE: adversarial fake secrets (anthropic key file, slack
  token file, config.yaml-pathed gmail password, a token pasted inside a doc)
  never appear in the outbound bundle — presence booleans only + scrub;
- relevance matcher: Chinese and English questions pull the right doc section,
  heading hits outrank body noise, no match -> empty list;
- CLI/answer happy path with a stubbed runner (injectable, merge_review
  pattern): JSON parsed, citation surfaced, history file written newest-first
  and capped at HISTORY_CAP;
- failure paths: timeout -> timeout=True, auth stderr -> classified
  claude_auth_failed, unknown error -> failure_id null (honesty), disabled
  config -> exit 2 semantics via the "disabled" marker.

Runs entirely inside the sandbox AIASSISTANT_HOME (tests/__init__.py).
"""
import json
import subprocess
import unittest
from types import SimpleNamespace
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import ask
from act.lib import config, secrets

FAKE_ANTHROPIC = "sk-ant-FAKEFAKEFAKEFAKEFAKE1234"
FAKE_SLACK = "xoxp-000000000000-FAKEFAKEFAKE"
FAKE_GMAIL = "abcdwxyzabcdwxyz"
FAKE_DOC_TOKEN = "ghp_FAKEFAKEFAKEFAKEFAKE12345"
# adversarial: a token-shaped string inside the doctor section must be masked
# by the final whole-bundle scrub even though doctor itself never emits one
FAKE_DOCTOR_LEAK = "sk-ant-DOCTORLEAKFAKE12345"


def _ok_runner(payload):
    def runner(prompt):
        return SimpleNamespace(returncode=0,
                               stdout=json.dumps(payload, ensure_ascii=False),
                               stderr="")
    return runner


class AskBase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        if ask.HISTORY_PATH.exists():
            ask.HISTORY_PATH.unlink()
        self.docs = config.HOME / "docs"
        self.docs.mkdir(exist_ok=True)
        (config.HOME / "docs" / "TROUBLESHOOTING.md").write_text(
            "# TROUBLESHOOTING\n\n"
            "## 雷达静默数天没有新卡\n\n"
            "症状：数天没有任何新审批卡。原因：cron 环境读不到凭证。\n\n"
            "## 录制模式切换\n\n"
            "菜单栏图标可以在 关/仅屏幕/屏幕+音频 三态之间切换 recording mode。\n"
            f"(fictional example token pasted by a user: {FAKE_DOC_TOKEN})\n",
            encoding="utf-8")
        (config.HOME / "README.md").write_text(
            "# Zelin's AI Assistant\n\nA personal AI secretary.\n",
            encoding="utf-8")
        # doctor --fast spawns real subprocesses (launchctl/claude/gh) — far
        # too slow for the 30+ answer() calls below. One real run is covered
        # by RealDoctorSectionTestCase; here a stub with a planted token also
        # proves the final scrub covers the doctor section.
        patcher = mock.patch.object(
            ask, "_doctor_summary",
            return_value="[ ok ] stub: healthy (pasted key %s)" % FAKE_DOCTOR_LEAK)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _plant_secrets(self):
        secrets.write_secret(secrets.ANTHROPIC_API_KEY_FILE, FAKE_ANTHROPIC)
        secrets.write_secret(secrets.SLACK_TOKEN_FILE, FAKE_SLACK)
        gmail_path = config.HOME / "gmail-pass.txt"
        gmail_path.write_text(FAKE_GMAIL + "\n", encoding="utf-8")
        config.CONFIG_PATH.write_text(
            "sources:\n  gmail:\n    app_password_path: \"%s\"\n" % gmail_path,
            encoding="utf-8")
        self.addCleanup(lambda: config.CONFIG_PATH.unlink(missing_ok=True))


# --------------------------------------------------------------------------- #
# bundle safety — secrets never enter the outbound bundle (adversarial)
# --------------------------------------------------------------------------- #
class BundleSafetyTestCase(AskBase):
    def test_secret_values_never_in_bundle(self):
        self._plant_secrets()
        bundle = ask.build_bundle("为什么没有新卡片?")
        for leak in (FAKE_ANTHROPIC, FAKE_SLACK, FAKE_GMAIL,
                     FAKE_DOC_TOKEN, FAKE_DOCTOR_LEAK):
            self.assertNotIn(leak, bundle)

    def test_bundle_reports_presence_booleans(self):
        self._plant_secrets()
        bundle = ask.build_bundle("anything")
        self.assertIn("anthropic key configured: yes", bundle)
        self.assertIn("slack token configured: yes", bundle)
        self.assertIn("gmail app password configured: yes", bundle)

    def test_bundle_never_contains_gmail_address(self):
        config.CONFIG_PATH.write_text(
            "sources:\n  gmail:\n    address: \"someone.private@gmail.com\"\n",
            encoding="utf-8")
        self.addCleanup(lambda: config.CONFIG_PATH.unlink(missing_ok=True))
        bundle = ask.build_bundle("gmail 怎么配置?")
        self.assertNotIn("someone.private@gmail.com", bundle)

    def test_secret_pasted_in_doc_is_scrubbed_not_dropped(self):
        # the section itself may be relevant; only the token gets masked
        bundle = ask.build_bundle("怎么换录制模式?")
        self.assertIn("录制模式", bundle)
        self.assertNotIn(FAKE_DOC_TOKEN, bundle)


# --------------------------------------------------------------------------- #
# relevance matcher
# --------------------------------------------------------------------------- #
class RelevanceTestCase(AskBase):
    def test_chinese_question_matches_chinese_section(self):
        secs = ask.relevant_sections("怎么换录制模式?")
        self.assertTrue(secs)
        rel, heading, body = secs[0]
        self.assertEqual(rel, "docs/TROUBLESHOOTING.md")
        self.assertIn("录制模式", heading)

    def test_english_question_matches_english_words(self):
        secs = ask.relevant_sections("how do I switch the recording mode?")
        self.assertTrue(secs)
        self.assertIn("recording", secs[0][2].lower())

    def test_no_match_returns_empty(self):
        self.assertEqual(ask.relevant_sections("量子色动力学晶格"), [])

    def test_empty_question_returns_empty(self):
        self.assertEqual(ask.relevant_sections("   "), [])

    def test_heading_hit_outranks_body_noise(self):
        corpus = [
            ("noise.md", "# noise\n\n## other stuff\n\n" + ("录制 " * 5) + "\n"),
            ("hit.md", "# hit\n\n## 录制模式\n\n菜单栏切换。\n"),
        ]
        secs = ask.relevant_sections("录制模式", corpus=corpus)
        self.assertTrue(secs)
        self.assertEqual(secs[0][0], "hit.md")
        self.assertIn("录制模式", secs[0][1])


# --------------------------------------------------------------------------- #
# answer() happy path (stubbed runner) + history
# --------------------------------------------------------------------------- #
class AnswerHappyPathTestCase(AskBase):
    def test_answer_parses_json_and_writes_history(self):
        res = ask.answer("为什么没有新卡片?", runner=_ok_runner(
            {"answer": "cron 环境读不到凭证——去设置页粘贴 API key。",
             "citation": "docs/TROUBLESHOOTING.md · 雷达静默"}))
        self.assertTrue(res["ok"])
        self.assertIn("凭证", res["answer"])
        self.assertEqual(res["citation"], "docs/TROUBLESHOOTING.md · 雷达静默")
        self.assertIn(res["lang"], ("zh", "en"))
        hist = ask.load_history()
        self.assertEqual(len(hist), 1)
        self.assertEqual(hist[0]["q"], "为什么没有新卡片?")
        self.assertEqual(hist[0]["citation"], "docs/TROUBLESHOOTING.md · 雷达静默")

    def test_prose_output_tolerated_as_answer(self):
        res = ask.answer("q", runner=lambda p: SimpleNamespace(
            returncode=0, stdout="就是一段没有 JSON 的回答。", stderr=""))
        self.assertTrue(res["ok"])
        self.assertIsNone(res["citation"])
        self.assertIn("回答", res["answer"])

    def test_history_newest_first_and_capped(self):
        for i in range(ask.HISTORY_CAP + 5):
            ask.answer("q%d" % i, runner=_ok_runner(
                {"answer": "a%d" % i, "citation": None}))
        hist = ask.load_history()
        self.assertEqual(len(hist), ask.HISTORY_CAP)
        self.assertEqual(hist[0]["q"], "q%d" % (ask.HISTORY_CAP + 4))

    def test_corrupt_history_never_blocks(self):
        ask.HISTORY_PATH.write_text("{not json", encoding="utf-8")
        self.assertEqual(ask.load_history(), [])
        res = ask.answer("q", runner=_ok_runner({"answer": "a", "citation": None}))
        self.assertTrue(res["ok"])
        self.assertEqual(len(ask.load_history()), 1)

    def test_prompt_carries_bundle_and_rules(self):
        seen = {}

        def runner(prompt):
            seen["prompt"] = prompt
            return SimpleNamespace(returncode=0,
                                   stdout='{"answer":"a","citation":null}',
                                   stderr="")
        ask.answer("怎么换录制模式?", runner=runner)
        self.assertIn("录制模式", seen["prompt"])          # excerpt made it in
        self.assertIn("GitHub Discussions", seen["prompt"])  # don't-guess rule
        self.assertIn(str(ask.WORD_LIMIT), seen["prompt"])   # length mandate


# --------------------------------------------------------------------------- #
# failure paths — classified, honest
# --------------------------------------------------------------------------- #
class AnswerFailureTestCase(AskBase):
    def test_timeout_is_flagged(self):
        def runner(prompt):
            raise subprocess.TimeoutExpired(cmd="claude", timeout=ask.ASK_TIMEOUT)
        res = ask.answer("q", runner=runner)
        self.assertFalse(res["ok"])
        self.assertTrue(res["timeout"])
        self.assertIn(str(ask.ASK_TIMEOUT), res["error"])

    def test_auth_error_classified(self):
        res = ask.answer("q", runner=lambda p: SimpleNamespace(
            returncode=1, stdout="", stderr="authentication_error: invalid api key"))
        self.assertFalse(res["ok"])
        self.assertEqual(res["failure_id"], "claude_auth_failed")

    def test_missing_cli_classified(self):
        def runner(prompt):
            raise FileNotFoundError("[Errno 2] No such file or directory: 'claude'")
        res = ask.answer("q", runner=runner)
        self.assertFalse(res["ok"])
        self.assertEqual(res["failure_id"], "claude_cli_missing")

    def test_unknown_error_stays_unclassified(self):
        res = ask.answer("q", runner=lambda p: SimpleNamespace(
            returncode=1, stdout="", stderr="some totally novel explosion"))
        self.assertFalse(res["ok"])
        self.assertIsNone(res["failure_id"])
        self.assertIn("novel explosion", res["error"])

    def test_empty_output_is_error(self):
        res = ask.answer("q", runner=lambda p: SimpleNamespace(
            returncode=0, stdout="", stderr=""))
        self.assertFalse(res["ok"])

    def test_empty_question_is_error(self):
        res = ask.answer("   ", runner=_ok_runner({"answer": "a"}))
        self.assertFalse(res["ok"])

    def test_disabled_config_short_circuits(self):
        config.CONFIG_PATH.write_text("ask:\n  enabled: false\n", encoding="utf-8")
        self.addCleanup(lambda: config.CONFIG_PATH.unlink(missing_ok=True))
        res = ask.answer("q", runner=_ok_runner({"answer": "a"}))
        self.assertFalse(res["ok"])
        self.assertTrue(res.get("disabled"))
        # CLI maps disabled -> exit 2 (contract §27)
        self.assertEqual(ask._main(["q"]), 2)

    def test_failed_answer_writes_no_history(self):
        ask.answer("q", runner=lambda p: SimpleNamespace(
            returncode=1, stdout="", stderr="boom"))
        self.assertEqual(ask.load_history(), [])


# --------------------------------------------------------------------------- #
# real doctor section — one run, no stub (slow-ish but honest)
# --------------------------------------------------------------------------- #
class RealDoctorSectionTestCase(unittest.TestCase):
    def test_doctor_summary_never_raises_and_returns_text(self):
        out = ask._doctor_summary()
        self.assertIsInstance(out, str)
        self.assertTrue(out.strip())


# --------------------------------------------------------------------------- #
# config plumbing
# --------------------------------------------------------------------------- #
class AskConfigTestCase(AskBase):
    def test_default_enabled(self):
        self.assertTrue(config.load_config().ask_enabled)

    def test_config_yaml_disable(self):
        config.CONFIG_PATH.write_text("ask:\n  enabled: false\n", encoding="utf-8")
        self.addCleanup(lambda: config.CONFIG_PATH.unlink(missing_ok=True))
        self.assertFalse(config.load_config().ask_enabled)


if __name__ == "__main__":
    unittest.main()
