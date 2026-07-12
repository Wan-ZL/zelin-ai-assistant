"""act/syncd.py — the headless cloud-sync daemon (plan of record §5).

Fake-transport unit tests (no network, everything under the sandbox
AIASSISTANT_HOME from tests/__init__.py). Covers:

  * the startup mode-gate: no state/sync.json (or mode!=cloud) => main() exits 0
    IMMEDIATELY and NEVER builds a transport / touches the network;
  * DOWN: a changed dashboard builds the correct encrypted board_snapshots
    UPSERT (encrypt -> row shape, seq bump, nonce column mirrored, payload
    decrypts byte-identical), and an UNCHANGED dashboard pushes nothing;
  * the seq seed = max(server row seq, local seq)+1 and never regresses;
  * UP: a pending inbox_actions row is decrypted, written as a valid inbox file
    and PATCHed delivered; the SAME action_id twice => exactly one inbox file
    (L3 dedup ledger);
  * auth exchange failure PAUSES (status file + reason) without crashing.

Skips gracefully when `cryptography` is absent (optional cloud dep), like
tests/test_e2e.py.
"""
import json
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import syncd
from act.lib import config, e2e

try:
    import cryptography  # noqa: F401

    HAVE_CRYPTO = True
except ImportError:
    HAVE_CRYPTO = False

_DEV = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
_OWNER = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
_EPOCH = 1
_K = b"\x22" * 32
_BOARD = b'{"generated_at":"2026-07-12T00:00:00Z","needs_approval":[{"id":"R-001"}]}'


def _sync_cfg(**over) -> dict:
    cfg = {
        "mode": "cloud",
        "device_id": _DEV,
        "owner": _OWNER,
        "epoch": _EPOCH,
        "platform": "macos",
        "supabase_url": "https://example.supabase.co",
        "apikey": "sb_publishable_x",
        "device_secret": "deadbeef",
    }
    cfg.update(over)
    return cfg


class FakeTransport(syncd.Transport):
    """Records every semantic op; returns canned rows. No network."""

    def __init__(self, *, token="tok-1", expires_in=3600, board_rows=None,
                 inbox_rows=None, fail_exchange=False):
        self.token = token
        self.expires_in = expires_in
        self.board_rows = list(board_rows or [])
        self.inbox_rows = list(inbox_rows or [])
        self.fail_exchange = fail_exchange
        self.exchanges = 0
        self.selects: list = []
        self.upserts: list = []
        self.patches: list = []

    def exchange_token(self, device_id, secret):
        self.exchanges += 1
        if self.fail_exchange:
            raise OSError("edge function unreachable")
        return {"access_token": self.token, "expires_in": self.expires_in,
                "device_id": device_id}

    def select(self, table, params, token):
        self.selects.append((table, dict(params)))
        if table == "board_snapshots":
            return list(self.board_rows)
        if table == "inbox_actions":
            return list(self.inbox_rows)
        return []

    def upsert(self, table, row, token, on_conflict):
        self.upserts.append((table, dict(row), on_conflict))

    def patch(self, table, params, patch, token):
        self.patches.append((table, dict(params), dict(patch)))

    # helpers
    def board_upserts(self):
        return [r for (t, r, _c) in self.upserts if t == "board_snapshots"]


def _reset_state():
    config.ensure_state_dirs()
    for p in (syncd.SYNC_CONFIG_PATH, syncd.DOWN_STATE_PATH, syncd.DELIVERED_LEDGER,
              syncd.APPLIED_LEDGER, syncd.APPLIED_CURSOR_PATH, syncd.STATUS_PATH,
              syncd.SECRETS_JSON_PATH, config.DASHBOARD_PATH):
        try:
            p.unlink()
        except OSError:
            pass
    for p in config.INBOX_DIR.glob("*.json"):
        p.unlink()
    # a fresh pairing key for this device
    try:
        e2e.pairing_key_path(_DEV).unlink()
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# startup mode-gate
# --------------------------------------------------------------------------- #
class ModeGateTestCase(unittest.TestCase):
    def setUp(self):
        _reset_state()

    def test_absent_sync_json_exits_0_with_zero_network(self):
        self.assertIsNone(syncd.startup_gate())
        # _default_transport must NEVER be reached (no sync.json => no network)
        boom = mock.Mock(side_effect=AssertionError("built a transport with no opt-in"))
        with mock.patch.object(syncd, "_default_transport", boom):
            self.assertEqual(syncd.main([]), 0)
        boom.assert_not_called()

    def test_mode_off_is_gated_out(self):
        syncd._atomic_write_json(syncd.SYNC_CONFIG_PATH,
                                 {"mode": "off", "device_id": _DEV})
        self.assertIsNone(syncd.startup_gate())
        boom = mock.Mock(side_effect=AssertionError("mode=off still synced"))
        with mock.patch.object(syncd, "_default_transport", boom):
            self.assertEqual(syncd.main([]), 0)

    def test_mode_cloud_opens_the_gate(self):
        syncd._atomic_write_json(syncd.SYNC_CONFIG_PATH, _sync_cfg())
        cfg = syncd.startup_gate()
        self.assertIsNotNone(cfg)
        self.assertEqual(cfg["device_id"], _DEV)


# --------------------------------------------------------------------------- #
# DOWN
# --------------------------------------------------------------------------- #
@unittest.skipUnless(HAVE_CRYPTO, "cryptography not installed (optional cloud dep)")
class DownTestCase(unittest.TestCase):
    def setUp(self):
        _reset_state()
        e2e.save_pairing(_DEV, _EPOCH, _K)
        config.DASHBOARD_PATH.write_bytes(_BOARD)

    def test_changed_dashboard_builds_correct_encrypted_upsert(self):
        ft = FakeTransport()
        d = syncd.Syncd(_sync_cfg(), ft)
        token = d.ensure_token()
        self.assertTrue(d.push_down_if_changed(token))

        rows = ft.board_upserts()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["device_id"], _DEV)
        self.assertEqual(row["owner"], _OWNER)
        self.assertEqual(row["alg"], syncd._ALG)
        self.assertEqual(row["seq"], 1)  # seed = max(0,0)+1
        # payload decrypts byte-identical to the raw dashboard
        blob = syncd._from_bytea(row["payload_enc"])
        self.assertEqual(
            e2e.decrypt_board(_K, _EPOCH, _DEV, row["seq"], blob), _BOARD)
        # nonce column mirrors the blob's embedded nonce (schema NOT NULL)
        self.assertEqual(syncd._from_bytea(row["nonce"]), e2e.embedded_nonce(blob))
        # the hash was NEVER uploaded (no content_hash / hash column)
        self.assertNotIn("hash", row)
        self.assertNotIn("content_hash", row)

    def test_unchanged_dashboard_pushes_nothing(self):
        ft = FakeTransport()
        d = syncd.Syncd(_sync_cfg(), ft)
        token = d.ensure_token()
        self.assertTrue(d.push_down_if_changed(token))
        # second call, same bytes => change-gate blocks it
        self.assertFalse(d.push_down_if_changed(token))
        self.assertEqual(len(ft.board_upserts()), 1)

    def test_heartbeat_carries_last_pushed_seq(self):
        ft = FakeTransport()
        d = syncd.Syncd(_sync_cfg(), ft)
        token = d.ensure_token()
        d.push_down_if_changed(token)
        d.heartbeat(token)
        beats = [r for (t, r, _c) in ft.upserts if t == "device_heartbeats"]
        self.assertEqual(len(beats), 1)
        self.assertEqual(beats[0]["last_pushed_seq"], 1)


# --------------------------------------------------------------------------- #
# seq seed / anti-rollback
# --------------------------------------------------------------------------- #
@unittest.skipUnless(HAVE_CRYPTO, "cryptography not installed (optional cloud dep)")
class SeqSeedTestCase(unittest.TestCase):
    def setUp(self):
        _reset_state()
        e2e.save_pairing(_DEV, _EPOCH, _K)
        config.DASHBOARD_PATH.write_bytes(_BOARD)

    def _push_with(self, server_seq, local_seq):
        syncd._atomic_write_json(syncd.DOWN_STATE_PATH,
                                 {"seq": local_seq, "hash": "stale-differs"})
        ft = FakeTransport(board_rows=[{"seq": server_seq}])
        d = syncd.Syncd(_sync_cfg(), ft)   # reads local down_state in __init__
        token = d.ensure_token()
        self.assertTrue(d.push_down_if_changed(token))
        return ft.board_upserts()[0]["seq"]

    def test_seed_is_max_server_local_plus_one(self):
        self.assertEqual(self._push_with(server_seq=5, local_seq=3), 6)

    def test_seq_never_regresses_when_local_is_ahead(self):
        self.assertEqual(self._push_with(server_seq=2, local_seq=5), 6)


# --------------------------------------------------------------------------- #
# UP
# --------------------------------------------------------------------------- #
@unittest.skipUnless(HAVE_CRYPTO, "cryptography not installed (optional cloud dep)")
class UpTestCase(unittest.TestCase):
    def setUp(self):
        _reset_state()
        e2e.save_pairing(_DEV, _EPOCH, _K)

    def _pending_row(self, aid, action_payload, board_seq=42):
        blob = e2e.encrypt_action(
            _K, _EPOCH, _DEV, aid, board_seq,
            json.dumps(action_payload).encode("utf-8"))
        return {"action_id": aid, "payload_enc": syncd._to_bytea(blob),
                "board_seq": board_seq}

    def test_pending_action_decrypts_writes_inbox_and_patches_delivered(self):
        aid = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
        payload = {"id": "R-001", "action": "approve", "comment": None,
                   "ts": "2026-07-12T01:00:00Z", "expected_status": "card_sent"}
        ft = FakeTransport(inbox_rows=[self._pending_row(aid, payload)])
        d = syncd.Syncd(_sync_cfg(), ft)
        token = d.ensure_token()

        self.assertEqual(d.pull_up(token), 1)
        path = config.INBOX_DIR / f"{aid}.json"
        self.assertTrue(path.exists())
        rec = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(rec["id"], "R-001")
        self.assertEqual(rec["action"], "approve")
        self.assertEqual(rec["expected_status"], "card_sent")
        self.assertEqual(rec["board_seq"], 42)
        # delivered PATCH issued for this action_id
        delivered = [(p, patch) for (t, p, patch) in ft.patches
                     if t == "inbox_actions" and patch.get("status") == "delivered"]
        self.assertEqual(len(delivered), 1)
        self.assertEqual(delivered[0][0]["action_id"], f"eq.{aid}")

    def test_same_action_id_twice_is_one_inbox_file(self):
        aid = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
        payload = {"id": "R-002", "action": "reject", "comment": None,
                   "ts": "2026-07-12T01:00:00Z"}
        ft = FakeTransport(inbox_rows=[self._pending_row(aid, payload)])
        d = syncd.Syncd(_sync_cfg(), ft)
        token = d.ensure_token()

        self.assertEqual(d.pull_up(token), 1)
        # the server still reports it pending (fake doesn't advance); the ledger
        # must dedup so no second inbox file is materialised
        self.assertEqual(d.pull_up(token), 0)
        files = list(config.INBOX_DIR.glob(f"{aid}.json"))
        self.assertEqual(len(files), 1)

    def test_ack_tail_patches_applied_with_result_status(self):
        aid = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
        syncd.SYNC_DIR.mkdir(parents=True, exist_ok=True)
        with syncd.APPLIED_LEDGER.open("w", encoding="utf-8") as fh:
            fh.write(json.dumps({"action_id": aid, "result_status": "running",
                                 "ts": "2026-07-12T02:00:00Z"}) + "\n")
        ft = FakeTransport()
        d = syncd.Syncd(_sync_cfg(), ft)
        token = d.ensure_token()
        self.assertEqual(d.ack_tail(token), 1)
        applied = [(p, patch) for (t, p, patch) in ft.patches
                   if t == "inbox_actions" and patch.get("status") == "applied"]
        self.assertEqual(len(applied), 1)
        self.assertEqual(applied[0][0]["action_id"], f"eq.{aid}")
        self.assertEqual(applied[0][1]["result_status"], "running")
        # cursor advanced => a second pass re-patches nothing
        self.assertEqual(d.ack_tail(token), 0)


# --------------------------------------------------------------------------- #
# auth exchange failure => pause, never crash
# --------------------------------------------------------------------------- #
class AuthFailTestCase(unittest.TestCase):
    def setUp(self):
        _reset_state()

    def test_exchange_failure_pauses_without_crashing(self):
        ft = FakeTransport(fail_exchange=True)
        d = syncd.Syncd(_sync_cfg(), ft)
        self.assertIsNone(d.ensure_token())
        self.assertIsNotNone(d._paused_reason)
        self.assertIn(syncd.PAUSE_MESSAGE, d._paused_reason)
        # run_once must swallow it entirely (actd keeps writing locally regardless)
        d.run_once()  # no exception
        self.assertEqual(ft.upserts, [])
        self.assertEqual(ft.patches, [])
        # a UI-readable status file names the honest next step
        status = json.loads(syncd.STATUS_PATH.read_text(encoding="utf-8"))
        self.assertTrue(status["paused"])
        self.assertIn("重新配对", status["reason"])


if __name__ == "__main__":
    unittest.main()
