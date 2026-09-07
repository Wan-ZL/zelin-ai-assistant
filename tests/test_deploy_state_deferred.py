"""§56.3 session gate — the `deferred` status as the readers see it.

scripts/auto-deploy.sh writes ``status=deferred`` + the add-only keys
``deferred_reason`` / ``deferred_sessions`` / ``deferred_since`` when a green
target waits for live background claude sessions (judged in
tests/integration/test_auto_deploy_session_gate.py). Pinned here, with an
injected clock: the three keys are projected (dashboard `deploy_state`,
doctor) while the mirror-private ``roster_unknown_ticks`` is not; the doctor
``auto-deploy`` row is OK for the first six hours (the owner is working, the
update follows by itself) and WARNs「更新已就绪，等待 N 个会话结束已 X 小时」
afterwards — no deferral ever ends a session, so a stuck one must be seen; a
missing/unparseable ``deferred_since`` is never counted as overdue; the
``roster_unknown`` shape (no count) renders too.
"""
import json
import os
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env before act imports

from act.lib import config, deploy_state

SINCE = "2026-09-07T05:40:00Z"
SINCE_EPOCH = deploy_state.parse_iso_utc(SINCE)


def _zh():
    return mock.patch.dict(os.environ, {"AIASSISTANT_UI_LANG": "zh"})


def _en():
    return mock.patch.dict(os.environ, {"AIASSISTANT_UI_LANG": "en"})


class DeferredProjectionTestCase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        self.path = deploy_state.PATH
        self.addCleanup(lambda: self.path.unlink(missing_ok=True))

    def test_deferred_keys_are_projected_and_the_tick_counter_is_not(self):
        self.path.write_text(json.dumps({
            "status": "deferred", "version": "1.0.73", "deferred_reason": "sessions_running",
            "deferred_sessions": "2", "deferred_since": SINCE, "roster_unknown_ticks": "1",
            "reason": "sessions_running", "detail": "deploy of v1.0.74 (abc1234) deferred"}),
            encoding="utf-8")
        got = deploy_state.read(self.path)
        self.assertEqual(got["status"], "deferred")
        self.assertEqual(got["deferred_reason"], "sessions_running")
        self.assertEqual(got["deferred_sessions"], "2")
        self.assertEqual(got["deferred_since"], SINCE)
        self.assertNotIn("roster_unknown_ticks", got, "private bookkeeping stays in the mirror")
        self.assertEqual(deploy_state.DEFERRED, "deferred")
        self.assertNotIn(deploy_state.DEFERRED, deploy_state.HEALTHY,
                         "deferred is its own case in auto_deploy_row, not a healthy status")


class DeferredRowTestCase(unittest.TestCase):
    def _state(self, **over):
        st = {"status": "deferred", "version": "1.0.73", "deferred_reason": "sessions_running",
              "deferred_sessions": "2", "deferred_since": SINCE,
              "detail": "deploy of v1.0.74 (abc1234) deferred: 2 live background claude session(s)"}
        st.update(over)
        return st

    def test_fresh_deferral_is_an_ok_row_that_says_what_it_waits_for(self):
        row = deploy_state.auto_deploy_row(self._state(), now=SINCE_EPOCH + 10 * 60)
        self.assertEqual(row["status"], "ok")
        self.assertIn("deferred (v1.0.73 ready, waiting for 2 live claude session(s) since %s)" % SINCE,
                      row["detail"])
        self.assertIn("deploy of v1.0.74", row["detail"])
        self.assertEqual(row["fix"], "")

    def test_just_under_six_hours_is_still_ok(self):
        row = deploy_state.auto_deploy_row(self._state(),
                                           now=SINCE_EPOCH + deploy_state.DEFER_WARN_AFTER_S - 1)
        self.assertEqual(row["status"], "ok")

    def test_six_hours_of_deferral_warns_bilingually_with_count_and_hours(self):
        now = SINCE_EPOCH + 7 * 3600 + 5 * 60
        with _zh():
            row = deploy_state.auto_deploy_row(self._state(), now=now)
        self.assertEqual(row["status"], "warn")
        self.assertTrue(row["detail"].startswith("更新已就绪，等待 2 个会话结束已 7 小时："), row["detail"])
        self.assertIn("deploy of v1.0.74", row["detail"])
        self.assertIn("§46/#119", row["fix"])
        self.assertIn("--force", row["fix"])
        with _en():
            row = deploy_state.auto_deploy_row(self._state(), now=now)
        self.assertEqual(row["status"], "warn")
        self.assertTrue(row["detail"].startswith("update ready, waiting for 2 session(s) to finish for 7 h:"),
                        row["detail"])
        self.assertIn("claude agents", row["fix"])

    def test_exactly_six_hours_is_the_threshold(self):
        row = deploy_state.auto_deploy_row(self._state(), now=SINCE_EPOCH + deploy_state.DEFER_WARN_AFTER_S)
        self.assertEqual(row["status"], "warn")
        self.assertEqual(deploy_state.DEFER_WARN_AFTER_S, 6 * 3600)

    def test_roster_unknown_shape_renders_without_a_count(self):
        st = self._state(deferred_reason="roster_unknown",
                         detail="deploy of v1.0.74 (abc1234) deferred: the claude roster cannot be read")
        st.pop("deferred_sessions")
        row = deploy_state.auto_deploy_row(st, now=SINCE_EPOCH + 60)
        self.assertEqual(row["status"], "ok")
        self.assertIn("waiting for ? live claude session(s)", row["detail"])
        with _en():
            row = deploy_state.auto_deploy_row(st, now=SINCE_EPOCH + 9 * 3600)
        self.assertEqual(row["status"], "warn")
        self.assertIn("waiting for ? session(s) to finish for 9 h", row["detail"])

    def test_missing_or_unparseable_since_is_never_overdue(self):
        st = self._state()
        st.pop("deferred_since")
        row = deploy_state.auto_deploy_row(st, now=SINCE_EPOCH + 48 * 3600)
        self.assertEqual(row["status"], "ok")
        self.assertNotIn("since", row["detail"])
        row = deploy_state.auto_deploy_row(self._state(deferred_since="yesterday-ish"),
                                           now=SINCE_EPOCH + 48 * 3600)
        self.assertEqual(row["status"], "ok")
        self.assertIsNone(deploy_state.deferred_hours(self._state(deferred_since="x"), SINCE_EPOCH))
        self.assertEqual(deploy_state.deferred_hours(self._state(), SINCE_EPOCH - 5), 0.0,
                         "a clock behind the stamp reads as zero, never negative")

    def test_default_clock_is_the_wall_clock(self):
        # `now` omitted → time.time(); a stamp far in the past is overdue either way
        with _en():
            row = deploy_state.auto_deploy_row(self._state(deferred_since="2026-01-01T00:00:00Z"))
        self.assertEqual(row["status"], "warn")

    def test_other_statuses_are_untouched(self):
        self.assertEqual(deploy_state.auto_deploy_row({"status": "deployed", "version": "1"})["status"], "ok")
        self.assertEqual(deploy_state.auto_deploy_row({"status": "ci_pending", "version": "1"})["status"], "warn")


if __name__ == "__main__":
    unittest.main()
