"""scripts/build_version.sh + shell/build.sh 的版本盖章判例（CONTRACT §56.1）——真 bash + 真 git。

住在 tests/integration/（防腐 #7：真 IO 只许住这里，单文件时间预算 BUDGET_SECONDS）。
夹具 = 临时 git repo，里面是**真的** act/__init__.py、act/lib/version.py、
scripts/version_stamp.py、scripts/build_version.sh、shell/build.sh、shell/Info.plist
的逐字拷贝 + 桩 Swift 源；PATH 前置一个假 `swiftc`（报 6.0、把 -o 目标写成空壳）。
不出网、不起 launchd、不碰真 $HOME（HOME 指到临时目录）、不装到 /Applications。

2026-09-02 v0.48.21 首次实战：auto-deploy 下 shell/build.sh 用 PATH 首位的 Homebrew
python3 跑 stamper，被 TCC 拒在外置卷外，`2>/dev/null` 吞掉报错，VERSION 为空，装到
/Applications 的壳报 Info.plist 占位 0.1.0。钉住的行为：
  - build_version.sh：stdout 恰一行版本；tag 在 → tag、写 act/_version.py；
    $AIASSISTANT_PYTHON 最先、pin（config/runtime.json）、/usr/bin/python3、PATH 的
    python3 依次；被拒（EPERM）的解释器跳过并在 stderr 点名 + 带它的最后一行 stderr；
  - stamper 能读不能写（act/ 只读）→ 退到**只读的 stamper**（同一决策：git 优先；陈旧的
    act/_version.py 不算——`import act` 会让 stamp 先赢，Codex P1）；stderr 带 WARN，退出 0；
  - 都答不上 → 退出 1，stderr 带每个解释器的诊断；**stdout 为空**；
  - shell/build.sh：bundle 的 CFBundleShortVersionString / CFBundleVersion == 版本；
    版本答不上 → BUILD FAILED（退出非零、没有 bundle）而非带占位出厂。
"""
import os
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
_WIN = sys.platform.startswith("win")
_DARWIN = sys.platform == "darwin"
BUDGET_SECONDS = 60
_T0 = [time.monotonic()]

COPIED = ("act/__init__.py", "act/lib/__init__.py", "act/lib/version.py",
          "scripts/version_stamp.py", "scripts/build_version.sh",
          "shell/build.sh", "shell/Info.plist")

FAKE_SWIFTC = r"""#!/bin/sh
# fake swiftc: the version banner build.sh parses + an empty executable at -o
case "${1:-}" in --version) echo "Apple Swift version 6.0 (fake)"; exit 0 ;; esac
out=""
while [ $# -gt 0 ]; do
    if [ "$1" = "-o" ]; then out="$2"; shift; fi
    shift
done
[ -n "$out" ] || exit 1
printf '#!/bin/sh\nexit 0\n' > "$out" && chmod +x "$out"
"""

DENIED_PYTHON = r"""#!/bin/sh
# the TCC-denied Homebrew python3 under launchd: cannot open any file of the checkout
echo "$0: can't open file '$1': [Errno 1] Operation not permitted" >&2
exit 2
"""


def setUpModule():
    _T0[0] = time.monotonic()


def tearDownModule():
    elapsed = time.monotonic() - _T0[0]
    if elapsed > BUDGET_SECONDS:
        raise AssertionError("tests/integration/test_build_version.py took %.0fs > %ds budget"
                             % (elapsed, BUDGET_SECONDS))


def _git(cwd, *args):
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false", *args],
        cwd=str(cwd), capture_output=True, text=True, timeout=60, check=True).stdout.strip()


@unittest.skipIf(_WIN, "bash scripts are POSIX-only")
class BuildVersionTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="buildver-"))
        self.addCleanup(shutil.rmtree, str(self.tmp), ignore_errors=True)
        self.repo = self.tmp / "repo"
        for rel in COPIED:
            dst = self.repo / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(str(REPO / rel), str(dst))
        (self.repo / "shell" / "Sources").mkdir()
        (self.repo / "shell" / "Sources" / "main.swift").write_text("// stub\n", encoding="utf-8")
        (self.repo / "shared" / "Sources").mkdir(parents=True)
        (self.repo / "shared" / "Sources" / "I18n.swift").write_text("// stub\n", encoding="utf-8")
        init = self.repo / "act" / "__init__.py"
        init.write_text(re.sub(r'^__version__ = "[^"]*"$', '__version__ = "0.48.16"',
                               init.read_text(encoding="utf-8"), count=1, flags=re.M), encoding="utf-8")
        _git(self.tmp, "init", "-q", str(self.repo))
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-q", "-m", "seed")
        _git(self.repo, "tag", "v0.48.16")
        # PATH: our python3 first, then the platform tools build.sh needs (plutil, codesign, sed…)
        self.bindir = self.tmp / "bin"
        self.bindir.mkdir()
        os.symlink(sys.executable, str(self.bindir / "python3"))
        self.env = {"PATH": "%s:/usr/bin:/bin:/usr/sbin:/sbin" % self.bindir, "HOME": str(self.tmp)}

    # -- helpers ------------------------------------------------------------ #

    def denied_python(self):
        fake = self.tmp / "homebrew" / "python3"
        fake.parent.mkdir(parents=True, exist_ok=True)
        fake.write_text(DENIED_PYTHON, encoding="utf-8")
        fake.chmod(0o755)
        return str(fake)

    def break_everything(self):
        """No interpreter can answer: the stamper exits 1 and `import act` raises."""
        (self.repo / "scripts" / "version_stamp.py").write_text(
            "import sys\nsys.exit('stamper boom')\n", encoding="utf-8")
        (self.repo / "act" / "__init__.py").write_text("raise RuntimeError('act boom')\n", encoding="utf-8")

    def build_version(self, env=None):
        merged = dict(self.env, **(env or {}))
        return subprocess.run(["/bin/bash", str(self.repo / "scripts" / "build_version.sh")],
                              capture_output=True, text=True, timeout=60, env=merged, cwd=str(self.tmp))

    def stamp(self):
        return (self.repo / "act" / "_version.py")

    # -- scripts/build_version.sh ------------------------------------------- #

    def test_prints_the_tag_version_and_writes_the_stamp(self):
        proc = self.build_version()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "0.48.16\n", "stdout is exactly the version, one line")
        self.assertIn("build_version: 0.48.16 (scripts/version_stamp.py --write via ", proc.stderr)
        self.assertIn('__version__ = "0.48.16"', self.stamp().read_text(encoding="utf-8"))

    def test_aiassistant_python_is_tried_first(self):
        proc = self.build_version({"AIASSISTANT_PYTHON": sys.executable})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "0.48.16")
        self.assertIn("via %s" % sys.executable, proc.stderr)

    def test_denied_interpreter_is_skipped_and_named(self):
        denied = self.denied_python()
        proc = self.build_version({"AIASSISTANT_PYTHON": denied})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "0.48.16")
        self.assertNotIn("via %s" % denied, proc.stderr)
        self.assertIn("skipped interpreter(s) that could not run the stamper — %s: " % denied, proc.stderr)
        self.assertIn("Operation not permitted", proc.stderr, "…with its last stderr line")
        self.assertTrue(self.stamp().exists(), "the next candidate stamped")

    def test_pinned_interpreter_is_a_candidate(self):
        # the pin is a path nothing else would resolve (not on PATH, not /usr/bin/python3)
        pin = str(self.bindir / "python3")
        (self.repo / "config").mkdir()
        (self.repo / "config" / "runtime.json").write_text('{"python": "%s"}\n' % pin, encoding="utf-8")
        proc = self.build_version({"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "0.48.16")
        self.assertIn("via %s" % pin, proc.stderr)

    @unittest.skipIf(os.geteuid() == 0 if hasattr(os, "geteuid") else False, "root ignores directory modes")
    def test_stamper_that_cannot_write_falls_back_to_its_read_only_answer_not_a_stale_stamp(self):
        # Codex P1 (#145): a stale act/_version.py from an older deploy must not
        # label the new build — the fallback is the stamper's own decision (git
        # first), not `import act` (stamp first)
        (self.repo / "act" / "_version.py").write_text('__version__ = "0.48.15"\n', encoding="utf-8")
        act_dir = self.repo / "act"
        act_dir.chmod(0o555)
        self.addCleanup(act_dir.chmod, 0o755)
        proc = self.build_version({"AIASSISTANT_PYTHON": sys.executable})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "0.48.16", "git describe answers; the stale 0.48.15 stamp does not")
        self.assertIn("WARN scripts/version_stamp.py --write failed via", proc.stderr)
        self.assertIn("using its read-only answer 0.48.16", proc.stderr)
        self.assertIn("NOT written", proc.stderr)
        self.assertIn('"0.48.15"', (self.repo / "act" / "_version.py").read_text(encoding="utf-8"), "stamp untouched")

    def test_nothing_answers_exits_1_with_diagnostics_and_empty_stdout(self):
        self.break_everything()
        denied = self.denied_python()
        proc = self.build_version({"AIASSISTANT_PYTHON": denied})
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(proc.stdout, "", "a caller's VERSION=\"$(…)\" must come back empty, never a guess")
        self.assertIn("build_version: ERROR no interpreter could derive the version", proc.stderr)
        self.assertIn("Operation not permitted", proc.stderr)
        self.assertIn("stamper boom", proc.stderr)
        self.assertIn("read-only:", proc.stderr, "both attempts per interpreter are named")

    # -- shell/build.sh ----------------------------------------------------- #

    def shell_build(self, env=None):
        (self.bindir / "swiftc").write_text(FAKE_SWIFTC, encoding="utf-8")
        (self.bindir / "swiftc").chmod(0o755)
        merged = dict(self.env, **(env or {}))
        return subprocess.run(["/bin/bash", str(self.repo / "shell" / "build.sh")],
                              capture_output=True, text=True, timeout=120, env=merged, cwd=str(self.tmp))

    def bundle_plist(self):
        return self.repo / "shell" / "build" / "Zelin AI Board.app" / "Contents" / "Info.plist"

    @unittest.skipUnless(_DARWIN, "shell/build.sh needs plutil/codesign")
    def test_shell_build_stamps_the_bundle_with_the_tag_version(self):
        proc = self.shell_build({"ZAI_PORT": "47821"})
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("stamped version 0.48.16", proc.stdout)
        with open(self.bundle_plist(), "rb") as fh:
            plist = plistlib.load(fh)
        self.assertEqual(plist["CFBundleShortVersionString"], "0.48.16")
        self.assertEqual(plist["CFBundleVersion"], "0.48.16")
        self.assertEqual(plist["ZAIServerPort"], "47821")
        self.assertTrue(self.stamp().exists(), "the build also wrote act/_version.py for the daemons")

    @unittest.skipUnless(_DARWIN, "shell/build.sh needs plutil/codesign")
    def test_shell_build_skips_the_denied_interpreter(self):
        proc = self.shell_build({"AIASSISTANT_PYTHON": self.denied_python()})
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        with open(self.bundle_plist(), "rb") as fh:
            self.assertEqual(plistlib.load(fh)["CFBundleShortVersionString"], "0.48.16")

    @unittest.skipUnless(_DARWIN, "shell/build.sh needs plutil/codesign")
    def test_shell_build_fails_loudly_instead_of_shipping_the_placeholder(self):
        self.break_everything()
        proc = self.shell_build({"AIASSISTANT_PYTHON": self.denied_python()})
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("BUILD FAILED", proc.stderr)
        self.assertIn("placeholder", proc.stderr)
        self.assertIn("stamper boom", proc.stderr, "the helper's diagnostics reach the build log")
        self.assertFalse((self.repo / "shell" / "build").exists(),
                         "the version is derived before compile/assembly: nothing half-built is left to pick up")


if __name__ == "__main__":
    unittest.main()
