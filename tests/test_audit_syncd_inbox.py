"""Audit regression tests — syncd's inbox boundary and board push.

Covers three confirmed audit findings:
  * fail-closed field-type validation in _write_inbox_file: a validly
    encrypted but shape-poisoned payload (non-string comment/text/id, non
    list-of-str ids) must be rejected AT THE BOUNDARY — it would otherwise
    wedge actd's whole inbox pass forever (AttributeError before ack+unlink,
    re-applied every 10s);
  * the pull_up write-failure contract: a phone action whose inbox file could
    not be staged must stay 'pending' and be retried — never PATCHed
    'delivered' while no inbox file ever existed;
  * the board_snapshots upsert must NOT client-stamp updated_at (the server
    clock is the liveness authority behind the phone's STALE/DEAD gate).

Fake transport, sandbox AIASSISTANT_HOME, no network.
"""
import json
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import syncd
from act.lib import config, e2e
from tests.test_syncd import (
    _BOARD, _CHAN, _EPOCH, _K, FakeTransport, _reset_state, _sync_cfg,
    _write_secret_files,
)

try:
    import cryptography  # noqa: F401

    HAVE_CRYPTO = True
except ImportError:
    HAVE_CRYPTO = False


def _delivered_patches(ft):
    return [patch for (t, _params, patch, _ws) in ft.patches
            if t == "inbox_actions" and patch.get("status") == "delivered"]


@unittest.skipUnless(HAVE_CRYPTO, "cryptography not installed (optional cloud dep)")
class _UpBase(unittest.TestCase):
    def setUp(self):
        _reset_state()
        e2e.save_pairing(_CHAN, _EPOCH, _K)
        _write_secret_files()

    def _pending_row(self, aid, action_payload, board_seq=42):
        blob = e2e.encrypt_action(_K, _EPOCH, _CHAN, aid, board_seq,
                                  json.dumps(action_payload).encode("utf-8"))
        return {"action_id": aid, "payload_enc": syncd._to_bytea(blob),
                "board_seq": board_seq}


class InboxShapeGateTestCase(_UpBase):
    def test_non_string_comment_is_rejected_at_the_boundary(self):
        aid = "11111111-1111-4111-8111-111111111111"
        ft = FakeTransport(inbox_rows=[self._pending_row(
            aid, {"id": "R-001", "action": "comment", "comment": {"x": 1}})])
        d = syncd.Syncd(_sync_cfg(), ft)
        self.assertTrue(d._ensure_ready())
        self.assertEqual(d.pull_up(), 0)
        # fail-closed: the poison payload never reaches actd's inbox
        self.assertFalse((config.INBOX_DIR / f"{aid}.json").exists())
        # and is never falsely reported delivered
        self.assertEqual(_delivered_patches(ft), [])

    def test_bad_shapes_are_all_skipped(self):
        bad = [
            {"action": 5, "id": "R-1"},
            {"action": "approve", "id": 7},
            {"action": "capture", "text": ["a"]},
            {"action": "merge_review", "ids": ["R-1", 5]},
            {"action": "merge_review", "ids": "R-1"},
            {"action": "merge_apply", "primary": {"id": "R-1"}},
        ]
        rows = [self._pending_row(f"22222222-2222-4222-8222-2222222222{i:02d}", p)
                for i, p in enumerate(bad)]
        ft = FakeTransport(inbox_rows=rows)
        d = syncd.Syncd(_sync_cfg(), ft)
        self.assertTrue(d._ensure_ready())
        self.assertEqual(d.pull_up(), 0)
        self.assertEqual(list(config.INBOX_DIR.glob("*.json")), [])
        self.assertEqual(_delivered_patches(ft), [])

    def test_none_valued_optional_fields_still_pass(self):
        # None counts as absent (actd coerces `comment or ""`), so the iOS
        # client's explicit nulls must not be rejected.
        aid = "33333333-3333-4333-8333-333333333333"
        ft = FakeTransport(inbox_rows=[self._pending_row(
            aid, {"id": "R-001", "action": "approve", "comment": None})])
        d = syncd.Syncd(_sync_cfg(), ft)
        self.assertTrue(d._ensure_ready())
        self.assertEqual(d.pull_up(), 1)
        self.assertTrue((config.INBOX_DIR / f"{aid}.json").exists())


class InboxWriteFailureTestCase(_UpBase):
    def test_inbox_write_failure_is_not_reported_delivered(self):
        # Pins the safe contract: 'delivered' is only ever set after the inbox
        # file exists. Pre-fix, pull_up ledgered the aid BEFORE the write, so
        # a failed write left the action fileless while the next pass PATCHed
        # the row 'delivered' — the user's approve/reject evaporated.
        aid = "44444444-4444-4444-8444-444444444444"
        payload = {"id": "R-002", "action": "approve",
                   "ts": "2026-07-15T01:00:00Z"}
        ft = FakeTransport(inbox_rows=[self._pending_row(aid, payload)])
        d = syncd.Syncd(_sync_cfg(), ft)
        self.assertTrue(d._ensure_ready())

        real_write_text = Path.write_text
        inbox_root = str(config.INBOX_DIR)

        def enospc_in_inbox(pself, data, *args, **kwargs):
            if str(pself).startswith(inbox_root):
                raise OSError(28, "No space left on device")
            return real_write_text(pself, data, *args, **kwargs)

        # pass 1 — staging the file fails: NOT ledgered, NOT delivered
        with mock.patch.object(Path, "write_text", enospc_in_inbox):
            self.assertEqual(d.pull_up(), 0)
        self.assertFalse((config.INBOX_DIR / f"{aid}.json").exists())
        self.assertNotIn(aid, d._delivered_set())
        self.assertEqual(_delivered_patches(ft), [])

        # pass 2 — disk recovered: retried, materialised, delivered ONCE
        self.assertEqual(d.pull_up(), 1)
        self.assertTrue((config.INBOX_DIR / f"{aid}.json").exists())
        self.assertEqual(len(_delivered_patches(ft)), 1)

        # pass 3 — ledger dedup: no re-materialise, file count stays 1
        self.assertEqual(d.pull_up(), 0)
        self.assertEqual(len(list(config.INBOX_DIR.glob(f"{aid}.json"))), 1)


@unittest.skipUnless(HAVE_CRYPTO, "cryptography not installed (optional cloud dep)")
class ServerClockAuthorityTestCase(unittest.TestCase):
    def setUp(self):
        _reset_state()
        e2e.save_pairing(_CHAN, _EPOCH, _K)
        _write_secret_files()
        config.DASHBOARD_PATH.write_bytes(_BOARD)

    def test_board_upsert_never_client_stamps_updated_at(self):
        # updated_at is the phone's liveness authority and must come from the
        # server trigger; a skewed Mac clock painting a dead board FRESH would
        # defeat the STALE/DEAD confirm gate on mutating actions.
        ft = FakeTransport()
        d = syncd.Syncd(_sync_cfg(), ft)
        self.assertTrue(d._ensure_ready())
        self.assertTrue(d.push_down_if_changed())
        row, _ws = ft.board_upserts()[0]
        self.assertNotIn("updated_at", row)


if __name__ == "__main__":
    unittest.main()
