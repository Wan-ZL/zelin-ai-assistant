#!/usr/bin/env python3
"""模块依赖方向硬门（stdlib ast import 走查）。

法典：docs/CONTRACT.md §58.3（防腐十条 #2 的机械化）。分层规则：
  1. act/lib/**   只准 import stdlib + yaml + act(.lib)——lib 永不向上。
  2. act/*.py     （entrypoint 层：actd/executor/radar*/digest/doctor/webui/
                   boardctl…）准 import act.lib，互相之间不准 import——唯一例外
                   是边界层 act.llm（§59 / 防腐 #3：所有带 prompt 的 claude 调用
                   必须经它构造 argv，禁它等于禁守法；v0.48.x §63 立法）。
  3. server/**    只准 import stdlib/第三方 + act.lib + server。
  4. 任何模块不准跨模块引用 `_私名`（from X import _y / X._y 属性链）。
第三方白名单检查（规则 1 的 stdlib 判定）用 sys.stdlib_module_names（3.10+）；
3.9 本地跑时该子项自动让位（CI 的 qa-gates 跑 3.x，全量执法）。
存量账本 qa/deps_baseline.txt（shrink-only）。判例：tests/test_qa_depgraph_rules.py。

用法：
    python3 scripts/qa/depgraph.py --check [--report DIR]
    python3 scripts/qa/depgraph.py --list
    python3 scripts/qa/depgraph.py --write-baseline
"""

import argparse
import ast
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import qa_common  # noqa: E402

BASELINE = os.path.join(qa_common.REPO_ROOT, "qa", "deps_baseline.txt")

STDLIB = frozenset(getattr(sys, "stdlib_module_names", ()))
# 宪法第 7 条：运行时第三方 = PyYAML；cryptography 是 act/lib/e2e.py 的
# 法定 lazy 依赖（没装时该功能自报不可用，CI macOS 腿安装）——两者之外
# 的第三方 import 出现在 act/lib 即 lib-thirdparty 违例。
_EXTERNAL_OK = frozenset({"yaml", "cryptography"})
_FIRST_PARTY_TOPS = ("act", "server", "scripts")
# §58.3 规则 2 的法定例外：entrypoint 层准 import 的同层模块（LLM 边界，§59）。
# act.llm 自己仍按 entry 受审——它只准向下到 act.lib。lib 层不在此列（lib
# 经注入缝拿 runner，绝不向上 import）。
BOUNDARY_MODULES = frozenset({"act.llm"})


# --------------------------------------------------------------------------- #
# 模块名解析
# --------------------------------------------------------------------------- #

def _iter_rel_py(root):
    for path in qa_common.iter_py_files(root):
        yield os.path.relpath(path, root).replace(os.sep, "/")


def _module_parts(relpath):
    """相对路径 → (模块名各段, 是否包 __init__)。"""
    parts = relpath[:-3].split("/")
    is_pkg = parts[-1] == "__init__"
    if is_pkg:
        parts = parts[:-1]
    return parts, is_pkg


def build_module_index(root):
    """仓内一方模块与包名全集（含各级包前缀）。"""
    index = set()
    for relpath in _iter_rel_py(root):
        parts, _ = _module_parts(relpath)
        for i in range(1, len(parts) + 1):
            index.add(".".join(parts[:i]))
    return index


def _in_package(module, package):
    return module == package or module.startswith(package + ".")


def classify(module):
    """分层：lib / entry / server / other（scripts、act 包本身）。"""
    if _in_package(module, "act.lib"):
        return "lib"
    if _in_package(module, "server"):
        return "server"
    if module.startswith("act.") and "." not in module[len("act."):]:
        return "entry"
    return "other"


def _is_first_party(top):
    return top in _FIRST_PARTY_TOPS


def _is_private_name(name):
    return name.startswith("_") and not (name.startswith("__") and name.endswith("__"))


# --------------------------------------------------------------------------- #
# 方向规则（§58.3 的 1–3）
# --------------------------------------------------------------------------- #

def _is_unknown_external(top):
    """stdlib 名单可用时才判第三方（3.9 本地无名单 = 让给 CI）。"""
    if not STDLIB:
        return False
    return top not in STDLIB and top not in _EXTERNAL_OK


def _lib_violation(target):
    top = target.split(".", 1)[0]
    if not _is_first_party(top):
        return "lib-thirdparty" if _is_unknown_external(top) else None
    if target in ("act", "act.lib") or target.startswith("act.lib."):
        return None
    return "lib-import"


def _server_violation(target):
    top = target.split(".", 1)[0]
    if not _is_first_party(top):
        return None  # stdlib/第三方归宪法白名单与 lint，不在方向门内
    ok = target in ("act", "act.lib", "server") or target.startswith(("act.lib.", "server."))
    return None if ok else "server-import"


def _entry_violation(importer, target):
    if target in BOUNDARY_MODULES:
        return None
    if classify(target) == "entry" and target != importer:
        return "entry-pair"
    return None


def _direction_violation(importer, target):
    """一条 import 边违反的规则名（合法 = None）。"""
    kind = classify(importer)
    if kind == "lib":
        return _lib_violation(target)
    if kind == "entry":
        return _entry_violation(importer, target)
    if kind == "server":
        return _server_violation(target)
    return None


# --------------------------------------------------------------------------- #
# import 语句走查（含函数体内的 lazy import）
# --------------------------------------------------------------------------- #

def _resolve_relative(node, parts, is_pkg):
    """相对 import → 绝对模块串（越级解析不了 = None，跳过）。"""
    package = list(parts) if is_pkg else list(parts[:-1])
    up = node.level - 1
    if up > len(package):
        return None
    base = package[: len(package) - up]
    suffix = node.module.split(".") if node.module else []
    return ".".join(base + suffix) or None


def _from_base(node, parts, is_pkg):
    if node.level == 0:
        return node.module or ""
    return _resolve_relative(node, parts, is_pkg) or ""


def _from_module_edges(node, base, index):
    """ImportFrom 的模块边：[(目标模块, 绑定名 or None)]。
    `from a import b` 里 b 若真是模块（在 index），目标 = a.b 并绑定 b。"""
    edges = []
    for alias in node.names:
        candidate = ("%s.%s" % (base, alias.name)) if base else alias.name
        if candidate in index:
            edges.append((candidate, alias.asname or alias.name))
        elif base:
            edges.append((base, None))
    return edges


def _from_private_names(node, base, index):
    """`from <一方模块> import _私名`（§58.3 规则 4 的 A 形）。"""
    if base not in index:
        return []
    return [(base, a.name) for a in node.names
            if _is_private_name(a.name) and "%s.%s" % (base, a.name) not in index]


def _record_edge(target, module, relpath, keys):
    rule = _direction_violation(module, target)
    if rule:
        keys["%s:%s->%s" % (rule, relpath, target)] = 1.0


def _record_plain_import(node, module, relpath, alias_map, keys):
    for alias in node.names:
        _record_edge(alias.name, module, relpath, keys)
        top = alias.name.split(".", 1)[0]
        if alias.asname:
            alias_map[alias.asname] = alias.name
        else:
            alias_map.setdefault(top, top)


def _record_from_import(node, parts, is_pkg, index, module, relpath, alias_map, keys):
    base = _from_base(node, parts, is_pkg)
    for target, binding in _from_module_edges(node, base, index):
        _record_edge(target, module, relpath, keys)
        if binding:
            alias_map[binding] = target
    for target, name in _from_private_names(node, base, index):
        keys["private:%s->%s.%s" % (relpath, target, name)] = 1.0


def _collect_import(node, parts, is_pkg, index, module, relpath, alias_map, keys):
    if isinstance(node, ast.Import):
        _record_plain_import(node, module, relpath, alias_map, keys)
    elif isinstance(node, ast.ImportFrom):
        _record_from_import(node, parts, is_pkg, index, module, relpath, alias_map, keys)


# --------------------------------------------------------------------------- #
# 属性链私名（§58.3 规则 4 的 B 形：mod._x）
# --------------------------------------------------------------------------- #

def _dotted(node):
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    parts.append(node.id)
    return ".".join(reversed(parts))


def _resolve_prefix(dotted, alias_map):
    if not dotted:
        return None
    first, _, rest = dotted.partition(".")
    resolved = alias_map.get(first, first)
    return "%s.%s" % (resolved, rest) if rest else resolved


def _foreign_module(target, index, own_module):
    return bool(target) and target in index and target != own_module


def _attr_privates(tree, alias_map, index, own_module):
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        if not _is_private_name(node.attr):
            continue
        target = _resolve_prefix(_dotted(node.value), alias_map)
        if _foreign_module(target, index, own_module):
            found.append((target, node.attr))
    return found


# --------------------------------------------------------------------------- #
# 扫描入口
# --------------------------------------------------------------------------- #

def _scan_file(relpath, root, index):
    tree = qa_common.parse_file(os.path.join(root, relpath))
    if tree is None:
        return {}
    parts, is_pkg = _module_parts(relpath)
    module = ".".join(parts)
    keys, alias_map = {}, {}
    for node in ast.walk(tree):
        _collect_import(node, parts, is_pkg, index, module, relpath, alias_map, keys)
    for target, name in _attr_privates(tree, alias_map, index, module):
        keys["private:%s->%s.%s" % (relpath, target, name)] = 1.0
    return keys


def scan(root=None):
    """全部方向/私名违例：{violation_key: 1.0}。"""
    root = root or qa_common.REPO_ROOT
    index = build_module_index(root)
    violations = {}
    for relpath in _iter_rel_py(root):
        violations.update(_scan_file(relpath, root, index))
    return violations


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--write-baseline", action="store_true")
    parser.add_argument("--report", metavar="DIR")
    args = parser.parse_args(argv)

    violations = scan()
    if args.list:
        for key in sorted(violations):
            print(key)
        return 0
    if args.write_baseline:
        print("wrote %d entries to %s"
              % (qa_common.write_ledger(BASELINE, violations, "depgraph"), BASELINE))
        return 0
    return qa_common.run_gate("deps", violations, BASELINE, threshold=0.0,
                              tolerance=0.0, report_dir=args.report)


if __name__ == "__main__":
    sys.exit(main())
