"""secrets._first_token_line — the one-token-per-line contract's edges (§19).

Pins: blank/whitespace-only text → None, surrounding whitespace stripped, the
multi-line warning names origin + count without the token value, fires once
per origin, and a failing stderr never breaks resolution.
"""
import io
import unittest
from unittest import mock

from act.lib import secrets


class FirstTokenLineTestCase(unittest.TestCase):
    def setUp(self):
        secrets._warned_multiline.clear()

    def test_blank_text_is_none(self):
        self.assertIsNone(secrets._first_token_line("", "o"))
        self.assertIsNone(secrets._first_token_line("  \n\t\n", "o"))

    def test_single_line_is_stripped_without_warning(self):
        err = io.StringIO()
        with mock.patch("sys.stderr", err):
            self.assertEqual(secrets._first_token_line("  tok-1  \n", "origin-a"), "tok-1")
        self.assertEqual(err.getvalue(), "")
        self.assertNotIn("origin-a", secrets._warned_multiline)

    def test_multiline_warns_with_origin_and_count_only(self):
        err = io.StringIO()
        with mock.patch("sys.stderr", err):
            self.assertEqual(secrets._first_token_line("\nsecret-value\n# note\n\nmore", "/p/x"),
                             "secret-value")
        text = err.getvalue()
        self.assertIn("/p/x has 3 non-empty lines", text)
        self.assertNotIn("secret-value", text)
        self.assertIn("/p/x", secrets._warned_multiline)

    def test_warning_once_per_origin(self):
        err = io.StringIO()
        with mock.patch("sys.stderr", err):
            secrets._first_token_line("a\nb", "same")
            secrets._first_token_line("c\nd", "same")
            secrets._first_token_line("e\nf", "other")
        self.assertEqual(err.getvalue().count("WARNING"), 2)

    def test_broken_stderr_never_raises(self):
        class Boom(io.StringIO):
            def write(self, *_a):
                raise OSError("closed")

        with mock.patch("sys.stderr", Boom()):
            self.assertEqual(secrets._first_token_line("x\ny", "z"), "x")
        self.assertIn("z", secrets._warned_multiline)


if __name__ == "__main__":
    unittest.main()
