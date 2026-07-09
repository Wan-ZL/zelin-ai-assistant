"""act/lib/agent_states.py — single source for the roster state-name sets (P1-13).

actd (reconcile: resume/promotion decisions) and dashboard (card partitioning)
used to hand-copy these sets; the tests here pin the shared-object contract so
a claude CLI state-string rename can only ever be fixed in ONE place, and the
"idle" asymmetry (live for reconcile, not running for the dashboard) stays
deliberate instead of drifting back into two literals.

Runs entirely inside the sandbox AIASSISTANT_HOME (tests/__init__.py).
"""
import itertools
import unittest

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import actd
from act.lib import agent_states, dashboard


class SharedObjectsTestCase(unittest.TestCase):
    def test_dashboard_uses_the_shared_sets(self):
        self.assertIs(dashboard._RUNNING_STATES, agent_states._RUNNING_STATES)
        self.assertIs(dashboard._BLOCKED_STATES, agent_states._BLOCKED_STATES)
        self.assertIs(dashboard._DONE_STATES, agent_states._DONE_STATES)

    def test_actd_uses_the_shared_sets(self):
        self.assertIs(actd._LIVE_STATES, agent_states._LIVE_STATES)
        self.assertIs(actd._RUNNING_STATES, agent_states._RUNNING_STATES)
        self.assertIs(actd._BLOCKED_STATES, agent_states._BLOCKED_STATES)
        self.assertIs(actd._DONE_STATES, agent_states._DONE_STATES)

    def test_actd_and_dashboard_sets_are_identical(self):
        # the P1-13 drift this refactor kills: two modules, two literals
        self.assertEqual(actd._RUNNING_STATES, dashboard._RUNNING_STATES)
        self.assertEqual(actd._BLOCKED_STATES, dashboard._BLOCKED_STATES)
        self.assertEqual(actd._DONE_STATES, dashboard._DONE_STATES)


class SetSemanticsTestCase(unittest.TestCase):
    def test_live_is_running_plus_idle_only(self):
        # derived, not a fourth literal: live = running ∪ {idle}
        self.assertEqual(agent_states._LIVE_STATES,
                         agent_states._RUNNING_STATES | {"idle"})
        # strict superset: running ⊂ live
        self.assertTrue(agent_states._RUNNING_STATES < agent_states._LIVE_STATES)
        # idle is live (do NOT resume) but not running (dashboard: a review
        # card whose agent idles stays in review, no review-active flip)
        self.assertIn("idle", agent_states._LIVE_STATES)
        self.assertNotIn("idle", agent_states._RUNNING_STATES)

    def test_partitions_are_pairwise_disjoint(self):
        sets = (agent_states._RUNNING_STATES,
                agent_states._BLOCKED_STATES,
                agent_states._DONE_STATES)
        for a, b in itertools.combinations(sets, 2):
            self.assertFalse(a & b, f"overlap: {a & b}")
        # reconcile checks LIVE first — idle leaking into blocked/done would
        # silently reorder the whole decision chain
        self.assertFalse(agent_states._LIVE_STATES & agent_states._BLOCKED_STATES)
        self.assertFalse(agent_states._LIVE_STATES & agent_states._DONE_STATES)

    def test_all_names_lowercase(self):
        # _norm_agent lowercases roster states before matching; a mixed-case
        # entry here would silently never match anything
        all_names = (agent_states._LIVE_STATES
                     | agent_states._BLOCKED_STATES
                     | agent_states._DONE_STATES)
        for name in all_names:
            self.assertEqual(name, name.lower())

    def test_sets_are_immutable(self):
        for s in (agent_states._RUNNING_STATES, agent_states._BLOCKED_STATES,
                  agent_states._DONE_STATES, agent_states._LIVE_STATES):
            self.assertIsInstance(s, frozenset)


if __name__ == "__main__":
    unittest.main()
