#!/usr/bin/env python3
"""release-on-merge 的 tag 算术（CONTRACT §56.2）——纯 stdin/stdout，零网络。

    git tag -l 'v[0-9]*' | python3 scripts/ci/release_tags.py next [--bump patch|minor|major]
    git tag -l 'v[0-9]*' | python3 scripts/ci/release_tags.py previous v0.48.16
    git tag -l 'v[0-9]*' | python3 scripts/ci/release_tags.py highest          # 最高的现有 tag，没有 → 空 + rc 1
    printf '%s' "$SUBJECT"  | python3 scripts/ci/release_tags.py pr-number      # "… (#141)" → 141，没有 → 空 + rc 1
    printf '%s\n' "$LABELS" | python3 scripts/ci/release_tags.py bump-from-labels  # release: minor|major → minor|major，否则 patch

逻辑全在 act.lib.version（判例 tests/test_version_tags.py）；这里只是壳。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from act.lib import version as ver  # noqa: E402


def _lines(stream) -> list:
    return [line.strip() for line in stream.read().splitlines() if line.strip()]


def _cmd_next(args, stdin) -> tuple:
    return 0, ver.next_tag(_lines(stdin), args.bump)


def _cmd_previous(args, stdin) -> tuple:
    found = ver.previous_tag(_lines(stdin), args.current)
    return (0 if found else 1), (found or "")


def _cmd_highest(args, stdin) -> tuple:
    top = ver.highest_tag(_lines(stdin))
    return (0 if top else 1), ("v" + ver.format_version(top) if top else "")


def _cmd_pr_number(args, stdin) -> tuple:
    number = ver.pr_number_from_subject(stdin.read())
    return (0 if number else 1), ("%d" % number if number else "")


def _cmd_bump_from_labels(args, stdin) -> tuple:
    return 0, ver.bump_from_labels(_lines(stdin))


_COMMANDS = {
    "next": _cmd_next,
    "previous": _cmd_previous,
    "highest": _cmd_highest,
    "pr-number": _cmd_pr_number,
    "bump-from-labels": _cmd_bump_from_labels,
}


def main(argv=None, stdin=None, stdout=None) -> int:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    parser = argparse.ArgumentParser(description="tag arithmetic for release-on-merge")
    sub = parser.add_subparsers(dest="cmd", required=True)
    nxt = sub.add_parser("next")
    nxt.add_argument("--bump", choices=ver.BUMPS, default="patch")
    prev = sub.add_parser("previous")
    prev.add_argument("current")
    for name in ("highest", "pr-number", "bump-from-labels"):
        sub.add_parser(name)
    args = parser.parse_args(argv)
    rc, text = _COMMANDS[args.cmd](args, stdin)
    stdout.write(text + "\n")
    return rc


if __name__ == "__main__":
    sys.exit(main())
