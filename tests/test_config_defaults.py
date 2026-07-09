"""execution.create_github_repo default (PRIVACY.md egress row 8).

Approving a card must NOT silently create GitHub repos for new users: the
default is False. Existing users who set the key explicitly in config.yaml
(either value) keep their behavior unchanged.
"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - ensures the sandbox env is set first

from act.lib import config


class CreateGithubRepoDefaultTestCase(unittest.TestCase):
    def _load_with_yaml(self, body: str) -> config.Config:
        path = Path(tempfile.mkdtemp(prefix="cfg-defaults-")) / "config.yaml"
        path.write_text(body, encoding="utf-8")
        with mock.patch.object(config, "CONFIG_PATH", path):
            return config.load_config()

    def test_dataclass_default_is_false(self):
        self.assertFalse(config.Config().create_github_repo)

    def test_missing_key_resolves_false(self):
        cfg = self._load_with_yaml("execution:\n  memory_inject: true\n")
        self.assertFalse(cfg.create_github_repo)

    def test_explicit_true_is_honored(self):
        cfg = self._load_with_yaml("execution:\n  create_github_repo: true\n")
        self.assertTrue(cfg.create_github_repo)

    def test_explicit_false_is_honored(self):
        cfg = self._load_with_yaml("execution:\n  create_github_repo: false\n")
        self.assertFalse(cfg.create_github_repo)


if __name__ == "__main__":
    unittest.main()
