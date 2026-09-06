"""GET /api/voice + POST /api/reveal {target:"voice_profile"} —— 语气档案区「当前生效」状态行（docs/VOICE.md；
CONTRACT §68.1 追记 / §49）。

钉住：两级候选与 act/lib/dispatch_prompt 同序（私有 state/voice-profile.md > 出厂 config/voice-profile.default.md）；
``enabled`` 读设置目录 voice_enabled 的 effective（override 优先）；两个都不在 → effective_path null、reveal 404；
reveal 走 open -R、客户端不传路径。
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first
from tests.test_server_common import get_json, post_json, start_server, write_text

from server import files as files_mod
from server import voice_profile


class VoiceProfileTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-voice-")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        (self.home / "state").mkdir(parents=True)
        _httpd, self.port = start_server(self, self.home)

    def test_snapshot_follows_the_dispatch_prompt_fallback_order(self):
        status, obj = get_json(self.port, "/api/voice")
        self.assertEqual(status, 200)
        self.assertEqual((obj["enabled"], obj["private_exists"], obj["default_exists"], obj["effective_path"]),
                         (True, False, False, None))
        self.assertEqual(obj["private_path"], str(self.home / "state" / "voice-profile.md"))
        write_text(self.home / "config" / "voice-profile.default.md", "# author\n")
        _s, obj = get_json(self.port, "/api/voice")
        self.assertEqual((obj["default_exists"], obj["effective_path"]), (True, str(self.home / "config" / "voice-profile.default.md")))
        write_text(self.home / "state" / "voice-profile.md", "# mine\n")
        _s, obj = get_json(self.port, "/api/voice")
        self.assertEqual((obj["private_exists"], obj["effective_path"]), (True, str(self.home / "state" / "voice-profile.md")))

    def test_enabled_reads_the_settings_override(self):
        write_text(self.home / "state" / "settings_overrides.json", json.dumps({"voice_enabled": False}))
        _s, obj = get_json(self.port, "/api/voice")
        self.assertFalse(obj["enabled"])
        self.assertEqual(voice_profile.snapshot(self.home)["enabled"], False)

    def test_reveal_voice_profile_opens_the_effective_file_or_404(self):
        with mock.patch.object(files_mod.sys, "platform", "darwin"), mock.patch.object(files_mod.subprocess, "run") as run:
            status, obj = post_json(self.port, "/api/reveal", {"target": "voice_profile"})
            self.assertEqual(status, 404)
            run.assert_not_called()
            write_text(self.home / "config" / "voice-profile.default.md", "# author\n")
            status, obj = post_json(self.port, "/api/reveal", {"target": "voice_profile"})
            self.assertEqual(status, 200)
            self.assertEqual(obj["revealed"], str(self.home / "config" / "voice-profile.default.md"))
            self.assertEqual(run.call_args[0][0], ["open", "-R", str(self.home / "config" / "voice-profile.default.md")])


if __name__ == "__main__":
    unittest.main()
