"""Local web dashboard server (act/webui.py) — security + contract tests.

Runs entirely inside the sandbox AIASSISTANT_HOME (tests/__init__.py). Starts a
real loopback server on an ephemeral port in a background thread and drives it
with http.client so Host / Origin / token headers can be set exactly.

Covers (per the build brief):
  * token required        — /api/* without the token -> 401
  * Host rejection        — DNS-rebinding style bad Host -> 403 (GET + POST)
  * Origin rejection      — cross-origin POST -> 403
  * action allow-list     — unknown action -> 400, no inbox file written
  * atomic inbox write     — approve -> a valid {id,action,comment,ts} file that
                            actd.process_inbox consumes cleanly
  * /api/dashboard bytes   — returns dashboard.json bytes (or {} when missing)
"""
import http.client
import json
import threading
import unittest

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act.* import

from act import actd, webui
from act.lib import config


class WebUITestCase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        for p in config.INBOX_DIR.glob("*.json"):
            p.unlink()
        if config.DASHBOARD_PATH.exists():
            config.DASHBOARD_PATH.unlink()

        self.httpd, self.url, self.token = webui.make_server()
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)

    # -- request helper ---------------------------------------------------- #
    def _req(self, method, path, *, body=None, token=None, host=None,
             origin=None, content_type=None):
        headers = {}
        if token is not None:
            headers["X-Webui-Token"] = token
        if host is not None:
            headers["Host"] = host
        if origin is not None:
            headers["Origin"] = origin
        if content_type is not None:
            headers["Content-Type"] = content_type
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request(method, path, body=body, headers=headers)
            resp = conn.getresponse()
            data = resp.read()
            return resp.status, data
        finally:
            conn.close()

    def _good_origin(self):
        return f"http://127.0.0.1:{self.port}"

    # -- tests ------------------------------------------------------------- #
    def test_dashboard_requires_token(self):
        status, _ = self._req("GET", "/api/dashboard")
        self.assertEqual(status, 401)

        status, _ = self._req("GET", "/api/dashboard", token="wrong-token")
        self.assertEqual(status, 401)

    def test_dashboard_returns_bytes_with_token(self):
        payload = {"generated_at": "2026-07-11T00:00:00Z",
                   "counts": {"needs_approval": 1}, "needs_approval": []}
        config.DASHBOARD_PATH.write_text(json.dumps(payload), encoding="utf-8")
        status, data = self._req("GET", "/api/dashboard", token=self.token)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data), payload)

    def test_dashboard_missing_returns_empty_object(self):
        status, data = self._req("GET", "/api/dashboard", token=self.token)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data), {})

    def test_index_injects_token_and_never_leaks_via_api(self):
        # The page (same-origin) carries the token...
        status, data = self._req("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(self.token.encode("utf-8"), data)
        self.assertNotIn(webui._TOKEN_PLACEHOLDER.encode("utf-8"), data)
        # ...but no /api/* GET hands it out (dashboard body is just the board).
        config.DASHBOARD_PATH.write_text("{}", encoding="utf-8")
        _, api_data = self._req("GET", "/api/dashboard", token=self.token)
        self.assertNotIn(self.token.encode("utf-8"), api_data)

    def test_bad_host_rejected_get_and_post(self):
        # DNS-rebinding style: a page at evil.example rebinds to 127.0.0.1; the
        # Host header still says evil.example, so it is refused.
        status, _ = self._req("GET", "/api/dashboard", token=self.token,
                              host="evil.example")
        self.assertEqual(status, 403)

        status, _ = self._req(
            "POST", "/api/inbox", token=self.token, host="evil.example",
            origin=self._good_origin(), content_type="application/json",
            body=json.dumps({"id": "R-1", "action": "approve"}))
        self.assertEqual(status, 403)

    def test_cross_origin_post_rejected(self):
        status, _ = self._req(
            "POST", "/api/inbox", token=self.token,
            origin="http://evil.example", content_type="application/json",
            body=json.dumps({"id": "R-1", "action": "approve"}))
        self.assertEqual(status, 403)
        self.assertEqual(list(config.INBOX_DIR.glob("*.json")), [])

    def test_post_missing_origin_rejected(self):
        # No Origin header at all is also refused (curl/script CSRF surface).
        status, _ = self._req(
            "POST", "/api/inbox", token=self.token,
            content_type="application/json",
            body=json.dumps({"id": "R-1", "action": "approve"}))
        self.assertEqual(status, 403)

    def test_post_requires_token(self):
        status, _ = self._req(
            "POST", "/api/inbox", origin=self._good_origin(),
            content_type="application/json",
            body=json.dumps({"id": "R-1", "action": "approve"}))
        self.assertEqual(status, 401)
        self.assertEqual(list(config.INBOX_DIR.glob("*.json")), [])

    def test_unknown_action_rejected_400(self):
        status, data = self._req(
            "POST", "/api/inbox", token=self.token, origin=self._good_origin(),
            content_type="application/json",
            body=json.dumps({"id": "R-1", "action": "delete_everything"}))
        self.assertEqual(status, 400)
        self.assertEqual(list(config.INBOX_DIR.glob("*.json")), [])

    def test_approve_writes_valid_inbox_file_consumed_by_actd(self):
        status, _ = self._req(
            "POST", "/api/inbox", token=self.token, origin=self._good_origin(),
            content_type="application/json",
            body=json.dumps({"id": "R-42", "action": "approve"}))
        self.assertEqual(status, 200)

        files = list(config.INBOX_DIR.glob("*.json"))
        self.assertEqual(len(files), 1)
        rec = json.loads(files[0].read_text(encoding="utf-8"))
        self.assertEqual(rec["id"], "R-42")
        self.assertEqual(rec["action"], "approve")
        self.assertIn("comment", rec)
        self.assertIsNone(rec["comment"])
        self.assertIn("ts", rec)

        # consumed-shape-correct: actd reads + deletes it without error (the req
        # is unknown here, which actd handles by logging + dropping the file).
        processed = actd.process_inbox()
        self.assertGreaterEqual(processed, 0)
        self.assertEqual(list(config.INBOX_DIR.glob("*.json")), [])

    def test_capture_roundtrips_through_actd(self):
        # No-id action: quick capture -> capture-<uuid>.json -> actd builds a
        # RAISING card (proves the no-req inbox shape is consumed end-to-end).
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()
        status, _ = self._req(
            "POST", "/api/inbox", token=self.token, origin=self._good_origin(),
            content_type="application/json",
            body=json.dumps({"action": "capture", "text": "webui capture test"}))
        self.assertEqual(status, 200)
        files = list(config.INBOX_DIR.glob("capture-*.json"))
        self.assertEqual(len(files), 1)

        from act.lib import registry
        actd.process_inbox()
        made = [r for r in registry.load_all() if r.title == "webui capture test"]
        self.assertEqual(len(made), 1)
        self.assertEqual(made[0].status, registry.State.RAISING.value)

    def test_traversal_id_rejected_normal_id_accepted(self):
        # A path-traversal-y id must be refused (400) and never reach the inbox
        # (downstream merge_review.job_path would otherwise escape state/merge/).
        for bad in ("../../x", "../../../tmp/x", "..", "a/b", ".hidden"):
            status, _ = self._req(
                "POST", "/api/inbox", token=self.token,
                origin=self._good_origin(), content_type="application/json",
                body=json.dumps({"id": bad, "action": "merge_apply"}))
            self.assertEqual(status, 400, f"expected 400 for id={bad!r}")
        self.assertEqual(list(config.INBOX_DIR.glob("*.json")), [])

        # A normal requirement id is accepted and written.
        status, _ = self._req(
            "POST", "/api/inbox", token=self.token, origin=self._good_origin(),
            content_type="application/json",
            body=json.dumps({"id": "R-1", "action": "approve"}))
        self.assertEqual(status, 200)
        self.assertEqual(len(list(config.INBOX_DIR.glob("*.json"))), 1)

    def test_static_and_traversal(self):
        status, data = self._req("GET", "/style.css")
        self.assertEqual(status, 200)
        self.assertIn(b".lane", data)
        # unknown path -> 404, and nothing outside the static allow-list serves.
        status, _ = self._req("GET", "/../act/webui.py")
        self.assertIn(status, (400, 404))
        status, _ = self._req("GET", "/state/webui.token")
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
