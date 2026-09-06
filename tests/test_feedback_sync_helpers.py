"""feedback_sync — the helpers split out of the sweep in P3b (§29 public
suggestion tracker).

Pins: the pending predicate's three legs (opt-in shape, attempts cap incl.
an unreadable counter, cooldown), marker matching (PRs skipped, missing body),
the transport's request shape + torn-body parsing via a patched urlopen (no
network), the record writer on a numberless response, failure bookkeeping
(transient rollback vs burned attempt, best-effort write), and the sweep's
short-circuits.
"""
import io
import json
import unittest
import urllib.error
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first

from act.lib import config, feedback, feedback_sync as fs


class PendingPredicateTestCase(unittest.TestCase):
    def test_opted_in_unpublished(self):
        self.assertTrue(fs._opted_in_unpublished({"id": "a", "publish": True}))
        self.assertFalse(fs._opted_in_unpublished({"id": "a", "publish": "true"}))
        self.assertFalse(fs._opted_in_unpublished({"id": "a", "publish": True, "issue_number": 3}))
        self.assertFalse(fs._opted_in_unpublished({"publish": True}))
        self.assertFalse(fs._opted_in_unpublished("junk"))

    def test_sync_attempts(self):
        self.assertEqual(fs._sync_attempts({}), 0)
        self.assertEqual(fs._sync_attempts({"sync_attempts": "2"}), 2)
        self.assertEqual(fs._sync_attempts({"sync_attempts": "many"}), fs.MAX_SYNC_ATTEMPTS)
        self.assertEqual(fs._sync_attempts({"sync_attempts": None}), 0)

    def test_in_cooldown(self):
        self.assertFalse(fs._in_cooldown({}))
        self.assertFalse(fs._in_cooldown({"last_sync_attempt_at": "junk"}))
        self.assertFalse(fs._in_cooldown({"last_sync_attempt_at": "2000-01-01T00:00:00Z"}))
        self.assertTrue(fs._in_cooldown({"last_sync_attempt_at": fs._iso_now()}))

    def test_is_pending_legs(self):
        base = {"id": "a", "publish": True}
        self.assertTrue(fs._is_pending(dict(base)))
        self.assertFalse(fs._is_pending(dict(base, sync_attempts=fs.MAX_SYNC_ATTEMPTS)))
        self.assertFalse(fs._is_pending(dict(base, last_sync_attempt_at=fs._iso_now())))
        self.assertFalse(fs._is_pending(dict(base, issue_number=1)))


class MarkerMatchingTestCase(unittest.TestCase):
    def test_is_issue_with_marker(self):
        self.assertTrue(fs._is_issue_with_marker({"body": "x MARK y"}, "MARK"))
        self.assertFalse(fs._is_issue_with_marker({"body": "x MARK y", "pull_request": {}}, "MARK"))
        self.assertFalse(fs._is_issue_with_marker({"body": None}, "MARK"))
        self.assertFalse(fs._is_issue_with_marker({}, "MARK"))
        self.assertFalse(fs._is_issue_with_marker("string", "MARK"))

    def test_page_hit_returns_first_match(self):
        page = [{"body": "no"}, {"body": "MARK", "number": 1}, {"body": "MARK", "number": 2}]
        self.assertEqual(fs._page_hit(page, "MARK")["number"], 1)
        self.assertIsNone(fs._page_hit([], "MARK"))


class _FakeResp:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TransportTestCase(unittest.TestCase):
    def test_send_builds_request_and_parses_body(self):
        captured = {}

        def fake_urlopen(req, timeout):
            captured["req"] = req
            captured["timeout"] = timeout
            return _FakeResp(b'{"number": 7}')

        send = fs._make_transport("tok")
        with mock.patch.object(fs.urllib.request, "urlopen", fake_urlopen):
            out = send("POST", "https://api.github.com/repos/o/r/issues", {"title": "t"})
        self.assertEqual(out, {"number": 7})
        req = captured["req"]
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(json.loads(req.data.decode("utf-8")), {"title": "t"})
        self.assertEqual(req.get_header("Authorization"), "Bearer tok")
        self.assertEqual(req.get_header("User-agent"), fs.USER_AGENT)
        self.assertEqual(captured["timeout"], fs.TIMEOUT_SECONDS)

    def test_send_get_has_no_body_and_torn_body_parses_to_empty(self):
        captured = {}

        def fake_urlopen(req, timeout):
            captured["data"] = req.data
            return _FakeResp(b"<html>torn")

        send = fs._make_transport("tok")
        with mock.patch.object(fs.urllib.request, "urlopen", fake_urlopen):
            self.assertEqual(send("GET", "https://api.github.com/x"), {})
        self.assertIsNone(captured["data"])
        self.assertEqual(fs._parse_json_or_empty(b"[1]"), [1])
        self.assertEqual(fs._parse_json_or_empty(b"\xff\xfe"), {})


class SyncOneHelpersTestCase(unittest.TestCase):
    def setUp(self):
        feedback.FEEDBACK_DIR.mkdir(parents=True, exist_ok=True)
        for p in feedback.FEEDBACK_DIR.glob("*.json"):
            p.unlink()

    tearDown = setUp

    def test_prewrite_attempt(self):
        rec = {"id": "pw1"}
        self.assertTrue(fs._prewrite_attempt(rec, 2))
        self.assertEqual(rec["sync_attempts"], 3)
        self.assertTrue(rec["last_sync_attempt_at"])
        with mock.patch.object(feedback, "write_record", side_effect=OSError("disk")):
            self.assertFalse(fs._prewrite_attempt({"id": "pw2"}, 0))

    def test_record_issue(self):
        rec = {"id": "ri1", "sync_error": "old"}
        with self.assertRaises(ValueError):
            fs._record_issue(rec, {"number": "7"})
        fs._record_issue(rec, {"number": 7, "html_url": None})
        self.assertEqual((rec["issue_number"], rec["issue_url"]), (7, ""))
        self.assertNotIn("sync_error", rec)
        self.assertTrue(rec["issue_synced_at"])

    def test_note_failure_transient_vs_burned(self):
        rec = {"id": "nf1", "sync_attempts": 2}
        fs._note_failure(rec, urllib.error.URLError("offline"), 1)
        self.assertEqual(rec["sync_attempts"], 1)
        self.assertIn("offline", rec["sync_error"])
        rec = {"id": "nf2", "sync_attempts": 2}
        fs._note_failure(rec, ValueError("shape"), 1)
        self.assertEqual(rec["sync_attempts"], 2)
        with mock.patch.object(feedback, "write_record", side_effect=OSError("disk")):
            fs._note_failure({"id": "nf3"}, ValueError("x"), 0)   # must not raise

    def test_locate_or_create_paths(self):
        calls = []

        def send(method, url, payload=None):
            calls.append(method)
            if method == "GET":
                return [{"body": fs._marker(rec), "number": 5}]
            return "not-a-dict"

        rec = {"id": "loc1", "text": "t", "ts": fs._iso_now()}
        self.assertEqual(fs._locate_or_create(send, "o/r", rec, True)["number"], 5)
        self.assertEqual(calls, ["GET"])
        calls.clear()
        self.assertEqual(fs._locate_or_create(send, "o/r", rec, False), {})
        self.assertEqual(calls, ["POST"])


class SweepShortCircuitTestCase(unittest.TestCase):
    def setUp(self):
        feedback.FEEDBACK_DIR.mkdir(parents=True, exist_ok=True)
        for p in feedback.FEEDBACK_DIR.glob("*.json"):
            p.unlink()

    tearDown = setUp

    def test_pending_records_skips_unreadable_and_private(self):
        (feedback.FEEDBACK_DIR / "bad.json").write_text("{", encoding="utf-8")
        (feedback.FEEDBACK_DIR / "priv.json").write_text(json.dumps({"id": "p"}), encoding="utf-8")
        (feedback.FEEDBACK_DIR / "pub.json").write_text(
            json.dumps({"id": "q", "publish": True}), encoding="utf-8")
        self.assertEqual([r["id"] for r in fs._pending_records()], ["q"])
        with mock.patch.object(type(feedback.FEEDBACK_DIR), "glob", side_effect=OSError("denied")):
            self.assertEqual(fs._pending_records(), [])

    def test_transport_for(self):
        cfg = config.Config()
        marker = object()
        self.assertIs(fs._transport_for(cfg, marker), marker)
        with mock.patch.object(fs, "_read_token", return_value=None):
            self.assertIsNone(fs._transport_for(cfg, None))
        with mock.patch.object(fs, "_read_token", return_value="tok"):
            self.assertTrue(callable(fs._transport_for(cfg, None)))

    def test_publish_all_gates(self):
        cfg = config.Config()
        cfg.features["feedback_sync"] = False
        self.assertEqual(fs._publish_all([{"id": "x"}], cfg, lambda *a: None), 0)
        cfg.features["feedback_sync"] = True
        with mock.patch.object(fs, "_read_token", return_value=None):
            self.assertEqual(fs._publish_all([{"id": "x"}], cfg, None), 0)
        with mock.patch.object(fs, "_sync_one", side_effect=[True, False, True]):
            self.assertEqual(fs._publish_all([{}, {}, {}], cfg, lambda *a: None), 2)

    def test_sweep_swallows_everything(self):
        with mock.patch.object(fs, "_pending_records", side_effect=RuntimeError("boom")):
            self.assertEqual(fs.sweep(), 0)
        self.assertEqual(fs.sweep(), 0)   # nothing pending → 0 without config work

    def test_default_transport_send_via_patched_urlopen_is_reachable(self):
        # belt-and-braces for the coverage-only CRAP row: the real send runs
        # end to end against a fake urlopen, never the network
        with mock.patch.object(fs.urllib.request, "urlopen",
                               return_value=_FakeResp(b'{"ok": 1}')):
            self.assertEqual(fs._make_transport("t")("GET", "https://api.github.com/"), {"ok": 1})
        self.assertIsInstance(io.StringIO(), io.StringIO)


class FindExistingShapeTestCase(unittest.TestCase):
    def test_non_list_listing_raises(self):
        rec = {"id": "shape", "text": "t", "ts": fs._iso_now()}
        with self.assertRaises(ValueError):
            fs._find_existing(lambda m, u, p=None: {"not": "a list"}, "o/r", rec)


if __name__ == "__main__":
    unittest.main()
