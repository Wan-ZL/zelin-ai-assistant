"""changelog.d 清理判例（CONTRACT §56.7）：scripts/ci/changelog_prune.py，git 走注入缝、零子进程。

  - released_blobs：`git ls-tree -r <tag> -- changelog.d` 输出 → {文件名: blob sha}；非 blob 行、
    子目录、README.md、别的路径都忽略；
  - prune_plan：本地 blob sha == tag 里的 → 可删；不同 → 「发版后改过」保留；tag 里没有 → 保留；
  - main：默认基线 = 最高 vX.Y.Z tag（--tag 可指定）；真跑只删可删的；--dry-run / --notice 不删；
    --notice 有可清的才打 ::notice::；没有 tag → 什么都不做 rc 0；改过的文件点名保留。
"""
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path

_CI_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "ci")
if _CI_DIR not in sys.path:
    sys.path.insert(0, _CI_DIR)
import changelog_fragments as cf  # noqa: E402
import changelog_prune as cp  # noqa: E402

SHIPPED = b"type: added\n- shipped\n"
EDITED = b"type: added\n- shipped (edited after release)\n"
FRESH = b"type: fixed\n- fresh\n"


def ls_tree(entries):
    """{name: bytes} → 假 `git ls-tree -r` 输出（blob sha 用真算法）。"""
    return "".join("100644 blob %s\tchangelog.d/%s\n" % (cf.blob_sha(data), name) for name, data in entries.items())


class ParseLsTreeTestCase(unittest.TestCase):
    def test_blobs_only_flat_only_no_readme(self):
        text = (ls_tree({"a.md": SHIPPED, "README.md": b"# x\n"})
                + "040000 tree 1234567890123456789012345678901234567890\tchangelog.d/sub\n"
                + "100644 blob aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\tchangelog.d/sub/deep.md\n"
                + "100644 blob bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\tCHANGELOG.md\n"
                + "garbage line without tab\n")
        self.assertEqual(cp.released_blobs(text), {"a.md": cf.blob_sha(SHIPPED)})

    def test_empty(self):
        self.assertEqual(cp.released_blobs(""), {})


class PlanTestCase(unittest.TestCase):
    def test_identical_pruned_modified_kept_unreleased_kept(self):
        released = cp.released_blobs(ls_tree({"shipped.md": SHIPPED, "edited.md": SHIPPED}))
        local = {"shipped.md": SHIPPED, "edited.md": EDITED, "fresh.md": FRESH}
        self.assertEqual(cp.prune_plan(local, released), (["shipped.md"], ["edited.md"]))

    def test_nothing_released(self):
        self.assertEqual(cp.prune_plan({"a.md": FRESH}, {}), ([], []))


class FakeGit:
    def __init__(self, tags, tree):
        self.tags = tags
        self.tree = tree
        self.calls = []

    def __call__(self, args):
        self.calls.append(list(args))
        if args[0] == "tag":
            return "\n".join(self.tags) + "\n"
        if args[0] == "ls-tree":
            self.tag_asked = args[2]
            return self.tree
        raise AssertionError("unexpected git call %r" % (args,))


class MainTestCase(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="prune-"))
        self.dir = self.root / "changelog.d"
        self.dir.mkdir()
        for name, data in (("shipped.md", SHIPPED), ("edited.md", EDITED), ("fresh.md", FRESH)):
            (self.dir / name).write_bytes(data)
        (self.dir / "README.md").write_text("# shape\n", encoding="utf-8")
        self.git = FakeGit(["v0.48.29", "v0.48.30", "v0.9.9", "backup-2026"],
                           ls_tree({"shipped.md": SHIPPED, "edited.md": SHIPPED, "README.md": b"# shape\n"}))

    def run_main(self, argv):
        out = io.StringIO()
        rc = cp.main(argv, git=self.git, stdout=out, root=self.root)
        return rc, out.getvalue()

    def names(self):
        return sorted(p.name for p in self.dir.iterdir())

    def test_real_run_deletes_only_identical_released_fragments(self):
        rc, out = self.run_main([])
        self.assertEqual(rc, 0)
        self.assertEqual(self.names(), ["README.md", "edited.md", "fresh.md"])
        self.assertEqual(self.git.tag_asked, "v0.48.30")  # highest numeric tag, not lexical / non-version
        self.assertIn("pruned changelog.d/shipped.md (released in v0.48.30)", out)
        self.assertIn("kept changelog.d/edited.md — modified since v0.48.30", out)
        self.assertNotIn("::notice::", out)

    def test_dry_run_and_notice_delete_nothing(self):
        rc, out = self.run_main(["--dry-run"])
        self.assertEqual((rc, self.names()), (0, ["README.md", "edited.md", "fresh.md", "shipped.md"]))
        self.assertIn("would prune changelog.d/shipped.md", out)
        self.assertNotIn("::notice::", out)
        rc, out = self.run_main(["--notice"])
        self.assertEqual((rc, self.names()), (0, ["README.md", "edited.md", "fresh.md", "shipped.md"]))
        self.assertIn("::notice::1 changelog.d fragment(s) were released in v0.48.30", out)

    def test_notice_silent_when_nothing_prunable(self):
        os.remove(self.dir / "shipped.md")
        rc, out = self.run_main(["--notice"])
        self.assertEqual(rc, 0)
        self.assertNotIn("::notice::", out)
        self.assertIn("nothing released in v0.48.30 to prune", out)

    def test_explicit_tag_overrides_highest(self):
        rc, _ = self.run_main(["--tag", "v0.48.29", "--dry-run"])
        self.assertEqual((rc, self.git.tag_asked), (0, "v0.48.29"))
        self.assertNotIn(["tag", "-l", "v[0-9]*"], self.git.calls)

    def test_no_release_tag_does_nothing(self):
        self.git.tags = ["backup-2026"]
        rc, out = self.run_main([])
        self.assertEqual(rc, 0)
        self.assertIn("no release tag found", out)
        self.assertEqual(self.names(), ["README.md", "edited.md", "fresh.md", "shipped.md"])


if __name__ == "__main__":
    unittest.main()
