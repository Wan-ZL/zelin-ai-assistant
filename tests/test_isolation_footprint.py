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
   argv; ingest/claude-env.sh spells the same literals (equality pinned here and in
   tests/test_ingest_claude_isolation.py).
3. **closed footprint** — exactly one LaunchAgent template ships (the board
   server) and it is the only resident label the doctor expects; the ingest
   chain's own launchd plists are gone (the a11y prune is a cleanup step, §72.5);
   the doctor's footprint probe is the one place that names the owner's skills
   dir, and it only lists symlink names and targets.
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
    "scripts/skills_sync.sh": "PR-C (skills/ store retired)",
    "scripts/ui/extract_native_inventory.py": "PR-C (§66 parity tooling retired)",
    "scripts/qa/coverage_run.py": "PR-C (§77 inventory tooling retired)",
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

    def test_shell_side_spells_the_same_literals(self):
        """§3.2 two definitions, one value: claude-env.sh's CLAUDE_CONFIG_DIR default is the
        same path as config.CLAUDE_CONFIG_DIR (both under $HOME, both relocated by the same
        AIASSISTANT_CLAUDE_CONFIG_DIR seam), HEADLESS_CWD is <that>/cwd on both sides, and
        the ISOLATION_ARGV array is the llm tuple element for element."""
        from tests.test_ingest_claude_isolation import shell_isolation_argv
        text = (REPO / "ingest" / "claude-env.sh").read_text(encoding="utf-8")
        m = re.search(r'^CLAUDE_CONFIG_DIR="\$\{AIASSISTANT_CLAUDE_CONFIG_DIR:-\$HOME/([^}"]+)\}"$',
                      text, re.MULTILINE)
        self.assertIsNotNone(m, "claude-env.sh must default CLAUDE_CONFIG_DIR under $HOME")
        self.assertEqual(Path.home() / m.group(1), config.CLAUDE_CONFIG_DIR)
        self.assertEqual(config.HEADLESS_CWD.relative_to(config.CLAUDE_CONFIG_DIR).as_posix(), "cwd")
        self.assertIn('HEADLESS_CWD="$CLAUDE_CONFIG_DIR/cwd"', text)
        self.assertIn("--print-path headless_cwd", text)
        self.assertEqual(shell_isolation_argv(text), llm.ISOLATION_ARGV)

    def test_the_env_seam_has_one_name_on_both_sides(self):
        text = (REPO / "ingest" / "claude-env.sh").read_text(encoding="utf-8")
        src = (REPO / "act" / "lib" / "config.py").read_text(encoding="utf-8")
        self.assertIn('os.environ.get("AIASSISTANT_CLAUDE_CONFIG_DIR"', src)
        self.assertIn("${AIASSISTANT_CLAUDE_CONFIG_DIR:-", text)

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

    def test_the_ingest_chain_ships_no_launchd_plists(self):
        self.assertFalse((REPO / "ingest" / "launchd").exists(),
                         "§72.5: the a11y prune runs inside screenpipe-cleanup.sh")

    def test_the_footprint_probe_is_the_one_place_naming_the_owner_claude_dir(self):
        literal = re.compile(r"""["']\.claude["']""")
        hits = sorted(path.relative_to(REPO).as_posix() for top in ("act", "server", "ingest")
                      for path in (REPO / top).rglob("*.py")
                      if literal.search(path.read_text(encoding="utf-8")))
        self.assertEqual(hits, ["act/lib/checks/launchd.py"])
        src = (REPO / "act" / "lib" / "checks" / "launchd.py").read_text(encoding="utf-8")
        body = src[src.index("def repo_skill_links"):src.index("def _footprint_skill_links")]
        self.assertNotIn("open(", body)
        self.assertNotIn("read_text", body)
        self.assertNotIn("unlink", body)

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
