#!/usr/bin/env python3
"""清掉已随某个 tag 发出去的 changelog.d/ fragments（CONTRACT §56.7）。

    python3 scripts/ci/changelog_prune.py              # 删除：在最新 tag 树里且逐字节相同的 fragment
    python3 scripts/ci/changelog_prune.py --dry-run    # 只列出会删什么
    python3 scripts/ci/changelog_prune.py --notice     # CI 提示：有可清的就打一条 ::notice::，永不失败
    python3 scripts/ci/changelog_prune.py --tag v0.48.30   # 以指定 tag 为「已发版」基线（默认 = 最高 tag）

**谁来跑**：下一个动 changelog.d 的 PR（或任何 chore PR）——CI **永不**往 main 写 commit
（§56.2「不写分支」），所以消费过的 fragment 不由发版 job 删，而是留给人 / agent 在
自己的 PR 里顺手清。发版正文是**增量**（changelog_release_notes.py：本 tag 树里有、
上一个 tag 树里没有的条目），所以晚清、不清都不会让一条记录发两次。

**判定**：`git ls-tree -r <tag> -- changelog.d` 给出发版树里每个 fragment 的 blob id；
本地文件的 blob id（`changelog_fragments.blob_sha`，不 spawn git）与之相同 → 已消费 →
删；不同 → 发版后被改过 → **保留**并点名（改过的条目会在下一版正文里再出现一次——
这是「改已发版的 fragment」的代价，正确做法是加新 fragment）；tag 里没有的 → 未发版
→ 保留。git 只在 CLI 壳里 spawn（`git` 注入缝），纯函数 ``released_blobs`` /
``prune_plan``；判例 tests/test_changelog_prune.py。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import changelog_fragments as frags  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from act.lib import version as ver  # noqa: E402

_PREFIX = frags.FRAGMENT_DIR + "/"


def _flat_fragment_name(path: str):
    """`changelog.d/<name>` → name；别的路径 / 子目录 / README.md → None。"""
    if not path.startswith(_PREFIX):
        return None
    name = path[len(_PREFIX):]
    return None if ("/" in name or name in frags.SKIP_NAMES) else name


def _blob_entry(raw: str):
    """一行 ls-tree 输出（`<mode> blob <sha>\t<path>`）→ (fragment 文件名, sha)；不是顶层 fragment blob → None。"""
    meta, sep, path = raw.partition("\t")
    parts = meta.split()
    if not sep or len(parts) != 3 or parts[1] != "blob":
        return None
    name = _flat_fragment_name(path)
    return (name, parts[2]) if name else None


def released_blobs(ls_tree_text: str) -> dict:
    """`git ls-tree -r <tag> -- changelog.d` 的输出 → {fragment 文件名: blob sha}。"""
    blobs: dict = {}
    for raw in ls_tree_text.splitlines():
        entry = _blob_entry(raw)
        if entry:
            blobs[entry[0]] = entry[1]
    return blobs


def prune_plan(local: dict, released: dict) -> tuple:
    """(可删的文件名, 发版后被改过的文件名)。local = {文件名: bytes}；released = released_blobs 的产物。"""
    prune: list = []
    modified: list = []
    for name in sorted(local):
        sha = released.get(name)
        if sha is None:
            continue
        (prune if frags.blob_sha(local[name]) == sha else modified).append(name)
    return prune, modified


def _git(args: list, root) -> str:
    """默认 git 壳：失败 / 没有 git → 空串（调用方按「没有」处理）。"""
    try:
        done = subprocess.run(["git"] + list(args), cwd=str(root), capture_output=True, text=True, check=False)
    except OSError:
        return ""
    return done.stdout if done.returncode == 0 else ""


def _local_fragments(dirpath: str) -> dict:
    local: dict = {}
    for name in frags.fragment_names(dirpath):
        path = os.path.join(dirpath, name)
        if os.path.isfile(path):
            with open(path, "rb") as fh:
                local[name] = fh.read()
    return local


def _report_kept(modified: list, tag: str, stdout) -> None:
    for name in modified:
        stdout.write("kept %s/%s — modified since %s (edited released fragments re-appear in the next notes; "
                     "add a new fragment instead)\n" % (frags.FRAGMENT_DIR, name, tag))


def _report(prune: list, modified: list, tag: str, dry: bool, notice: bool, stdout) -> None:
    _report_kept(modified, tag, stdout)
    if not prune:
        stdout.write("changelog fragments: nothing released in %s to prune\n" % tag)
        return
    verb = "would prune" if dry else "pruned"
    for name in prune:
        stdout.write("%s %s/%s (released in %s)\n" % (verb, frags.FRAGMENT_DIR, name, tag))
    if notice:
        stdout.write("::notice::%d changelog.d fragment(s) were released in %s — prune them in your next PR: "
                     "python3 scripts/ci/changelog_prune.py\n" % (len(prune), tag))


def _parse_args(argv):
    parser = argparse.ArgumentParser(description="delete changelog.d fragments already shipped in a tag")
    parser.add_argument("--tag", default="", help="released baseline (default: highest vX.Y.Z tag)")
    parser.add_argument("--dry-run", action="store_true", help="list only, delete nothing")
    parser.add_argument("--notice", action="store_true", help="dry-run + GitHub ::notice:: when something is prunable")
    return parser.parse_args(argv)


def _context(git, stdout, root) -> tuple:
    """注入缝的默认值：真 git 壳、sys.stdout、repo 根。"""
    stdout = stdout or sys.stdout
    root = Path(root or REPO_ROOT)
    git = git or (lambda args: _git(args, root))
    return git, stdout, root


def _delete(dirpath: str, names: list) -> None:
    for name in names:
        os.remove(os.path.join(dirpath, name))


def main(argv=None, git=None, stdout=None, root=None) -> int:
    git, stdout, root = _context(git, stdout, root)
    args = _parse_args(argv)
    tag = args.tag or _highest_tag(git)
    if not tag:
        stdout.write("changelog fragments: no release tag found — nothing to prune\n")
        return 0
    dirpath = str(root / frags.FRAGMENT_DIR)
    released = released_blobs(git(["ls-tree", "-r", tag, "--", frags.FRAGMENT_DIR]))
    prune, modified = prune_plan(_local_fragments(dirpath), released)
    dry = bool(args.dry_run or args.notice)
    _report(prune, modified, tag, dry, args.notice, stdout)
    if not dry:
        _delete(dirpath, prune)
    return 0


def _highest_tag(git) -> str:
    tags = [t.strip() for t in git(["tag", "-l", "v[0-9]*"]).splitlines() if t.strip()]
    top = ver.highest_tag(tags)
    return "v" + ver.format_version(top) if top else ""


if __name__ == "__main__":
    sys.exit(main())
