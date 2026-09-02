"""install.sh `ui` step (CONTRACT §56.5; §54 shell) — behavior pins, real bash.

2026-09-02 live: the owner machine ran daemons at v0.48.12 while the board UI
had NEVER been built or installed — nothing in install.sh built web/dist or the
shell app, so "merge = deploy" silently excluded the product's face. The `ui`
step closes that gap. Pinned here (the functions are extracted from install.sh
verbatim and run against a fixture repo with fake npm/node/swiftc/shell
build.sh; nothing real is installed, /Applications is a temp dir):

  - node+npm present → `npm ci` + `npm run build` → web/dist; swiftc present
    (macOS) → shell/build.sh + stage-then-swap into the apps dir → `ui=ok`
    with per-half durations in the detail;
  - the web build runs in a build dir under $HOME (sources rsync'ed there),
    never with node's cwd inside the repo: homebrew node is TCC-denied on an
    external-volume repo under launchd (probed 2026-09-02); dist/ is copied
    back by cp. `npm ci` runs only when that dir's node_modules is missing or
    package-lock.json changed (cksum stamp) — not on every deploy;
  - EPERM / "operation not permitted" in the npm log → the web half is
    `skipped_tcc` (a rollback cannot grant Full Disk Access), never `fail`;
  - toolchain absent → the half is `skipped` with a warn, the step is never
    `fail` because of it (mirror of the `app` precedent, §56.5);
  - a broken build (npm run build / shell/build.sh non-zero, or a build that
    exits 0 without producing its artifact) → `ui=fail`;
  - the per-command wall-clock budget (AIASSISTANT_UI_BUDGET) turns a hung
    build into `ui=fail` (exit 124) instead of eating the auto-deploy watchdog;
  - the legacy "Zelin's AI Assistant.app" next to the shell bundle is never
    touched (D3) — byte-identical and same mtime afterwards;
  - `--pkg-postinstall` skips the whole step;
  - failed_deploy_steps counts `ui=fail`, never `ui=skipped`;
  - relaunch rule: only --non-interactive, only after this run installed a
    bundle, only when the app is running (pkill -TERM … then open -g); the
    interactive run never quits a running app.
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
_DARWIN = sys.platform == "darwin"

# the shape install.sh's shell half needs from the real system (never faked)
_SYSTEM_TOOLS = ("bash", "sh", "uname", "date", "tail", "head", "wc", "cut", "cksum",
                 "mv", "rm", "mkdir", "dirname", "basename", "sed", "cat", "sleep",
                 "tr", "grep", "awk", "env", "ditto", "cp", "touch", "rsync")

FAKE_NPM = r"""#!/bin/bash
# fake npm: record "npm <args> cwd=<pwd>", honour FAKE_NPM_CI_RC / FAKE_NPM_BUILD_RC,
# produce dist/index.html on a successful build unless FAKE_NPM_NO_DIST=1.
printf 'npm %s cwd=%s\n' "$*" "$PWD" >> "$CALLS"
case "$1" in
    ci)  if [ "${FAKE_NPM_EPERM:-0}" -eq 1 ]; then echo "npm error EPERM: operation not permitted, scandir '$PWD'" >&2; exit 1; fi
         exit "${FAKE_NPM_CI_RC:-0}" ;;
    run) [ -n "${FAKE_NPM_BUILD_SLEEP:-}" ] && sleep "$FAKE_NPM_BUILD_SLEEP"
         if [ "${FAKE_NPM_BUILD_RC:-0}" -ne 0 ]; then echo "tsc: boom" >&2; exit "$FAKE_NPM_BUILD_RC"; fi
         if [ "${FAKE_NPM_NO_DIST:-0}" -ne 1 ]; then mkdir -p dist && echo '<!doctype html>' > dist/index.html; fi
         exit 0 ;;
esac
exit 0
"""

FAKE_SHELL_BUILD = r"""#!/bin/bash
# fake shell/build.sh: record args + the ZAI_PORT env install.sh hands over;
# --check-toolchain answers FAKE_SHELL_TOOLCHAIN_RC; a build honours FAKE_SHELL_RC
# and assembles a minimal bundle unless FAKE_SHELL_NO_BUNDLE=1.
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
printf 'shell/build.sh %s ZAI_PORT=%s\n' "$*" "${ZAI_PORT:-}" >> "$CALLS"
if [ "${1:-}" = "--check-toolchain" ]; then exit "${FAKE_SHELL_TOOLCHAIN_RC:-0}"; fi
if [ "${FAKE_SHELL_RC:-0}" -ne 0 ]; then echo "swiftc: boom" >&2; exit "$FAKE_SHELL_RC"; fi
if [ "${FAKE_SHELL_NO_BUNDLE:-0}" -ne 1 ]; then
    app="$here/build/Zelin AI Board.app"
    rm -rf "$app"; mkdir -p "$app/Contents/MacOS"
    printf '<plist><dict><key>CFBundleIdentifier</key><string>com.zelin.ai-board</string></dict></plist>\n' > "$app/Contents/Info.plist"
    printf 'build-%s\n' "${FAKE_SHELL_BUILD_TAG:-1}" > "$app/Contents/MacOS/ZelinAIBoard"
fi
exit 0
"""

# pgrep/pkill/open fakes for the relaunch rule: a flag file = "the app is running"
FAKE_PGREP = "#!/bin/bash\nprintf 'pgrep %s\\n' \"$*\" >> \"$CALLS\"\n[ -f \"$RUNNING_FLAG\" ]\n"
FAKE_PKILL = "#!/bin/bash\nprintf 'pkill %s\\n' \"$*\" >> \"$CALLS\"\nrm -f \"$RUNNING_FLAG\"\n"
FAKE_OPEN = "#!/bin/bash\nprintf 'open %s\\n' \"$*\" >> \"$CALLS\"\nexit \"${FAKE_OPEN_RC:-0}\"\n"

_FNS = ("report_step", "failed_deploy_steps", "ui_run_with_timeout", "ui_log_begin",
        "ui_log_tail", "ui_now", "ui_web_build_dir", "ui_sync_web_sources",
        "ui_log_says_tcc", "ui_web_failed", "install_web_ui", "install_shell_app",
        "install_ui", "relaunch_shell_app")


def _install_sh_text():
    return (REPO / "install.sh").read_text(encoding="utf-8")


def _install_sh_fn(name):
    m = re.search(r"^%s\(\) \{.*?^\}" % re.escape(name), _install_sh_text(),
                  flags=re.S | re.M)
    assert m, "install.sh no longer defines %s()" % name
    return m.group(0) + "\n"


def _ui_globals():
    """The `UI_*=` assignments install.sh makes at top level (real values)."""
    lines = [ln for ln in _install_sh_text().splitlines()
             if re.match(r"^UI_[A-Z_]+=", ln)]
    assert lines, "install.sh lost its UI_* globals"
    return "\n".join(lines) + "\n"


@unittest.skipIf(_WIN, "install.sh is POSIX-only; the Windows installer is install.ps1")
class InstallUiStepTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="install-ui-step-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.calls = self.tmp / "calls.log"
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.apps = self.tmp / "Applications"
        self.apps.mkdir()
        # the frozen legacy app (D3) — must come out byte-identical
        self.legacy = self.apps / "Zelin's AI Assistant.app" / "Contents" / "Info.plist"
        self.legacy.parent.mkdir(parents=True)
        self.legacy.write_text("legacy-untouched\n", encoding="utf-8")
        os.utime(self.legacy, (1_700_000_000, 1_700_000_000))
        self.legacy_snapshot = (self.legacy.read_bytes(), self.legacy.stat().st_mtime)

        self.repo = self.tmp / "repo"
        (self.repo / "web").mkdir(parents=True)
        (self.repo / "web" / "package.json").write_text('{"name": "zai-web"}\n', encoding="utf-8")
        (self.repo / "web" / "package-lock.json").write_text("lock-v1\n", encoding="utf-8")
        (self.repo / "shell").mkdir()
        self._write_exec(self.repo / "shell" / "build.sh", FAKE_SHELL_BUILD)

        # system tools only via symlinks: PATH never reaches /usr/bin, so the
        # "toolchain absent" case cannot accidentally find the real swiftc/npm
        self.sysbin = self.tmp / "sysbin"
        self.sysbin.mkdir()
        for tool in _SYSTEM_TOOLS:
            real = shutil.which(tool)
            if real:
                os.symlink(real, self.sysbin / tool)
        self.fakebin = self.tmp / "fakebin"
        self.fakebin.mkdir()
        self._write_exec(self.fakebin / "pgrep", FAKE_PGREP)
        self._write_exec(self.fakebin / "pkill", FAKE_PKILL)
        self._write_exec(self.fakebin / "open", FAKE_OPEN)
        self.toolbin = self.tmp / "toolbin"   # npm/node/swiftc live here (or not)
        self.toolbin.mkdir()
        self.running_flag = self.tmp / "app.running"
        self.build = self.tmp / "web-build"   # AIASSISTANT_UI_BUILD_DIR (stands in for ~/Library/Caches/…)

    def _write_exec(self, path, text):
        path.write_text(text, encoding="utf-8")
        path.chmod(0o755)

    def _with_toolchains(self, web=True, swift=True):
        if web:
            self._write_exec(self.toolbin / "npm", FAKE_NPM)
            self._write_exec(self.toolbin / "node", "#!/bin/bash\nexit 0\n")
        if swift:
            self._write_exec(self.toolbin / "swiftc", "#!/bin/bash\nexit 0\n")

    def _run(self, *, non_interactive=1, pkg=0, port="47820", relaunch=False,
             env=None, timeout=90):
        script = ("set -u\n"
                  "ok() { echo \"OK: $1\"; }; warn() { echo \"WARN: $1\"; }; info() { echo \"INFO: $1\"; }\n"
                  "REPORT_STEPS=''\n"
                  + _ui_globals()
                  + "".join(_install_sh_fn(fn) for fn in _FNS)
                  + 'REPO_ROOT="$1"; NON_INTERACTIVE="$2"; PKG_POSTINSTALL="$3"; SERVER_PORT="$4"\n'
                  "install_ui\n"
                  '[ "${RUN_RELAUNCH:-0}" = 1 ] && relaunch_shell_app\n'
                  "printf '@@REPORT@@%s' \"$REPORT_STEPS\"\n")
        full = {
            "PATH": os.pathsep.join([str(self.fakebin), str(self.toolbin), str(self.sysbin)]),
            "HOME": str(self.home),
            "AIASSISTANT_UI_APPS_DIR": str(self.apps),
            "AIASSISTANT_UI_BUILD_DIR": str(self.build),
            "CALLS": str(self.calls),
            "RUNNING_FLAG": str(self.running_flag),
            "RUN_RELAUNCH": "1" if relaunch else "0",
            **(env or {}),
        }
        proc = subprocess.run(
            ["bash", "-c", script, "bash", str(self.repo), str(non_interactive), str(pkg), port],
            capture_output=True, text=True, timeout=timeout, env=full)
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        out, _, report = proc.stdout.partition("@@REPORT@@")
        return out, report.strip()

    def _calls(self):
        return self.calls.read_text(encoding="utf-8").splitlines() if self.calls.exists() else []

    def _ui_line(self, report):
        lines = [ln for ln in report.splitlines() if ln.startswith("ui=")]
        self.assertEqual(len(lines), 1, report)
        return lines[0]

    def _assert_legacy_untouched(self):
        self.assertEqual((self.legacy.read_bytes(), self.legacy.stat().st_mtime),
                         self.legacy_snapshot,
                         "the frozen legacy app must never be touched by the ui step (D3)")

    # -- the happy path -------------------------------------------------------- #

    def test_toolchains_present_builds_and_installs_and_reports_ok(self):
        self._with_toolchains()
        out, report = self._run()
        ui = self._ui_line(report)
        self.assertTrue(ui.startswith("ui=ok:"), ui)
        self.assertIn("web ok (npm ci", ui)
        self.assertRegex(ui, r"\d+s total$")
        calls = self._calls()
        self.assertTrue(any(c.startswith("npm ci --no-audit --no-fund") for c in calls), calls)
        self.assertTrue(any(c.startswith("npm run build") for c in calls), calls)
        self.assertTrue((self.repo / "web" / "dist" / "index.html").exists(), "dist published into the repo")
        for c in calls:
            if c.startswith("npm "):
                self.assertTrue(c.endswith("cwd=%s" % self.build), "node must never run with cwd inside the repo (TCC): " + c)
        self.assertTrue((self.build / "package.json").exists(), "sources mirrored into the build dir")
        self.assertFalse((self.repo / "web" / "node_modules").exists(), "npm ci never writes into the checkout")
        if _DARWIN:
            self.assertIn("shell ok (", ui)
            self.assertTrue((self.apps / "Zelin AI Board.app" / "Contents" / "MacOS"
                             / "ZelinAIBoard").exists(), "bundle installed into the apps dir")
            self.assertFalse((self.apps / ".Zelin AI Board.app.staged").exists(), "staging dir swapped away")
            self.assertTrue(any("shell/build.sh  ZAI_PORT=47820" in c for c in calls), calls)
        else:
            self.assertIn("shell skipped (not macOS)", ui)
        self._assert_legacy_untouched()

    def test_npm_ci_runs_only_when_the_lock_changes(self):
        self._with_toolchains(swift=False)
        self._run()
        first = [c for c in self._calls() if c.startswith("npm ci")]
        self.assertEqual(len(first), 1, "first run: node_modules missing → npm ci")
        # stamp was written by the successful ci (in the build dir, next to node_modules)
        stamp = self.build / "node_modules" / ".zai-package-lock.cksum"
        self.assertTrue(stamp.exists())
        (self.build / "node_modules" / "left-by-npm").write_text("", encoding="utf-8")
        self._run()
        self.assertTrue((self.build / "node_modules" / "left-by-npm").exists(),
                        "the source mirror must not wipe node_modules (rsync --exclude)")
        self.assertEqual(len([c for c in self._calls() if c.startswith("npm ci")]), 1,
                         "same lock → no second npm ci")
        self.assertEqual(len([c for c in self._calls() if c.startswith("npm run build")]), 2,
                         "the build itself runs every deploy")
        (self.repo / "web" / "package-lock.json").write_text("lock-v2\n", encoding="utf-8")
        self._run()
        self.assertEqual(len([c for c in self._calls() if c.startswith("npm ci")]), 2,
                         "changed lock → npm ci again")

    @unittest.skipUnless(_DARWIN, "the shell half only builds on macOS")
    def test_shell_port_from_config_reaches_build_sh(self):
        self._with_toolchains(web=False)
        self._run(port="47999")
        self.assertTrue(any("shell/build.sh  ZAI_PORT=47999" in c for c in self._calls()), self._calls())

    @unittest.skipUnless(_DARWIN, "the shell half only builds on macOS")
    def test_reinstall_replaces_the_bundle_wholesale(self):
        self._with_toolchains(web=False)
        self._run(env={"FAKE_SHELL_BUILD_TAG": "one"})
        stale = self.apps / "Zelin AI Board.app" / "Contents" / "Resources" / "stale.txt"
        stale.parent.mkdir(parents=True)
        stale.write_text("from an older build\n", encoding="utf-8")
        self._run(env={"FAKE_SHELL_BUILD_TAG": "two"})
        binary = self.apps / "Zelin AI Board.app" / "Contents" / "MacOS" / "ZelinAIBoard"
        self.assertEqual(binary.read_text(encoding="utf-8"), "build-two\n")
        self.assertFalse(stale.exists(), "stage-then-swap must not merge into the old bundle")
        self._assert_legacy_untouched()

    # -- skipped, never fail ----------------------------------------------------- #

    def test_missing_toolchains_skip_with_a_warn_never_fail(self):
        out, report = self._run()   # toolbin empty: no npm/node/swiftc anywhere on PATH
        ui = self._ui_line(report)
        self.assertTrue(ui.startswith("ui=skipped:"), ui)
        self.assertIn("web skipped (no node/npm)", ui)
        self.assertIn("shell skipped", ui)
        self.assertIn("WARN:", out)
        self.assertEqual(self._calls(), [], "nothing may be invoked without a toolchain")
        self.assertFalse((self.apps / "Zelin AI Board.app").exists())
        self._assert_legacy_untouched()

    def test_web_only_toolchain_is_ok_with_shell_skipped(self):
        self._with_toolchains(swift=False)
        _, report = self._run()
        ui = self._ui_line(report)
        self.assertTrue(ui.startswith("ui=ok:"), ui)
        self.assertIn("shell skipped", ui)

    def test_pkg_postinstall_skips_the_whole_step(self):
        self._with_toolchains()
        _, report = self._run(pkg=1)
        self.assertEqual(self._ui_line(report), "ui=skipped:pkg-postinstall never builds the UI")
        self.assertEqual(self._calls(), [])

    # -- fail ------------------------------------------------------------------- #

    def test_web_build_failure_is_ui_fail(self):
        self._with_toolchains()
        out, report = self._run(env={"FAKE_NPM_BUILD_RC": "2"})
        ui = self._ui_line(report)
        self.assertTrue(ui.startswith("ui=fail:"), ui)
        self.assertIn("web fail (npm run build exit 2)", ui)
        self.assertIn("last lines of", out, "the build log tail is echoed on failure")
        self.assertFalse((self.repo / "web" / "dist" / "index.html").exists())
        if _DARWIN:
            self.assertIn("shell ok (", ui, "the halves are independent")
        self._assert_legacy_untouched()

    def test_npm_ci_failure_is_ui_fail_and_skips_the_build(self):
        self._with_toolchains(swift=False)
        _, report = self._run(env={"FAKE_NPM_CI_RC": "1"})
        ui = self._ui_line(report)
        self.assertIn("web fail (npm ci exit 1)", ui)
        self.assertFalse(any(c.startswith("npm run build") for c in self._calls()))
        self.assertFalse((self.build / "node_modules" / ".zai-package-lock.cksum").exists(),
                         "a failed ci must not stamp the lock")

    def test_eperm_from_node_is_skipped_tcc_not_fail(self):
        # 2026-09-02 launchd probe: homebrew node → EPERM on the external volume.
        # A rollback cannot grant Full Disk Access, so this is not a deploy failure.
        self._with_toolchains(swift=False)
        out, report = self._run(env={"FAKE_NPM_EPERM": "1"})
        ui = self._ui_line(report)
        self.assertTrue(ui.startswith("ui=skipped_tcc:"), ui)
        self.assertIn("web skipped_tcc (npm ci exit 1", ui)
        self.assertIn("Full Disk Access", ui)
        self.assertIn("Full Disk Access", out)
        self.assertFalse(any(c.startswith("npm run build") for c in self._calls()))

    def test_skipped_tcc_web_with_a_good_shell_is_still_ok(self):
        if not _DARWIN:
            self.skipTest("the shell half only builds on macOS")
        self._with_toolchains()
        _, report = self._run(env={"FAKE_NPM_EPERM": "1"})
        ui = self._ui_line(report)
        self.assertTrue(ui.startswith("ui=ok:"), ui)
        self.assertIn("web skipped_tcc", ui)
        self.assertIn("shell ok", ui)

    def test_build_that_produces_no_dist_is_ui_fail(self):
        self._with_toolchains(swift=False)
        _, report = self._run(env={"FAKE_NPM_NO_DIST": "1"})
        self.assertIn("web fail (no dist/index.html after build)", self._ui_line(report))

    @unittest.skipUnless(_DARWIN, "the shell half only builds on macOS")
    def test_shell_build_failure_is_ui_fail_and_leaves_the_installed_bundle(self):
        self._with_toolchains()
        self._run()   # a good install first
        _, report = self._run(env={"FAKE_SHELL_RC": "3"})
        ui = self._ui_line(report)
        self.assertTrue(ui.startswith("ui=fail:"), ui)
        self.assertIn("shell fail (shell/build.sh exit 3)", ui)
        self.assertIn("web ok", ui)
        self.assertTrue((self.apps / "Zelin AI Board.app").exists(),
                        "a failed build leaves the previously installed bundle alone")
        self._assert_legacy_untouched()

    @unittest.skipUnless(_DARWIN, "the shell half only builds on macOS")
    def test_shell_toolchain_too_old_is_skipped_not_fail(self):
        self._with_toolchains(web=False)
        _, report = self._run(env={"FAKE_SHELL_TOOLCHAIN_RC": "1"})
        ui = self._ui_line(report)
        self.assertTrue(ui.startswith("ui=skipped:"), ui)
        self.assertIn("shell skipped (no swift toolchain)", ui)
        self.assertEqual([c for c in self._calls() if "build.sh" in c],
                         ["shell/build.sh --check-toolchain ZAI_PORT="])

    def test_budget_turns_a_hung_build_into_ui_fail(self):
        self._with_toolchains(swift=False)
        _, report = self._run(env={"FAKE_NPM_BUILD_SLEEP": "4", "AIASSISTANT_UI_BUDGET": "1"},
                              timeout=60)
        self.assertIn("web fail (npm run build exit 124)", self._ui_line(report))

    # -- the exit-code rule ------------------------------------------------------- #

    def test_failed_deploy_steps_counts_ui_fail_but_not_ui_skipped(self):
        script = ("set -u\n" + _install_sh_fn("failed_deploy_steps")
                  + 'REPORT_STEPS="$1"\nfailed_deploy_steps\n')

        def failed(steps):
            proc = subprocess.run(["bash", "-c", script, "bash", steps],
                                  capture_output=True, text=True, timeout=60)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            return [ln for ln in proc.stdout.splitlines() if ln]

        self.assertEqual(failed("config=ok\nui=skipped:web skipped (no node/npm); shell skipped\nlaunchd=ok\n"), [])
        self.assertEqual(failed("ui=skipped_tcc:web skipped_tcc (npm ci exit 1: EPERM under launchd); shell skipped\n"), [],
                         "TCC refusal is not a deploy failure (a rollback cannot grant FDA)")
        self.assertEqual(failed("config=ok\nui=fail:web fail (npm run build exit 2); shell ok\nlaunchd=ok\n"),
                         ["ui=fail:web fail (npm run build exit 2); shell ok"])
        self.assertEqual(failed("app=fail:legacy\nui=ok:web ok; shell ok\n"), [],
                         "the legacy app exception stays; a good ui step adds nothing")

    # -- the relaunch rule (§56.5) ------------------------------------------------ #

    @unittest.skipUnless(_DARWIN, "relaunch needs an installed bundle (macOS half)")
    def test_non_interactive_relaunches_a_running_app_after_install(self):
        self._with_toolchains(web=False)
        self.running_flag.write_text("", encoding="utf-8")
        self._run(non_interactive=1, relaunch=True)
        calls = self._calls()
        self.assertIn("pkill -TERM -x ZelinAIBoard", calls)
        opens = [c for c in calls if c.startswith("open ")]
        self.assertEqual(len(opens), 1, calls)
        self.assertTrue(opens[0].startswith("open -g "), "relaunch must not steal focus")
        self.assertIn("Zelin AI Board.app", opens[0])
        self.assertLess(calls.index("pkill -TERM -x ZelinAIBoard"), calls.index(opens[0]))

    @unittest.skipUnless(_DARWIN, "relaunch needs an installed bundle (macOS half)")
    def test_non_interactive_does_not_relaunch_when_the_app_is_not_running(self):
        self._with_toolchains(web=False)
        self._run(non_interactive=1, relaunch=True)
        calls = self._calls()
        self.assertFalse(any(c.startswith(("pkill", "open")) for c in calls), calls)

    def test_interactive_never_quits_or_relaunches(self):
        self._with_toolchains()
        self.running_flag.write_text("", encoding="utf-8")
        out, _ = self._run(non_interactive=0, relaunch=True)
        calls = self._calls()
        self.assertFalse(any(c.startswith(("pkill", "open")) for c in calls), calls)
        if _DARWIN:
            self.assertIn("quit + reopen it", out, "interactive mode tells the owner instead")

    def test_relaunch_is_a_no_op_when_nothing_was_installed(self):
        # toolchain absent → nothing installed → even a running app is left alone
        self.running_flag.write_text("", encoding="utf-8")
        self._run(non_interactive=1, relaunch=True)
        self.assertEqual(self._calls(), [])


if __name__ == "__main__":
    unittest.main()
