"""`npm run build` succeeds from a copy of web/ that lives OUTSIDE the repo (CONTRACT §56.5, §69.4).

install.sh's ui step mirrors web/ (minus node_modules / dist) into a build dir
under $HOME and runs `npm ci` + `npm run build` THERE — homebrew node is
TCC-denied on an external-volume checkout under launchd, so node never touches
the repo. The 2026-09-03 fresh-install acceptance run died on exactly that
shape: `tsc --noEmit` type-checked src/parity.test.tsx, whose static imports
of `../../ui/parity/*.json` resolve only inside the repo → ui=fail → the
install reported one failed step. This is the real-npm reproduction of that
mirror (the static half is tests/test_web_build_self_contained.py):

  - copy web/ the way install.sh does (same exclude set), point node_modules
    at the repo's already-installed tree (no `npm ci`, no network), run the
    real `npm run build` → exit 0 and dist/index.html present;
  - the full `npm run typecheck` (tsconfig.json, tests included) passes from
    that copy too — the parity suite reaches the repo fixtures through
    import.meta.glob, never through a path tsc has to resolve.

Skips when npm is absent or web/node_modules is not installed (the plain
unittest jobs; the qa-gates job and a developer machine have both). Lives in
tests/integration/ (防腐 #7: real subprocesses only here, single-file time
budget BUDGET_SECONDS — one tsc + one vite build, well under a minute).
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
WEB = REPO / "web"
NODE_MODULES = WEB / "node_modules"
_WIN = sys.platform.startswith("win")
BUDGET_SECONDS = 120
_T0 = [time.monotonic()]
# install.sh ui_sync_web_sources: rsync --exclude node_modules --exclude dist --exclude '.zai-*'
_MIRROR_EXCLUDES = ("node_modules", "dist")


def setUpModule():
    _T0[0] = time.monotonic()


def tearDownModule():
    elapsed = time.monotonic() - _T0[0]
    if elapsed > BUDGET_SECONDS:
        raise AssertionError("tests/integration/test_web_build_outside_repo.py took %.0fs > %ds budget"
                             % (elapsed, BUDGET_SECONDS))


def _mirror_ignore(_dir, names):
    return [n for n in names if n in _MIRROR_EXCLUDES or n.startswith(".zai-")]


@unittest.skipIf(_WIN, "install.sh's mirror build is POSIX-only (install.ps1 has no ui step)")
@unittest.skipUnless(shutil.which("npm") and (NODE_MODULES / ".package-lock.json").exists(),
                     "needs npm and an installed web/node_modules (cd web && npm ci)")
class WebBuildOutsideRepoTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # the copy lives under a temp dir that is NOT inside the repo: ../../ui/parity
        # resolves to nothing there, exactly like ~/Library/Caches/zelin-ai-assistant/web-build
        cls.tmp = Path(tempfile.mkdtemp(prefix="zai-web-mirror-"))
        cls.copy = cls.tmp / "web-build"
        shutil.copytree(WEB, cls.copy, ignore=_mirror_ignore, symlinks=True)
        os.symlink(NODE_MODULES, cls.copy / "node_modules")
        assert not (cls.tmp / "ui").exists()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _npm(self, *script):
        env = dict(os.environ, CI="1")
        return subprocess.run(["npm", "run", *script], cwd=str(self.copy), env=env,
                              capture_output=True, text=True, timeout=BUDGET_SECONDS)

    def test_build_succeeds_and_emits_dist(self):
        proc = self._npm("build")
        self.assertEqual(proc.returncode, 0, "npm run build failed in the mirror:\n%s\n%s"
                         % (proc.stdout[-3000:], proc.stderr[-3000:]))
        self.assertTrue((self.copy / "dist" / "index.html").is_file(), "no dist/index.html after the build")
        self.assertNotIn("TS2307", proc.stdout + proc.stderr)

    def test_full_typecheck_passes_in_the_mirror_too(self):
        proc = self._npm("typecheck")
        self.assertEqual(proc.returncode, 0, "npm run typecheck failed in the mirror:\n%s\n%s"
                         % (proc.stdout[-3000:], proc.stderr[-3000:]))


if __name__ == "__main__":
    unittest.main()
