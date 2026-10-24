"""The ingest chain's headless claude runs on the §78 isolation constants (constitution art.13).

``ingest/process-screenpipe.sh`` is the one headless ``claude -p`` that does not go through
``act/llm.py``; ``ingest/claude-env.sh`` is its half of the constants. Pinned by literal
reading of the two shell scripts (no subprocess here — the real script with a stub claude
runs in tests/integration/test_ingest_smoke.py):

  - claude-env.sh's ``ISOLATION_ARGV`` bash array, parsed with shell quoting rules, is
    element-for-element ``act.llm.ISOLATION_ARGV`` (the empty ``--setting-sources``
    value included — the reason it is an array and not a string);
  - every isolation flag appears literally in claude-env.sh; it exports
    ``CLAUDE_CONFIG_DIR`` (the same Application Support literal as act/lib/config.py,
    with the same ``AIASSISTANT_CLAUDE_CONFIG_DIR`` seam) and ``DISABLE_AUTOUPDATER=1``,
    resolves ``CLAUDE_BIN`` / ``HEADLESS_CWD`` through the config CLI, and reads the key
    files in the §19 order;
  - process-screenpipe.sh sources claude-env.sh, runs claude from ``$HEADLESS_CWD`` with
    ``"${ISOLATION_ARGV[@]}"``, ``--add-dir "$VAULT"``, the vault CLAUDE.md through
    ``--append-system-prompt-file`` and ``--allowedTools Read,Write,Edit,Bash,Glob,Grep``;
    its prompt says "Never run git."; the retired fallback to the CLI's own credentials,
    its own claude lookup and the ``cd "$VAULT"`` cwd are gone.
"""
import re
import shlex
import unittest
from pathlib import Path

from tests import TMP_HOME  # noqa: F401 - sandbox env before any act import

from act import llm
from act.lib import config

REPO = Path(__file__).resolve().parent.parent
ENV_SH = REPO / "ingest" / "claude-env.sh"
PROCESS_SH = REPO / "ingest" / "process-screenpipe.sh"


def _code(path: Path) -> str:
    """The script with full-line comments dropped (a flag named in a comment proves nothing)."""
    return "\n".join(line for line in path.read_text(encoding="utf-8").splitlines()
                     if not line.lstrip().startswith("#"))


def shell_isolation_argv(text: str) -> tuple:
    """The elements of the ``ISOLATION_ARGV=( ... )`` array, split with shell quoting rules."""
    m = re.search(r"^ISOLATION_ARGV=\((.*?)^\)", text, re.MULTILINE | re.DOTALL)
    if m is None:
        raise AssertionError("claude-env.sh defines no ISOLATION_ARGV array")
    return tuple(shlex.split(m.group(1), comments=True, posix=True))


class SharedConstantsTestCase(unittest.TestCase):
    def setUp(self):
        self.env = _code(ENV_SH)

    def test_the_shell_array_equals_the_python_tuple(self):
        self.assertEqual(shell_isolation_argv(self.env), llm.ISOLATION_ARGV)

    def test_the_parser_keeps_the_empty_setting_sources_value(self):
        argv = shell_isolation_argv(self.env)
        self.assertEqual(argv[argv.index("--setting-sources") + 1], "")
        self.assertEqual(argv[argv.index("--mcp-config") + 1], '{"mcpServers":{}}')

    def test_every_isolation_flag_is_spelled_literally(self):
        for flag in llm.ISOLATION_FLAGS:
            self.assertIn(flag, self.env, flag)

    def test_the_config_dir_literal_matches_config_py(self):
        support = config.CLAUDE_CONFIG_DIR.relative_to(Path.home())
        self.assertEqual(support.as_posix(), "Library/Application Support/ZelinAIAssistant/claude-config")
        self.assertIn('CLAUDE_CONFIG_DIR="${AIASSISTANT_CLAUDE_CONFIG_DIR:-$HOME/%s}"' % support.as_posix(),
                      self.env)
        self.assertIn("export CLAUDE_CONFIG_DIR", self.env)
        self.assertIn("export DISABLE_AUTOUPDATER=1", self.env)

    def test_binary_and_cwd_come_from_the_config_layer(self):
        self.assertIn("-m act.lib.config --print-value claude_bin", self.env)
        self.assertIn("-m act.lib.config --print-path headless_cwd", self.env)
        self.assertIn('HEADLESS_CWD="$CLAUDE_CONFIG_DIR/cwd"', self.env)   # the no-python fallback

    def test_the_key_order_is_section_19(self):
        secrets = self.env.index("$AIASSISTANT_HOME/config/secrets/anthropic-api-key.txt")
        legacy = self.env.index("$HOME/.config/anthropic-key.txt")
        self.assertLess(secrets, legacy)
        self.assertIn("export ANTHROPIC_API_KEY", self.env)

    def test_no_owner_claude_home_and_no_oauth_fallback(self):
        self.assertNotIn("/.claude/", self.env)
        self.assertNotIn("keychain", self.env.lower())


class ProcessInvocationTestCase(unittest.TestCase):
    def setUp(self):
        self.text = _code(PROCESS_SH)

    def test_it_sources_the_shared_constants(self):
        self.assertIn('. "$SCRIPT_DIR/claude-env.sh"', self.text)

    def test_claude_runs_from_the_headless_cwd(self):
        self.assertIn('cd "$HEADLESS_CWD"', self.text)
        self.assertNotIn('cd "$VAULT"', self.text)
        self.assertLess(self.text.index('cd "$HEADLESS_CWD"'), self.text.index('"$CLAUDE_BIN" -p'))

    def test_the_invocation_carries_every_flag(self):
        start = self.text.index('"$CLAUDE_BIN" -p')
        call = self.text[start:self.text.index("CLAUDE_PID=$!", start)]
        for literal in ('"${ISOLATION_ARGV[@]}"', '--add-dir "$VAULT"',
                        '--allowedTools "Read,Write,Edit,Bash,Glob,Grep"'):
            self.assertIn(literal, call)
        self.assertIn("VAULT_PROMPT_ARGV", call)
        self.assertIn('--append-system-prompt-file "$VAULT/CLAUDE.md"', self.text)

    def test_the_prompt_forbids_git(self):
        self.assertIn("Never run git.", self.text)

    def test_the_retired_paths_are_gone(self):
        self.assertNotIn("falling back to the claude CLI's own credentials", self.text)
        self.assertNotIn("/opt/homebrew/bin/claude", self.text)
        self.assertNotIn("anthropic-key.txt\" ]", self.text)   # its own key reader retired
        self.assertEqual(self.text.count('"$CLAUDE_BIN" -p'), 1)

    def test_no_key_is_a_loud_failure_not_a_fallback(self):
        self.assertIn('if [ -z "${ANTHROPIC_API_KEY:-}" ]; then', self.text)
        self.assertIn("no Anthropic API key", self.text)


if __name__ == "__main__":
    unittest.main()
