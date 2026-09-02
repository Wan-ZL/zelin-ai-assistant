#!/usr/bin/env python3
"""CI 门「Version pins untouched」（CONTRACT §56.1 / §56.7）：PR 不许手 bump 版本，也不许手写共享账本行。

    git diff HEAD^1 HEAD | python3 scripts/ci/version_pins_check.py --latest-tag v0.48.16 [--legacy-base]

对一份 unified diff 判决（纯函数 ``check``，判例 tests/test_version_pins_check.py）：
  - ios/project.yml、ios/…/project.pbxproj：任何 ``MARKETING_VERSION`` 行的增删 = FAIL
    （提交的永远是占位 0.0.0-dev，构建前才 sed）。
  - act/__init__.py：``__version__ = "…"`` 那一行只允许**刷新到当前最新 tag**
    （chore PR 追平回落常量）；改成别的值 = FAIL（那是手 bump）。
  - CHANGELOG.md：新增 ``## [X.Y.Z]`` 版本标题或 ``[X.Y.Z]: https://…`` 比较链接 = FAIL
    （发版历史住在 GitHub Releases + tag）；新增顶格 bullet（``- `` / ``* ``）或 ``### 组``
    标题 = FAIL——`[Unreleased]` 自 §56.7 起**只减不增**（legacy 文本冻结），新记录写
    ``changelog.d/<slug>.md`` fragment；删行随时放行（chore PR 清已发版的旧条目）。
  - docs/design/vnext2-plan.md：新增 ``| YYYY-MM-DD |`` 开头的表格行 = FAIL——§8 进度表冻结，
    新行写 ``docs/design/progress/<date>-<slug>.md`` fragment（scripts/ci/progress_log.py 渲染）。
  - act/_version.py 出现在 diff 里 = FAIL（生成文件，git-ignored）。

**过渡条款**（``--legacy-base``）：PR 的 merge-base 早于本门诞生（那一刻的树里
还没有本脚本）= 带着旧式手 bump 的在飞 PR，只打印会被拦的内容、不判 FAIL；
rebase 过本门之后新规则生效。workflow 用 ``git cat-file -e <merge-base>:scripts/ci/version_pins_check.py``
判定，不靳任何 sha。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from act.lib import version as ver  # noqa: E402

INIT_FILE = "act/__init__.py"
STAMP_FILE = "act/_version.py"
CHANGELOG = "CHANGELOG.md"
PLAN_FILE = "docs/design/vnext2-plan.md"
_HEADING_RE = re.compile(r"^## \[\d+\.\d+\.\d+")
_LINK_RE = re.compile(r"^\[\d+\.\d+\.\d+\]: https?://")
_ENTRY_RE = re.compile(r"^(- |\* |### )")
_PLAN_ROW_RE = re.compile(r"^\| *\d{4}-\d{2}-\d{2} *\|")
_INIT_LINE_RE = re.compile(r'^__version__ = "([^"]*)"')
_FILE_RE = re.compile(r"^\+\+\+ b/(.+)$")


def _iter_changes(diff_text: str):
    """(文件路径, 符号 +/-, 行内容) 逐条产出；``+++``/``---`` 头与上下文行跳过。"""
    current = None
    for raw in diff_text.splitlines():
        m = _FILE_RE.match(raw)
        if m:
            current = m.group(1).strip()
            continue
        if raw.startswith(("+++", "---")) or current is None:
            continue
        if raw[:1] in ("+", "-"):
            yield current, raw[0], raw[1:]


def _pin_problem(path: str, line: str):
    if path in ver.PIN_FILES and "MARKETING_VERSION" in line:
        return "%s: MARKETING_VERSION edited — it stays %s, stamped at build (never by a PR)" % (
            path, ver.PIN_PLACEHOLDER)
    return None


def _changelog_problem(sign: str, line: str):
    if sign != "+":
        return None
    if _HEADING_RE.match(line) or _LINK_RE.match(line):
        return "%s: version heading/link added (%r) — releases are tags, the file never gets a version" % (
            CHANGELOG, line[:60])
    if _ENTRY_RE.match(line):
        return ("%s: entry added (%r) — [Unreleased] is frozen (shrink-only, CONTRACT §56.7); write "
                "changelog.d/<kebab-slug>.md instead (first line `type: added|changed|fixed|removed|security`, "
                "then `- ` bullets)" % (CHANGELOG, line[:60]))
    return None


def _plan_problem(sign: str, line: str):
    if sign == "+" and _PLAN_ROW_RE.match(line):
        return ("%s: progress row added (%r) — the §8 table is frozen (CONTRACT §56.7); write "
                "docs/design/progress/<YYYY-MM-DD>-<slug>.md instead (rendered by scripts/ci/progress_log.py)"
                % (PLAN_FILE, line[:60]))
    return None


def _init_problem(sign: str, line: str, latest: str):
    m = _INIT_LINE_RE.match(line)
    if not m or sign != "+":
        return None
    if ver.parse_tag(m.group(1)) == ver.parse_tag(latest or ""):
        return None
    return "%s: __version__ fallback changed to %r — hand bumps are rejected; it may only be refreshed to the latest tag (%s)" % (
        INIT_FILE, m.group(1), latest or "?")


def _init_balance(path: str, sign: str, line: str, balance: dict) -> None:
    """记 act/__init__.py 里 ``__version__`` 行的增删数——只删不加 = 回落常量没了。"""
    if path == INIT_FILE and _INIT_LINE_RE.match(line):
        balance[sign] = balance.get(sign, 0) + 1


def _problem_for(path: str, sign: str, line: str, latest: str):
    """一条 diff 行的违规描述（合法 = None）。"""
    if path == STAMP_FILE:
        return "%s: generated stamp must never be committed (git-ignored)" % STAMP_FILE
    if path == CHANGELOG:
        return _changelog_problem(sign, line)
    if path == PLAN_FILE:
        return _plan_problem(sign, line)
    if path == INIT_FILE:
        return _init_problem(sign, line, latest)
    return _pin_problem(path, line)


def check(diff_text: str, latest_tag: str) -> list:
    """违规描述列表（空 = 通过）。"""
    problems = []
    balance: dict = {}
    for path, sign, line in _iter_changes(diff_text):
        _init_balance(path, sign, line, balance)
        problem = _problem_for(path, sign, line, latest_tag)
        if problem:
            problems.append(problem)
    if balance.get("-", 0) > balance.get("+", 0):
        problems.append("%s: the __version__ fallback line was removed — keep exactly one" % INIT_FILE)
    return sorted(set(problems))


def _report(problems: list, legacy: bool, stdout) -> int:
    if not problems:
        stdout.write("version pins untouched: ok\n")
        return 0
    marker = "::notice::" if legacy else "::error::"
    for p in problems:
        stdout.write(marker + p + "\n")
    if legacy:
        stdout.write("legacy PR (merge-base predates the gate): hand bumps tolerated until it rebases past the cutover\n")
        return 0
    stdout.write("version pins untouched: FAIL — versions come from git tags (CONTRACT §56.1) and release notes / "
                 "progress rows are fragments (§56.7): drop the bump, move the text into changelog.d/ or "
                 "docs/design/progress/\n")
    return 1


def main(argv=None, stdin=None, stdout=None) -> int:
    parser = argparse.ArgumentParser(description="reject hand-bumped versions in a PR diff")
    parser.add_argument("--latest-tag", default="", help="highest existing vX.Y.Z tag on main")
    parser.add_argument("--legacy-base", action="store_true",
                        help="the PR's merge-base predates this gate: report, never fail")
    args = parser.parse_args(argv)
    problems = check((stdin or sys.stdin).read(), args.latest_tag)
    return _report(problems, args.legacy_base, stdout or sys.stdout)


if __name__ == "__main__":
    sys.exit(main())
