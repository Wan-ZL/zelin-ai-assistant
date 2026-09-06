"""``POST /api/reveal`` 的 add-only ``mode: "open" | "reveal"``（CONTRACT §68.1 追记 (b) 修订；原生 Settings.swift
「打开档案」= NSWorkspace.open，在默认编辑器里打开 .md，不是访达定位）。

钉住：缺省 = reveal（``open -R``，既有客户端零改动）；``mode:"open"`` 只对 ``voice_profile`` 放行——server 跑裸 ``open
<path>``，回执 ``{"ok", "opened"}``；用在 config / skill / mcp_* / 交付物 reveal 上 → 400 INVALID_FIELD 且不 spawn；
词表外的 mode（含非字串）→ 400；``mode:"reveal"`` 显式给出等于缺省；非 darwin 501。子进程走 files.subprocess.run 注入缝。
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first
from tests.test_server_common import assert_envelope, post_json, start_server, write_text

from server import files as files_mod


class RevealOpenModeTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-reveal-open-")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        (self.home / "state").mkdir(parents=True)
        self.profile = self.home / "config" / "voice-profile.default.md"
        write_text(self.profile, "# author\n")
        write_text(self.home / "config.yaml", "features: {}\n")
        _httpd, self.port = start_server(self, self.home)

    def _post(self, payload, platform="darwin"):
        with mock.patch.object(files_mod.sys, "platform", platform), mock.patch.object(files_mod.subprocess, "run") as run:
            status, obj = post_json(self.port, "/api/reveal", payload)
        return status, obj, run

    def test_vocabulary(self):
        self.assertEqual(files_mod.REVEAL_MODES, ("reveal", "open"))
        self.assertEqual(files_mod.resolve_mode(None, "config"), "reveal")
        self.assertEqual(files_mod.resolve_mode("reveal", None), "reveal")
        self.assertEqual(files_mod.resolve_mode("open", "voice_profile"), "open")

    def test_open_mode_runs_bare_open_on_the_effective_profile(self):
        status, obj, run = self._post({"target": "voice_profile", "mode": "open"})
        self.assertEqual(status, 200, obj)
        self.assertEqual(obj, {"ok": True, "opened": str(self.profile)})
        self.assertEqual(run.call_args[0][0], ["open", str(self.profile)])
        # 私有档案落地后 open 的是它（两级候选与 reveal 同序，路径仍由 server 选）
        private = self.home / "state" / "voice-profile.md"
        write_text(private, "# mine\n")
        status, obj, run = self._post({"target": "voice_profile", "mode": "open"})
        self.assertEqual(obj["opened"], str(private))
        self.assertEqual(run.call_args[0][0], ["open", str(private)])

    def test_default_and_explicit_reveal_still_use_open_dash_r(self):
        for payload in ({"target": "voice_profile"}, {"target": "voice_profile", "mode": "reveal"}):
            with self.subTest(payload=payload):
                status, obj, run = self._post(payload)
                self.assertEqual(status, 200, obj)
                self.assertEqual(obj, {"ok": True, "revealed": str(self.profile)})
                self.assertEqual(run.call_args[0][0], ["open", "-R", str(self.profile)])

    def test_open_mode_is_rejected_for_every_other_target(self):
        write_text(self.home / ".mcp.json", json.dumps({"mcpServers": {}}))
        for payload in ({"target": "config", "mode": "open"}, {"target": "mcp_project", "mode": "open"},
                        {"target": "skill", "name": "board-agent", "mode": "open"},
                        {"card_id": "R-1", "mode": "open"}):
            with self.subTest(payload=payload):
                status, obj, run = self._post(payload)
                self.assertEqual(status, 400, obj)
                assert_envelope(self, obj, "INVALID_FIELD")
                self.assertEqual(obj["error"]["details"].get("field"), "mode")
                run.assert_not_called()

    def test_unknown_or_non_string_mode_is_400(self):
        for mode in ("edit", "", 1, True, ["open"], {"m": "open"}):
            with self.subTest(mode=mode):
                status, obj, run = self._post({"target": "voice_profile", "mode": mode})
                self.assertEqual(status, 400, obj)
                assert_envelope(self, obj, "INVALID_FIELD")
                self.assertEqual(obj["error"]["details"].get("choices"), ["reveal", "open"])
                run.assert_not_called()

    def test_missing_profile_is_404_in_open_mode_too(self):
        self.profile.unlink()
        status, obj, run = self._post({"target": "voice_profile", "mode": "open"})
        self.assertEqual(status, 404)
        assert_envelope(self, obj, "NOT_FOUND")
        run.assert_not_called()

    def test_non_darwin_returns_501(self):
        status, obj, run = self._post({"target": "voice_profile", "mode": "open"}, platform="linux")
        self.assertEqual(status, 501)
        assert_envelope(self, obj, "NOT_IMPLEMENTED")
        run.assert_not_called()

    def test_spawn_failure_message_names_the_mode(self):
        # 页面把 message 原样显示：编辑器打不开要念「could not open」，访达定位失败才是「could not reveal」
        for payload, verb in (({"target": "voice_profile", "mode": "open"}, "open"),
                              ({"target": "voice_profile"}, "reveal")):
            with self.subTest(payload=payload):
                with mock.patch.object(files_mod.sys, "platform", "darwin"), \
                        mock.patch.object(files_mod.subprocess, "run", side_effect=OSError("boom")):
                    status, obj = post_json(self.port, "/api/reveal", payload)
                self.assertEqual(status, 404, obj)
                assert_envelope(self, obj, "NOT_FOUND")
                self.assertEqual(obj["error"]["message"], "could not " + verb)
                self.assertEqual(obj["error"]["details"], {"target": "voice_profile", "reason": "boom"})


if __name__ == "__main__":
    unittest.main()
