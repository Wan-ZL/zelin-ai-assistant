"""§63: the recap round hangs off the EXISTING 30-minute screenpipe cron chain.

Owner (issue #129): no new daemon, no manual trigger, no crontab change —
``ingest/process-screenpipe.sh`` runs ``python -m act.recap --once`` before its
own PID lock, and a failing recap must never break the ingest chain. Pinned
as text so a refactor of the script cannot silently drop the hook or move it
behind the lock (a 30-minute ingest would then delay every recap by a round).
"""
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "ingest" / "process-screenpipe.sh"


class CronHookTestCase(unittest.TestCase):
    def setUp(self):
        self.text = SCRIPT.read_text(encoding="utf-8")

    def test_recap_once_runs_before_the_pid_lock_and_never_fails_the_chain(self):
        hook = re.search(r'"\$py" -m act\.recap --once[^\n]*\|\| true', self.text)
        self.assertIsNotNone(hook, "act.recap --once hook missing or not failure-proof")
        call = self.text.index("\nrun_recap_once\n")
        lock = self.text.index("# Prevent concurrent runs — PID lock.")
        self.assertLess(hook.start(), lock)
        self.assertLess(call, lock)

    def test_hook_uses_the_daemon_interpreter_first(self):
        # same resolution as resolve_config_path: config/runtime.json python → PATH python3
        self.assertIn('config/runtime.json', self.text[self.text.index("run_recap_once()"):])
        self.assertIn('command -v python3', self.text[self.text.index("run_recap_once()"):])

    def test_no_new_daemon_or_crontab_line(self):
        for f in (REPO / "install.sh", REPO / "install-linux.sh"):
            self.assertNotIn("act.recap", f.read_text(encoding="utf-8"), f.name)
        self.assertEqual(sorted(p.name for p in (REPO / "act" / "launchd").glob("*recap*")), [])


if __name__ == "__main__":
    unittest.main()
