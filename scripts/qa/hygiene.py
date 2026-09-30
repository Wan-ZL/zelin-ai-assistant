#!/usr/bin/env python3
"""防腐十条可机械化项的硬门（文件/函数/class 行数上限 + 模块 docstring 引 §）。

法典：docs/CONTRACT.md §58.3；阈值 truth = qa/gates.toml [hygiene]。范围：
  - 行数上限：act/ server/ scripts/ 的 .py（防腐 #1：文件 ≤2000、函数 ≤300、
    class ≤800）+ shell/ 的 .swift（≤1500）。mac/ 按 D3 豁免（退役中），
    tests/ 是判例不设门。
  - docstring 引 §（防腐 #5 前半）：act/** + server/** 的模块 docstring 必须
    含 `§<数字>`（__init__.py 豁免——版本占位/包壳没有行为可引）。
  - 判例草稿目录（防腐 #4 的测试侧；issue #436）：tests/** 里 `tempfile.mkdtemp`
    只准住 tests/__init__.py（整次 run 的沙箱根，退出时整树删）与
    tests/scratch_testkit.py（scratch_dir 工厂，cleanup 阶段删）；别处每一处裸调用
    记 `mkdtemp:<文件>`，分 = 该文件的调用数。2026-09-19 owner 机器的 $TMPDIR 里
    215k 个泄漏目录全部出自这些调用点。
存量账本 qa/hygiene_baseline.txt（shrink-only：挂账文件不许再长）。
判例：tests/test_qa_hygiene_caps.py、tests/test_qa_hygiene_test_scratch.py。

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
# issue #436：tests/ 里准直接 mkdtemp 的两处（沙箱根 + scratch_dir 工厂）。
_MKDTEMP_ALLOWED = frozenset({"tests/__init__.py", "tests/scratch_testkit.py"})


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


def _is_mkdtemp_call(node):
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
    return name == "mkdtemp"


def _raw_mkdtemp_count(path):
    """文件里 `mkdtemp(...)` 调用数（`tempfile.mkdtemp` 与裸 `mkdtemp` 两形）。
    先做文本预筛：530+ 个判例文件里只解析提到 mkdtemp 的那几个。"""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            if "mkdtemp" not in fh.read():
                return 0
    except OSError:
        return 0
    tree = qa_common.parse_file(path)
    if tree is None:
        return 0
    return sum(1 for node in ast.walk(tree) if _is_mkdtemp_call(node))


def _scan_test_scratch(root, scores):
    """tests/** 的裸 mkdtemp（issue #436）：白名单外每个文件记一条，分 = 调用数。"""
    for path in qa_common.iter_py_files(root, rel_dirs=("tests",)):
        rel = os.path.relpath(path, root).replace(os.sep, "/")
        if rel in _MKDTEMP_ALLOWED:
            continue
        count = _raw_mkdtemp_count(path)
        if count:
            scores["mkdtemp:%s" % rel] = float(count)


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
    _scan_test_scratch(root, scores)
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
