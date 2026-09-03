"""Dispatched agents see the store's skills (CONTRACT §67 / R2.7.4) + skills_sync.sh
end to end — real git, real bash, a FAKE claude (tests/integration/, 防腐 #7).

Mechanism under test — the two places Claude Code documents it reads skills from:
  1. **project**: `.claude/skills/<name>` of the working directory and its parents,
     following symlinks. The repo TRACKS `.claude/skills/<name>` → `../../skills/<name>`
     (relative), so every checkout and every `git worktree` — including the
     `<repo>/.claude/worktrees/<name>` a `claude --bg` agent isolates into — carries
     the default_enabled skills with no per-machine step;
  2. **personal**: `~/.claude/skills/<name>` — where the store's enable puts a symlink.

The fixture repo is a real git repo carrying the real skills/ + .claude/skills of
this checkout and a minimal act/ (config + version + skills). `claude-fake` (a
python script, deliberately NOT named `claude` — tests/__init__.py bans a
`claude --bg <prompt>` shape) implements the documented discovery: personal wins
on a name clash, same target loaded once. It is launched through the REAL
executor._default_runner with cwd = the worktree, so the argv/cwd/env the
product hands an agent is what is exercised. Then scripts/skills_sync.sh runs for
real against the fixture (HOME → tempdir): defaults linked, decisions respected,
exit 3 on a broken manifest.

Honesty note: this proves our filesystem contract against Claude Code's documented
discovery rules, not Claude Code itself (a real `claude` is never spawned in tests).
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first

from act import executor
from act.lib import config, skills

REPO = Path(__file__).resolve().parents[2]
_WIN = sys.platform.startswith("win")
BUDGET_SECONDS = 60
_T0 = [time.monotonic()]

ACT_FILES = ("act/__init__.py", "act/lib/__init__.py", "act/lib/version.py",
             "act/lib/config.py", "act/lib/skills.py", "scripts/skills_sync.sh")

FAKE_CLAUDE = r'''#!/usr/bin/env python3
"""claude-fake: the skill discovery Claude Code documents, nothing else.
argv shape from executor._default_runner: --bg [--dangerously-skip-permissions] [--model X] --name N <prompt>.
Prints the session line the executor parses; writes what it saw to $FAKE_CLAUDE_OUT."""
import json, os, sys

def skills_in(root):
    d = os.path.join(root, ".claude", "skills")
    out = {}
    if not os.path.isdir(d):
        return out
    for name in sorted(os.listdir(d)):
        md = os.path.join(d, name, "SKILL.md")
        if os.path.isfile(md):          # follows symlinks
            out[name] = os.path.realpath(os.path.dirname(md))
    return out

def git_root(start):
    cur = os.path.abspath(start)
    while True:
        if os.path.exists(os.path.join(cur, ".git")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            return os.path.abspath(start)
        cur = parent

argv = sys.argv[1:]
assert "--bg" in argv, argv
cwd = os.getcwd()
seen = {}
# project: cwd and every parent up to the repository root
cur, root = cwd, git_root(cwd)
while True:
    for name, real in skills_in(cur).items():
        seen.setdefault(name, {"source": "project", "dir": cur, "real": real})
    if cur == root:
        break
    cur = os.path.dirname(cur)
# personal overrides project on a name clash; same target is loaded once
for name, real in skills_in(os.path.expanduser("~")).items():
    seen[name] = {"source": "personal", "dir": os.path.expanduser("~"), "real": real}
with open(os.environ["FAKE_CLAUDE_OUT"], "w") as fh:
    json.dump({"cwd": cwd, "argv": argv, "skills": seen}, fh)
print("claude-fake: session id: 0123456789abcdef0123456789abcdef")
'''


def setUpModule():
    _T0[0] = time.monotonic()


def tearDownModule():
    elapsed = time.monotonic() - _T0[0]
    if elapsed > BUDGET_SECONDS:
        raise AssertionError("tests/integration/test_skills_agent_visibility.py took %.0fs > %ds"
                             % (elapsed, BUDGET_SECONDS))


def _yaml_parent() -> str:
    import yaml
    return str(Path(yaml.__file__).resolve().parents[1])


def _git(cwd, *args):
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
         "-c", "commit.gpgsign=false", *args],
        cwd=str(cwd), capture_output=True, text=True, timeout=60, check=True).stdout.strip()


def build_fixture_repo(root: Path) -> Path:
    root.mkdir(parents=True)
    shutil.copytree(str(REPO / "skills"), str(root / "skills"), symlinks=True)
    shutil.copytree(str(REPO / ".claude" / "skills"), str(root / ".claude" / "skills"), symlinks=True)
    for rel in ACT_FILES:
        dst = root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(REPO / rel), str(dst))
    (root / ".gitignore").write_text("state/\n__pycache__/\n", encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "fixture store")
    return root


@unittest.skipIf(_WIN, "git symlinks + bash are POSIX here")
class AgentVisibilityTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="skills-agent-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = build_fixture_repo(self.tmp / "repo")
        self.user_home = self.tmp / "user"
        (self.user_home / ".claude").mkdir(parents=True)
        env = mock.patch.dict(os.environ, {"HOME": str(self.user_home), "USERPROFILE": str(self.user_home)})
        env.start()
        self.addCleanup(env.stop)
        # the worktree a claude --bg agent isolates into (same shape: <repo>/.claude/worktrees/<name>)
        self.worktree = self.repo / ".claude" / "worktrees" / "R-1 agent"
        _git(self.repo, "worktree", "add", "-q", "-b", "agent-branch", str(self.worktree))

    def test_tracked_links_resolve_inside_the_worktree(self):
        for name in ("board-agent", "test-code"):
            link = self.worktree / ".claude" / "skills" / name
            self.assertTrue(link.is_symlink(), name)
            self.assertEqual(os.readlink(str(link)), os.path.join("..", "..", "skills", name))
            self.assertTrue((link / "SKILL.md").is_file(), "resolves to the WORKTREE's own copy")
            self.assertEqual((link / "SKILL.md").resolve(), (self.worktree / "skills" / name / "SKILL.md").resolve())
        self.assertFalse((self.worktree / ".claude" / "skills" / "write-better").exists(),
                         "default_enabled: false skills are not project-visible")

    def test_executor_launch_in_the_worktree_sees_project_and_personal_skills(self):
        # the store enables write-better personally (as Settings → Skills would)
        store = skills.Store(repo_root=self.repo, claude_home=self.user_home / ".claude",
                             state_dir=self.tmp / "state")
        store.enable("write-better")
        # a personal test-code that points at the MAIN checkout (the owner's machine today)
        os.symlink(str(self.repo / "skills" / "test-code"), str(self.user_home / ".claude" / "skills" / "test-code"))

        fake = self.tmp / "bin" / "claude-fake"
        fake.parent.mkdir()
        fake.write_text(FAKE_CLAUDE, encoding="utf-8")
        fake.chmod(0o755)
        out_path = self.tmp / "seen.json"
        cfg = config.Config()
        cfg.claude_bin = str(fake)
        with mock.patch.dict(os.environ, {"FAKE_CLAUDE_OUT": str(out_path)}):
            proc = executor._default_runner("do the card", cwd=self.worktree, name="R-1 · card", cfg=cfg)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(executor._parse_session_id(proc.stdout), "0123456789abcdef0123456789abcdef")

        seen = json.loads(out_path.read_text(encoding="utf-8"))
        self.assertEqual(Path(seen["cwd"]).resolve(), self.worktree.resolve())
        self.assertEqual(seen["argv"][0], "--bg")
        self.assertNotIn("--add-dir", seen["argv"], "no argv change: the tracked links carry the skills")
        skills_seen = seen["skills"]
        self.assertEqual(sorted(skills_seen), ["board-agent", "test-code", "write-better"])
        self.assertEqual(skills_seen["board-agent"]["source"], "project")
        self.assertEqual(Path(skills_seen["board-agent"]["real"]),
                         (self.worktree / "skills" / "board-agent").resolve(),
                         "the agent reads the worktree's own copy of a project skill")
        self.assertEqual(skills_seen["test-code"]["source"], "personal",
                         "personal overrides project on a name clash (documented precedence)")
        self.assertEqual(skills_seen["write-better"]["source"], "personal")
        self.assertEqual(Path(skills_seen["write-better"]["real"]),
                         (self.repo / "skills" / "write-better").resolve())


@unittest.skipIf(_WIN, "git symlinks + bash are POSIX here")
class SkillsSyncScriptTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="skills-sync-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = build_fixture_repo(self.tmp / "repo")
        self.user_home = self.tmp / "user"
        self.user_home.mkdir()
        self.env = {**os.environ, "HOME": str(self.user_home), "USERPROFILE": str(self.user_home),
                    "AIASSISTANT_PYTHON": sys.executable,
                    # HOME moved → a user-site PyYAML (~/Library/Python/…) would vanish from
                    # sys.path; hand the interpreter the yaml it already has
                    "PYTHONPATH": os.pathsep.join(filter(None, [_yaml_parent(), os.environ.get("PYTHONPATH")]))}
        self.env.pop("AIASSISTANT_HOME", None)   # the script must set it itself

    def run_sync(self, *args):
        return subprocess.run(["bash", str(self.repo / "scripts" / "skills_sync.sh"), *args],
                              capture_output=True, text=True, timeout=60, env=self.env)

    def link(self, name):
        return self.user_home / ".claude" / "skills" / name

    def test_fresh_machine_gets_the_defaults_and_decisions_stick(self):
        proc = self.run_sync()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("actions: board-agent=enabled_default, test-code=enabled_default", proc.stdout)
        for name in ("board-agent", "test-code"):
            self.assertTrue(self.link(name).is_symlink(), name)
            # the script resolves REPO_ROOT with `pwd -P` (§55): compare physical paths
            self.assertEqual(Path(os.readlink(str(self.link(name)))).resolve(),
                             (self.repo / "skills" / name).resolve())
        self.assertFalse(self.link("write-better").exists())
        state = json.loads((self.repo / "state" / "skills.json").read_text(encoding="utf-8"))
        self.assertEqual(state["decisions"], {"board-agent": "enabled", "test-code": "enabled"})
        # idempotent
        proc = self.run_sync()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn("actions:", proc.stdout)
        # the owner switches one off (the CLI the Settings page also routes to) — sync respects it
        off = subprocess.run([sys.executable, "-m", "act.lib.skills", "disable", "test-code"],
                             cwd=str(self.repo), capture_output=True, text=True, timeout=60,
                             env={**self.env, "AIASSISTANT_HOME": str(self.repo)})
        self.assertEqual(off.returncode, 0, off.stderr)
        self.assertFalse(self.link("test-code").is_symlink())
        proc = self.run_sync()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertFalse(self.link("test-code").is_symlink(), "a disabled decision survives sync")
        self.assertIn("disabled 3 (test-code, test-ui, write-better)", proc.stdout)

    def test_json_no_defaults_and_broken_manifest_exit_codes(self):
        proc = self.run_sync("--no-defaults", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        doc = json.loads(proc.stdout)
        self.assertEqual(doc["actions"], [])
        self.assertEqual({r["name"]: r["state"] for r in doc["skills"]},
                         {"board-agent": "disabled", "test-code": "disabled", "test-ui": "disabled", "write-better": "disabled"})
        self.assertEqual(self.run_sync("--bogus").returncode, 2)
        (self.repo / "skills" / "index.yaml").write_text("schema: 1\nskills: []\n", encoding="utf-8")
        proc = self.run_sync()
        self.assertEqual(proc.returncode, 3)
        self.assertIn("manifest error", proc.stderr)

    def test_help_and_no_python(self):
        proc = self.run_sync("--help")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("--pull", proc.stdout)
        env = {**self.env, "AIASSISTANT_PYTHON": "/nonexistent/python3", "PATH": str(self.tmp / "empty-bin")}
        (self.tmp / "empty-bin").mkdir()
        # git/bash/sed/tail must still resolve for the script's own plumbing
        for tool in ("bash", "sed", "git", "tail", "dirname", "command"):
            path = shutil.which(tool)
            if path:
                os.symlink(path, str(self.tmp / "empty-bin" / tool))
        proc = subprocess.run(["bash", str(self.repo / "scripts" / "skills_sync.sh")],
                              capture_output=True, text=True, timeout=60, env=env)
        if proc.returncode == 5:
            self.assertIn("no python3", proc.stderr)
        else:   # /usr/bin/python3 with PyYAML exists on this box: the platform candidate won
            self.assertEqual(proc.returncode, 0, proc.stderr)
