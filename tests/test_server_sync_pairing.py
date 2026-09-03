"""设置「同步 / 配对」的 server 半边（CONTRACT §68.15；原生 SettingsSync.swift）：

- GET /api/sync：只读 state/sync.json（mode=cloud + channel_id = 开着）与 syncd 落下的 pairing-qr.png（开着才带回，base64）；
- POST /api/sync/pair {label?}：子进程 ``act.syncd --pair --json [--label X]``（runner 注入，绝不真起）——JSON 行透传
  + 二维码；label 归一 / ≤64 / 非字串 400；未知键 400；解释器起不来 → no_python；没 JSON → pair_failed（不 500）；
- POST /api/sync/disable {}：``--disable`` → ok + 快照；非空 body 400；四闸。
"""
import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first
from tests.test_server_common import (assert_envelope, auth_headers, get_json, http_request,
                                      post_json, start_server, write_text)

from server import subproc, sync_pairing

PNG = b"\x89PNG\r\n\x1a\n demo"


class _ServerCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-sync-")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        (self.home / "state").mkdir(parents=True)
        _httpd, self.port = start_server(self, self.home)

    def _patch(self, out="", rc=0, err="", side_effect=None):
        calls = []

        def runner(argv, env, cwd, timeout_s):
            calls.append((argv, env, timeout_s))
            if side_effect:
                side_effect()
            return rc, out, err
        patcher = mock.patch.object(subproc, "default_runner", runner)
        patcher.start()
        self.addCleanup(patcher.stop)
        return calls

    def _cloud(self, label="公司 Mac"):
        write_text(self.home / "state" / "sync.json", json.dumps({"mode": "cloud", "channel_id": "ch-1", "label": label}))
        (self.home / "state" / "sync").mkdir(exist_ok=True)
        (self.home / "state" / "sync" / "pairing-qr.png").write_bytes(PNG)


class SnapshotTestCase(_ServerCase):
    def test_off_when_no_config_or_mode_off_and_qr_only_when_on(self):
        status, obj = get_json(self.port, "/api/sync")
        self.assertEqual(status, 200)
        self.assertEqual((obj["enabled"], obj["channel_id"], obj["label"], obj["qr_png_base64"]), (False, "", "", None))
        self.assertTrue(obj["default_label"])
        self._cloud()
        _s, obj = get_json(self.port, "/api/sync")
        self.assertEqual((obj["enabled"], obj["channel_id"], obj["label"]), (True, "ch-1", "公司 Mac"))
        self.assertEqual(base64.b64decode(obj["qr_png_base64"]), PNG)
        write_text(self.home / "state" / "sync.json", json.dumps({"mode": "off", "channel_id": "ch-1", "label": "公司 Mac"}))
        _s, obj = get_json(self.port, "/api/sync")
        self.assertEqual((obj["enabled"], obj["label"], obj["qr_png_base64"]), (False, "公司 Mac", None))   # 名字留着、码不带回

    def test_bad_config_file_is_off_not_500(self):
        write_text(self.home / "state" / "sync.json", "{not json")
        status, obj = get_json(self.port, "/api/sync")
        self.assertEqual((status, obj["enabled"]), (200, False))


class PairTestCase(_ServerCase):
    def _pair_out(self):
        return json.dumps({"channel_id": "ch-9", "qr_blob": "blob", "qr_png_path": str(self.home / "state/sync/pairing-qr.png"),
                           "registered": True, "label": "书房 Mac"})

    def test_pair_runs_syncd_and_returns_channel_label_and_png(self):
        def lands():
            self._cloud("书房 Mac")
        calls = self._patch(self._pair_out(), side_effect=lands)
        status, obj = post_json(self.port, "/api/sync/pair", {"label": "  书房   Mac "})
        self.assertEqual(status, 200)
        self.assertEqual((obj["ok"], obj["channel_id"], obj["label"], obj["registered"]), (True, "ch-9", "书房 Mac", True))
        self.assertEqual(base64.b64decode(obj["qr_png_base64"]), PNG)
        argv, env, timeout_s = calls[0]
        self.assertEqual(argv[1:], ["-m", "act.syncd", "--pair", "--json", "--label", "书房 Mac"])   # 空白归一
        self.assertEqual(env["AIASSISTANT_HOME"], str(self.home))
        self.assertEqual(timeout_s, sync_pairing.PAIR_TIMEOUT_S)

    def test_pair_without_label_keeps_syncd_own_label(self):
        calls = self._patch(self._pair_out())
        for body in ({}, {"label": None}, {"label": "   "}):
            post_json(self.port, "/api/sync/pair", body)
        for argv, _env, _t in calls:
            self.assertEqual(argv[1:], ["-m", "act.syncd", "--pair", "--json"], argv)

    def test_pair_rejects_bad_labels_and_unknown_fields_without_running(self):
        calls = self._patch(self._pair_out())
        status, obj = post_json(self.port, "/api/sync/pair", {"label": 3})
        self.assertEqual(status, 400)
        assert_envelope(self, obj, "INVALID_FIELD")
        status, obj = post_json(self.port, "/api/sync/pair", {"label": "x" * (sync_pairing.LABEL_MAX + 1)})
        self.assertEqual(status, 400)
        status, obj = post_json(self.port, "/api/sync/pair", {"label": "ok", "supabase_url": "https://evil"})
        self.assertEqual(status, 400)
        assert_envelope(self, obj, "UNKNOWN_FIELD")
        self.assertEqual(calls, [])

    def test_pair_failures_are_honest_not_500(self):
        self._patch("", rc=127, err="[Errno 2] No such file or directory")
        _s, obj = post_json(self.port, "/api/sync/pair", {})
        self.assertEqual((obj["ok"], obj["error"]), (False, "no_python"))
        self.assertIn("Errno 2", obj["message"])
        self._patch("not json", rc=1, err="Traceback: boom")
        _s, obj = post_json(self.port, "/api/sync/pair", {})
        self.assertEqual((obj["ok"], obj["error"], obj["message"]), (False, "pair_failed", "Traceback: boom"))
        self._patch(json.dumps({"channel_id": "", "qr_blob": ""}), rc=0)
        _s, obj = post_json(self.port, "/api/sync/pair", {})
        self.assertEqual((obj["ok"], obj["error"]), (False, "pair_failed"))

    def test_pair_and_disable_need_the_write_gates(self):
        self._patch(self._pair_out())
        for path in ("/api/sync/pair", "/api/sync/disable"):
            status, _h, _d = http_request(self.port, "POST", path, body=b"{}",
                                          headers={"Content-Type": "application/json"})
            self.assertEqual(status, 401, path)
            status, _h, _d = http_request(self.port, "POST", path, body=b"{}",
                                          headers=dict(auth_headers(self.port), Origin="https://evil.example"))
            self.assertEqual(status, 403, path)


class DisableTestCase(_ServerCase):
    def test_disable_runs_syncd_and_returns_the_fresh_snapshot(self):
        self._cloud()

        def flips():
            write_text(self.home / "state" / "sync.json", json.dumps({"mode": "off", "channel_id": "ch-1", "label": "公司 Mac"}))
        calls = self._patch(side_effect=flips)
        status, obj = post_json(self.port, "/api/sync/disable", {})
        self.assertEqual(status, 200)
        self.assertEqual((obj["ok"], obj["enabled"], obj["label"], obj["qr_png_base64"]), (True, False, "公司 Mac", None))
        self.assertEqual(calls[0][0][1:], ["-m", "act.syncd", "--disable"])
        self.assertEqual(calls[0][2], sync_pairing.DISABLE_TIMEOUT_S)

    def test_disable_failure_and_body_shape(self):
        self._patch(rc=1, err="boom")
        _s, obj = post_json(self.port, "/api/sync/disable", {})
        self.assertEqual((obj["ok"], obj["error"], obj["message"]), (False, "disable_failed", "boom"))
        status, obj = post_json(self.port, "/api/sync/disable", {"force": True})
        self.assertEqual(status, 400)
        assert_envelope(self, obj, "UNKNOWN_FIELD")


if __name__ == "__main__":
    unittest.main()
