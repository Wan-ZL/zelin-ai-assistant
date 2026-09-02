#!/usr/bin/env python3
"""qa/ 账本与阈值对 PR base 的 shrink-only 差分门。

法典：docs/CONTRACT.md §58.4。门内的三态判决（qa_common.compare_with_ledger）
只看「测量 vs 账本」，看不见「账本自己长了」——一个 PR 可以新增债务并同 PR
把它写进 qa/*_baseline.txt，三态判决照样全绿（f2a54c1 审查 blocker 1 的活
演示）。本门补上另一半：把 head 的 qa/ 文件与 merge-base 的版本逐键比较——
  - qa/*_baseline.txt 加键 / 抬分 / 整文件删除 = FAIL（账本只许缩）
  - qa/coverage_floor.txt 下调 / 删除 = FAIL（地板只许升）
  - qa/gates.toml 阈值放宽 / 删键 = FAIL（方向表 _LOOSEN_UP 认「涨 = 放宽」；
    不在方向表的键一律 fail-closed——新旋钮必须先在这里declare方向）
base 上不存在的文件不比（账本出生的 PR 免比——门从上线第一天就是绿的，D15）。
判例：tests/test_qa_ledger_diff.py。

用法：
    python3 scripts/qa/ledger_diff.py --base HEAD^1        # CI（merge ref 的第一父 = 当前 main）
    python3 scripts/qa/ledger_diff.py --base origin/main   # 本地（自动取 merge-base）
"""

import argparse
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import qa_common  # noqa: E402

FLOOR_REL = "qa/coverage_floor.txt"
GATES_REL = "qa/gates.toml"

# qa/gates.toml 的方向表：这些键「数值变大 = 门变松」。新增旋钮必须同 PR
# 在此声明方向（判例 tests/test_qa_ledger_diff.py 钉死：表外数字键改动即 FAIL）。
_LOOSEN_UP = frozenset({
    ("complexity", "max"),
    ("crap", "max"),
    ("crap", "tolerance"),
    ("coverage", "ratchet_trigger"),
    ("coverage", "ratchet_buffer"),
    ("hygiene", "max_file_lines_py"),
    ("hygiene", "max_file_lines_swift"),
    ("hygiene", "max_function_lines"),
    ("hygiene", "max_class_lines"),
})


# --------------------------------------------------------------------------- #
# 纯比较器（判例直接喂文本；git 只住在下面的 plumbing 里）
# --------------------------------------------------------------------------- #

def diff_baseline(name, base_text, head_text):
    """一本账本 vs base：加键 / 抬分 / 删文件的发现列表（空 = 干净）。"""
    if base_text is None:
        return []
    if head_text is None:
        return ["GROW: %s deleted (retiring a ledger is an owner decision)" % name]
    base = qa_common.parse_ledger_text(base_text)
    head = qa_common.parse_ledger_text(head_text)
    findings = ["GROW: %s added key %s (=%s)"
                % (name, key, qa_common.format_score(head[key]))
                for key in sorted(head) if key not in base]
    findings += ["GROW: %s raised %s %s -> %s"
                 % (name, key, qa_common.format_score(base[key]),
                    qa_common.format_score(head[key]))
                 for key in sorted(head) if key in base and head[key] > base[key]]
    return findings


def diff_floor(base_text, head_text):
    """覆盖率地板 vs base：只许升不许降（删除 = 降到没有）。"""
    if base_text is None:
        return []
    if head_text is None:
        return ["FLOOR: %s deleted" % FLOOR_REL]
    base = qa_common.parse_floor_text(base_text)
    head = qa_common.parse_floor_text(head_text)
    if head < base:
        return ["FLOOR: %s lowered %s -> %s"
                % (FLOOR_REL, qa_common.format_score(base), qa_common.format_score(head))]
    return []


def _diff_gate_pair(pair, old, new):
    """单个阈值键的判决（改动过才进来）。"""
    label = "[%s].%s" % pair
    if new is None:
        return ["LOOSEN: %s threshold %s removed (was %s)" % (GATES_REL, label, old)]
    if pair in _LOOSEN_UP:
        if new > old:
            return ["LOOSEN: %s threshold %s raised %s -> %s"
                    % (GATES_REL, label, old, new)]
        return []
    return ["LOOSEN: %s %s changed %s -> %s (direction not declared in"
            " ledger_diff._LOOSEN_UP — fail closed)" % (GATES_REL, label, old, new)]


def diff_gates(base_text, head_text):
    """qa/gates.toml vs base：放宽 / 删键 / 未声明方向的改动都 FAIL。"""
    if base_text is None:
        return []
    if head_text is None:
        return ["LOOSEN: %s deleted" % GATES_REL]
    base = _flatten_gates(qa_common.parse_gates_text(base_text))
    head = _flatten_gates(qa_common.parse_gates_text(head_text))
    findings = []
    for pair in sorted(base):
        if head.get(pair) != base[pair]:
            findings += _diff_gate_pair(pair, base[pair], head.get(pair))
    return findings


def _flatten_gates(sections):
    return {(section, key): value
            for section, keys in sections.items()
            for key, value in keys.items()}


def collect_findings(read_base, read_head, baseline_names):
    """全部发现（注入缝：read_base/read_head 是 relpath → 文本 or None）。"""
    findings = []
    for name in sorted(baseline_names):
        findings += diff_baseline(name, read_base(name), read_head(name))
    findings += diff_floor(read_base(FLOOR_REL), read_head(FLOOR_REL))
    findings += diff_gates(read_base(GATES_REL), read_head(GATES_REL))
    return findings


# --------------------------------------------------------------------------- #
# git plumbing（真实入口；测试不走这里）
# --------------------------------------------------------------------------- #

def _git(args):
    return subprocess.check_output(
        ["git"] + args, cwd=qa_common.REPO_ROOT, stderr=subprocess.DEVNULL
    ).decode("utf-8", "replace")


def _show(rev, relpath):
    try:
        return _git(["show", "%s:%s" % (rev, relpath)])
    except subprocess.CalledProcessError:
        return None  # base 上不存在 = 出生，免比


def _base_baseline_names(rev):
    try:
        listing = _git(["ls-tree", "-r", "--name-only", rev, "--", "qa"])
    except subprocess.CalledProcessError:
        return set()
    return {p for p in listing.splitlines() if p.endswith("_baseline.txt")}


def _head_baseline_names():
    qa_dir = os.path.join(qa_common.REPO_ROOT, "qa")
    if not os.path.isdir(qa_dir):
        return set()
    return {"qa/" + fn for fn in sorted(os.listdir(qa_dir))
            if fn.endswith("_baseline.txt")}


def _read_worktree(relpath):
    path = os.path.join(qa_common.REPO_ROOT, relpath)
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True,
                        help="base rev（与 HEAD 取 merge-base 后比较）")
    parser.add_argument("--report", metavar="DIR")
    args = parser.parse_args(argv)

    base = _git(["merge-base", "HEAD", args.base]).strip()
    names = _base_baseline_names(base) | _head_baseline_names()
    findings = collect_findings(lambda rel: _show(base, rel), _read_worktree, names)
    lines = ["[ledger-diff] vs base %s: %d finding(s)" % (base[:12], len(findings))]
    lines += ["  " + finding for finding in findings]
    lines.append("[ledger-diff] %s" % ("FAIL" if findings else "OK"))
    text = "\n".join(lines)
    print(text)
    if findings:
        print("[ledger-diff] the qa/ ledgers only shrink (§58.4): fix new debt"
              " in the code, never enroll it; loosening a threshold or lowering"
              " the floor is an owner decision — amend docs/CONTRACT.md §58 in"
              " the same PR")
    if args.report:
        qa_common.write_report(args.report, "ledger_diff_verdict.txt", text + "\n")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
