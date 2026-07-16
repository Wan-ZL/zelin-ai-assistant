"""v0.40.0 钱看得见、事有回执 (CONTRACT §40) — honesty/feedback debt.

Pinned here:
- cost_state projection matrix (§40.1): "estimated" for any parseable number
  (the threshold keeps gating only show_cost / the collapsed badge),
  "unknown" for missing/corrupt estimates — unknown-cost cards must never
  read as free;
- purge_at math (§40.5): trashed_at + retention, null for pinned rows /
  disabled retention / unparsable trashed_at — exactly the rows
  actd.purge_trash skips, so the countdown never promises a purge that
  isn't coming.

Runs entirely inside the sandbox AIASSISTANT_HOME (tests/__init__.py); no
LLM subprocess is ever spawned.
"""
import os
import tempfile
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act.lib import config, dashboard
from act.lib.registry import Requirement


# --------------------------------------------------------------------------- #
# §40.1 cost_state projection matrix
# --------------------------------------------------------------------------- #
class CostStateTestCase(unittest.TestCase):
    def setUp(self):
        self.cfg = config.Config()  # show_cost_above_usd = 5.0
        home = tempfile.mkdtemp(prefix="cost-home-")
        patcher = mock.patch.dict(os.environ, {"HOME": home})
        patcher.start()
        self.addCleanup(patcher.stop)

    def _card(self, **fields):
        req = Requirement.from_dict(
            {"id": "R-1", "title": "t", "status": "card_sent", **fields})
        dash = dashboard.build_dashboard(reqs=[req], agents=[], cfg=self.cfg)
        return dash["needs_approval"][0]

    def test_estimate_below_threshold_is_estimated_but_badge_hidden(self):
        item = self._card(cost_estimate_usd=3)
        self.assertEqual(item["cost_usd"], 3.0)
        self.assertFalse(item["show_cost"])         # threshold gates the badge
        self.assertEqual(item["cost_state"], "estimated")  # detail says money

    def test_estimate_at_threshold_shows_badge_and_estimated(self):
        item = self._card(cost_estimate_usd=12)
        self.assertTrue(item["show_cost"])
        self.assertEqual(item["cost_state"], "estimated")

    def test_missing_estimate_is_unknown_not_free(self):
        item = self._card()
        self.assertIsNone(item["cost_usd"])
        self.assertFalse(item["show_cost"])
        self.assertEqual(item["cost_state"], "unknown")

    def test_corrupt_estimate_is_unknown(self):
        item = self._card(cost_estimate_usd="cheap")
        self.assertIsNone(item["cost_usd"])
        self.assertEqual(item["cost_state"], "unknown")


# --------------------------------------------------------------------------- #
# §40.5 purge_at math + pinned
# --------------------------------------------------------------------------- #
class PurgeAtTestCase(unittest.TestCase):
    def setUp(self):
        self.cfg = config.Config()  # trash_retention_days = 60
        home = tempfile.mkdtemp(prefix="purge-home-")
        patcher = mock.patch.dict(os.environ, {"HOME": home})
        patcher.start()
        self.addCleanup(patcher.stop)

    def _row(self, **fields):
        req = Requirement.from_dict(
            {"id": "R-9", "title": "t", "status": "trashed",
             "trash_reason": "deleted", **fields})
        dash = dashboard.build_dashboard(reqs=[req], agents=[], cfg=self.cfg)
        return dash["trash"][0]

    def test_purge_at_is_trashed_at_plus_retention(self):
        row = self._row(trashed_at="2026-07-01T00:00:00Z")
        self.assertEqual(row["purge_at"], "2026-08-30T00:00:00Z")  # +60 days

    def test_pinned_row_never_gets_a_deadline(self):
        row = self._row(trashed_at="2026-07-01T00:00:00Z", permanent=True)
        self.assertIsNone(row["purge_at"])

    def test_retention_disabled_means_no_deadline(self):
        self.cfg.trash_retention_days = 0
        row = self._row(trashed_at="2026-07-01T00:00:00Z")
        self.assertIsNone(row["purge_at"])

    def test_unparsable_trashed_at_means_no_deadline(self):
        # purge_trash skips these rows too — the countdown must not promise
        # a purge that isn't coming.
        row = self._row(trashed_at="not-a-date")
        self.assertIsNone(row["purge_at"])


if __name__ == "__main__":
    unittest.main()
