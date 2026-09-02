#!/usr/bin/env python3
"""GitHub Release 正文 = CHANGELOG 的 ``[Unreleased]`` 增量（CONTRACT §56.2）。

    python3 scripts/changelog_release_notes.py CHANGELOG.md [--previous PREV_CHANGELOG.md]

PR 只往 ``## [Unreleased]`` 下写；文件在发版时**不改写**（没有「把 Unreleased
改名为 [X.Y.Z]」这一步——那一步正是六个 PR 互相 rebase 的根源之一）。发版历史
住在 GitHub Releases + tag。所以 release.yml 取的是**增量**：本 tag 的
[Unreleased] 段里、上一个 tag 的 [Unreleased] 段里没有的行（顺序保留、空标题
段落不留）。没有上一个 tag（首个 release / --previous 缺）= 整段。

纯函数 ``unreleased_section`` / ``release_notes``（判例
tests/test_changelog_release_notes.py）；stdout 是 markdown，空 = 没有新条目
（release.yml 只在非空时把它放进正文）。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_UNRELEASED_RE = re.compile(r"^## \[unreleased\]", re.I)
_H2_RE = re.compile(r"^## ")
_H3_RE = re.compile(r"^### ")


def unreleased_section(text: str) -> list:
    """``## [Unreleased]`` 之后、下一个 ``## `` 之前的行（不含标题本身）。没有该段 → []。"""
    lines = text.splitlines()
    out: list = []
    inside = False
    for line in lines:
        if _UNRELEASED_RE.match(line):
            inside = True
            continue
        if inside and _H2_RE.match(line):
            break
        if inside:
            out.append(line.rstrip())
    return out


class _Groups:
    """把 [Unreleased] 的行重新拼起来：空组标题不留、连续空行压缩、首尾空行去掉。"""

    def __init__(self):
        self.out: list = []
        self.pending_heading = None

    def _blank(self) -> None:
        if self.out and self.out[-1] != "":
            self.out.append("")

    def feed(self, line: str) -> None:
        if _H3_RE.match(line):
            self.pending_heading = line
        elif not line.strip():
            self._blank()
        else:
            self._flush_heading()
            self.out.append(line)

    def _flush_heading(self) -> None:
        if self.pending_heading is None:
            return
        self._blank()
        self.out.append(self.pending_heading)
        self.pending_heading = None

    def result(self) -> list:
        out = list(self.out)
        while out and out[-1] == "":
            out.pop()
        while out and out[0] == "":
            out.pop(0)
        return out


def _drop_empty_groups(lines: list) -> list:
    groups = _Groups()
    for line in lines:
        groups.feed(line)
    return groups.result()


def release_notes(current: str, previous: str = "") -> str:
    """本版正文：current 的 [Unreleased] 行中不在 previous 的 [Unreleased] 里的
    那些（``### `` 分组标题总是保留，随后由 _drop_empty_groups 清掉空组）。"""
    now = unreleased_section(current)
    seen = set(line.strip() for line in unreleased_section(previous) if line.strip())
    kept = [line for line in now if _H3_RE.match(line) or not line.strip() or line.strip() not in seen]
    return "\n".join(_drop_empty_groups(kept))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="[Unreleased] delta as release notes")
    parser.add_argument("changelog", help="CHANGELOG.md at the tag being released")
    parser.add_argument("--previous", help="CHANGELOG.md as of the previous tag (git show <tag>:CHANGELOG.md)")
    args = parser.parse_args(argv)
    current = Path(args.changelog).read_text(encoding="utf-8")
    previous = ""
    if args.previous and Path(args.previous).exists():
        previous = Path(args.previous).read_text(encoding="utf-8")
    body = release_notes(current, previous)
    if body:
        sys.stdout.write(body + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
