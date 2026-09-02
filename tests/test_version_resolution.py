"""版本解析顺序判例（CONTRACT §56.1；宪法第 8 条修宪）：stamp → git describe → 回落值。

零真 subprocess：git 经注入的 run= 假货回答（防腐 #7：真 git 夹具住
tests/integration/test_version_git_fixture.py）。钉住的行为：
  - act/_version.py 在 → 它赢，git 连问都不问；
  - 没有 stamp、git 答 `vX.Y.Z-0-g…` → X.Y.Z；领先 N → X.Y.Z+N；
  - 过渡条款：回落常量比最近 tag 新且 HEAD 不在 tag 上 → 回落常量（旧部署脚本
    用 sed 读那一行当期望版本，新 actd 心跳必须逐字相等）；常量 ≤ tag 时不触发；
  - git 答不上（非 checkout / 无 tag / 没装 git / 超时）→ 回落常量；resolve 永不抛；
  - stamp_decision：git 答不上但已有 stamp → 保留（.pkg 副本盖的是真 tag）；
  - write_stamp 原子写、read_stamp 只认 `__version__ = "…"` 形状；
  - doctor `version` 行：无 stamp = WARN、stamp ≠ describe = WARN、一致 = OK、
    非 git checkout 有 stamp = OK；永不 FAIL。
"""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from act import doctor
from act.lib import version as ver


def fake_git(stdout=None, returncode=0, raise_exc=None):
    """注入 run=：记录 argv，按剧本回 CompletedProcess 或抛。"""
    calls = []

    def run(argv, **kwargs):
        calls.append(list(argv))
        run.kwargs.append(kwargs)
        if raise_exc is not None:
            raise raise_exc
        return subprocess.CompletedProcess(argv, returncode, stdout or "", "")
    run.calls = calls
    run.kwargs = []
    return run


class ParseTagTestCase(unittest.TestCase):
    def test_accepts_v_prefix_and_bare(self):
        self.assertEqual(ver.parse_tag("v0.48.15"), (0, 48, 15))
        self.assertEqual(ver.parse_tag("0.48.15"), (0, 48, 15))
        self.assertEqual(ver.parse_tag(" v1.2.3 "), (1, 2, 3))

    def test_rejects_other_shapes(self):
        for bad in ("", None, "v0.48", "0.48.15+2", "v0.48.15-rc1", "release-1", "vX.Y.Z"):
            self.assertIsNone(ver.parse_tag(bad), bad)


class DescribeTestCase(unittest.TestCase):
    def test_exact_tag(self):
        run = fake_git("v0.48.16-0-g61cfed6\n")
        self.assertEqual(ver.git_describe(Path("/repo"), run), ("0.48.16", 0))
        argv = run.calls[0]
        self.assertEqual(argv[:3], ["git", "-C", str(Path("/repo"))])  # str(): Windows spells it \repo
        self.assertIn("--long", argv)
        self.assertIn("--tags", argv)

    def test_ahead_counts_commits(self):
        self.assertEqual(ver.git_describe(Path("/r"), fake_git("v0.48.16-3-gabcdef0")), ("0.48.16", 3))

    def test_failures_are_none(self):
        self.assertIsNone(ver.git_describe(Path("/r"), fake_git("fatal: No names found", 128)))
        self.assertIsNone(ver.git_describe(Path("/r"), fake_git("garbage")))
        self.assertIsNone(ver.git_describe(Path("/r"), fake_git(raise_exc=FileNotFoundError("git"))))
        self.assertIsNone(ver.git_describe(Path("/r"), fake_git(
            raise_exc=subprocess.TimeoutExpired("git", 10))))

    def test_non_version_tag_is_none(self):
        # --match filters server-side, but a stray shape must not crash the parser
        self.assertIsNone(ver.git_describe(Path("/r"), fake_git("vnext-2-gabc")))

    def test_describe_never_climbs_above_the_repo_root(self):
        # a .pkg / tarball copy under a $HOME that is itself a git repo (dotfiles)
        # must not inherit that repo's tags: the ceiling is the root's parent
        run = fake_git("v9.9.9-0-gabc")
        ver.git_describe(Path("/home/u/pipeline"), run)
        env = run.kwargs[0]["env"]
        self.assertEqual(env["GIT_CEILING_DIRECTORIES"], str(Path("/home/u")))
        self.assertEqual(env["GIT_TERMINAL_PROMPT"], "0")


class FromDescribeTestCase(unittest.TestCase):
    def test_exact_tag_wins_regardless_of_fallback(self):
        self.assertEqual(ver.from_describe(("0.48.16", 0), "0.48.17"), "0.48.16")
        self.assertEqual(ver.from_describe(("0.48.16", 0), None), "0.48.16")

    def test_ahead_appends_build_metadata(self):
        self.assertEqual(ver.from_describe(("0.48.16", 2), "0.48.16"), "0.48.16+2")
        self.assertEqual(ver.from_describe(("0.48.16", 2), "0.48.10"), "0.48.16+2")
        self.assertEqual(ver.from_describe(("0.48.16", 2), None), "0.48.16+2")

    def test_transition_clause_fallback_ahead_of_tag(self):
        # the one-time cutover: the committed line says 0.48.17, the checkout is
        # past v0.48.16 but the v0.48.17 tag has not been fetched (or minted) yet
        self.assertEqual(ver.from_describe(("0.48.16", 1), "0.48.17"), "0.48.17")
        self.assertEqual(ver.from_describe(("0.48.16", 5), "0.49.0"), "0.49.0")


class ComputeAndResolveTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ver-"))
        self.stamp = self.tmp / "act" / "_version.py"

    def test_compute_prefers_git_then_fallback(self):
        self.assertEqual(ver.compute("0.48.17", self.tmp, fake_git("v0.48.16-0-gabc")), ("0.48.16", "git"))
        self.assertEqual(ver.compute("0.48.17", self.tmp, fake_git("", 128)), ("0.48.17", "fallback"))
        self.assertEqual(ver.compute(None, self.tmp, fake_git("", 128)), ("0.0.0", "fallback"))

    def test_resolve_stamp_first_git_never_asked(self):
        ver.write_stamp("9.9.9", self.stamp)
        run = fake_git("v0.48.16-0-gabc")
        self.assertEqual(ver.resolve("0.48.17", self.stamp, self.tmp, run), "9.9.9")
        self.assertEqual(run.calls, [])

    def test_resolve_git_then_fallback(self):
        self.assertEqual(ver.resolve("0.48.17", self.stamp, self.tmp, fake_git("v0.48.16-0-gabc")), "0.48.16")
        self.assertEqual(ver.resolve("0.48.17", self.stamp, self.tmp, fake_git("", 128)), "0.48.17")

    def test_resolve_never_raises(self):
        def boom(*a, **k):
            raise RuntimeError("weird")
        self.assertEqual(ver.resolve("0.48.17", self.stamp, self.tmp, boom), "0.48.17")
        self.assertEqual(ver.resolve(None, self.stamp, self.tmp, boom), "0.0.0")

    def test_stamp_decision_keeps_existing_stamp_when_git_cannot_answer(self):
        ver.write_stamp("0.48.16", self.stamp)
        self.assertEqual(ver.stamp_decision("0.48.99", self.stamp, self.tmp, fake_git("", 128)),
                         ("0.48.16", "stamp"))
        # git answers → git wins even over an existing stamp (re-stamp after a merge)
        self.assertEqual(ver.stamp_decision("0.48.99", self.stamp, self.tmp, fake_git("v0.48.17-0-gabc")),
                         ("0.48.17", "git"))
        # nothing at all → fallback
        self.stamp.unlink()
        self.assertEqual(ver.stamp_decision("0.48.99", self.stamp, self.tmp, fake_git("", 128)),
                         ("0.48.99", "fallback"))

    def test_write_and_read_stamp(self):
        path = ver.write_stamp("0.48.17+2", self.stamp)
        self.assertEqual(path, self.stamp)
        text = self.stamp.read_text(encoding="utf-8")
        self.assertIn("§56", text)
        self.assertIn('__version__ = "0.48.17+2"', text)
        self.assertEqual(ver.read_stamp(self.stamp), "0.48.17+2")
        self.assertEqual([p.name for p in self.stamp.parent.iterdir()], ["_version.py"],
                         "atomic write must leave no temp file behind")
        if os.name == "posix":
            # mkstemp's 0600 would break the .pkg postinstall rsync (root-owned payload read as the user)
            self.assertEqual(os.stat(self.stamp).st_mode & 0o777, 0o644)

    def test_read_stamp_rejects_garbage(self):
        self.assertIsNone(ver.read_stamp(self.tmp / "missing.py"))
        bad = self.tmp / "bad.py"
        bad.write_text("VERSION = '1.2.3'\n", encoding="utf-8")
        self.assertIsNone(ver.read_stamp(bad))
        bad.write_text('__version__ = ""\n', encoding="utf-8")
        self.assertIsNone(ver.read_stamp(bad))

    def test_read_fallback_reads_the_real_init(self):
        fb = ver.read_fallback()
        self.assertIsNotNone(ver.parse_tag(fb), "act/__init__.py must keep exactly one X.Y.Z fallback line: %r" % fb)

    def test_status_shape(self):
        ver.write_stamp("0.48.16", self.stamp)
        st = ver.status("0.48.16", self.stamp, self.tmp, fake_git("v0.48.16-2-gabc"))
        self.assertEqual(st, {"stamp": "0.48.16", "git": True, "computed": "0.48.16+2", "fallback": "0.48.16"})
        st = ver.status("0.48.16", self.stamp, self.tmp, fake_git("", 128))
        self.assertEqual(st["git"], False)
        self.assertEqual(st["computed"], "0.48.16")


class DoctorVersionRowTestCase(unittest.TestCase):
    def row(self, **st):
        base = {"stamp": None, "git": True, "computed": "0.48.16", "fallback": "0.48.16"}
        base.update(st)
        return doctor._check_version(doctor.Probes(version_status=lambda: dict(base)))

    def test_missing_stamp_is_warn_with_install_fix(self):
        r = self.row(stamp=None)
        self.assertEqual((r.name, r.status), ("version", doctor.WARN))
        self.assertIn("install.sh", r.fix)

    def test_stale_stamp_is_warn(self):
        r = self.row(stamp="0.48.15", computed="0.48.16")
        self.assertEqual(r.status, doctor.WARN)
        self.assertIn("0.48.15", r.detail)
        self.assertIn("0.48.16", r.detail)

    def test_matching_stamp_is_ok(self):
        r = self.row(stamp="0.48.16", computed="0.48.16")
        self.assertEqual(r.status, doctor.OK)
        self.assertIn("v0.48.16", r.detail)

    def test_non_git_checkout_with_stamp_is_ok(self):
        r = self.row(stamp="0.48.16", git=False, computed="0.48.99")
        self.assertEqual(r.status, doctor.OK)

    def test_row_never_fails(self):
        for st in ({"stamp": None}, {"stamp": "1", "computed": "2"}, {"stamp": None, "git": False}):
            self.assertNotEqual(self.row(**st).status, doctor.FAIL)

    def test_row_is_in_every_platform_composition(self):
        self.assertIn(doctor._check_version, doctor._checks_for_platform())


if __name__ == "__main__":
    unittest.main()
