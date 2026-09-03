"""install.sh ``--reinstall-agent <label>`` — the §48.7 single-agent re-render + reload
behind the board's「重新安装」button (server/radars.py).

Runs ``reinstall_agent_mode`` straight out of install.sh (same _install_sh_fn harness as
tests/test_install_skills_step.py) with every launchd / render helper stubbed to a recorder,
in a temp REPO_ROOT holding a fake template. Pinned:
  - no template for the label → exit 2, nothing touched;
  - no pinned interpreter (config/runtime.json) → exit 4, nothing touched — a button press
    never re-runs the §55 probe, an unpinned repo has never been installed;
  - a radar whose source is switched off → exit 3 (§48.5: step 5 would retire it again);
  - happy path → unload → cap log → render (same renderer as step 5) → load → verify → exit 0,
    in that order, with the pinned interpreter as RUNTIME_PY and the login-shell claude resolved;
  - load failure → exit 1 with the failure hint; verify failure → exit 1;
  - the top-level parse: ``--reinstall-agent`` without a label is usage (exit 2) and the flag
    never falls through to the numbered steps.
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

STUBS = r"""
ok()   { printf "  [ ok ] %s\n" "$1"; }
warn() { printf "  [warn] %s\n" "$1"; }
info() { printf "  [info] %s\n" "$1"; }
_rec() { printf '%s\n' "$*" >> "$REC"; }
pinned_python() { printf '%s' "${FAKE_PINNED:-}"; }
py_imports_yaml() { case "${1:-}" in /*) [ "${FAKE_YAML_OK:-1}" = "1" ] ;; *) return 1 ;; esac; }
radar_source_enabled() { _rec "gate $1"; [ "${FAKE_SOURCE_ON:-1}" = "1" ]; }
resolve_claude_login_bin() { CLAUDE_LOGIN_BIN="/fake/claude"; _rec "claude"; }
server_port() { printf '47820'; }
launchd_unload() { _rec "unload $2"; }
cap_launchd_log() { _rec "cap $1"; }
render_launchd_plist() { _rec "render $(basename "$1") -> $2 py=$RUNTIME_PY claude=$CLAUDE_LOGIN_BIN"; : > "$2"; }
launchd_load() { _rec "load $(basename "$1")"; [ "${FAKE_LOAD_OK:-1}" = "1" ]; }
verify_launchd_agent() { _rec "verify $1"; [ "${FAKE_VERIFY_OK:-1}" = "1" ]; }
launchd_failure_hint() { _rec "hint $1"; }
sleep() { :; }
"""


def _install_sh_fn(name):
    text = (REPO / "install.sh").read_text(encoding="utf-8")
    m = re.search(r"^%s\(\) \{.*?^\}" % re.escape(name), text, flags=re.S | re.M)
    assert m, "install.sh no longer defines %s()" % name
    return m.group(0) + "\n"


@unittest.skipIf(_WIN, "install.sh is POSIX-only")
class ReinstallAgentModeTestCase(unittest.TestCase):
    LABEL = "com.zelin.aiassistant.slackradar"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="install-reinstall-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo_root = self.tmp / "repo"
        (self.repo_root / "act" / "launchd").mkdir(parents=True)
        (self.repo_root / "act" / "launchd" / (self.LABEL + ".plist")).write_text("<plist/>", encoding="utf-8")
        self.la_dir = self.tmp / "LaunchAgents"
        self.rec = self.tmp / "calls.log"

    def run_mode(self, label=None, **env_over):
        script = ("set -uo pipefail\n" + STUBS + _install_sh_fn("reinstall_agent_mode")
                  + 'REPO_ROOT="$1"; LA_DIR="$2"; REC="$3"; RUNTIME_PY=""; CLAUDE_LOGIN_BIN=""; SERVER_PORT=""\n'
                  + 'reinstall_agent_mode "$4"\n')
        env = {**os.environ, "FAKE_PINNED": "/pinned/python3"}
        env.update({k: str(v) for k, v in env_over.items()})
        proc = subprocess.run(["bash", "-c", script, "bash", str(self.repo_root), str(self.la_dir),
                               str(self.rec), label or self.LABEL],
                              capture_output=True, text=True, timeout=60, env=env)
        calls = self.rec.read_text(encoding="utf-8").splitlines() if self.rec.exists() else []
        return proc.returncode, proc.stdout + proc.stderr, calls

    def test_happy_path_reuses_step5_helpers_in_order(self):
        rc, out, calls = self.run_mode()
        self.assertEqual(rc, 0, out)
        self.assertEqual(calls, [
            "gate slack", "claude",
            "unload %s" % self.LABEL, "cap %s" % self.LABEL,
            "render %s.plist -> %s/%s.plist py=/pinned/python3 claude=/fake/claude" % (self.LABEL, self.la_dir, self.LABEL),
            "load %s.plist" % self.LABEL, "verify %s" % self.LABEL,
        ])
        self.assertIn("reinstalled %s" % self.LABEL, out)
        self.assertTrue((self.la_dir / (self.LABEL + ".plist")).exists())

    def test_no_template_is_exit_2_and_touches_nothing(self):
        rc, out, calls = self.run_mode(label="com.zelin.aiassistant.nosuch")
        self.assertEqual(rc, 2)
        self.assertIn("no launchd template", out)
        self.assertEqual(calls, [])

    def test_unpinned_interpreter_is_exit_4_and_touches_nothing(self):
        rc, out, calls = self.run_mode(FAKE_PINNED="")
        self.assertEqual(rc, 4)
        self.assertIn("no pinned daemon interpreter", out)
        self.assertEqual(calls, [])
        rc, _out, calls = self.run_mode(FAKE_YAML_OK="0")
        self.assertEqual(rc, 4, "a pinned path that cannot import yaml is no interpreter either")
        self.assertEqual(calls, [])

    def test_switched_off_source_is_exit_3_before_any_launchd_call(self):
        rc, out, calls = self.run_mode(FAKE_SOURCE_ON="0")
        self.assertEqual(rc, 3)
        self.assertIn("switched off", out)
        self.assertEqual(calls, ["gate slack"])

    def test_non_radar_label_skips_the_source_gate(self):
        label = "com.zelin.aiassistant.weeklydigest"
        (self.repo_root / "act" / "launchd" / (label + ".plist")).write_text("<plist/>", encoding="utf-8")
        rc, _out, calls = self.run_mode(label=label, FAKE_SOURCE_ON="0")
        self.assertEqual(rc, 0)
        self.assertNotIn("gate slack", calls)
        self.assertNotIn("gate gmail", calls)

    def test_load_failure_is_exit_1_with_hint(self):
        rc, out, calls = self.run_mode(FAKE_LOAD_OK="0")
        self.assertEqual(rc, 1)
        self.assertIn("failed to load", out)
        self.assertIn("hint slackradar", calls)
        self.assertNotIn("verify %s" % self.LABEL, calls)

    def test_verify_failure_is_exit_1(self):
        rc, _out, calls = self.run_mode(FAKE_VERIFY_OK="0")
        self.assertEqual(rc, 1)
        self.assertEqual(calls[-1], "verify %s" % self.LABEL)


@unittest.skipIf(_WIN, "install.sh is POSIX-only")
class ReinstallAgentParseTestCase(unittest.TestCase):
    """The real install.sh, argument parsing only (both paths exit before step 1)."""

    def _run(self, *args):
        return subprocess.run(["bash", str(REPO / "install.sh"), *args], capture_output=True,
                              text=True, timeout=60, env={**os.environ, "HOME": tempfile.gettempdir()})

    def test_missing_label_is_usage(self):
        proc = self._run("--reinstall-agent")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("usage: bash install.sh --reinstall-agent", proc.stderr)
        self.assertNotIn("Dependency checks", proc.stdout)

    def test_unknown_template_exits_before_step_1(self):
        proc = self._run("--reinstall-agent", "com.zelin.aiassistant.nosuch")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("no launchd template", proc.stderr)
        self.assertNotIn("Dependency checks", proc.stdout)

    def test_usage_string_lists_the_mode(self):
        proc = self._run("--bogus")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("--reinstall-agent <label>", proc.stderr)


if __name__ == "__main__":
    unittest.main()
