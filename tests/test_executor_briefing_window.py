"""§39.2 for briefings — executor._briefing_window_open's fresh roster read.

The window is CLOSED only for a session that is actively working with a live
process (pid present, state outside the blocked set); absent / dead / blocked
sessions and a roster that cannot be read are all open windows (the same
best-effort posture as stop_session: nothing to interrupt). The roster is
read through the dashboard's ``_run_claude_agents`` / ``_index_agents`` pair,
patched here — never a real ``claude agents``.
"""
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import executor
from act.lib import dashboard
from act.lib.agent_states import _BLOCKED_STATES

SID = "d1a1beef-0000-4000-8000-000000000001"


def _agent(state, pid=None):
    a = {"id": "d1a1beef", "sessionId": SID, "state": state, "cwd": "/tmp/wt"}
    if pid is not None:
        a["pid"] = pid
    return a


class BriefingWindowTestCase(unittest.TestCase):
    def _open(self, agents):
        with mock.patch.object(dashboard, "_run_claude_agents", return_value=agents):
            return executor._briefing_window_open(SID)

    def test_absent_session_is_open(self):
        self.assertIs(self._open([]), True)
        self.assertIs(self._open([_agent("working", pid=7) | {"id": "other001",
                                                             "sessionId": "other001-x"}]),
                      True)

    def test_working_with_live_pid_is_closed(self):
        self.assertIs(self._open([_agent("working", pid=41)]), False)
        self.assertIs(self._open([_agent("WORKING", pid=41)]), False)   # normalised lower

    def test_blocked_states_are_open_even_with_a_pid(self):
        for state in sorted(_BLOCKED_STATES):
            with self.subTest(state=state):
                self.assertIs(self._open([_agent(state, pid=41)]), True)

    def test_dead_process_is_open_whatever_the_state(self):
        self.assertIs(self._open([_agent("working")]), True)
        self.assertIs(self._open([_agent("working", pid=0)]), True)

    def test_short_id_lookup_matches_the_roster_index(self):
        with mock.patch.object(dashboard, "_run_claude_agents",
                               return_value=[_agent("working", pid=41)]):
            self.assertIs(executor._briefing_window_open("d1a1beef"), False)

    def test_roster_failure_is_open(self):
        with mock.patch.object(dashboard, "_run_claude_agents",
                               side_effect=RuntimeError("roster exploded")):
            self.assertIs(executor._briefing_window_open(SID), True)


if __name__ == "__main__":
    unittest.main()
