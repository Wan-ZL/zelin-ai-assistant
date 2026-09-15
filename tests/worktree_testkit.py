"""§75 worktree 回收判例共用的假 git runner + 真 tmpdir 工厂（零子进程、零网络）。

``FakeGit`` 按 argv 前缀路由 ``act/lib/worktrees.py`` 用到的全部 git 子命令，并把每次
调用记进 ``calls``（判例据此钉「prune 在 remove 之前」「永不 --force / 永不 -D」这类
顺序与形状）。目录是真的（`touched_at` 要 stat、`under` 要 realpath），内容是空的。
"""
from __future__ import annotations

import os
import shutil
import tempfile
import time


class Tree:
    """一个假 repo：`<base>/repo` 主工作树 + `<base>/repo/.claude/worktrees/<name>`。"""

    def __init__(self):
        self.base = os.path.realpath(tempfile.mkdtemp(prefix="wt-gc-"))
        self.repo = os.path.join(self.base, "repo")
        self.root = os.path.join(self.repo, ".claude", "worktrees")
        os.makedirs(self.root)

    def add(self, name, *, age_days=0.0, gitdir=True):
        path = os.path.join(self.root, name)
        os.makedirs(path, exist_ok=True)
        if gitdir:
            with open(os.path.join(path, ".git"), "w", encoding="utf-8") as fh:
                fh.write("gitdir: %s\n" % os.path.join(self.repo, ".git", "worktrees", name))
        stamp = time.time() - age_days * 86400.0
        for p in (os.path.join(path, ".git"), path):
            if os.path.exists(p):
                os.utime(p, (stamp, stamp))
        return path

    def cleanup(self):
        shutil.rmtree(self.base, ignore_errors=True)


class FakeGit:
    """``git(args, cwd) -> (rc, stdout)`` 的替身。``entries`` = [{path, branch, head,
    locked}]（主工作树自动排在第一条）；``merged`` / ``remotes`` / ``dirty`` /
    ``ahead`` 是判决的输入；``fail`` 里的子命令前缀报 rc=1。"""

    def __init__(self, repo, entries=(), *, merged=(), remotes=("origin/main",),
                 dirty=(), ahead=None, default_ref="origin/main", fail=(),
                 undeletable=()):
        self.repo = repo
        self.entries = [dict(e) for e in entries]
        self.merged = set(merged)
        self.remotes = list(remotes)
        self.dirty = set(dirty)
        self.ahead = dict(ahead or {})
        self.default_ref = default_ref
        self.fail = set(fail)
        self.undeletable = set(undeletable)
        self.calls: list = []
        self.removed: list = []
        self.branches_deleted: list = []
        self.pruned = 0

    def __call__(self, args, cwd):
        args = [str(a) for a in args]
        self.calls.append((tuple(args), cwd))
        head = " ".join(args[:2])
        if head in self.fail:
            return 1, ""
        return getattr(self, "_" + _slot(args), self._unknown)(args, cwd)

    # -- readers ------------------------------------------------------------ #
    def _worktree_list(self, _args, _cwd):
        lines = ["worktree %s" % self.repo, "HEAD " + "0" * 40, "branch refs/heads/main", ""]
        for e in self.entries:
            lines.append("worktree %s" % e["path"])
            lines.append("HEAD %s" % e.get("head", "a" * 40))
            lines.append(("branch refs/heads/" + e["branch"]) if e.get("branch") else "detached")
            if e.get("locked"):
                lines.append("locked")
            lines.append("")
        return 0, "\n".join(lines)

    def _symbolic_ref(self, _args, _cwd):
        return (0, self.default_ref + "\n") if self.default_ref else (1, "")

    def _for_each_ref(self, _args, _cwd):
        return 0, "\n".join(self.remotes) + "\n"

    def _branch_merged(self, _args, _cwd):
        return 0, "\n".join(sorted(self.merged)) + "\n"

    def _status(self, _args, cwd):
        return (0, "?? junk\n") if cwd in self.dirty else (0, "")

    def _rev_list(self, args, _cwd):
        return 0, "%d\n" % int(self.ahead.get(args[2], 0))

    # -- writers ------------------------------------------------------------ #
    def _worktree_prune(self, _args, _cwd):
        self.pruned += 1
        return 0, ""

    def _worktree_remove(self, args, _cwd):
        path = args[-1]
        self.removed.append(path)
        self.entries = [e for e in self.entries if e["path"] != path]
        return 0, ""

    def _branch_delete(self, args, _cwd):
        name = args[-1]
        if name in self.undeletable:
            return 1, "not fully merged"
        self.branches_deleted.append(name)
        return 0, ""

    def _unknown(self, args, _cwd):
        raise AssertionError("unexpected git call: %s" % (args,))


def _slot(args) -> str:
    """argv → FakeGit 上的方法名后缀。"""
    two = tuple(args[:2])
    table = {("worktree", "list"): "worktree_list", ("worktree", "prune"): "worktree_prune",
             ("worktree", "remove"): "worktree_remove", ("branch", "--merged"): "branch_merged",
             ("branch", "-d"): "branch_delete", ("symbolic-ref", "--quiet"): "symbolic_ref",
             ("for-each-ref", "--format=%(refname:short)"): "for_each_ref",
             ("status", "--porcelain"): "status", ("rev-list", "--count"): "rev_list"}
    return table.get(two, "unknown")
