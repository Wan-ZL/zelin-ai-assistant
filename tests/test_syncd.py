"""act/syncd.py — the headless cloud-sync daemon (QR-only capability model).

Fake-transport unit tests (no network, everything under the sandbox
AIASSISTANT_HOME from tests/__init__.py). Covers:

  * the startup mode-gate: no state/sync.json (or mode!=cloud / no channel_id)
    => main() exits 0 IMMEDIATELY and NEVER builds a transport / touches network;
  * capability headers: every request carries x-sync-channel; writes (board
    upsert, inbox patch, channel insert) also carry x-sync-write;
  * DOWN: a changed dashboard builds the correct encrypted board_snapshots
    UPSERT on_conflict channel_id (encrypt -> row shape, seq bump, nonce column
    mirrored, payload decrypts byte-identical), and an UNCHANGED dashboard
    pushes nothing;
  * the seq seed = max(server row seq, local seq)+1 and never regresses;
  * UP: a pending inbox_actions row is decrypted, written as a valid inbox file
    and PATCHed delivered; the SAME action_id twice => exactly one inbox file
    (L3 dedup ledger);
  * init_channel: generates channel_id + write_secret + K, registers the channel
    (write_secret_hash + label_enc), writes state/sync.json mode=cloud, and the
    emitted QR blob round-trips through e2e.parse_channel_qr;
  * a missing write_secret / pairing key PAUSES (status file + reason) without
    crashing.

Skips crypto-dependent cases gracefully when `cryptography` is absent (optional
cloud dep), like tests/test_e2e.py.
"""
import hashlib
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

_CHAN = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
_EPOCH = 1
_K = b"\x22" * 32
_WRITE_RAW = b"\x33" * 32
_WRITE_TEXT = syncd._b64url_encode(_WRITE_RAW)
_BOARD = b'{"generated_at":"2026-07-12T00:00:00Z","needs_approval":[{"id":"R-001"}]}'


def _sync_cfg(**over) -> dict:
    cfg = {
        "mode": "cloud",
        "channel_id": _CHAN,
        "epoch": _EPOCH,
        "platform": "macos",
    }
    cfg.update(over)
    return cfg


class FakeTransport(syncd.Transport):
    """Records every semantic op (with the write_secret carried on writes);
    returns canned rows. No network."""

    def __init__(self, *, board_rows=None, inbox_rows=None, insert_error=None):
        self.board_rows = list(board_rows or [])
        self.inbox_rows = list(inbox_rows or [])
        self.selects: list = []
        self.inserts: list = []
        self.upserts: list = []
        self.patches: list = []
        # optional: an exception the (recorded) insert raises, to exercise the
        # self-heal channel-registration retry / duplicate-as-success paths.
        self.insert_error = insert_error

    def select(self, table, params):
        self.selects.append((table, dict(params)))
        if table == "board_snapshots":
            return list(self.board_rows)
        if table == "inbox_actions":
            return list(self.inbox_rows)
        return []

    def insert(self, table, row, write_secret):
        self.inserts.append((table, dict(row), write_secret))
        if self.insert_error is not None:
            raise self.insert_error

    def upsert(self, table, row, on_conflict, write_secret):
        self.upserts.append((table, dict(row), on_conflict, write_secret))

    def patch(self, table, params, patch, write_secret):
        self.patches.append((table, dict(params), dict(patch), write_secret))

    # helpers
    def board_upserts(self):
        return [(r, ws) for (t, r, _c, ws) in self.upserts if t == "board_snapshots"]


def _write_secret_files():
    """Persist the channel's write_secret so _ensure_ready() finds it."""
    syncd._ensure_sync_dir()
    syncd.WRITE_SECRET_PATH.write_text(_WRITE_TEXT + "\n", encoding="utf-8")


def _reset_state():
    config.ensure_state_dirs()
    for p in (syncd.SYNC_CONFIG_PATH, syncd.DOWN_STATE_PATH, syncd.DELIVERED_LEDGER,
              syncd.APPLIED_LEDGER, syncd.APPLIED_CURSOR_PATH, syncd.STATUS_PATH,
              syncd.CHANNEL_ID_PATH, syncd.WRITE_SECRET_PATH, syncd.PAIRING_QR_PNG,
              config.DASHBOARD_PATH):
        try:
            p.unlink()
        except OSError:
            pass
    for p in config.INBOX_DIR.glob("*.json"):
        p.unlink()
    # a fresh pairing key for this channel
    try:
        e2e.pairing_key_path(_CHAN).unlink()
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
                                 {"mode": "off", "channel_id": _CHAN})
        self.assertIsNone(syncd.startup_gate())
        boom = mock.Mock(side_effect=AssertionError("mode=off still synced"))
        with mock.patch.object(syncd, "_default_transport", boom):
            self.assertEqual(syncd.main([]), 0)

    def test_missing_channel_id_is_gated_out(self):
        syncd._atomic_write_json(syncd.SYNC_CONFIG_PATH, {"mode": "cloud"})
        self.assertIsNone(syncd.startup_gate())

    def test_mode_cloud_opens_the_gate(self):
        syncd._atomic_write_json(syncd.SYNC_CONFIG_PATH, _sync_cfg())
        cfg = syncd.startup_gate()
        self.assertIsNotNone(cfg)
        self.assertEqual(cfg["channel_id"], _CHAN)


# --------------------------------------------------------------------------- #
# transport headers (anon key + capability headers)
# --------------------------------------------------------------------------- #
class HeaderTestCase(unittest.TestCase):
    def test_channel_header_always_write_header_only_on_writes(self):
        t = syncd.HttpTransport("https://x.supabase.co", "anon-key", _CHAN)
        read = t._headers()
        self.assertEqual(read[syncd.HDR_CHANNEL], _CHAN)
        self.assertNotIn(syncd.HDR_WRITE, read)
        self.assertEqual(read["apikey"], "anon-key")
        self.assertEqual(read["Authorization"], "Bearer anon-key")
        write = t._headers(_WRITE_TEXT)
        self.assertEqual(write[syncd.HDR_WRITE], _WRITE_TEXT)
        self.assertEqual(write[syncd.HDR_CHANNEL], _CHAN)


# --------------------------------------------------------------------------- #
# DOWN
# --------------------------------------------------------------------------- #
@unittest.skipUnless(HAVE_CRYPTO, "cryptography not installed (optional cloud dep)")
class DownTestCase(unittest.TestCase):
    def setUp(self):
        _reset_state()
        e2e.save_pairing(_CHAN, _EPOCH, _K)
        _write_secret_files()
        config.DASHBOARD_PATH.write_bytes(_BOARD)

    def test_changed_dashboard_builds_correct_encrypted_upsert(self):
        ft = FakeTransport()
        d = syncd.Syncd(_sync_cfg(), ft)
        self.assertTrue(d._ensure_ready())
        self.assertTrue(d.push_down_if_changed())

        rows = ft.board_upserts()
        self.assertEqual(len(rows), 1)
        row, write_secret = rows[0]
        self.assertEqual(row["channel_id"], _CHAN)
        self.assertEqual(row["alg"], syncd._ALG)
        self.assertEqual(row["seq"], 1)  # seed = max(0,0)+1
        # write header carried the write_secret on the upsert
        self.assertEqual(write_secret, _WRITE_TEXT)
        # on_conflict target is channel_id
        self.assertEqual(ft.upserts[0][2], "channel_id")
        # payload decrypts byte-identical to the raw dashboard
        blob = syncd._from_bytea(row["payload_enc"])
        self.assertEqual(
            e2e.decrypt_board(_K, _EPOCH, _CHAN, row["seq"], blob), _BOARD)
        # nonce column mirrors the blob's embedded nonce (schema NOT NULL)
        self.assertEqual(syncd._from_bytea(row["nonce"]), e2e.embedded_nonce(blob))
        # the hash was NEVER uploaded (no content_hash / hash column)
        self.assertNotIn("hash", row)
        self.assertNotIn("content_hash", row)
        # no device_id / owner columns in the v2 schema
        self.assertNotIn("device_id", row)
        self.assertNotIn("owner", row)

    def test_unchanged_dashboard_pushes_nothing(self):
        ft = FakeTransport()
        d = syncd.Syncd(_sync_cfg(), ft)
        self.assertTrue(d._ensure_ready())
        self.assertTrue(d.push_down_if_changed())
        # second call, same bytes => change-gate blocks it
        self.assertFalse(d.push_down_if_changed())
        self.assertEqual(len(ft.board_upserts()), 1)


# --------------------------------------------------------------------------- #
# seq seed / anti-rollback
# --------------------------------------------------------------------------- #
@unittest.skipUnless(HAVE_CRYPTO, "cryptography not installed (optional cloud dep)")
class SeqSeedTestCase(unittest.TestCase):
    def setUp(self):
        _reset_state()
        e2e.save_pairing(_CHAN, _EPOCH, _K)
        _write_secret_files()
        config.DASHBOARD_PATH.write_bytes(_BOARD)

    def _push_with(self, server_seq, local_seq):
        syncd._atomic_write_json(syncd.DOWN_STATE_PATH,
                                 {"seq": local_seq, "hash": "stale-differs"})
        ft = FakeTransport(board_rows=[{"seq": server_seq}])
        d = syncd.Syncd(_sync_cfg(), ft)   # reads local down_state in __init__
        self.assertTrue(d._ensure_ready())
        self.assertTrue(d.push_down_if_changed())
        return ft.board_upserts()[0][0]["seq"]

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
        e2e.save_pairing(_CHAN, _EPOCH, _K)
        _write_secret_files()

    def _pending_row(self, aid, action_payload, board_seq=42):
        blob = e2e.encrypt_action(
            _K, _EPOCH, _CHAN, aid, board_seq,
            json.dumps(action_payload).encode("utf-8"))
        return {"action_id": aid, "payload_enc": syncd._to_bytea(blob),
                "board_seq": board_seq}

    def test_pending_action_decrypts_writes_inbox_and_patches_delivered(self):
        aid = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
        payload = {"id": "R-001", "action": "approve", "comment": None,
                   "ts": "2026-07-12T01:00:00Z", "expected_status": "card_sent"}
        ft = FakeTransport(inbox_rows=[self._pending_row(aid, payload)])
        d = syncd.Syncd(_sync_cfg(), ft)
        self.assertTrue(d._ensure_ready())

        self.assertEqual(d.pull_up(), 1)
        # the pull SELECT filtered on channel_id (not device_id)
        sel = [p for (t, p) in ft.selects if t == "inbox_actions"][0]
        self.assertEqual(sel["channel_id"], f"eq.{_CHAN}")
        self.assertEqual(sel["status"], "eq.pending")
        self.assertNotIn("target_device_id", sel)

        path = config.INBOX_DIR / f"{aid}.json"
        self.assertTrue(path.exists())
        rec = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(rec["id"], "R-001")
        self.assertEqual(rec["action"], "approve")
        self.assertEqual(rec["expected_status"], "card_sent")
        self.assertEqual(rec["board_seq"], 42)
        # delivered PATCH issued for this action_id, carrying the write_secret
        delivered = [(p, patch, ws) for (t, p, patch, ws) in ft.patches
                     if t == "inbox_actions" and patch.get("status") == "delivered"]
        self.assertEqual(len(delivered), 1)
        self.assertEqual(delivered[0][0]["action_id"], f"eq.{aid}")
        self.assertEqual(delivered[0][2], _WRITE_TEXT)

    def test_same_action_id_twice_is_one_inbox_file(self):
        aid = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
        payload = {"id": "R-002", "action": "reject", "comment": None,
                   "ts": "2026-07-12T01:00:00Z"}
        ft = FakeTransport(inbox_rows=[self._pending_row(aid, payload)])
        d = syncd.Syncd(_sync_cfg(), ft)
        self.assertTrue(d._ensure_ready())

        self.assertEqual(d.pull_up(), 1)
        # the server still reports it pending (fake doesn't advance); the ledger
        # must dedup so no second inbox file is materialised
        self.assertEqual(d.pull_up(), 0)
        files = list(config.INBOX_DIR.glob(f"{aid}.json"))
        self.assertEqual(len(files), 1)

    def test_crash_between_ledger_and_inbox_does_not_double_materialise(self):
        # M4 mark-then-materialise: the L3 delivered ledger is appended BEFORE
        # the inbox file is written. Simulate a crash DURING the inbox write —
        # the ledger must already record the action, so a re-run (after actd may
        # have consumed+deleted the file) sees it delivered and does NOT
        # re-materialise. The pre-fix order (write-then-ledger) would leave the
        # ledger empty after the crash → the re-run re-writes the file → a
        # non-idempotent capture/feedback double-applies.
        aid = "ffffffff-ffff-4fff-8fff-ffffffffffff"
        payload = {"action": "capture", "text": "quick note",
                   "ts": "2026-07-12T01:00:00Z"}
        ft = FakeTransport(inbox_rows=[self._pending_row(aid, payload)])
        d = syncd.Syncd(_sync_cfg(), ft)
        self.assertTrue(d._ensure_ready())

        # pass 1 — crash mid-materialise (process dies while writing the file)
        with mock.patch.object(d, "_write_inbox_file",
                               side_effect=RuntimeError("crash")):
            with self.assertRaises(RuntimeError):
                d.pull_up()
        # the M4 invariant the pre-fix order violated: ledger recorded it FIRST
        self.assertIn(aid, d._delivered_set())
        # actd already consumed+deleted whatever might have landed
        for p in config.INBOX_DIR.glob(f"{aid}.json"):
            p.unlink()

        # pass 2 — recovered: the re-run must SKIP, never re-materialise
        self.assertEqual(d.pull_up(), 0)
        self.assertFalse((config.INBOX_DIR / f"{aid}.json").exists())

    def test_ack_tail_patches_applied_with_result_status(self):
        aid = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
        syncd.SYNC_DIR.mkdir(parents=True, exist_ok=True)
        with syncd.APPLIED_LEDGER.open("w", encoding="utf-8") as fh:
            fh.write(json.dumps({"action_id": aid, "result_status": "running",
                                 "ts": "2026-07-12T02:00:00Z"}) + "\n")
        ft = FakeTransport()
        d = syncd.Syncd(_sync_cfg(), ft)
        self.assertTrue(d._ensure_ready())
        self.assertEqual(d.ack_tail(), 1)
        applied = [(p, patch, ws) for (t, p, patch, ws) in ft.patches
                   if t == "inbox_actions" and patch.get("status") == "applied"]
        self.assertEqual(len(applied), 1)
        self.assertEqual(applied[0][0]["action_id"], f"eq.{aid}")
        self.assertEqual(applied[0][1]["result_status"], "running")
        self.assertEqual(applied[0][2], _WRITE_TEXT)  # write header carried
        # cursor advanced => a second pass re-patches nothing
        self.assertEqual(d.ack_tail(), 0)


# --------------------------------------------------------------------------- #
# init_channel (replaces pair)
# --------------------------------------------------------------------------- #
@unittest.skipUnless(HAVE_CRYPTO, "cryptography not installed (optional cloud dep)")
class InitChannelTestCase(unittest.TestCase):
    def setUp(self):
        _reset_state()

    def test_init_channel_registers_and_writes_opt_in_and_qr(self):
        ft = FakeTransport()
        with mock.patch.object(syncd, "HttpTransport", return_value=ft):
            result = syncd.init_channel("公司 Mac")

        # channel_id + write_secret + K persisted
        cid = result["channel_id"]
        self.assertTrue(syncd.CHANNEL_ID_PATH.exists())
        self.assertTrue(syncd.WRITE_SECRET_PATH.exists())
        epoch, k_i = e2e.load_pairing(cid)
        self.assertEqual(len(k_i), 32)

        # channel INSERT: write_secret_hash + label_enc, carrying the write header
        self.assertEqual(len(ft.inserts), 1)
        table, row, write_secret = ft.inserts[0]
        self.assertEqual(table, "channels")
        self.assertEqual(row["channel_id"], cid)
        write_text = syncd.WRITE_SECRET_PATH.read_text(encoding="utf-8").strip()
        self.assertEqual(write_secret, write_text)
        self.assertEqual(row["write_secret_hash"],
                         hashlib.sha256(write_text.encode("ascii")).hexdigest())
        # label is E2E-encrypted (never plaintext on the server)
        label_blob = syncd._from_bytea(row["label_enc"])
        self.assertEqual(e2e.decrypt_label(k_i, epoch, cid, label_blob), "公司 Mac")
        self.assertTrue(result["registered"])

        # opt-in written: mode=cloud + channel_id (startup gate now opens)
        cfg = json.loads(syncd.SYNC_CONFIG_PATH.read_text(encoding="utf-8"))
        self.assertEqual(cfg["mode"], "cloud")
        self.assertEqual(cfg["channel_id"], cid)
        self.assertEqual(cfg["label"], "公司 Mac")
        self.assertIsNotNone(syncd.startup_gate())

        # the QR blob round-trips through parse_channel_qr with matching secrets
        parsed = e2e.parse_channel_qr(result["qr_blob"])
        self.assertEqual(parsed["channel_id"], cid)
        self.assertEqual(parsed["epoch"], epoch)
        self.assertEqual(parsed["key"], k_i)
        self.assertEqual(parsed["label"], "公司 Mac")
        self.assertEqual(syncd._b64url_encode(parsed["write_secret"]), write_text)
        # PNG rendered
        self.assertTrue(syncd.PAIRING_QR_PNG.exists())

    def test_init_channel_is_idempotent_stable_qr(self):
        ft = FakeTransport()
        with mock.patch.object(syncd, "HttpTransport", return_value=ft):
            first = syncd.init_channel("这台 Mac")
            second = syncd.init_channel("这台 Mac")
        # same channel_id + same QR blob (secrets are stable across re-pair)
        self.assertEqual(first["channel_id"], second["channel_id"])
        self.assertEqual(first["qr_blob"], second["qr_blob"])

    def test_init_channel_survives_register_failure(self):
        boom = mock.Mock()
        boom.insert.side_effect = OSError("network down")
        with mock.patch.object(syncd, "HttpTransport", return_value=boom):
            result = syncd.init_channel("这台 Mac")
        # QR + opt-in still produced; registered flagged False (retry --pair)
        self.assertFalse(result["registered"])
        self.assertTrue(syncd.SYNC_CONFIG_PATH.exists())
        self.assertTrue(result["qr_blob"])


# --------------------------------------------------------------------------- #
# self-heal channel registration (offline-at-pair recovery)
# --------------------------------------------------------------------------- #
class _DupError(Exception):
    """Mimics a PostgREST 409 unique-violation (channel already registered)."""
    code = 409


@unittest.skipUnless(HAVE_CRYPTO, "cryptography not installed (optional cloud dep)")
class SelfHealRegisterTestCase(unittest.TestCase):
    def setUp(self):
        _reset_state()
        e2e.save_pairing(_CHAN, _EPOCH, _K)
        _write_secret_files()
        config.DASHBOARD_PATH.write_bytes(_BOARD)

    @staticmethod
    def _channel_inserts(ft):
        return [(r, ws) for (t, r, ws) in ft.inserts if t == "channels"]

    def test_run_once_registers_channel_when_not_yet_registered(self):
        ft = FakeTransport()
        d = syncd.Syncd(_sync_cfg(label="公司 Mac"), ft)
        d.run_once()

        rows = self._channel_inserts(ft)
        self.assertEqual(len(rows), 1)
        row, write_secret = rows[0]
        self.assertEqual(row["channel_id"], _CHAN)
        self.assertEqual(write_secret, _WRITE_TEXT)   # x-sync-write carried
        self.assertEqual(row["write_secret_hash"],
                         hashlib.sha256(_WRITE_TEXT.encode("ascii")).hexdigest())
        # label is E2E-encrypted, never plaintext, and decrypts back
        self.assertEqual(
            e2e.decrypt_label(_K, _EPOCH, _CHAN,
                              syncd._from_bytea(row["label_enc"])), "公司 Mac")
        # a second pass must NOT re-attempt (idempotent success is cached)
        d.run_once()
        self.assertEqual(len(self._channel_inserts(ft)), 1)

    def test_duplicate_registration_is_treated_as_success(self):
        ft = FakeTransport(insert_error=_DupError())
        d = syncd.Syncd(_sync_cfg(label="X"), ft)
        d.run_once()   # 409 duplicate => already registered, must NOT crash
        d.run_once()   # cached as registered => no re-attempt
        self.assertEqual(len(self._channel_inserts(ft)), 1)
        # the rest of the pass still ran despite the register "error"
        self.assertEqual(len(ft.board_upserts()), 1)

    def test_transient_register_error_is_retried_next_pass(self):
        ft = FakeTransport(insert_error=RuntimeError("network down"))
        d = syncd.Syncd(_sync_cfg(label="X"), ft)
        d.run_once()   # attempt 1 — non-duplicate error => stays unregistered
        d.run_once()   # attempt 2 — retried because still not registered
        self.assertEqual(len(self._channel_inserts(ft)), 2)


# --------------------------------------------------------------------------- #
# missing capability / key => pause, never crash
# --------------------------------------------------------------------------- #
class PauseTestCase(unittest.TestCase):
    def setUp(self):
        _reset_state()

    def test_missing_write_secret_pauses_without_crashing(self):
        # no write_secret file, no pairing key
        ft = FakeTransport()
        d = syncd.Syncd(_sync_cfg(), ft)
        self.assertFalse(d._ensure_ready())
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

    @unittest.skipUnless(HAVE_CRYPTO, "cryptography not installed (optional cloud dep)")
    def test_missing_pairing_key_pauses(self):
        _write_secret_files()  # write cap present, but no pairing key
        ft = FakeTransport()
        d = syncd.Syncd(_sync_cfg(), ft)
        self.assertFalse(d._ensure_ready())
        self.assertIn("配对密钥", d._paused_reason)


if __name__ == "__main__":
    unittest.main()
