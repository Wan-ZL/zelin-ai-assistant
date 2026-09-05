"""§17（issue #23）：重点人物账本的 pass 挂在既有 30 分钟 screenpipe cron 链上。

与 §63 recap 同款（tests/test_recap_cron_hook.py）：无新 daemon、不改 crontab——
``ingest/process-screenpipe.sh`` 在 recap 之后、PID 锁之前跑
``python -m act.people_ledger --once``，失败不断链。钉成文本，防止重构悄悄
把钩子挪到锁后（30 分钟的 ingest 会把每轮账本拖后一轮）。
"""
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "ingest" / "process-screenpipe.sh"


class CronHookTestCase(unittest.TestCase):
    def setUp(self):
        self.text = SCRIPT.read_text(encoding="utf-8")

    def test_people_ledger_once_runs_before_the_pid_lock_and_never_fails_the_chain(self):
        hook = re.search(r'"\$py" -m act\.people_ledger --once[^\n]*\|\| true', self.text)
        self.assertIsNotNone(hook, "act.people_ledger --once hook missing or not failure-proof")
        call = self.text.index("\nrun_people_ledger_once\n")
        lock = self.text.index("# Prevent concurrent runs — PID lock.")
        recap_call = self.text.index("\nrun_recap_once\n")
        self.assertLess(hook.start(), lock)
        self.assertLess(recap_call, call)
        self.assertLess(call, lock)

    def test_hook_uses_the_daemon_interpreter_first(self):
        body = self.text[self.text.index("run_people_ledger_once()"):]
        self.assertIn('config/runtime.json', body)
        self.assertIn('command -v python3', body)

    def test_no_new_daemon_or_crontab_line(self):
        for f in (REPO / "install.sh", REPO / "install-linux.sh"):
            self.assertNotIn("act.people_ledger", f.read_text(encoding="utf-8"), f.name)
        self.assertEqual(sorted(p.name for p in (REPO / "act" / "launchd").glob("*ledger*")), [])


if __name__ == "__main__":
    unittest.main()
