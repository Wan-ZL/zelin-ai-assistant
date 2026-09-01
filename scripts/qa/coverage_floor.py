#!/usr/bin/env python3
"""总行覆盖率不下降门：act/ + server/ 的 line coverage ≥ qa/coverage_floor.txt。

法典：docs/CONTRACT.md §58.2（R2.3.3 的「覆盖率不低于 main」落成一个
committed 数字）。地板只经 PR 上调（自动棘轮：覆盖率高出地板
[coverage].ratchet_trigger 以上时，门在输出与 report 里给出建议新地板 =
当前值 − [coverage].ratchet_buffer，向下取 1 位小数）。
判例：tests/test_qa_coverage_floor.py。

用法：
    python3 scripts/qa/coverage_floor.py --coverage-json PATH [--report DIR]
"""

import argparse
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import qa_common  # noqa: E402

FLOOR_FILE = os.path.join(qa_common.REPO_ROOT, "qa", "coverage_floor.txt")


def evaluate_floor(percent, floor, trigger, buffer):
    """(ok, 建议新地板 or None)。percent/floor 都是 0–100 的百分数。"""
    if percent < floor:
        return False, None
    if percent >= floor + trigger:
        return True, math.floor((percent - buffer) * 10) / 10.0
    return True, None


def read_floor(path=FLOOR_FILE):
    """地板文件 = 单个数字一行（# 注释与空行忽略）。"""
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            text = line.split("#", 1)[0].strip()
            if text:
                return float(text)
    raise ValueError("no floor number in %s" % path)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coverage-json", required=True)
    parser.add_argument("--report", metavar="DIR")
    args = parser.parse_args(argv)

    cfg = qa_common.load_gates()["coverage"]
    with open(args.coverage_json, "r", encoding="utf-8") as fh:
        percent = float(json.load(fh)["totals"]["percent_covered"])
    floor = read_floor()
    ok, suggestion = evaluate_floor(percent, floor,
                                    float(cfg["ratchet_trigger"]),
                                    float(cfg["ratchet_buffer"]))
    lines = ["[coverage-floor] total %.2f%% vs floor %.1f%% -> %s"
             % (percent, floor, "OK" if ok else "FAIL")]
    if not ok:
        lines.append("[coverage-floor] coverage dropped below the committed floor"
                     " — add tests or (only with owner eyes) lower %s"
                     % os.path.relpath(FLOOR_FILE, qa_common.REPO_ROOT))
    if suggestion is not None:
        lines.append("[coverage-floor] coverage rose — suggest ratcheting the floor"
                     " up to %.1f (edit qa/coverage_floor.txt in a PR)" % suggestion)
    text = "\n".join(lines)
    print(text)
    if args.report:
        qa_common.write_report(args.report, "coverage_floor_verdict.txt", text + "\n")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
