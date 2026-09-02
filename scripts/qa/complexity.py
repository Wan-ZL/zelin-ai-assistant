#!/usr/bin/env python3
"""每函数圈复杂度硬门（≤ qa/gates.toml [complexity].max）。

法典：docs/CONTRACT.md §58.1。范围 act/ server/ scripts/（qa_common.PY_SCAN_DIRS）；
存量账本 qa/complexity_baseline.txt（shrink-only：新超线 FAIL、账上恶化 FAIL、
已达标仍挂账 FAIL）。判例：tests/test_qa_complexity_counter.py。

用法：
    python3 scripts/qa/complexity.py --check [--report DIR]   # 门（CI 与本地）
    python3 scripts/qa/complexity.py --list                   # 全量分数（降序）
    python3 scripts/qa/complexity.py --write-baseline         # 重铸账本（收账用）
    python3 scripts/qa/complexity.py --print-threshold        # 给 ruff C901 用
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import qa_common  # noqa: E402

BASELINE = os.path.join(qa_common.REPO_ROOT, "qa", "complexity_baseline.txt")


def scan(root=None):
    """{<相对路径>::<qualname>: CC}，全函数（含方法与嵌套函数）。"""
    root = root or qa_common.REPO_ROOT
    scores = {}
    for path in qa_common.iter_py_files(root):
        tree = qa_common.parse_file(path)
        if tree is None:
            continue
        rel = os.path.relpath(path, root).replace(os.sep, "/")
        for qual, node in qa_common.collect_functions(tree):
            scores["%s::%s" % (rel, qual)] = float(qa_common.cyclomatic_complexity(node))
    return scores


def _print_listing(scores):
    for key, score in sorted(scores.items(), key=lambda kv: (-kv[1], kv[0])):
        print("%4d  %s" % (int(score), key))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--write-baseline", action="store_true")
    parser.add_argument("--print-threshold", action="store_true")
    parser.add_argument("--report", metavar="DIR")
    args = parser.parse_args(argv)

    threshold = float(qa_common.load_gates()["complexity"]["max"])
    if args.print_threshold:
        print(int(threshold))
        return 0
    scores = scan()
    if args.list:
        _print_listing(scores)
        return 0
    if args.write_baseline:
        over = {k: v for k, v in scores.items() if v > threshold}
        print("wrote %d entries to %s"
              % (qa_common.write_ledger(BASELINE, over, "complexity"), BASELINE))
        return 0
    return qa_common.run_gate("complexity", scores, BASELINE, threshold,
                              tolerance=0.0, report_dir=args.report)


if __name__ == "__main__":
    sys.exit(main())
