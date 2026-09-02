"""The real skill store is consistent (CONTRACT §65; D13 / R2.7.1 / R2.7.4).

Read-only over the checkout — no ~/.claude, no state/ (Store is only used for
its repo-side validation). Pinned:
  - skills/index.yaml loads; every skills/*/SKILL.md is listed and vice versa;
  - each SKILL.md frontmatter `version` equals the manifest version; name = dir;
  - names are kebab-case (they become the /slash command);
  - the tracked .claude/skills/ set == the default_enabled set, each entry a
    RELATIVE symlink (../../skills/<name>) that resolves to a SKILL.md — that is
    the mechanism dispatched agents see repo skills through, in every checkout
    and every git worktree, so it must never drift from the manifest;
  - .gitignore keeps Claude Code's runtime files out of .claude/ but tracks
    .claude/skills/;
  - skills/README.md documents every store skill by name.
"""
import os
import re
import sys
import unittest
from pathlib import Path

from tests import TMP_HOME  # noqa: F401 - sandbox env first

from act.lib import skills

REPO = Path(__file__).resolve().parents[1]
_WIN = sys.platform.startswith("win")
KEBAB = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


class RealStoreTestCase(unittest.TestCase):
    def setUp(self):
        self.store = skills.Store(repo_root=REPO, claude_home=Path(TMP_HOME) / "unused-claude",
                                  state_dir=Path(TMP_HOME) / "unused-state")
        self.entries = self.store.manifest()

    def test_manifest_lists_the_known_skills(self):
        names = [e["name"] for e in self.entries]
        for expected in ("board-agent", "test-code", "write-better"):
            self.assertIn(expected, names)
        self.assertEqual(names, sorted(names), "keep index.yaml alphabetical")

    def test_names_are_kebab_case_and_match_frontmatter(self):
        for e in self.entries:
            self.assertRegex(e["name"], KEBAB)
            fm = skills.read_frontmatter(REPO / "skills" / e["name"])
            self.assertEqual(fm.get("name"), e["name"], e["name"])
            self.assertTrue(str(fm.get("description", "")).strip(), "%s needs a description" % e["name"])

    @unittest.skipIf(_WIN, "symlinks are checked out as files on Windows")
    def test_store_is_consistent(self):
        self.assertEqual(skills.validate_repo(self.store), [])

    @unittest.skipIf(_WIN, "symlinks are checked out as files on Windows")
    def test_default_enabled_skills_are_project_visible_through_relative_links(self):
        project = REPO / ".claude" / "skills"
        expected = sorted(e["name"] for e in self.entries if e["default_enabled"])
        self.assertEqual(sorted(p.name for p in project.iterdir()), expected)
        for name in expected:
            link = project / name
            self.assertTrue(link.is_symlink(), name)
            self.assertEqual(os.readlink(str(link)), os.path.join("..", "..", "skills", name))
            self.assertTrue((link / "SKILL.md").is_file(), name)

    def test_write_better_is_off_by_default_and_versioned(self):
        wb = next(e for e in self.entries if e["name"] == "write-better")
        self.assertFalse(wb["default_enabled"], "a skill that changes the voice never turns on via git pull")
        self.assertIsNotNone(skills.parse_version(wb["version"]))
        self.assertTrue((REPO / "skills" / "write-better" / "scripts" / "style_check.py").is_file())

    def test_gitignore_tracks_only_skills_under_dot_claude(self):
        text = (REPO / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertIn(".claude/*", text)
        self.assertIn("!.claude/skills/", text)

    def test_readme_documents_every_skill(self):
        readme = (REPO / "skills" / "README.md").read_text(encoding="utf-8")
        for e in self.entries:
            self.assertIn("`%s`" % e["name"], readme, e["name"])
        self.assertIn("index.yaml", readme)
        self.assertIn("skills_sync.sh", readme)
