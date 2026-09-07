"""§10 capture keeps the line breaks of a multi-line text (D52, 2026-09-06).

Before D52 ``act/lib/actd/inbox.py`` ``_capture_text`` collapsed ALL whitespace
(newlines included) to single spaces, so the structure typed into the D35
multi-line composer was gone before the LLM / agent ever saw it. Now:

- ``normalize_capture_text``: CRLF → LF, interior space runs collapse per
  line, trailing spaces stripped, the common leading indentation (spaces / tabs
  only) is dedented and the RELATIVE indentation kept, consecutive blank lines
  capped at one, outer blank lines stripped; a single-line input is
  byte-identical to the old flattening.
- ``capture_title``: the card ``title`` stays ONE line ≤80 chars (flattened —
  the dedupe / re-raise identity anchor, §37).
- End to end (inbox file → card): ``sources[0].quote`` and the text handed to
  the proposal / direct-run paths keep the newlines; the expand prompt (§10 →
  process_raising) and the dispatch prompt (§34 direct run, §4 fencing) both
  carry the user's lines. Fixtures / goldens elsewhere are untouched.

Runs inside the sandbox AIASSISTANT_HOME (tests/__init__.py); no LLM, no
subprocess.
"""
import json
import unittest
import uuid
from pathlib import Path

from tests import TMP_HOME  # noqa: F401 - sandbox env first

from act import actd, analyze
from act.lib import config, dispatch_prompt, registry
from act.lib.actd import inbox
from act.lib.registry import State

MULTI = "给 my-bench 加导出按钮\n- 支持 CSV\n- 支持 PDF\n\n附：上周会议提过一次"


class NormalizeCaptureTextTestCase(unittest.TestCase):
    def test_single_line_is_identical_to_the_old_flattening(self):
        for text in ["给 my-bench 加一个一键导出报告按钮", "  a   b\tc  ", "x"]:
            self.assertEqual(inbox.normalize_capture_text(text),
                             " ".join(text.split()).strip())

    def test_newlines_survive_and_crlf_becomes_lf(self):
        self.assertEqual(inbox.normalize_capture_text("a\r\nb\rc\nd"), "a\nb\nc\nd")

    def test_interior_space_runs_collapse_and_trailing_spaces_go(self):
        self.assertEqual(inbox.normalize_capture_text("a   b \t c   \nd  e  "),
                         "a b c\nd e")

    def test_leading_indentation_is_kept(self):
        # nested lists / pasted code depend on it; only interior runs collapse
        self.assertEqual(inbox.normalize_capture_text("- 主项\n    - 子项   甲\n\t- 子项 乙"),
                         "- 主项\n    - 子项 甲\n\t- 子项 乙")

    def test_consecutive_blank_lines_cap_at_one(self):
        self.assertEqual(inbox.normalize_capture_text("a\n\n\n\nb\n \n  \nc"),
                         "a\n\nb\n\nc")

    def test_outer_blank_lines_are_stripped_but_a_first_line_indent_is_relative(self):
        # only the outer blank lines go; the first line is not special — its
        # indent relative to line 2 survives (review of D52: `.strip()` used to
        # eat it and turn "  - a\n  - b" into parent + nested child)
        self.assertEqual(inbox.normalize_capture_text("\n\n   标题\n正文\n\n\n"),
                         "   标题\n正文")

    def test_common_indentation_is_dedented_relative_indentation_kept(self):
        # sibling bullets stay siblings; pasted code keeps its nesting
        self.assertEqual(inbox.normalize_capture_text("  - a\n  - b"), "- a\n- b")
        self.assertEqual(inbox.normalize_capture_text("    def f():\n        return 1"),
                         "def f():\n    return 1")
        # blank lines do not count towards the common indent
        self.assertEqual(inbox.normalize_capture_text("  x\n\n  y"), "x\n\ny")
        # tabs and spaces are distinct characters: only the truly common prefix goes
        self.assertEqual(inbox.normalize_capture_text("\t\ta\n\t  b"), "\ta\n  b")

    def test_only_spaces_and_tabs_count_as_indentation(self):
        # NBSP / U+3000 / form feed / vertical tab / U+2028 at the start of a
        # line collapse exactly like they did before D52 (interior runs already
        # did) — they never ride into the quote / prompts as "indentation"
        for odd in ["\xa0\xa0", "\u3000\u3000", "\x0c\x0c", "\x0b", "\u2028"]:
            self.assertEqual(inbox.normalize_capture_text("x\n" + odd + "y"), "x\ny", repr(odd))
            self.assertEqual(inbox.normalize_capture_text(odd + "x"), "x", repr(odd))
        # a real space in front of the odd run is indentation; the run behind it collapses
        self.assertEqual(inbox.normalize_capture_text("x\n \xa0 y"), "x\n y")

    def test_whitespace_only_is_empty(self):
        for text in ["", "   ", "\r\n \n\t\n"]:
            self.assertEqual(inbox.normalize_capture_text(text), "")


class CaptureTitleTestCase(unittest.TestCase):
    def test_multi_line_text_flattens_to_one_line(self):
        self.assertEqual(inbox.capture_title(MULTI),
                         "给 my-bench 加导出按钮 - 支持 CSV - 支持 PDF 附：上周会议提过一次")
        self.assertNotIn("\n", inbox.capture_title(MULTI))

    def test_title_caps_at_80_chars(self):
        text = "\n".join(["第%d行的字" % i for i in range(40)])
        title = inbox.capture_title(text)
        self.assertEqual(len(title), 80)
        self.assertNotIn("\n", title)

    def test_single_line_title_is_the_text(self):
        self.assertEqual(inbox.capture_title("修一下登录页的 bug"), "修一下登录页的 bug")


def _reset_sandbox():
    config.ensure_state_dirs()
    if config.REGISTRY_DIR.exists():
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()
    for p in config.INBOX_DIR.glob("*.json"):
        p.unlink()


def _write_capture(text, mode=None) -> str:
    payload = {"action": "capture", "text": text, "ts": "2026-09-06T00:00:00Z"}
    if mode is not None:
        payload["mode"] = mode
    stem = f"capture-{uuid.uuid4()}"
    (config.INBOX_DIR / f"{stem}.json").write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return stem


def _only_card() -> registry.Requirement:
    cards = registry.load_all()
    assert len(cards) == 1, cards
    return cards[0]


class CaptureKeepsNewlinesEndToEndTestCase(unittest.TestCase):
    def setUp(self):
        _reset_sandbox()

    def test_proposal_capture_keeps_newlines_in_quote_and_flattens_title(self):
        _write_capture(MULTI)
        self.assertEqual(actd.process_inbox(), 1)
        req = _only_card()
        self.assertEqual(req.status, State.RAISING.value)
        self.assertEqual(req.sources[0]["channel"], "quick_capture")
        self.assertEqual(req.sources[0]["quote"], MULTI)
        self.assertEqual(req.title, inbox.capture_title(MULTI))
        self.assertNotIn("\n", req.title)

    def test_quote_round_trips_through_the_yaml_on_disk(self):
        _write_capture(MULTI)
        actd.process_inbox()
        rid = _only_card().id
        # re-read from disk, not from the in-memory object
        fresh = registry.load(rid)
        self.assertIsNotNone(fresh)
        self.assertEqual(fresh.sources[0]["quote"], MULTI)

    def test_direct_run_capture_keeps_newlines_too(self):
        _write_capture(MULTI, mode="run")
        self.assertEqual(actd.process_inbox(), 1)
        req = _only_card()
        self.assertEqual(req.status, State.APPROVED.value)
        self.assertEqual(req.sources[0]["quote"], MULTI)
        self.assertEqual(req.title, inbox.capture_title(MULTI))

    def test_crlf_from_a_windows_ish_client_lands_as_lf(self):
        _write_capture("第一行\r\n第二行\r\n")
        actd.process_inbox()
        self.assertEqual(_only_card().sources[0]["quote"], "第一行\n第二行")

    def test_whitespace_and_newline_only_text_files_nothing(self):
        _write_capture("\r\n  \n\t\n")
        actd.process_inbox()
        self.assertEqual(registry.load_all(), [])
        self.assertEqual(list(config.INBOX_DIR.glob("*.json")), [])

    def test_same_words_with_and_without_newlines_dedupe_to_one_card(self):
        # title = flattened text is the identity anchor (merge_or_new by title):
        # restating the same ask on one line folds into the multi-line card
        _write_capture(MULTI)
        actd.process_inbox()
        _write_capture(" ".join(MULTI.split()))
        actd.process_inbox()
        self.assertEqual(len(registry.load_all()), 1)

    def test_expand_prompt_carries_the_lines(self):
        _write_capture(MULTI)
        actd.process_inbox()
        prompt = analyze.build_expand_prompt(_only_card(), config.Config())
        self.assertIn("- 支持 CSV\n- 支持 PDF\n\n附：上周会议提过一次", prompt)
        self.assertIn(f"TITLE: {inbox.capture_title(MULTI)}\n", prompt)

    def test_dispatch_prompt_carries_the_lines_inside_the_fence(self):
        _write_capture(MULTI, mode="run")
        actd.process_inbox()
        req = _only_card()
        cfg = config.Config()
        prompt = dispatch_prompt.render(req, cfg, Path(TMP_HOME), remote=False)
        self.assertIn("- 支持 CSV\n- 支持 PDF\n\n附：上周会议提过一次", prompt)


if __name__ == "__main__":
    unittest.main()
