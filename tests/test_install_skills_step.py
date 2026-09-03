"""install.sh `install_skills` — the §23 `skills` step (CONTRACT §67; §56.5 spirit).

Runs the function text straight out of install.sh (same _install_sh_fn harness as
tests/test_install_cron_tcc.py) against a stub scripts/skills_sync.sh in a temp
REPO_ROOT; never the real script, never ~/.claude. Pinned:
  - sync exit 0 → `skills=ok:<summary line>`, the daemon interpreter is handed
    down as $AIASSISTANT_PYTHON (the §55 TCC-viable one, not PATH's python3);
  - exit 3 (manifest broken) → `skills=fail:manifest broken — <stderr tail>` and
    it IS a deploy-failure step (the repo is broken, same class as a build break);
  - any other non-zero (store refused / unwritable home) → `skills=warn:exit N — <why>`,
    NOT a deploy-failure step (environment problems never roll a deploy back);
  - no RUNTIME_PY → `skills=skipped:no daemon python`; script missing → skipped;
  - the function never aborts install.sh (returns 0 on every path).
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

FAKE_SYNC = r"""#!/bin/bash
# stub scripts/skills_sync.sh: record the interpreter handed down, act per mode
printf 'py=%s\n' "${AIASSISTANT_PYTHON:-unset}" >> "$FAKE_SYNC_LOG"
case "${FAKE_SYNC_MODE:-ok}" in
    ok)       echo "enabled 2 (board-agent, test-code) · actions: test-code=enabled_default"; exit 0 ;;
    manifest) echo "skills: manifest error: skill 'x': skills/x/SKILL.md does not exist" >&2; exit 3 ;;
    refused)  echo "skills: SKILL_CUSTOM_KEEP: /h/.claude/skills/x is a local custom copy" >&2; exit 4 ;;
    crash)    echo "Traceback (most recent call last):" >&2; echo "PermissionError: [Errno 1] Operation not permitted" >&2; exit 1 ;;
esac
"""


def _install_sh_fn(name):
    text = (REPO / "install.sh").read_text(encoding="utf-8")
    m = re.search(r"^%s\(\) \{.*?^\}" % re.escape(name), text, flags=re.S | re.M)
    assert m, "install.sh no longer defines %s()" % name
    return m.group(0) + "\n"


@unittest.skipIf(_WIN, "install.sh is POSIX-only")
class InstallSkillsStepTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="install-skills-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo_root = self.tmp / "repo"
        (self.repo_root / "scripts").mkdir(parents=True)
        stub = self.repo_root / "scripts" / "skills_sync.sh"
        stub.write_text(FAKE_SYNC, encoding="utf-8")
        stub.chmod(0o755)
        self.log = self.tmp / "sync.log"

    def run_step(self, mode="ok", runtime_py="/pinned/python3", drop_script=False):
        if drop_script:
            (self.repo_root / "scripts" / "skills_sync.sh").unlink()
        script = ("set -uo pipefail\n"
                  + _install_sh_fn("report_step")
                  + _install_sh_fn("failed_deploy_steps")
                  + _install_sh_fn("install_skills")
                  + 'ok()   { printf "  [ ok ] %s\\n" "$1"; }\n'
                    'warn() { printf "  [warn] %s\\n" "$1"; }\n'
                    'info() { printf "  [info] %s\\n" "$1"; }\n'
                    'REPORT_STEPS=""\n'
                    'REPO_ROOT="$1"; RUNTIME_PY="$2"\n'
                    'install_skills; echo "rc=$?"\n'
                    'printf "===REPORT===\\n%s" "$REPORT_STEPS"\n'
                    'printf "===FAILED===\\n"\n'
                    'failed_deploy_steps\n')
        env = {**os.environ, "FAKE_SYNC_MODE": mode, "FAKE_SYNC_LOG": str(self.log),
               "AIASSISTANT_PYTHON": "/should/be/overridden"}
        proc = subprocess.run(["bash", "-c", script, "bash", str(self.repo_root), runtime_py],
                              capture_output=True, text=True, timeout=60, env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out, _, rest = proc.stdout.partition("===REPORT===\n")
        report, _, failed = rest.partition("===FAILED===\n")
        self.assertIn("rc=0", out, "install_skills must never abort install.sh")
        return out, report.splitlines(), [ln for ln in failed.splitlines() if ln]

    def sync_calls(self):
        return self.log.read_text(encoding="utf-8").splitlines() if self.log.exists() else []

    def test_ok_records_summary_and_hands_down_the_daemon_python(self):
        out, report, failed = self.run_step()
        self.assertEqual(report, ["skills=ok:enabled 2 (board-agent, test-code) · actions: test-code=enabled_default"])
        self.assertEqual(failed, [])
        self.assertIn("[ ok ] skills:", out)
        self.assertEqual(self.sync_calls(), ["py=/pinned/python3"])

    def test_broken_manifest_is_a_deploy_failure(self):
        _out, report, failed = self.run_step(mode="manifest")
        self.assertEqual(len(report), 1)
        self.assertTrue(report[0].startswith("skills=fail:manifest broken — "), report)
        self.assertIn("SKILL.md does not exist", report[0])
        self.assertEqual(failed, report)

    def test_refusal_and_crash_are_warn_not_failure(self):
        out, report, failed = self.run_step(mode="refused")
        self.assertEqual(report, ["skills=warn:exit 4 — skills: SKILL_CUSTOM_KEEP: /h/.claude/skills/x is a local custom copy"])
        self.assertEqual(failed, [])
        self.assertIn("[warn]", out)
        out, report, failed = self.run_step(mode="crash")
        self.assertEqual(report, ["skills=warn:exit 1 — PermissionError: [Errno 1] Operation not permitted"])
        self.assertEqual(failed, [], "an environment problem never rolls a deploy back")

    def test_no_daemon_python_skips(self):
        _out, report, failed = self.run_step(runtime_py="")
        self.assertEqual(report, ["skills=skipped:no daemon python"])
        self.assertEqual(failed, [])
        self.assertEqual(self.sync_calls(), [], "the script is not even called")

    def test_missing_script_skips(self):
        _out, report, failed = self.run_step(drop_script=True)
        self.assertEqual(report, ["skills=skipped:scripts/skills_sync.sh missing"])
        self.assertEqual(failed, [])

    def test_install_sh_calls_the_step_between_state_dirs_and_the_mac_app(self):
        text = (REPO / "install.sh").read_text(encoding="utf-8")
        self.assertLess(text.index('report_step "state_dirs" "ok"'), text.index("\ninstall_skills\n"))
        self.assertLess(text.index("\ninstall_skills\n"), text.index("\ninstall_mac_app\n"))
