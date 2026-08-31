"""W18 remote direct-run gate at the webui ingress (act/webui.py).

Runs entirely inside the sandbox AIASSISTANT_HOME (tests/__init__.py). Starts a
real loopback server on an ephemeral port and drives it with http.client so
Host / Origin / token headers can be set exactly (live tests/test_webui.py 的
harness 同款)。

Covers (vnext-amendments §W18):
  * default OFF   — capture mode:"run" -> 200 + notice, inbox 文件无 mode 字段
                    (降级为普通 propose capture,不报错不吞任务)
  * config opt-in — remote.allow_direct_run: true -> mode:"run" 原样进 inbox,
                    响应无 notice
  * §34 词表不变 — mode ≠ "run" / 非 capture 带 mode 照旧 400 fail-closed
  * 普通 capture  — 无 mode 时行为零变化(无 notice)
"""
import http.client
import json
import threading
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act.* import

from act import actd, webui
from act.lib import config, registry


class WebUIRemoteGateTestCase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        for p in config.INBOX_DIR.glob("*.json"):
            p.unlink()
        # 闸门读 config.yaml(sandbox HOME 下);默认不存在 = 闸门关。
        self.addCleanup(self._remove_config)
        self._remove_config()

        self.httpd, self.url, self.token = webui.make_server()
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)

    @staticmethod
    def _remove_config():
        if config.CONFIG_PATH.exists():
            config.CONFIG_PATH.unlink()

    # -- request helper ---------------------------------------------------- #
    def _post_inbox(self, payload: dict):
        headers = {
            "X-Webui-Token": self.token,
            "Origin": f"http://127.0.0.1:{self.port}",
            "Content-Type": "application/json",
        }
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request("POST", "/api/inbox",
                         body=json.dumps(payload), headers=headers)
            resp = conn.getresponse()
            return resp.status, json.loads(resp.read() or b"{}")
        finally:
            conn.close()

    def _single_capture_record(self) -> dict:
        files = list(config.INBOX_DIR.glob("capture-*.json"))
        self.assertEqual(len(files), 1)
        return json.loads(files[0].read_text(encoding="utf-8"))

    # -- W18: default OFF = downgrade, not error ---------------------------- #
    def test_mode_run_downgraded_by_default(self):
        status, body = self._post_inbox(
            {"action": "capture", "text": "远端直跑一下", "mode": "run"})
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertIn("notice", body)
        self.assertIn("saved as a proposal", body["notice"])
        rec = self._single_capture_record()
        self.assertEqual(rec["action"], "capture")
        self.assertEqual(rec["text"], "远端直跑一下")
        self.assertNotIn("mode", rec)  # 降级 = 普通 propose capture

    def test_plain_capture_unchanged_no_notice(self):
        status, body = self._post_inbox(
            {"action": "capture", "text": "普通提案"})
        self.assertEqual(status, 200)
        self.assertNotIn("notice", body)
        rec = self._single_capture_record()
        self.assertNotIn("mode", rec)

    # -- W18: config opt-in forwards mode:"run" ------------------------------ #
    def test_mode_run_forwarded_when_opted_in(self):
        config.CONFIG_PATH.write_text(
            "remote:\n  allow_direct_run: true\n", encoding="utf-8")
        status, body = self._post_inbox(
            {"action": "capture", "text": "run it now", "mode": "run"})
        self.assertEqual(status, 200)
        self.assertNotIn("notice", body)
        rec = self._single_capture_record()
        self.assertEqual(rec["mode"], "run")
        self.assertEqual(rec["text"], "run it now")

    def test_explicit_false_still_downgrades(self):
        config.CONFIG_PATH.write_text(
            "remote:\n  allow_direct_run: false\n", encoding="utf-8")
        status, body = self._post_inbox(
            {"action": "capture", "text": "x", "mode": "run"})
        self.assertEqual(status, 200)
        self.assertIn("notice", body)
        self.assertNotIn("mode", self._single_capture_record())

    def test_malformed_config_fails_closed(self):
        # 坏 YAML 绝不打开闸门(risk.remote_direct_run_allowed fail-closed)。
        config.CONFIG_PATH.write_text("remote: [::bad", encoding="utf-8")
        status, body = self._post_inbox(
            {"action": "capture", "text": "x", "mode": "run"})
        self.assertEqual(status, 200)
        self.assertIn("notice", body)
        self.assertNotIn("mode", self._single_capture_record())

    # -- W18: 闸门每请求热读 config，开合无需重启 server --------------------- #
    def test_gate_flips_per_request_without_restart(self):
        status, body = self._post_inbox(
            {"action": "capture", "text": "第一发", "mode": "run"})
        self.assertIn("notice", body)                    # 默认关：降级
        for p in config.INBOX_DIR.glob("*.json"):
            p.unlink()
        config.CONFIG_PATH.write_text(
            "remote:\n  allow_direct_run: true\n", encoding="utf-8")
        status, body = self._post_inbox(
            {"action": "capture", "text": "第二发", "mode": "run"})
        self.assertEqual(status, 200)
        self.assertNotIn("notice", body)                 # 同一 server：已放行
        self.assertEqual(self._single_capture_record()["mode"], "run")

    # -- W18 端到端：default-deny 的降级记录在 actd 里长成普通提案 ------------ #
    def test_downgraded_capture_becomes_proposal_never_dispatches(self):
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()
        self._post_inbox(
            {"action": "capture", "text": "远端想直跑的活", "mode": "run"})
        ex_mock = mock.MagicMock()
        with mock.patch.object(actd, "executor", ex_mock), \
                mock.patch.object(actd.notify, "notify"):
            actd.process_inbox()
        reqs = registry.load_all()
        self.assertEqual(len(reqs), 1)
        req = reqs[0]
        self.assertEqual(req.status, registry.State.RAISING.value)  # 提案管线
        self.assertEqual(req.origin_trust, "hand")   # 远端捕获仍是 owner 手打
        ex_mock.dispatch.assert_not_called()         # 绝没有任何东西被直跑

    def test_gate_open_mode_run_still_plain_capture_in_actd(self):
        # 现状钉子（amendments W-actd 未接线清单）：§34 direct-run 的 actd 侧
        # 语义尚未接线——闸门放行的 mode:"run" 记录目前仍按普通 capture 走
        # 提案管线（backward-safe，宁可少跑不可多跑）。§34 落地时更新本测试。
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()
        config.CONFIG_PATH.write_text(
            "remote:\n  allow_direct_run: true\n", encoding="utf-8")
        self._post_inbox(
            {"action": "capture", "text": "闸门开着的直跑请求", "mode": "run"})
        ex_mock = mock.MagicMock()
        with mock.patch.object(actd, "executor", ex_mock), \
                mock.patch.object(actd.notify, "notify"):
            actd.process_inbox()
        reqs = registry.load_all()
        self.assertEqual(len(reqs), 1)
        self.assertEqual(reqs[0].status, registry.State.RAISING.value)
        ex_mock.dispatch.assert_not_called()

    # -- §34 词表 fail-closed 不受闸门影响 ----------------------------------- #
    def test_undefined_mode_still_400(self):
        for payload in (
            {"action": "capture", "text": "x", "mode": "walk"},
            {"action": "capture", "text": "x", "mode": 5},
            {"action": "capture", "text": "x", "mode": ["run"]},
            {"id": "R-1", "action": "approve", "mode": "run"},  # mode ⇒ capture only
        ):
            status, _ = self._post_inbox(payload)
            self.assertEqual(status, 400, payload)
        self.assertEqual(list(config.INBOX_DIR.glob("*.json")), [])


if __name__ == "__main__":
    unittest.main()
