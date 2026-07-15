"""Audit fixes — act/webui.py.

* Anti-framing: every response now carries ``X-Frame-Options: DENY`` and
  ``Content-Security-Policy: frame-ancestors 'none'`` — a malicious local page
  could otherwise iframe the token-armed dashboard and clickjack 批准.
* Inbox field types (webui side of the poison-inbox finding): free-text
  fields (``comment``/``text``) must be str or null/absent, ``ids`` a list of
  strings — anything else is a 400 and never reaches state/inbox/.

Same loopback-server harness as tests/test_webui.py.
"""
import http.client
import json
import threading
import unittest

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act.* import

from act import webui
from act.lib import config


class WebUIAuditTestCase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        for p in config.INBOX_DIR.glob("*.json"):
            p.unlink()
        self.httpd, self.url, self.token = webui.make_server()
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)

    def _req(self, method, path, *, body=None, token=None, origin=None):
        headers = {}
        if token is not None:
            headers["X-Webui-Token"] = token
        if origin is not None:
            headers["Origin"] = origin
        if body is not None:
            headers["Content-Type"] = "application/json"
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request(method, path, body=body, headers=headers)
            resp = conn.getresponse()
            data = resp.read()
            return resp.status, dict(resp.getheaders()), data
        finally:
            conn.close()

    def _post_inbox(self, payload):
        return self._req("POST", "/api/inbox", body=json.dumps(payload),
                         token=self.token, origin=f"http://127.0.0.1:{self.port}")

    # -- anti-framing headers ------------------------------------------------ #
    def test_anti_framing_headers_on_every_response(self):
        cases = [
            ("GET", "/", None),               # the token-armed page itself
            ("GET", "/api/dashboard", self.token),
            ("GET", "/nope", None),           # error responses included
        ]
        for method, path, token in cases:
            _, headers, _ = self._req(method, path, token=token)
            self.assertEqual(headers.get("X-Frame-Options"), "DENY",
                             f"{method} {path}")
            self.assertEqual(headers.get("Content-Security-Policy"),
                             "frame-ancestors 'none'", f"{method} {path}")

    # -- inbox field types --------------------------------------------------- #
    def test_non_string_comment_rejected_400(self):
        for bad in (5, {"x": 1}, ["a"], True):
            status, _, _ = self._post_inbox(
                {"id": "R-1", "action": "comment", "comment": bad})
            self.assertEqual(status, 400, f"comment={bad!r}")
        self.assertEqual(list(config.INBOX_DIR.glob("*.json")), [])

    def test_null_and_string_comment_accepted(self):
        status, _, _ = self._post_inbox(
            {"id": "R-1", "action": "approve", "comment": None})
        self.assertEqual(status, 200)
        status, _, _ = self._post_inbox(
            {"id": "R-1", "action": "comment", "comment": "改一下标题"})
        self.assertEqual(status, 200)
        files = list(config.INBOX_DIR.glob("*.json"))
        self.assertEqual(len(files), 2)
        comments = {json.loads(f.read_text(encoding="utf-8")).get("comment")
                    for f in files}
        self.assertEqual(comments, {None, "改一下标题"})

    def test_non_string_text_rejected_400(self):
        status, _, _ = self._post_inbox({"action": "capture", "text": 123})
        self.assertEqual(status, 400)
        self.assertEqual(list(config.INBOX_DIR.glob("*.json")), [])

    def test_ids_must_be_a_list_of_strings(self):
        for bad in ("R-1", {"a": 1}, ["R-1", 5], [None]):
            status, _, _ = self._post_inbox(
                {"action": "merge_review", "ids": bad})
            self.assertEqual(status, 400, f"ids={bad!r}")
        self.assertEqual(list(config.INBOX_DIR.glob("*.json")), [])
        status, _, _ = self._post_inbox(
            {"action": "merge_review", "ids": ["R-1", "R-2"]})
        self.assertEqual(status, 200)


if __name__ == "__main__":
    unittest.main()
