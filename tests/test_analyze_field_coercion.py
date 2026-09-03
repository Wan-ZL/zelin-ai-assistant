"""act/analyze — field-level sanitising of the expansion reply (§8, 宪法第 11 条:
LLM 输出不可信，逐字段消毒) + the prompt's grounding lines.

Pinned here (P3 mutation net):
- ``_sources_text``: no sources -> "(no sources)"; quote preferred over ref;
  non-dict entries skipped; all-non-dict -> "(no sources)";
- ``build_expand_prompt``: TYPE/NOTES fallbacks, the quick_capture note only
  for a quick_capture source;
- ``_coerce_plan``: list items stringified/blank-dropped, a multi-line string
  splits, a single-line string stays one step, junk -> [];
- ``_coerce_cost``: None/junk -> None, numeric strings parse;
- ``_apply_expansion``: cost applied only when parseable, target_repo only
  when non-blank, target_kind only new|existing (case-folded), illegal
  delivery_mode -> repo, definition_of_done capped at 3 and blanks dropped,
  an empty summary/plan keeps the existing values (title fallback);
- ``_apply_fallback``: the manual-attention tag is appended to existing notes;
- ``expand_debt``: a non-zero exit code is a fallback even when stdout is JSON.
"""
import json
import unittest

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import analyze
from act.lib import config
from act.lib.registry import Requirement, State


class _Proc:
    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.returncode = returncode


def _req(**kw) -> Requirement:
    kw.setdefault("id", "R-901")
    kw.setdefault("title", "调研 sidecut 数据")
    kw.setdefault("status", State.RAISING.value)
    return Requirement(**kw)


class SourcesTextTestCase(unittest.TestCase):
    def test_no_sources(self):
        self.assertEqual(analyze._sources_text(None), "(no sources)")
        self.assertEqual(analyze._sources_text([]), "(no sources)")

    def test_quote_preferred_then_ref(self):
        out = analyze._sources_text([
            {"channel": "slack", "date": "2026-07-01", "quote": "do X", "ref": "r1"},
            {"channel": "gmail", "date": "2026-07-02", "ref": "<mid>"},
            {"channel": "meeting"},
        ])
        self.assertEqual(out.splitlines(), [
            "  - [slack 2026-07-01] do X",
            "  - [gmail 2026-07-02] <mid>",
            "  - [meeting ?] ",
        ])

    def test_non_dict_entries_skipped_and_all_junk_is_no_sources(self):
        out = analyze._sources_text(["junk", {"channel": "c", "date": "d", "quote": "q"}])
        self.assertEqual(out, "  - [c d] q")
        self.assertEqual(analyze._sources_text(["junk", 3]), "(no sources)")


class PromptTestCase(unittest.TestCase):
    def test_type_and_notes_fallbacks(self):
        prompt = analyze.build_expand_prompt(_req(type=None, notes=None), config.Config())
        self.assertIn("TYPE: unspecified\n", prompt)
        self.assertIn("NOTES: (none)\n", prompt)
        self.assertNotIn("channel=quick_capture", prompt)

    def test_quick_capture_source_adds_the_context_note(self):
        req = _req(type="code", notes="n",
                   sources=[{"channel": "quick_capture", "quote": "一句话"}])
        prompt = analyze.build_expand_prompt(req, config.Config())
        self.assertIn("TYPE: code\n", prompt)
        self.assertIn("channel=quick_capture 的来源", prompt)
        self.assertTrue(analyze._has_quick_capture_source(req.sources))
        self.assertFalse(analyze._has_quick_capture_source(
            [{"channel": "slack"}, "junk"]))
        self.assertFalse(analyze._has_quick_capture_source(None))


class CoercionTestCase(unittest.TestCase):
    def test_coerce_plan(self):
        self.assertEqual(analyze._coerce_plan(["a", 2, "  ", None]), ["a", "2", "None"])
        self.assertEqual(analyze._coerce_plan("one\n\n two \n"), ["one", "two"])
        self.assertEqual(analyze._coerce_plan("  single step  "), ["single step"])
        self.assertEqual(analyze._coerce_plan("   "), [])
        self.assertEqual(analyze._coerce_plan(None), [])
        self.assertEqual(analyze._coerce_plan(42), [])

    def test_coerce_cost(self):
        self.assertIsNone(analyze._coerce_cost(None))
        self.assertIsNone(analyze._coerce_cost("cheap"))
        self.assertEqual(analyze._coerce_cost("2.5"), 2.5)
        self.assertEqual(analyze._coerce_cost(3), 3.0)


class ApplyExpansionTestCase(unittest.TestCase):
    def test_each_field_rule(self):
        req = _req(summary="", plan=[], notes="n")
        analyze._apply_expansion(req, {
            "summary": "  说人话  ",
            "plan": "step 1\nstep 2",
            "cost_estimate_usd": "1.25",
            "target_repo": "  ~/Projects/x  ",
            "target_kind": " Existing ",
            "delivery_mode": "CHAT",
            "definition_of_done": [" a ", "", "b", "c", "d"],
        })
        self.assertEqual(req.summary, "说人话")
        self.assertEqual(req.plan, ["step 1", "step 2"])
        self.assertEqual(req.cost_estimate_usd, 1.25)
        self.assertEqual(req.target_repo, "~/Projects/x")
        self.assertEqual(req.target_kind, "existing")
        self.assertEqual(req.delivery_mode, "chat")
        self.assertEqual(req.definition_of_done, ["a", "b", "c"])

    def test_junk_fields_leave_defaults(self):
        req = _req(summary="", plan=[], target_repo="~/keep", target_kind="new",
                   cost_estimate_usd=7.0)
        analyze._apply_expansion(req, {
            "summary": "", "plan": [], "cost_estimate_usd": "n/a",
            "target_repo": "   ", "target_kind": "sideways",
            "delivery_mode": "carrier-pigeon", "definition_of_done": ["", "  "],
        })
        self.assertEqual(req.summary, req.title)        # empty -> title fallback
        self.assertEqual(req.plan, [req.title])
        self.assertEqual(req.cost_estimate_usd, 7.0)    # unparseable -> untouched
        self.assertEqual(req.target_repo, "~/keep")
        self.assertEqual(req.target_kind, "new")
        self.assertEqual(req.delivery_mode, "repo")     # illegal -> repo
        self.assertIsNone(req.definition_of_done)       # all blank -> untouched

    def test_existing_summary_and_plan_survive_an_empty_reply(self):
        req = _req(summary="keep me", plan=["keep"])
        analyze._apply_expansion(req, {"summary": None, "plan": None})
        self.assertEqual(req.summary, "keep me")
        self.assertEqual(req.plan, ["keep"])


class FallbackTestCase(unittest.TestCase):
    def test_fallback_appends_tag_to_existing_notes(self):
        req = _req(summary="", plan=[], notes="old note")
        analyze._apply_fallback(req)
        self.assertEqual(req.notes, "old note (auto-expand failed, needs manual)")
        self.assertEqual(req.summary, req.title)
        self.assertEqual(req.plan, [req.title])
        req2 = _req(notes=None)
        analyze._apply_fallback(req2)
        self.assertEqual(req2.notes, "(auto-expand failed, needs manual)")

    def test_non_zero_exit_is_a_fallback_even_with_json_stdout(self):
        config.ensure_state_dirs()
        req = _req(id="R-902")
        payload = json.dumps({"summary": "should not apply", "plan": ["x"]})
        out = analyze.expand_debt(
            req, cfg=config.Config(),
            runner=lambda p: _Proc(stdout=payload, returncode=2))
        self.assertEqual(str(out.status), State.CARD_SENT.value)
        self.assertIn("auto-expand failed", out.notes)
        self.assertEqual(out.summary, req.title)

    def test_runner_exception_is_a_fallback(self):
        config.ensure_state_dirs()

        def boom(prompt):
            raise OSError("claude missing")

        out = analyze.expand_debt(_req(id="R-903"), cfg=config.Config(), runner=boom)
        self.assertIn("auto-expand failed", out.notes)
        self.assertEqual(str(out.status), State.CARD_SENT.value)


if __name__ == "__main__":
    unittest.main()
