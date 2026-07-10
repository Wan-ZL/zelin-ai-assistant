"""act/radar_imessage.py — the §13 command surface over iMessage.

Everything runs against a FIXTURE sqlite db built in-test with the minimal
chat.db schema (chat / chat_handle_join / handle / chat_message_join /
message) — the real ~/Library/Messages/chat.db is NEVER touched. Sends are
captured by an injected fake runner; the LLM is an injected fake extractor.
All state lives under the sandbox AIASSISTANT_HOME (tests/__init__.py).

Contract under test:
(a) 批准/拒绝/打回/验收 R-xxx -> the SAME inbox decision files radar_slack
    writes; 打回 without feedback replies guidance and writes nothing;
(b) non-command text -> quick_capture (fake extractor) with the result
    replied back (🤖-prefixed);
(c) a 👍 (2001) tapback on a tracked outbound 🔔 notification approves that
    R-xxx exactly once; 👎 (2002) and untracked targets approve nothing;
(d) the marker advances past everything fetched; a second scan is a no-op;
(e) our own 🔔/🤖 posts, is_from_me=0 rows, and non-self chats are never
    captured; attributedBody-only messages still parse;
(f) db missing / locked / corrupt -> health-logged silent no-op; scan() never
    raises.
"""
import json
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - ensures the sandbox env is set first

from act import radar_imessage
from act.lib import config, health, notify

SELF = "+15551234567"
OTHER = "+19998887777"

_SCHEMA = """
CREATE TABLE handle (ROWID INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT, service TEXT);
CREATE TABLE chat (ROWID INTEGER PRIMARY KEY AUTOINCREMENT, guid TEXT, chat_identifier TEXT);
CREATE TABLE chat_handle_join (chat_id INTEGER, handle_id INTEGER);
CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER);
CREATE TABLE message (
    ROWID INTEGER PRIMARY KEY AUTOINCREMENT,
    guid TEXT, text TEXT, attributedBody BLOB,
    handle_id INTEGER DEFAULT 0, is_from_me INTEGER DEFAULT 1,
    date INTEGER DEFAULT 0,
    associated_message_type INTEGER DEFAULT 0,
    associated_message_guid TEXT);
"""


def _build_db(path: Path, with_handle_join: bool = True) -> sqlite3.Connection:
    """Fixture chat.db: chat 1 = the self thread, chat 2 = a normal DM."""
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA)
    conn.execute("INSERT INTO handle (id, service) VALUES (?, 'iMessage')", (SELF,))
    conn.execute("INSERT INTO handle (id, service) VALUES (?, 'iMessage')", (OTHER,))
    conn.execute("INSERT INTO chat (guid, chat_identifier) VALUES (?, ?)",
                 ("iMessage;-;" + SELF, SELF))
    conn.execute("INSERT INTO chat (guid, chat_identifier) VALUES (?, ?)",
                 ("iMessage;-;" + OTHER, OTHER))
    if with_handle_join:
        conn.execute("INSERT INTO chat_handle_join VALUES (1, 1)")
    conn.execute("INSERT INTO chat_handle_join VALUES (2, 2)")
    conn.commit()
    return conn


def _add_msg(conn, text=None, body=None, from_me=1, assoc_type=0,
             assoc_guid=None, guid=None, chat_id=1) -> int:
    cur = conn.execute(
        "INSERT INTO message (guid, text, attributedBody, is_from_me, "
        "associated_message_type, associated_message_guid) VALUES (?,?,?,?,?,?)",
        (guid or str(uuid.uuid4()).upper(), text, body, from_me,
         assoc_type, assoc_guid))
    rowid = cur.lastrowid
    conn.execute("INSERT INTO chat_message_join VALUES (?, ?)", (chat_id, rowid))
    conn.commit()
    return rowid


def _typedstream(text: str) -> bytes:
    """Minimal attributedBody blob the NSString-marker heuristic can read."""
    payload = text.encode("utf-8")
    if len(payload) < 0x80:
        ln = bytes([len(payload)])
    else:
        ln = b"\x81" + len(payload).to_bytes(2, "little")
    return (b"\x04\x0bstreamtyped\x81\xe8\x03\x84\x01@\x84\x84\x84"
            b"NSString\x01\x94\x84\x01+" + ln + payload + b"\x86")


class _FakeSender:
    """Injectable osascript send runner: records (handle, text)."""

    def __init__(self, returncode: int = 0):
        self.sent: list = []
        self.returncode = returncode

    def __call__(self, handle: str, text: str) -> subprocess.CompletedProcess:
        self.sent.append((handle, text))
        return subprocess.CompletedProcess(args=["osascript"],
                                           returncode=self.returncode)


class _FakeExtractor:
    """Injectable quick-capture LLM: records prompts, returns canned JSON."""

    def __init__(self, payload: dict):
        self.calls: list = []
        self.payload = payload

    def __call__(self, prompt: str) -> subprocess.CompletedProcess:
        self.calls.append(prompt)
        return subprocess.CompletedProcess(
            args=["claude"], returncode=0,
            stdout=json.dumps(self.payload, ensure_ascii=False))


class IMessageRadarTestCase(unittest.TestCase):
    def setUp(self):
        tmp = Path(tempfile.mkdtemp(prefix="imsg-test-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        self.db_path = tmp / "chat.db"
        self.conn = _build_db(self.db_path)
        self.addCleanup(self.conn.close)
        for p in (radar_imessage._marker_path(), radar_imessage._outbox_path(),
                  health.HEALTH_PATH):
            if p.exists():
                p.unlink()
        shutil.rmtree(config.INBOX_DIR, ignore_errors=True)
        self.cfg = config.Config()
        self.cfg.phone_channel = "imessage"
        self.cfg.imessage_self_handle = SELF
        self.sender = _FakeSender()

    def _scan(self, extractor=None):
        return radar_imessage.scan(self.cfg, extractor=extractor,
                                   send_runner=self.sender,
                                   db_path=self.db_path)

    def _inbox(self) -> list:
        if not config.INBOX_DIR.exists():
            return []
        return [json.loads(p.read_text(encoding="utf-8"))
                for p in sorted(config.INBOX_DIR.glob("*.json"))]

    def _health(self) -> dict:
        return json.loads(health.HEALTH_PATH.read_text(encoding="utf-8"))["imessage"]

    # -- (a) commands -> inbox decision files -------------------------------- #
    def test_approve_command_writes_inbox_decision(self):
        _add_msg(self.conn, text="批准 R-007")
        self.assertEqual(self._scan(), 1)
        inbox = self._inbox()
        self.assertEqual(len(inbox), 1)
        self.assertEqual(inbox[0]["action"], "approve")
        self.assertEqual(inbox[0]["id"], "R-007")
        self.assertIsNone(inbox[0]["comment"])
        self.assertEqual(len(self.sender.sent), 1)
        handle, text = self.sender.sent[0]
        self.assertEqual(handle, SELF)
        self.assertTrue(text.startswith("🤖"))
        self.assertIn("批准 R-007", text)
        self.assertIsNone(self._health()["skip_reason"])   # pass counted healthy

    def test_all_verbs_map_to_slack_actions(self):
        _add_msg(self.conn, text="拒绝 R-001")
        _add_msg(self.conn, text="验收 R-002")
        _add_msg(self.conn, text="打回 R-003 参数表要补上依据")
        self.assertEqual(self._scan(), 3)
        by_id = {d["id"]: d for d in self._inbox()}
        self.assertEqual(by_id["R-001"]["action"], "reject")
        self.assertEqual(by_id["R-002"]["action"], "accept")
        self.assertEqual(by_id["R-003"]["action"], "rework")
        self.assertEqual(by_id["R-003"]["comment"], "参数表要补上依据")

    def test_rework_without_feedback_replies_guidance_and_writes_nothing(self):
        _add_msg(self.conn, text="打回 R-003")
        self._scan()
        self.assertEqual(self._inbox(), [])
        self.assertEqual(len(self.sender.sent), 1)
        self.assertIn("打回需要带反馈", self.sender.sent[0][1])

    # -- (b) capture path ----------------------------------------------------- #
    def test_non_command_text_goes_to_quick_capture(self):
        _add_msg(self.conn, text="研究一下 example-bench 的报告导出格式")
        extractor = _FakeExtractor({"action": "ignore", "reason": "闲聊"})
        self.assertEqual(self._scan(extractor=extractor), 1)
        self.assertEqual(len(extractor.calls), 1)
        self.assertIn("研究一下 example-bench 的报告导出格式", extractor.calls[0])
        self.assertEqual(self._inbox(), [])                 # no approval written
        self.assertIn("先不建卡", self.sender.sent[0][1])   # capture reply relayed

    def test_attributed_body_fallback_parses_command(self):
        _add_msg(self.conn, text=None, body=_typedstream("批准 R-020"))
        self.assertEqual(self._scan(), 1)
        inbox = self._inbox()
        self.assertEqual(inbox[0]["action"], "approve")
        self.assertEqual(inbox[0]["id"], "R-020")

    def test_decode_attributed_body_long_string(self):
        text = "打" * 200   # forces the 0x81 + uint16le length encoding
        self.assertEqual(radar_imessage.decode_attributed_body(_typedstream(text)),
                         text)
        self.assertEqual(radar_imessage.decode_attributed_body(None), "")
        self.assertEqual(radar_imessage.decode_attributed_body(b"garbage"), "")

    # -- (c) tapback approvals ------------------------------------------------- #
    def _mirror_with_tapback(self, req: str, tapback_type: int,
                             tracked: bool = True) -> str:
        if tracked:
            radar_imessage.record_outbox(req)
        guid = str(uuid.uuid4()).upper()
        _add_msg(self.conn, text=f"🔔 有新需求待审批\n本周周报\n#{req}", guid=guid)
        _add_msg(self.conn, assoc_type=tapback_type, assoc_guid=f"p:0/{guid}")
        return guid

    def test_thumbsup_tapback_on_tracked_notification_approves_once(self):
        guid = self._mirror_with_tapback("R-012", 2001)
        self.assertEqual(self._scan(), 1)
        inbox = self._inbox()
        self.assertEqual(len(inbox), 1)
        self.assertEqual(inbox[0], {**inbox[0], "action": "approve", "id": "R-012"})
        self.assertIn("已批准 R-012", self.sender.sent[0][1])
        self.assertTrue(radar_imessage.load_outbox()[0].get("consumed"))
        # a second 👍 on the same notification must NOT approve again
        _add_msg(self.conn, assoc_type=2001, assoc_guid=f"p:0/{guid}")
        self._scan()
        self.assertEqual(len(self._inbox()), 1)

    def test_negative_tapback_does_not_approve(self):
        self._mirror_with_tapback("R-013", 2002)   # 👎
        self.assertEqual(self._scan(), 0)
        self.assertEqual(self._inbox(), [])
        self.assertFalse(radar_imessage.load_outbox()[0].get("consumed"))

    def test_tapback_on_untracked_notification_does_not_approve(self):
        self._mirror_with_tapback("R-099", 2001, tracked=False)
        self.assertEqual(self._scan(), 0)
        self.assertEqual(self._inbox(), [])

    # -- (d) marker discipline -------------------------------------------------- #
    def test_marker_advances_and_second_scan_is_noop(self):
        last = _add_msg(self.conn, text="批准 R-030")
        self._scan()
        self.assertEqual(radar_imessage.load_marker(), last)
        sends = len(self.sender.sent)
        extractor = _FakeExtractor({"action": "ignore", "reason": "x"})
        self.assertEqual(self._scan(extractor=extractor), 0)
        self.assertEqual(len(self.sender.sent), sends)      # nothing re-handled
        self.assertEqual(extractor.calls, [])
        self.assertEqual(len(self._inbox()), 1)

    # -- (e) skip rules ----------------------------------------------------------- #
    def test_own_mirrors_incoming_rows_and_other_chats_are_skipped(self):
        _add_msg(self.conn, text="🔔 有新需求待审批\nxxx\n#R-050")   # our mirror
        _add_msg(self.conn, text="🤖 收到：批准 R-050（已写入处理队列）")
        _add_msg(self.conn, text="批准 R-051", from_me=0)   # self-thread echo edge
        last = _add_msg(self.conn, text="批准 R-052", chat_id=2)  # NOT the self chat
        extractor = _FakeExtractor({"action": "ignore", "reason": "x"})
        self.assertEqual(self._scan(extractor=extractor), 0)
        self.assertEqual(self._inbox(), [])
        self.assertEqual(extractor.calls, [])               # never quick-captured
        self.assertEqual(self.sender.sent, [])
        # marker still advanced past the skipped self-thread rows
        self.assertEqual(radar_imessage.load_marker(), last - 1)

    def test_self_chat_found_via_chat_identifier_without_handle_join(self):
        tmp = Path(tempfile.mkdtemp(prefix="imsg-test2-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        db = tmp / "chat.db"
        conn = _build_db(db, with_handle_join=False)
        self.addCleanup(conn.close)
        _add_msg(conn, text="批准 R-060")
        n = radar_imessage.scan(self.cfg, send_runner=self.sender, db_path=db)
        self.assertEqual(n, 1)
        self.assertEqual(self._inbox()[0]["id"], "R-060")

    # -- (f) failure modes: silent health-logged no-ops --------------------------- #
    def test_channel_off_skips_disabled(self):
        self.cfg.phone_channel = "none"
        self.assertEqual(self._scan(), 0)
        self.assertEqual(self._health()["skip_reason"], "disabled")

    def test_missing_self_handle_skips(self):
        self.cfg.imessage_self_handle = "  "
        self.assertEqual(self._scan(), 0)
        self.assertEqual(self._health()["skip_reason"], "no_self_handle")

    def test_db_missing_is_silent_noop(self):
        n = radar_imessage.scan(self.cfg, send_runner=self.sender,
                                db_path=self.db_path.with_name("nope.db"))
        self.assertEqual(n, 0)
        self.assertEqual(self._health()["skip_reason"], "db_missing")
        self.assertEqual(self.sender.sent, [])

    def test_non_darwin_default_db_skips_platform_unsupported(self):
        # PORTING.md: this channel is darwin-exclusive — off macOS an ENABLED
        # channel must classify itself, not limp into db_missing. Only the
        # default chat.db path is guarded; the injected-db tests above stay
        # cross-platform.
        with mock.patch("sys.platform", "linux"):
            n = radar_imessage.scan(self.cfg, send_runner=self.sender)
        self.assertEqual(n, 0)
        self.assertEqual(self._health()["skip_reason"], "platform_unsupported")
        self.assertEqual(self.sender.sent, [])

    def test_db_locked_is_silent_noop(self):
        _add_msg(self.conn, text="批准 R-070")
        orig = radar_imessage._DB_TIMEOUT_S
        radar_imessage._DB_TIMEOUT_S = 0.05   # don't stall the suite on the busy wait
        self.addCleanup(setattr, radar_imessage, "_DB_TIMEOUT_S", orig)
        self.conn.execute("BEGIN EXCLUSIVE")
        try:
            self.assertEqual(self._scan(), 0)
        finally:
            self.conn.rollback()
        self.assertTrue(self._health()["skip_reason"].startswith("db_"))
        self.assertEqual(self._inbox(), [])
        self.assertEqual(radar_imessage.load_marker(), 0)   # untouched -> retried

    def test_corrupt_db_never_raises(self):
        self.db_path.with_name("bad.db").write_bytes(b"this is not a sqlite file")
        n = radar_imessage.scan(self.cfg, send_runner=self.sender,
                                db_path=self.db_path.with_name("bad.db"))
        self.assertEqual(n, 0)
        self.assertTrue(self._health()["skip_reason"].startswith("db_"))

    def test_one_bad_message_does_not_kill_the_pass(self):
        _add_msg(self.conn, text="批准 R-080")
        _add_msg(self.conn, text="批准 R-081")
        calls = {"n": 0}
        orig = radar_imessage._handle_self_message

        def boom_once(m, cfg, reply, extractor=None):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("boom")
            return orig(m, cfg, reply, extractor=extractor)

        radar_imessage._handle_self_message = boom_once
        self.addCleanup(setattr, radar_imessage, "_handle_self_message", orig)
        self.assertEqual(self._scan(), 1)                   # second one survived
        self.assertEqual(self._inbox()[0]["id"], "R-081")

    # -- notify mirror -> outbox --------------------------------------------------- #
    def test_imessage_notify_sends_and_records_outbox(self):
        ok = notify.imessage_notify("有新需求待审批", "本周周报",
                                    req="R-090", cfg=self.cfg, runner=self.sender)
        self.assertTrue(ok)
        handle, text = self.sender.sent[0]
        self.assertEqual(handle, SELF)
        self.assertTrue(text.startswith("🔔 有新需求待审批"))
        self.assertIn("#R-090", text)
        items = radar_imessage.load_outbox()
        self.assertEqual(items[0]["req"], "R-090")
        self.assertFalse(items[0].get("consumed"))

    def test_imessage_notify_requires_channel_and_handle(self):
        self.cfg.phone_channel = "slack"
        self.assertFalse(notify.imessage_notify("t", "b", cfg=self.cfg,
                                                runner=self.sender))
        self.cfg.phone_channel = "imessage"
        self.cfg.imessage_self_handle = None
        self.assertFalse(notify.imessage_notify("t", "b", cfg=self.cfg,
                                                runner=self.sender))
        self.assertEqual(self.sender.sent, [])

    def test_imessage_notify_send_failure_records_nothing(self):
        failing = _FakeSender(returncode=1)
        self.assertFalse(notify.imessage_notify("t", "b", req="R-091",
                                                cfg=self.cfg, runner=failing))
        self.assertEqual(radar_imessage.load_outbox(), [])


if __name__ == "__main__":
    unittest.main()
