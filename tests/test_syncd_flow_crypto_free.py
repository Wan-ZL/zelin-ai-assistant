"""syncd 的 DOWN / UP / ack-tail / 自愈注册 / --pair 主线，**不依赖 cryptography**。

tests/test_syncd.py 用真 AEAD 钉密码学正确性，但 CI 的 canonical 腿（qa-gates，
只装 pyyaml + coverage）与 ubuntu 测试腿都没有 cryptography，那些用例整组
skip——syncd 的守护逻辑在判卷环境里此前是 0 覆盖（P3a 审计）。本文件把
e2e 的四个密文原语换成可逆的假实现（AEAD 的「认证」由 fake 的显式 BAD 标记
模拟），只测 syncd 自己的行为：change-gate、seq 种子、M4 stage→ledger→replace
顺序、L3 去重、shape 闸、via:"remote" 强制落款、ack 游标只在 PATCH 成功后
推进、注册自愈、--pair 的两种输出、startup gate。契约：CONTRACT §5.2 / §5.4
（syncd 两文件契约）。
"""
from __future__ import annotations

import io
import json
import os
import unittest
from contextlib import redirect_stdout
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first
from tests.test_syncd import (_BOARD, _CHAN, _EPOCH, _K, _WRITE_TEXT,
                              FakeTransport, _reset_state, _sync_cfg,
                              _write_secret_files)

from act import syncd
from act.lib import config, e2e

_NONCE = b"N" * 12


def _fake_encrypt_board(k, epoch, cid, seq, raw):
    assert (k, epoch, cid) == (_K, _EPOCH, _CHAN)
    return _NONCE + raw


def _fake_embedded_nonce(blob):
    return blob[:len(_NONCE)]


def _fake_decrypt_action(k, epoch, cid, aid, board_seq, blob):
    if blob.startswith(b"BAD"):
        raise ValueError("authentication failed")
    return blob


def _fake_encrypt_label(k, epoch, cid, label):
    return ("label:" + label).encode("utf-8")


class _CryptoFree(unittest.TestCase):
    """Reset state, persist write secret + pairing key, stub the AEAD primitives."""

    def setUp(self):
        _reset_state()
        _write_secret_files()
        e2e.save_pairing(_CHAN, _EPOCH, _K)
        for name, fake in (("encrypt_board", _fake_encrypt_board),
                           ("embedded_nonce", _fake_embedded_nonce),
                           ("decrypt_action", _fake_decrypt_action),
                           ("encrypt_label", _fake_encrypt_label)):
            p = mock.patch.object(e2e, name, fake)
            p.start()
            self.addCleanup(p.stop)

    @staticmethod
    def _pending(aid: str, payload: dict, board_seq=7) -> dict:
        return {"action_id": aid, "board_seq": board_seq,
                "payload_enc": syncd._to_bytea(json.dumps(payload).encode("utf-8"))}


class DownFlowTestCase(_CryptoFree):
    def test_first_push_shape_and_state(self):
        config.DASHBOARD_PATH.write_bytes(_BOARD)
        ft = FakeTransport(board_rows=[{"seq": 4}])
        d = syncd.Syncd(_sync_cfg(), ft)
        self.assertTrue(d.run_once() is None)
        [(row, ws)] = ft.board_upserts()
        self.assertEqual(ws, _WRITE_TEXT)
        self.assertEqual(row["seq"], 5)          # max(server 4, local 0) + 1
        self.assertEqual(row["channel_id"], _CHAN)
        self.assertEqual(row["alg"], syncd._ALG)
        self.assertEqual(syncd._from_bytea(row["nonce"]), _NONCE)
        self.assertEqual(syncd._from_bytea(row["payload_enc"]), _NONCE + _BOARD)
        self.assertNotIn("updated_at", row)
        st = json.loads(syncd.DOWN_STATE_PATH.read_text(encoding="utf-8"))
        self.assertEqual(st["seq"], 5)
        self.assertEqual(st["hash"], syncd._gate_digest(_BOARD))

    def test_unchanged_board_and_generated_at_only_change_push_nothing(self):
        config.DASHBOARD_PATH.write_bytes(_BOARD)
        ft = FakeTransport()
        d = syncd.Syncd(_sync_cfg(), ft)
        self.assertTrue(d._ensure_ready())
        self.assertTrue(d.push_down_if_changed())
        self.assertFalse(d.push_down_if_changed())
        config.DASHBOARD_PATH.write_bytes(_BOARD.replace(b"2026-07-12", b"2026-07-13"))
        self.assertFalse(d.push_down_if_changed())
        self.assertEqual(len(ft.board_upserts()), 1)

    def test_upsert_failure_does_not_advance_state(self):
        config.DASHBOARD_PATH.write_bytes(_BOARD)
        ft = FakeTransport()
        ft.upsert = mock.Mock(side_effect=OSError("offline"))
        d = syncd.Syncd(_sync_cfg(), ft)
        self.assertTrue(d._ensure_ready())
        self.assertFalse(d.push_down_if_changed())
        self.assertFalse(syncd.DOWN_STATE_PATH.exists())
        self.assertIsNone(d._last_hash)
        # next pass retries the same seq
        ft.upsert = mock.Mock()
        self.assertTrue(d.push_down_if_changed())
        self.assertEqual(ft.upsert.call_args[0][1]["seq"], 1)

    def test_missing_dashboard_is_a_quiet_no_push(self):
        d = syncd.Syncd(_sync_cfg(), FakeTransport())
        self.assertFalse(d.push_down_if_changed())

    def test_seed_survives_server_read_failure_and_local_wins(self):
        syncd._atomic_write_json(syncd.DOWN_STATE_PATH, {"seq": 9, "hash": "x"})
        ft = FakeTransport()
        ft.select = mock.Mock(side_effect=OSError("offline"))
        d = syncd.Syncd(_sync_cfg(), ft)
        d._seed_seq()
        self.assertEqual(d._next_seq, 10)
        d._seed_seq()   # idempotent
        self.assertEqual(ft.select.call_count, 1)


class UpFlowTestCase(_CryptoFree):
    def _daemon(self, rows):
        ft = FakeTransport(inbox_rows=rows)
        d = syncd.Syncd(_sync_cfg(), ft)
        self.assertTrue(d._ensure_ready())
        return d, ft

    def test_pending_action_materialises_with_remote_stamp(self):
        d, ft = self._daemon([self._pending("a1", {"action": "approve", "id": "R-1",
                                                   "via": "web"})])
        self.assertEqual(d.pull_up(), 1)
        rec = json.loads((config.INBOX_DIR / "a1.json").read_text(encoding="utf-8"))
        self.assertEqual(rec["via"], "remote")      # spoofed via overwritten
        self.assertEqual(rec["board_seq"], 7)
        self.assertRegex(rec["ts"], r"^\d{4}-\d{2}-\d{2}T")
        self.assertEqual([p[2]["status"] for p in ft.patches], ["delivered"])
        ledger = syncd.DELIVERED_LEDGER.read_text(encoding="utf-8").splitlines()
        self.assertEqual(json.loads(ledger[0])["action_id"], "a1")

    def test_duplicate_action_id_is_one_file_and_repatches(self):
        d, ft = self._daemon([self._pending("a1", {"action": "approve", "id": "R-1"})])
        self.assertEqual(d.pull_up(), 1)
        self.assertEqual(d.pull_up(), 0)
        self.assertEqual(len(list(config.INBOX_DIR.glob("a1*.json"))), 1)
        self.assertEqual(len(ft.patches), 2)

    def test_bad_blob_non_json_no_action_and_bad_shape_are_skipped(self):
        rows = [
            {"action_id": "bad", "board_seq": 1, "payload_enc": syncd._to_bytea(b"BAD!")},
            {"action_id": "txt", "board_seq": 1, "payload_enc": syncd._to_bytea(b"not json")},
            self._pending("noact", {"id": "R-1"}),
            self._pending("shape", {"action": "comment", "id": "R-1", "comment": {"x": 1}}),
            self._pending("ids", {"action": "merge_review", "ids": ["R-1", 2]}),
            {"action_id": "", "board_seq": 1, "payload_enc": syncd._to_bytea(b"{}")},
        ]
        d, ft = self._daemon(rows)
        self.assertEqual(d.pull_up(), 0)
        self.assertEqual(list(config.INBOX_DIR.glob("*.json")), [])
        self.assertEqual(ft.patches, [])
        self.assertFalse(syncd.DELIVERED_LEDGER.exists())

    def test_stage_failure_keeps_action_pending_and_unledgered(self):
        d, ft = self._daemon([self._pending("a1", {"action": "approve", "id": "R-1"})])
        with mock.patch.object(syncd.Path, "write_text", side_effect=OSError("disk full")):
            self.assertEqual(d.pull_up(), 0)
        self.assertFalse(syncd.DELIVERED_LEDGER.exists())
        self.assertEqual(ft.patches, [])

    def test_finalise_failure_after_ledger_drops_not_double_applies(self):
        d, ft = self._daemon([self._pending("a1", {"action": "approve", "id": "R-1"})])
        with mock.patch.object(syncd.os, "replace", side_effect=OSError("boom")):
            self.assertEqual(d.pull_up(), 0)
        self.assertIn("a1", syncd.DELIVERED_LEDGER.read_text(encoding="utf-8"))
        self.assertEqual(list(config.INBOX_DIR.glob("a1.json")), [])
        self.assertEqual(ft.patches, [])
        # a re-pull sees the ledger and only re-patches delivered
        self.assertEqual(d.pull_up(), 0)
        self.assertEqual([p[2]["status"] for p in ft.patches], ["delivered"])

    def test_pull_failure_is_zero_not_an_exception(self):
        ft = FakeTransport()
        ft.select = mock.Mock(side_effect=OSError("offline"))
        d = syncd.Syncd(_sync_cfg(), ft)
        self.assertEqual(d.pull_up(), 0)

    def test_inbox_record_shape_rules(self):
        self.assertIsNone(syncd._inbox_record("x", ["not", "dict"], None))
        self.assertIsNone(syncd._inbox_record("x", {"action": ""}, None))
        rec = syncd._inbox_record("x", {"action": "approve", "id": "R-1", "board_seq": 3,
                                        "ts": "2026-01-01T00:00:00Z"}, 9)
        self.assertEqual(rec["board_seq"], 3)        # payload's own wins
        self.assertEqual(rec["ts"], "2026-01-01T00:00:00Z")
        self.assertEqual(rec["via"], "remote")
        self.assertIsNone(syncd._inbox_shape_error({"ids": ["a", "b"], "action": "x"}))
        self.assertIn("ids", syncd._inbox_shape_error({"ids": "a"}) or "")

    def test_delivered_set_tolerates_junk_lines(self):
        syncd._ensure_sync_dir()
        syncd.DELIVERED_LEDGER.write_text(
            '{"action_id": "a"}\n\nnot json\n{"ts": "x"}\n{"action_id": "b"}\n',
            encoding="utf-8")
        d = syncd.Syncd(_sync_cfg(), FakeTransport())
        self.assertEqual(d._delivered_set(), {"a", "b"})


class AckTailTestCase(_CryptoFree):
    def _applied(self, *lines: str) -> None:
        syncd._ensure_sync_dir()
        with syncd.APPLIED_LEDGER.open("a", encoding="utf-8") as fh:
            fh.write("".join(ln + "\n" for ln in lines))

    def test_acks_patch_and_advance_cursor(self):
        self._applied(json.dumps({"action_id": "a1", "result_status": "approved"}),
                      "garbage line",
                      json.dumps({"ts": "no id here"}))
        ft = FakeTransport()
        d = syncd.Syncd(_sync_cfg(), ft)
        d._ensure_ready()
        self.assertEqual(d.ack_tail(), 1)
        self.assertEqual(ft.patches[0][2]["status"], "applied")
        self.assertEqual(ft.patches[0][2]["result_status"], "approved")
        cursor = json.loads(syncd.APPLIED_CURSOR_PATH.read_text(encoding="utf-8"))
        self.assertEqual(cursor["offset"], syncd.APPLIED_LEDGER.stat().st_size)
        self.assertEqual(d.ack_tail(), 0)   # nothing new

    def test_patch_failure_stops_the_tail_before_the_line(self):
        self._applied(json.dumps({"action_id": "a1", "result_status": "approved"}),
                      json.dumps({"action_id": "a2", "result_status": "rejected"}))
        ft = FakeTransport()
        calls = []

        def flaky(table, params, patch, ws):
            calls.append(params["action_id"])
            if params["action_id"] == "eq.a2":
                raise OSError("offline")

        ft.patch = flaky
        d = syncd.Syncd(_sync_cfg(), ft)
        d._ensure_ready()
        self.assertEqual(d.ack_tail(), 1)
        first_line_end = len(syncd.APPLIED_LEDGER.read_text(encoding="utf-8").splitlines()[0]) + 1
        cursor = json.loads(syncd.APPLIED_CURSOR_PATH.read_text(encoding="utf-8"))
        self.assertEqual(cursor["offset"], first_line_end)
        # retried next pass
        ft.patch = lambda *a: None
        self.assertEqual(d.ack_tail(), 1)

    def test_incomplete_trailing_line_waits(self):
        syncd._ensure_sync_dir()
        syncd.APPLIED_LEDGER.write_text('{"action_id": "a1"', encoding="utf-8")
        d = syncd.Syncd(_sync_cfg(), FakeTransport())
        d._ensure_ready()
        self.assertEqual(d.ack_tail(), 0)
        self.assertEqual(list(syncd._complete_lines(syncd.APPLIED_LEDGER, 10 ** 6)), [])


class PauseResumeTestCase(_CryptoFree):
    def test_pause_then_resume_rewrites_status(self):
        syncd.WRITE_SECRET_PATH.unlink()
        d = syncd.Syncd(_sync_cfg(), FakeTransport())
        self.assertFalse(d._ensure_ready())
        self.assertTrue(json.loads(syncd.STATUS_PATH.read_text(encoding="utf-8"))["paused"])
        d._ensure_ready()   # same reason twice: one log line, no second write needed
        _write_secret_files()
        self.assertTrue(d._ensure_ready())
        status = json.loads(syncd.STATUS_PATH.read_text(encoding="utf-8"))
        self.assertFalse(status["paused"])
        self.assertEqual(status["channel_id"], _CHAN)
        d._resume()   # idempotent when not paused
        self.assertIsNone(d._paused_reason)

    def test_corrupt_secret_pauses_with_its_own_reason(self):
        syncd.WRITE_SECRET_PATH.write_text("short\n", encoding="utf-8")
        d = syncd.Syncd(_sync_cfg(), FakeTransport())
        self.assertFalse(d._ensure_ready())
        self.assertIn("已损坏", d._paused_reason)

    def test_status_write_failure_is_swallowed(self):
        syncd.WRITE_SECRET_PATH.unlink()
        d = syncd.Syncd(_sync_cfg(), FakeTransport())
        with mock.patch.object(syncd, "_atomic_write_json", side_effect=OSError("ro")):
            self.assertFalse(d._ensure_ready())
            _write_secret_files()
            self.assertTrue(d._ensure_ready())


class SelfHealRegisterTestCase(_CryptoFree):
    def test_registers_once_with_write_header(self):
        ft = FakeTransport()
        d = syncd.Syncd(_sync_cfg(label="桌上的 Mac"), ft)
        d.run_once()
        d.run_once()
        self.assertEqual(len(ft.inserts), 1)
        table, row, ws = ft.inserts[0]
        self.assertEqual((table, ws), ("channels", _WRITE_TEXT))
        self.assertEqual(row["write_secret_hash"], syncd._write_secret_hash(_WRITE_TEXT))
        self.assertEqual(syncd._from_bytea(row["label_enc"]), b"label:\xe6\xa1\x8c\xe4\xb8\x8a\xe7\x9a\x84 Mac")

    def test_duplicate_is_success_and_transient_error_retries(self):
        dup = OSError("409 Conflict duplicate key")
        ft = FakeTransport(insert_error=dup)
        d = syncd.Syncd(_sync_cfg(), ft)
        d.run_once()
        self.assertTrue(d._channel_registered)
        ft2 = FakeTransport(insert_error=OSError("timeout"))
        d2 = syncd.Syncd(_sync_cfg(), ft2)
        d2.run_once()
        d2.run_once()
        self.assertFalse(d2._channel_registered)
        self.assertEqual(len(ft2.inserts), 2)


class PairAndMainTestCase(_CryptoFree):
    def _pair(self, *argv) -> "tuple[int, str]":
        buf = io.StringIO()
        with mock.patch.object(syncd, "HttpTransport", return_value=FakeTransport()), \
                mock.patch.object(syncd, "_open_file"), redirect_stdout(buf):
            rc = syncd.main(["--pair", *argv])
        return rc, buf.getvalue()

    def test_pair_json_is_exactly_one_object(self):
        rc, out = self._pair("--json", "--label", "  工位 Mac ")
        self.assertEqual(rc, 0)
        doc = json.loads(out)
        self.assertEqual(doc["label"], "工位 Mac")
        self.assertTrue(doc["registered"])
        self.assertEqual(out.count("\n"), 1)
        cfg = json.loads(syncd.SYNC_CONFIG_PATH.read_text(encoding="utf-8"))
        self.assertEqual(cfg["label"], "工位 Mac")

    def test_bare_repair_keeps_label_then_defaults(self):
        self._pair("--json", "--label", "自定义")
        _rc, out = self._pair("--json")
        self.assertEqual(json.loads(out)["label"], "自定义")
        _reset_state()
        _write_secret_files()
        _rc, out = self._pair("--json")
        self.assertEqual(json.loads(out)["label"], "这台 Mac")

    def test_pair_human_output_mentions_channel_and_png(self):
        rc, out = self._pair()
        self.assertEqual(rc, 0)
        self.assertIn("channel_id:", out)
        self.assertIn("配对 blob", out)

    def test_pair_human_output_warns_when_unregistered(self):
        boom = mock.Mock()
        boom.insert.side_effect = OSError("down")
        buf = io.StringIO()
        with mock.patch.object(syncd, "HttpTransport", return_value=boom), \
                mock.patch.object(syncd, "_open_file"), redirect_stdout(buf):
            self.assertEqual(syncd.main(["--pair"]), 0)
        self.assertIn("频道注册请求未成功", buf.getvalue())

    def test_consent_text_and_disable(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(syncd.main(["--consent-text"]), 0)
        self.assertIn("默认关闭", buf.getvalue())
        syncd._atomic_write_json(syncd.SYNC_CONFIG_PATH, _sync_cfg())
        with redirect_stdout(io.StringIO()):
            self.assertEqual(syncd.main(["--disable"]), 0)
        self.assertIsNone(syncd.startup_gate())

    def test_once_runs_a_pass_and_swallows_failures(self):
        syncd._atomic_write_json(syncd.SYNC_CONFIG_PATH, _sync_cfg())
        ft = FakeTransport()
        with mock.patch.object(syncd, "_default_transport", return_value=ft):
            self.assertEqual(syncd.main(["--once"]), 0)
        self.assertEqual(len(ft.inserts), 1)
        with mock.patch.object(syncd.Syncd, "run_once", side_effect=RuntimeError("x")):
            self.assertEqual(syncd.main(["--once"]), 0)

    def test_not_opted_in_exits_zero_before_any_transport(self):
        with mock.patch.object(syncd, "_default_transport",
                               side_effect=AssertionError("must not build transport")):
            self.assertEqual(syncd.main([]), 0)

    def test_open_file_is_darwin_only_and_never_raises(self):
        with mock.patch.object(syncd.sys, "platform", "linux"), \
                mock.patch.object(syncd.subprocess, "run",
                                  side_effect=AssertionError("no spawn")):
            syncd._open_file("/x.png")
        with mock.patch.object(syncd.sys, "platform", "darwin"), \
                mock.patch.object(syncd.subprocess, "run", side_effect=OSError("no open")):
            syncd._open_file("/x.png")


class SecretPersistenceTestCase(unittest.TestCase):
    def setUp(self):
        _reset_state()

    def test_corrupt_write_secret_rotates_channel(self):
        syncd._ensure_sync_dir()
        syncd.CHANNEL_ID_PATH.write_text(_CHAN + "\n", encoding="utf-8")
        syncd.WRITE_SECRET_PATH.write_text("short\n", encoding="utf-8")
        val = syncd._load_or_create_write_secret()
        self.assertTrue(syncd._valid_write_secret(val))
        self.assertFalse(syncd.CHANNEL_ID_PATH.exists())
        self.assertNotEqual(syncd._load_or_create_channel_id(), _CHAN)

    def test_valid_secret_and_channel_are_stable(self):
        a = syncd._load_or_create_write_secret()
        self.assertEqual(syncd._load_or_create_write_secret(), a)
        cid = syncd._load_or_create_channel_id()
        self.assertEqual(syncd._load_or_create_channel_id(), cid)
        if os.name == "posix":
            self.assertEqual(syncd.WRITE_SECRET_PATH.stat().st_mode & 0o777, 0o600)

    def test_corrupt_channel_id_is_replaced(self):
        syncd._ensure_sync_dir()
        syncd.CHANNEL_ID_PATH.write_text("not-a-uuid\n", encoding="utf-8")
        self.assertIsNone(syncd._existing_channel_id())
        cid = syncd._load_or_create_channel_id()
        self.assertEqual(syncd._existing_channel_id(), cid)

    def test_chmod_failure_is_tolerated(self):
        with mock.patch.object(syncd.os, "chmod", side_effect=OSError("ntfs")):
            syncd._persist_secret(syncd.CHANNEL_ID_PATH, "x")
        self.assertEqual(syncd.CHANNEL_ID_PATH.read_text(encoding="utf-8"), "x\n")

    def test_bytea_and_b64url_round_trip(self):
        self.assertEqual(syncd._from_bytea(syncd._to_bytea(b"\x00\xff")), b"\x00\xff")
        self.assertEqual(syncd._from_bytea(b"\x01"), b"\x01")
        self.assertEqual(syncd._from_bytea("00ff"), b"\x00\xff")
        self.assertEqual(syncd._b64url_decode(syncd._b64url_encode(b"\x01" * 32)), b"\x01" * 32)

    def test_duplicate_error_detection(self):
        err = OSError("x")
        err.code = 409
        self.assertTrue(syncd._is_duplicate_error(err))
        self.assertTrue(syncd._is_duplicate_error(ValueError("23505 unique_violation")))
        self.assertFalse(syncd._is_duplicate_error(ValueError("timeout")))


class HttpTransportShapeTestCase(unittest.TestCase):
    """No network: assert the Request objects the transport builds."""

    def setUp(self):
        self.t = syncd.HttpTransport("https://x.supabase.co/", "anon", _CHAN)
        self.sent = []

        def capture(req):
            self.sent.append(req)
            return b"[]"
        p = mock.patch.object(syncd.HttpTransport, "_send", staticmethod(capture))
        p.start()
        self.addCleanup(p.stop)

    def test_select_get_without_write_header(self):
        self.assertEqual(self.t.select("inbox_actions", {"status": "eq.pending"}), [])
        req = self.sent[0]
        self.assertEqual(req.get_method(), "GET")
        self.assertIn("status=eq.pending", req.full_url)
        self.assertEqual(req.get_header("X-sync-channel"), _CHAN)
        self.assertFalse(req.has_header("X-sync-write"))

    def test_writes_carry_secret_and_prefer_headers(self):
        self.t.insert("channels", {"a": 1}, "ws")
        self.t.upsert("board_snapshots", {"a": 1}, "channel_id", "ws")
        self.t.patch("inbox_actions", {"action_id": "eq.a"}, {"status": "x"}, "ws")
        methods = [r.get_method() for r in self.sent]
        self.assertEqual(methods, ["POST", "POST", "PATCH"])
        for r in self.sent:
            self.assertEqual(r.get_header("X-sync-write"), "ws")
        self.assertIn("merge-duplicates", self.sent[1].get_header("Prefer"))
        self.assertIn("on_conflict=channel_id", self.sent[1].full_url)

    def test_non_list_select_body_reads_as_empty(self):
        with mock.patch.object(syncd.HttpTransport, "_send",
                               staticmethod(lambda req: b'{"error": 1}')):
            self.assertEqual(self.t.select("x", {}), [])


if __name__ == "__main__":
    unittest.main()
