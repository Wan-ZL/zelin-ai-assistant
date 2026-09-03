"""scripts/ci/select_xcode.sh — the pinned-Xcode selector both workflows run (issue #15).

Real bash against a fake /Applications tree; `xcode-select` and `swiftc` are PATH
stubs that record argv (never touches the developer's toolchain). Pinned behavior:
  - the pin in .github/xcode-version is the ONLY input — a newer Xcode on the image
    is never picked over it (no `sort -V | tail -n1` fallback);
  - the pinned bundle missing → exit 1 with an ::error:: line + the installed list;
  - an empty / malformed pin file → exit 1 (a broken pin must not select anything);
  - the committed pin file parses and looks like a version.
Lives in tests/integration/ (防腐 #7：真子进程只许住这里，单文件时间预算)。
"""
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "ci" / "select_xcode.sh"
PIN_FILE = REPO / ".github" / "xcode-version"
BUDGET_SECONDS = 30
_T0 = [time.monotonic()]

STUB_XCODE_SELECT = """#!/bin/sh
printf '%s\\n' "$@" > "$STUB_LOG"
"""
STUB_SWIFTC = """#!/bin/sh
echo "Apple Swift version 6.3 (stub)"
"""


def setUpModule():
    _T0[0] = time.monotonic()


def tearDownModule():
    elapsed = time.monotonic() - _T0[0]
    if elapsed > BUDGET_SECONDS:
        raise AssertionError("tests/integration/test_select_xcode.py took %.0fs > %ds budget"
                             % (elapsed, BUDGET_SECONDS))


@unittest.skipIf(sys.platform.startswith("win"), "bash scripts are POSIX-only")
class SelectXcodeTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="select-xcode-")
        base = Path(self.tmp.name)
        self.apps = base / "Applications"
        self.apps.mkdir()
        self.bin = base / "bin"
        self.bin.mkdir()
        self.log = base / "xcode-select.argv"
        for name, body in (("xcode-select", STUB_XCODE_SELECT), ("swiftc", STUB_SWIFTC)):
            p = self.bin / name
            p.write_text(body, encoding="utf-8")
            p.chmod(0o755)
        self.pin = base / "xcode-version"

    def tearDown(self):
        self.tmp.cleanup()

    def _install(self, version):
        (self.apps / f"Xcode_{version}.app" / "Contents" / "Developer").mkdir(parents=True)

    def _run(self, pin_text):
        self.pin.write_text(pin_text, encoding="utf-8")
        env = dict(os.environ,
                   PATH=f"{self.bin}:{os.environ.get('PATH', '')}",
                   XCODE_VERSION_FILE=str(self.pin),
                   XCODE_APPS_DIR=str(self.apps),
                   XCODE_SUDO="",
                   STUB_LOG=str(self.log))
        return subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True,
                              text=True, timeout=20)

    def test_selects_exactly_the_pinned_xcode_even_when_a_newer_one_exists(self):
        self._install("26.6")
        self._install("27.0")   # newer on the image — must NOT win
        proc = self._run("26.6\n")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        argv = self.log.read_text(encoding="utf-8").split("\n")
        self.assertEqual(argv[:2], ["-s", str(self.apps / "Xcode_26.6.app" / "Contents" / "Developer")])
        self.assertIn("selected Xcode 26.6", proc.stdout)
        self.assertIn("Apple Swift version", proc.stdout)

    def test_missing_pinned_version_fails_loudly_and_lists_installed(self):
        self._install("27.0")
        proc = self._run("26.6\n")
        self.assertEqual(proc.returncode, 1)
        self.assertFalse(self.log.exists(), "xcode-select must not run when the pin is missing")
        self.assertIn("::error::Pinned Xcode 26.6 is not on this runner image", proc.stderr)
        self.assertIn("Xcode_27.0.app", proc.stderr)
        self.assertIn(".github/xcode-version", proc.stderr)

    def test_empty_or_malformed_pin_fails_without_selecting(self):
        self._install("26.6")
        for bad in ("", "latest\n", "26.6; rm -rf /\n"):
            with self.subTest(pin=bad):
                proc = self._run(bad)
                self.assertEqual(proc.returncode, 1)
                self.assertIn("::error::", proc.stderr)
                self.assertFalse(self.log.exists())

    def test_committed_pin_file_is_a_plain_version(self):
        text = PIN_FILE.read_text(encoding="utf-8").strip()
        self.assertRegex(text, r"^\d+(\.\d+)+$", ".github/xcode-version must be e.g. 26.6")

    def test_both_workflows_use_the_shared_selector_and_no_newest_fallback(self):
        for wf in ("ci.yml", "release.yml"):
            with self.subTest(workflow=wf):
                text = (REPO / ".github" / "workflows" / wf).read_text(encoding="utf-8")
                self.assertIn("bash scripts/ci/select_xcode.sh", text)
                self.assertIsNone(re.search(r"Xcode\*\.app.*sort -V", text),
                                  f"{wf} still picks the newest Xcode by sort -V")


if __name__ == "__main__":
    unittest.main()
