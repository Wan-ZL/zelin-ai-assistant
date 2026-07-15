"""Audit fix — act/lib/notify.py detect_auth_failure precision.

The classifier used to match generic words (auth / login / credentials) over
the WHOLE dispatch launch log, whose fixed header embeds the target path
(``# cwd=<target>``). A card targeting ~/Projects/auth-service produced a
fabricated "需要重新登录" notification right after a successful dispatch.

Now: the launch-log header lines are stripped before classifying, and only
high-precision credential-failure signatures (aligned with
act/lib/failures.py claude_auth_failed) trigger.
"""
import unittest

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act.* import

from act.lib import notify


class DetectAuthFailureTestCase(unittest.TestCase):
    def test_auth_named_cwd_header_is_not_a_credential_failure(self):
        for repo in ("auth-service", "login-page", "my-credentials-vault"):
            log = (f"# dispatch R-7 @ 2026-07-15T09:00:00\n"
                   f"# cwd=/Users/zelin/Projects/{repo}\n\n"
                   "=== STDOUT ===\nStarted session abc123\n\n"
                   "=== STDERR ===\n")
            self.assertFalse(notify.detect_auth_failure(log),
                             f"repo name {repo!r} must not read as auth failure")

    def test_generic_words_alone_no_longer_flag(self):
        for text in ("updated the login page copy",
                     "refactored the credentials helper module",
                     "auth flow diagram committed",
                     "shipping the 401k-Rollover-Website landing page"):
            self.assertFalse(notify.detect_auth_failure(text), text)

    def test_high_precision_signatures_still_flag(self):
        for text in ('API Error: 401 {"type":"authentication_error"}',
                     "invalid x-api-key",
                     "OAuth token has expired. Please run /login.",
                     "error: session expired — please sign in",
                     "response: Unauthorized",
                     "api key is invalid or revoked"):
            self.assertTrue(notify.detect_auth_failure(text), text)

    def test_real_failure_in_body_flags_despite_auth_named_cwd(self):
        log = ("# dispatch R-7 @ 2026-07-15T09:00:00\n"
               "# cwd=/Users/zelin/Projects/auth-service\n\n"
               "=== STDOUT ===\n\n=== STDERR ===\n"
               "authentication_error: invalid api key\n")
        self.assertTrue(notify.detect_auth_failure(log))

    def test_empty_text_is_false(self):
        self.assertFalse(notify.detect_auth_failure(""))


if __name__ == "__main__":
    unittest.main()
