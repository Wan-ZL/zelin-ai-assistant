"""Skill store behaviour (CONTRACT §67; D13 / R2.7) — act/lib/skills.py against a
fixture store in a tempdir: never the real repo's skills/, never the developer's
~/.claude (every Store gets an injected claude_home + state_dir).

Pinned:
  - manifest validation: schema / list / required strings / default_enabled bool /
    version shape / missing SKILL.md / duplicate names → ManifestError with the reason;
  - frontmatter: fence must open the file, bad YAML → {}, `version` top-level or
    under `metadata`;
  - version distance = first differing component (0.2.1 → 0.4.0 = behind 2);
  - content hash ignores __pycache__ / .DS_Store / *.pyc and is path-sorted;
  - states: disabled → enable = symlink to THIS checkout; symlink into another
    checkout's skills/<name> = enabled + stale_target (sync re-points); symlink
    elsewhere / plain file = foreign (locked); identical dir = copy; recorded older
    copy = copy + stale (sync refreshes); anything else = custom (locked, never
    touched, shows installed_version + relation);
  - copy fallback when symlink() refuses (OSError) — recorded so it stays "copy";
  - sync: default_enabled applies only without a decision; a `disabled` decision
    survives every sync; a `enabled` decision re-creates a hand-deleted link;
  - CLI exit codes 0 / 2 usage / 3 manifest / 4 refusal, --json shape.
"""
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first

from act.lib import skills

_WIN = sys.platform.startswith("win")

MANIFEST = """schema: 1
skills:
  - name: alpha
    version: 1.2.0
    upstream: somewhere
    upstream_version: 2026-01
    default_enabled: true
    description: first fixture skill
  - name: beta
    version: 0.1.0
    default_enabled: false
    description: second fixture skill
"""


def skill_md(name, version, body="# body\n"):
    return "---\nname: %s\ndescription: fixture %s\nversion: %s\n---\n\n%s" % (name, name, version, body)


def make_repo(root: Path, manifest: str = MANIFEST, project_links=("alpha",)) -> Path:
    """A fixture store: skills/index.yaml + two skills + tracked-style .claude/skills links."""
    skills_dir = root / "skills"
    (skills_dir / "alpha" / "references").mkdir(parents=True)
    (skills_dir / "alpha" / "SKILL.md").write_text(skill_md("alpha", "1.2.0"), encoding="utf-8")
    (skills_dir / "alpha" / "references" / "notes.md").write_text("notes\n", encoding="utf-8")
    (skills_dir / "beta").mkdir()
    (skills_dir / "beta" / "SKILL.md").write_text(skill_md("beta", "0.1.0"), encoding="utf-8")
    (skills_dir / "index.yaml").write_text(manifest, encoding="utf-8")
    project = root / ".claude" / "skills"
    project.mkdir(parents=True)
    for name in project_links:
        os.symlink(os.path.join("..", "..", "skills", name), str(project / name))
    return root


@unittest.skipIf(_WIN, "the fixture store needs symlinks (POSIX); pure-function cases below still run")
class _Case(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="skills-store-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = make_repo(self.tmp / "repo")
        self.claude_home = self.tmp / "claude-home"
        self.state_dir = self.tmp / "state"
        self.store = skills.Store(repo_root=self.repo, claude_home=self.claude_home,
                                  state_dir=self.state_dir)

    def rows(self):
        return {r["name"]: r for r in self.store.snapshot()["skills"]}

    def link(self, name):
        return self.claude_home / "skills" / name


# --------------------------------------------------------------------------- #
# manifest + frontmatter
# --------------------------------------------------------------------------- #
class ManifestTestCase(_Case):
    def test_valid_manifest_loads_in_order(self):
        entries = self.store.manifest()
        self.assertEqual([e["name"] for e in entries], ["alpha", "beta"])
        self.assertEqual(entries[0]["upstream_version"], "2026-01")

    def _bad(self, text, fragment):
        (self.repo / "skills" / "index.yaml").write_text(text, encoding="utf-8")
        with self.assertRaises(skills.ManifestError) as ctx:
            self.store.manifest()
        self.assertIn(fragment, str(ctx.exception))

    def test_missing_file(self):
        (self.repo / "skills" / "index.yaml").unlink()
        with self.assertRaises(skills.ManifestError) as ctx:
            self.store.manifest()
        self.assertIn("cannot read", str(ctx.exception))

    def test_bad_yaml(self):
        self._bad("schema: [\n", "not valid YAML")

    def test_wrong_schema_and_shape(self):
        self._bad("schema: 2\nskills: []\n", "schema: 1")
        self._bad("- a\n", "schema: 1")
        self._bad("schema: 1\nskills: {}\n", "non-empty list")
        self._bad("schema: 1\nskills: []\n", "non-empty list")

    def test_entry_shapes(self):
        self._bad("schema: 1\nskills:\n  - alpha\n", "must be a mapping")
        self._bad("schema: 1\nskills:\n  - name: alpha\n    version: 1.0.0\n    description: ''\n"
                  "    default_enabled: true\n", "`description` must be a non-empty string")
        self._bad("schema: 1\nskills:\n  - name: alpha\n    version: 1.0.0\n    description: x\n"
                  "    default_enabled: yes please\n", "default_enabled")
        self._bad("schema: 1\nskills:\n  - name: alpha\n    version: v1\n    description: x\n"
                  "    default_enabled: true\n", "dotted integers")
        self._bad("schema: 1\nskills:\n  - name: gamma\n    version: 1.0.0\n    description: x\n"
                  "    default_enabled: true\n", "SKILL.md does not exist")

    def test_without_pyyaml_the_manifest_is_an_error_and_frontmatter_is_empty(self):
        with mock.patch.object(skills, "yaml", None):
            with self.assertRaises(skills.ManifestError) as ctx:
                self.store.manifest()
            self.assertIn("PyYAML", str(ctx.exception))
            self.assertEqual(skills.read_frontmatter(self.repo / "skills" / "alpha"), {})

    def test_duplicate_names(self):
        dup = MANIFEST.replace("name: beta", "name: alpha")
        self._bad(dup, "duplicate")

    def test_frontmatter_rules(self):
        d = self.repo / "skills" / "alpha"
        self.assertEqual(skills.frontmatter_version(skills.read_frontmatter(d)), "1.2.0")
        (d / "SKILL.md").write_text("# no fence\n---\nversion: 9\n---\n", encoding="utf-8")
        self.assertEqual(skills.read_frontmatter(d), {})
        (d / "SKILL.md").write_text("---\nname: [\n---\n", encoding="utf-8")
        self.assertEqual(skills.read_frontmatter(d), {})
        (d / "SKILL.md").write_text("---\nname: a\nmetadata:\n  version: 2.0\n---\n", encoding="utf-8")
        self.assertEqual(skills.frontmatter_version(skills.read_frontmatter(d)), "2.0")
        (d / "SKILL.md").write_text("---\n- list\n---\n", encoding="utf-8")
        self.assertEqual(skills.read_frontmatter(d), {})
        (d / "SKILL.md").write_text("---\nname: a\n", encoding="utf-8")   # never closed
        self.assertEqual(skills.read_frontmatter(d), {})
        self.assertEqual(skills.read_frontmatter(self.repo / "skills" / "nope"), {})
        self.assertIsNone(skills.frontmatter_version({"version": ["1"]}))

    def test_validate_repo_is_clean_for_the_fixture_and_names_every_drift(self):
        self.assertEqual(skills.validate_repo(self.store), [])
        # frontmatter drift
        (self.repo / "skills" / "beta" / "SKILL.md").write_text(skill_md("beta", "0.2.0"), encoding="utf-8")
        # unlisted dir
        (self.repo / "skills" / "gamma").mkdir()
        (self.repo / "skills" / "gamma" / "SKILL.md").write_text(skill_md("gamma", "1.0.0"), encoding="utf-8")
        # project links: alpha absolute, beta present though not default
        proj = self.repo / ".claude" / "skills"
        (proj / "alpha").unlink()
        os.symlink(str(self.repo / "skills" / "alpha"), str(proj / "alpha"))
        os.symlink(os.path.join("..", "..", "skills", "beta"), str(proj / "beta"))
        problems = skills.validate_repo(self.store)
        joined = "\n".join(problems)
        self.assertIn("beta: SKILL.md frontmatter version '0.2.0' != manifest '0.1.0'", joined)
        self.assertIn("skills/gamma has a SKILL.md but is not in index.yaml", joined)
        self.assertIn(".claude/skills/beta present but not default_enabled", joined)
        self.assertIn(".claude/skills/alpha must be a relative symlink", joined)
        shutil.rmtree(str(proj))
        self.assertIn(".claude/skills/alpha missing", "\n".join(skills.validate_repo(self.store)))


class VersionAndHashTestCase(unittest.TestCase):
    def test_parse_version(self):
        self.assertEqual(skills.parse_version("0.2.1"), (0, 2, 1))
        self.assertEqual(skills.parse_version(" 3 "), (3,))
        for bad in ("v1", "", "  ", None, 3, "1.x"):
            self.assertIsNone(skills.parse_version(bad), bad)

    def test_relation_is_first_differing_component(self):
        self.assertEqual(skills.version_relation("0.2.1", "0.4.0"), ("behind", 2))
        self.assertEqual(skills.version_relation("0.2.1", "0.2.3"), ("behind", 2))
        self.assertEqual(skills.version_relation("2.0.0", "1.9.9"), ("ahead", 1))
        self.assertEqual(skills.version_relation("1.0", "1.0.0"), ("same", 0))
        self.assertEqual(skills.version_relation("1.0.0", "1.0.0.1"), ("behind", 1))
        self.assertEqual(skills.version_relation(None, "1.0.0"), ("unknown", 0))
        self.assertEqual(skills.version_relation("1.0.0", "x"), ("unknown", 0))

    def test_tree_hash_skips_caches_and_is_order_independent(self):
        tmp = Path(tempfile.mkdtemp(prefix="skills-hash-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        a, b = tmp / "a", tmp / "b"
        for d in (a, b):
            (d / "sub").mkdir(parents=True)
            (d / "SKILL.md").write_text("x", encoding="utf-8")
            (d / "sub" / "f.py").write_text("print(1)\n", encoding="utf-8")
        (a / "__pycache__").mkdir()
        (a / "__pycache__" / "f.cpython-39.pyc").write_bytes(b"junk")
        (a / "sub" / "g.pyc").write_bytes(b"junk")
        (a / ".DS_Store").write_bytes(b"junk")
        self.assertEqual(skills.tree_hash(a), skills.tree_hash(b))
        (b / "sub" / "f.py").write_text("print(2)\n", encoding="utf-8")
        self.assertNotEqual(skills.tree_hash(a), skills.tree_hash(b))
        (b / "sub" / "f.py").write_text("print(1)\n", encoding="utf-8")
        os.rename(str(b / "sub" / "f.py"), str(b / "sub" / "h.py"))
        self.assertNotEqual(skills.tree_hash(a), skills.tree_hash(b), "path is part of the hash")


# --------------------------------------------------------------------------- #
# states + enable/disable
# --------------------------------------------------------------------------- #
@unittest.skipIf(_WIN, "symlink semantics are POSIX here")
class StateTestCase(_Case):
    def test_fresh_machine_everything_disabled_and_project_visibility_reported(self):
        rows = self.rows()
        self.assertEqual({n: r["state"] for n, r in rows.items()}, {"alpha": "disabled", "beta": "disabled"})
        self.assertEqual({n: r["toggle"] for n, r in rows.items()}, {"alpha": "enable", "beta": "enable"})
        self.assertTrue(rows["alpha"]["project_visible"])
        self.assertFalse(rows["beta"]["project_visible"])
        self.assertIsNone(rows["alpha"]["decision"])
        self.assertEqual(rows["alpha"]["link"], "none")
        snap = self.store.snapshot()
        self.assertEqual(snap["skills_dir"], str(self.claude_home / "skills"))
        self.assertEqual(snap["repo_skills_dir"], str(self.repo / "skills"))

    def test_enable_creates_symlink_to_this_checkout_and_records_decision(self):
        row = self.store.enable("alpha")
        self.assertEqual((row["state"], row["link"], row["toggle"]), ("enabled", "symlink", "disable"))
        self.assertFalse(row["stale_target"])
        self.assertEqual(row["installed_version"], "1.2.0")
        self.assertEqual(os.readlink(str(self.link("alpha"))), str(self.repo / "skills" / "alpha"))
        self.assertTrue((self.link("alpha") / "SKILL.md").is_file())
        self.assertEqual(self.store.state()["decisions"], {"alpha": "enabled"})
        self.assertEqual(self.store.state()["copies"], {})
        # idempotent
        self.assertEqual(self.store.enable("alpha")["state"], "enabled")

    def test_disable_unlinks_and_records(self):
        self.store.enable("alpha")
        row = self.store.disable("alpha")
        self.assertEqual(row["state"], "disabled")
        self.assertFalse(self.link("alpha").exists() or self.link("alpha").is_symlink())
        self.assertTrue((self.repo / "skills" / "alpha" / "SKILL.md").is_file(), "the repo copy is untouched")
        self.assertEqual(self.store.state()["decisions"], {"alpha": "disabled"})
        # disabling an absent skill only records the decision
        self.assertEqual(self.store.disable("beta")["decision"], "disabled")

    def test_unknown_skill(self):
        with self.assertRaises(skills.SkillError) as ctx:
            self.store.enable("nope")
        self.assertEqual(ctx.exception.code, "SKILL_UNKNOWN")

    def test_symlink_into_another_checkout_is_enabled_but_stale_and_sync_repoints(self):
        other = make_repo(self.tmp / "other")
        self.claude_home.joinpath("skills").mkdir(parents=True)
        os.symlink(str(other / "skills" / "alpha"), str(self.link("alpha")))
        row = self.rows()["alpha"]
        self.assertEqual((row["state"], row["stale_target"], row["toggle"]), ("enabled", True, "disable"))
        snap = self.store.sync()
        self.assertIn({"name": "alpha", "action": "repointed"}, snap["actions"])
        self.assertEqual(os.readlink(str(self.link("alpha"))), str(self.repo / "skills" / "alpha"))
        self.assertFalse(self.rows()["alpha"]["stale_target"])

    def test_broken_link_shaped_like_ours_is_stale_and_enable_repoints(self):
        self.claude_home.joinpath("skills").mkdir(parents=True)
        os.symlink(str(self.tmp / "gone" / "skills" / "alpha"), str(self.link("alpha")))
        self.assertEqual(self.rows()["alpha"]["stale_target"], True)
        row = self.store.enable("alpha")
        self.assertEqual((row["state"], row["stale_target"]), ("enabled", False))

    def test_foreign_symlink_and_plain_file_are_locked(self):
        self.claude_home.joinpath("skills").mkdir(parents=True)
        elsewhere = self.tmp / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / "SKILL.md").write_text("---\nname: alpha\n---\n", encoding="utf-8")
        os.symlink(str(elsewhere), str(self.link("alpha")))
        (self.link("beta")).write_text("not a dir", encoding="utf-8")
        rows = self.rows()
        self.assertEqual((rows["alpha"]["state"], rows["alpha"]["toggle"]), ("foreign", "locked"))
        self.assertEqual((rows["beta"]["state"], rows["beta"]["link"]), ("foreign", "file"))
        for name in ("alpha", "beta"):
            for verb in (self.store.enable, self.store.disable):
                with self.assertRaises(skills.SkillError) as ctx:
                    verb(name)
                self.assertEqual(ctx.exception.code, "SKILL_FOREIGN_LINK")
        self.assertTrue(self.link("alpha").is_symlink(), "never touched")
        self.assertEqual(self.store.sync()["actions"], [], "sync never touches foreign entries")

    def test_identical_directory_is_a_copy_and_disable_removes_it(self):
        self.claude_home.joinpath("skills").mkdir(parents=True)
        shutil.copytree(str(self.repo / "skills" / "alpha"), str(self.link("alpha")))
        row = self.rows()["alpha"]
        self.assertEqual((row["state"], row["link"], row["toggle"], row["stale_target"]),
                         ("copy", "directory", "disable", False))
        self.assertEqual((row["installed_version"], row["relation"]), ("1.2.0", "same"))
        self.store.disable("alpha")
        self.assertFalse(self.link("alpha").exists())

    def test_custom_copy_is_locked_and_reports_distance(self):
        self.claude_home.joinpath("skills").mkdir(parents=True)
        shutil.copytree(str(self.repo / "skills" / "alpha"), str(self.link("alpha")))
        (self.link("alpha") / "SKILL.md").write_text(skill_md("alpha", "1.0.0", "# my edit\n"), encoding="utf-8")
        row = self.rows()["alpha"]
        self.assertEqual((row["state"], row["toggle"]), ("custom", "locked"))
        self.assertEqual((row["installed_version"], row["relation"], row["distance"]), ("1.0.0", "behind", 2))
        for verb in (self.store.enable, self.store.disable):
            with self.assertRaises(skills.SkillError) as ctx:
                verb("alpha")
            self.assertEqual(ctx.exception.code, "SKILL_CUSTOM_KEEP")
            self.assertIn(str(self.link("alpha")), str(ctx.exception))
        self.assertEqual(self.store.sync()["actions"], [])
        self.assertIn("my edit", (self.link("alpha") / "SKILL.md").read_text(encoding="utf-8"),
                      "the owner's edit survives every store operation")

    def test_custom_copy_ahead_or_unversioned(self):
        self.claude_home.joinpath("skills").mkdir(parents=True)
        shutil.copytree(str(self.repo / "skills" / "alpha"), str(self.link("alpha")))
        (self.link("alpha") / "SKILL.md").write_text(skill_md("alpha", "2.0.0"), encoding="utf-8")
        row = self.rows()["alpha"]
        self.assertEqual((row["relation"], row["distance"]), ("ahead", 1))
        (self.link("alpha") / "SKILL.md").write_text("# no frontmatter at all\n", encoding="utf-8")
        row = self.rows()["alpha"]
        self.assertEqual((row["state"], row["installed_version"], row["relation"]), ("custom", None, "unknown"))


@unittest.skipIf(_WIN, "symlink semantics are POSIX here")
class CopyFallbackTestCase(_Case):
    def setUp(self):
        super().setUp()

        def refuse(_src, _dst):
            raise OSError(1, "symlinks not supported on this filesystem")

        self.store = skills.Store(repo_root=self.repo, claude_home=self.claude_home,
                                  state_dir=self.state_dir, symlink=refuse)

    def test_enable_copies_and_records_the_hash(self):
        row = self.store.enable("alpha")
        self.assertEqual((row["state"], row["link"], row["stale_target"]), ("copy", "directory", False))
        self.assertFalse(self.link("alpha").is_symlink())
        self.assertTrue((self.link("alpha") / "references" / "notes.md").is_file())
        rec = self.store.state()["copies"]["alpha"]
        self.assertEqual(rec["version"], "1.2.0")
        self.assertEqual(rec["hash"], skills.tree_hash(self.repo / "skills" / "alpha"))

    def test_repo_moves_on_unmodified_copy_becomes_stale_and_sync_refreshes(self):
        self.store.enable("alpha")
        # the repo skill advances (content + version); the copy is untouched
        (self.repo / "skills" / "alpha" / "SKILL.md").write_text(skill_md("alpha", "1.4.0", "# newer\n"), encoding="utf-8")
        manifest = MANIFEST.replace("version: 1.2.0", "version: 1.4.0")
        (self.repo / "skills" / "index.yaml").write_text(manifest, encoding="utf-8")
        row = self.rows()["alpha"]
        self.assertEqual((row["state"], row["stale_target"], row["relation"], row["distance"]),
                         ("copy", True, "behind", 2))
        snap = self.store.sync()
        self.assertIn({"name": "alpha", "action": "copy_refreshed"}, snap["actions"])
        row = self.rows()["alpha"]
        self.assertEqual((row["state"], row["stale_target"], row["installed_version"]), ("copy", False, "1.4.0"))
        self.assertIn("newer", (self.link("alpha") / "SKILL.md").read_text(encoding="utf-8"))

    def test_edited_copy_turns_custom_and_disable_refuses(self):
        self.store.enable("alpha")
        (self.link("alpha") / "references" / "notes.md").write_text("mine\n", encoding="utf-8")
        self.assertEqual(self.rows()["alpha"]["state"], "custom")
        with self.assertRaises(skills.SkillError):
            self.store.disable("alpha")
        self.assertTrue((self.link("alpha") / "references" / "notes.md").is_file())

    def test_enable_on_existing_copy_refreshes_it(self):
        self.store.enable("alpha")
        (self.repo / "skills" / "alpha" / "references" / "notes.md").write_text("v2\n", encoding="utf-8")
        row = self.store.enable("alpha")
        self.assertEqual((row["state"], row["stale_target"]), ("copy", False))
        self.assertEqual((self.link("alpha") / "references" / "notes.md").read_text(encoding="utf-8"), "v2\n")


# --------------------------------------------------------------------------- #
# sync: defaults + decisions
# --------------------------------------------------------------------------- #
@unittest.skipIf(_WIN, "symlink semantics are POSIX here")
class SyncTestCase(_Case):
    def test_defaults_apply_only_without_a_decision(self):
        snap = self.store.sync()
        self.assertEqual(snap["actions"], [{"name": "alpha", "action": "enabled_default"}])
        rows = {r["name"]: r for r in snap["skills"]}
        self.assertEqual((rows["alpha"]["state"], rows["beta"]["state"]), ("enabled", "disabled"))
        self.assertEqual(self.store.state()["decisions"], {"alpha": "enabled"})
        self.assertEqual(self.store.sync()["actions"], [], "second sync is a no-op")

    def test_disabled_decision_survives_sync(self):
        self.store.sync()
        self.store.disable("alpha")
        for _ in range(2):
            snap = self.store.sync()
            self.assertEqual(snap["actions"], [])
            self.assertEqual({r["name"]: r["state"] for r in snap["skills"]}, {"alpha": "disabled", "beta": "disabled"})

    def test_no_defaults_flag(self):
        snap = self.store.sync(apply_defaults=False)
        self.assertEqual(snap["actions"], [])
        self.assertEqual(self.store.state()["decisions"], {})

    def test_enabled_decision_recreates_a_hand_deleted_link(self):
        self.store.enable("beta")
        self.link("beta").unlink()
        snap = self.store.sync()
        self.assertIn({"name": "beta", "action": "relinked"}, snap["actions"])
        self.assertTrue(self.link("beta").is_symlink())

    def test_state_file_shape_and_tolerance(self):
        self.store.sync()
        doc = json.loads(self.store.state_path.read_text(encoding="utf-8"))
        self.assertEqual(sorted(doc), ["copies", "decisions", "schema", "updated_at"])
        self.assertEqual(doc["schema"], 1)
        self.store.state_path.write_text("garbage", encoding="utf-8")
        self.assertEqual(self.store.state()["decisions"], {})
        self.store.state_path.write_text('{"decisions": "no", "copies": 3}', encoding="utf-8")
        self.assertEqual(self.store.state()["copies"], {})
        self.store.state_path.write_text('[1]', encoding="utf-8")
        self.assertEqual(self.store.state()["decisions"], {})


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
@unittest.skipIf(_WIN, "symlink semantics are POSIX here")
class CliTestCase(_Case):
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(skills, "Store", lambda: self.store), \
                redirect_stdout(out), redirect_stderr(err):
            rc = skills.main(list(argv))
        return rc, out.getvalue(), err.getvalue()

    def test_usage(self):
        rc, _out, err = self.run_cli()
        self.assertEqual(rc, 2)
        self.assertIn("usage", err)
        self.assertEqual(self.run_cli("frobnicate")[0], 2)

    def test_list_summary_and_json(self):
        rc, out, _ = self.run_cli("list")
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "disabled 2 (alpha, beta)")
        rc, out, _ = self.run_cli("list", "--json")
        self.assertEqual(rc, 0)
        doc = json.loads(out)
        self.assertEqual([r["name"] for r in doc["skills"]], ["alpha", "beta"])

    def test_sync_summary_names_actions(self):
        rc, out, _ = self.run_cli("sync")
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "disabled 1 (beta) · enabled 1 (alpha) · actions: alpha=enabled_default")
        rc, out, _ = self.run_cli("sync", "--no-defaults")
        self.assertEqual(rc, 0)

    def test_enable_disable_and_exit_codes(self):
        rc, out, _ = self.run_cli("enable", "beta")
        self.assertEqual((rc, out.strip()), (0, "enabled 1 (beta)"))
        rc, _, err = self.run_cli("disable")
        self.assertEqual(rc, 4)
        self.assertIn("usage: disable <name>", err)
        rc, _, err = self.run_cli("enable", "nope")
        self.assertEqual(rc, 4)
        self.assertIn("SKILL_UNKNOWN", err)
        (self.repo / "skills" / "index.yaml").write_text("schema: 1\n", encoding="utf-8")
        rc, _, err = self.run_cli("list")
        self.assertEqual(rc, 3)
        self.assertIn("manifest error", err)

    def test_default_store_points_at_the_sandbox_home(self):
        store = skills.Store()
        self.assertEqual(str(store.repo_root), os.environ["AIASSISTANT_HOME"])
        self.assertEqual(store.state_path, Path(os.environ["AIASSISTANT_HOME"]) / "state" / "skills.json")
        self.assertEqual(store.claude_skills, Path.home() / ".claude" / "skills")
