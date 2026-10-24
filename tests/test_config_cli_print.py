"""`python3 -m act.lib.config --print-path / --print-value` — the shell's window into
the config layer (CONTRACT §18 cron chain; §72.4 media retention; §78 isolation
paths + the shared claude binary).

Pinned: the obsidian keys resolve overrides → config.yaml → the derived default
and anchor a relative value on AIASSISTANT_HOME (cron's cwd is undefined); a
config that cannot load prints the built-in default — never a traceback, never
an empty line; the two §78 paths bypass config.yaml entirely; `claude_bin`
prints the shared resolution, and its own fallback when resolution explodes.
"""
import contextlib
import io
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first

from act.lib import config


def _run(argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = config.main(argv)
    return rc, buf.getvalue()


class PrintPathTestCase(unittest.TestCase):
    def test_an_absolute_obsidian_dir_prints_as_is(self):
        cfg = config.Config()
        cfg.obsidian_wiki = "/vault/4 - wiki"
        with mock.patch.object(config, "load_config", return_value=cfg):
            self.assertEqual(_run(["--print-path", "obsidian_wiki"]), (0, "/vault/4 - wiki\n"))

    def test_a_relative_value_is_anchored_on_the_home(self):
        cfg = config.Config()
        cfg.obsidian_raw = "notes/raw"
        with mock.patch.object(config, "load_config", return_value=cfg):
            rc, out = _run(["--print-path", "obsidian_raw"])
        self.assertEqual((rc, out.strip()), (0, str(config.HOME / "notes" / "raw")))

    def test_unset_or_unloadable_prints_the_derived_default(self):
        vault = Path(config.DEFAULT_OBSIDIAN_VAULT).expanduser()
        cfg = config.Config()
        cfg.obsidian_raw = "  "
        with mock.patch.object(config, "load_config", return_value=cfg):
            self.assertEqual(_run(["--print-path", "obsidian_raw"])[1].strip(), str(vault / "2 - raw"))
        with mock.patch.object(config, "load_config", side_effect=RuntimeError("broken yaml")):
            rc, out = _run(["--print-path", "obsidian_unprocessed"])
        self.assertEqual((rc, out.strip()), (0, str(vault / "1 - unprocessed")))

    def test_the_isolation_paths_never_read_config(self):
        with mock.patch.object(config, "load_config", side_effect=AssertionError("read config")):
            rc, out = _run(["--print-path", "claude_config_dir"])
            self.assertEqual((rc, out.strip()), (0, str(config.claude_config_dir())))
            rc, out = _run(["--print-path", "headless_cwd"])
            self.assertEqual((rc, out.strip()), (0, config.headless_cwd()))


class PrintValueTestCase(unittest.TestCase):
    def test_media_retention_prints_the_effective_value_or_the_factory_one(self):
        cfg = config.Config()
        cfg.screenpipe_media_retention_minutes = 120
        with mock.patch.object(config, "load_config", return_value=cfg):
            self.assertEqual(_run(["--print-value", "screenpipe_media_retention_minutes"]), (0, "120\n"))
        with mock.patch.object(config, "load_config", side_effect=OSError("unreadable")):
            self.assertEqual(_run(["--print-value", "screenpipe_media_retention_minutes"]), (0, "60\n"))

    def test_claude_bin_is_the_shared_resolution(self):
        with mock.patch.object(config, "resolve_claude_bin", return_value="/opt/claude"):
            self.assertEqual(_run(["--print-value", "claude_bin"]), (0, "/opt/claude\n"))

    def test_claude_bin_falls_back_when_resolution_explodes(self):
        with mock.patch.object(config, "resolve_claude_bin", side_effect=RuntimeError("boom")), \
                mock.patch.object(config.shutil, "which", return_value=None):
            rc, out = _run(["--print-value", "claude_bin"])
        self.assertEqual((rc, out.strip()), (0, str(Path.home() / ".local" / "bin" / "claude")))

    def test_an_unknown_key_is_an_argparse_error(self):
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            config.main(["--print-value", "skip_permissions"])


if __name__ == "__main__":
    unittest.main()
