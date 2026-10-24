"""load_config robustness: config.yaml is a hand-edited file.

v2.0.0: the privacy-default classes this file used to pin (execution.create_github_repo,
the telemetry block, feature flags) retired with the Act pipeline and the §78
zero-telemetry rule (full text in tag ``full``). What stays is the first line of
defence: a malformed file never takes a process down.
"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - ensures the sandbox env is set first

from act.lib import config


class LoadConfigRobustnessTestCase(unittest.TestCase):
    """load_config 防崩（夜间审计批次）：config.yaml 是用户手改文件——坏
    yaml、标量写成块、字符串写成数字，都必须回退默认而不是把 daemon/CLI
    整个带崩）。"""

    def _load_with_yaml(self, body: str) -> config.Config:
        path = Path(tempfile.mkdtemp(prefix="cfg-robust-")) / "config.yaml"
        path.write_text(body, encoding="utf-8")
        with mock.patch.object(config, "CONFIG_PATH", path):
            return config.load_config()

    def test_malformed_yaml_returns_defaults(self):
        cfg = self._load_with_yaml("sources: [\n")  # YAMLError
        self.assertEqual(cfg.language, "zh")
        self.assertEqual(cfg.server_port, config.DEFAULT_SERVER_PORT)

    def test_scalar_blocks_do_not_crash(self):
        cfg = self._load_with_yaml(
            "owner: hi\nsources: 3\napproval: nope\nexecution: [1]\n"
            "telemetry: x\ntrash: y\nvoice: z\nredaction: w\nrecording: v\n"
            "models: m\nupdates: u\nrecap: r\nserver: s\n"
        )
        self.assertIsNone(cfg.claude_bin)
        self.assertTrue(cfg.updates_check_enabled)
        self.assertTrue(cfg.recap_enabled)
        self.assertEqual(cfg.recording_ignored_apps, list(config.DEFAULT_IGNORED_APPS))

    def test_uncastable_numbers_keep_defaults(self):
        cfg = self._load_with_yaml(
            "server:\n  port: abc\n"
            "recording:\n  retention_days: forever\n  media_retention_minutes: soon\n"
        )
        self.assertEqual(cfg.server_port, config.DEFAULT_SERVER_PORT)
        self.assertEqual(cfg.screenpipe_retention_days, 0)
        self.assertEqual(cfg.screenpipe_media_retention_minutes, 60)


if __name__ == "__main__":
    unittest.main()
