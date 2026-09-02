#!/usr/bin/env python3
"""test-code skill · 结构门（第 1 档，核心圈）：目录/放置/依赖图的确定性指标，不靠 LLM。

法典指针：CLAUDE.md 防腐十条第 7 条（测试位置）、第 9 条（同一 basename 禁止出现在两个
目录层级）、第 1 条（长度上限归 hygiene）；设计 = docs/design/vnext2-plan.md R2.8。
阈值来自项目 qa/gates.toml [structure]（缺席 → skill 默认值 DEFAULT_CAPS）。

规则（每条一个 key 前缀，全部进 shrink-only 账本 .test-code/baselines/structure.txt）：
  tests-outside:<path>   python 测试文件不在声明的测试目录里（JS 的 *.test.* / *.spec.* 与
                         Go 的 _test.go 按生态惯例允许并排，不算）
  dup-basename:<name>    同一 python 模块名出现在多个目录（值 = 目录数）
  depth:<path>           目录深度超上限
  crowded-dir:<dir>      单目录直接文件数超上限（测试目录根免检——平铺是主流惯例）
  cycle:<a>>b>...>       python 项目内 import 环（值 = 环内模块数）
  orphan:<path>          python 源模块：无人 import、无 __main__ 入口、不在 scripts/bin/tools
镜像率（每个源模块有没有同名测试）只进 details，不判红。
判例：tests/test_skill_test_code_structure.py（含负控制）。
"""

import ast
import os
import re

_TEST_NAME_RE = re.compile(r"(^|/)(test_[^/]*\.py|[^/]*_test\.py)$")
_ENTRY_DIRS = ("scripts", "bin", "tools")
_MODULE_EXEMPT = ("__init__.py", "__main__.py", "conftest.py", "setup.py", "conf.py")
DEFAULT_CAPS = {"max_dir_depth": 6, "max_files_per_dir": 40}


def _dirname(rel):
    return rel.rsplit("/", 1)[0] if "/" in rel else ""


def _is_py_test(rel):
    return bool(_TEST_NAME_RE.search(rel))


def _under(rel, directory):
    return bool(directory) and (rel == directory or rel.startswith(directory + "/"))


# --------------------------------------------------------------------------- #
# 放置规则
# --------------------------------------------------------------------------- #

def tests_outside(files, tests_dir):
    """python 测试文件不在 tests_dir 下 → 违例；tests_dir 缺席 = 规则不适用（{}）。"""
    if not tests_dir:
        return {}
    return {"tests-outside:%s" % f: 1.0 for f in files if _is_py_test(f) and not _under(f, tests_dir)}


def dup_basenames(files, tests_dir):
    """同名 python 模块出现在 >1 个目录（测试目录、__init__/__main__/conftest/setup 免检）。"""
    seen = {}
    for f in files:
        base = os.path.basename(f)
        if f.endswith(".py") and base not in _MODULE_EXEMPT and not _under(f, tests_dir):
            seen.setdefault(base, set()).add(_dirname(f))
    return {"dup-basename:%s" % b: float(len(d)) for b, d in seen.items() if len(d) > 1}


def too_deep(files, cap):
    return {"depth:%s" % f: float(f.count("/")) for f in files if f.count("/") > cap}


def crowded_dirs(files, cap, tests_dir):
    counts = {}
    for f in files:
        counts[_dirname(f)] = counts.get(_dirname(f), 0) + 1
    return {"crowded-dir:%s" % (d or "."): float(n) for d, n in counts.items()
            if n > cap and d != tests_dir}


# --------------------------------------------------------------------------- #
# python import 图：环 + 孤儿 + 镜像率
# --------------------------------------------------------------------------- #

def module_name(rel):
    """`act/lib/x.py` → `act.lib.x`；`pkg/__init__.py` → `pkg`。"""
    stem = rel[:-3] if rel.endswith(".py") else rel
    parts = stem.split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _relative_base(importer, is_package, level):
    """`from ..x import y` 的锚点：importer=`pkg.sub.mod`（文件）level 1 → `pkg.sub`；包 __init__ 自身算一层。"""
    parts = importer.split(".")
    keep = len(parts) - level + (1 if is_package else 0)
    return ".".join(parts[:max(keep, 0)])


def _from_names(node, importer, is_package):
    """ImportFrom → 绝对模块名列表：模块本身 + 模块.别名（别名可能是子模块，多报无害）。"""
    if node.level:
        base = _relative_base(importer, is_package, node.level)
        module = ".".join(p for p in (base, node.module or "") if p)
    else:
        module = node.module or ""
    if not module:
        return []
    return [module] + ["%s.%s" % (module, alias.name) for alias in node.names]


def _imported_names(tree, importer="", is_package=False):
    """`import a.b` → a.b；`from a import b` → a 与 a.b；相对导入按 importer 解析成绝对名
    （首次跨项目实跑：itsdangerous 的 `from ._json import …` 让 _json.py 被误报为孤儿）。"""
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names += _from_names(node, importer, is_package)
    return names


def _resolve(name, known):
    """`a.b.c` → 已知模块里最长的前缀（`a.b.c` 或 `a.b`…）；不属于项目 → None。"""
    parts = name.split(".")
    for n in range(len(parts), 0, -1):
        candidate = ".".join(parts[:n])
        if candidate in known:
            return candidate
    return None


def scan_imports(read, py_files):
    """→ (graph, stems, errors)：graph = {module: set(项目内被 import 的模块)}；stems = 全部被
    import 的名字的末段（`import m` / `from pkg.m import x` 都留下 "m"），给孤儿判定兜底
    （sys.path 花式导入解析不到全名，但末段命中就不算孤儿——宁漏报不误报）。"""
    known = {module_name(f): f for f in py_files}
    graph, stems, errors = {m: set() for m in known}, set(), []
    for mod, rel in known.items():
        try:
            tree = ast.parse(read(rel) or "", filename=rel)
        except (SyntaxError, ValueError) as exc:
            errors.append("%s: %s" % (rel, exc))
            continue
        names = _imported_names(tree, mod, rel.endswith("__init__.py"))
        stems.update(n.split(".")[-1] for n in names)
        targets = {_resolve(n, known) for n in names}
        graph[mod] = {t for t in targets if t and t != mod}
    return graph, stems, errors


def import_graph(read, py_files):
    """→ (graph, errors)；见 scan_imports。"""
    graph, _stems, errors = scan_imports(read, py_files)
    return graph, errors


def _tarjan(graph):
    """迭代版 Tarjan：→ 强连通分量列表（每个是排序后的模块列表）。"""
    state = {"index": {}, "low": {}, "on_stack": set(), "stack": [], "comps": []}
    for root in sorted(graph):
        if root not in state["index"]:
            _tarjan_from(root, graph, state)
    return state["comps"]


def _tarjan_enter(node, graph, state, work):
    state["index"][node] = state["low"][node] = len(state["index"])
    state["stack"].append(node)
    state["on_stack"].add(node)
    work.append((node, iter(sorted(graph[node]))))


def _tarjan_from(root, graph, state):
    work = []
    _tarjan_enter(root, graph, state, work)
    while work:
        node, children = work[-1]
        child = next(children, None)
        if child is None:
            work.pop()
            _tarjan_close(node, work, state)
        elif child not in state["index"]:
            _tarjan_enter(child, graph, state, work)
        elif child in state["on_stack"]:
            state["low"][node] = min(state["low"][node], state["index"][child])


def _tarjan_close(node, work, state):
    if work:
        parent = work[-1][0]
        state["low"][parent] = min(state["low"][parent], state["low"][node])
    if state["low"][node] != state["index"][node]:
        return
    comp = []
    while True:
        member = state["stack"].pop()
        state["on_stack"].discard(member)
        comp.append(member)
        if member == node:
            break
    state["comps"].append(sorted(comp))


def cycles(graph):
    """环 = 大小 >1 的强连通分量（自环已在建图时剔除）。"""
    return {"cycle:%s" % ">".join(comp): float(len(comp)) for comp in _tarjan(graph) if len(comp) > 1}


def _has_main_guard(source):
    return "__name__" in source and "__main__" in source


def _is_entry(rel, read):
    return rel.split("/")[0] in _ENTRY_DIRS or _has_main_guard(read(rel) or "")


def _referenced(rel, imported, stems):
    mod = module_name(rel)
    return mod in imported or mod.split(".")[-1] in stems


def _all_imported(graph):
    out = set()
    for targets in graph.values():
        out |= targets
    return out


def orphans(graph, py_files, read, tests_dir, stems=frozenset()):
    """无人 import 的源模块，排除入口（__main__ 守卫、scripts/bin/tools、包 __init__、测试）；
    末段名被任何文件 import 过（stems）也不算孤儿。"""
    imported = _all_imported(graph)
    out = {}
    for rel in py_files:
        exempt = (_referenced(rel, imported, stems) or _under(rel, tests_dir)
                  or os.path.basename(rel) in _MODULE_EXEMPT)
        if not exempt and not _is_entry(rel, read):
            out["orphan:%s" % rel] = 1.0
    return out


def _mirror_sources(py_files, tests_dir):
    return [f for f in py_files if not _under(f, tests_dir) and os.path.basename(f) not in _MODULE_EXEMPT
            and f.split("/")[0] not in _ENTRY_DIRS]


def _has_mirror(rel, test_stems):
    stem = os.path.basename(rel)[:-3]
    return any(s.startswith("test_" + stem) for s in test_stems)


def mirror_ratio(py_files, tests_dir):
    """源模块里有同名测试（tests_dir/**/test_<stem>*.py）的比例；无测试目录/无源模块 → None。"""
    if not tests_dir:
        return None
    test_stems = {os.path.basename(f)[:-3] for f in py_files if _under(f, tests_dir) and _is_py_test(f)}
    sources = _mirror_sources(py_files, tests_dir)
    if not sources:
        return None
    return round(sum(1 for f in sources if _has_mirror(f, test_stems)) / len(sources), 3)


# --------------------------------------------------------------------------- #
# 汇总
# --------------------------------------------------------------------------- #

def measure(files, read, tests_dir, caps):
    """→ (violations, details, errors)。files = 仓库相对路径（已排除 SKIP_DIRS）。"""
    py_files = [f for f in files if f.endswith(".py")]
    graph, stems, errors = scan_imports(read, py_files)
    violations = {}
    violations.update(tests_outside(files, tests_dir))
    violations.update(dup_basenames(files, tests_dir))
    violations.update(too_deep(files, caps["max_dir_depth"]))
    violations.update(crowded_dirs(files, caps["max_files_per_dir"], tests_dir))
    violations.update(cycles(graph))
    violations.update(orphans(graph, py_files, read, tests_dir, stems))
    details = {"mirror_ratio": mirror_ratio(py_files, tests_dir), "python_modules": len(py_files),
               "caps": dict(caps)}
    return violations, details, errors
