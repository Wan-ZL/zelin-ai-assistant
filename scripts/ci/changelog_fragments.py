#!/usr/bin/env python3
"""changelog.d/ 变更记录 fragments（CONTRACT §56.7）：解析、校验、拼成 [Unreleased] 形状。

    python3 scripts/ci/changelog_fragments.py check  [DIR]   # 全部 fragment 形状合法 → rc 0；否则 ::error:: + rc 1
    python3 scripts/ci/changelog_fragments.py render [DIR]   # 拼成 `### Added` … 段落打到 stdout（人眼预览）

**为什么有它**：CHANGELOG `[Unreleased]` 是并行 PR 最后一个「相邻行冲突」策源地——
六个 PR 各在同一段落顶端插一条 bullet，每合一个其余全部 rebase。fragment = 一个
PR 一个文件，文件之间永不相邻，git 合并零冲突。发版时由
`scripts/ci/changelog_release_notes.py` 把所有 fragment 与 legacy `[Unreleased]`
文本合成 Release 正文；消费过的 fragment 由下一个动 changelog.d 的 PR 用
`scripts/ci/changelog_prune.py` 清掉（CI 永不往 main 写 commit）。

**文件形状**（`changelog.d/<kebab-slug>.md`；README.md 免检）：

    type: added            ← 首个非空行；added|changed|deprecated|removed|fixed|security（不分大小写）
    - 一条顶格 bullet（`- ` 或 `* `）
      缩进的续行 / 子项归上一条 bullet
    - 再一条

一个 fragment 一种 type；一个 PR 既 Added 又 Fixed 就写两个文件（`<slug>.md` +
`<slug>-fixed.md`）。纯函数 ``parse_fragment`` / ``section_lines``；判例
tests/test_changelog_fragments.py。stdlib-only。
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys

FRAGMENT_DIR = "changelog.d"
# Keep a Changelog 1.1.0 的六个组，顺序即 Release 正文里的顺序。
TYPES = ("added", "changed", "deprecated", "removed", "fixed", "security")
SKIP_NAMES = frozenset({"README.md"})
_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*\.md$")
_TYPE_RE = re.compile(r"^type:\s*([a-z]+)\s*$", re.I)
_BULLET_PREFIXES = ("- ", "* ")


class Fragment:
    """一个已解析的 fragment：文件名、组（小写 type）、条目块（每块 = bullet 行 + 续行）。"""

    def __init__(self, name: str, kind: str, blocks: list):
        self.name = name
        self.kind = kind
        self.blocks = blocks


def title_for(kind: str) -> str:
    """组名 → `### ` 标题正文（`added` → `Added`）。"""
    return kind[:1].upper() + kind[1:]


def _type_line(lines: list) -> tuple:
    """(组名或 None, 剩余行)。首个非空行必须是 `type: <kind>`。"""
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        m = _TYPE_RE.match(line.strip())
        kind = m.group(1).lower() if m else None
        return (kind if kind in TYPES else None), lines[i + 1:]
    return None, []


def _split_entries(lines: list) -> tuple:
    """(条目块列表, 问题列表)：顶格 bullet 开新块，缩进行归上一块，其它非空行 = 问题。"""
    blocks: list = []
    problems: list = []
    for line in lines:
        if not line.strip():
            continue
        if line[:2] in _BULLET_PREFIXES:
            blocks.append([line.rstrip()])
        elif line[:1].isspace() and blocks:
            blocks[-1].append(line.rstrip())
        else:
            problems.append("loose line %r — every entry is a top-level `- ` bullet" % line[:60])
    return blocks, problems


def parse_fragment(name: str, text: str) -> tuple:
    """(Fragment 或 None, 问题列表)。问题列表为空 ⇔ 形状合法。"""
    problems: list = []
    if not _NAME_RE.match(name):
        problems.append("file name must be kebab-case `<slug>.md` (a-z, 0-9, hyphens)")
    kind, rest = _type_line(text.splitlines())
    if kind is None:
        problems.append("first line must be `type: <kind>` with kind in %s" % "|".join(TYPES))
    blocks, entry_problems = _split_entries(rest)
    problems.extend(entry_problems)
    if not blocks:
        problems.append("no entries — at least one `- ` bullet")
    if problems:
        return None, problems
    return Fragment(name, kind, blocks), []


def fragment_names(dirpath: str) -> list:
    """目录里应当是 fragment 的文件名（排序；README.md 与不存在的目录 → 跳过 / 空）。"""
    if not os.path.isdir(dirpath):
        return []
    return sorted(n for n in os.listdir(dirpath) if n not in SKIP_NAMES and not n.startswith("."))


def load_fragments(dirpath: str) -> tuple:
    """(合法 Fragment 列表——按文件名排序, 问题列表 `<name>: <problem>`)。"""
    fragments: list = []
    problems: list = []
    for name in fragment_names(dirpath):
        path = os.path.join(dirpath, name)
        if not os.path.isfile(path):
            problems.append("%s: not a file — changelog.d/ holds flat fragments only" % name)
            continue
        with open(path, "r", encoding="utf-8") as fh:
            fragment, issues = parse_fragment(name, fh.read())
        problems.extend("%s: %s" % (name, issue) for issue in issues)
        if fragment is not None:
            fragments.append(fragment)
    return fragments, problems


def section_lines(fragments: list) -> list:
    """拼成 `[Unreleased]` 段落的形状：按 TYPES 顺序 `### <Title>` + 该组全部条目 + 空行。"""
    out: list = []
    for kind in TYPES:
        blocks = [block for frag in fragments if frag.kind == kind for block in frag.blocks]
        if not blocks:
            continue
        out.append("### " + title_for(kind))
        for block in blocks:
            out.extend(block)
        out.append("")
    return out


def blob_sha(data: bytes) -> str:
    """git 的 blob 对象 id（`git hash-object` 同款）——比对「与 tag 里那一版逐字节相同」不需要 spawn git。"""
    header = ("blob %d\0" % len(data)).encode("ascii")
    return hashlib.sha1(header + data).hexdigest()


def _cmd_check(dirpath: str, stdout) -> int:
    fragments, problems = load_fragments(dirpath)
    for problem in problems:
        stdout.write("::error::%s/%s\n" % (FRAGMENT_DIR, problem))
    if problems:
        stdout.write("changelog fragments: FAIL — see changelog.d/README.md for the shape\n")
        return 1
    stdout.write("changelog fragments: ok (%d)\n" % len(fragments))
    return 0


def _cmd_render(dirpath: str, stdout) -> int:
    fragments, problems = load_fragments(dirpath)
    for problem in problems:
        sys.stderr.write("::warning::%s/%s (skipped)\n" % (FRAGMENT_DIR, problem))
    lines = section_lines(fragments)
    if lines:
        stdout.write("\n".join(lines).rstrip("\n") + "\n")
    return 0


def main(argv=None, stdout=None) -> int:
    stdout = stdout or sys.stdout
    parser = argparse.ArgumentParser(description="changelog.d fragments: check / render")
    parser.add_argument("cmd", choices=("check", "render"))
    parser.add_argument("dir", nargs="?", default=FRAGMENT_DIR)
    args = parser.parse_args(argv)
    if args.cmd == "check":
        return _cmd_check(args.dir, stdout)
    return _cmd_render(args.dir, stdout)


if __name__ == "__main__":
    sys.exit(main())
