#!/usr/bin/env python3
"""test-code skill · 最小尺子（stdlib ast）：每函数圈复杂度 + 函数/文件行数。

**fallback only**：项目有自己的尺子时 runner 优先用项目的（本 repo =
scripts/qa/complexity.py + hygiene.py，阈值 truth = qa/gates.toml，
docs/CONTRACT.md §58.1 / §58.3）。本文件不定义第二套阈值：命令行默认值只在
项目零配置时生效，报告里注明「Bob-strict = 6」。计数口径对齐 §58.1：
1 + if/elif/for/while/except/assert/三元/and-or（n−1）/comprehension-if/
match-case；with-as 不算；嵌套 def 不计入外层（各自成账）。

用法：
    complexity_min.py [--max-cc N] [--max-func-lines N] [--max-file-lines N]
                      [--only cc|lengths] [--baseline FILE] [--write-baseline]
                      [--root DIR] [--json] PATH...
退出码：0 无新违例；1 有新违例（new/worse vs 账本）；2 输入不可读/语法错
（fail closed：自制检查器读不到 = 失败，不是通过）。
判例：tests/test_skill_test_code_complexity_min.py（含负控制）。
"""

import argparse
import ast
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ladder_common as lc  # noqa: E402

DEFAULTS = {"max_cc": 10, "max_func_lines": 100, "max_file_lines": 1000}
STRICT_NOTE = "skill defaults; Bob-strict = 6 (this repo pins it in qa/gates.toml)"

_ONE_POINT = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.ExceptHandler,
              ast.Assert, ast.IfExp) + tuple(
    node for node in (getattr(ast, "match_case", None),) if node is not None)
_FUNC_NODES = (ast.FunctionDef, ast.AsyncFunctionDef)


def _decision_points(node):
    if isinstance(node, _ONE_POINT):
        return 1
    if isinstance(node, ast.BoolOp):
        return len(node.values) - 1
    if isinstance(node, ast.comprehension):
        return len(node.ifs)
    return 0


def cyclomatic_complexity(func):
    """1 + 决策点；嵌套 def 跳过（各自成账），lambda 留在外层。"""
    total = 1
    stack = list(ast.iter_child_nodes(func))
    while stack:
        node = stack.pop()
        if isinstance(node, _FUNC_NODES):
            continue
        total += _decision_points(node)
        stack.extend(ast.iter_child_nodes(node))
    return total


class _Collector(ast.NodeVisitor):
    """按定义顺序收 (qualname, func_node)；class 只贡献 qualname 前缀。"""

    def __init__(self):
        self.stack = []
        self.found = []

    def _named(self, node, is_func):
        if is_func:
            self.found.append((".".join(self.stack + [node.name]), node))
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    def visit_ClassDef(self, node):
        self._named(node, False)

    def visit_FunctionDef(self, node):
        self._named(node, True)

    visit_AsyncFunctionDef = visit_FunctionDef


def collect_functions(tree):
    collector = _Collector()
    collector.visit(tree)
    return collector.found


def measure_source(source, filename="<source>"):
    """→ {"functions": [(qual, cc, lines, lineno, end_lineno)], "file_lines": n}；
    语法错抛 SyntaxError（调用方 fail closed）。"""
    tree = ast.parse(source, filename=filename)
    functions = [(qual, cyclomatic_complexity(node), node.end_lineno - node.lineno + 1,
                  node.lineno, node.end_lineno)
                 for qual, node in collect_functions(tree)]
    return {"functions": functions, "file_lines": len(source.splitlines())}


def _over(kind, key, value, cap):
    return {"%s:%s" % (kind, key): float(value)} if value > cap else {}


def violations_for(relpath, measured, caps, only=None):
    """超线项 {key: 测量值}；key 形 cc:path::qual / func-lines:path::qual /
    file-lines:path（不含行号：无关编辑不移账）。"""
    out = {}
    if only != "cc":
        out.update(_over("file-lines", relpath, measured["file_lines"], caps["max_file_lines"]))
    for qual, cc, lines, _start, _end in measured["functions"]:
        key = "%s::%s" % (relpath, qual)
        if only != "lengths":
            out.update(_over("cc", key, cc, caps["max_cc"]))
        if only != "cc":
            out.update(_over("func-lines", key, lines, caps["max_func_lines"]))
    return out


def _walk_py(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if lc.keep_dir(d))
        for fn in sorted(filenames):
            if fn.endswith(".py"):
                yield os.path.join(dirpath, fn)


def iter_py_files(paths):
    for path in paths:
        if os.path.isfile(path):
            yield path
        else:
            for found in _walk_py(path):
                yield found


def scan(paths, root, caps, only=None):
    """→ (violations, errors)；errors=[(rel, msg)]，非空即 fail closed。"""
    violations, errors = {}, []
    real_root = os.path.realpath(root)
    for path in iter_py_files(paths):
        # realpath 两边都做：macOS 的 /var → /private/var 符号链接会让 relpath 爬出 ../../
        rel = os.path.relpath(os.path.realpath(path), real_root).replace(os.sep, "/")
        try:
            with open(path, "r", encoding="utf-8") as fh:
                measured = measure_source(fh.read(), rel)
        except (OSError, SyntaxError, ValueError) as exc:
            errors.append((rel, "%s: %s" % (type(exc).__name__, exc)))
            continue
        violations.update(violations_for(rel, measured, caps, only))
    return violations, errors


def _parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("paths", nargs="+")
    parser.add_argument("--max-cc", type=int, default=DEFAULTS["max_cc"])
    parser.add_argument("--max-func-lines", type=int, default=DEFAULTS["max_func_lines"])
    parser.add_argument("--max-file-lines", type=int, default=DEFAULTS["max_file_lines"])
    parser.add_argument("--only", choices=("cc", "lengths"))
    parser.add_argument("--baseline", metavar="FILE")
    parser.add_argument("--write-baseline", action="store_true")
    parser.add_argument("--root", metavar="DIR")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def _emit_text(caps, violations, result):
    for kind in ("new", "worse"):
        for key in result[kind]:
            print("%s %s = %s" % (kind.upper(), key, lc.format_value(violations[key])))
    for key in result["stale"]:
        print("STALE(advisory) %s — strike it from the baseline" % key)
    verdict = "OK" if result["ok"] else "FAIL"
    print("complexity_min: %s (caps %s; %s)" % (verdict, json.dumps(caps, sort_keys=True), STRICT_NOTE))


def _emit(as_json, caps, violations, result):
    if not as_json:
        _emit_text(caps, violations, result)
        return
    payload = dict(result)
    payload.update({"caps": caps, "violations": violations, "note": STRICT_NOTE})
    print(json.dumps(payload, indent=1, sort_keys=True))


def _report_errors(errors):
    for rel, msg in errors:
        print("ERROR %s: %s" % (rel, msg), file=sys.stderr)
    print("complexity_min: %d unreadable/unparseable file(s) — fail closed" % len(errors),
          file=sys.stderr)
    return 2


def _write_baseline(path, violations):
    if not path:
        print("--write-baseline needs --baseline FILE", file=sys.stderr)
        return 2
    count = lc.write_ledger(path, violations, "complexity_min baseline")
    print("wrote %d entries to %s" % (count, path))
    return 0


def main(argv=None):
    args = _parse_args(argv)
    caps = {"max_cc": args.max_cc, "max_func_lines": args.max_func_lines,
            "max_file_lines": args.max_file_lines}
    root = os.path.abspath(args.root or os.getcwd())
    violations, errors = scan(args.paths, root, caps, args.only)
    if errors:
        return _report_errors(errors)
    if args.write_baseline:
        return _write_baseline(args.baseline, violations)
    result = lc.compare_ledger(violations, lc.load_ledger(args.baseline))
    _emit(args.json, caps, violations, result)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
