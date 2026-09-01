#!/usr/bin/env python3
"""每函数 CRAP 硬门：CRAP(f) = CC(f)² × (1 − cov(f))³ + CC(f) ≤ [crap].max。

法典：docs/CONTRACT.md §58.2（owner 决策 D4：上限 6）。覆盖率原料 =
scripts/qa/run_coverage.sh 产出的 coverage JSON（act/ + server/，行覆盖）；
行覆盖按 AST 函数行段映射到函数（qa_common.span_coverage）。存量账本
qa/crap_baseline.txt（shrink-only + [crap].tolerance 抖动缓冲）。
判例：tests/test_qa_crap_formula.py。

用法：
    python3 scripts/qa/crap.py --check --coverage-json PATH [--report DIR]
    python3 scripts/qa/crap.py --list  --coverage-json PATH [--top N]
    python3 scripts/qa/crap.py --write-baseline --coverage-json PATH
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import qa_common  # noqa: E402

BASELINE = os.path.join(qa_common.REPO_ROOT, "qa", "crap_baseline.txt")


def scan(coverage_json, root=None):
    """(scores, details)：scores = {key: CRAP}；details = {key: (cc, cov)}。
    只测 coverage JSON 里出现的文件（= coverage --source=act,server 的范围）。"""
    root = root or qa_common.REPO_ROOT
    with open(coverage_json, "r", encoding="utf-8") as fh:
        files = json.load(fh).get("files", {})
    scores, details = {}, {}
    for rel in sorted(files):
        _scan_file(rel, files[rel], root, scores, details)
    return scores, details


def _scan_file(rel, filedata, root, scores, details):
    tree = qa_common.parse_file(os.path.join(root, rel))
    if tree is None:
        return
    executed = set(filedata.get("executed_lines", ()))
    missing = set(filedata.get("missing_lines", ()))
    relkey = rel.replace(os.sep, "/")
    for qual, node in qa_common.collect_functions(tree):
        cc = qa_common.cyclomatic_complexity(node)
        cov = qa_common.span_coverage(node, executed, missing)
        key = "%s::%s" % (relkey, qual)
        scores[key] = qa_common.crap_score(cc, cov)
        details[key] = (cc, cov)


def _offender_table(scores, details, top):
    """markdown 榜单（report artifact + --list 共用）。"""
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))[:top]
    lines = ["| CRAP | CC | cov | function |", "|---|---|---|---|"]
    for key, score in ranked:
        cc, cov = details[key]
        lines.append("| %.1f | %d | %.0f%% | `%s` |" % (score, cc, cov * 100, key))
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--write-baseline", action="store_true")
    parser.add_argument("--coverage-json", required=True)
    parser.add_argument("--report", metavar="DIR")
    parser.add_argument("--top", type=int, default=30)
    args = parser.parse_args(argv)

    cfg = qa_common.load_gates()["crap"]
    threshold, tolerance = float(cfg["max"]), float(cfg["tolerance"])
    scores, details = scan(args.coverage_json)
    if args.list:
        print(_offender_table(scores, details, args.top), end="")
        return 0
    if args.write_baseline:
        over = {k: v for k, v in scores.items() if v > threshold}
        print("wrote %d entries to %s"
              % (qa_common.write_ledger(BASELINE, over, "crap"), BASELINE))
        return 0
    if args.report:
        qa_common.write_report(args.report, "crap_report.md",
                               "# CRAP top offenders\n\n"
                               + _offender_table(scores, details, args.top))
    rc = qa_common.run_gate("crap", scores, BASELINE, threshold,
                            tolerance=tolerance, report_dir=args.report)
    return qa_common.soften_off_canonical(rc, sys.platform, "crap")


if __name__ == "__main__":
    sys.exit(main())
