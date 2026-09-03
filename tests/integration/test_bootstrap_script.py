"""scripts/bootstrap.sh — the one-command fresh-machine bootstrap (CONTRACT §69).

Real bash runs of the real script against fake tools on PATH (git /
xcode-select / uname / sw_vers / open / python3 are stubs that log argv), plus
one class against REAL git and a local bare origin. Nothing here touches the
developer's machine: HOME is a temp dir, the "clone" lands under it, `open`
is a stub, install.sh is a stub the fake clone ships. Pinned:

  preflight   not macOS → exit 2 naming install-linux.sh / install.ps1, no git
              call; no Xcode CLT → exit 3 naming `xcode-select --install`;
              no git → exit 4; `--help` → 0 + usage; unknown flag → 2;
  checkout    default ~/Projects/zelin-ai-assistant; positional / --dir /
              ZAI_BOOTSTRAP_DIR / literal `~/` all resolve; a dir outside
              $HOME earns the §55 TCC warning, inside does not;
  clone       first run: `git clone --branch main <url> <dir>`, config.yaml
              created from the example, install.sh run with --non-interactive
              (+ --no-launchd when asked) and a CLOSED stdin (the curl pipe
              must never be eaten), doctor --fresh-install via the pinned
              runtime python, the Board bundle opened unless --no-open;
  re-run      = update: no clone; fetch + checkout + ff-only merge; config.yaml
              untouched; install.sh run again; "bootstrap done (updated)";
              local edits → left alone (installs what is there); a checkout
              of another repo / a non-empty non-git dir → refused (7);
  exit code   install.sh's failed-step count wins, else the doctor's broken
              count, else 0;
  real git    clone from a bare origin, then a second run fast-forwards to a
              new origin commit while config.yaml survives byte-for-byte.

Lives in tests/integration/ (防腐 #7: real subprocesses only here, single-file
time budget BUDGET_SECONDS — ~25 bash runs, each well under a second).
"""
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BOOTSTRAP = REPO / "scripts" / "bootstrap.sh"
_WIN = sys.platform.startswith("win")
BUDGET_SECONDS = 120
_T0 = [time.monotonic()]


def setUpModule():
    _T0[0] = time.monotonic()


def tearDownModule():
    elapsed = time.monotonic() - _T0[0]
    if elapsed > BUDGET_SECONDS:
        raise AssertionError("tests/integration/test_bootstrap_script.py took %.0fs > %ds budget"
                             % (elapsed, BUDGET_SECONDS))

FAKE_GIT = r"""#!/bin/bash
# fake git: log argv, emulate the subcommands bootstrap.sh relies on
printf 'git %s\n' "$*" >> "$FAKE_GIT_LOG"
dir=""
if [ "${1:-}" = "-C" ]; then dir="$2"; shift 2; fi
case "${1:-}" in
  clone)
    target="${@: -1}"; url="${@: -2:1}"
    mkdir -p "$target/.git"
    printf '%s\n' "$url" > "$target/.git/origin-url"
    cp -R "$FAKE_CLONE_PAYLOAD"/. "$target"/
    exit "${FAKE_GIT_CLONE_RC:-0}" ;;
  remote) cat "$dir/.git/origin-url" 2>/dev/null ;;
  status) printf '%s' "${FAKE_GIT_STATUS:-}" ;;
  fetch) exit "${FAKE_GIT_FETCH_RC:-0}" ;;
  checkout) exit "${FAKE_GIT_CHECKOUT_RC:-0}" ;;
  show-ref) exit 0 ;;
  merge) exit "${FAKE_GIT_MERGE_RC:-0}" ;;
  rev-parse) echo abc1234 ;;
  *) exit 0 ;;
esac
"""

FAKE_INSTALL = r"""#!/bin/bash
# stub install.sh shipped by the fake clone: log argv; prove stdin is closed;
# pin the fake python as the daemon interpreter; "install" a Board bundle
printf 'install.sh %s\n' "$*" >> "$FAKE_INSTALL_LOG"
if read -r line; then printf 'STDIN:%s\n' "$line" >> "$FAKE_INSTALL_LOG"; fi
here="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$here/config" && printf '{"python": "%s"}\n' "$FAKE_PYTHON" > "$here/config/runtime.json"
mkdir -p "$HOME/Applications/Zelin's AI Assistant.app/Contents"
if [ -n "${FAKE_BUNDLE_ID:-}" ]; then
  printf '<?xml version="1.0" encoding="UTF-8"?>\n<plist version="1.0"><dict><key>CFBundleIdentifier</key><string>%s</string></dict></plist>\n' \
    "$FAKE_BUNDLE_ID" > "$HOME/Applications/Zelin's AI Assistant.app/Contents/Info.plist"
fi
exit "${FAKE_INSTALL_RC:-0}"
"""

FAKE_PYTHON = r"""#!/bin/bash
printf 'python3 %s [cwd=%s home=%s]\n' "$*" "$PWD" "${AIASSISTANT_HOME:-}" >> "$FAKE_PY_LOG"
echo "fresh install — fake summary"
exit "${FAKE_DOCTOR_RC:-0}"
"""

FAKE_XCODE_SELECT = r"""#!/bin/bash
[ "${FAKE_XCODE_MISSING:-0}" = "1" ] && { echo "xcode-select: error: unable to get active developer directory" >&2; exit 2; }
echo /Library/Developer/CommandLineTools
"""


def _write_exec(path: Path, text: str):
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _mini_bin(tmp: Path, names) -> Path:
    """Real coreutils bootstrap.sh needs, symlinked so PATH never reaches /usr/bin."""
    mini = tmp / "mini"
    mini.mkdir(exist_ok=True)
    for name in names:
        real = shutil.which(name, path="/usr/bin:/bin:/usr/local/bin")
        assert real, "no %s on this machine" % name
        link = mini / name
        if not link.exists():
            link.symlink_to(real)
    return mini


CORE_TOOLS = ("bash", "sh", "dirname", "basename", "mkdir", "ls", "cp", "sed", "cat", "rm", "env")


@unittest.skipIf(_WIN, "bootstrap.sh is a macOS bash script (stubs are POSIX)")
class FakeToolsTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bootstrap-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.fakebin = self.tmp / "fakebin"
        self.fakebin.mkdir()
        self.logs = {k: self.tmp / ("%s.log" % k) for k in ("git", "install", "py", "open")}
        _write_exec(self.fakebin / "git", FAKE_GIT)
        _write_exec(self.fakebin / "python3", FAKE_PYTHON)
        _write_exec(self.fakebin / "xcode-select", FAKE_XCODE_SELECT)
        _write_exec(self.fakebin / "uname", '#!/bin/bash\necho "${FAKE_UNAME:-Darwin}"\n')
        _write_exec(self.fakebin / "sw_vers", '#!/bin/bash\necho 15.1\n')
        _write_exec(self.fakebin / "open", '#!/bin/bash\nprintf \'open %s\\n\' "$*" >> "$FAKE_OPEN_LOG"\n')
        self.payload = self.tmp / "payload"
        self.payload.mkdir()
        _write_exec(self.payload / "install.sh", FAKE_INSTALL)
        (self.payload / "config.example.yaml").write_text("sources: {}\n", encoding="utf-8")
        self.mini = _mini_bin(self.tmp, CORE_TOOLS)

    def _log(self, key):
        p = self.logs[key]
        return p.read_text(encoding="utf-8").splitlines() if p.exists() else []

    def run_bootstrap(self, *args, env=None, path_dirs=None, stdin_text=None):
        base = {
            "HOME": str(self.home),
            "PATH": os.pathsep.join(str(p) for p in (path_dirs or [self.fakebin, self.mini])),
            "ZAI_BOOTSTRAP_REPO_URL": "https://example.invalid/Wan-ZL/zelin-ai-assistant.git",
            "FAKE_GIT_LOG": str(self.logs["git"]), "FAKE_INSTALL_LOG": str(self.logs["install"]),
            "FAKE_PY_LOG": str(self.logs["py"]), "FAKE_OPEN_LOG": str(self.logs["open"]),
            "FAKE_CLONE_PAYLOAD": str(self.payload), "FAKE_PYTHON": str(self.fakebin / "python3"),
            # hermetic: never look at the developer's real /Applications
            "AIASSISTANT_UI_APPS_DIR": str(self.tmp / "no-system-apps"),
        }
        base.update(env or {})
        if stdin_text is None:
            return subprocess.run(["bash", str(BOOTSTRAP), *args], capture_output=True, text=True,
                                  timeout=120, env=base, stdin=subprocess.DEVNULL)
        # the curl pipe shape: the script itself arrives on stdin
        return subprocess.run(["bash", "-s", "--", *args], input=stdin_text, capture_output=True,
                              text=True, timeout=120, env=base)

    # -- preflight --------------------------------------------------------- #
    def test_not_macos_points_at_the_other_installers(self):
        proc = self.run_bootstrap(env={"FAKE_UNAME": "Linux"})
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("install-linux.sh", proc.stderr)
        self.assertIn("install.ps1", proc.stderr)
        self.assertEqual(self._log("git"), [])

    def test_missing_command_line_tools_names_the_fix(self):
        proc = self.run_bootstrap(env={"FAKE_XCODE_MISSING": "1"})
        self.assertEqual(proc.returncode, 3, proc.stderr)
        self.assertIn("xcode-select --install", proc.stderr)
        self.assertEqual(self._log("git"), [])

    def test_missing_git_exits_4(self):
        nogit = self.tmp / "nogit"
        nogit.mkdir()
        for name in ("python3", "xcode-select", "uname", "sw_vers", "open"):
            (nogit / name).symlink_to(self.fakebin / name)
        proc = self.run_bootstrap(path_dirs=[nogit, self.mini])
        self.assertEqual(proc.returncode, 4, proc.stderr)
        self.assertIn("git not found", proc.stderr)

    def test_help_and_unknown_flag(self):
        proc = self.run_bootstrap("--help")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("--no-launchd", proc.stdout)
        proc = self.run_bootstrap("--bogus")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("unknown flag: --bogus", proc.stderr)
        self.assertEqual(self._log("git"), [])

    # -- first run --------------------------------------------------------- #
    def test_fresh_run_clones_configures_installs_summarises_and_opens(self):
        proc = self.run_bootstrap()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        target = self.home / "Projects" / "zelin-ai-assistant"
        self.assertEqual(self._log("git"), [
            "git clone --branch main https://example.invalid/Wan-ZL/zelin-ai-assistant.git %s" % target,
            "git -C %s rev-parse --short HEAD" % target])
        self.assertEqual((target / "config.yaml").read_text(encoding="utf-8"), "sources: {}\n")
        self.assertEqual(self._log("install"), ["install.sh --non-interactive"])  # no STDIN: line = stdin closed
        py = self._log("py")
        self.assertEqual(len(py), 1)
        self.assertEqual(py[0], "python3 -m act.doctor --fresh-install [cwd=%s home=%s]" % (target, target))
        self.assertEqual(self._log("open"), ["open %s/Applications/Zelin's AI Assistant.app" % self.home])
        self.assertIn("inside $HOME", proc.stdout)
        self.assertIn("bootstrap done (cloned)", proc.stdout)
        self.assertIn("fresh install — fake summary", proc.stdout)

    @unittest.skipUnless(sys.platform == "darwin", "PlistBuddy reads the bundle id only on macOS")
    def test_open_judges_the_bundle_by_id_not_by_folder_name(self):
        # §54 name swap: the frozen legacy app can sit under the product folder
        # name with its own id — bootstrap must not open that one
        proc = self.run_bootstrap(env={"FAKE_BUNDLE_ID": "com.zelin.ai-engineer"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self._log("open"), [])
        self.assertIn("no board app bundle was built", proc.stdout)
        self.logs["open"].unlink(missing_ok=True)
        proc = self.run_bootstrap(env={"FAKE_BUNDLE_ID": "com.zelin.ai-board"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(len(self._log("open")), 1)

    def test_no_launchd_and_no_open_pass_through(self):
        proc = self.run_bootstrap("--no-launchd", "--no-open")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self._log("install"), ["install.sh --non-interactive --no-launchd"])
        self.assertEqual(self._log("open"), [])
        self.assertIn("not opening the board (--no-open)", proc.stdout)

    def test_dir_flag_positional_env_and_tilde(self):
        for args, env, expected in (
            (("--dir", str(self.tmp / "a")), {}, self.tmp / "a"),
            ((str(self.tmp / "b"),), {}, self.tmp / "b"),
            ((), {"ZAI_BOOTSTRAP_DIR": str(self.tmp / "c")}, self.tmp / "c"),
            (("~/d",), {}, self.home / "d"),
            (("--dir=~/e", "--no-open"), {}, self.home / "e"),
        ):
            with self.subTest(args=args, env=env):
                for log in self.logs.values():
                    log.unlink(missing_ok=True)
                proc = self.run_bootstrap(*args, env=env)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertTrue((expected / "config.yaml").is_file(), proc.stdout)
                self.assertIn("checkout dir: %s" % expected, proc.stdout)

    def test_dir_outside_home_earns_the_tcc_warning(self):
        proc = self.run_bootstrap("--dir", str(self.tmp / "Volumes" / "Ext" / "zai"))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("OUTSIDE your home folder", proc.stdout)
        self.assertIn("Full Disk Access", proc.stdout)
        self.assertNotIn("inside $HOME", proc.stdout)

    def test_piped_through_stdin_like_curl_bash(self):
        proc = self.run_bootstrap("--no-open", stdin_text=BOOTSTRAP.read_text(encoding="utf-8"))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        # the children must not have eaten script text off stdin
        self.assertEqual(self._log("install"), ["install.sh --non-interactive"])
        self.assertIn("bootstrap done (cloned)", proc.stdout)

    def test_clone_failure_exits_8(self):
        proc = self.run_bootstrap(env={"FAKE_GIT_CLONE_RC": "128"})
        self.assertEqual(proc.returncode, 8, proc.stderr)
        self.assertIn("git clone failed", proc.stderr)
        self.assertEqual(self._log("install"), [])

    def test_install_failed_steps_become_the_exit_code(self):
        proc = self.run_bootstrap("--no-open", env={"FAKE_INSTALL_RC": "2"})
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("install.sh reported 2 failed step(s)", proc.stdout)
        self.assertEqual(len(self._log("py")), 1)  # the summary still runs
        self.assertIn("bootstrap done", proc.stdout)

    def test_doctor_broken_rows_become_the_exit_code(self):
        proc = self.run_bootstrap("--no-open", env={"FAKE_DOCTOR_RC": "3"})
        self.assertEqual(proc.returncode, 3, proc.stderr)
        self.assertIn("doctor found 3 broken row(s)", proc.stdout)

    # -- re-run = update ---------------------------------------------------- #
    def _seed_checkout(self, origin="https://example.invalid/Wan-ZL/zelin-ai-assistant.git"):
        target = self.home / "Projects" / "zelin-ai-assistant"
        (target / ".git").mkdir(parents=True)
        (target / ".git" / "origin-url").write_text(origin + "\n", encoding="utf-8")
        shutil.copy(self.payload / "install.sh", target / "install.sh")
        shutil.copy(self.payload / "config.example.yaml", target / "config.example.yaml")
        (target / "config.yaml").write_text("sources: {}\nmine: true\n", encoding="utf-8")
        return target

    def test_rerun_updates_and_keeps_config(self):
        target = self._seed_checkout()
        proc = self.run_bootstrap("--no-open")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        git = self._log("git")
        self.assertFalse(any(" clone " in ln for ln in git), git)
        self.assertEqual(git[:6], [
            "git -C %s remote get-url origin" % target,
            "git -C %s status --porcelain --untracked-files=no" % target,
            "git -C %s fetch --tags --force origin" % target,
            "git -C %s checkout -q main" % target,
            "git -C %s show-ref --verify -q refs/remotes/origin/main" % target,
            "git -C %s merge -q --ff-only origin/main" % target,
        ])
        self.assertEqual((target / "config.yaml").read_text(encoding="utf-8"), "sources: {}\nmine: true\n")
        self.assertIn("config.yaml exists — left untouched", proc.stdout)
        self.assertEqual(self._log("install"), ["install.sh --non-interactive"])
        self.assertIn("bootstrap done (updated)", proc.stdout)

    def test_ref_flag_selects_the_branch(self):
        proc = self.run_bootstrap("--no-open", "--ref", "release/x")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(self._log("git")[0].startswith("git clone --branch release/x "), self._log("git"))

    def test_local_edits_are_left_alone(self):
        target = self._seed_checkout()
        proc = self.run_bootstrap("--no-open", env={"FAKE_GIT_STATUS": " M act/actd.py\n"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("local edits", proc.stdout)
        self.assertFalse(any(" fetch " in ln or " merge " in ln for ln in self._log("git")))
        self.assertEqual(self._log("install"), ["install.sh --non-interactive"])
        self.assertIn("bootstrap done (local-edits)", proc.stdout)
        self.assertTrue((target / "config.yaml").is_file())

    def test_offline_fetch_still_installs(self):
        self._seed_checkout()
        proc = self.run_bootstrap("--no-open", env={"FAKE_GIT_FETCH_RC": "128"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("git fetch failed (offline?)", proc.stdout)
        self.assertIn("bootstrap done (offline)", proc.stdout)

    def test_diverged_checkout_is_refused(self):
        self._seed_checkout()
        proc = self.run_bootstrap("--no-open", env={"FAKE_GIT_MERGE_RC": "1"})
        self.assertEqual(proc.returncode, 9, proc.stderr)
        self.assertIn("diverged", proc.stderr)
        self.assertEqual(self._log("install"), [])

    def test_checkout_of_another_repo_is_refused(self):
        self._seed_checkout(origin="https://github.com/someone/other-thing.git")
        proc = self.run_bootstrap("--no-open")
        self.assertEqual(proc.returncode, 7, proc.stderr)
        self.assertIn("git checkout of something else", proc.stderr)
        self.assertEqual(self._log("install"), [])

    def test_non_empty_non_git_dir_is_refused(self):
        target = self.home / "Projects" / "zelin-ai-assistant"
        target.mkdir(parents=True)
        (target / "precious.txt").write_text("keep\n", encoding="utf-8")
        proc = self.run_bootstrap("--no-open")
        self.assertEqual(proc.returncode, 7, proc.stderr)
        self.assertIn("not a git checkout", proc.stderr)
        self.assertEqual((target / "precious.txt").read_text(encoding="utf-8"), "keep\n")
        self.assertEqual(self._log("git"), [])


@unittest.skipIf(_WIN or shutil.which("git") is None, "needs real git")
class RealGitTestCase(unittest.TestCase):
    """clone from a bare origin, then fast-forward on the second run — real git."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bootstrap-git-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.fakebin = self.tmp / "fakebin"
        self.fakebin.mkdir()
        _write_exec(self.fakebin / "python3", FAKE_PYTHON)
        _write_exec(self.fakebin / "xcode-select", FAKE_XCODE_SELECT)
        _write_exec(self.fakebin / "uname", '#!/bin/bash\necho Darwin\n')
        _write_exec(self.fakebin / "sw_vers", '#!/bin/bash\necho 15.1\n')
        _write_exec(self.fakebin / "open", '#!/bin/bash\nexit 0\n')
        self.logs = {k: self.tmp / ("%s.log" % k) for k in ("install", "py")}
        # source repo → bare origin (main)
        self.src = self.tmp / "src"
        self.src.mkdir()
        _write_exec(self.src / "install.sh", FAKE_INSTALL)
        (self.src / "config.example.yaml").write_text("sources: {}\n", encoding="utf-8")
        self._git(self.src, "init", "-q", "-b", "main")
        self._git(self.src, "add", "-A")
        self._git(self.src, "commit", "-q", "-m", "v1")
        self.origin = self.tmp / "origin.git"
        subprocess.run(["git", "clone", "-q", "--bare", str(self.src), str(self.origin)], check=True, timeout=60)

    def _git(self, cwd, *args):
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=str(cwd),
                       check=True, capture_output=True, timeout=60)

    def _run(self):
        env = {
            "HOME": str(self.home),
            "PATH": os.pathsep.join([str(self.fakebin), "/usr/bin", "/bin", "/usr/local/bin"]),
            "ZAI_BOOTSTRAP_REPO_URL": str(self.origin), "ZAI_BOOTSTRAP_NO_OPEN": "1",
            "FAKE_INSTALL_LOG": str(self.logs["install"]), "FAKE_PY_LOG": str(self.logs["py"]),
            "FAKE_PYTHON": str(self.fakebin / "python3"),
            "GIT_CONFIG_NOSYSTEM": "1",
        }
        return subprocess.run(["bash", str(BOOTSTRAP), "--no-launchd"], capture_output=True, text=True,
                              timeout=180, env=env, stdin=subprocess.DEVNULL)

    def test_clone_then_update(self):
        target = self.home / "Projects" / "zelin-ai-assistant"
        first = self._run()
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertIn("bootstrap done (cloned)", first.stdout)
        self.assertTrue((target / ".git").is_dir())
        self.assertEqual((target / "config.yaml").read_text(encoding="utf-8"), "sources: {}\n")
        head1 = subprocess.run(["git", "-C", str(target), "rev-parse", "HEAD"], capture_output=True,
                               text=True, check=True).stdout.strip()
        # a local config edit + a new origin commit
        (target / "config.yaml").write_text("sources: {}\nmine: true\n", encoding="utf-8")
        (self.src / "README.md").write_text("v2\n", encoding="utf-8")
        self._git(self.src, "add", "-A")
        self._git(self.src, "commit", "-q", "-m", "v2")
        self._git(self.src, "push", "-q", str(self.origin), "main")
        second = self._run()
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertIn("bootstrap done (updated)", second.stdout)
        head2 = subprocess.run(["git", "-C", str(target), "rev-parse", "HEAD"], capture_output=True,
                               text=True, check=True).stdout.strip()
        self.assertNotEqual(head1, head2)
        self.assertTrue((target / "README.md").is_file())
        self.assertEqual((target / "config.yaml").read_text(encoding="utf-8"), "sources: {}\nmine: true\n")
        self.assertEqual(self.logs["install"].read_text(encoding="utf-8").splitlines(),
                         ["install.sh --non-interactive --no-launchd"] * 2)
        branch = subprocess.run(["git", "-C", str(target), "rev-parse", "--abbrev-ref", "HEAD"],
                                capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(branch, "main")


if __name__ == "__main__":
    unittest.main()
