"""install.sh --no-launchd — scheduler-less install + the one-pass daemon proof (CONTRACT §69 / §23).

The CI acceptance run and scripts/bootstrap.sh dry runs pass
`--non-interactive --no-launchd`: step 5 loads nothing, step 6 writes nothing,
and one real `python3 -m act.actd --once` proves the daemon in this
environment. Pinned here with the real function text (same
`_install_sh_fn` extraction as tests/test_install_cron_tcc.py):

  - flags combine and an unknown flag is a usage error (exit 2) before any
    side effect — a typo in an unattended caller must not run the wrong mode;
  - `run_actd_once` records `launchd=skipped:--no-launchd…` + `actd_once=ok`
    when the interpreter exits 0, `actd_once=fail:…` on non-zero (and that
    IS a failed deploy step — `failed_deploy_steps` counts it), `skipped`
    when there is no daemon interpreter; the pass runs with cwd = repo,
    AIASSISTANT_HOME = repo, argv `-m act.actd --once`;
  - the actd-once log lives under ~/Library/Logs/zelin-ai-assistant and is
    capped (防腐 #4);
  - the step-5 dispatch is `run_actd_once` vs `install_launchd_agents`, and
    the crontab branch is skipped with `cron=skipped:--no-launchd…`.
"""
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
_WIN = sys.platform.startswith("win")

FAKE_PY = r"""#!/bin/bash
# stub daemon interpreter: log argv + cwd + AIASSISTANT_HOME, exit per FAKE_ACTD_RC
printf 'argv=%s\ncwd=%s\nhome=%s\n' "$*" "$PWD" "${AIASSISTANT_HOME:-}" >> "$FAKE_ACTD_LOG"
echo "actd fake pass" ; echo "fake stderr line" >&2
exit "${FAKE_ACTD_RC:-0}"
"""


def _install_sh_text():
    return (REPO / "install.sh").read_text(encoding="utf-8")


def _install_sh_fn(name):
    m = re.search(r"^%s\(\) \{.*?^\}" % re.escape(name), _install_sh_text(), flags=re.S | re.M)
    assert m, "install.sh no longer defines %s()" % name
    return m.group(0) + "\n"


@unittest.skipIf(_WIN, "install.sh is POSIX-only")
class FlagParsingTestCase(unittest.TestCase):
    def test_unknown_flag_is_a_usage_error_before_any_side_effect(self):
        tmp = Path(tempfile.mkdtemp(prefix="install-flags-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        proc = subprocess.run(["bash", str(REPO / "install.sh"), "--bogus"],
                              capture_output=True, text=True, timeout=60,
                              env={"HOME": str(tmp), "PATH": "/usr/bin:/bin"})
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("unknown flag '--bogus'", proc.stderr)
        self.assertIn("--no-launchd", proc.stderr)
        self.assertEqual(proc.stdout, "")           # nothing ran
        self.assertEqual(list(tmp.iterdir()), [])   # nothing written under HOME

    def test_flag_loop_recognises_every_documented_flag(self):
        text = _install_sh_text()
        loop = re.search(r'for _arg in "\$@"; do\n(.*?)\ndone', text, flags=re.S).group(1)
        for flag in ("--pkg-postinstall", "--non-interactive", "--no-launchd"):
            self.assertIn(flag + ")", loop)
        self.assertIn('if [ "$NO_LAUNCHD" -eq 1 ]; then\n    run_actd_once\nelse\n    install_launchd_agents\nfi', text)
        self.assertIn('report_step "cron" "skipped" "--no-launchd: crontab not touched"', text)
        # --check forwards the rest of argv to the doctor (bash install.sh --check --fresh-install)
        self.assertIn('exec "$DOCTOR_PY" -m act.doctor "$@"', text)

    def test_auto_deploy_never_passes_no_launchd(self):
        """§56.5: the deploy path proves "running", never the --no-launchd dry run."""
        text = (REPO / "scripts" / "auto-deploy.sh").read_text(encoding="utf-8")
        calls = [ln for ln in text.splitlines()
                 if "install.sh" in ln and "--non-interactive" in ln and not ln.lstrip().startswith("#")]
        self.assertTrue(calls, "auto-deploy.sh no longer runs install.sh --non-interactive")
        for line in calls:
            self.assertNotIn("--no-launchd", line)


@unittest.skipIf(_WIN, "install.sh is POSIX-only")
class RunActdOnceTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="actd-once-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        self.fake_py = self.tmp / "python3"
        self.fake_py.write_text(FAKE_PY, encoding="utf-8")
        self.fake_py.chmod(0o755)
        self.actd_log = self.tmp / "fake-actd.log"

    def _run(self, runtime_py, rc=0, budget="30"):
        script = (
            "set -uo pipefail\n"
            + _install_sh_fn("ui_run_with_timeout") + _install_sh_fn("ui_now")
            + _install_sh_fn("report_step") + _install_sh_fn("failed_deploy_steps")
            + _install_sh_fn("run_actd_once")
            + 'ok()   { printf "  [ ok ] %s\\n" "$1"; }\n'
            + 'warn() { printf "  [warn] %s\\n" "$1"; }\n'
            + 'info() { printf "  [info] %s\\n" "$1"; }\n'
            + 'REPORT_STEPS=""; UI_LOG_CAP_BYTES=1048576\n'
            + 'REPO_ROOT="$1"; RUNTIME_PY="$2"; UI_BUDGET_S="$3"\n'
            + 'ACTD_ONCE_LOG="$HOME/Library/Logs/zelin-ai-assistant/actd-once.log"\n'
            + "run_actd_once\n"
            + 'printf "%s" "$REPORT_STEPS" > "$4"\n'
            + 'failed_deploy_steps > "$5"\n'
        )
        steps_out = self.tmp / "steps.txt"
        failed_out = self.tmp / "failed.txt"
        proc = subprocess.run(
            ["bash", "-c", script, "bash", str(self.repo), runtime_py, budget,
             str(steps_out), str(failed_out)],
            capture_output=True, text=True, timeout=120,
            env={"HOME": str(self.home), "PATH": "/usr/bin:/bin",
                 "FAKE_ACTD_LOG": str(self.actd_log), "FAKE_ACTD_RC": str(rc)})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        steps = dict(line.split("=", 1) for line in steps_out.read_text().splitlines() if line)
        return proc.stdout, steps, failed_out.read_text().splitlines()

    def test_ok_pass_records_launchd_skipped_and_actd_once_ok(self):
        out, steps, failed = self._run(str(self.fake_py), rc=0)
        self.assertTrue(steps["launchd"].startswith("skipped:--no-launchd"), steps)
        self.assertTrue(steps["actd_once"].startswith("ok:one pass in"), steps)
        self.assertIn(str(self.fake_py), steps["actd_once"])
        self.assertEqual(failed, [])
        self.assertIn("[ ok ] actd --once", out)
        log = self.actd_log.read_text()
        self.assertIn("argv=-m act.actd --once", log)
        self.assertIn("cwd=%s" % self.repo.resolve(), log.replace(str(self.repo), str(self.repo.resolve())))
        self.assertIn("home=%s" % self.repo, log)
        once_log = self.home / "Library" / "Logs" / "zelin-ai-assistant" / "actd-once.log"
        self.assertTrue(once_log.is_file())
        self.assertIn("actd fake pass", once_log.read_text())
        self.assertIn("fake stderr line", once_log.read_text())
        self.assertIn("==== install.sh actd --once", once_log.read_text())

    def test_failed_pass_is_a_failed_deploy_step(self):
        out, steps, failed = self._run(str(self.fake_py), rc=3)
        self.assertTrue(steps["actd_once"].startswith("fail:python3 -m act.actd --once exit 3"), steps)
        self.assertEqual(failed, ["actd_once=fail:python3 -m act.actd --once exit 3; see %s"
                                  % (self.home / "Library/Logs/zelin-ai-assistant/actd-once.log")])
        self.assertIn("[warn] actd --once failed (exit 3)", out)
        self.assertIn("fake stderr line", out)   # the log tail is echoed

    def test_no_interpreter_is_skipped_not_failed(self):
        out, steps, failed = self._run("", rc=0)
        self.assertEqual(steps["actd_once"], "skipped:no daemon interpreter")
        self.assertEqual(failed, [])
        self.assertFalse(self.actd_log.exists())
        self.assertIn("[warn] actd --once skipped", out)

    def test_log_is_capped_at_the_shared_cap(self):
        once_log = self.home / "Library" / "Logs" / "zelin-ai-assistant" / "actd-once.log"
        once_log.parent.mkdir(parents=True)
        once_log.write_bytes(b"x" * (1048576 + 4096))
        self._run(str(self.fake_py), rc=0)
        size = once_log.stat().st_size
        self.assertLess(size, 1048576, size)   # newest half kept + this run's section
        self.assertGreater(size, 1048576 // 2)


if __name__ == "__main__":
    unittest.main()
