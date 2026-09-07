"""install.sh — the STABLE daemon copy of the claude binary (CONTRACT §55 第五幕).

macOS keys Full Disk Access for bare executables by path; `~/.local/bin/claude`
is a symlink Claude Code re-points to a new `~/.local/share/claude/versions/<v>`
on every update, so the owner's grant died with every update (live 2026-09-02:
2.1.258 → 2.1.259 turned doctor's `launchd claude` red again). install.sh now
keeps a copy at ONE fixed $HOME path and refreshes it in place. Pinned here by
executing the REAL `refresh_stable_claude` against fake `claude` / `codesign`
shims on PATH, with AIASSISTANT_STABLE_CLAUDE aimed at a tempdir:

- created when absent (report `stable_claude=ok:created…` + the one-time grant
  hint naming the path); byte-identical source → `unchanged`, no rewrite;
- a different source → refreshed IN PLACE at the same path (the grant's
  subject never moves), via a temp file beside it that never lingers;
- refusals keep the previous copy and are warn, never fail (an environment
  problem must not roll a deploy back, §56.5): source with no valid
  Apple-anchored signature (npm-installed node script), a copy that cannot
  run `--version`;
- no claude at all → `skipped`.

POSIX-only (install.sh is the macOS/Linux installer).
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
_WIN = sys.platform.startswith("win")

# fake codesign: `--verify` passes only when the FILE CONTENT carries the
# marker "SIGNED" (a stand-in for a valid Developer ID signature); the flags
# are otherwise ignored. Every call is logged.
FAKE_CODESIGN = r'''#!/bin/bash
echo "codesign $*" >> "$FAKE_CALLS"
for a in "$@"; do f="$a"; done
grep -q SIGNED "$f" 2>/dev/null && exit 0
echo "$f: code object is not signed at all" >&2
exit 1
'''


def _install_sh_fn(name):
    text = (REPO / "install.sh").read_text(encoding="utf-8")
    m = re.search(r"^%s\(\) \{.*?^\}" % re.escape(name), text, flags=re.S | re.M)
    assert m, "install.sh no longer defines %s()" % name
    return m.group(0) + "\n"


def _install_sh_line(prefix):
    text = (REPO / "install.sh").read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.startswith(prefix):
            return line + "\n"
    raise AssertionError("install.sh no longer has a line starting %r" % prefix)


@unittest.skipIf(_WIN, "install.sh is POSIX-only")
class RefreshStableClaudeTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="stable-claude-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        shim = self.bin / "codesign"
        shim.write_text(FAKE_CODESIGN, encoding="utf-8")
        shim.chmod(0o755)
        self.calls = self.tmp / "calls.log"
        self.calls.write_text("", encoding="utf-8")
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.stable = self.home / "Library" / "Application Support" / "ZelinAIAssistant" / "bin" / "claude"

    def _fake_claude(self, name, version, signed=True, runs=True):
        """A fake claude binary: prints `version` for --version (or dies when
        runs=False); carries the SIGNED marker for the fake codesign."""
        d = self.tmp / name
        d.mkdir(exist_ok=True)
        p = d / "claude"
        body = "#!/bin/sh\n# %s\n" % ("SIGNED" if signed else "unsigned")
        if runs:
            body += 'case "$1" in --version) echo "%s (Claude Code)";; esac\nexit 0\n' % version
        else:
            body += 'echo "dyld: broken" >&2\nexit 1\n'
        p.write_text(body, encoding="utf-8")
        p.chmod(0o755)
        return p

    def _run(self, src):
        script = ("set -uo pipefail\n"
                  'ok()   { printf "  [ ok ] %s\\n" "$1"; }\n'
                  'warn() { printf "  [warn] %s\\n" "$1"; }\n'
                  'info() { printf "  [info] %s\\n" "$1"; }\n'
                  'REPORT_STEPS=""\n'
                  + _install_sh_fn("report_step")
                  + _install_sh_fn("failed_deploy_steps")
                  + _install_sh_line("STABLE_CLAUDE_BIN=")
                  + _install_sh_fn("refresh_stable_claude")
                  + 'refresh_stable_claude "$1"\n'
                    'printf "===REPORT===\\n%s" "$REPORT_STEPS"\n'
                    'printf "===FAILED===\\n"\n'
                    'failed_deploy_steps\n')
        env = {**os.environ,
               "HOME": str(self.home),
               "PATH": str(self.bin) + os.pathsep + os.environ.get("PATH", "/usr/bin:/bin"),
               "AIASSISTANT_STABLE_CLAUDE": str(self.stable),
               "FAKE_CALLS": str(self.calls)}
        proc = subprocess.run(["bash", "-c", script, "bash", str(src) if src else ""],
                              capture_output=True, text=True, timeout=60, env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out, _, rest = proc.stdout.partition("===REPORT===\n")
        report, _, failed = rest.partition("===FAILED===\n")
        return out, report.splitlines(), [ln for ln in failed.splitlines() if ln]

    def _no_temp_left(self):
        leftovers = [p.name for p in self.stable.parent.iterdir() if p.name != "claude"] \
            if self.stable.parent.exists() else []
        self.assertEqual(leftovers, [], "temp copy left beside the stable path")

    def test_created_when_absent_and_names_the_one_time_grant(self):
        src = self._fake_claude("versions-258", "2.1.258")
        out, report, failed = self._run(src)
        self.assertTrue(self.stable.is_file() and os.access(str(self.stable), os.X_OK))
        self.assertEqual(self.stable.read_bytes(), src.read_bytes())
        self.assertTrue(any(ln.startswith("stable_claude=ok:created:") for ln in report), report)
        self.assertIn("2.1.258", "".join(report))
        self.assertIn("Full Disk Access", out)
        self.assertIn(str(self.stable), out, "the hint names the exact path to grant")
        self.assertEqual(failed, [])
        self._no_temp_left()

    def test_byte_identical_source_is_unchanged_and_not_rewritten(self):
        src = self._fake_claude("versions-258", "2.1.258")
        self._run(src)
        before = self.stable.stat()
        self.calls.write_text("", encoding="utf-8")
        out, report, failed = self._run(src)
        self.assertTrue(any(ln.startswith("stable_claude=ok:unchanged:") for ln in report), report)
        self.assertEqual(self.stable.stat().st_ino, before.st_ino, "no rewrite when nothing changed")
        self.assertEqual(self.calls.read_text(encoding="utf-8"), "",
                         "an unchanged copy costs no codesign call")
        self.assertNotIn("Full Disk Access", out, "the one-time hint is for creation only")
        self.assertEqual(failed, [])

    def test_new_claude_version_refreshes_in_place_at_the_same_path(self):
        old = self._fake_claude("versions-258", "2.1.258")
        self._run(old)
        new = self._fake_claude("versions-259", "2.1.259")
        out, report, failed = self._run(new)
        self.assertTrue(any(ln.startswith("stable_claude=ok:refreshed:") for ln in report), report)
        self.assertIn("2.1.259", "".join(report))
        self.assertEqual(self.stable.read_bytes(), new.read_bytes())
        # same path forever — that is the whole point (the FDA grant is path-keyed)
        self.assertEqual(sorted(p.name for p in self.stable.parent.iterdir()), ["claude"])
        self.assertNotIn("Full Disk Access", out, "a refresh needs no new grant")
        self.assertEqual(failed, [])

    def test_unsigned_source_is_refused_and_keeps_the_previous_copy(self):
        good = self._fake_claude("versions-258", "2.1.258")
        self._run(good)
        npm = self._fake_claude("npm", "2.1.300", signed=False)
        out, report, failed = self._run(npm)
        self.assertTrue(any(ln.startswith("stable_claude=warn:refused:") for ln in report), report)
        self.assertIn("previous copy stays", "".join(report))
        self.assertEqual(self.stable.read_bytes(), good.read_bytes(), "the old copy is untouched")
        self.assertIn("signature", out)
        self.assertEqual(failed, [], "a refusal is never a deploy failure (no rollback)")
        self._no_temp_left()

    def test_unsigned_source_on_a_fresh_machine_creates_nothing(self):
        npm = self._fake_claude("npm", "2.1.300", signed=False)
        _, report, failed = self._run(npm)
        self.assertFalse(self.stable.exists())
        self.assertTrue(any(ln.startswith("stable_claude=warn:refused:") for ln in report), report)
        self.assertNotIn("previous copy", "".join(report))
        self.assertEqual(failed, [])

    def test_copy_that_cannot_run_version_is_discarded(self):
        good = self._fake_claude("versions-258", "2.1.258")
        self._run(good)
        broken = self._fake_claude("broken", "x", runs=False)
        _, report, failed = self._run(broken)
        self.assertTrue(any(ln.startswith("stable_claude=warn:copy does not run --version") for ln in report),
                        report)
        self.assertEqual(self.stable.read_bytes(), good.read_bytes())
        self.assertEqual(failed, [])
        self._no_temp_left()

    def test_no_claude_is_skipped(self):
        _, report, failed = self._run(None)
        self.assertIn("stable_claude=skipped:no claude to copy", report)
        self.assertFalse(self.stable.exists())
        self.assertEqual(failed, [])

    def test_source_symlink_is_followed(self):
        # ~/.local/bin/claude -> ~/.local/share/claude/versions/<v>: the copy is
        # of the real file, and the report names the resolved version
        real = self._fake_claude("versions-259", "2.1.259")
        link = self.tmp / "local-bin" / "claude"
        link.parent.mkdir()
        link.symlink_to(real)
        _, report, _ = self._run(link)
        self.assertTrue(any(ln.startswith("stable_claude=ok:created:") for ln in report), report)
        self.assertEqual(self.stable.read_bytes(), real.read_bytes())
        self.assertFalse(self.stable.is_symlink(), "a copy, never a symlink (TCC follows the exec path)")


@unittest.skipIf(_WIN, "install.sh is POSIX-only")
class StablePathContractTestCase(unittest.TestCase):
    """install.sh and act/lib/config.py must agree on the default path and on
    the override variable — the doctor names what install.sh writes."""

    def test_default_path_matches_config(self):
        from act.lib import config
        line = _install_sh_line("STABLE_CLAUDE_BIN=")
        self.assertIn("AIASSISTANT_STABLE_CLAUDE", line)
        home = "/Users/fixture"
        rendered = subprocess.run(
            ["bash", "-c", "unset AIASSISTANT_STABLE_CLAUDE; HOME=%s; %s printf '%%s' \"$STABLE_CLAUDE_BIN\""
             % (home, line)], capture_output=True, text=True, timeout=30)
        self.assertEqual(rendered.returncode, 0, rendered.stderr)
        expected = str(config.STABLE_CLAUDE_BIN).replace(str(Path.home()), home, 1)
        self.assertEqual(rendered.stdout, expected)

    def test_default_lives_under_home_not_in_the_repo(self):
        from act.lib import config
        self.assertTrue(str(config.STABLE_CLAUDE_BIN).startswith(str(Path.home()) + os.sep))
        self.assertFalse(str(config.STABLE_CLAUDE_BIN).startswith(str(config.HOME) + os.sep),
                         "the copy must not live in the checkout (external volume = TCC-gated)")
