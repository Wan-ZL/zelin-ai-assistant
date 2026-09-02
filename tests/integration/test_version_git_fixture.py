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
  - 解析不依赖进程 cwd（daemon 在 launchd 下 cwd=$HOME、repo 走 PYTHONPATH）：`import act`
    与绝对路径调用的 stamper 都从包自己的根 `git -C`；陈旧 stamp 在 → `import act` 报它
    （§56.1 顺序），stamper 的决策仍以 git 为准；
  - 非 git 副本（.pkg / tarball 形状）：已有 stamp 保留，没有 → 回落常量；
  - install.sh 的 stamp_version（抠原文真跑）：写 stamp、报 `version=ok:<v>` 行、
    STAMPED_VERSION 可用；没有 python 也不致命；
  - **解释器挑选**（2026-09-02 v0.48.21 首次实战：auto-deploy 下 PATH 首位的 Homebrew
    python3 被 TCC 拒在外置卷外、stderr 被 2>/dev/null 吞掉）：候选按 §55 daemon 顺序
    （$AIASSISTANT_PYTHON 最先），第一个能盖章的赢；被拒的解释器**跳过并点名**
    （[info] 行带它的最后一行 stderr）；全部失败 → warn（不 fail）且 §23 report 的
    `version` detail 带每个解释器的最后一行 stderr；launchd 式洁净环境（无 LANG/LC_*、
    最小 PATH、cwd=repo）下 tag 在 → tag，tag 不在 → 回落值，都不失败；
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

    def test_resolution_never_depends_on_the_process_cwd(self):
        # the daemons run under launchd with WorkingDirectory=$HOME and the repo on
        # PYTHONPATH (act/launchd/*.plist); the stamper is called by absolute path from
        # wherever. `git -C <root derived from __file__>` — never the cwd (2026-09-02
        # false-rollback review asked for this pin).
        elsewhere = self.tmp / "home"
        elsewhere.mkdir()
        env = dict(self.env, PYTHONPATH=str(self.repo))
        proc = subprocess.run([sys.executable, "-c", "import act; print(act.__version__)"],
                              capture_output=True, text=True, timeout=60, cwd=str(elsewhere), env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "0.48.16", "no stamp: git describe from the package's own root")
        proc = subprocess.run([sys.executable, str(self.repo / "scripts" / "version_stamp.py")],
                              capture_output=True, text=True, timeout=60, cwd=str(elsewhere), env=self.env)
        self.assertEqual(proc.stdout.strip(), "0.48.16")
        # a stale stamp is what the daemons report until re-stamped — that is the
        # §56.1 order (stamp first), and why auto-deploy keys readiness on the stamp
        (self.repo / "act" / "_version.py").write_text('__version__ = "0.48.15"\n', encoding="utf-8")
        proc = subprocess.run([sys.executable, "-c", "import act; print(act.__version__)"],
                              capture_output=True, text=True, timeout=60, cwd=str(elsewhere), env=env)
        self.assertEqual(proc.stdout.strip(), "0.48.15")
        self.assertEqual(self.stamper()[0], "0.48.16", "the stamper's decision ignores a stale stamp when git answers")

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

    # install.sh's stamp_version + everything it calls (the §55 candidate order
    # is the real one: stamp_python_candidates → daemon_python_candidates →
    # repo_outside_home / physical_path / pinned_python).
    STAMP_FNS = ("report_step", "physical_path", "repo_outside_home", "pinned_python",
                 "daemon_python_candidates", "stamp_python_candidates", "stamp_version")

    def stamp_script(self, override=""):
        return ("set -u\n"
                'ok()   { printf "  [ ok ] %s\\n" "$1"; }\n'
                'warn() { printf "  [warn] %s\\n" "$1"; }\n'
                'info() { printf "  [info] %s\\n" "$1"; }\n'
                'REPORT_STEPS=""\n'
                + "".join(_install_sh_fn(fn) for fn in self.STAMP_FNS)
                + override
                + 'REPO_ROOT="$1"; PY="$2"\n'
                'stamp_version\n'
                'printf "STAMPED=%s\\nREPORT=%s" "$STAMPED_VERSION" "$REPORT_STEPS"\n')

    def run_stamp(self, py, env=None, override="", cwd=None):
        # HOME = the sandbox: the repo is INSIDE it, so the §55 order is
        # $AIASSISTANT_PYTHON, the pin, ~/miniconda3 (absent), $PY, /usr/bin/python3
        base = dict(self.env, HOME=str(self.tmp))
        base.pop("AIASSISTANT_PYTHON", None)
        base.update(env or {})
        return subprocess.run(["bash", "-c", self.stamp_script(override), "bash", str(self.repo), py],
                              capture_output=True, text=True, timeout=60, env=base, cwd=cwd)

    def denied_python(self):
        """A stand-in for the TCC-denied Homebrew python3 under launchd: cannot open
        any file of the checkout, says so on stderr the way CPython does, exits 2."""
        fake = self.tmp / "homebrew" / "python3"
        fake.parent.mkdir(parents=True, exist_ok=True)
        fake.write_text("#!/bin/sh\n"
                        "echo \"$0: can't open file '$1': [Errno 1] Operation not permitted\" >&2\n"
                        "exit 2\n", encoding="utf-8")
        fake.chmod(0o755)
        return str(fake)

    @unittest.skipIf(_WIN, "install.sh is POSIX-only")
    def test_install_sh_stamp_version(self):
        proc = self.run_stamp(sys.executable)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("[ ok ] act/_version.py -> v0.48.16", proc.stdout)
        self.assertIn(sys.executable, proc.stdout, "the ok line names the interpreter that stamped")
        self.assertIn("STAMPED=0.48.16", proc.stdout)
        self.assertIn("version=ok:0.48.16", proc.stdout)
        self.assertNotIn("skipped interpreter", proc.stdout)
        self.assertEqual(self.import_version(), "0.48.16")

    @unittest.skipIf(_WIN, "install.sh is POSIX-only")
    def test_install_sh_stamp_version_skips_the_tcc_denied_interpreter_and_names_it(self):
        denied = self.denied_python()
        proc = self.run_stamp(sys.executable, env={"AIASSISTANT_PYTHON": denied})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("STAMPED=0.48.16", proc.stdout)
        self.assertIn("version=ok:0.48.16", proc.stdout)
        self.assertIn("[info]   version: skipped interpreter(s)", proc.stdout)
        self.assertIn(denied + ": ", proc.stdout, "the skipped interpreter is named")
        self.assertIn("Operation not permitted", proc.stdout, "…with its last stderr line")
        self.assertEqual(self.import_version(), "0.48.16")

    @unittest.skipIf(_WIN, "install.sh is POSIX-only")
    def test_install_sh_stamp_version_every_interpreter_failing_is_a_warn_that_carries_stderr(self):
        stamper = self.repo / "scripts" / "version_stamp.py"
        stamper.write_text("import sys\nsys.exit('stamper boom: pretend act/ is unwritable')\n", encoding="utf-8")
        proc = self.run_stamp(sys.executable)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("STAMPED=\n", proc.stdout)
        self.assertIn("[warn] scripts/version_stamp.py failed with every interpreter", proc.stdout)
        self.assertIn("stamper boom", proc.stdout, "the [warn] line carries the stamper's last stderr line")
        report = proc.stdout.split("REPORT=", 1)[1]
        self.assertIn("version=warn:stamp failed — ", report)
        self.assertIn(sys.executable + ": stamper boom", report, "the §23 detail names interpreter + stderr")
        self.assertNotIn("=fail", proc.stdout)
        self.assertFalse((self.repo / "act" / "_version.py").exists())

    @unittest.skipIf(_WIN, "install.sh is POSIX-only")
    def test_install_sh_stamp_version_without_python_is_a_warn_not_a_fail(self):
        # no candidate at all (the real list always holds /usr/bin/python3, so pin
        # the list to the one nonexistent $PY to reach this branch)
        proc = self.run_stamp("/nonexistent/python3",
                              override='stamp_python_candidates() { printf "%s\\n" "${PY:-}"; }\n')
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("[warn] no python3", proc.stdout)
        self.assertIn("version=warn:no python3 to stamp", proc.stdout)
        self.assertNotIn("=fail", proc.stdout)

    @unittest.skipIf(_WIN, "install.sh is POSIX-only")
    def test_install_sh_stamp_version_under_a_launchd_like_environment(self):
        # no LANG / LC_* / PYTHON*, a minimal PATH whose python3 is ours, cwd = the repo
        bindir = self.tmp / "bin"
        bindir.mkdir()
        os.symlink(sys.executable, str(bindir / "python3"))
        scrubbed = {"PATH": "%s:/usr/bin:/bin" % bindir, "HOME": str(self.tmp)}
        script = self.stamp_script()
        proc = subprocess.run(["bash", "-c", script, "bash", str(self.repo), str(bindir / "python3")],
                              capture_output=True, text=True, timeout=60, env=scrubbed, cwd=str(self.repo))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("STAMPED=0.48.16", proc.stdout)
        self.assertIn("version=ok:0.48.16", proc.stdout)
        # tags never fetched (or none yet): the fallback constant, still ok, never a fail
        _git(self.repo, "tag", "-d", "v0.48.16")
        proc = subprocess.run(["bash", "-c", script, "bash", str(self.repo), str(bindir / "python3")],
                              capture_output=True, text=True, timeout=60, env=scrubbed, cwd=str(self.repo))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("STAMPED=0.48.16", proc.stdout, "no tag → the fixture's baked fallback (setUp)")
        self.assertIn("version=ok:0.48.16", proc.stdout)
        self.assertNotIn("=fail", proc.stdout)


if __name__ == "__main__":
    unittest.main()
