"""install.sh `relaunch_shell_app` hands the shell an explicit `--background` (D38; CONTRACT §56.5 追记).

§56.5 promised the auto-deploy relaunch 「不抢焦点」 with `open -g`, but `-g` only asks
LaunchServices not to switch apps — the shell's own buildWindow() used to
makeKeyAndOrderFront + activate on every launch, so a window the owner had closed
reappeared after every deploy. The shell now decides visibility from its launch
source (shell/Sources/ShellSupport.swift LaunchPolicy, pinned by
shell/tests/LaunchHarness.swift); this file pins the installer half of the
contract: the relaunch is `open -g "<bundle>" --args --background` — `-g` kept,
the flag verbatim, `--args` last so it is passed to the app as argv[1] — and the
flag never appears on any other `open` in install.sh (an interactive `open` must
stay a foreground launch). Real bash, fake pgrep / pkill / open (nothing is
launched); the function text is extracted from install.sh verbatim, the same
way tests/test_install_ui_step.py does.
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

FAKE_PGREP = "#!/bin/bash\nprintf 'pgrep %s\\n' \"$*\" >> \"$CALLS\"\n[ -f \"$RUNNING_FLAG\" ]\n"
FAKE_PKILL = "#!/bin/bash\nprintf 'pkill %s\\n' \"$*\" >> \"$CALLS\"\nrm -f \"$RUNNING_FLAG\"\n"
# the fake records every argv word separately so the assertion sees exactly what
# the shell process would receive after `--args`
FAKE_OPEN = "#!/bin/bash\nfor a in \"$@\"; do printf 'open-arg %s\\n' \"$a\" >> \"$CALLS\"; done\nexit 0\n"


def _install_sh_text():
    return (REPO / "install.sh").read_text(encoding="utf-8")


def _install_sh_fn(name):
    m = re.search(r"^%s\(\) \{.*?^\}" % re.escape(name), _install_sh_text(), flags=re.S | re.M)
    assert m, "install.sh no longer defines %s()" % name
    return m.group(0) + "\n"


@unittest.skipIf(_WIN, "install.sh is POSIX-only; the Windows installer is install.ps1")
class RelaunchBackgroundFlagTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="install-relaunch-bg-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.calls = self.tmp / "calls.log"
        self.running_flag = self.tmp / "app.running"
        self.fakebin = self.tmp / "fakebin"
        self.fakebin.mkdir()
        for name, text in (("pgrep", FAKE_PGREP), ("pkill", FAKE_PKILL), ("open", FAKE_OPEN)):
            p = self.fakebin / name
            p.write_text(text, encoding="utf-8")
            p.chmod(0o755)
        # only the fakes plus the real `sleep`; PATH never reaches the real pgrep/pkill/open
        real_sleep = shutil.which("sleep")
        assert real_sleep, "sleep missing"
        os.symlink(real_sleep, self.fakebin / "sleep")
        self.app_path = self.tmp / "Applications" / "Zelin's AI Assistant.app"

    def _run(self, *, running=True, non_interactive=1, installed=1):
        if running:
            self.running_flag.write_text("", encoding="utf-8")
        script = ("set -u\n"
                  "ok() { echo \"OK: $1\"; }; warn() { echo \"WARN: $1\"; }\n"
                  "UI_EXEC_NAME=ZelinAIBoard\n"
                  + _install_sh_fn("relaunch_shell_app")
                  + 'NON_INTERACTIVE="$1"; UI_SHELL_INSTALLED="$2"; UI_APP_PATH="$3"\n'
                  "relaunch_shell_app\n")
        proc = subprocess.run(
            ["/bin/bash", "-c", script, "bash", str(non_interactive), str(installed), str(self.app_path)],
            capture_output=True, text=True, timeout=60,
            env={"PATH": str(self.fakebin), "CALLS": str(self.calls),
                 "RUNNING_FLAG": str(self.running_flag)})
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        return proc.stdout

    def _calls(self):
        return self.calls.read_text(encoding="utf-8").splitlines() if self.calls.exists() else []

    def test_relaunch_passes_background_after_args(self):
        out = self._run()
        calls = self._calls()
        open_args = [c[len("open-arg "):] for c in calls if c.startswith("open-arg ")]
        self.assertEqual(open_args, ["-g", str(self.app_path), "--args", "--background"],
                         "relaunch must be `open -g <bundle> --args --background`: -g kept, "
                         "--args last so the shell sees --background as argv[1]")
        self.assertIn("pkill -TERM -x ZelinAIBoard", calls)
        self.assertLess(calls.index("pkill -TERM -x ZelinAIBoard"), calls.index("open-arg -g"),
                        "quit first, relaunch after")
        self.assertIn("relaunched the shell app", out)

    def test_flag_is_the_word_the_shell_policy_reads(self):
        # the two halves of the contract must agree on the exact word; the shell side
        # is `LaunchPolicy.backgroundFlag` in shell/Sources/ShellSupport.swift
        swift = (REPO / "shell" / "Sources" / "ShellSupport.swift").read_text(encoding="utf-8")
        m = re.search(r'static let backgroundFlag = "([^"]+)"', swift)
        self.assertIsNotNone(m, "ShellSupport.swift lost LaunchPolicy.backgroundFlag")
        self.assertEqual(m.group(1), "--background")
        fn = _install_sh_fn("relaunch_shell_app")
        self.assertIn('open -g "$UI_APP_PATH" --args --background', fn)

    def test_no_other_open_in_install_sh_carries_the_flag(self):
        # an interactive `open` (owner-typed hint, bootstrap finale…) is a foreground
        # launch by definition; only the auto-deploy relaunch hides the window
        text = _install_sh_text()
        carriers = [ln.strip() for ln in text.splitlines()
                    if "--background" in ln and re.search(r"\bopen\b", ln) and not ln.strip().startswith("#")]
        self.assertEqual(carriers, ['if open -g "$UI_APP_PATH" --args --background 2>/dev/null; then'])

    def test_not_running_means_no_open_at_all(self):
        self._run(running=False)
        self.assertFalse(any(c.startswith(("pkill", "open-arg")) for c in self._calls()), self._calls())

    def test_interactive_never_relaunches(self):
        self._run(non_interactive=0)
        self.assertEqual(self._calls(), [])


if __name__ == "__main__":
    unittest.main()
