#!/usr/bin/env python3
"""GitHub Release 正文 = changelog.d/ fragments + CHANGELOG legacy ``[Unreleased]`` 的增量（CONTRACT §56.1 / §56.7）。

    python3 scripts/ci/changelog_release_notes.py CHANGELOG.md [--previous PREV_CHANGELOG.md]
        [--fragments changelog.d] [--previous-fragments PREV_DIR/changelog.d]

发版从不改写任何文件（没有「把 Unreleased 改名为 [X.Y.Z]」这一步、也不删 fragment——
那两步都是并行 PR 互相 rebase 的根源）。发版历史住在 GitHub Releases + tag。所以
release.yml 取的是**增量**：本 tag 树里的条目（fragments ∪ legacy [Unreleased]）中、
上一个 tag 树里没有的那些——整块比较（bullet + 缩进续行），`### 组` 按 Keep a
Changelog 顺序（Added / Changed / Deprecated / Removed / Fixed / Security）合并
同名组、空组不留。没有上一个 tag（首个 release / --previous 缺）= 全部。

fragment 目录由 scripts/ci/changelog_fragments.py 解析；形状坏的 fragment 在这里
只 ::warning:: 并跳过（发版不因一个坏文件失败——PR 时的 `check` 门才是硬门）。
纯函数 ``unreleased_section`` / ``release_notes``（判例
tests/test_changelog_release_notes.py）；stdout 是 markdown，空 = 没有新条目
（release.yml 只在非空时把它放进正文）。
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import changelog_fragments as frags  # noqa: E402

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


def _starts_entry(line: str, blocks: list) -> bool:
    """``### 组`` 标题、顶格 bullet、或没有可归属的前块（首行 / 前块是标题）= 新块。"""
    if _H3_RE.match(line) or line[:2] in ("- ", "* "):
        return True
    return not blocks or bool(_H3_RE.match(blocks[-1][0]))


def _entries(lines: list) -> list:
    """把段落行切成条目块：``### 组`` 标题自成一块；``- `` / ``* `` 顶格 bullet 开新块，
    其后的缩进续行（子项、折行）归入同一块；空行不进块。"""
    blocks: list = []
    for line in lines:
        if not line.strip():
            continue
        if _starts_entry(line, blocks):
            blocks.append([line])
        else:
            blocks[-1].append(line)
    return blocks


def _key(block: list) -> str:
    return "\n".join(line.strip() for line in block)


def _group_key(heading: str) -> str:
    """``### Added`` → ``added``（同名组合并的键；大小写不敏感）。"""
    return heading[4:].strip().lower()


def _regroup(blocks: list) -> tuple:
    """(组 → 条目行, 组首次出现顺序, 组 → 原标题行)。legacy [Unreleased] 与 fragments 各带一套
    ``### `` 标题，同名组在这里合并；标题之前的裸条目归空键 ``""``。"""
    groups: dict = {}
    order: list = []
    headings: dict = {}
    key = ""
    for block in blocks:
        is_heading = bool(_H3_RE.match(block[0]))
        if is_heading:
            key = _group_key(block[0])
            headings.setdefault(key, block[0].strip())
        if key not in groups:
            groups[key] = []
            order.append(key)
        if not is_heading:
            groups[key].extend(block)
    return groups, order, headings


def _ordered_keys(groups: dict, order: list) -> list:
    """已知组按 Keep a Changelog 顺序，未知组按首次出现顺序跟在后面；空键（裸条目）不在其中。"""
    canonical = [k for k in frags.TYPES if k in groups]
    others = [k for k in order if k not in frags.TYPES]
    return canonical + [k for k in others if k]


def _render(groups: dict, order: list, headings: dict) -> list:
    """裸条目最前；然后各组 = 原标题行 + 条目，组间一个空行；空组不留。"""
    out: list = list(groups.get("", []))
    for key in _ordered_keys(groups, order):
        if not groups[key]:
            continue
        if out:
            out.append("")
        out.append(headings[key])
        out.extend(groups[key])
    return out


def release_notes(current: str, previous: str = "", fragment_lines=(), previous_fragment_lines=()) -> str:
    """本版正文：(current 的 [Unreleased] ∪ fragment_lines) 的**条目**中不在 (previous 的 [Unreleased] ∪
    previous_fragment_lines) 里的那些——整块比较（两个不同 bullet 共用同一句子项续行时子项不会被误删）；
    ``### `` 组按 Keep a Changelog 顺序合并同名组，空组不留。fragment_lines 是
    changelog_fragments.section_lines 的产物（``### Added`` + bullets 的形状）。"""
    now = _entries(unreleased_section(current)) + _entries(list(fragment_lines))
    before = _entries(unreleased_section(previous)) + _entries(list(previous_fragment_lines))
    seen = {_key(block) for block in before if not _H3_RE.match(block[0])}
    kept = [block for block in now if _H3_RE.match(block[0]) or _key(block) not in seen]
    return "\n".join(_render(*_regroup(kept)))


def _read_text(path) -> str:
    if path and Path(path).exists():
        return Path(path).read_text(encoding="utf-8")
    return ""


def _fragment_lines(dirpath, warn: bool) -> list:
    """目录 → section 形状的行；坏 fragment 跳过（warn=True 时 stderr 打 ::warning::）。"""
    if not dirpath:
        return []
    fragments, problems = frags.load_fragments(dirpath)
    if warn:
        for problem in problems:
            sys.stderr.write("::warning::%s/%s (skipped in release notes)\n" % (frags.FRAGMENT_DIR, problem))
    return frags.section_lines(fragments)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="release notes = fragments + [Unreleased] delta")
    parser.add_argument("changelog", help="CHANGELOG.md at the tag being released")
    parser.add_argument("--previous", help="CHANGELOG.md as of the previous tag (git show <tag>:CHANGELOG.md)")
    parser.add_argument("--fragments", help="changelog.d/ at the tag being released")
    parser.add_argument("--previous-fragments", help="changelog.d/ as of the previous tag (git archive <tag>)")
    args = parser.parse_args(argv)
    body = release_notes(
        _read_text(args.changelog),
        _read_text(args.previous),
        _fragment_lines(args.fragments, warn=True),
        _fragment_lines(args.previous_fragments, warn=False),
    )
    if body:
        sys.stdout.write(body + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
