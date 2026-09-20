"""一个 .pkg 永不写进 git checkout（CONTRACT §74；issue #333 / 2026-09-07 事故）。

Real bash, real rsync, real `mac/scripts/pkg_dest_guard.sh` — and the REAL
postinstall: its text is lifted verbatim out of `mac/package.sh` (the heredoc
that pkgbuild ships as the `scripts/postinstall`), with exactly one line
rewritten — the `MASTER=` path, so the payload is a temp dir instead of
`/Library/Application Support/…`. Everything the postinstall reaches for from
the system (stat / dscl / sudo / id / launchctl / open) is a stub on PATH that
logs argv; PATH never reaches /usr/bin, so nothing on the developer's machine
is read, written, restarted or launched.

Pinned (all four are the 2026-09-07 incident's shape — the retired Sparkle
shell auto-installed v1.0.14's .pkg and its postinstall rsynced an OLDER tag
over the live checkout: 232 tracked files reverted, 18 deleted-upstream files
resurrected untracked, actd restarted with stale code):

  refuses   a destination that is itself a git checkout — nothing copied, the
            checkout's bytes unchanged, install.sh never run, no app launched,
            exit 0 (the payloads DID install; only per-user setup is skipped);
  refuses   a destination reached THROUGH a symlink into a checkout
            (`~/Projects -> /Volumes/Storage/Server/Projects`, the live shape);
  seeds     an empty destination exactly as before — payload copied,
            `install.sh --pkg-postinstall` run;
  fail-closed  the guard missing from the payload = refuse, not proceed, and a
            destination that cannot be resolved (unenterable directory on the
            way) = refuse too — an unprovable path is a dirty path (§74.1);
  §74.2     `install.sh --pkg-postinstall` refuses a checkout by itself (the
            second lock: defence in depth for hand / automation / future
            callers of the flag, NOT for pre-guard payloads — those ship and
            run their own guard-free install.sh, §74.4 边界).

Lives in tests/integration/ (防腐 #7: real subprocesses only here; single-file
budget BUDGET_SECONDS — a handful of sub-second bash runs).
"""
import os
import re
import shutil
import stat as statmod
import subprocess
import sys
import time
import unittest
from pathlib import Path
from tests.scratch_testkit import scratch_dir

REPO = Path(__file__).resolve().parents[2]
PACKAGE_SH = REPO / "mac" / "package.sh"
GUARD_REL = Path("mac") / "scripts" / "pkg_dest_guard.sh"
INSTALL_SH = REPO / "install.sh"
_WIN = sys.platform.startswith("win")
BUDGET_SECONDS = 60
_T0 = [time.monotonic()]

# the one line the test rewrites — asserted verbatim so a rename in package.sh
# fails here loudly instead of silently testing a /Library path that is not there
MASTER_LINE = 'MASTER="/Library/Application Support/ZelinAIAssistant/pipeline"'

CONSOLE_USER = "zelin"
LIVE_BYTES = "LIVE HEAD — never overwrite me\n"
PAYLOAD_BYTES = "PAYLOAD v1.0.14\n"


def setUpModule():
    _T0[0] = time.monotonic()


def tearDownModule():
    elapsed = time.monotonic() - _T0[0]
    if elapsed > BUDGET_SECONDS:
        raise AssertionError("tests/integration/test_pkg_postinstall_git_guard.py "
                             "took %.0fs > %ds budget" % (elapsed, BUDGET_SECONDS))


FAKE_STAT = '#!/bin/bash\necho "$FAKE_CONSOLE_USER"\n'
FAKE_DSCL = '#!/bin/bash\necho "NFSHomeDirectory: $HOME"\n'
FAKE_ID = '#!/bin/bash\necho 501\n'
FAKE_OPEN = '#!/bin/bash\nprintf \'open %s\\n\' "$*" >> "$FAKE_OPEN_LOG"\n'
FAKE_SUDO = r"""#!/bin/bash
# fake sudo: drop `-u <user>` / `-H` and run the rest in place
while [ "$#" -gt 0 ]; do
  case "$1" in
    -u) shift 2 ;;
    -H|-E) shift ;;
    --) shift; break ;;
    *) break ;;
  esac
done
exec "$@"
"""
FAKE_LAUNCHCTL = r"""#!/bin/bash
# fake launchctl: `asuser <uid> <cmd…>` runs <cmd…>; anything else is a no-op
if [ "${1:-}" = "asuser" ]; then shift 2; exec "$@"; fi
exit 0
"""
STUB_INSTALL_SH = r"""#!/bin/bash
printf 'install.sh %s [cwd=%s]\n' "$*" "$PWD" >> "$FAKE_INSTALL_LOG"
exit 0
"""

CORE_TOOLS = ("bash", "sh", "mkdir", "rsync", "sed", "basename", "dirname",
              "cat", "cp", "rm", "ls", "env", "chmod")


def _postinstall_body() -> str:
    text = PACKAGE_SH.read_text(encoding="utf-8")
    m = re.search(r"cat > \"\$SCRIPTS_DIR/postinstall\" <<'POSTINSTALL'\n(.*?)\nPOSTINSTALL\n",
                  text, flags=re.S)
    assert m, "mac/package.sh no longer ships a quoted POSTINSTALL heredoc"
    return m.group(1)


def _write_exec(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | statmod.S_IXUSR | statmod.S_IXGRP | statmod.S_IXOTH)


def _mini_bin(tmp: Path) -> Path:
    """Real tools the scripts need, symlinked so PATH never reaches /usr/bin."""
    mini = tmp / "mini"
    mini.mkdir(exist_ok=True)
    for name in CORE_TOOLS:
        real = shutil.which(name, path="/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin")
        if not real:
            raise unittest.SkipTest("no %s on this machine" % name)
        link = mini / name
        if not link.exists():
            link.symlink_to(real)
    return mini


@unittest.skipIf(_WIN, "the .pkg postinstall is a macOS bash script (stubs are POSIX)")
class PostinstallGitGuardTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(scratch_dir(self, prefix="pkg-guard-"))
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.mini = _mini_bin(self.tmp)
        self.fakebin = self.tmp / "fakebin"
        self.fakebin.mkdir()
        _write_exec(self.fakebin / "stat", FAKE_STAT)
        _write_exec(self.fakebin / "dscl", FAKE_DSCL)
        _write_exec(self.fakebin / "id", FAKE_ID)
        _write_exec(self.fakebin / "sudo", FAKE_SUDO)
        _write_exec(self.fakebin / "launchctl", FAKE_LAUNCHCTL)
        _write_exec(self.fakebin / "open", FAKE_OPEN)
        self.install_log = self.tmp / "install.log"
        self.open_log = self.tmp / "open.log"

        # the pkg payload: the repo export the postinstall would rsync out
        self.master = self.tmp / "master"
        (self.master / "act").mkdir(parents=True)
        (self.master / "act" / "__init__.py").write_text(PAYLOAD_BYTES, encoding="utf-8")
        (self.master / "README.md").write_text("payload readme\n", encoding="utf-8")
        _write_exec(self.master / "install.sh", STUB_INSTALL_SH)
        (self.master / GUARD_REL.parent).mkdir(parents=True)
        shutil.copy2(REPO / GUARD_REL, self.master / GUARD_REL)

        body = _postinstall_body()
        self.assertIn(MASTER_LINE, body,
                      "mac/package.sh's postinstall no longer sets MASTER the way this test rewrites")
        self.script = self.tmp / "postinstall"
        _write_exec(self.script, body.replace(MASTER_LINE, 'MASTER="%s"' % self.master, 1))

    # -- helpers ----------------------------------------------------------- #
    def run_postinstall(self):
        env = {
            "HOME": str(self.home),
            "PATH": os.pathsep.join([str(self.fakebin), str(self.mini)]),
            "FAKE_CONSOLE_USER": CONSOLE_USER,
            "FAKE_INSTALL_LOG": str(self.install_log),
            "FAKE_OPEN_LOG": str(self.open_log),
        }
        return subprocess.run(["bash", str(self.script)], capture_output=True, text=True,
                              timeout=60, env=env, stdin=subprocess.DEVNULL)

    def make_checkout(self, root: Path):
        """A working tree the way the live machine has one: .git + tracked files."""
        (root / ".git").mkdir(parents=True)
        (root / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
        (root / "act").mkdir(parents=True)
        (root / "act" / "__init__.py").write_text(LIVE_BYTES, encoding="utf-8")

    def assert_refused(self, proc, checkout: Path):
        self.assertEqual(proc.returncode, 0, proc.stderr)      # payloads installed fine
        self.assertIn("skipped per-user setup", proc.stderr)
        self.assertIn("no daemon restarted", proc.stderr)
        # the checkout is byte-identical and gained nothing
        self.assertEqual((checkout / "act" / "__init__.py").read_text(encoding="utf-8"), LIVE_BYTES)
        self.assertFalse((checkout / "README.md").exists(), "payload leaked into the checkout")
        self.assertFalse(self.install_log.exists(), "install.sh ran against a checkout")
        self.assertFalse(self.open_log.exists(), "the legacy app was launched anyway")

    # -- refusals ---------------------------------------------------------- #
    def test_destination_that_is_a_checkout_is_refused(self):
        dest = self.home / "Projects" / "zelin-ai-assistant"
        self.make_checkout(dest)
        proc = self.run_postinstall()
        self.assert_refused(proc, dest)
        self.assertIn("is a git checkout", proc.stderr)

    def test_destination_reached_through_a_symlink_into_a_checkout_is_refused(self):
        # the live shape: ~/Projects is a symlink to an external volume whose
        # zelin-ai-assistant IS the development checkout
        volume = self.tmp / "Volumes" / "Storage" / "Server" / "Projects"
        checkout = volume / "zelin-ai-assistant"
        self.make_checkout(checkout)
        (self.home / "Projects").symlink_to(volume)
        proc = self.run_postinstall()
        self.assert_refused(proc, checkout)

    def test_guard_missing_from_the_payload_is_a_refusal_not_a_free_pass(self):
        (self.master / GUARD_REL).unlink()
        proc = self.run_postinstall()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("skipped per-user setup", proc.stderr)
        self.assertIn("rc=127", proc.stderr)
        self.assertFalse((self.home / "Projects" / "zelin-ai-assistant" / "README.md").exists())
        self.assertFalse(self.install_log.exists())

    # -- the fresh-install path is untouched -------------------------------- #
    def test_empty_destination_is_still_seeded_and_configured(self):
        proc = self.run_postinstall()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn("skipped per-user setup", proc.stderr)
        dest = self.home / "Projects" / "zelin-ai-assistant"
        self.assertEqual((dest / "act" / "__init__.py").read_text(encoding="utf-8"), PAYLOAD_BYTES)
        self.assertTrue((dest / "README.md").exists())
        self.assertIn("install.sh --pkg-postinstall",
                      self.install_log.read_text(encoding="utf-8"))


@unittest.skipIf(_WIN, "the guard is a macOS/Linux bash script")
@unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0,
                 "root walks through a 0000 directory, so there is nothing to fail on")
class GuardUnresolvableDestinationTestCase(unittest.TestCase):
    """§74.1 fail-closed — a destination that cannot be resolved is REFUSED.

    `cd … && pwd -P` yields an empty string when some directory on the way is
    not enterable (mode 000, a mount that never came up). Treating that empty
    string as "the resolved ancestor" produces a bogus `/dest`-shaped path that
    is of course inside no checkout — i.e. the guard would wave through a
    destination that really does live in one.
    """

    def setUp(self):
        self.tmp = Path(scratch_dir(self, prefix="pkg-guard-resolve-"))

    def _run(self, dest: Path):
        return subprocess.run(["bash", str(REPO / GUARD_REL), str(dest)],
                              capture_output=True, text=True, timeout=60,
                              stdin=subprocess.DEVNULL)

    def test_an_unenterable_ancestor_inside_a_checkout_is_refused(self):
        checkout = self.tmp / "repo"
        (checkout / ".git").mkdir(parents=True)
        locked = checkout / "locked"
        locked.mkdir()
        # before: the same path resolves and is refused as a checkout
        self.assertEqual(self._run(locked / "dest").returncode, 3)

        locked.chmod(0o000)
        self.addCleanup(locked.chmod, 0o755)    # runs before the rmtree cleanup
        proc = self._run(locked / "dest")
        self.assertEqual(proc.returncode, 3, proc.stdout + proc.stderr)
        self.assertIn("cannot be resolved", proc.stderr)
        self.assertEqual(proc.stdout, "")       # no checkout root to report


@unittest.skipIf(_WIN, "install.sh is a macOS/Linux bash script")
class InstallShSecondLockTestCase(unittest.TestCase):
    """§74.2 — defence in depth for `--pkg-postinstall`: the flag refuses a git
    working tree on its own, so a hand / automation invocation inside a checkout
    (or any future caller) is stopped even if the postinstall gate is bypassed
    or edited out. It does NOT cover a payload built before the guard: such a
    payload rsyncs its own guard-free install.sh over $DEST first and then runs
    THAT one (§74.4 边界)."""

    def setUp(self):
        self.tmp = Path(scratch_dir(self, prefix="pkg-guard-install-"))
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.copy = self.tmp / "zelin-ai-assistant"
        (self.copy / GUARD_REL.parent).mkdir(parents=True)
        shutil.copy2(INSTALL_SH, self.copy / "install.sh")
        shutil.copy2(REPO / GUARD_REL, self.copy / GUARD_REL)

    def _run(self, *args):
        return subprocess.run(["bash", str(self.copy / "install.sh"), *args],
                              capture_output=True, text=True, timeout=60,
                              env={"HOME": str(self.home), "PATH": "/usr/bin:/bin"},
                              stdin=subprocess.DEVNULL)

    def test_pkg_postinstall_refuses_a_checkout_and_changes_nothing(self):
        (self.copy / ".git").mkdir()
        (self.copy / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
        proc = self._run("--pkg-postinstall")
        self.assertEqual(proc.returncode, 3, proc.stdout + proc.stderr)
        self.assertIn("refusing to configure a git checkout", proc.stderr)
        self.assertEqual(proc.stdout, "")                       # nothing ran
        self.assertEqual(list(self.home.iterdir()), [])         # nothing written under HOME
        self.assertFalse((self.copy / "state").exists())
        self.assertFalse((self.copy / "config").exists())

    def test_the_other_modes_are_not_guarded(self):
        """A checkout is exactly where a hand / auto-deploy run belongs (§56)."""
        text = INSTALL_SH.read_text(encoding="utf-8")
        m = re.search(r'\nif \[ "\$PKG_POSTINSTALL" -eq 1 \] && ! bash "\$REPO_ROOT/'
                      r'mac/scripts/pkg_dest_guard\.sh" "\$REPO_ROOT"', text)
        self.assertTrue(m, "install.sh no longer guards --pkg-postinstall with pkg_dest_guard.sh")
        # …and it fires before anything can be written
        self.assertLess(m.start(), text.index('ok()   { printf "  [ ok ] %s\\n" "$1"; }'))


if __name__ == "__main__":
    unittest.main()
