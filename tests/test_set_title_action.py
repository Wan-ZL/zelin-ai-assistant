"""§37 set_title inbox action — actd apply + fail-closed boundary validation.

Covers (v0.37 build brief):
- happy path: {action:"set_title", id, title} sets display_title +
  user_titled, previous name lands in former_titles, inbox file consumed;
- fail-closed: non-string / empty / >64-char titles are logged no-ops;
- archived cards stay sealed (no rename without unarchive);
- user_titled pin: a later LLM/harvest title never overwrites the user's;
- webui boundary: set_title allowed, title type/length rejected with 400;
- syncd boundary: title joins the str-or-absent shape gate.
"""
import json
import unittest
import uuid

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import actd, syncd, webui
from act.lib import config, registry
from act.lib.registry import Requirement, State


def _mk(req_id="R-940", status=State.CARD_SENT.value, **kw):
    req = Requirement(id=req_id, title="内部标题（冻结）", status=status, **kw)
    registry.save(req)
    return req


def _drop(payload: dict):
    config.ensure_state_dirs()
    path = config.INBOX_DIR / f"{uuid.uuid4()}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


class SetTitleActdTestCase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()
        archive = config.REGISTRY_DIR / "archive"
        if archive.exists():
            for p in archive.glob("*.yaml"):
                p.unlink()
        for p in config.INBOX_DIR.glob("*.json"):
            p.unlink()

    def test_happy_path_sets_and_pins(self):
        _mk("R-940")
        _drop({"id": "R-940", "action": "set_title", "title": "  新的  名字 ",
               "ts": "2026-07-16T00:00:00Z"})
        self.assertEqual(actd.process_inbox(), 1)
        req = registry.load("R-940")
        self.assertEqual(req.display_title, "新的 名字")   # whitespace collapsed
        self.assertTrue(req.user_titled)
        self.assertEqual(list(config.INBOX_DIR.glob("*.json")), [])
        # internal title untouched (identity anchor)
        self.assertEqual(req.title, "内部标题（冻结）")

    def test_previous_name_lands_in_former_titles(self):
        req = _mk("R-941")
        registry.set_display_title(req, "LLM 起的名")
        registry.save(req)
        _drop({"id": "R-941", "action": "set_title", "title": "用户改的名",
               "ts": "2026-07-16T00:00:00Z"})
        actd.process_inbox()
        req = registry.load("R-941")
        self.assertEqual(req.display_title, "用户改的名")
        self.assertEqual(req.former_titles, ["LLM 起的名"])

    def test_fail_closed_titles(self):
        _mk("R-942")
        for bad in (123, ["x"], "", "   ", "长" * 65, None):
            _drop({"id": "R-942", "action": "set_title", "title": bad,
                   "ts": "2026-07-16T00:00:00Z"})
        actd.process_inbox()
        req = registry.load("R-942")
        self.assertIsNone(req.display_title)
        self.assertFalse(req.user_titled)

    def test_archived_card_is_sealed(self):
        req = _mk("R-943", status=State.DELIVERED.value)
        registry.archive(req, reason="user")
        _drop({"id": "R-943", "action": "set_title", "title": "改名",
               "ts": "2026-07-16T00:00:00Z"})
        actd.process_inbox()
        self.assertIsNone(registry.load("R-943").display_title)

    def test_user_pin_survives_harvest_title(self):
        _mk("R-944", status=State.EXECUTING.value)
        _drop({"id": "R-944", "action": "set_title", "title": "我的名字",
               "ts": "2026-07-16T00:00:00Z"})
        actd.process_inbox()
        req = registry.load("R-944")
        # the harvest-side apply path must be a no-op on a pinned card
        actd._apply_harvest_title(req, {"card_title": "agent 起的新名"})
        self.assertEqual(req.display_title, "我的名字")

    def test_idempotent_rename_acks_noop(self):
        req = _mk("R-945")
        self.assertEqual(actd._apply_set_title(req, "同一个名"), "running")
        self.assertEqual(actd._apply_set_title(req, "同一个名"), "noop")


class SetTitleBoundaryTestCase(unittest.TestCase):
    def test_syncd_shape_gate(self):
        ok = {"id": "R-1", "action": "set_title", "title": "名字"}
        self.assertIsNone(syncd._inbox_shape_error(ok))
        bad = {"id": "R-1", "action": "set_title", "title": 42}
        self.assertIn("title", syncd._inbox_shape_error(bad))

    def test_webui_allows_set_title(self):
        self.assertIn("set_title", webui.ALLOWED_ACTIONS)
        self.assertIn("title", webui._INBOX_KEYS)


class SetTitleWebUITestCase(unittest.TestCase):
    """Real loopback server (same harness as tests/test_webui.py)."""

    def setUp(self):
        import threading
        config.ensure_state_dirs()
        for p in config.INBOX_DIR.glob("*.json"):
            p.unlink()
        self.httpd, self.url, self.token = webui.make_server()
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever,
                                       daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)

    def _post(self, payload: dict):
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request(
                "POST", "/api/inbox", body=json.dumps(payload),
                headers={"X-Webui-Token": self.token,
                         "Host": f"127.0.0.1:{self.port}",
                         "Origin": f"http://127.0.0.1:{self.port}"})
            resp = conn.getresponse()
            return resp.status, resp.read()
        finally:
            conn.close()

    def test_valid_set_title_writes_inbox_file(self):
        status, _ = self._post({"id": "R-950", "action": "set_title",
                                "title": "网页里改的名"})
        self.assertEqual(status, 200)
        files = list(config.INBOX_DIR.glob("*.json"))
        self.assertEqual(len(files), 1)
        rec = json.loads(files[0].read_text(encoding="utf-8"))
        self.assertEqual(rec["action"], "set_title")
        self.assertEqual(rec["title"], "网页里改的名")

    def test_bad_titles_are_400(self):
        for bad in (42, "", "长" * 65, ["x"]):
            status, _ = self._post({"id": "R-950", "action": "set_title",
                                    "title": bad})
            self.assertEqual(status, 400, msg=repr(bad))
        self.assertEqual(list(config.INBOX_DIR.glob("*.json")), [])


if __name__ == "__main__":
    unittest.main()
