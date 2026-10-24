"""Smoke test for the ingest shell chain's entry point with a stubbed `claude` (issue #16).

Runs the REAL ``ingest/process-screenpipe.sh`` (CONTRACT §18) the way cron does —
against a sandboxed HOME (fake vault) + sandboxed AIASSISTANT_HOME — with a stub
``claude`` prepended to PATH that records its argv + environment, "processes" the
inbox the way the /unprocessed-ingest skill would (moves the note to ``2 - raw``),
and never touches the network. Pinned behavior:

  - §78 isolation (constitution art.13): claude runs from the headless cwd (an
    empty dir under the sandboxed CLAUDE_CONFIG_DIR, outside the repo tree) with
    ``CLAUDE_CONFIG_DIR`` exported (an inherited value is overridden),
    ``DISABLE_AUTOUPDATER=1`` and the argv ``-p <prompt> *ISOLATION_ARGV --add-dir
    <vault> [--append-system-prompt-file <vault>/CLAUDE.md] --allowedTools
    Read,Edit,Bash`` (element for element ``act.llm.ISOLATION_ARGV``,
    the empty ``--setting-sources`` value included); the prompt names the inbox, the
    SKILL.md (the repo's copy when the vault has none) and says "Never run git.";
  - key file present (``config/secrets/anthropic-api-key.txt`` first, legacy
    ``~/.config/anthropic-key.txt`` second) → ``ANTHROPIC_API_KEY`` exported to
    claude; no key at all → exit 1 with a line saying so and claude never launched
    (``--bare`` reads no OAuth / keychain, so the 1.x CLI-credential fallback retired);
  - success → exit 0, "✅ Processing complete", the note left the inbox;
  - claude non-zero → the same exit code propagates + "❌ Processing failed";
  - inbox dir missing (vault gone / unconfigured) → exit 1 with a line naming
    the path, claude never launched (was: exit 0 "No ingestable files");
  - another live run holding the PID lock → exit 3, claude never launched.

Hermetic: lock + log go through the PROCESS_SCREENPIPE_LOCK / PROCESS_SCREENPIPE_LOG
seams (never the machine's /tmp files), SCREENPIPE_NO_WAIT=1 skips the 90 s pad,
vault-sync stays in direct mode (no mode file under the tmp AIASSISTANT_HOME).
Lives in tests/integration/ (防腐 #7：真子进程只许住这里，单文件时间预算)。
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "ingest" / "process-screenpipe.sh"
REPO_SKILL = REPO / "ingest" / "skills" / "unprocessed-ingest" / "SKILL.md"
BUDGET_SECONDS = 60
_T0 = [time.monotonic()]

# The stub: record argv (one per line) + whether ANTHROPIC_API_KEY reached us,
# then act like the skill did its job (inbox → 2 - raw). No network, ever.
STUB_CLAUDE = r"""#!/bin/sh
printf '%s\n' "$@" > "$STUB_ARGV"
if [ -n "${ANTHROPIC_API_KEY+x}" ]; then
    printf 'set:%s\n' "$ANTHROPIC_API_KEY" > "$STUB_KEY"
else
    printf 'unset\n' > "$STUB_KEY"
fi
printf 'cwd=%s\nconfig=%s\nautoupdater=%s\n' "$(pwd -P)" "${CLAUDE_CONFIG_DIR:-}" \
    "${DISABLE_AUTOUPDATER:-}" > "$STUB_ENV"
if [ -n "${STUB_INBOX:-}" ] && [ -d "$STUB_INBOX" ]; then
    for f in "$STUB_INBOX"/*; do
        [ -f "$f" ] && mv "$f" "$STUB_RAW"/
    done
fi
echo "stub claude: ingest done"
exit "${STUB_EXIT:-0}"
"""

# act.llm.ISOLATION_ARGV, spelled here so this file never imports act (the tuple
# equality itself is pinned by tests/test_ingest_claude_isolation.py)
ISOLATION_ARGV = ["--bare", "--no-session-persistence", "--setting-sources", "",
                  "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}']


def setUpModule():
    _T0[0] = time.monotonic()


def tearDownModule():
    elapsed = time.monotonic() - _T0[0]
    if elapsed > BUDGET_SECONDS:
        raise AssertionError("tests/integration/test_ingest_smoke.py took %.0fs > %ds budget"
                             % (elapsed, BUDGET_SECONDS))


@unittest.skipIf(sys.platform.startswith("win"), "bash scripts are POSIX-only")
class IngestSmokeTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="ingest-smoke-")
        base = Path(self.tmp.name)
        self.home = base / "home"
        self.ai_home = base / "aihome"
        self.bin = base / "bin"
        for d in (self.home, self.ai_home, self.bin):
            d.mkdir()
        # config-layer default == shell fallback: $HOME/Documents/Obsidian Vault
        self.vault = self.home / "Documents" / "Obsidian Vault"
        self.inbox = self.vault / "1 - unprocessed"
        self.raw = self.vault / "2 - raw"
        self.inbox.mkdir(parents=True)
        self.raw.mkdir()
        (self.inbox / "meeting.md").write_text("# fake meeting\nhello\n", encoding="utf-8")
        (self.inbox / ".DS_Store").write_bytes(b"")
        stub = self.bin / "claude"
        stub.write_text(STUB_CLAUDE, encoding="utf-8")
        stub.chmod(0o755)
        self.argv_log = base / "claude.argv"
        self.key_log = base / "claude.key"
        self.env_log = base / "claude.env"
        self.claude_config = base / "claude-config"
        self.lock = base / "process.lock"
        self.log = base / "auto.log"

    def tearDown(self):
        self.tmp.cleanup()

    def _env(self, **extra):
        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        env.update({
            "HOME": str(self.home),
            "AIASSISTANT_HOME": str(self.ai_home),
            "PATH": f"{self.bin}:{env.get('PATH', '')}",
            "SCREENPIPE_NO_WAIT": "1",
            "CLAUDE_MAX_SECONDS": "30",
            "PROCESS_SCREENPIPE_LOCK": str(self.lock),
            "PROCESS_SCREENPIPE_LOG": str(self.log),
            "STUB_ARGV": str(self.argv_log),
            "STUB_KEY": str(self.key_log),
            "STUB_ENV": str(self.env_log),
            "AIASSISTANT_CLAUDE_CONFIG_DIR": str(self.claude_config),
            # a cron started from inside the owner's own Claude Code session inherits this
            "CLAUDE_CONFIG_DIR": str(self.home / ".claude"),
            "STUB_INBOX": str(self.inbox),
            "STUB_RAW": str(self.raw),
        })
        env.update(extra)
        return env

    def _run(self, **extra):
        return subprocess.run(["bash", str(SCRIPT)], env=self._env(**extra),
                              capture_output=True, text=True, timeout=50)

    def _log(self):
        return self.log.read_text(encoding="utf-8") if self.log.exists() else ""

    def _argv(self):
        return self.argv_log.read_text(encoding="utf-8").rstrip("\n").split("\n")

    def _stub_env(self):
        return dict(line.split("=", 1) for line in
                    self.env_log.read_text(encoding="utf-8").splitlines())

    def _assert_claude_flags(self, vault_claude_md=False):
        argv = self._argv()
        self.assertEqual(argv[0], "-p")
        prompt = argv[1]
        self.assertIn(str(self.inbox), prompt)
        # vault has no skill copy → the repo's SKILL.md is the fallback
        self.assertIn(str(REPO_SKILL), prompt)
        self.assertIn("Never run git.", prompt)
        tail = ["--add-dir", str(self.vault)]
        if vault_claude_md:
            tail += ["--append-system-prompt-file", str(self.vault / "CLAUDE.md")]
        tail += ["--allowedTools", "Read,Edit,Bash"]
        self.assertEqual(argv[2:], ISOLATION_ARGV + tail)
        env = self._stub_env()
        cwd = Path(env["cwd"])
        self.assertEqual(cwd, (self.claude_config / "cwd").resolve())
        self.assertEqual(list(cwd.iterdir()), [], "the headless cwd stays empty")
        self.assertNotIn(REPO.resolve(), (cwd, *cwd.parents))
        self.assertEqual(env["config"], str(self.claude_config))
        self.assertEqual(env["autoupdater"], "1")
        self.assertFalse((self.home / ".claude").exists(), "the owner's Claude Code home untouched")

    def test_key_file_present_exports_the_key_and_processes(self):
        secrets = self.ai_home / "config" / "secrets"
        secrets.mkdir(parents=True)
        (secrets / "anthropic-api-key.txt").write_text("sk-ant-test-0000\n", encoding="utf-8")
        proc = self._run()
        self.assertEqual(proc.returncode, 0, proc.stderr + self._log())
        self._assert_claude_flags()
        self.assertEqual(self.key_log.read_text(encoding="utf-8").strip(), "set:sk-ant-test-0000")
        self.assertNotIn("no API key file", self._log())
        self.assertIn("✅ Processing complete (exit 0)", self._log())
        self.assertFalse((self.inbox / "meeting.md").exists(), "the note left the inbox")
        self.assertTrue((self.raw / "meeting.md").exists())
        self.assertFalse(self.lock.exists(), "lock released after the run")

    def test_legacy_key_path_is_the_second_choice(self):
        cfg = self.home / ".config"
        cfg.mkdir()
        (cfg / "anthropic-key.txt").write_text("sk-ant-legacy-1111\n", encoding="utf-8")
        proc = self._run()
        self.assertEqual(proc.returncode, 0, proc.stderr + self._log())
        self._assert_claude_flags()
        self.assertEqual(self.key_log.read_text(encoding="utf-8").strip(), "set:sk-ant-legacy-1111")

    def test_no_key_fails_loudly_without_launching_claude(self):
        proc = self._run()
        self.assertEqual(proc.returncode, 1, proc.stderr + self._log())
        self.assertFalse(self.argv_log.exists(), "claude must not launch without a key")
        self.assertIn("❌ no Anthropic API key", self._log())
        self.assertNotIn("falling back to the claude CLI's own credentials", self._log())
        self.assertTrue((self.inbox / "meeting.md").exists(), "nothing consumed")

    def test_a_key_already_in_the_environment_is_used(self):
        proc = self._run(ANTHROPIC_API_KEY="sk-ant-env-2222")
        self.assertEqual(proc.returncode, 0, proc.stderr + self._log())
        self.assertEqual(self.key_log.read_text(encoding="utf-8").strip(), "set:sk-ant-env-2222")

    def test_the_vault_claude_md_is_appended_when_present(self):
        (self.vault / "CLAUDE.md").write_text("# vault conventions\n", encoding="utf-8")
        self._key()
        proc = self._run()
        self.assertEqual(proc.returncode, 0, proc.stderr + self._log())
        self._assert_claude_flags(vault_claude_md=True)

    def _key(self):
        secrets = self.ai_home / "config" / "secrets"
        secrets.mkdir(parents=True, exist_ok=True)
        (secrets / "anthropic-api-key.txt").write_text("sk-ant-test-0000\n", encoding="utf-8")

    def test_claude_failure_propagates_exit_code(self):
        self._key()
        proc = self._run(STUB_EXIT="2")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("❌ Processing failed (exit 2)", self._log())

    def test_missing_inbox_fails_loudly_without_launching_claude(self):
        shutil.rmtree(self.vault)
        proc = self._run()
        self.assertEqual(proc.returncode, 1)
        self.assertFalse(self.argv_log.exists(), "claude must not launch on a missing vault")
        log = self._log()
        self.assertIn("❌ inbox not readable", log)
        self.assertIn(str(self.inbox), log)
        self.assertNotIn("No ingestable files found", log)

    def test_empty_inbox_is_a_quiet_success(self):
        (self.inbox / "meeting.md").unlink()
        proc = self._run()
        self.assertEqual(proc.returncode, 0, proc.stderr + self._log())
        self.assertFalse(self.argv_log.exists())
        self.assertIn("No ingestable files found", self._log())

    def test_live_lock_holder_yields_exit_3(self):
        # a live process whose command line matches the chain's liveness grep
        holder = subprocess.Popen(
            ["bash", "-c", "exec -a process-screenpipe-holder sleep 30"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            self.lock.write_text(f"{holder.pid}\n", encoding="utf-8")
            proc = self._run()
            self.assertEqual(proc.returncode, 3)
            self.assertFalse(self.argv_log.exists())
            self.assertIn("Skipped — already running", self._log())
            self.assertTrue(self.lock.exists(), "the holder keeps its lock")
        finally:
            holder.kill()
            holder.wait(timeout=10)


if __name__ == "__main__":
    unittest.main()
