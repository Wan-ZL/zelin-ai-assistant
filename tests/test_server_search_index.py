"""GET /api/search-index — 看板搜索的会话内容层 server 面（CONTRACT §37.2 第三条 / §49；owner 决策 D45）。

覆盖：
- 缺席 → 200 空投影 ``{entries: {}, truncated: false}``、无 ETag（层缺席不是错误——宪法第 11 条；永不 404 / 500；
  带 If-None-Match 也照样 200：没有 (mtime, size) 可比）；
- 200 投影 ``{entries: {card_id: text}, truncated: false}``：只发 text（updated_at 不发）、非 dict / 非 str 条目跳过、
  每条尾裁 TEXT_CAP（与 act/lib/search_index.TEXT_CAP 同值——drift-pin）；
- ETag = "<mtime_ns>-<size>"；If-None-Match 命中（含 ``W/`` 弱前缀、多值、``*``）→ 304 空体仍带 ETag，
  且不发 Content-Length / Content-Type（RFC 9110 §8.6）而安全头照发；文件换版后旧 ETag → 200 新体新 ETag；
- size cap：超过 MAX_FILE_BYTES 的文件不读 → 空 entries + truncated:true；坏 JSON / 顶层 list → 200 空 entries，永不 500；
- 路径永不由客户端控制：query 里的 path / home / file 一律忽略，同一响应；路由只有精确 ``/api/search-index``；
- server/paths.search_index_path 与 act/lib/search_index.INDEX_PATH 的布局镜像 pin（server 绝不 import act，测试侧可以）。
真 server 起在随机端口（tests/test_server_common 夹具），零真实网络之外的 IO。
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first
from tests.test_server_common import assert_envelope, http_request, start_server

from act.lib import config, search_index as act_search_index
from server import paths, search_index_source

_SAMPLE = {
    "P-101": {"updated_at": "2026-09-06T10:00:00Z", "text": "推荐信 chen 的会话正文"},
    "P-102": {"updated_at": "2026-09-06T10:00:00Z", "text": ""},          # 空 text → 跳过
    "P-103": {"text": 42},                                                # 非 str → 跳过
    "P-104": "not a dict",                                                # 非 dict → 跳过
    "P-105": {"updated_at": "x", "text": "second card transcript"},
}


def _write_index(home: Path, data, raw: "str | None" = None) -> Path:
    p = paths.search_index_path(home)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(raw if raw is not None else json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return p


class SearchIndexRouteTestCase(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="zai-search-index-"))
        _, self.port = start_server(self, self.home)

    def _get(self, headers=None, path="/api/search-index"):
        return http_request(self.port, "GET", path, headers=headers or {})

    def test_absent_file_is_200_empty_layer_not_an_error(self):
        # 新装机 / 从未 harvest：文件不在 = 层缺席，不是 404 也不是 500；响应体里不出现 home 的绝对路径
        for headers in ({}, {"If-None-Match": '"1-2"'}, {"If-None-Match": "*"}):
            with self.subTest(headers=headers):
                status, resp_headers, body = self._get(headers)
                self.assertEqual(status, 200)
                self.assertEqual(json.loads(body.decode("utf-8")), {"entries": {}, "truncated": False})
                self.assertIsNone(resp_headers.get("ETag"))
                self.assertEqual(resp_headers.get("Cache-Control"), "no-store")
                self.assertNotIn(str(self.home), body.decode("utf-8"))

    def test_projection_sends_text_only_and_skips_bad_entries(self):
        _write_index(self.home, _SAMPLE)
        status, headers, body = self._get()
        self.assertEqual(status, 200)
        self.assertIn("application/json", headers.get("Content-Type", ""))
        self.assertEqual(headers.get("Cache-Control"), "no-store")
        doc = json.loads(body.decode("utf-8"))
        self.assertEqual(doc, {
            "entries": {"P-101": "推荐信 chen 的会话正文", "P-105": "second card transcript"},
            "truncated": False,
        })
        self.assertNotIn("updated_at", body.decode("utf-8"))

    def test_etag_is_mtime_size_and_if_none_match_yields_304(self):
        p = _write_index(self.home, _SAMPLE)
        st = p.stat()
        status, headers, _body = self._get()
        self.assertEqual(status, 200)
        etag = headers.get("ETag")
        self.assertEqual(etag, '"%d-%d"' % (st.st_mtime_ns, st.st_size))
        for header in (etag, "W/" + etag, '"stale", ' + etag, "*"):
            with self.subTest(if_none_match=header):
                status, headers2, body2 = self._get({"If-None-Match": header})
                self.assertEqual(status, 304)
                self.assertEqual(body2, b"")
                self.assertEqual(headers2.get("ETag"), etag)  # 304 也带 ETag，客户端缓存戳不丢
                self.assertEqual(headers2.get("Cache-Control"), "no-store")
                # RFC 9110 §8.6：304 不发 Content-Length（发 0 ≠ 200 的体长即违规）也不发 Content-Type
                self.assertNotIn("Content-Length", headers2)
                self.assertNotIn("Content-Type", headers2)
                # 安全头单一真源（_emit_security_headers）在 304 路上也不漏
                self.assertEqual(headers2.get("X-Content-Type-Options"), "nosniff")
                self.assertEqual(headers2.get("X-Frame-Options"), "DENY")
        # 不命中的 ETag → 200 全体
        status, _h, body3 = self._get({"If-None-Match": '"0-0"'})
        self.assertEqual(status, 200)
        self.assertTrue(body3)

    def test_rewritten_file_changes_etag_and_serves_new_body(self):
        p = _write_index(self.home, _SAMPLE)
        _status, headers, _body = self._get()
        old_etag = headers["ETag"]
        # 换版：内容与长度都变（mtime 分辨率不够时 size 兜底）
        _write_index(self.home, {"P-201": {"updated_at": "x", "text": "brand new transcript body here"}})
        os.utime(p, ns=(p.stat().st_atime_ns, p.stat().st_mtime_ns + 1_000_000))
        status, headers2, body2 = self._get({"If-None-Match": old_etag})
        self.assertEqual(status, 200)
        self.assertNotEqual(headers2["ETag"], old_etag)
        self.assertEqual(json.loads(body2.decode("utf-8"))["entries"],
                         {"P-201": "brand new transcript body here"})

    def test_corrupt_or_wrong_shape_is_empty_layer_never_500(self):
        for raw in ('{"P-101": {"text": "half', "[1, 2, 3]", "", "null"):
            with self.subTest(raw=raw):
                _write_index(self.home, None, raw=raw)
                status, _h, body = self._get()
                self.assertEqual(status, 200)
                self.assertEqual(json.loads(body.decode("utf-8")),
                                 {"entries": {}, "truncated": False})

    def test_size_cap_refuses_to_read_and_says_truncated(self):
        _write_index(self.home, _SAMPLE)
        with mock.patch.object(search_index_source, "MAX_FILE_BYTES", 8):
            status, headers, body = self._get()
        self.assertEqual(status, 200)
        self.assertIn("ETag", headers)
        self.assertEqual(json.loads(body.decode("utf-8")), {"entries": {}, "truncated": True})

    def test_per_entry_text_is_tail_clipped_to_the_act_cap(self):
        self.assertEqual(search_index_source.TEXT_CAP, act_search_index.TEXT_CAP)
        long_text = "a" * 10 + "b" * search_index_source.TEXT_CAP
        _write_index(self.home, {"P-301": {"updated_at": "x", "text": long_text}})
        _status, _h, body = self._get()
        served = json.loads(body.decode("utf-8"))["entries"]["P-301"]
        self.assertEqual(len(served), search_index_source.TEXT_CAP)
        self.assertEqual(served, "b" * search_index_source.TEXT_CAP)  # 尾部保留（act 侧同为 tail cap）

    def test_client_query_never_selects_the_file(self):
        _write_index(self.home, _SAMPLE)
        _status, _h, plain = self._get()
        for query in ("?path=/etc/passwd", "?home=/tmp", "?file=../../config.yaml", "?refresh=1"):
            with self.subTest(query=query):
                status, _headers, body = self._get(path="/api/search-index" + query)
                self.assertEqual(status, 200)
                self.assertEqual(body, plain)
        # 路由只有精确路径：尾段 / 穿越形 → 404（不是本路由）
        for path in ("/api/search-index/", "/api/search-index/../board", "/api/search-index.json"):
            with self.subTest(path=path):
                status, _headers, body = self._get(path=path)
                self.assertEqual(status, 404)
                assert_envelope(self, json.loads(body.decode("utf-8")), "NOT_FOUND")

    def test_head_returns_headers_without_body(self):
        _write_index(self.home, _SAMPLE)
        status, headers, body = http_request(self.port, "HEAD", "/api/search-index")
        self.assertEqual(status, 200)
        self.assertIn("ETag", headers)
        self.assertEqual(body, b"")


class PureFunctionsTestCase(unittest.TestCase):
    def test_etag_matches_truth_table(self):
        etag = '"1-2"'
        self.assertFalse(search_index_source.etag_matches(None, etag))
        self.assertFalse(search_index_source.etag_matches("", etag))
        self.assertTrue(search_index_source.etag_matches(etag, etag))
        self.assertTrue(search_index_source.etag_matches("W/" + etag, etag))
        self.assertTrue(search_index_source.etag_matches('"9-9", ' + etag, etag))
        self.assertTrue(search_index_source.etag_matches("*", etag))
        self.assertFalse(search_index_source.etag_matches('"1-3"', etag))
        self.assertFalse(search_index_source.etag_matches("1-2", etag))  # 没引号不是同一个字面

    def test_snapshot_skips_non_dict_top_level(self):
        home = Path(tempfile.mkdtemp(prefix="zai-search-index-pure-"))
        p = _write_index(home, None, raw='["P-1"]')
        self.assertEqual(search_index_source.snapshot(p, p.stat().st_size),
                         {"entries": {}, "truncated": False})


class PathMirrorTestCase(unittest.TestCase):
    """server/paths.search_index_path 与 act/lib/search_index.INDEX_PATH 的布局 pin（test_server_paths_mirror 同款）。"""

    def test_layout_matches_the_act_writer(self):
        home = Path("/tmp/zai-search-index-pin")
        rel = act_search_index.INDEX_PATH.relative_to(config.HOME)
        self.assertEqual(paths.search_index_path(home), home / rel)


if __name__ == "__main__":
    unittest.main()
