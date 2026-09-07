"""install.sh — the stable daemon copy is never swapped under a running worker
(CONTRACT §55 第五幕 追记 2026-09-06; §23 step `stable_claude`).

`refresh_stable_claude` `mv`s the new copy over the old one atomically, and a
process already running keeps its old inode — which is exactly how a worker
(and, on Claude Code ≥ 2.1.26x, the per-user daemon hosting its spares) ends up
one version behind the file the board's takeover command and every new spawn
run: the 2026-09-04 / 09-07 "worker crashed (exit 143) … exit 1 before init"
shape. So when `lsof` shows any process executing the copy the refresh is
DEFERRED — copy kept, `stable_claude=skipped:in use …`, an info line naming the
path — and the next deploy retries. Pinned by executing the REAL
`stable_claude_in_use` + `refresh_stable_claude` against fake `claude` /
`codesign` / `lsof` shims on PATH (same harness as
tests/test_install_stable_claude.py):

- in use + newer source → deferred: bytes unchanged, no codesign call, no temp
  file, never a deploy failure;
- nobody runs it → refreshed as before;
- byte-identical source → `unchanged` before lsof is even asked;
- no copy yet → created without asking (nothing can be running it);
- lsof unavailable → cannot tell → refresh proceeds (fail-open, today's shape).

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

FAKE_CODESIGN = r'''#!/bin/bash
echo "codesign $*" >> "$FAKE_CALLS"
for a in "$@"; do f="$a"; done
grep -q SIGNED "$f" 2>/dev/null && exit 0
echo "$f: code object is not signed at all" >&2
exit 1
'''

# fake lsof: `-t` output = the pids in $FAKE_LSOF_PIDS (space separated), one
# per line, exit 0; empty → nothing, exit 1 (real lsof's "no match" shape).
FAKE_LSOF = r'''#!/bin/bash
echo "lsof $*" >> "$FAKE_CALLS"
if [ -n "${FAKE_LSOF_PIDS:-}" ]; then
    for p in $FAKE_LSOF_PIDS; do printf '%s\n' "$p"; done
    exit 0
fi
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
class StableClaudeInUseTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="stable-claude-inuse-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        for name, body in (("codesign", FAKE_CODESIGN), ("lsof", FAKE_LSOF)):
            shim = self.bin / name
            shim.write_text(body, encoding="utf-8")
            shim.chmod(0o755)
        self.calls = self.tmp / "calls.log"
        self.calls.write_text("", encoding="utf-8")
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.stable = self.home / "Library" / "Application Support" / "ZelinAIAssistant" / "bin" / "claude"

    def _fake_claude(self, name, version):
        d = self.tmp / name
        d.mkdir(exist_ok=True)
        p = d / "claude"
        p.write_text("#!/bin/sh\n# SIGNED\n"
                     'case "$1" in --version) echo "%s (Claude Code)";; esac\nexit 0\n' % version,
                     encoding="utf-8")
        p.chmod(0o755)
        return p

    def _run(self, src, pids="", path=None):
        script = ("set -uo pipefail\n"
                  'ok()   { printf "  [ ok ] %s\\n" "$1"; }\n'
                  'warn() { printf "  [warn] %s\\n" "$1"; }\n'
                  'info() { printf "  [info] %s\\n" "$1"; }\n'
                  'REPORT_STEPS=""\n'
                  + _install_sh_fn("report_step")
                  + _install_sh_fn("failed_deploy_steps")
                  + _install_sh_line("STABLE_CLAUDE_BIN=")
                  + _install_sh_fn("stable_claude_in_use")
                  + _install_sh_fn("refresh_stable_claude")
                  + 'refresh_stable_claude "$1"\n'
                    'printf "===REPORT===\\n%s" "$REPORT_STEPS"\n'
                    'printf "===FAILED===\\n"\n'
                    'failed_deploy_steps\n')
        env = {**os.environ,
               "HOME": str(self.home),
               "PATH": path if path is not None
               else str(self.bin) + os.pathsep + os.environ.get("PATH", "/usr/bin:/bin"),
               "AIASSISTANT_STABLE_CLAUDE": str(self.stable),
               "FAKE_CALLS": str(self.calls),
               "FAKE_LSOF_PIDS": pids}
        proc = subprocess.run(["bash", "-c", script, "bash", str(src)],
                              capture_output=True, text=True, timeout=60, env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stderr, "", "no stray errors (e.g. a helper the harness forgot)")
        out, _, rest = proc.stdout.partition("===REPORT===\n")
        report, _, failed = rest.partition("===FAILED===\n")
        return out, report.splitlines(), [ln for ln in failed.splitlines() if ln]

    def _calls(self):
        return self.calls.read_text(encoding="utf-8")

    def _no_temp_left(self):
        leftovers = [p.name for p in self.stable.parent.iterdir() if p.name != "claude"]
        self.assertEqual(leftovers, [], "temp copy left beside the stable path")

    def test_in_use_defers_the_refresh_and_keeps_the_copy(self):
        old = self._fake_claude("versions-261", "2.1.261")
        self._run(old)
        self.calls.write_text("", encoding="utf-8")
        new = self._fake_claude("versions-263", "2.1.263")
        out, report, failed = self._run(new, pids="58493 62253")
        self.assertTrue(any(ln.startswith("stable_claude=skipped:in use by 2 process(es)") for ln in report),
                        report)
        self.assertIn("2.1.263", "".join(report), "the report names the version that is waiting")
        self.assertEqual(self.stable.read_bytes(), old.read_bytes(), "not swapped under the workers")
        self.assertNotIn("codesign", self._calls(), "a deferred refresh costs no codesign call")
        self.assertIn("lsof -t -w -- %s" % self.stable, self._calls())
        self.assertIn(str(self.stable), out)
        self.assertIn("next deploy", out)
        self.assertEqual(failed, [], "deferring is never a deploy failure (no rollback)")
        self._no_temp_left()

    def test_idle_copy_is_refreshed_as_before(self):
        old = self._fake_claude("versions-261", "2.1.261")
        self._run(old)
        new = self._fake_claude("versions-263", "2.1.263")
        _, report, failed = self._run(new, pids="")
        self.assertTrue(any(ln.startswith("stable_claude=ok:refreshed:") for ln in report), report)
        self.assertEqual(self.stable.read_bytes(), new.read_bytes())
        self.assertEqual(failed, [])

    def test_byte_identical_source_is_unchanged_without_asking_lsof(self):
        src = self._fake_claude("versions-263", "2.1.263")
        self._run(src)
        self.calls.write_text("", encoding="utf-8")
        _, report, _ = self._run(src, pids="58493")
        self.assertTrue(any(ln.startswith("stable_claude=ok:unchanged:") for ln in report), report)
        self.assertNotIn("lsof", self._calls(), "nothing to swap → nothing to ask")

    def test_first_copy_is_created_without_asking_lsof(self):
        src = self._fake_claude("versions-263", "2.1.263")
        _, report, _ = self._run(src, pids="58493")
        self.assertTrue(any(ln.startswith("stable_claude=ok:created:") for ln in report), report)
        self.assertTrue(self.stable.is_file())
        self.assertNotIn("lsof", self._calls(), "no copy yet → nothing can be running it")

    def test_without_lsof_the_refresh_proceeds(self):
        # PATH without the fake lsof: the helper falls back to /usr/sbin/lsof
        # where it exists (macOS) or reports 0 — either way nobody runs the
        # sandbox file, so the refresh is not blocked by an unanswerable question
        old = self._fake_claude("versions-261", "2.1.261")
        self._run(old)
        new = self._fake_claude("versions-263", "2.1.263")
        only_codesign = self.tmp / "only-codesign"
        only_codesign.mkdir()
        shutil.copy2(self.bin / "codesign", only_codesign / "codesign")
        path = str(only_codesign) + os.pathsep + "/usr/bin:/bin"
        _, report, failed = self._run(new, pids="58493", path=path)
        self.assertTrue(any(ln.startswith("stable_claude=ok:refreshed:") for ln in report), report)
        self.assertEqual(self.stable.read_bytes(), new.read_bytes())
        self.assertEqual(failed, [])

    def test_helper_prints_a_count_and_zero_when_the_copy_is_absent(self):
        script = ("set -uo pipefail\n"
                  + _install_sh_line("STABLE_CLAUDE_BIN=")
                  + _install_sh_fn("stable_claude_in_use")
                  + 'stable_claude_in_use\n')
        env = {**os.environ, "HOME": str(self.home),
               "PATH": str(self.bin) + os.pathsep + "/usr/bin:/bin",
               "AIASSISTANT_STABLE_CLAUDE": str(self.stable),
               "FAKE_CALLS": str(self.calls), "FAKE_LSOF_PIDS": "1 2 3"}
        absent = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env, timeout=30)
        self.assertEqual((absent.returncode, absent.stdout), (0, "0"))
        self._fake_claude("x", "1")
        self.stable.parent.mkdir(parents=True)
        self.stable.write_text("#!/bin/sh\n", encoding="utf-8")
        present = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env, timeout=30)
        self.assertEqual((present.returncode, present.stdout.strip()), (0, "3"))


if __name__ == "__main__":
    unittest.main()
