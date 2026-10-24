"""§78 / constitution art.13 自成一块 — the isolation footprint gate (v2.0.0).

The app never reads or writes the owner's own Claude Code home (settings, hooks,
skills, session transcripts, ``~/.claude.json``); every headless ``claude -p``
runs on one set of constants (``CLAUDE_CONFIG_DIR`` / ``HEADLESS_CWD`` /
``ISOLATION_ARGV``); the resident footprint is a closed list. This file is the
precedent art.13 cites. Four pins:

1. **grep gate** — no ``~/.claude``, ``.claude/settings``, ``.claude/skills``,
   ``.claude/projects`` or ``.claude.json`` in act/ server/ ingest/ shell/
   web/src/ scripts/ outside tombstone comments. install.sh is the one file
   allowed to touch ``~/.claude/skills`` (the 1.x migration unlinks the
   symlinks that point into this repo) and is not scanned. The vault's own
   project skill (``$VAULT/.claude/skills/...``, loaded by absolute path) is not
   the owner's Claude Code home and is allowed. Files still carrying a hit
   because their surface retires in a later lite-2.0 slice are listed in
   ``PENDING`` with the slice that removes them — the list is shrink-only: an
   entry whose file no longer has a hit fails until it is struck, and any file
   outside it fails at once.
2. **one set of constants** — act/lib/config.py defines the paths; act/llm.py the
   argv; ingest/claude-env.sh (when it exists) must spell the same literals.
3. **closed footprint** — exactly one LaunchAgent template ships (the board
   server) and it is the only resident label the doctor expects.
4. **the boundary sets the env** — ``llm.runner_env`` always points
   ``CLAUDE_CONFIG_DIR`` at the app's own directory.
"""
import os
import re
import unittest
from pathlib import Path

from tests import TMP_HOME  # noqa: F401 - sandbox env before any act import

from act import llm
from act.lib import config
from act.lib.checks import core

REPO = Path(__file__).resolve().parent.parent
SCANNED = ("act", "server", "ingest", "shell", "web/src", "scripts")
SKIP_PARTS = {"node_modules", "__pycache__", "dist", ".git"}
PATTERN = re.compile(r"~/\.claude(?![-\w])|\.claude/settings|\.claude/skills|\.claude/projects"
                     r"|\.claude\.json")
# the vault's own project skill — read by absolute path, never the owner's ~/.claude
ALLOWED = re.compile(r"\$VAULT/\.claude/skills/")
# a comment that records a retirement may name the retired path
TOMBSTONE = re.compile(r"(#|//|/\*|\*|<!--).*\b(retired|tombstone|退役)\b", re.IGNORECASE)

# file → the lite-2.0 slice that deletes or rewrites it (shrink-only; see docstring)
PENDING = {
    "web/src/types.ts": "PR-B (web trim)",
    "web/src/store.ts": "PR-B (web trim)",
    "web/src/pages/SkillsPage.test.tsx": "PR-B (web trim)",
    "web/src/components/settings/SkillsSection.tsx": "PR-B (web trim)",
    "web/src/components/settings/SkillsSection.test.tsx": "PR-B (web trim)",
    "web/src/components/settings/ModelsSection.test.tsx": "PR-B (web trim)",
    "web/src/components/settings/ModelsSection.fallback.test.tsx": "PR-B (web trim)",
    "web/src/components/settings/McpSection.test.tsx": "PR-B (web trim)",
    "web/src/components/settings/ClaudeImportSection.tsx": "PR-B (web trim)",
    "web/src/components/settings/ClaudeCodeDefaultRow.tsx": "PR-B (web trim)",
}


def _files():
    for top in SCANNED:
        root = REPO / top
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_file() and not (SKIP_PARTS & set(path.relative_to(REPO).parts)):
                yield path


def _hits(path: Path) -> list:
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return []
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        if not PATTERN.search(line):
            continue
        if ALLOWED.search(line) and not PATTERN.search(ALLOWED.sub("", line)):
            continue
        if TOMBSTONE.search(line):
            continue
        out.append((n, line.strip()[:140]))
    return out


class GrepGateTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hits = {}
        for path in _files():
            found = _hits(path)
            if found:
                cls.hits[path.relative_to(REPO).as_posix()] = found

    def test_no_file_outside_the_pending_list_names_the_owners_claude_home(self):
        stray = {rel: lines for rel, lines in self.hits.items() if rel not in PENDING}
        self.assertEqual(stray, {}, "§78: these files read or name the owner's Claude Code home")

    def test_the_pending_list_only_shrinks(self):
        stale = sorted(rel for rel in PENDING if rel not in self.hits)
        self.assertEqual(stale, [], "strike these PENDING entries — their hits are gone")

    def test_act_server_and_shell_are_already_clean(self):
        for rel in self.hits:
            self.assertFalse(rel.startswith(("act/", "server/", "shell/")), rel)

    def test_install_sh_is_the_only_unscanned_exception(self):
        self.assertTrue((REPO / "install.sh").is_file())
        self.assertNotIn("install.sh", PENDING)

    def test_the_gate_sees_what_it_should(self):
        self.assertTrue(PATTERN.search('Path("~/.claude/settings.json")'))
        self.assertTrue(PATTERN.search("open ~/.claude.json"))
        self.assertTrue(PATTERN.search("x/.claude/projects/y"))
        self.assertFalse(PATTERN.search("~/.claude-bin"))
        self.assertFalse(PATTERN.search("claude-config/cwd"))
        self.assertTrue(TOMBSTONE.search("# retired v2.0.0: the ~/.claude/settings.json writer"))


class ConstantsTestCase(unittest.TestCase):
    """One set of constants (§78): defined once in Python, mirrored by the shell side."""

    def test_python_side(self):
        support = Path.home() / "Library" / "Application Support" / "ZelinAIAssistant"
        self.assertEqual(config.APP_SUPPORT_DIR, support)
        self.assertEqual(config.CLAUDE_CONFIG_DIR, support / "claude-config")
        self.assertEqual(config.HEADLESS_CWD, config.CLAUDE_CONFIG_DIR / "cwd")
        self.assertEqual(llm.ISOLATION_ARGV, ("--bare", "--no-session-persistence",
                                              "--setting-sources", "", "--strict-mcp-config",
                                              "--mcp-config", '{"mcpServers":{}}'))
        self.assertEqual(llm.CONFIG_DIR_ENV, "CLAUDE_CONFIG_DIR")

    def test_shell_side_spells_the_same_literals_when_it_exists(self):
        # TODO(PR-D): ingest/claude-env.sh lands with the ingest rewrite; once it
        # exists this branch is the equality pin the plan names (§3.2).
        env_sh = REPO / "ingest" / "claude-env.sh"
        if not env_sh.is_file():
            self.skipTest("ingest/claude-env.sh lands in PR-D")
        text = env_sh.read_text(encoding="utf-8")
        self.assertIn("ZelinAIAssistant/claude-config", text)
        self.assertIn("--print-path headless_cwd", text)
        for flag in llm.ISOLATION_FLAGS:
            self.assertIn(flag, text)

    def test_the_cli_exposes_both_paths_for_the_shell(self):
        self.assertIn("claude_config_dir", config._CLI_PATH_KEYS)
        self.assertIn("headless_cwd", config._CLI_PATH_KEYS)
        self.assertIn("claude_bin", config._CLI_VALUE_KEYS)


class ClosedFootprintTestCase(unittest.TestCase):
    """§78 closed list: one LaunchAgent (the board server); the doctor expects exactly it."""

    def test_one_launch_agent_template(self):
        templates = sorted(p.stem for p in (REPO / "act" / "launchd").glob("*.plist"))
        self.assertEqual(templates, ["com.zelin.aiassistant.server"])
        self.assertEqual(core.RESIDENT_LABELS, frozenset({"com.zelin.aiassistant.server"}))

    def test_no_systemd_or_task_scheduler_templates(self):
        for gone in ("act/systemd", "act/tasksched", "act/approve"):
            self.assertFalse((REPO / gone).exists(), gone)


class BoundaryEnvTestCase(unittest.TestCase):
    def test_runner_env_overrides_an_inherited_config_dir(self):
        old = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["CLAUDE_CONFIG_DIR"] = str(Path.home() / ".claude")
        try:
            env = llm.runner_env(config.Config())
        finally:
            if old is None:
                os.environ.pop("CLAUDE_CONFIG_DIR", None)
            else:
                os.environ["CLAUDE_CONFIG_DIR"] = old
        self.assertEqual(env["CLAUDE_CONFIG_DIR"], str(config.claude_config_dir()))
        self.assertNotEqual(Path(env["CLAUDE_CONFIG_DIR"]), Path.home() / ".claude")


if __name__ == "__main__":
    unittest.main()
