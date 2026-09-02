"""版本真源 = git tag 的真夹具判例（CONTRACT §56.1）——真 git + 真 python 子进程。

住在 tests/integration/（防腐 #7：真 IO 只许住这里，单文件时间预算
BUDGET_SECONDS）。夹具 = 一个临时 git repo，里面是**真的**
act/__init__.py、act/lib/version.py、scripts/version_stamp.py 与两份 iOS pin 文件
的逐字拷贝（其余一律不拷——stamper 用自己的路径定 repo root 并只 import
act.lib.version，所以夹具里的 act 包就是被测代码）。钉住的行为：

  - HEAD 恰在 vX.Y.Z tag 上 → 版本 = X.Y.Z；`--write` 落 act/_version.py，
    夹具里 `import act` 与 `--runtime` 都报同一个数；
  - 领先 tag N 个 commit：回落常量 ≤ tag → `X.Y.Z+N`；回落常量 > tag（过渡条款，
    本轮切换那一刻的形状）→ 回落常量；随后打上 tag → 恰好是 tag；
  - 仓库没有任何 v* tag → 回落常量；
  - 非 git 副本（.pkg / tarball 形状）：已有 stamp 保留，没有 → 回落常量；
  - install.sh 的 stamp_version（抠原文真跑）：写 stamp、报 `version=ok:<v>` 行、
    STAMPED_VERSION 可用；没有 python 也不致命；
  - `--ios` 只改工作树里的 pin、`--check-pins` 随之变红；`--stamp-into DIR` 往
    打包 stage 落 stamp。
"""
import os
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
BUDGET_SECONDS = 60
_T0 = [time.monotonic()]

COPIED = ("act/__init__.py", "act/lib/__init__.py", "act/lib/version.py", "scripts/version_stamp.py",
          "ios/project.yml", "ios/ZelinAIAssistant.xcodeproj/project.pbxproj")


def setUpModule():
    # the clock starts when THIS module's tests start, not at discovery-time import
    _T0[0] = time.monotonic()


def tearDownModule():
    elapsed = time.monotonic() - _T0[0]
    if elapsed > BUDGET_SECONDS:
        raise AssertionError("tests/integration/test_version_git_fixture.py took %.0fs > %ds budget"
                             % (elapsed, BUDGET_SECONDS))


def _git(cwd, *args):
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false", *args],
        cwd=str(cwd), capture_output=True, text=True, timeout=60, check=True).stdout.strip()


def _install_sh_fn(name):
    text = (REPO / "install.sh").read_text(encoding="utf-8")
    m = re.search(r"^%s\(\) \{.*?^\}" % re.escape(name), text, flags=re.S | re.M)
    assert m, "install.sh no longer defines %s()" % name
    return m.group(0) + "\n"


class VersionGitFixtureTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="vergit-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = self.tmp / "repo"
        for rel in COPIED:
            dst = self.repo / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(str(REPO / rel), str(dst))
        _git(self.tmp, "init", "-q", str(self.repo))
        # the fixture's baked fallback == the seed tag, whatever the real tree says
        # right now (the real line moves at the cutover / on chore refreshes)
        self.set_fallback("0.48.16", commit=False)
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-q", "-m", "seed")
        _git(self.repo, "tag", "v0.48.16")
        self.env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH",)}

    # -- helpers ------------------------------------------------------------ #

    def set_fallback(self, version, commit=True):
        """改夹具 act/__init__.py 的回落行；commit=True 时连同一个计数文件提交
        （HEAD 前进一步，哪怕回落值没变）。"""
        init = self.repo / "act" / "__init__.py"
        text = re.sub(r'^__version__ = "[^"]*"$', '__version__ = "%s"' % version, init.read_text(encoding="utf-8"),
                      count=1, flags=re.M)
        init.write_text(text, encoding="utf-8")
        if commit:
            self.advance("fallback %s" % version)

    def advance(self, message="more"):
        counter = self.repo / "COUNTER"
        n = int(counter.read_text(encoding="utf-8") or "0") + 1 if counter.exists() else 1
        counter.write_text("%d\n" % n, encoding="utf-8")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-q", "-m", message)

    def stamper(self, *args, root=None, check=True):
        proc = subprocess.run([sys.executable, str((root or self.repo) / "scripts" / "version_stamp.py"), *args],
                              capture_output=True, text=True, timeout=60, env=self.env, cwd=str(self.tmp))
        if check:
            self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout.strip(), proc.stderr, proc.returncode

    def import_version(self, root=None):
        proc = subprocess.run([sys.executable, "-c", "import act; print(act.__version__)"],
                              capture_output=True, text=True, timeout=60, cwd=str(root or self.repo), env=self.env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout.strip()

    # -- resolution order --------------------------------------------------- #

    def test_exact_tag_everywhere(self):
        self.assertEqual(self.stamper()[0], "0.48.16")
        self.assertEqual(self.import_version(), "0.48.16", "no stamp yet: import act derives from git")
        out, err, _ = self.stamper("--write")
        self.assertEqual(out, "0.48.16")
        self.assertIn("(git)", err)
        self.assertEqual((self.repo / "act" / "_version.py").read_text(encoding="utf-8").count('"0.48.16"'), 1)
        self.assertEqual(self.stamper("--runtime")[0], "0.48.16")
        self.assertEqual(self.import_version(), "0.48.16")

    def test_ahead_of_tag_with_fallback_at_or_below_tag(self):
        self.set_fallback("0.48.16")            # == tag → honest +N
        self.assertEqual(self.stamper()[0], "0.48.16+1")
        self.set_fallback("0.48.10")            # behind → still +N
        self.assertEqual(self.stamper()[0], "0.48.16+2")
        self.assertEqual(self.import_version(), "0.48.16+2")

    def test_transition_clause_then_tag_lands(self):
        # the cutover shape: the committed line declares the release the merge will
        # mint, the checkout is one commit past the last tag, the tag not fetched yet
        self.set_fallback("0.48.17")
        self.assertEqual(self.stamper()[0], "0.48.17")
        self.assertEqual(self.stamper("--write")[0], "0.48.17")
        self.assertEqual(self.import_version(), "0.48.17")
        # release-on-merge tags HEAD → identical answer, now from the tag itself
        _git(self.repo, "tag", "v0.48.17")
        out, err, _ = self.stamper("--write")
        self.assertEqual(out, "0.48.17")
        # and one more commit past it goes back to +N
        self.advance()
        self.assertEqual(self.stamper()[0], "0.48.17+1")

    def test_no_version_tags_falls_back(self):
        _git(self.repo, "tag", "-d", "v0.48.16")
        _git(self.repo, "tag", "backup-2026")   # a non-version tag must not be picked up
        self.set_fallback("0.48.17")
        out, err, _ = self.stamper("--write")
        self.assertEqual(out, "0.48.17")
        self.assertIn("(fallback)", err)

    def test_git_less_copy_keeps_the_stamp(self):
        self.stamper("--write")
        copy = self.tmp / "pkg-copy"
        shutil.copytree(str(self.repo), str(copy), ignore=shutil.ignore_patterns(".git"))
        self.set_fallback("0.48.99", commit=False)          # only in the git repo, irrelevant here
        out, err, _ = self.stamper("--write", root=copy)
        self.assertEqual(out, "0.48.16")
        self.assertIn("(stamp)", err)
        self.assertEqual(self.import_version(copy), "0.48.16")
        # no stamp + no git → the baked fallback of that copy (0.48.16, see setUp)
        (copy / "act" / "_version.py").unlink()
        self.assertEqual(self.import_version(copy), "0.48.16")
        out, err, _ = self.stamper("--write", root=copy)
        self.assertEqual(out, "0.48.16")
        self.assertIn("(fallback)", err)

    def test_explicit_version_and_stamp_into(self):
        stage = self.tmp / "stage"
        (stage / "act").mkdir(parents=True)
        out, _, _ = self.stamper("--version", "v1.2.3", "--stamp-into", str(stage))
        self.assertEqual(out, "1.2.3")
        self.assertIn('__version__ = "1.2.3"', (stage / "act" / "_version.py").read_text(encoding="utf-8"))
        self.assertFalse((self.repo / "act" / "_version.py").exists(), "--stamp-into must not touch the repo")

    # -- iOS pins ----------------------------------------------------------- #

    def test_ios_pins_stamped_in_the_working_tree_only(self):
        self.assertEqual(self.stamper("--check-pins")[2], 0)
        out, err, _ = self.stamper("--ios")
        self.assertEqual(out, "0.48.16")
        for rel in ("ios/project.yml", "ios/ZelinAIAssistant.xcodeproj/project.pbxproj"):
            self.assertIn("0.48.16", (self.repo / rel).read_text(encoding="utf-8"))
        self.assertEqual(self.stamper("--check-pins", check=False)[2], 1)
        self.assertIn("MARKETING_VERSION", _git(self.repo, "diff", "--stat", "--", "ios") + _git(self.repo, "diff", "--", "ios"))

    # -- install.sh stamp_version ------------------------------------------- #

    @unittest.skipIf(_WIN, "install.sh is POSIX-only")
    def test_install_sh_stamp_version(self):
        script = ("set -u\n"
                  'ok()   { printf "  [ ok ] %s\\n" "$1"; }\n'
                  'warn() { printf "  [warn] %s\\n" "$1"; }\n'
                  'REPORT_STEPS=""\n'
                  + _install_sh_fn("report_step") + _install_sh_fn("stamp_version")
                  + 'REPO_ROOT="$1"; PY="$2"\n'
                  'stamp_version\n'
                  'printf "STAMPED=%s\\nREPORT=%s" "$STAMPED_VERSION" "$REPORT_STEPS"\n')
        proc = subprocess.run(["bash", "-c", script, "bash", str(self.repo), sys.executable],
                              capture_output=True, text=True, timeout=60, env=self.env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("[ ok ] act/_version.py -> v0.48.16", proc.stdout)
        self.assertIn("STAMPED=0.48.16", proc.stdout)
        self.assertIn("version=ok:0.48.16", proc.stdout)
        self.assertEqual(self.import_version(), "0.48.16")

    @unittest.skipIf(_WIN, "install.sh is POSIX-only")
    def test_install_sh_stamp_version_without_python_is_a_warn_not_a_fail(self):
        script = ("set -u\n"
                  'ok()   { printf "  [ ok ] %s\\n" "$1"; }\n'
                  'warn() { printf "  [warn] %s\\n" "$1"; }\n'
                  'REPORT_STEPS=""\n'
                  + _install_sh_fn("report_step") + _install_sh_fn("stamp_version")
                  + 'REPO_ROOT="$1"; PY="/nonexistent/python3"\n'
                  'PATH=/nonexistent stamp_version\n'
                  'printf "REPORT=%s" "$REPORT_STEPS"\n')
        proc = subprocess.run(["bash", "-c", script, "bash", str(self.repo)],
                              capture_output=True, text=True, timeout=60, env=self.env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("version=warn", proc.stdout)
        self.assertNotIn("=fail", proc.stdout)


if __name__ == "__main__":
    unittest.main()
