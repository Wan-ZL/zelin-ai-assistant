"""ai_fix.main — the CLI's success / --open / context-file / crash paths (§25 让 AI 修).

Pins the P3b split: the optional context file (absent → None, unreadable →
None, readable → its text reaches build_command_file), the printed path,
``--open`` routing through platform.open_path, and the escape hatch that turns
any failure into a printed hint + exit 1 instead of a traceback.
"""
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first

from act import ai_fix
from act.lib import config


class ReadContextTestCase(unittest.TestCase):
    def test_read_context(self):
        self.assertIsNone(ai_fix._read_context(None))
        self.assertIsNone(ai_fix._read_context(""))
        self.assertIsNone(ai_fix._read_context("/nonexistent/ctx.txt"))
        tmp = Path(tempfile.mkdtemp(prefix="aifix-")) / "ctx.txt"
        tmp.write_text("extra", encoding="utf-8")
        self.assertEqual(ai_fix._read_context(str(tmp)), "extra")


class MainTestCase(unittest.TestCase):
    def _cfg(self, enabled=True):
        cfg = config.Config()
        cfg.doctor_ai_fix_enabled = enabled
        return cfg

    def test_success_prints_path_and_opens_on_request(self):
        fake_path = Path("/tmp/zelin-ai-fix-test.command")
        with mock.patch.object(ai_fix.config, "load_config", return_value=self._cfg()), \
                mock.patch.object(ai_fix, "build_command_file", return_value=fake_path) as bcf, \
                mock.patch.object(ai_fix.platform, "open_path") as op, \
                mock.patch("sys.stdout", io.StringIO()) as out:
            self.assertEqual(ai_fix.main([]), 0)
            op.assert_not_called()
            self.assertEqual(ai_fix.main(["--open"]), 0)
        op.assert_called_once_with(fake_path)
        self.assertEqual(out.getvalue().strip().splitlines(), [str(fake_path), str(fake_path)])
        self.assertIsNone(bcf.call_args.kwargs["extra_context"])

    def test_context_file_is_forwarded(self):
        tmp = Path(tempfile.mkdtemp(prefix="aifix-ctx-")) / "ctx.txt"
        tmp.write_text("from the app", encoding="utf-8")
        with mock.patch.object(ai_fix.config, "load_config", return_value=self._cfg()), \
                mock.patch.object(ai_fix, "build_command_file", return_value=Path("/x")) as bcf, \
                mock.patch("sys.stdout", io.StringIO()):
            ai_fix.main(["--context-file", str(tmp)])
        self.assertEqual(bcf.call_args.kwargs["extra_context"], "from the app")

    def test_crash_becomes_hint_and_exit_1(self):
        with mock.patch.object(ai_fix.config, "load_config", return_value=self._cfg()), \
                mock.patch.object(ai_fix, "build_command_file", side_effect=OSError("disk full")), \
                mock.patch("sys.stdout", io.StringIO()) as out:
            self.assertEqual(ai_fix.main([]), 1)
        self.assertIn("disk full", out.getvalue())

    def test_disabled_exits_2(self):
        with mock.patch.object(ai_fix.config, "load_config", return_value=self._cfg(enabled=False)), \
                mock.patch("sys.stdout", io.StringIO()) as out:
            self.assertEqual(ai_fix.main([]), 2)
        self.assertIn("ai_fix_enabled", out.getvalue())


if __name__ == "__main__":
    unittest.main()
