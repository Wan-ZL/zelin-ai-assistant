"""素材库 HTTP 面（CONTRACT §62.4；§49 路由表 + 四闸；server/materials.py）。

- GET /api/materials/list：token-light；?status=open 默认（只回尚未开 PR / 完成 /
  放弃的条目）、all、单状态；坏 status → 400；counts 反映全量。
- POST /api/materials/add：四闸（缺 token 401 且台账不落）、字段白名单 400
  UNKNOWN_FIELD、类型闸 400、url/note 归一后落台账；full → 409 CONFLICT。
- POST /api/materials/dismiss：未知 id 404；再放弃 → 409（状态机拒绝）；
  成功回执 = 更新后的记录且 GET open 不再含它。
- act.lib 不可 import（部分安装形态）→ 501 NOT_IMPLEMENTED，不装成功。
真 server 随机端口（tests/test_server_common.py）；stdlib 客户端。
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first
from tests.test_server_common import (assert_envelope, auth_headers, get_json,
                                      http_request, post_json, start_server)

from act.lib import materials
from server import materials as server_materials
from server import security as security_mod


class _ServerCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-materials-srv-")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        (self.home / "state").mkdir(parents=True)
        self.ledger = materials.ledger_path(self.home)
        _httpd, self.port = start_server(self, self.home)

    def add(self, **payload):
        status, obj = post_json(self.port, "/api/materials/add", payload)
        self.assertEqual(status, 200, obj)
        return obj


class AddTestCase(_ServerCase):
    def test_add_returns_record_and_lands_in_ledger(self):
        rec = self.add(url=" https://example.com/a ", note="  worth a look ")
        self.assertEqual(rec["status"], "new")
        self.assertEqual(rec["url"], "https://example.com/a")
        self.assertEqual(rec["note"], "worth a look")
        self.assertEqual(rec["links"], {})
        self.assertRegex(rec["id"], materials.ID_RE.pattern)
        self.assertEqual(set(rec), {"id", "ts", "created_at", "url", "note", "status", "links"})
        lines = self.ledger.read_text(encoding="utf-8").splitlines()
        self.assertEqual(json.loads(lines[0]), rec)

    def test_note_only_allowed_url_optional(self):
        rec = self.add(note="idea without a link")
        self.assertEqual(rec["url"], "")

    def test_unknown_field_zero_tolerance(self):
        status, obj = post_json(self.port, "/api/materials/add", {"note": "x", "status": "done"})
        self.assertEqual(status, 400)
        assert_envelope(self, obj, "UNKNOWN_FIELD")
        self.assertEqual(obj["error"]["details"]["fields"], ["status"])
        self.assertFalse(self.ledger.exists())

    def test_type_and_shape_gates(self):
        for payload, field in (({"url": 5}, "url"), ({"note": ["a"]}, "note")):
            status, obj = post_json(self.port, "/api/materials/add", payload)
            self.assertEqual(status, 400, payload)
            assert_envelope(self, obj, "INVALID_FIELD")
            self.assertEqual(obj["error"]["details"]["field"], field)
        for payload in ({}, {"url": "", "note": "  "}, {"url": "ftp://x/y", "note": "n"},
                        {"note": "a" * (materials.MAX_NOTE_CHARS + 1)}):
            status, obj = post_json(self.port, "/api/materials/add", payload)
            self.assertEqual(status, 400, payload)
            assert_envelope(self, obj, "INVALID_FIELD")
            self.assertEqual(obj["error"]["details"]["reason"], "invalid")
        self.assertFalse(self.ledger.exists())

    def test_full_box_is_409_conflict(self):
        with mock.patch.object(materials, "MAX_OPEN_ITEMS", 1):
            self.add(note="one")
            status, obj = post_json(self.port, "/api/materials/add", {"note": "two"})
        self.assertEqual(status, 409)
        assert_envelope(self, obj, "CONFLICT")
        self.assertEqual(obj["error"]["details"]["reason"], "full")

    def test_write_gates_apply_missing_token_401_no_ledger(self):
        body = json.dumps({"note": "x"}).encode("utf-8")
        headers = auth_headers(self.port)
        headers.pop(security_mod.TOKEN_HEADER)
        status, _h, data = http_request(self.port, "POST", "/api/materials/add",
                                        body=body, headers=headers)
        self.assertEqual(status, 401)
        assert_envelope(self, json.loads(data), "UNAUTHORIZED")
        self.assertFalse(self.ledger.exists())
        # dismiss is gated the same way
        status, _h, data = http_request(self.port, "POST", "/api/materials/dismiss",
                                        body=json.dumps({"id": "m-000000000001"}).encode(),
                                        headers=headers)
        self.assertEqual(status, 401)

    def test_cross_origin_add_rejected(self):
        headers = auth_headers(self.port)
        headers["Origin"] = "http://evil.example"
        status, _h, data = http_request(self.port, "POST", "/api/materials/add",
                                        body=json.dumps({"note": "x"}).encode(), headers=headers)
        self.assertEqual(status, 403)
        assert_envelope(self, json.loads(data), "FORBIDDEN")
        self.assertFalse(self.ledger.exists())


class ListTestCase(_ServerCase):
    def test_default_open_filter_hides_pr_opened_done_dismissed(self):
        a = self.add(note="a")
        b = self.add(note="b")
        c = self.add(note="c")
        d = self.add(note="d")
        materials.transition(self.ledger, b["id"], "dismissed")
        for step in ("picked_up", "proposal_created", "pr_opened"):
            materials.transition(self.ledger, c["id"], step)
        materials.transition(self.ledger, d["id"], "picked_up")
        status, obj = get_json(self.port, "/api/materials/list")
        self.assertEqual(status, 200)
        self.assertEqual(obj["status"], "open")
        self.assertEqual([r["id"] for r in obj["items"]], [d["id"], a["id"]])  # newest first
        self.assertEqual(obj["counts"], {"open": 2, "total": 4})
        status, obj = get_json(self.port, "/api/materials/list?status=all")
        self.assertEqual(len(obj["items"]), 4)
        status, obj = get_json(self.port, "/api/materials/list?status=pr_opened")
        self.assertEqual([r["id"] for r in obj["items"]], [c["id"]])
        self.assertEqual(obj["status"], "pr_opened")

    def test_empty_ledger_is_an_empty_list_not_an_error(self):
        status, obj = get_json(self.port, "/api/materials/list")
        self.assertEqual((status, obj), (200, {"items": [], "status": "open",
                                               "counts": {"open": 0, "total": 0}}))

    def test_bad_status_filter_400(self):
        status, obj = get_json(self.port, "/api/materials/list?status=bogus")
        self.assertEqual(status, 400)
        assert_envelope(self, obj, "INVALID_FIELD")

    def test_read_is_token_light_and_no_store(self):
        status, headers, _data = http_request(self.port, "GET", "/api/materials/list")
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Cache-Control"), "no-store")
        self.assertNotIn("Access-Control-Allow-Origin", headers)


class DismissTestCase(_ServerCase):
    def test_dismiss_then_open_list_excludes_it(self):
        rec = self.add(note="drop me")
        status, out = post_json(self.port, "/api/materials/dismiss", {"id": rec["id"]})
        self.assertEqual(status, 200)
        self.assertEqual(out["status"], "dismissed")
        self.assertEqual(out["id"], rec["id"])
        self.assertEqual(out["created_at"], rec["created_at"])
        _s, listed = get_json(self.port, "/api/materials/list")
        self.assertEqual(listed["items"], [])
        self.assertEqual(listed["counts"], {"open": 0, "total": 1})
        # the ledger still holds the record (return ticket exists at the API level)
        self.assertEqual(materials.get(self.ledger, rec["id"])["status"], "dismissed")

    def test_unknown_id_404(self):
        status, obj = post_json(self.port, "/api/materials/dismiss", {"id": "m-0000000000ff"})
        self.assertEqual(status, 404)
        assert_envelope(self, obj, "NOT_FOUND")

    def test_double_dismiss_409(self):
        rec = self.add(note="x")
        post_json(self.port, "/api/materials/dismiss", {"id": rec["id"]})
        status, obj = post_json(self.port, "/api/materials/dismiss", {"id": rec["id"]})
        self.assertEqual(status, 409)
        assert_envelope(self, obj, "CONFLICT")
        self.assertEqual(obj["error"]["details"]["reason"], "bad_transition")

    def test_field_gates(self):
        status, obj = post_json(self.port, "/api/materials/dismiss", {"id": "m-1", "note": "x"})
        self.assertEqual(status, 400)
        assert_envelope(self, obj, "UNKNOWN_FIELD")
        status, obj = post_json(self.port, "/api/materials/dismiss", {"id": 7})
        self.assertEqual(status, 400)
        assert_envelope(self, obj, "INVALID_FIELD")


class LibUnavailableTestCase(_ServerCase):
    def test_501_when_act_lib_missing(self):
        with mock.patch.object(server_materials, "_materials", None):
            status, obj = get_json(self.port, "/api/materials/list")
            self.assertEqual(status, 501)
            assert_envelope(self, obj, "NOT_IMPLEMENTED")
            status, obj = post_json(self.port, "/api/materials/add", {"note": "x"})
            self.assertEqual(status, 501)
            status, obj = post_json(self.port, "/api/materials/dismiss", {"id": "m-000000000001"})
            self.assertEqual(status, 501)
        self.assertFalse(self.ledger.exists())


if __name__ == "__main__":
    unittest.main()
