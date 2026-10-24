"""The server's own recap actions (CONTRACT §49 / §63 / §63.8 / §63.9; v2.0.0).

v1 had actd own the page: the board read ``dashboard.json`` ``recaps[]`` and the
buttons dropped inbox files (``recap_generate`` / ``recap_revert`` /
``recap_slack_draft``) that actd spawned ``python -m act.recap`` for. v2.0.0 the
server owns it end to end — this file pins the three routes over a real server:

- ``GET /api/recaps`` → ``{"recaps": [...], "recap_counts": {active, archived,
  dismissed}}`` straight from ``recap_store.snapshot`` over ``<home>/state``;
  token-light; an empty / corrupt layer is an empty listing, never 500;
- ``POST /api/recaps/generate {key, note?, partial?, shape?, answers?}`` → the
  v1 inbox_writer field gates verbatim (400 on the first bad field, nothing
  spawned, nothing recorded); a good request spawns ``act.recap --generate …``
  detached through the launcher seam and records the §63.8 ledger; the receipt
  carries ``launch`` + the fresh ``generate_request``;
- ``POST /api/recaps/revert {key, version}`` → ``act.recap --revert KEY
  --to-version N`` detached; ``version`` a real integer 1..99999;
- all three write routes stand behind the four §49 gates; the retired
  ``POST /api/actions`` is 404.

``detached.launch`` is patched for every request (tests never spawn).
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first
from tests.test_server_common import (assert_envelope, auth_headers, get_json,
                                      http_request, post_json, start_server, write_text)

from act.lib import detached

KEY = "meeting:2026-08-31T1256-zoom"


class _Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-recap-actions-")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        (self.home / "state").mkdir(parents=True)
        self.launched = []
        self.result = detached.RUNNING
        patcher = mock.patch.object(detached, "launch", self._launch)
        patcher.start()
        self.addCleanup(patcher.stop)
        _httpd, self.port = start_server(self, self.home)

    def _launch(self, argv, log_name, label, log=None, home=None):
        self.launched.append({"argv": list(argv), "log": log_name, "home": home})
        return self.result

    def ledger(self) -> dict:
        p = self.home / "state" / "recap_requests.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}

    def write_recap(self, key=KEY, **fields):
        rec = {"key": key, "status": "closed", "start_ts": 1756669000.0, "end_ts": 1756670200.0,
               "app": "zoom", "en": ["Decided: x"], "zh": ["定了：x"], "version": 1,
               "generated_at": "2026-08-31T20:20:00Z", "history": []}
        rec.update(fields)
        write_text(self.home / "state" / "recap" / "recaps" / (key.replace(":", "_") + ".json"),
                   json.dumps(rec))


class ListingTestCase(_Case):
    def test_empty_home_is_an_empty_listing(self):
        status, obj = get_json(self.port, "/api/recaps")
        self.assertEqual(status, 200)
        self.assertEqual(obj, {"recaps": [], "recap_counts": {"active": 0, "archived": 0, "dismissed": 0}})

    def test_rows_come_from_the_recap_store_under_this_home(self):
        self.write_recap()
        status, obj = get_json(self.port, "/api/recaps")
        self.assertEqual(status, 200)
        self.assertEqual([r["key"] for r in obj["recaps"]], [KEY])
        self.assertEqual(obj["recap_counts"]["active"], 1)
        self.assertIn("generate_request", obj["recaps"][0])          # add-only §63.8 key, always present
        self.assertIsNone(obj["recaps"][0]["generate_request"])

    def test_a_corrupt_file_is_skipped_not_a_500(self):
        write_text(self.home / "state" / "recap" / "recaps" / "meeting_2026-08-31T1256-zoom.json", "{torn")
        status, obj = get_json(self.port, "/api/recaps")
        self.assertEqual(status, 200)
        self.assertEqual(obj["recaps"], [])

    def test_listing_is_token_light(self):
        status, _h, _d = http_request(self.port, "GET", "/api/recaps")
        self.assertEqual(status, 200)


class GenerateTestCase(_Case):
    def test_a_regenerate_spawns_the_writer_and_records_the_request(self):
        self.write_recap()
        status, obj = post_json(self.port, "/api/recaps/generate",
                                {"key": KEY, "note": "the deadline is Friday", "shape": "sections"})
        self.assertEqual(status, 200)
        self.assertEqual((obj["ok"], obj["key"], obj["launch"]), (True, KEY, "running"))
        self.assertEqual(obj["generate_request"]["state"], "running")
        self.assertEqual(self.launched, [{"argv": ["act.recap", "--generate", KEY, "--note",
                                                   "the deadline is Friday", "--shape", "sections"],
                                          "log": "recap.log", "home": self.home}])
        self.assertEqual(self.ledger()[KEY]["launch"], "running")
        _s, listing = get_json(self.port, "/api/recaps")
        self.assertEqual(listing["recaps"][0]["generate_request"]["state"], "running")

    def test_generate_now_and_intent_answers(self):
        status, obj = post_json(self.port, "/api/recaps/generate",
                                {"key": KEY, "partial": True, "answers": ["split1=drop", "aud=send"]})
        self.assertEqual(status, 200, obj)
        argv = self.launched[0]["argv"]
        self.assertEqual(argv[:4], ["act.recap", "--generate", KEY, "--partial"])
        self.assertEqual(argv[-2], "--answers")
        self.assertEqual(json.loads(argv[-1]), ["split1=drop", "aud=send"])

    def test_a_failed_launch_is_noop_and_says_so(self):
        self.result = detached.NOOP
        status, obj = post_json(self.port, "/api/recaps/generate", {"key": KEY})
        self.assertEqual(status, 200)
        self.assertEqual((obj["ok"], obj["launch"]), (False, "noop"))
        self.assertEqual(obj["generate_request"]["note"], "launch_failed")

    def test_validation_fails_closed_and_spawns_nothing(self):
        cases = (
            ({}, "INVALID_FIELD"),
            ({"key": "R-101"}, "INVALID_FIELD"),
            ({"key": "meeting:../../etc"}, "INVALID_FIELD"),
            ({"key": KEY, "note": "n" * 501}, "INVALID_FIELD"),
            ({"key": KEY, "note": ""}, "INVALID_FIELD"),
            ({"key": KEY, "note": 5}, "INVALID_FIELD"),
            ({"key": KEY, "partial": False}, "INVALID_FIELD"),
            ({"key": KEY, "shape": "Sections"}, "INVALID_FIELD"),
            ({"key": KEY, "channel_id": "C123"}, "UNKNOWN_FIELD"),   # §63.4 retired
            ({"key": KEY, "meeting_key": KEY}, "UNKNOWN_FIELD"),     # v1 inbox spelling
            ({"key": KEY, "id": "R-1"}, "UNKNOWN_FIELD"),
        )
        for payload, code in cases:
            with self.subTest(payload=payload):
                status, body = post_json(self.port, "/api/recaps/generate", payload)
                self.assertEqual(status, 400)
                assert_envelope(self, body, code)
        self.assertEqual(self.launched, [])
        self.assertEqual(self.ledger(), {})

    def test_answers_fail_closed(self):
        for answers in ([], "split1=drop", [1], ["split1"], ["Split1=drop"], ["aud=send too"],
                        ["split1=drop", "split1=keep"], ["aud=send"] * 13, [["split1=drop"]],
                        [{"split1": "drop"}], ["zzz=nope"]):
            with self.subTest(answers=answers):
                status, body = post_json(self.port, "/api/recaps/generate",
                                         {"key": KEY, "answers": answers})
                self.assertEqual(status, 400)
                assert_envelope(self, body, "INVALID_FIELD")
        self.assertEqual(self.launched, [])


class RevertTestCase(_Case):
    def test_revert_spawns_the_writer_and_stays_out_of_the_ledger(self):
        status, obj = post_json(self.port, "/api/recaps/revert", {"key": KEY, "version": 2})
        self.assertEqual(status, 200)
        self.assertEqual(obj, {"ok": True, "key": KEY, "version": 2, "launch": "running"})
        self.assertEqual(self.launched[0]["argv"], ["act.recap", "--revert", KEY, "--to-version", "2"])
        self.assertEqual(self.ledger(), {})

    def test_revert_validation_fails_closed(self):
        for payload, code in (
            ({"key": KEY}, "INVALID_FIELD"),
            ({"key": KEY, "version": 0}, "INVALID_FIELD"),
            ({"key": KEY, "version": -1}, "INVALID_FIELD"),
            ({"key": KEY, "version": True}, "INVALID_FIELD"),
            ({"key": KEY, "version": "2"}, "INVALID_FIELD"),
            ({"key": KEY, "version": 2.0}, "INVALID_FIELD"),
            ({"key": KEY, "version": 10 ** 9}, "INVALID_FIELD"),
            ({"key": "R-1", "version": 2}, "INVALID_FIELD"),
            ({"key": KEY, "version": 2, "note": "x"}, "UNKNOWN_FIELD"),
        ):
            with self.subTest(payload=payload):
                status, body = post_json(self.port, "/api/recaps/revert", payload)
                self.assertEqual(status, 400)
                assert_envelope(self, body, code)
        self.assertEqual(self.launched, [])


class GatesTestCase(_Case):
    def test_write_routes_need_the_token(self):
        for path, payload in (("/api/recaps/generate", {"key": KEY}),
                              ("/api/recaps/revert", {"key": KEY, "version": 1})):
            with self.subTest(path=path):
                headers = auth_headers(self.port)
                headers.pop("X-Zai-Token")
                status, _h, _d = http_request(self.port, "POST", path,
                                              body=json.dumps(payload).encode("utf-8"),
                                              headers=headers)
                self.assertEqual(status, 401)
        self.assertEqual(self.launched, [])

    def test_the_v1_inbox_route_is_gone(self):
        status, _obj = post_json(self.port, "/api/actions",
                                 {"action": "recap_generate", "meeting_key": KEY})
        self.assertEqual(status, 404)
        self.assertEqual(self.launched, [])
        self.assertFalse((self.home / "state" / "inbox").exists())


if __name__ == "__main__":
    unittest.main()
