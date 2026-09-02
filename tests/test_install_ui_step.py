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
  - the §54 name swap (owner 2026-09-02): the shell installs as
    "Zelin's AI Assistant.app"; a legacy bundle (id com.zelin.ai-engineer)
    found on that path is MOVED to "Zelin's AI Assistant (old).app" — a
    same-directory rename, its files byte-identical and same mtime afterwards
    (never edited: the Info.plist is inside the signature seal); when "(old)"
    already exists that copy is kept and the redundant product-path bundle is
    removed (parked under a timestamped name when rm is refused); the
    pre-rename "Zelin AI Board.app" is removed after the install only when its
    id is com.zelin.ai-board; a com.zelin.ai-board bundle on the product path
    is simply replaced; nothing is ever moved or deleted by folder name alone;
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
                 "tr", "grep", "awk", "env", "ditto", "cp", "touch", "rsync", "plutil")

SHELL_APP = "Zelin's AI Assistant.app"            # install.sh UI_APP_NAME (§54)
LEGACY_OLD_APP = "Zelin's AI Assistant (old).app"  # install.sh UI_LEGACY_APP_NAME
PREVIOUS_SHELL_APP = "Zelin AI Board.app"          # install.sh UI_PREVIOUS_APP_NAME (≤ v0.48.29)
SHELL_ID = "com.zelin.ai-board"
LEGACY_ID = "com.zelin.ai-engineer"

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
    app="$here/build/Zelin's AI Assistant.app"
    rm -rf "$app"; mkdir -p "$app/Contents/MacOS"
    printf '<plist><dict><key>CFBundleIdentifier</key><string>com.zelin.ai-board</string></dict></plist>\n' > "$app/Contents/Info.plist"
    printf 'build-%s\n' "${FAKE_SHELL_BUILD_TAG:-1}" > "$app/Contents/MacOS/ZelinAIBoard"
fi
exit 0
"""

_FNS = ("report_step", "failed_deploy_steps", "ui_run_with_timeout", "ui_log_begin",
        "ui_log_tail", "ui_now", "ui_web_build_dir", "ui_sync_web_sources",
        "ui_log_says_tcc", "ui_web_failed", "install_web_ui", "ui_bundle_id",
        "ui_retire_legacy_app", "ui_remove_previous_shell", "install_shell_app",
        "install_ui", "relaunch_shell_app")


def _plant_bundle(app_dir, bundle_id, payload="payload\n", mtime=1_700_000_000):
    """A fake .app with a real (minimal) Info.plist carrying `bundle_id`; returns
    (Info.plist path, executable path) with a fixed mtime for the untouched check."""
    plist = app_dir / "Contents" / "Info.plist"
    exe = app_dir / "Contents" / "MacOS" / "bin"
    exe.parent.mkdir(parents=True)
    plist.write_text('<plist><dict><key>CFBundleIdentifier</key><string>%s</string></dict></plist>\n'
                     % bundle_id, encoding="utf-8")
    exe.write_text(payload, encoding="utf-8")
    for p in (plist, exe):
        os.utime(p, (mtime, mtime))
    return plist, exe


# pgrep/pkill/open fakes for the relaunch rule: a flag file = "the app is running"
FAKE_PGREP = "#!/bin/bash\nprintf 'pgrep %s\\n' \"$*\" >> \"$CALLS\"\n[ -f \"$RUNNING_FLAG\" ]\n"
FAKE_PKILL = "#!/bin/bash\nprintf 'pkill %s\\n' \"$*\" >> \"$CALLS\"\nrm -f \"$RUNNING_FLAG\"\n"
FAKE_OPEN = "#!/bin/bash\nprintf 'open %s\\n' \"$*\" >> \"$CALLS\"\nexit \"${FAKE_OPEN_RC:-0}\"\n"


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
        # the frozen legacy app (D3) already moved to "(old)" — must come out
        # byte-identical whatever the ui step does
        self.legacy_plist, self.legacy_exe = _plant_bundle(self.apps / LEGACY_OLD_APP, LEGACY_ID,
                                                            payload="legacy-untouched\n")
        self.legacy_snapshot = self._snapshot(self.legacy_plist, self.legacy_exe)

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

    @staticmethod
    def _snapshot(*paths):
        return [(p.read_bytes(), p.stat().st_mtime) for p in paths]

    def _assert_legacy_untouched(self):
        self.assertEqual(self._snapshot(self.legacy_plist, self.legacy_exe), self.legacy_snapshot,
                         "the frozen legacy app must never be edited by the ui step (D3; signature seal)")

    def _shell_bundle_ok(self, apps_dir=None, tag="1"):
        app = (apps_dir or self.apps) / SHELL_APP
        self.assertTrue((app / "Contents" / "MacOS" / "ZelinAIBoard").exists(), "bundle installed into the apps dir")
        self.assertEqual((app / "Contents" / "MacOS" / "ZelinAIBoard").read_text(encoding="utf-8"), "build-%s\n" % tag)
        self.assertFalse(((apps_dir or self.apps) / (".%s.staged" % SHELL_APP)).exists(), "staging dir swapped away")

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
            self.assertNotIn("legacy app moved", ui, "nothing sat on the product path")
            self._shell_bundle_ok()
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
        stale = self.apps / SHELL_APP / "Contents" / "Resources" / "stale.txt"
        stale.parent.mkdir(parents=True)
        stale.write_text("from an older build\n", encoding="utf-8")
        self._run(env={"FAKE_SHELL_BUILD_TAG": "two"})
        self._shell_bundle_ok(tag="two")
        self.assertFalse(stale.exists(), "stage-then-swap must not merge into the old bundle")
        self._assert_legacy_untouched()

    # -- the §54 name swap: legacy bundle on the product path ---------------------- #

    @unittest.skipUnless(_DARWIN, "the shell half only builds on macOS")
    def test_legacy_app_on_the_product_path_is_moved_to_old_then_shell_installed(self):
        # the live 2026-09-02 shape: only the legacy app is installed, under the product name
        shutil.rmtree(self.apps / LEGACY_OLD_APP)
        plist, exe = _plant_bundle(self.apps / SHELL_APP, LEGACY_ID, payload="legacy-v0.48.0\n")
        before = self._snapshot(plist, exe)
        self._with_toolchains(web=False)
        out, report = self._run()
        ui = self._ui_line(report)
        self.assertTrue(ui.startswith("ui=ok:"), ui)
        self.assertIn('legacy app moved to "%s"' % LEGACY_OLD_APP, ui)
        self.assertIn("legacy app moved aside", out)
        self._shell_bundle_ok()
        moved = self.apps / LEGACY_OLD_APP
        self.assertEqual(self._snapshot(moved / "Contents" / "Info.plist", moved / "Contents" / "MacOS" / "bin"),
                         before, "a same-directory rename: bytes and mtimes untouched (signature seal)")
        self.assertEqual(sorted(p.name for p in self.apps.iterdir()), sorted([SHELL_APP, LEGACY_OLD_APP]))

    @unittest.skipUnless(_DARWIN, "the shell half only builds on macOS")
    def test_legacy_absent_is_a_plain_install(self):
        shutil.rmtree(self.apps / LEGACY_OLD_APP)
        self._with_toolchains(web=False)
        _, report = self._run()
        ui = self._ui_line(report)
        self.assertTrue(ui.startswith("ui=ok:"), ui)
        self.assertNotIn("legacy", ui)
        self.assertEqual([p.name for p in self.apps.iterdir()], [SHELL_APP])

    @unittest.skipUnless(_DARWIN, "the shell half only builds on macOS")
    def test_both_legacy_copies_present_keeps_old_and_removes_the_product_path_one(self):
        # "(old)" exists from an earlier run AND a legacy bundle came back to the
        # product path (an old-layout .pkg / Sparkle re-install): the "(old)" copy
        # is the one that stays, the redundant one is removed, the shell lands.
        _plant_bundle(self.apps / SHELL_APP, LEGACY_ID, payload="legacy-came-back\n")
        self._with_toolchains(web=False)
        out, report = self._run()
        ui = self._ui_line(report)
        self.assertTrue(ui.startswith("ui=ok:"), ui)
        self.assertNotIn("legacy app moved", ui, "nothing was moved to (old) — it was already there")
        self.assertIn("second legacy bundle", out)
        self._shell_bundle_ok()
        self._assert_legacy_untouched()
        self.assertEqual(sorted(p.name for p in self.apps.iterdir()), sorted([SHELL_APP, LEGACY_OLD_APP]),
                         "no parked copy when rm succeeds")

    @unittest.skipUnless(_DARWIN, "the shell half only builds on macOS")
    def test_both_present_and_rm_refused_parks_the_redundant_copy_beside(self):
        # a root-owned (.pkg) bundle cannot be deleted without sudo — but a same-dir
        # rename works; the redundant copy is parked under a timestamped name and
        # the shell still lands (deploy proceeds, the warn names the sudo cleanup)
        _plant_bundle(self.apps / SHELL_APP, LEGACY_ID, payload="root-owned\n")
        locked = [self.apps / SHELL_APP / "Contents", self.apps / SHELL_APP / "Contents" / "MacOS"]
        for d in locked:
            d.chmod(0o555)

        def unlock():
            for d in self.apps.glob("*/Contents/MacOS"):
                d.chmod(0o755)
            for d in self.apps.glob("*/Contents"):
                d.chmod(0o755)
        self.addCleanup(unlock)
        self._with_toolchains(web=False)
        out, report = self._run()
        ui = self._ui_line(report)
        self.assertTrue(ui.startswith("ui=ok:"), ui)
        self.assertIn("parked as", out)
        self.assertIn("sudo rm -rf", out)
        self._shell_bundle_ok()
        self._assert_legacy_untouched()
        parked = [p.name for p in self.apps.iterdir() if p.name not in (SHELL_APP, LEGACY_OLD_APP)]
        self.assertEqual(len(parked), 1, parked)
        self.assertRegex(parked[0], r"^Zelin's AI Assistant \(old\) \d{8}-\d{6}\.app$")
        self.assertEqual((self.apps / parked[0] / "Contents" / "MacOS" / "bin").read_text(encoding="utf-8"),
                         "root-owned\n")

    @unittest.skipUnless(_DARWIN, "the shell half only builds on macOS")
    def test_previous_board_bundle_is_removed_after_the_install_only_when_it_is_the_shell(self):
        _plant_bundle(self.apps / PREVIOUS_SHELL_APP, SHELL_ID, payload="old-shell\n")
        self._with_toolchains(web=False)
        out, _ = self._run()
        self.assertFalse((self.apps / PREVIOUS_SHELL_APP).exists(), "the ≤ v0.48.29 shell bundle goes")
        self.assertIn("removed the pre-rename shell bundle", out)
        self._shell_bundle_ok()
        self._assert_legacy_untouched()
        # a stranger under that name (not our bundle id) is left alone
        _plant_bundle(self.apps / PREVIOUS_SHELL_APP, "com.example.other", payload="not-ours\n")
        out, _ = self._run()
        self.assertTrue((self.apps / PREVIOUS_SHELL_APP).exists())
        self.assertIn("left alone", out)

    @unittest.skipUnless(_DARWIN, "the shell half only builds on macOS")
    def test_previous_board_bundle_stays_when_the_install_fails(self):
        _plant_bundle(self.apps / PREVIOUS_SHELL_APP, SHELL_ID, payload="old-shell\n")
        self._with_toolchains(web=False)
        _, report = self._run(env={"FAKE_SHELL_RC": "3"})
        self.assertIn("shell fail", self._ui_line(report))
        self.assertTrue((self.apps / PREVIOUS_SHELL_APP).exists(), "nothing new landed → the old bundle is still the product")

    @unittest.skipUnless(_DARWIN, "the shell half only builds on macOS")
    def test_shell_on_the_product_path_is_replaced_not_retired(self):
        _plant_bundle(self.apps / SHELL_APP, SHELL_ID, payload="previous shell build\n")
        self._with_toolchains(web=False)
        out, report = self._run(env={"FAKE_SHELL_BUILD_TAG": "new"})
        self.assertTrue(self._ui_line(report).startswith("ui=ok:"))
        self.assertNotIn("legacy", out)
        self._shell_bundle_ok(tag="new")
        self.assertEqual(sorted(p.name for p in self.apps.iterdir()), sorted([SHELL_APP, LEGACY_OLD_APP]))

    @unittest.skipUnless(_DARWIN, "the shell half only builds on macOS")
    def test_legacy_on_the_product_path_that_cannot_move_is_shell_fail_never_rm(self):
        # the apps dir is writable but the rename is refused (a read-only sub-mount
        # or ACL) → the legacy bundle is never rm'ed and the shell is not installed
        # over it; the half is `fail` with the mv command in the output
        shutil.rmtree(self.apps / LEGACY_OLD_APP)
        plist, exe = _plant_bundle(self.apps / SHELL_APP, LEGACY_ID)
        before = self._snapshot(plist, exe)
        self._write_exec(self.fakebin / "mv", "#!/bin/bash\necho 'mv: refused' >&2\nexit 1\n")
        self._with_toolchains(web=False)
        out, report = self._run()
        ui = self._ui_line(report)
        self.assertIn("shell fail (legacy app still on the product path", ui)
        self.assertIn("could not move the legacy app", out)
        self.assertEqual(self._snapshot(plist, exe), before)
        self.assertEqual([p.name for p in self.apps.iterdir()], [SHELL_APP])

    # -- skipped, never fail ----------------------------------------------------- #

    def test_missing_toolchains_skip_with_a_warn_never_fail(self):
        out, report = self._run()   # toolbin empty: no npm/node/swiftc anywhere on PATH
        ui = self._ui_line(report)
        self.assertTrue(ui.startswith("ui=skipped:"), ui)
        self.assertIn("web skipped (no node/npm)", ui)
        self.assertIn("shell skipped", ui)
        self.assertIn("WARN:", out)
        self.assertEqual(self._calls(), [], "nothing may be invoked without a toolchain")
        self.assertFalse((self.apps / SHELL_APP).exists())
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
        self.assertTrue((self.apps / SHELL_APP).exists(),
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
        self.assertIn(SHELL_APP, opens[0])
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
