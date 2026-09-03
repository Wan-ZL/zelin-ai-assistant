"""repos — the ~/Projects inventory the routing prompt is fed (§7 target_repo).

Characterization net for ``inventory`` / ``_readme_hint`` on a temp tree:
skip list, hidden dirs, non-git dirs, the limit, README precedence + fallthrough
past a blank/unreadable README, ``#`` stripping and the 90-char clip, and the
never-raises contract on an unreadable root.
"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from act.lib import repos


def _repo(root: Path, name: str, readme: str = None, readme_name: str = "README.md",
          git: bool = True) -> Path:
    p = root / name
    p.mkdir(parents=True)
    if git:
        (p / ".git").mkdir()
    if readme is not None:
        (p / readme_name).write_text(readme, encoding="utf-8")
    return p


class InventoryTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="repos-inv-")
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_lists_only_visible_git_repos_outside_skip_list(self):
        _repo(self.root, "alpha", "# Alpha\nbody")
        _repo(self.root, ".hidden", "x")
        _repo(self.root, "plain", "x", git=False)
        _repo(self.root, "data", "x")
        _repo(self.root, "zelin-ai-assistant", "x")
        (self.root / "loose.txt").write_text("not a dir", encoding="utf-8")
        out = repos.inventory(self.root)
        self.assertEqual(out, [{"name": "alpha", "path": str(self.root / "alpha"),
                                "hint": "Alpha"}])

    def test_limit_stops_the_scan(self):
        for n in ("a", "b", "c"):
            _repo(self.root, n)
        self.assertEqual([r["name"] for r in repos.inventory(self.root, limit=2)], ["a", "b"])
        self.assertEqual(repos.inventory(self.root, limit=0), [])

    def test_unreadable_root_never_raises(self):
        self.assertEqual(repos.inventory(self.root / "missing"), [])

    def test_inventory_text_shapes(self):
        self.assertEqual(repos.inventory_text(self.root), "(no repos found)")
        _repo(self.root, "one", "Hint here")
        _repo(self.root, "two")
        self.assertEqual(repos.inventory_text(self.root),
                         f"- {self.root / 'one'} — Hint here\n- {self.root / 'two'}")


class ReadmeHintTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="repos-hint-")
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_precedence_md_before_rst(self):
        p = _repo(self.root, "r", "from rst", readme_name="README.rst")
        (p / "README.md").write_text("\n\n## from md\n", encoding="utf-8")
        self.assertEqual(repos._readme_hint(p), "from md")

    def test_blank_readme_falls_through_to_next_name(self):
        p = _repo(self.root, "r", "   \n\n", readme_name="README.md")
        (p / "README").write_text("bare readme", encoding="utf-8")
        self.assertEqual(repos._readme_hint(p), "bare readme")

    def test_no_readme_is_empty(self):
        self.assertEqual(repos._readme_hint(_repo(self.root, "r")), "")

    def test_hash_only_line_is_skipped_and_clipped_to_90(self):
        p = _repo(self.root, "r", "###\n# " + "x" * 120)
        self.assertEqual(repos._readme_hint(p), "x" * 90)

    def test_unreadable_readme_falls_through(self):
        p = _repo(self.root, "r", "secret", readme_name="README.md")
        (p / "README.txt").write_text("fallback", encoding="utf-8")
        real_read_text = Path.read_text

        def boom(self_path, *a, **kw):
            if self_path.name == "README.md":
                raise OSError("denied")
            return real_read_text(self_path, *a, **kw)

        with mock.patch.object(Path, "read_text", boom):
            self.assertEqual(repos._readme_hint(p), "fallback")

    def test_first_line_on_missing_file(self):
        self.assertEqual(repos._first_line(self.root / "nope"), "")

    def test_is_candidate_repo(self):
        good = _repo(self.root, "good")
        self.assertTrue(repos._is_candidate_repo(good))
        self.assertFalse(repos._is_candidate_repo(_repo(self.root, "nogit", git=False)))
        self.assertFalse(repos._is_candidate_repo(_repo(self.root, ".dot")))
        self.assertFalse(repos._is_candidate_repo(_repo(self.root, "data")))
        f = self.root / "file"
        f.write_text("", encoding="utf-8")
        self.assertFalse(repos._is_candidate_repo(f))
        self.assertTrue(os.path.isdir(good))


if __name__ == "__main__":
    unittest.main()
