"""install_report.main in-process (§23) — the CLI paths a subprocess run cannot
count toward coverage.

Pins: repeatable ``--step`` entries joined with ``--steps-stdin`` text, the
printed report path, an empty agents string → [], and a write failure that
prints a one-line diagnostic and exits 1 (diagnostics must never break an
install).
"""
import io
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first

from act.lib import install_report


class MainInProcessTestCase(unittest.TestCase):
    def test_steps_from_flags_and_stdin_are_joined(self):
        captured = {}

        def fake_write(mode, steps, agents_loaded):
            captured.update(mode=mode, steps=steps, agents=agents_loaded)
            return Path("/tmp/report.json")

        with mock.patch.object(install_report, "write_report", side_effect=fake_write), \
                mock.patch("sys.stdin", io.StringIO("launchd=ok:2 loaded\n")), \
                mock.patch("sys.stdout", io.StringIO()) as out:
            rc = install_report.main(["--mode", "interactive", "--step", "config=ok:kept",
                                      "--step", "version=ok:1.0.0", "--steps-stdin",
                                      "--agents", " a.b  c.d "])
        self.assertEqual(rc, 0)
        self.assertEqual(out.getvalue().strip(), "/tmp/report.json")
        self.assertEqual(captured["mode"], "interactive")
        self.assertEqual([s["name"] for s in captured["steps"]], ["config", "version", "launchd"])
        self.assertEqual(captured["agents"], ["a.b", "c.d"])

    def test_stdin_only_and_empty_agents(self):
        captured = {}

        def fake_write(mode, steps, agents_loaded):
            captured.update(steps=steps, agents=agents_loaded)
            return Path("/tmp/r.json")

        with mock.patch.object(install_report, "write_report", side_effect=fake_write), \
                mock.patch("sys.stdin", io.StringIO("ui=skipped\n")), \
                mock.patch("sys.stdout", io.StringIO()):
            self.assertEqual(install_report.main(["--mode", "pkg-postinstall", "--steps-stdin"]), 0)
        self.assertEqual([s["name"] for s in captured["steps"]], ["ui"])
        self.assertEqual(captured["agents"], [])

    def test_write_failure_is_reported_not_raised(self):
        with mock.patch.object(install_report, "write_report", side_effect=OSError("read-only")), \
                mock.patch("sys.stderr", io.StringIO()) as err, \
                mock.patch("sys.stdout", io.StringIO()):
            self.assertEqual(install_report.main(["--mode", "interactive", "--step", "config=ok"]), 1)
        self.assertIn("install_report: read-only", err.getvalue())


if __name__ == "__main__":
    unittest.main()
