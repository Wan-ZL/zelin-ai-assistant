#!/usr/bin/env python3
"""vnext2-plan §8 进度日志 fragments（CONTRACT §56.7；docs/design/progress/）：校验 + 按需渲染成表。

    python3 scripts/ci/progress_log.py check  [DIR]                  # 全部 fragment 形状合法 → rc 0；否则 ::error:: + rc 1
    python3 scripts/ci/progress_log.py render [DIR] [--plan PATH]    # §8 完整表（plan 里的历史行 + fragments）→ stdout

**为什么有它**：`docs/design/vnext2-plan.md` §8 的表每个 PR 追加一行，全部追加在表尾 =
并行 PR 的相邻行冲突（与 CHANGELOG [Unreleased] 同一类）。自本协议起 §8 里已有的行
是历史、原样冻结；新行 = `docs/design/progress/<YYYY-MM-DD>-<slug>.md` 一个文件，渲染
只在需要读全表时按需做（stdout；**不**写回 plan——写回又会制造同一种冲突）。

**文件形状**（日期取自文件名；README.md 免检）：

    pr: `ci/changelog-fragments`（PR #NNN）      ← 头部三行 key: value，顺序不限
    phase: 横切（流程；§56）
    law: §56.1 / §56.7（新增）
                                                ← 空行
    做了什么——正文，可多段；渲染进表时段落用空格连、`|` 转义为 `\\|`

纯函数 ``parse_fragment`` / ``render_rows`` / ``historical_rows``；判例
tests/test_progress_log.py。stdlib-only。
"""
from __future__ import annotations

import argparse
import os
import re
import sys

PROGRESS_DIR = "docs/design/progress"
PLAN_FILE = "docs/design/vnext2-plan.md"
KEYS = ("pr", "phase", "law")
SKIP_NAMES = frozenset({"README.md"})
DEFAULT_HEADER = ("| 日期 | PR / 分支 | 阶段 | 做了什么 | 法典 |", "|---|---|---|---|---|")
_NAME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-[a-z0-9]+(?:-[a-z0-9]+)*\.md$")
_KEY_RE = re.compile(r"^([a-z]+):\s*(.*)$")
_SECTION8_RE = re.compile(r"^## 8\.")
_H2_RE = re.compile(r"^## ")


def _header_and_body(lines: list) -> tuple:
    """(头部 {key: value}, 正文行, 问题)。头部 = 首个空行之前的 `key: value` 行。"""
    header: dict = {}
    problems: list = []
    i = 0
    while i < len(lines) and lines[i].strip():
        m = _KEY_RE.match(lines[i])
        if not m or m.group(1) not in KEYS:
            problems.append("header line %r — expected `pr:` / `phase:` / `law:`" % lines[i][:60])
        else:
            header[m.group(1)] = m.group(2).strip()
        i += 1
    return header, lines[i:], problems


def parse_fragment(name: str, text: str) -> tuple:
    """(行 dict {date, pr, phase, law, body} 或 None, 问题列表)。"""
    problems: list = []
    m = _NAME_RE.match(name)
    if not m:
        problems.append("file name must be `<YYYY-MM-DD>-<kebab-slug>.md`")
    header, body_lines, header_problems = _header_and_body(text.splitlines())
    problems.extend(header_problems)
    problems.extend("missing `%s:` in the header" % key for key in KEYS if not header.get(key))
    body = _cell("\n".join(body_lines))
    if not body:
        problems.append("empty body — say what the PR did (the 做了什么 column)")
    if problems:
        return None, problems
    row = {"date": m.group(1), "body": body}
    row.update((key, _cell(header[key])) for key in KEYS)
    return row, []


def _cell(text: str) -> str:
    """多段正文 → 单个表格单元：段内换行与段间空行都压成一个空格，`|` 转义。"""
    return " ".join(text.split()).replace("|", "\\|")


def fragment_names(dirpath: str) -> list:
    if not os.path.isdir(dirpath):
        return []
    return sorted(n for n in os.listdir(dirpath) if n not in SKIP_NAMES and not n.startswith("."))


def load_rows(dirpath: str) -> tuple:
    """(行列表——按 (日期, 文件名) 排序, 问题列表 `<name>: <problem>`)。"""
    rows: list = []
    problems: list = []
    for name in fragment_names(dirpath):
        path = os.path.join(dirpath, name)
        if not os.path.isfile(path):
            problems.append("%s: not a file — progress/ holds flat fragments only" % name)
            continue
        with open(path, "r", encoding="utf-8") as fh:
            row, issues = parse_fragment(name, fh.read())
        problems.extend("%s: %s" % (name, issue) for issue in issues)
        if row is not None:
            rows.append(row)
    rows.sort(key=lambda r: r["date"])
    return rows, problems


def render_rows(rows: list) -> list:
    return ["| %s | %s | %s | %s | %s |" % (r["date"], r["pr"], r["phase"], r["body"], r["law"]) for r in rows]


def _section8_lines(plan_text: str) -> list:
    """plan 里 `## 8.` 之后、下一个 `## ` 之前的行（没有 §8 → []）。"""
    lines = plan_text.splitlines()
    starts = [i for i, line in enumerate(lines) if _SECTION8_RE.match(line)]
    if not starts:
        return []
    body = lines[starts[0] + 1:]
    ends = [i for i, line in enumerate(body) if _H2_RE.match(line)]
    return body[:ends[0]] if ends else body


def historical_rows(plan_text: str) -> list:
    """§8 里的表格行（含表头两行）——以 `|` 开头的行。"""
    return [line.rstrip() for line in _section8_lines(plan_text) if line.startswith("|")]


def render_table(plan_text: str, rows: list) -> str:
    """完整 §8 表 = plan 的历史行（表头 + 已冻结的行）+ fragments 的行。plan 没表 → 默认表头。"""
    history = historical_rows(plan_text)
    if len(history) < 2:
        history = list(DEFAULT_HEADER)
    return "\n".join(history + render_rows(rows))


def _read(path: str) -> str:
    if not os.path.isfile(path):
        return ""
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def _cmd_check(dirpath: str, stdout) -> int:
    rows, problems = load_rows(dirpath)
    for problem in problems:
        stdout.write("::error::%s/%s\n" % (PROGRESS_DIR, problem))
    if problems:
        stdout.write("progress fragments: FAIL — see docs/design/progress/README.md for the shape\n")
        return 1
    stdout.write("progress fragments: ok (%d)\n" % len(rows))
    return 0


def _cmd_render(dirpath: str, plan: str, stdout) -> int:
    rows, problems = load_rows(dirpath)
    for problem in problems:
        sys.stderr.write("::warning::%s/%s (skipped)\n" % (PROGRESS_DIR, problem))
    stdout.write(render_table(_read(plan), rows) + "\n")
    return 0


def main(argv=None, stdout=None) -> int:
    stdout = stdout or sys.stdout
    parser = argparse.ArgumentParser(description="vnext2-plan §8 progress fragments: check / render")
    parser.add_argument("cmd", choices=("check", "render"))
    parser.add_argument("dir", nargs="?", default=PROGRESS_DIR)
    parser.add_argument("--plan", default=PLAN_FILE, help="plan file whose §8 rows are the frozen history")
    args = parser.parse_args(argv)
    if args.cmd == "check":
        return _cmd_check(args.dir, stdout)
    return _cmd_render(args.dir, args.plan, stdout)


if __name__ == "__main__":
    sys.exit(main())
