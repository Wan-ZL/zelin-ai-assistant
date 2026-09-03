"""act/radar_gmail — the IMAP fetch primitives under ``fetch_new_messages``
(§14): SEARCH → uid list, FETCH → literal, and the §14bis command-item
mapping.

Pinned (P3 mutation net):
- ``_search_unseen``: the search asks for ``UNSEEN UID <marker+1>:*``; an
  IMAP/OS error, a non-OK status or an empty result -> []; non-numeric uids
  dropped; uids at/below the marker dropped (the ``n:*`` quirk);
- ``_fetch_message``: the FETCH raising -> None (the caller must NOT advance
  the marker past that uid), and the exact data items requested;
- ``_fetched_literal`` / ``_first_literal``: non-OK / empty / tuple-less /
  empty literal -> None;
- ``_command_message``: non-dict or bad uid -> (None, None) (skipped
  entirely); at/below marker or pre-filtered -> (uid, None) (marker still
  advances); otherwise the message dict with truncated body and stripped ids;
- ``_command_argv`` / ``_fetcher_stdout``: unparseable or empty command,
  a crashing / non-zero fetcher -> None.
"""
import imaplib
import shlex
import sys
import unittest

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import radar_gmail


class _Conn:
    def __init__(self, search=None, search_exc=None, fetch=None, fetch_exc=None):
        self.search = search
        self.search_exc = search_exc
        self.fetch = fetch or {}
        self.fetch_exc = fetch_exc
        self.calls = []

    def uid(self, cmd, *args):
        self.calls.append((cmd,) + args)
        if cmd == "search":
            if self.search_exc:
                raise self.search_exc
            return self.search
        if self.fetch_exc:
            raise self.fetch_exc
        return self.fetch[int(args[0])]


class SearchUnseenTestCase(unittest.TestCase):
    def test_query_shape_and_filtering(self):
        conn = _Conn(search=("OK", [b"3 7 abc 12 15"]))
        self.assertEqual(radar_gmail._search_unseen(conn, 7), [12, 15])
        self.assertEqual(conn.calls, [("search", None, "UNSEEN", "UID 8:*")])

    def test_failures_are_empty(self):
        self.assertEqual(radar_gmail._search_unseen(
            _Conn(search_exc=imaplib.IMAP4.error("boom")), 0), [])
        self.assertEqual(radar_gmail._search_unseen(_Conn(search_exc=OSError()), 0), [])
        self.assertEqual(radar_gmail._search_unseen(_Conn(search=("NO", [b"1"])), 0), [])
        self.assertEqual(radar_gmail._search_unseen(_Conn(search=("OK", [])), 0), [])
        self.assertEqual(radar_gmail._search_unseen(_Conn(search=("OK", [b""])), 0), [])
        self.assertEqual(radar_gmail._search_unseen(_Conn(search=("OK", [None])), 0), [])


class FetchMessageTestCase(unittest.TestCase):
    def test_fetch_items_and_failure(self):
        conn = _Conn(fetch={5: ("OK", [(b"5 (UID 5)", b"raw")])})
        self.assertEqual(radar_gmail._fetch_message(conn, 5), ("OK", [(b"5 (UID 5)", b"raw")]))
        self.assertEqual(conn.calls, [("fetch", "5", "(BODY.PEEK[] X-GM-THRID)")])
        self.assertIsNone(radar_gmail._fetch_message(_Conn(fetch_exc=OSError()), 5))
        self.assertIsNone(radar_gmail._fetch_message(
            _Conn(fetch_exc=imaplib.IMAP4.error("x")), 5))

    def test_literal_extraction(self):
        self.assertEqual(radar_gmail._fetched_literal(("OK", [(b"env", b"raw")])), b"raw")
        self.assertIsNone(radar_gmail._fetched_literal(("NO", [(b"env", b"raw")])))
        self.assertIsNone(radar_gmail._fetched_literal(("OK", [])))
        self.assertIsNone(radar_gmail._fetched_literal(("OK", None)))
        self.assertIsNone(radar_gmail._fetched_literal(("OK", [b")", (b"only",)])))
        self.assertIsNone(radar_gmail._fetched_literal(("OK", [(b"env", b"")])))
        # first qualifying tuple wins
        self.assertEqual(radar_gmail._first_literal([b"x", (b"a", b"first"), (b"b", b"second")]),
                         b"first")

    def test_fetch_new_messages_skips_a_failed_fetch_without_advancing(self):
        conn = _Conn(search=("OK", [b"5 6"]), fetch_exc=OSError("net"))
        out, newest = radar_gmail.fetch_new_messages(conn, 0)
        self.assertEqual(out, [])
        self.assertEqual(newest, 0)


class CommandMessageTestCase(unittest.TestCase):
    def test_shapes(self):
        self.assertEqual(radar_gmail._command_message("junk", 0), (None, None))
        self.assertEqual(radar_gmail._command_message({"uid": "x"}, 0), (None, None))
        self.assertEqual(radar_gmail._command_message({}, 0), (None, None))
        self.assertEqual(radar_gmail._command_message({"uid": 3}, 3), (3, None))
        self.assertEqual(radar_gmail._command_message(
            {"uid": 9, "from": "noreply@x.io"}, 3), (9, None))
        uid, msg = radar_gmail._command_message({
            "uid": "10", "from": "boss@corp.com", "subject": "s", "date": "d",
            "message_id": " <m> ", "body": "b" * 3000, "gmail_thread_id": 42,
        }, 3)
        self.assertEqual(uid, 10)
        self.assertEqual(msg["message_id"], "<m>")
        self.assertEqual(msg["gm_thrid"], "42")
        self.assertEqual(len(msg["body"]), radar_gmail.BODY_TRUNCATE)
        _uid, msg2 = radar_gmail._command_message({"uid": 11}, 3)
        self.assertEqual(msg2, {"uid": 11, "from": "", "subject": "", "date": "",
                                "message_id": "", "gm_thrid": None, "body": ""})

    def test_command_messages_marker_semantics(self):
        out, newest = radar_gmail._command_messages(
            [{"uid": 2}, {"uid": 50, "from": "noreply@x"}, "junk", {"uid": 7}], 5)
        self.assertEqual([m["uid"] for m in out], [7])
        self.assertEqual(newest, 50)


class FetcherCommandTestCase(unittest.TestCase):
    def test_argv_and_stdout(self):
        self.assertIsNone(radar_gmail._command_argv("unterminated 'q"))
        self.assertIsNone(radar_gmail._command_argv("   "))
        self.assertEqual(radar_gmail._command_argv("~/bin/f --x")[1], "--x")
        self.assertIsNone(radar_gmail._fetcher_stdout("unterminated 'q", 0))
        self.assertIsNone(radar_gmail._fetcher_stdout("/definitely/missing/exe", 0))
        py = shlex.quote(sys.executable)   # Windows paths carry backslashes
        self.assertIsNone(radar_gmail._fetcher_stdout(
            f"{py} -c 'import sys; sys.exit(3)'", 0))
        out = radar_gmail._fetcher_stdout(
            f"{py} -c 'import os; print(os.environ[\"GMAIL_RADAR_LAST_UID\"])'", 41)
        self.assertEqual(out.strip(), "41")
        self.assertIsNone(radar_gmail._command_array("no brackets"))
        self.assertIsNone(radar_gmail._command_array("[not json"))
        self.assertIsNone(radar_gmail._command_array('{"a": 1}'))     # object, no array
        self.assertEqual(radar_gmail._command_array(' x [1, 2] y '), [1, 2])


if __name__ == "__main__":
    unittest.main()
