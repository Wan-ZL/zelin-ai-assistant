"""§56.3 session gate — what a `deferred` row must NOT hide (review of #284).

Three overrides on the OK「deferred on vX (waiting …)」row of
tests/test_deploy_state_deferred.py, each pinned with an injected clock:

  - a `last_incident` still on file (a rollback verdict no later `deployed`
    cleared) WARNs under `deferred` exactly as it does under a healthy status
    (#135 rule) — before the fix the deferred branch returned first and the
    verdict vanished from the doctor for as long as the episode lasted;
  - a deferred REPAIR — the install.sh re-run of §56.3 step 2 — keeps its
    install_incomplete tokens in `reason` (`heartbeat_missing sessions_running`)
    and renders WARN from the first run, whatever the age: the machine is not
    running its checkout, and the row it replaced was a WARN;
  - the WARN fix names what actually ends a counted session (a done worker
    exits when claude retires it, when the owner 验收/打回 its card, or on
    `claude stop <id>`; a blocked one is §46/#119's harvest) — not「a stuck
    session」nobody can find on the board.
"""
import os
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env before act imports

from act.lib import deploy_state

SINCE = "2026-09-07T05:40:00Z"
SINCE_EPOCH = deploy_state.parse_iso_utc(SINCE)
INCIDENT = "2026-09-07T04:10:00Z rollback_failed: rollback refused (store2 became the registry truth)"


def _zh():
    return mock.patch.dict(os.environ, {"AIASSISTANT_UI_LANG": "zh"})


def _en():
    return mock.patch.dict(os.environ, {"AIASSISTANT_UI_LANG": "en"})


def _deferred(**over):
    st = {"status": "deferred", "version": "1.0.73", "reason": "sessions_running",
          "deferred_reason": "sessions_running", "deferred_sessions": "2", "deferred_since": SINCE,
          "detail": "deploy of v1.0.74 (abc1234) deferred: 2 live background claude session(s)"}
    st.update(over)
    return st


class DeferredIncidentTestCase(unittest.TestCase):
    def test_incident_under_a_fresh_deferral_warns_like_the_healthy_path(self):
        with _en():
            healthy = deploy_state.auto_deploy_row({"status": "up_to_date", "version": "1.0.73",
                                                    "last_incident": INCIDENT})
            row = deploy_state.auto_deploy_row(_deferred(last_incident=INCIDENT), now=SINCE_EPOCH + 600)
        self.assertEqual(healthy["status"], "warn", "the #135 rule this mirrors")
        self.assertEqual(row["status"], "warn")
        self.assertIn("deferred on v1.0.73", row["detail"])
        self.assertIn("unresolved deploy incident: " + INCIDENT, row["detail"])
        self.assertEqual(row["fix"], healthy["fix"], "same verdict, same fix text")

    def test_incident_is_appended_when_the_row_already_warns(self):
        with _en():
            overdue = deploy_state.auto_deploy_row(_deferred(last_incident=INCIDENT),
                                                   now=SINCE_EPOCH + 7 * 3600)
            repair = deploy_state.auto_deploy_row(
                _deferred(reason="heartbeat_missing sessions_running", last_incident=INCIDENT),
                now=SINCE_EPOCH + 60)
        for row in (overdue, repair):
            self.assertEqual(row["status"], "warn")
            self.assertIn("unresolved deploy incident: " + INCIDENT, row["detail"], row)
        self.assertTrue(overdue["detail"].startswith("update ready, waiting for 2 session(s) to finish for 7 h"))
        self.assertTrue(repair["detail"].startswith("install incomplete (heartbeat_missing)"))

    def test_without_an_incident_the_fresh_row_is_still_ok(self):
        row = deploy_state.auto_deploy_row(_deferred(), now=SINCE_EPOCH + 600)
        self.assertEqual(row["status"], "ok")
        self.assertNotIn("incident", row["detail"])


class DeferredRepairTestCase(unittest.TestCase):
    def test_deferred_repair_warns_from_the_first_run_and_keeps_the_mismatch_visible(self):
        st = _deferred(reason="heartbeat_missing install_report_version_mismatch sessions_running",
                       deferred_sessions="1",
                       detail="install.sh re-run of v1.0.73 (abc1234) deferred: 1 live background claude "
                              "session(s) on the roster; install incomplete: state/actd.heartbeat missing")
        self.assertEqual(deploy_state.deferred_repair_tokens(st),
                         "heartbeat_missing install_report_version_mismatch")
        with _zh():
            row = deploy_state.auto_deploy_row(st, now=SINCE_EPOCH + 60)
        self.assertEqual(row["status"], "warn", "one minute in — a deferred repair is never plain OK")
        self.assertTrue(row["detail"].startswith(
            "安装未完成（heartbeat_missing install_report_version_mismatch），修补等待 1 个会话结束："), row["detail"])
        self.assertIn("state/actd.heartbeat missing", row["detail"])
        self.assertIn("actd 没在跑时没有任何自动机制结束它们", row["fix"])
        self.assertIn("--force", row["fix"])
        with _en():
            row = deploy_state.auto_deploy_row(st, now=SINCE_EPOCH + 60)
        self.assertTrue(row["detail"].startswith(
            "install incomplete (heartbeat_missing install_report_version_mismatch); the repair waits for "
            "1 session(s) to finish:"), row["detail"])
        self.assertIn("with actd down nothing automatic ends them", row["fix"])

    def test_roster_unknown_repair_is_a_warn_too(self):
        st = _deferred(reason="heartbeat_stale roster_unknown", deferred_reason="roster_unknown")
        st.pop("deferred_sessions")
        self.assertEqual(deploy_state.deferred_repair_tokens(st), "heartbeat_stale")
        with _en():
            row = deploy_state.auto_deploy_row(st, now=SINCE_EPOCH + 60)
        self.assertEqual(row["status"], "warn")
        self.assertIn("install incomplete (heartbeat_stale); the repair waits for ? session(s)", row["detail"])

    def test_a_plain_deploy_deferral_carries_no_repair_tokens(self):
        for reason in ("sessions_running", "roster_unknown", ""):
            st = _deferred(reason=reason)
            self.assertEqual(deploy_state.deferred_repair_tokens(st), "", reason)
            self.assertEqual(deploy_state.auto_deploy_row(st, now=SINCE_EPOCH + 60)["status"], "ok", reason)


class DeferredFixTextTestCase(unittest.TestCase):
    def test_the_six_hour_warn_names_the_real_exits_not_a_stuck_session(self):
        with _zh():
            row = deploy_state.auto_deploy_row(_deferred(), now=SINCE_EPOCH + 7 * 3600)
        self.assertEqual(row["status"], "warn")
        for token in ("验收/打回", "claude stop <id>", "§46/#119", "--force", "claude agents"):
            self.assertIn(token, row["fix"], token)
        self.assertNotIn("卡住的会话", row["fix"], "a done worker awaiting review is not stuck")
        with _en():
            row = deploy_state.auto_deploy_row(_deferred(), now=SINCE_EPOCH + 7 * 3600)
        for token in ("accept/reject", "claude stop <id>", "retires it", "--force"):
            self.assertIn(token, row["fix"], token)


if __name__ == "__main__":
    unittest.main()
