#!/usr/bin/env python3
"""QA 门共用件：阈值配置读取、圈复杂度计数、shrink-only 账本语义。

法典：docs/CONTRACT.md §58（质量仪表与合并硬门）。五道门（complexity /
crap / coverage_floor / depgraph / hygiene）都从 qa/gates.toml（单源）读
阈值、用同一个账本比较器——语义只实现一次。判例：
tests/test_qa_ledger_shrink.py（账本语义）、tests/test_qa_complexity_counter.py
（计数口径）、tests/test_qa_crap_formula.py（公式与行段覆盖率映射）。

stdlib-only：跑在 owner 机器的 /usr/bin/python3（3.9 floor）和 CI 的
qa-gates job 上；不依赖 PyYAML（TOML 子集自带解析），不依赖 tomllib（3.11+）。
"""

import ast
import math
import os
import re

REPO_ROOT = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
)
GATES_FILE = os.path.join(REPO_ROOT, "qa", "gates.toml")

# 扫描范围（§58.1）：tests/ 是白盒判例不设门；mac/ 按 D3 豁免（退役中）。
PY_SCAN_DIRS = ("act", "server", "scripts")

_INT_RE = re.compile(r"-?\d+$")
_FLOAT_RE = re.compile(r"-?\d+\.\d+$")


# --------------------------------------------------------------------------- #
# qa/gates.toml —— TOML 子集解析（[section] + key = int/float/bool/"str"）
# --------------------------------------------------------------------------- #

def _parse_scalar(raw):
    """单个 TOML 标量；不认识的形状 fail-loud（阈值文件坏了必须停门）。"""
    text = raw.strip()
    if text.startswith('"'):
        end = text.find('"', 1)
        if end < 0:
            raise ValueError("unterminated string: %r" % raw)
        return text[1:end]
    text = text.split("#", 1)[0].strip()
    if text in ("true", "false"):
        return text == "true"
    if _INT_RE.match(text):
        return int(text)
    if _FLOAT_RE.match(text):
        return float(text)
    raise ValueError("unsupported TOML scalar: %r" % raw)


def _open_section(text, sections):
    if not text.endswith("]"):
        raise ValueError("unparseable gates.toml section: %r" % text)
    return sections.setdefault(text[1:-1].strip(), {})


def _apply_line(text, sections, current):
    """一行配置 → 返回行后所处的 section（fail-loud：坏行必须停门）。"""
    if not text or text.startswith("#"):
        return current
    if text.startswith("["):
        return _open_section(text, sections)
    key, sep, raw = text.partition("=")
    if not sep or current is None:
        raise ValueError("unparseable gates.toml line: %r" % text)
    current[key.strip()] = _parse_scalar(raw)
    return current


def parse_gates_text(text):
    """gates.toml 文本 → {section: {key: value}}（ledger_diff 对 base 版本
    也用同一解析器——阈值怎么被门读，就怎么被差分门读）。"""
    sections = {}
    current = None
    for line in text.splitlines():
        current = _apply_line(line.strip(), sections, current)
    return sections


def load_gates(path=GATES_FILE):
    """qa/gates.toml → {section: {key: value}}。"""
    with open(path, "r", encoding="utf-8") as fh:
        return parse_gates_text(fh.read())


# --------------------------------------------------------------------------- #
# 文件遍历 + 解析
# --------------------------------------------------------------------------- #

def iter_py_files(root, rel_dirs=PY_SCAN_DIRS):
    """扫描范围内全部 .py（跳过 __pycache__；目录缺席 = 空产出）。"""
    for rel in rel_dirs:
        for dirpath, dirnames, filenames in os.walk(os.path.join(root, rel)):
            dirnames[:] = sorted(d for d in dirnames if d != "__pycache__")
            for fn in sorted(filenames):
                if fn.endswith(".py"):
                    yield os.path.join(dirpath, fn)


def parse_file(path):
    """AST or None——读不了/语法坏的文件不许崩门（宪法第 11 条）；
    真正的语法错误由既有 compileall 门负责报。"""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return ast.parse(fh.read(), filename=path)
    except (SyntaxError, ValueError, OSError):
        return None


# --------------------------------------------------------------------------- #
# 圈复杂度（§58.1 计数口径）
# --------------------------------------------------------------------------- #

# 每个命中 +1 的节点：if/elif（elif 即嵌套 If）、for、while、except、
# assert、三元。with-as 故意不算（§58.1）。
_UNIT_NODES = (
    ast.If, ast.For, ast.AsyncFor, ast.While,
    ast.ExceptHandler, ast.Assert, ast.IfExp,
)
_MATCH_CASE = getattr(ast, "match_case", None)  # 3.10+；3.9 无 match 语法
_FUNC_NODES = (ast.FunctionDef, ast.AsyncFunctionDef)


def _decision_points(node):
    """单节点贡献的决策点数。BoolOp 是 n 路短路 = n−1 个分叉；
    comprehension 只数 if 子句（for 本体不算，§58.1）。"""
    if isinstance(node, _UNIT_NODES):
        return 1
    if isinstance(node, ast.BoolOp):
        return len(node.values) - 1
    if isinstance(node, ast.comprehension):
        return len(node.ifs)
    if _MATCH_CASE is not None and isinstance(node, _MATCH_CASE):
        return 1
    return 0


def cyclomatic_complexity(func_node):
    """1 + 函数体内决策点。嵌套 def 不计入外层——它们各自成账（否则
    「拆函数」这条唯一出路会被计数口径没收）。lambda 留在外层。"""
    total = 1
    stack = list(ast.iter_child_nodes(func_node))
    while stack:
        node = stack.pop()
        if isinstance(node, _FUNC_NODES):
            continue
        total += _decision_points(node)
        stack.extend(ast.iter_child_nodes(node))
    return total


class _DefCollector(ast.NodeVisitor):
    """按定义顺序收 (qualname, node, kind)；qualname 用点号嵌套。"""

    def __init__(self):
        self.stack = []
        self.found = []

    def _visit_named(self, node, kind):
        qual = ".".join(self.stack + [node.name])
        self.found.append((qual, node, kind))
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    def visit_ClassDef(self, node):
        self._visit_named(node, "class")

    def visit_FunctionDef(self, node):
        self._visit_named(node, "func")

    visit_AsyncFunctionDef = visit_FunctionDef


def collect_definitions(tree):
    """[(qualname, node, kind)]；同名重定义追加 #2/#3…（文件内稳定键）。"""
    collector = _DefCollector()
    collector.visit(tree)
    seen = {}
    out = []
    for qual, node, kind in collector.found:
        seen[qual] = seen.get(qual, 0) + 1
        key = qual if seen[qual] == 1 else "%s#%d" % (qual, seen[qual])
        out.append((key, node, kind))
    return out


def collect_functions(tree):
    """[(qualname, node)]，只留函数（含 async、方法、嵌套函数）。"""
    return [(q, n) for q, n, kind in collect_definitions(tree) if kind == "func"]


# --------------------------------------------------------------------------- #
# CRAP（§58.2）
# --------------------------------------------------------------------------- #

def crap_score(cc, cov):
    """CRAP(f) = CC² × (1 − cov)³ + CC；cov ∈ [0,1]。四舍五入到 1 位小数
    （账本可读，也吞掉浮点尾差）。"""
    return round(cc * cc * (1.0 - cov) ** 3 + cc, 1)


def span_coverage(node, executed, missing):
    """函数行覆盖率 = 行段内 coverage 认识的语句行里被执行的比例。
    行段内没有已知语句（1 行 stub 之类）按 1.0——没东西可测不算欠账。"""
    span = set(range(node.lineno, node.end_lineno + 1))
    known = span & (executed | missing)
    if not known:
        return 1.0
    return len(span & executed) / len(known)


# --------------------------------------------------------------------------- #
# shrink-only 账本（§58.4；判例 tests/test_qa_ledger_shrink.py）
# --------------------------------------------------------------------------- #

def _parse_score(raw):
    """账本/地板数字：必须是有限数，否则 fail-loud（与 _parse_scalar 同哲学）。
    `float()` 认 nan/inf，而 nan 与任何数比较都是 False——放进登记分或地板
    就把三态判决（worse/stale）与 ledger_diff 的 base 差分同时 fail-open，
    等于单 token 永久豁免。判例：tests/test_qa_ledger_shrink.py。"""
    value = float(raw)
    if not math.isfinite(value):
        raise ValueError("non-finite score in qa ledger/floor text: %r" % raw)
    return value


def parse_ledger_text(text):
    """账本文本 → {key: 登记分}。行形 `<key> <score>`；# 注释与空行忽略；
    无分数按 1.0（不计分的违例种类）；非有限分数 fail-loud。"""
    entries = {}
    for line in text.splitlines():
        stripped = line.split("#", 1)[0].strip()
        if not stripped:
            continue
        parts = stripped.split()
        entries[parts[0]] = _parse_score(parts[1]) if len(parts) > 1 else 1.0
    return entries


def load_ledger(path):
    """账本文件 → {key: 登记分}。文件缺席 = 空账（一切违例都算新）。"""
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as fh:
        return parse_ledger_text(fh.read())


def format_score(score):
    """登记分 → 账本文本形（整数不带小数点；ledger_diff 的输出同款）。"""
    if float(score).is_integer():
        return str(int(score))
    return "%.1f" % score


def parse_floor_text(text):
    """coverage_floor.txt 文本 → 地板数字（首个非注释 token；非有限数
    fail-loud；coverage_floor.read_floor 与 ledger_diff 共用同一解析）。"""
    for line in text.splitlines():
        stripped = line.split("#", 1)[0].strip()
        if stripped:
            return _parse_score(stripped)
    raise ValueError("no floor number in coverage_floor text")


def format_ledger(violations):
    """{key: score} → 账本文本（按 key 排序，key + 空格 + 分数）。"""
    lines = ["%s %s" % (key, format_score(violations[key]))
             for key in sorted(violations)]
    return "".join(line + "\n" for line in lines)


def write_ledger(path, violations, gate_name):
    """重铸账本（收账用）：标准头 + 排序条目。头是注释，load_ledger 忽略。"""
    header = (
        "# %s —— shrink-only 存量账本（docs/CONTRACT.md §58.4）。\n"
        "# 只许缩：新违例 FAIL、账上恶化 FAIL、已修好仍挂账 FAIL（划掉这行）。\n"
        "# 重铸：python3 scripts/qa/%s.py --write-baseline（P3 清账轮以外别用）。\n"
        % (os.path.basename(path), gate_name)
    )
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(header + format_ledger(violations))
    return len(violations)


def _new_violations(violations, ledger):
    return sorted(k for k in violations if k not in ledger)


def _worse_violations(violations, ledger, tolerance):
    listed = set(violations) & set(ledger)
    return sorted(k for k in listed if violations[k] > ledger[k] + tolerance)


def _better_violations(violations, ledger, tolerance):
    listed = set(violations) & set(ledger)
    return sorted(k for k in listed if violations[k] < ledger[k] - tolerance)


def _stale_and_limbo(scores, ledger, threshold, tolerance):
    stale, limbo = [], []
    for key in ledger:
        score = scores.get(key)
        if score is None or score <= threshold - tolerance:
            stale.append(key)
        elif score <= threshold:
            limbo.append(key)
    return sorted(stale), sorted(limbo)


def compare_with_ledger(scores, ledger, threshold, tolerance=0.0):
    """shrink-only 判决。scores = 全量测量 {key: score}；violation = 分数
    严格大于 threshold 的条目。

    FAIL（三种，任意一种即不 ok）：
      new   —— 超阈值且不在账上（新违例：新代码必须干净）
      worse —— 在账上且比登记分恶化超过 tolerance（存量只许持平或变好）
      stale —— 在账上但已达标（score ≤ threshold − tolerance）或已消失
               （修好了就必须把账划掉——账本只许缩，这就是棘轮）
    不 FAIL（两种提示）：
      limbo  —— 在账上、落在 (threshold − tolerance, threshold]：coverage
                抖动缓冲带，建议观察后删账
      better —— 在账上、仍超阈值但比登记分好出 tolerance：建议把登记分拧低
    """
    violations = {k: v for k, v in scores.items() if v > threshold}
    stale, limbo = _stale_and_limbo(scores, ledger, threshold, tolerance)
    result = {
        "violations": violations,
        "new": _new_violations(violations, ledger),
        "worse": _worse_violations(violations, ledger, tolerance),
        "better": _better_violations(violations, ledger, tolerance),
        "stale": stale,
        "limbo": limbo,
    }
    result["ok"] = not (result["new"] or result["worse"] or result["stale"])
    return result


# --------------------------------------------------------------------------- #
# 判决打印 + 建议账本落盘（每道门共用的出口）
# --------------------------------------------------------------------------- #

def _describe(kind, keys, scores, ledger):
    lines = []
    for key in keys:
        current = scores.get(key)
        shown = "gone" if current is None else format_score(current)
        listed = ledger.get(key)
        suffix = "" if listed is None else " (baseline %s)" % format_score(listed)
        lines.append("  %s: %s = %s%s" % (kind, key, shown, suffix))
    return lines


def render_verdict(name, result, scores, ledger, threshold):
    """人读判决文本（stdout 与 report 同一份）。"""
    lines = ["[%s] threshold %s: %d violation(s), %d listed"
             % (name, format_score(threshold), len(result["violations"]), len(ledger))]
    lines += _describe("NEW", result["new"], scores, ledger)
    lines += _describe("WORSE", result["worse"], scores, ledger)
    lines += _describe("STALE", result["stale"], scores, ledger)
    lines += _describe("limbo(advisory)", result["limbo"], scores, ledger)
    lines += _describe("better(advisory)", result["better"], scores, ledger)
    lines.append("[%s] %s" % (name, "OK" if result["ok"] else "FAIL"))
    return "\n".join(lines)


def write_report(report_dir, filename, text):
    """report 目录落一份文本（目录不存在就建）。"""
    os.makedirs(report_dir, exist_ok=True)
    path = os.path.join(report_dir, filename)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def soften_off_canonical(rc, platform, gate):
    """coverage 派生的门（crap / coverage_floor）只在 canonical 环境判卷
    （= CI 的 qa-gates job，linux；§58.2）。别的平台上平台守卫的 skip 会改变
    函数/总体覆盖率——同一函数可以 darwin 干净、ubuntu 超标，反之亦然——
    所以非 linux 上判决全文照印、退出码归零（只报不判）。对账以 CI 的
    qa-report artifact 为准。纯 AST 的门（complexity/deps/hygiene）平台无关，
    不走这里。"""
    if rc != 0 and not platform.startswith("linux"):
        print("[%s] non-canonical platform (%s): verdict above is advisory —"
              " this gate judges on the CI qa-gates job (linux); reconcile"
              " the qa/ ledger from its qa-report artifact" % (gate, platform))
        return 0
    return rc


def _print_red_hint(name, result, ledger_path):
    """红判决的出路提示，按三态分开写：NEW 的唯一出路是修代码——把新债
    写进账本会被 ledger_diff 的 base 差分门拒掉（§58.4，f2a54c1 审查
    blocker 1：旧提示「reconcile the ledger」对 NEW 就是教人自记账）。"""
    rel = os.path.relpath(ledger_path, REPO_ROOT)
    if result["new"]:
        print("[%s] NEW violations must be fixed in the code — enrolling"
              " them in %s is rejected by scripts/qa/ledger_diff.py"
              " against the PR base" % (name, rel))
    if result["worse"] or result["stale"]:
        print("[%s] WORSE: improve the code back to its listed score;"
              " STALE: strike the now-clean line from %s in this PR"
              % (name, rel))


def run_gate(name, scores, ledger_path, threshold, tolerance=0.0, report_dir=None):
    """一道门的统一出口：比较、打印、写建议账本；返回进程退出码。
    建议账本 = 当前全部超阈值条目——CI 上 FAIL 时直接从 artifact 拷回
    qa/ 即完成收账（P3 清账也走同一条路）。"""
    ledger = load_ledger(ledger_path)
    result = compare_with_ledger(scores, ledger, threshold, tolerance)
    verdict = render_verdict(name, result, scores, ledger, threshold)
    print(verdict)
    if not result["ok"]:
        _print_red_hint(name, result, ledger_path)
    if report_dir:
        write_report(report_dir, "%s_verdict.txt" % name, verdict + "\n")
        write_report(report_dir, "%s_suggested_baseline.txt" % name,
                     format_ledger(result["violations"]))
    return 0 if result["ok"] else 1
