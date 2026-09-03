"""webui — the POST /api/inbox rule table and body reader split out in P3b (§41).

Pins each validation rule on its own (pure functions, no server), the rule
order (first failing rule names the error), the W18 run-gate downgrade shapes,
and — over a real loopback server — the body-length rejections (missing,
non-numeric, oversized Content-Length) and the unknown-path 404 that the
existing suites did not reach.
"""
import http.client
import json
import threading
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first

from act import webui
from act.lib import config


class RuleTableTestCase(unittest.TestCase):
    def test_parse_payload(self):
        self.assertEqual(webui._parse_inbox_payload(b"{bad"), (None, "invalid json"))
        self.assertEqual(webui._parse_inbox_payload(b"\xff\xfe"), (None, "invalid json"))
        self.assertEqual(webui._parse_inbox_payload(b"[1]"), (None, "body must be a json object"))
        self.assertEqual(webui._parse_inbox_payload(b'{"a": 1}'), ({"a": 1}, None))

    def test_each_rule(self):
        self.assertEqual(webui._check_action({}), "unknown action: None")
        self.assertEqual(webui._check_action({"action": "dance"}), "unknown action: 'dance'")
        self.assertIsNone(webui._check_action({"action": "approve"}))
        self.assertEqual(webui._check_id({"id": "../x"}), "invalid id")
        self.assertEqual(webui._check_id({"id": 5}), "invalid id")
        self.assertIsNone(webui._check_id({"id": None}))
        self.assertIsNone(webui._check_id({"id": "R-1"}))
        self.assertEqual(webui._check_text_fields({"comment": 1}), "comment must be a string")
        self.assertEqual(webui._check_text_fields({"note_ts": []}), "note_ts must be a string")
        self.assertIsNone(webui._check_text_fields({"comment": None, "text": "t"}))
        self.assertEqual(webui._check_title({"title": ""}), "title must be a string of 1-64 chars")
        self.assertEqual(webui._check_title({"title": "x" * 65}), "title must be a string of 1-64 chars")
        self.assertIsNone(webui._check_title({"title": "x" * 64}))
        self.assertEqual(webui._check_ids({"ids": "R-1"}), "ids must be a list of strings")
        self.assertEqual(webui._check_ids({"ids": ["R-1", 2]}), "ids must be a list of strings")
        self.assertIsNone(webui._check_ids({"ids": []}))
        self.assertEqual(webui._check_primary({"primary": "bad id"}), "invalid primary")
        self.assertIsNone(webui._check_primary({"primary": "R-2"}))
        self.assertEqual(webui._check_mode({"mode": "run", "action": "approve"}),
                         'mode is only capture mode:"run"')
        self.assertEqual(webui._check_mode({"mode": "walk", "action": "capture"}),
                         'mode is only capture mode:"run"')
        self.assertIsNone(webui._check_mode({"mode": "run", "action": "capture"}))
        self.assertIsNone(webui._check_mode({"action": "capture"}))

    def test_merge_force_rule(self):
        self.assertIsNone(webui._check_merge_force({"action": "approve"}))
        err = "merge_force needs >=2 distinct ids and primary among them"
        self.assertEqual(webui._check_merge_force({"action": "merge_force"}), err)
        self.assertEqual(webui._check_merge_force({"action": "merge_force", "ids": ["R-1", "R-1"],
                                                   "primary": "R-1"}), err)
        self.assertEqual(webui._check_merge_force({"action": "merge_force", "ids": ["R-1", "R-2"],
                                                   "primary": "R-3"}), err)
        self.assertEqual(webui._check_merge_force({"action": "merge_force", "ids": ["R-1", "../x"],
                                                   "primary": "R-1"}), err)
        self.assertIsNone(webui._check_merge_force({"action": "merge_force", "ids": ["R-1", "R-2", "R-1"],
                                                    "primary": "R-2"}))
        self.assertFalse(webui._merge_force_shape_ok(["R-1"], "R-1"))

    def test_rule_order_first_failure_wins(self):
        payload = {"action": "dance", "id": "../x", "comment": 5}
        self.assertEqual(webui._inbox_problem(payload), "unknown action: 'dance'")
        payload = {"action": "approve", "id": "../x", "comment": 5}
        self.assertEqual(webui._inbox_problem(payload), "invalid id")
        self.assertIsNone(webui._inbox_problem({"action": "approve", "id": "R-1"}))

    def test_run_gate(self):
        plain = {"action": "capture", "text": "t"}
        self.assertEqual(webui._apply_run_gate(plain), (plain, None))
        self.assertEqual(webui._apply_run_gate({"action": "approve", "mode": "run"}),
                         ({"action": "approve", "mode": "run"}, None))
        run = {"action": "capture", "mode": "run", "text": "t"}
        with mock.patch.object(webui.risk, "remote_direct_run_allowed", return_value=False):
            self.assertEqual(webui._apply_run_gate(run),
                             ({"action": "capture", "text": "t"}, webui._RUN_DOWNGRADE_NOTICE))
        with mock.patch.object(webui.risk, "remote_direct_run_allowed", return_value=True):
            self.assertEqual(webui._apply_run_gate(run), (run, webui._RUN_RESERVED_NOTICE))


class BodyAndPathTestCase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        self.httpd, self.url, self.token = webui.make_server()
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)

    def _post(self, path, body=None, headers=None):
        h = {"X-Webui-Token": self.token, "Origin": f"http://127.0.0.1:{self.port}"}
        h.update(headers or {})
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request("POST", path, body=body, headers=h)
            resp = conn.getresponse()
            return resp.status, json.loads(resp.read().decode("utf-8"))
        finally:
            conn.close()

    def test_body_length_rejections(self):
        status, body = self._post("/api/inbox", body=None, headers={"Content-Length": "0"})
        self.assertEqual((status, body["error"]), (400, "bad body length"))
        status, body = self._post("/api/inbox", body=b"{}", headers={"Content-Length": "abc"})
        self.assertEqual((status, body["error"]), (400, "bad body length"))
        status, body = self._post("/api/inbox", body=b"{}", headers={"Content-Length": "2000000"})
        self.assertEqual((status, body["error"]), (400, "bad body length"))

    def test_unknown_path_is_404_after_auth(self):
        status, body = self._post("/api/other", body=b'{"action": "approve"}')
        self.assertEqual((status, body["error"]), (404, "not found"))
        status, body = self._post("/api/inbox", body=b"{bad")
        self.assertEqual((status, body["error"]), (400, "invalid json"))

    def test_write_failure_is_generic_500(self):
        with mock.patch.object(webui, "write_inbox", side_effect=OSError("/secret/path")):
            status, body = self._post("/api/inbox", body=b'{"action": "capture", "text": "t"}')
        self.assertEqual((status, body), (500, {"error": "internal error"}))


if __name__ == "__main__":
    unittest.main()
