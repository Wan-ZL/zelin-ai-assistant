#!/usr/bin/env python3
"""Tests for style_check.py. Run:  python3 test_style_check.py  (stdlib only)."""
import os
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "style_check.py")
sys.path.insert(0, HERE)
import style_check as sc  # noqa: E402


def rules(text, latex=False):
    return [f[3] for f in sc.check_text(text, latex=latex)]


def find(text, rule, latex=False):
    return [f for f in sc.check_text(text, latex=latex) if f[3] == rule]


def run_cli(args, stdin=None):
    p = subprocess.run([sys.executable, SCRIPT] + args, input=stdin,
                       capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


class TestContractions(unittest.TestCase):
    def test_full_paradigm(self):
        for w in ["can't", "won't", "don't", "weren't", "ain't", "shan't", "he'll",
                  "she'll", "it'll", "that'll", "who's", "here's", "y'all", "must've",
                  "could've", "who'd", "it's", "let's", "I'm", "they're", "how's"]:
            self.assertIn("contraction", rules("Well %s be fine." % w), w)

    def test_unicode_apostrophe(self):
        self.assertIn("contraction", rules("It\u2019s fine."))

    def test_possessive_not_flagged(self):
        self.assertNotIn("contraction", rules("Singh's rule applies. The model's score rose."))


class TestSmallNumeral(unittest.TestCase):
    def test_catches_0_and_1(self):
        self.assertEqual(len(find("We saw 0 failures and 1 success there.", "small-numeral")), 2)

    def test_digits_before_punctuation(self):
        hits = find("We compared models 3, 4, and 5.", "small-numeral")
        self.assertEqual(len(hits), 3)

    def test_runs_in_latex_mode(self):
        self.assertIn("small-numeral", rules("We ran 3 models here.", latex=True))

    def test_identifiers_and_metrics_not_flagged(self):
        for txt in ["pass@10 is the metric.", "the w05 world", "a 3.2% gain",
                    "GPT-4 and v2 shipped.", "score of 3.5 held"]:
            self.assertNotIn("small-numeral", rules(txt), txt)

    def test_figure_table_section_pages_eq_exempt(self):
        for txt in ["See Figure 3 now.", "See Table 2 now.", "in Section 4 only",
                    "The proof spans pages 3 and 4 here.", "by Eq. 2 above", "See Fig. 7 too."]:
            self.assertNotIn("small-numeral", rules(txt), txt)

    def test_endash_ranges_exempt(self):
        self.assertNotIn("small-numeral", rules("chains of length 5\u20139 held"))
        self.assertNotIn("small-numeral", rules("lengths 5--9 held", latex=True))


class TestUrls(unittest.TestCase):
    def test_suppression_is_span_only(self):
        out = rules("See https://a.com/x/y for the pass/fail bit.")
        self.assertIn("slash", out)  # pass/fail still caught
        self.assertEqual(len(find("See https://a.com/x/y for the pass/fail bit.", "slash")), 1)

    def test_bare_blog_word_is_not_a_url(self):
        # "blog." at sentence end must not shield the rest of the line
        self.assertIn("slash-banned", rules("We posted it w/ notes on the blog."))

    def test_domain_still_shields_itself(self):
        self.assertNotIn("slash", rules("Code lives at github.com/postmanlabs/APIFlow-Bench today."))


class TestDates(unittest.TestCase):
    def test_slash_date(self):
        hits = find("Due on 8/17 sharp.", "numeric-date")
        self.assertEqual(len(hits), 1)
        self.assertIn("August", hits[0][4])

    def test_fraction_message(self):
        hits = find("About 1/3 of runs failed.", "numeric-date")
        self.assertEqual(len(hits), 1)
        self.assertIn("one-third", hits[0][4])

    def test_iso_date(self):
        self.assertTrue(find("The 2026-07-22 release shipped.", "numeric-date"))

    def test_no_double_report_with_slash(self):
        out = sc.check_text("Due on 8/17 sharp.")
        spans = [(f[1], f[2]) for f in out]
        self.assertEqual(len(spans), len(set(spans)))
        self.assertNotIn("slash", [f[3] for f in out])

    def test_year_range_not_a_date(self):
        self.assertNotIn("numeric-date", rules("during 2019--2021 the bank grew", latex=True))

    def test_sentence_final_slash_date(self):
        # regression: '8/17.' at sentence end is still a numeric date, not [slash]
        t = "The deadline was 8/17."
        hits = find(t, "numeric-date")
        self.assertEqual(len(hits), 1)
        self.assertIn("August", hits[0][4])
        self.assertNotIn("slash", rules(t))
        self.assertNotIn("numeric-date", rules("The odds shifted 8/17.5 times."))


class TestSlash(unittest.TestCase):
    def test_allowlisted_terms_warn(self):
        for t in ["I/O", "A/B", "N/A", "TCP/IP", "tokens/s"]:
            hits = find("We measured %s here." % t, "slash-term")
            self.assertEqual(len(hits), 1, t)
            self.assertEqual(hits[0][0], "WARN", t)

    def test_banned_slashes_stay_error(self):
        for t in ["and/or", "w/ milk", "24/7"]:
            hits = find("It works %s here." % t, "slash-banned")
            self.assertEqual(hits[0][0], "ERROR", t)

    def test_generic_slash_error(self):
        hits = find("A single pass/fail bit is coarse.", "slash")
        self.assertEqual(hits[0][0], "ERROR")

    def test_sentence_final_terms_stay_warn(self):
        # regression: a trailing '.' is not part of the token
        for txt, snip in [("The bottleneck is I/O.", "I/O"),
                          ("We measured 50 tokens/s.", "tokens/s"),
                          ("We ran an A/B test on I/O throughput at 50 tokens/s.", None)]:
            self.assertNotIn("slash", rules(txt), txt)
            hits = find(txt, "slash-term")
            self.assertTrue(hits, txt)
            for h in hits:
                self.assertEqual(h[0], "WARN", txt)
            if snip:
                self.assertEqual(hits[0][5], snip, txt)


class TestPassive(unittest.TestCase):
    def test_participles(self):
        for t in ["was shown", "is sent", "was kept", "were chosen", "is held",
                  "was thought", "is meant", "was brought", "is taught", "was told",
                  "is paid", "was built", "is set", "was led"]:
            self.assertIn("passive", rules("The result %s to us." % t), t)

    def test_adverb_between(self):
        self.assertIn("passive", rules("Reliability is systematically overestimated by it."))

    def test_ed_lookalikes_not_flagged(self):
        for t in ["The claim is indeed correct.", "The car is red.",
                  "The scale was unprecedented.", "The report is detailed.",
                  "The site is sacred ground."]:
            self.assertNotIn("passive", rules(t), t)


class TestSerialComma(unittest.TestCase):
    def test_single_word_items(self):
        self.assertIn("serial-comma", rules("We ate apples, oranges and pears."))

    def test_multi_word_items(self):
        self.assertIn("serial-comma",
                      rules("It ships a frozen bank, a public leaderboard and a replay harness."))

    def test_discourse_adverbs_not_flagged(self):
        for t in ["However, apples and pears differ.",
                  "Moreover, models and humans agree.",
                  "Finally, cost and latency matter.",
                  "First, primes and evens differ.",
                  "In contrast, models and humans diverge."]:
            self.assertNotIn("serial-comma", rules(t), t)

    def test_serial_comma_present_not_flagged(self):
        self.assertNotIn("serial-comma", rules("We ate apples, oranges, and pears."))


class TestPronounStart(unittest.TestCase):
    def test_broadened(self):
        for t in ["This is bad.", "It seems fine.", "These are new.",
                  "Those depend on the seed.", "This results in drift.", "It failed early."]:
            hits = find(t, "pronoun-start")
            self.assertTrue(hits, t)
            self.assertEqual(hits[0][0], "WARN", t)

    def test_word_sets_have_no_merged_tokens(self):
        # guards the space-separated set literals against concatenation typos
        import re as _re
        for name in ("AUX_VERBS", "DISCOURSE_LAST", "SUBORD", "ED_STOP"):
            for w in getattr(sc, name):
                self.assertTrue(_re.fullmatch(r"[a-z']+", w), "%s: %r" % (name, w))
        self.assertTrue(find("This becomes clear.", "pronoun-start"))

    def test_pronoun_plus_noun_not_flagged(self):
        for t in ["This paper shows the gap.", "These models fail badly."]:
            self.assertNotIn("pronoun-start", rules(t), t)


class TestSentenceJoining(unittest.TestCase):
    def test_hard_wrap_numeral_not_flagged(self):
        text = "The safety filter declined\n87.8 of its runs there."
        self.assertNotIn("sent-start-numeral", rules(text))

    def test_real_sentence_initial_numeral_warns(self):
        text = "It blocks publication outright.\n465 of the 467 tasks pass."
        hits = find(text, "sent-start-numeral")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0][0], "WARN")
        self.assertEqual(hits[0][1], 2)  # original line number

    def test_emdash_across_join(self):
        self.assertTrue(find("clean chains ---\n60.9 including flagged cells", "em-dash-spaced"))

    def test_abbreviations_guarded(self):
        for t in ["See Fig. 3 for details on it.", "Singh et al. showed the result.",
                  "It holds vs. the baseline.", "The rate, i.e. the mean, rose.",
                  "Use tools, e.g. curl, daily."]:
            self.assertNotIn("lowercase-start", rules(t), t)

    def test_question_inside_quote_not_a_boundary(self):
        t = 'Checkpoints ("did it back off?") are diagnostics, never gates.'
        self.assertNotIn("lowercase-start", rules(t))


class TestNewChecks(unittest.TestCase):
    def test_lowercase_sentence_start(self):
        hits = find("The test ran. gpt-5.5 leads the board.", "lowercase-start")
        self.assertEqual(hits[0][0], "ERROR")

    def test_latex_macro_start_not_flagged(self):
        self.assertNotIn("lowercase-start",
                         rules("The run ended. \\emph{Good} results followed.", latex=True))

    def test_spaced_emdash_tex_and_plain(self):
        self.assertTrue(find("state --- and the rest", "em-dash-spaced", latex=True))
        self.assertTrue(find("state \u2014 and the rest", "em-dash-spaced"))
        self.assertEqual(find("state --- and the rest", "em-dash-spaced")[0][0], "ERROR")

    def test_unspaced_emdash_ok_and_hrule_ok(self):
        self.assertFalse(find("state---and the rest", "em-dash-spaced"))
        self.assertFalse(find("a ----------- b", "em-dash-spaced"))

    def test_emdash_overuse_warn(self):
        t = "a --- b --- c --- d"
        hits = find(t, "em-dash-many", latex=True)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0][0], "WARN")
        self.assertFalse(find("a --- b", "em-dash-many", latex=True))

    def test_comma_splice(self):
        self.assertTrue(find("We ran the test, it failed.", "comma-splice"))
        self.assertTrue(find("The gate passed, Yiyang is happy.", "comma-splice"))
        self.assertNotIn("comma-splice", rules("However, it is broken."))
        self.assertNotIn("comma-splice", rules("If the check holds, it follows."))
        self.assertNotIn("comma-splice", rules("In our tests, we found a gap."))

    def test_the_figure_family(self):
        for t in ["the Figure 3", "the Table 2", "the Section 4"]:
            self.assertIn("the-figure", rules("See %s now." % t), t)
        self.assertIn("the-figure",
                      rules("See the Figure~\\ref{fig:x} now.", latex=True))


class TestLatexStripping(unittest.TestCase):
    def test_paths_in_macros_not_flagged(self):
        for t in ["\\includegraphics[width=\\linewidth]{figs/fig_spread.pdf}",
                  "Run \\texttt{scripts/golden_replay.py} locally.",
                  "See \\url{https://x.com/a/b} for code.",
                  "Or \\path{a/b/c} instead.",
                  "Use \\verb|a/b| here.",
                  "In \\input{sections/intro} we show it.",
                  "As \\href{https://a.com/x/y}{the site} says."]:
            out = rules(t, latex=True)
            self.assertNotIn("slash", out, t)
            self.assertNotIn("slash-term", out, t)

    def test_math_stripped(self):
        self.assertNotIn("slash", rules("The rate $a/b$ rose.", latex=True))
        self.assertNotIn("sent-start-numeral",
                         rules("counts ($11+5+14=239$ segments $+2 =\n241$ solos).", latex=True))

    def test_display_math_and_linebreak_spacing(self):
        # \[...\] stripped; \\[2pt] is a line break, not an opener
        t = "First row \\\\[2pt]\nA pass/fail bit stays visible.\n\\[ x = a/b \\]\nAnd don't stop."
        out = rules(t, latex=True)
        self.assertIn("slash", out)
        self.assertIn("contraction", out)
        self.assertEqual(len(find(t, "slash", latex=True)), 1)

    def test_comments_stripped(self):
        self.assertFalse(rules("% don't use w/ 8/17 --- ever", latex=True))
        self.assertNotIn("contraction", rules("Fine text. % don't", latex=True))
        # escaped \% is not a comment start
        self.assertIn("contraction", rules("A 50\\% share don't lie.", latex=True))

    def test_latex_checks_on_unstripped_text(self):
        self.assertIn("straight-quote", rules('a "quoted" word', latex=True))
        self.assertNotIn("straight-quote", rules('Schr\\"odinger and ``ok\'\' fine', latex=True))
        self.assertIn("footnote", rules("Text\\footnote{no}.", latex=True))
        self.assertIn("hardcoded-ref", rules("See Figure 3 here.", latex=True))
        self.assertNotIn("straight-quote", rules('a "quote"'))  # plain mode: no LaTeX rules


class TestMechanics(unittest.TestCase):
    def test_dedup_same_position(self):
        out = sc.check_text("It ended late.\n3 items failed.")
        at = [f for f in out if (f[1], f[2]) == (2, 0)]
        self.assertEqual(len(at), 1)
        self.assertEqual(at[0][3], "sent-start-numeral")

    def test_suppression_line_and_sentence(self):
        self.assertFalse(find("A pass/fail bit. lint-ok: slash", "slash"))
        self.assertFalse(find("A pass/fail bit. % lint-ok: slash", "slash", latex=True))
        # other rules still fire on that line
        self.assertIn("contraction", rules("We don't use pass/fail lint-ok: slash"))
        # suppression works when the match sits on a joined continuation line
        self.assertFalse(find("A long sentence with a\npass/fail bit. lint-ok: slash", "slash"))

    def test_cjk_lines_skipped(self):
        self.assertFalse(rules("\u6211\u4eec\u7528\u4e86 pass/fail \u548c don't"))
        out = rules("\u8fd9\u884c\u8df3\u8fc7 don't\nBut this don't line counts.")
        self.assertEqual(out.count("contraction"), 1)

    def test_severity_policy(self):
        self.assertEqual(find("It rose significantly there.", "amplifier")[0][0], "WARN")
        self.assertEqual(find("Start here.\n7 runs failed.", "sent-start-numeral")[0][0], "WARN")
        self.assertEqual(find("We saw 3 runs pass.", "small-numeral")[0][0], "WARN")

    def test_unicode_and_empty(self):
        self.assertEqual(sc.check_text(""), [])
        sc.check_text("Caf\u00e9 \u201cquotes\u201d \u2014dash\u2014 fine. \U0001f600")

    def test_perf_2mb(self):
        para = ("The harness replays every world forward and checks the answer card. "
                "It records latency, cost and the verdict for the run.\n") * 4 + "\n"
        big = para * (2_000_000 // len(para))
        t0 = time.monotonic()
        sc.check_text(big, latex=True)
        self.assertLess(time.monotonic() - t0, 3.0)


class TestCli(unittest.TestCase):
    def _tmp(self, content, suffix=".txt"):
        fd, path = tempfile.mkstemp(suffix=suffix, dir=self.dir.name)
        with os.fdopen(fd, "w") as fh:
            fh.write(content)
        return path

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.dir.cleanup()

    def test_exit_codes(self):
        clean = self._tmp("A fine sentence stands alone.\n")
        warn = self._tmp("The result was shown to us.\n")
        err = self._tmp("We don't stop.\n")
        self.assertEqual(run_cli([clean])[0], 0)
        self.assertEqual(run_cli([warn])[0], 1)
        self.assertEqual(run_cli([err])[0], 2)
        code, out = run_cli(["/nonexistent/x.txt", clean])
        self.assertEqual(code, 3)
        self.assertIn("cannot read", out)
        self.assertIn("clean", out)  # continued past the bad file
        self.assertEqual(run_cli([self.dir.name])[0], 3)  # directory

    def test_stdin(self):
        code, out = run_cli(["-"], stdin="We don't stop.\n")
        self.assertEqual(code, 2)
        self.assertIn("contraction", out)
        code, out = run_cli(["--latex", "-"], stdin='a "quote" here\n')
        self.assertIn("straight-quote", out)

    def test_basename_only(self):
        path = self._tmp("We don't stop.\n")
        code, out = run_cli([path])
        self.assertNotIn(self.dir.name, out)
        self.assertIn(os.path.basename(path), out)

    def test_latex_mode_resets_per_file(self):
        tex = self._tmp('a "quote" in tex\n', suffix=".tex")
        txt = self._tmp('a "quote" in txt\n')
        code, out = run_cli([tex, txt])
        tex_lines = [ln for ln in out.splitlines() if os.path.basename(tex) in ln]
        txt_lines = [ln for ln in out.splitlines() if os.path.basename(txt) in ln]
        self.assertTrue(any("straight-quote" in ln for ln in tex_lines))
        self.assertFalse(any("straight-quote" in ln for ln in txt_lines))

    def test_output_format(self):
        path = self._tmp("We don't stop.\n")
        code, out = run_cli([path])
        base = os.path.basename(path)
        self.assertRegex(out, r"ERROR %s:1:\d+  \[contraction\]" % base)
        self.assertIn("error(s)", out)



class TestP2Regressions(unittest.TestCase):
    def test_comma_splice_irregular_past_pre_clause(self):
        self.assertIn("comma-splice", rules("I met Yiyang at an event in 2024, Yiyang is a PhD candidate."))

    def test_elliptical_pair_not_serial_comma(self):
        self.assertNotIn("serial-comma", rules("Results cover 8% of chain trials, reported with and without throughout."))


if __name__ == "__main__":
    unittest.main(verbosity=2)
