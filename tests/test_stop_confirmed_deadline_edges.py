"""§46.1 stop_session_confirmed — every clock check and the final probe.

tests/test_stop_confirmed.py pins the happy paths and the two fast-fail rules;
these are the remaining edges of the verify-then-stop loop, each with a
scripted clock: the budget expiring BETWEEN the probe and the stop (no stop is
issued), the budget expiring AFTER the last round (no final probe), the final
probe failing (a failure, never "stopped"), and the final probe confirming a
late death ("stopped", not the still-alive message). Also ``stop_session``'s
own contract: no live pid → nothing runs; live pid → ``claude stop <short>``
then the 2s grace (sleep patched).
"""
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import executor

SID = "abcd1234-0000-4000-8000-000000000001"


class DeadlineEdgeTestCase(unittest.TestCase):
    def _run(self, clock_values, prober, retries=0, stopper=None):
        stopper = stopper or mock.Mock(return_value=True)
        clock = mock.Mock(side_effect=clock_values)
        out = executor.stop_session_confirmed(
            SID, retries=retries, prober=prober, stopper=stopper,
            sleeper=mock.Mock(), budget_s=60.0, clock=clock)
        return out, stopper

    def test_budget_expiring_between_probe_and_stop_issues_nothing(self):
        (stopped, issued, detail), stopper = self._run(
            [0.0, 1.0, 100.0], prober=mock.Mock(return_value={"pid": 42}))
        self.assertEqual((stopped, issued), (False, False))
        self.assertIn("budget", detail)
        stopper.assert_not_called()

    def test_budget_expiring_after_the_last_round_skips_the_final_probe(self):
        prober = mock.Mock(return_value={"pid": 42})
        (stopped, issued, detail), stopper = self._run([0.0, 1.0, 2.0, 100.0], prober)
        self.assertEqual((stopped, issued), (False, True))
        self.assertIn("budget", detail)
        self.assertEqual(prober.call_count, 1)     # no final probe past the deadline
        stopper.assert_called_once()

    def test_final_probe_failure_is_a_failure(self):
        prober = mock.Mock(side_effect=[{"pid": 42}, None])
        (stopped, issued, detail), _ = self._run([0.0, 1.0, 2.0, 3.0], prober)
        self.assertEqual((stopped, issued), (False, True))
        self.assertIn("roster query failed", detail)

    def test_final_probe_confirming_death_is_stopped(self):
        prober = mock.Mock(side_effect=[{"pid": 42}, {}])
        (stopped, issued, detail), _ = self._run([0.0, 1.0, 2.0, 3.0], prober)
        self.assertEqual((stopped, issued, detail), (True, True, "stopped"))

    def test_still_alive_detail_names_pid_and_attempts(self):
        prober = mock.Mock(return_value={"pid": 4242})
        (stopped, issued, detail), _ = self._run(
            [0.0] + [1.0] * 10, prober, retries=1)
        self.assertEqual((stopped, issued), (False, True))
        self.assertIn("pid 4242", detail)
        self.assertIn("after 2 stop attempts", detail)

    def test_clock_exactly_at_the_deadline_is_over_budget(self):
        (stopped, issued, detail), stopper = self._run(
            [0.0, 60.0], prober=mock.Mock(return_value={"pid": 42}))
        self.assertEqual((stopped, issued), (False, False))
        self.assertIn("budget", detail)
        stopper.assert_not_called()
        (stopped, issued, detail), stopper = self._run(
            [0.0, 1.0, 60.0], prober=mock.Mock(return_value={"pid": 42}))
        self.assertEqual((stopped, issued), (False, False))
        stopper.assert_not_called()

    def test_default_retries_mean_three_attempts(self):
        prober = mock.Mock(return_value={"pid": 42})
        stopper = mock.Mock(return_value=True)
        stopped, issued, detail = executor.stop_session_confirmed(
            SID, prober=prober, stopper=stopper, sleeper=mock.Mock())
        self.assertEqual((stopped, issued), (False, True))
        self.assertEqual(stopper.call_count, 3)
        self.assertEqual(executor.STOP_CONFIRM_RETRIES, 2)
        self.assertIn("after 3 stop attempts", detail)

    def test_stopper_that_always_fails_never_counts_as_issued(self):
        prober = mock.Mock(return_value={"pid": 42})
        stopper = mock.Mock(side_effect=OSError("claude missing"))
        stopped, issued, _ = executor.stop_session_confirmed(
            SID, retries=1, prober=prober, stopper=stopper, sleeper=mock.Mock())
        self.assertEqual((stopped, issued), (False, False))
        self.assertEqual(stopper.call_count, 2)

    def test_default_seams_are_the_strict_probe_and_stop_session(self):
        # no prober/stopper given → roster via _agent_info_strict, stop via stop_session
        with mock.patch.object(executor, "_agent_info_strict", return_value={}) as probe, \
                mock.patch.object(executor, "stop_session") as stop:
            out = executor.stop_session_confirmed(SID, sleeper=mock.Mock())
        self.assertEqual(out, (True, False, "not running"))
        probe.assert_called_once_with(SID)
        stop.assert_not_called()


class StopSessionTestCase(unittest.TestCase):
    def test_no_live_pid_runs_nothing(self):
        with mock.patch.object(executor.subprocess, "run") as run, \
                mock.patch.object(executor, "_agent_info", return_value={}):
            self.assertFalse(executor.stop_session(SID))
            self.assertFalse(executor.stop_session(SID, info={"pid": None}))
        run.assert_not_called()

    def test_live_pid_issues_stop_with_short_id_then_waits(self):
        with mock.patch.object(executor.subprocess, "run") as run, \
                mock.patch.object(executor.time, "sleep") as slept, \
                mock.patch.object(executor.llm, "claude_bin", return_value="/opt/claude"):
            self.assertTrue(executor.stop_session(SID, info={"pid": 42}))
        self.assertEqual(run.call_args.args[0], ["/opt/claude", "stop", "abcd1234"])
        self.assertEqual(run.call_args.kwargs, {"capture_output": True, "text": True, "timeout": 30})
        slept.assert_called_once_with(2)

    def test_stop_spawn_failure_propagates_to_the_caller(self):
        with mock.patch.object(executor.subprocess, "run", side_effect=OSError("gone")), \
                mock.patch.object(executor.time, "sleep"):
            with self.assertRaises(OSError):
                executor.stop_session(SID, info={"pid": 42})


if __name__ == "__main__":
    unittest.main()
