#!/usr/bin/env python3
"""防腐十条可机械化项的硬门（文件/函数/class 行数上限 + 模块 docstring 引 §）。

法典：docs/CONTRACT.md §58.3；阈值 truth = qa/gates.toml [hygiene]。范围：
  - 行数上限：act/ server/ scripts/ 的 .py（防腐 #1：文件 ≤2000、函数 ≤300、
    class ≤800）+ shell/ 的 .swift（≤1500）。mac/ 按 D3 豁免（退役中），
    tests/ 是判例不设门。
  - docstring 引 §（防腐 #5 前半）：act/** + server/** 的模块 docstring 必须
    含 `§<数字>`（__init__.py 豁免——版本占位/包壳没有行为可引）。
存量账本 qa/hygiene_baseline.txt（shrink-only：挂账文件不许再长）。
判例：tests/test_qa_hygiene_caps.py。

用法：
    python3 scripts/qa/hygiene.py --check [--report DIR]
    python3 scripts/qa/hygiene.py --list
    python3 scripts/qa/hygiene.py --write-baseline
"""

import argparse
import ast
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import qa_common  # noqa: E402

BASELINE = os.path.join(qa_common.REPO_ROOT, "qa", "hygiene_baseline.txt")
_SECTION_RE = re.compile(r"§\s*\d")
_DOCSTRING_DIRS = ("act", "server")


def _line_count(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return sum(1 for _ in fh)
    except OSError:
        return 0


def _scan_py_file(relpath, root, caps, scores):
    path = os.path.join(root, relpath)
    total = _line_count(path)
    if total > caps["max_file_lines_py"]:
        scores["file-lines:%s" % relpath] = float(total)
    tree = qa_common.parse_file(path)
    if tree is None:
        return
    for qual, node, kind in qa_common.collect_definitions(tree):
        _check_span(relpath, qual, node, kind, caps, scores)


def _check_span(relpath, qual, node, kind, caps, scores):
    cap = caps["max_function_lines"] if kind == "func" else caps["max_class_lines"]
    span = node.end_lineno - node.lineno + 1
    if span > cap:
        scores["%s-lines:%s::%s" % (kind, relpath, qual)] = float(span)


def _scan_swift_caps(root, cap, scores):
    base = os.path.join(root, "shell")
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = sorted(d for d in dirnames if d not in ("build", ".build"))
        for fn in sorted(filenames):
            if not fn.endswith(".swift"):
                continue
            path = os.path.join(dirpath, fn)
            total = _line_count(path)
            if total > cap:
                rel = os.path.relpath(path, root).replace(os.sep, "/")
                scores["file-lines:%s" % rel] = float(total)


def _scan_docstring(relpath, root, scores):
    if os.path.basename(relpath) == "__init__.py":
        return
    tree = qa_common.parse_file(os.path.join(root, relpath))
    if tree is None:
        return
    doc = ast.get_docstring(tree) or ""
    if not _SECTION_RE.search(doc):
        scores["docstring:%s" % relpath] = 1.0


def scan(root=None):
    """全部 hygiene 违例：{violation_key: 测量值}。"""
    root = root or qa_common.REPO_ROOT
    caps = qa_common.load_gates(os.path.join(root, "qa", "gates.toml"))["hygiene"]
    scores = {}
    for path in qa_common.iter_py_files(root):
        rel = os.path.relpath(path, root).replace(os.sep, "/")
        _scan_py_file(rel, root, caps, scores)
        if rel.split("/", 1)[0] in _DOCSTRING_DIRS:
            _scan_docstring(rel, root, scores)
    _scan_swift_caps(root, caps["max_file_lines_swift"], scores)
    return scores


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--write-baseline", action="store_true")
    parser.add_argument("--report", metavar="DIR")
    args = parser.parse_args(argv)

    scores = scan()
    if args.list:
        for key in sorted(scores):
            print("%s %s" % (key, int(scores[key])))
        return 0
    if args.write_baseline:
        print("wrote %d entries to %s"
              % (qa_common.write_ledger(BASELINE, scores, "hygiene"), BASELINE))
        return 0
    return qa_common.run_gate("hygiene", scores, BASELINE, threshold=0.0,
                              tolerance=0.0, report_dir=args.report)


if __name__ == "__main__":
    sys.exit(main())
