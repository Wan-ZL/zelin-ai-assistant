#!/usr/bin/env python3
"""test-code skill · 检查目录（catalog）+ 命令构造（builders）+ 自制检查（internal）。

法典指针：docs/CONTRACT.md §58（项目有门就用项目的门——complexity / crap /
coverage_floor / depgraph / hygiene；阈值 truth = qa/gates.toml，skill 只读，
R2.8.3）、§57（变异 runner scripts/qa/mutate.py + qa/mutation_targets.toml 靶区）。
设计 = docs/design/vnext2-plan.md R2.8 / D14；层菜单与触发器 = references/tiers.md、
references/triggers.md；生态命令表 = references/adapters.md。

每个 check = CATALOG 一行 + builder(ctx) → plan：
  {"kind": "cmd", "steps": [{"argv", "cwd", "env"?}], "tool", "post"?}   命令（顺序、首败即停）
  {"kind": "substituted", ...同上..., "note"}                               替代物跑了，永不写 pass
  {"kind": "internal", "fn": callable}                                     纯 Python 检查
  {"kind": "na", "reason"} / {"kind": "unavailable", "reason"}             项目无此面 / 工具缺席
  {"kind": "missing", "reason"}                                            触发器点名却无判例 → fail
自制检查一律 fail closed（读不到 = fail，不是 pass）；每个都有负控制判例：
tests/test_skill_test_code_checks.py。
"""

import ast
import fnmatch
import hashlib
import os
import re
import shlex
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import complexity_min as cm  # noqa: E402
import ladder_common as lc  # noqa: E402
import structure_check as sc  # noqa: E402

TIER_TIMEOUTS = {1: 300, 2: 1800, 3: 3600, 4: 7200, 5: None}  # 第 1–5 档；第 5 档无时限
TRIGGER_CHECKS = {
    "persisted_state": ["crash_recovery"],
    "boundary": ["fault_injection"],
    "concurrency": ["race_stress"],
    "spawns_processes": ["resource_leak"],
    "persisted_format": ["corpus_regression"],
    "documented_behavior": ["contract_drift", "docs_drift"],
    "always": ["diff_minimality"],
    "deps_changed": ["dependency_audit", "dependency_budget"],
}
_PROSE_EXT = (".md", ".txt", ".rst")
SKILL_SCRIPTS = os.path.dirname(os.path.abspath(__file__))


def make_ctx(repo, det, sel=None, out=None, py=None, init_baselines=False):
    """builders / internal checks 共用的上下文（唯一出生点；字段 add-only）。"""
    return {"repo": repo, "det": det, "sel": sel or {}, "out": out,
            "baselines": os.path.join(repo, ".test-code", "baselines"),
            "skill_scripts": SKILL_SCRIPTS, "py": py or sys.executable,
            "init_baselines": init_baselines}


# --------------------------------------------------------------------------- #
# plan / result 形状
# --------------------------------------------------------------------------- #

def _steps(kind, steps, tool, post=None, note=None):
    return {"kind": kind, "steps": steps, "tool": tool, "post": post, "note": note}


def _step(argv, cwd, env=None):
    return {"argv": argv, "cwd": cwd, "env": env}


def _cmd(argv, cwd, tool=None, post=None, env=None):
    return _steps("cmd", [_step(argv, cwd, env)], tool or argv[0], post)


def _internal(fn):
    return {"kind": "internal", "fn": fn, "tool": "internal", "steps": []}


def _na(reason):
    return {"kind": "na", "reason": reason, "steps": []}


def _unavailable(reason):
    return {"kind": "unavailable", "reason": reason, "steps": []}


def _missing(reason):
    return {"kind": "missing", "reason": reason, "steps": []}


def _res(status, summary, details=None):
    return {"status": status, "summary": summary, "details": details or {}}


def preview(plan):
    """菜单/报告里给人看的命令预览。"""
    if plan["kind"] == "internal":
        return "internal:%s" % plan["fn"].__name__
    return " && ".join(shlex.join(step["argv"]) for step in plan.get("steps", []))


# --------------------------------------------------------------------------- #
# ctx 访问器
# --------------------------------------------------------------------------- #

def _tool(ctx, name):
    return ctx["det"]["tools"].get(name)


def _pymod(ctx, name):
    return bool(ctx["det"]["pymods"].get(name))


def _has(ctx, rel):
    return os.path.exists(os.path.join(ctx["repo"], rel))


def _stacks(ctx):
    return set(ctx["det"]["stacks"])


def _layout(ctx):
    return ctx["det"]["layout"]


def _files(ctx):
    return ctx["det"]["files"]


def _diff(ctx):
    return ctx["det"]["diff"]


def _fired(ctx):
    return {t["id"] for t in ctx["det"]["triggers"]}


def _out(ctx, name):
    return os.path.join(ctx["out"] or ".", name)


def _ledger_path(ctx, name):
    return os.path.join(ctx["baselines"], name + ".txt")


def _skill_script(ctx, name):
    return os.path.join(ctx["skill_scripts"], name)


def _js_pkgs(ctx):
    return _layout(ctx).get("js_packages") or []


def _pkg_has(pkg, name):
    """package.json 声明了 name 且 node_modules/.bin 里装了它。"""
    return bool(pkg.get(name)) and name in pkg.get("bins", [])


def _js_ready(ctx, pkgs, needed):
    """npx 在、每个 pkg 的 node_modules/.bin 都有 needed → None；否则 unavailable plan。"""
    missing = [p["dir"] for p in pkgs if not set(needed) <= set(p.get("bins", []))]
    if missing or not _tool(ctx, "npx"):
        return _unavailable("%s not installed in %s (npm ci) or npx missing"
                            % ("/".join(needed), missing or ["node_modules"]))
    return None


def _need_python(ctx):
    return None if "python" in _stacks(ctx) else _na("no python sources")


def _no_tests_dir(ctx):
    return None if _layout(ctx).get("tests_dir") else _na("no python tests dir")


def _is_test_file(rel, tests_dir):
    base = os.path.basename(rel)
    return rel.startswith(tests_dir + "/") and base.startswith("test") and base.endswith(".py")


def _py_tests_matching(ctx, pattern):
    """tests 目录下文件名命中正则的 python 测试文件（相对路径，排序）。"""
    tests_dir = _layout(ctx).get("tests_dir")
    if not tests_dir:
        return []
    rx = re.compile(pattern, re.I)
    return [f for f in _files(ctx)
            if _is_test_file(f, tests_dir) and rx.search(os.path.basename(f))]


def _py_test_argv(ctx, files=None):
    """项目的 python 测试命令（pytest 或 unittest）；files 给子集。"""
    layout = _layout(ctx)
    if layout.get("py_runner") == "pytest":
        return [ctx["py"], "-m", "pytest", "-q"] + list(files or [])
    if files:
        return [ctx["py"], "-m", "unittest"] + list(files)
    return [ctx["py"], "-m", "unittest", "discover", "-s", layout.get("tests_dir") or "tests"]


def _subset_plan(ctx, files, reruns=1, note=None):
    steps = [_step(_py_test_argv(ctx, files), ctx["repo"])] * reruns
    kind = "substituted" if note else "cmd"
    return _steps(kind, steps, "python-tests", post=_post_tests, note=note)


def _trigger_subset(ctx, trigger, pattern, reruns=1):
    """触发器加挂层：点名的判例存在就跑；触发却没有判例 = missing（fail）；
    用户 waive = na（原因进报告）；没触发也没判例 = na。"""
    waived = (ctx["sel"].get("triggers_waived") or {}).get(trigger)
    if waived:
        return _na("trigger %s waived by user: %s" % (trigger, waived))
    files = _py_tests_matching(ctx, pattern)
    if files:
        return _subset_plan(ctx, files, reruns)
    if trigger in _fired(ctx):
        return _missing("trigger %s fired but no python test file matches /%s/ — "
                        "write one (references/triggers.md) or waive it in the selection"
                        % (trigger, pattern))
    return _na("trigger %s not fired and no test matches /%s/" % (trigger, pattern))


# --------------------------------------------------------------------------- #
# post hooks（命令跑完后解读输出；返回 {status?, summary, details}）
# --------------------------------------------------------------------------- #

_VERDICT_RE = re.compile(r"^\s*(NEW|WORSE|STALE)(?:\(advisory\))?[: ]\s*(\S+)", re.M)
_UNITTEST_FAIL = re.compile(r"^(?:FAIL|ERROR): (\S+) \(([^)]+)\)", re.M)
# pytest 短摘要行；`[^\s(]` 把 unittest 自己的 `FAILED (failures=1)` 尾行挡在外面
_PYTEST_FAIL = re.compile(r"^(?:FAILED|ERROR) ([^\s(]\S*)", re.M)
_JS_FAIL = re.compile(r"^\s*(?:✗|×|FAIL)\s+(\S.*?)\s*$", re.M)
_RAN_RE = re.compile(r"^Ran (\d+) tests?|(\d+) passed", re.M)


def _post_ledger_verdict(ctx, plan, runs):
    """项目 QA 门 / complexity_min 的 NEW/WORSE/STALE 行 → details。"""
    text = "\n".join(r.text() for r in runs)
    found = {"new": [], "worse": [], "stale": []}
    for kind, key in _VERDICT_RE.findall(text):
        found[kind.lower()].append(key)
    summary = "%d NEW, %d WORSE, %d STALE" % (len(found["new"]), len(found["worse"]), len(found["stale"]))
    return {"summary": summary, "details": found}


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def parse_test_failures(text):
    """unittest / pytest / vitest-jest 输出里的失败用例 id（排序去重）。先剥 ANSI 颜色码——
    pytest 带色输出的 `ERROR tests/x.py`（收集错误）否则一条都对不上（跨项目实跑抓到）。"""
    text = _ANSI_RE.sub("", text)
    ids = set()
    for name, qual in _UNITTEST_FAIL.findall(text):
        ids.add(qual if qual.endswith("." + name) else "%s.%s" % (qual, name))
    ids.update(_PYTEST_FAIL.findall(text))
    ids.update(_JS_FAIL.findall(text))
    return sorted(ids)


def _test_count(text):
    hits = [a or b for a, b in _RAN_RE.findall(text)]
    return "%s tests" % hits[-1] if hits else "test count unknown"


def _known_failing(ctx):
    known = set(ctx["sel"].get("known_failing") or [])
    return known | set(lc.load_ledger(_ledger_path(ctx, "known_failing")))


def _tests_verdict(failing, new, known, rc_ok, text, runs=1):
    if rc_ok:
        times = " ×%d runs" % runs if runs > 1 else ""
        return {"status": "pass", "summary": "%s, 0 failures%s" % (_test_count(text), times),
                "details": {"failing": failing, "new": new, "runs": runs}}
    pre = sorted(set(failing) & known)
    if not failing:
        return {"status": "fail", "summary": "runner exited non-zero with no parseable failure — fail closed",
                "details": {"failing": [], "new": []}}
    if new:
        return {"status": "fail", "summary": "%d NEW failing test(s) (%d pre-existing)" % (len(new), len(pre)),
                "details": {"failing": failing, "new": new, "pre_existing": pre}}
    return {"status": "pass", "summary": "0 NEW failures; %d pre-existing failure(s) in baseline note" % len(pre),
            "details": {"failing": failing, "new": [], "pre_existing": pre}}


def _post_tests(ctx, plan, runs):
    text = "\n".join(r.text() for r in runs)
    failing = parse_test_failures(text)
    rc_ok = all(r.ok for r in runs)
    if ctx.get("init_baselines"):
        n = lc.write_ledger(_ledger_path(ctx, "known_failing"), {t: 1.0 for t in failing},
                            "known failing tests (zero-NEW rule baseline)")
        return {"status": "pass" if rc_ok or failing else "fail",
                "summary": "grandfathered %d failing test(s) into baselines/known_failing.txt" % n,
                "details": {"failing": failing, "new": []}}
    known = _known_failing(ctx)
    new = [t for t in failing if t not in known]
    return _tests_verdict(failing, new, known, rc_ok, text, runs=len(runs))


def _coverage_total(ctx):
    path = _out(ctx, "coverage.json")
    if not os.path.exists(path):
        return None
    return lc.read_json(path).get("totals", {}).get("percent_covered")


def _post_coverage_project(ctx, plan, runs):
    total = _coverage_total(ctx)
    shown = "total %.1f%%" % total if total is not None else "coverage.json missing"
    return {"summary": "%s (floor = project's qa/coverage_floor.txt)" % shown, "details": {"total": total}}


def _no_drop_verdict(total, floor):
    if floor is None:
        return {"status": "substituted", "summary": "total %.1f%% measured only — no-drop not armed "
                "(run --init-baselines)" % total, "details": {"total": total}}
    status = "fail" if total < floor - 0.1 else "pass"
    return {"status": status, "summary": "total %.1f%% vs baseline %.1f%%" % (total, floor),
            "details": {"total": total, "baseline": floor}}


def _post_coverage_generic(ctx, plan, runs):
    """无项目地板时的 no-drop：对 baselines/coverage_total.txt 比较（容差 0.1）。"""
    total = _coverage_total(ctx)
    if total is None or not all(r.ok for r in runs):
        return {"status": "fail", "summary": "coverage run failed or produced no coverage.json", "details": {}}
    path = _ledger_path(ctx, "coverage_total")
    if ctx.get("init_baselines"):
        lc.write_ledger(path, {"total": round(total, 1)}, "coverage total baseline (no-drop)")
        return {"status": "pass", "summary": "total %.1f%% recorded as no-drop baseline" % total,
                "details": {"total": total}}
    return _no_drop_verdict(total, lc.load_ledger(path).get("total"))


def _tally_survivors(report, equivalent):
    """mutate.py 报告 → (存活体[排除已判等价], killed, executed)；timeout 记 killed 侧（同 §57）。"""
    survivors, killed, total = [], 0, 0
    for rel, mod in sorted(report.get("modules", {}).items()):
        killed += mod.get("killed", 0) + mod.get("timeout", 0)
        total += mod.get("executed", 0)
        survivors += [dict(s, module=rel) for s in mod.get("survivors", [])
                      if s.get("location") not in equivalent]
    return survivors, killed, total


def _post_mutation(ctx, plan, runs):
    path = _out(ctx, "mutation.json")
    if not os.path.exists(path):
        return {"status": "fail", "summary": "mutation runner produced no mutation.json — fail closed", "details": {}}
    report = lc.read_json(path)
    equivalent = set(ctx["sel"].get("equivalent_mutants") or [])
    survivors, killed, total = _tally_survivors(report, equivalent)
    status = "fail" if survivors else "pass"
    summary = "%d/%d killed, %d surviving (%d classified equivalent)" % (killed, total, len(survivors), len(equivalent))
    return {"status": status, "summary": summary,
            "details": {"survivors": survivors, "complete": report.get("complete")}}


# --------------------------------------------------------------------------- #
# 自制检查（internal）—— 全部 fail closed，全部走 shrink-only 账本
# --------------------------------------------------------------------------- #

def _ledger_result(ctx, name, violations, ok_summary):
    path = _ledger_path(ctx, name)
    rel = os.path.relpath(path, ctx["repo"])
    if ctx.get("init_baselines"):
        n = lc.write_ledger(path, violations, "%s baseline" % name)
        return _res("pass", "%d finding(s) grandfathered into %s" % (n, rel),
                    {"grandfathered": sorted(violations), "total": len(violations)})
    cmp = lc.compare_ledger(violations, lc.load_ledger(path))
    details = {"new": cmp["new"], "worse": cmp["worse"], "stale": cmp["stale"], "total": len(violations)}
    if cmp["ok"]:
        extra = " (%d pre-existing on ledger)" % len(violations) if violations else ""
        return _res("pass", ok_summary + extra, details)
    return _res("fail", "%d NEW / %d WORSE vs %s" % (len(cmp["new"]), len(cmp["worse"]), rel), details)


def _scan_files(repo, files, fn):
    """逐文件 fn(rel, text) → 合并违例；读不到/解析不了记 errors（caller fail closed）。"""
    violations, errors = {}, []
    for rel in files:
        try:
            text = lc.read_text(os.path.join(repo, rel))
            if text is not None:
                violations.update(fn(rel, text))
        except (OSError, SyntaxError, ValueError) as exc:
            errors.append("%s: %s: %s" % (rel, type(exc).__name__, exc))
    return violations, errors


def _finish_scan(ctx, name, scanned, ok_summary):
    violations, errors = scanned
    if errors:
        return _res("fail", "%d unreadable/unparseable file(s) — fail closed" % len(errors), {"errors": errors})
    return _ledger_result(ctx, name, violations, ok_summary)


# secret scan ---------------------------------------------------------------- #

SECRET_RULES = [
    ("aws-access-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github-token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,}\b|\bgithub_pat_[A-Za-z0-9_]{60,}\b")),
    ("slack-token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
    ("private-key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----")),
    ("anthropic-key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b")),
    ("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    # 值里至少一个数字：真密钥几乎必含数字；设计 token（`token: "text-primary-strong"`）不含——
    # 首次实跑在 web/src/styles/typeScale.ts 上撞出 11 条误报。
    ("generic-assignment", re.compile(
        r"(?i)\b(?:api[_-]?key|secret|token|passwd|password)\b\s*[:=]\s*['\"](?=[A-Za-z0-9/+_\-]*\d)[A-Za-z0-9/+_\-]{20,}['\"]")),
]


def _line_hash(line):
    return hashlib.sha1(line.strip().encode("utf-8")).hexdigest()[:10]


def _secret_hits(rel, text):
    out = {}
    for line in text.splitlines():
        for rule, rx in SECRET_RULES:
            if rx.search(line):
                out["%s::%s::%s" % (rel, rule, _line_hash(line))] = 1.0
    return out


def check_secret_scan(ctx):
    return _finish_scan(ctx, "secret_scan", _scan_files(ctx["repo"], _files(ctx), _secret_hits),
                        "no secret-shaped strings in tracked files")


# GitHub Actions SHA pin ----------------------------------------------------- #

_USES_RE = re.compile(r"^\s*-?\s*uses:\s*[\"']?([^\s\"'#]+)")
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def _unpinned(uses):
    if uses.startswith("./") or uses.startswith("docker://"):
        return False
    ref = uses.rsplit("@", 1)[1] if "@" in uses else ""
    return not _SHA_RE.match(ref)


def _pin_violations(rel, text):
    out = {}
    for line in text.splitlines():
        match = _USES_RE.match(line)
        if match and _unpinned(match.group(1)):
            out["%s::%s" % (rel, match.group(1))] = 1.0
    return out


def check_actions_sha_pin(ctx):
    files = [f for f in _files(ctx)
             if f.startswith(".github/workflows/") and f.endswith((".yml", ".yaml"))]
    if not files:
        return _res("na", "no GitHub Actions workflows")
    return _finish_scan(ctx, "actions_sha_pin", _scan_files(ctx["repo"], files, _pin_violations),
                        "every `uses:` pinned to a 40-hex SHA")


# test smells ---------------------------------------------------------------- #

_ASSERTISH = re.compile(r"assert|raises|fail|expect|check|verify", re.I)
_IO_RE = re.compile(r"^(subprocess|urllib|requests|socket|httpx|http\.client)(\.|$)")


def _call_name(node):
    fn = node.func
    if isinstance(fn, ast.Attribute):
        return fn.attr
    if isinstance(fn, ast.Name):
        return fn.id
    return ""


def _node_flags(sub):
    """(断言形?, sleep?) —— 单节点。"""
    if isinstance(sub, ast.Assert):
        return True, False
    if not isinstance(sub, ast.Call):
        return False, False
    name = _call_name(sub)
    return bool(_ASSERTISH.search(name)), name == "sleep"


def _smells_in_test(node):
    flags = [_node_flags(sub) for sub in ast.walk(node)]
    has_assert = any(a for a, _ in flags)
    sleeps = any(s for _, s in flags)
    return [name for name, hit in (("no-assert", not has_assert), ("sleep", sleeps)) if hit]


def _import_names(node):
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if isinstance(node, ast.ImportFrom) and node.module:
        return [node.module]
    return []


def _io_imports(tree):
    names = {n for node in ast.walk(tree) for n in _import_names(node)}
    return sorted(n for n in names if _IO_RE.match(n))


def _smell_violations(rel, text, is_unit):
    tree = ast.parse(text, filename=rel)
    out = {}
    for qual, node in cm.collect_functions(tree):
        if not node.name.startswith("test"):
            continue
        for smell in _smells_in_test(node):
            out["%s:%s::%s" % (smell, rel, qual)] = 1.0
    if is_unit:
        out.update({"real-io:%s::%s" % (rel, mod): 1.0 for mod in _io_imports(tree)})
    return out


def check_test_smells(ctx):
    layout = _layout(ctx)
    tests_dir = layout.get("tests_dir")
    if not tests_dir:
        return _res("na", "no python tests dir (JS/Swift smell scan is not in v1)")
    integ = (layout.get("integration_dir") or "\0") + "/"
    files = [f for f in _files(ctx) if _is_test_file(f, tests_dir)]

    def fn(rel, text):
        return _smell_violations(rel, text, not rel.startswith(integ))

    return _finish_scan(ctx, "test_smells", _scan_files(ctx["repo"], files, fn),
                        "no no-assert / sleep / real-io smells in unit tests")


# docs drift ----------------------------------------------------------------- #

_TICK_RE = re.compile(r"`([^`\s]+)`")
# 源码/配置类扩展名才算「应当存在的仓库路径」；.json/.txt 多为运行时生成的数据文件
# （config/runtime.json、secrets/*.txt），首次实跑证明它们只产生误报。
_DOC_EXT = (".py", ".ts", ".tsx", ".js", ".mjs", ".sh", ".swift", ".toml", ".yaml",
            ".yml", ".md", ".html", ".css", ".plist")
_BAD_CHARS = set("*?{}<>$~\\")


def _path_token(token):
    """反引号里长得像仓库相对路径的 token；否则 None。"""
    clean = token.rstrip(".,:;)")
    if "/" not in clean or clean.startswith(("/", "http", "./", "../")) or _BAD_CHARS & set(clean):
        return None
    return clean if clean.endswith(_DOC_EXT) else None


def _ignored_literals(repo):
    """.gitignore 里不带通配符的字面路径/文件名：文档提到它们 = 声明过的生成物（act/_version.py），不算悬空。"""
    out = set()
    for raw in lc.read_text_or_empty(os.path.join(repo, ".gitignore")).splitlines():
        line = raw.split("#", 1)[0].strip().lstrip("/").rstrip("/")
        if line and not any(ch in line for ch in "*?[]!"):
            out.add(line)
    return out


def _drift_scanner(repo, files):
    """只判「首段是仓库顶层目录」的 token（`state/x.json` 这类运行时路径不算悬空）；
    存在性看 tracked 文件集 + 目录，不看工作区里碰巧有没有生成物；.gitignore 字面声明的
    生成物也不算。"""
    known = set(files)
    top_dirs = {f.split("/")[0] for f in files if "/" in f}
    ignored = _ignored_literals(repo)

    def dangling(path):
        if path in ignored or os.path.basename(path) in ignored:
            return False
        return path.split("/")[0] in top_dirs and path not in known and not os.path.isdir(os.path.join(repo, path))

    def fn(rel, text):
        out = {}
        for token in _TICK_RE.findall(text):
            path = _path_token(token)
            if path and dangling(path):
                out["%s::%s" % (rel, path)] = 1.0
        return out
    return fn


def check_docs_drift(ctx):
    """CHANGELOG* 免检：历史条目引用已删文件是正常的。"""
    files = [f for f in _files(ctx) if f.endswith(".md") and not os.path.basename(f).startswith("CHANGELOG")]
    if not files:
        return _res("na", "no markdown docs")
    scanner = _drift_scanner(ctx["repo"], _files(ctx))
    return _finish_scan(ctx, "docs_drift", _scan_files(ctx["repo"], files, scanner),
                        "every backticked repo path in docs exists")


# diff coverage -------------------------------------------------------------- #

def _diff_cov_tally(files, py_added):
    uncovered, measured, unmeasured = [], 0, []
    for path, lines in sorted(py_added.items()):
        data = files.get(path)
        if data is None:
            unmeasured.append(path)
            continue
        missing = set(data.get("missing_lines", ())) & lines
        known = (set(data.get("executed_lines", ())) | set(data.get("missing_lines", ()))) & lines
        measured += len(known)
        uncovered += ["%s:%d" % (path, n) for n in sorted(missing)]
    return uncovered, measured, unmeasured


def check_diff_coverage(ctx):
    cov_path = _out(ctx, "coverage.json")
    if not os.path.exists(cov_path):
        return _res("unavailable", "no coverage.json in this run (select py_coverage)")
    py_added = {p: set(lines) for p, lines in _diff(ctx)["added"].items() if p.endswith(".py")}
    if not py_added:
        return _res("na", "no python lines added vs base")
    uncovered, measured, unmeasured = _diff_cov_tally(lc.read_json(cov_path).get("files", {}), py_added)
    covered = measured - len(uncovered)
    status = "fail" if uncovered else "pass"
    return _res(status, "%d/%d added statements covered; %d file(s) outside coverage source"
                % (covered, measured, len(unmeasured)),
                {"uncovered": uncovered, "unmeasured": unmeasured, "measured": measured})


# field add-only ------------------------------------------------------------- #

_SCHEMAISH = re.compile(r"schema|contract|wire|model|types|proto|\.(json|ya?ml|proto|graphql)$", re.I)
_KEY_RE = re.compile(r"^\s*[\"']?([A-Za-z_][A-Za-z0-9_]*)[\"']?\s*[:=]")


def _removed_keys(path, lines):
    matches = [_KEY_RE.match(line) for line in lines]
    return ["%s: -%s" % (path, m.group(1)) for m in matches if m]


def check_field_add_only(ctx):
    diff = _diff(ctx)
    schema_files = [p for p in diff["changed_files"]
                    if _SCHEMAISH.search(p) and not p.endswith(_PROSE_EXT)]
    if not schema_files:
        return _res("pass", "no schema-ish files in the diff", {"files": []})
    hits = [h for p in schema_files for h in _removed_keys(p, diff["removed"].get(p, []))]
    status = "fail" if hits else "pass"
    return _res(status, "%d key(s) removed from %d schema-ish file(s) — fields are add-only"
                % (len(hits), len(schema_files)), {"files": schema_files, "removed_keys": hits})


# diff minimality ------------------------------------------------------------ #

def check_diff_minimality(ctx):
    declared = ctx["sel"].get("declared_files") or []
    changed = _diff(ctx)["changed_files"]
    if not changed:
        return _res("pass", "no changed files vs base — nothing to bound", {"outside": [], "changed": []})
    if not declared:
        return _res("unavailable", "no declared file set (selection.declared_files / --declared); "
                    "%d changed file(s) listed for review" % len(changed), {"changed": changed})
    outside = [p for p in changed if not any(fnmatch.fnmatch(p, g) for g in declared)]
    status = "fail" if outside else "pass"
    return _res(status, "%d changed file(s) outside the declared set" % len(outside),
                {"outside": outside, "declared": declared, "changed": changed})


# dependency budget ---------------------------------------------------------- #

MANIFEST_RE = re.compile(
    r"(^|/)(requirements[^/]*\.txt|pyproject\.toml|setup\.py|setup\.cfg|Pipfile(\.lock)?|"
    r"package\.json|package-lock\.json|yarn\.lock|pnpm-lock\.yaml|Package\.swift|Package\.resolved|"
    r"go\.mod|go\.sum|Cargo\.toml|Cargo\.lock|pom\.xml|build\.gradle[^/]*|build\.sbt)$")


def _added_manifest_lines(diff, manifests):
    return {p: [line for line in diff["added_text"].get(p, []) if line.strip()] for p in manifests}


def _undeclared(added, declared):
    out = {}
    for path, lines in added.items():
        rest = [line for line in lines if not any(d in line for d in declared)]
        if rest:
            out[path] = rest
    return out


def check_dependency_budget(ctx):
    diff = _diff(ctx)
    manifests = [p for p in diff["changed_files"] if MANIFEST_RE.search(p)]
    if not manifests:
        return _res("pass", "dependency manifests unchanged", {"manifests": []})
    added = _added_manifest_lines(diff, manifests)
    declared = ctx["sel"].get("declared_deps")
    if declared is None:
        return _res("unavailable", "manifests changed but selection.declared_deps missing — added lines listed",
                    {"manifests": manifests, "added": added})
    undeclared = _undeclared(added, declared)
    status = "fail" if undeclared else "pass"
    return _res(status, "%d manifest(s) with undeclared additions" % len(undeclared),
                {"manifests": manifests, "added": added, "undeclared": undeclared})


# CRAP fallback (generic projects) ------------------------------------------- #

def crap_score(cc, cov):
    return round(cc * cc * (1.0 - cov) ** 3 + cc, 1)


def _span_cov(start, end, executed, missing):
    span = set(range(start, end + 1))
    known = span & (executed | missing)
    if not known:
        return 1.0
    return len(span & executed) / len(known)


def _crap_file(repo, rel, data, scores, errors):
    try:
        text = lc.read_text(os.path.join(repo, rel))
        measured = cm.measure_source(text or "", rel)
    except (OSError, SyntaxError, ValueError) as exc:
        errors.append("%s: %s" % (rel, exc))
        return
    executed, missing = set(data.get("executed_lines", ())), set(data.get("missing_lines", ()))
    for qual, cc, _lines, start, end in measured["functions"]:
        scores["%s::%s" % (rel, qual)] = crap_score(cc, _span_cov(start, end, executed, missing))


def check_crap_fallback(ctx):
    cov_path = _out(ctx, "coverage.json")
    if not os.path.exists(cov_path):
        return _res("unavailable", "no coverage.json in this run (select py_coverage)")
    threshold = float(ctx["det"]["thresholds"]["crap_max"])
    scores, errors = {}, []
    files = lc.read_json(cov_path).get("files", {})
    for rel in sorted(files):
        _crap_file(ctx["repo"], rel, files[rel], scores, errors)
    if errors:
        return _res("fail", "%d source file(s) unreadable — fail closed" % len(errors), {"errors": errors})
    violations = {k: v for k, v in scores.items() if v > threshold}
    return _ledger_result(ctx, "crap", violations, "no function above CRAP %s" % threshold)


# --------------------------------------------------------------------------- #
# builders —— 第 1 档 静态门
# --------------------------------------------------------------------------- #

def _b_py_compile(ctx):
    roots = _layout(ctx).get("py_roots") or ["."]
    return _need_python(ctx) or _cmd([ctx["py"], "-m", "compileall", "-q"] + roots, ctx["repo"], tool="python")


def _b_py_lint(ctx):
    if _need_python(ctx):
        return _need_python(ctx)
    for tool in ("ruff", "flake8"):
        if _tool(ctx, tool):
            return _cmd([tool, "check", "."] if tool == "ruff" else [tool, "."], ctx["repo"], tool=tool)
    return _unavailable("no ruff/flake8 on PATH (`uvx ruff check .` works without installing)")


_FORMATTERS = {"ruff": ["ruff", "format", "--check", "."], "black": ["black", "--check", "."]}


def _b_py_format(ctx):
    argv = _FORMATTERS.get(_layout(ctx).get("py_format") or "")
    if not argv:
        return _na("no formatter configured (pyproject [tool.ruff.format] / [tool.black])")
    if not _tool(ctx, argv[0]):
        return _unavailable("%s configured but not on PATH" % argv[0])
    return _cmd(argv, ctx["repo"], tool=argv[0])


def _js_steps(ctx, pkgs, argv):
    return [_step(argv, os.path.join(ctx["repo"], p["dir"])) for p in pkgs]


def _b_ts_typecheck(ctx):
    pkgs = [p for p in _js_pkgs(ctx) if p.get("tsconfig")]
    if not pkgs:
        return _na("no tsconfig.json")
    blocked = _js_ready(ctx, pkgs, ["tsc"])
    if blocked:
        return blocked
    return _steps("cmd", _js_steps(ctx, pkgs, ["npx", "--no-install", "tsc", "--noEmit", "-p", "."]), "tsc")


def _b_js_lint(ctx):
    pkgs = [p for p in _js_pkgs(ctx) if p.get("eslint")]
    if not pkgs:
        return _na("no eslint config in any package dir")
    blocked = _js_ready(ctx, pkgs, ["eslint"])
    if blocked:
        return blocked
    return _steps("cmd", _js_steps(ctx, pkgs, ["npx", "--no-install", "eslint", "."]), "eslint")


def _b_shellcheck(ctx):
    files = [f for f in _files(ctx) if f.endswith(".sh")]
    if not files:
        return _na("no .sh files")
    if not _tool(ctx, "shellcheck"):
        return _unavailable("shellcheck not on PATH")
    return _cmd(["shellcheck"] + files, ctx["repo"], tool="shellcheck")


def _b_swift_parse(ctx):
    """一个文件一次 swiftc -parse：合并调用会被同名文件（filename used twice）和多个
    带 top-level 语句的脚本文件拒掉——首次实跑两种都撞上了。"""
    files = [f for f in _files(ctx) if f.endswith(".swift")]
    if not files:
        return _na("no .swift files")
    if not _tool(ctx, "swiftc"):
        return _unavailable("swiftc not on PATH")
    steps = [_step(["swiftc", "-parse", rel], ctx["repo"]) for rel in files]
    return _steps("cmd", steps, "swiftc")


def _b_deps_direction(ctx):
    if _has(ctx, "scripts/qa/depgraph.py"):
        return _cmd([ctx["py"], "scripts/qa/depgraph.py", "--check"], ctx["repo"], tool="python",
                    post=_post_ledger_verdict)
    if _layout(ctx).get("importlinter"):
        if _tool(ctx, "lint-imports"):
            return _cmd(["lint-imports"], ctx["repo"], tool="lint-imports")
        return _unavailable("import-linter configured but lint-imports not on PATH")
    return _na("no dependency-direction rules declared (scripts/qa/depgraph.py / import-linter)")


def _complexity_min_plan(ctx, only, ledger_name, caps_argv):
    roots = _layout(ctx).get("py_src_roots") or ["."]
    argv = [ctx["py"], _skill_script(ctx, "complexity_min.py"), "--only", only, "--root", ctx["repo"]] + caps_argv
    ledger = _ledger_path(ctx, ledger_name)
    if ctx.get("init_baselines"):
        argv += ["--baseline", ledger, "--write-baseline"]
    elif os.path.exists(ledger):
        argv += ["--baseline", ledger]
    return _cmd(argv + roots, ctx["repo"], tool="complexity_min", post=_post_ledger_verdict)


def _b_length_caps(ctx):
    if _has(ctx, "scripts/qa/hygiene.py"):
        return _cmd([ctx["py"], "scripts/qa/hygiene.py", "--check"], ctx["repo"], tool="python",
                    post=_post_ledger_verdict)
    thr = ctx["det"]["thresholds"]
    caps = ["--max-func-lines", str(thr["max_function_lines"]), "--max-file-lines", str(thr["max_file_lines"])]
    return _need_python(ctx) or _complexity_min_plan(ctx, "lengths", "length_caps", caps)


def _b_secret_scan(ctx):
    return _internal(check_secret_scan)


def _b_actions_sha_pin(ctx):
    return _internal(check_actions_sha_pin)


# --------------------------------------------------------------------------- #
# builders —— 第 2 档 单元 + 尺子
# --------------------------------------------------------------------------- #

def _b_py_unit(ctx):
    blocked = _need_python(ctx) or _no_tests_dir(ctx)
    if blocked:
        return blocked
    if _layout(ctx).get("py_runner") == "pytest" and not _pymod(ctx, "pytest"):
        return _unavailable("project uses pytest but it is not importable")
    return _cmd(_py_test_argv(ctx), ctx["repo"], tool="python-tests", post=_post_tests)


_JS_TEST_ARGV = {"vitest": ["npx", "--no-install", "vitest", "run"], "jest": ["npx", "--no-install", "jest", "--ci"]}


def _b_js_unit(ctx):
    pkgs = [p for p in _js_pkgs(ctx) if p.get("test_runner") in _JS_TEST_ARGV]
    if not pkgs:
        return _na("no vitest/jest in any package.json")
    blocked = next((b for b in (_js_ready(ctx, [p], [p["test_runner"]]) for p in pkgs) if b), None)
    if blocked:
        return blocked
    steps = [_step(_JS_TEST_ARGV[p["test_runner"]], os.path.join(ctx["repo"], p["dir"])) for p in pkgs]
    return _steps("cmd", steps, "js-tests", post=_post_tests)


def _b_swift_unit(ctx):
    swift = _layout(ctx).get("swift") or {}
    if "swift" not in _stacks(ctx):
        return _na("no swift sources")
    if swift.get("package_dir") is not None:
        return _b_swift_package(ctx, swift["package_dir"])
    return _b_swift_xcode(ctx, swift.get("schemes") or [])


def _b_swift_package(ctx, package_dir):
    if not _tool(ctx, "swift"):
        return _unavailable("swift toolchain not on PATH")
    return _cmd(["swift", "test"], os.path.join(ctx["repo"], package_dir), tool="swift")


def _b_swift_xcode(ctx, schemes):
    if not schemes:
        return _na("no Package.swift and no shared .xcscheme found")
    if not _tool(ctx, "xcodebuild"):
        return _unavailable("xcodebuild not on PATH")
    first = schemes[0]
    argv = ["xcodebuild", "test", "-scheme", first["scheme"], "-destination", "platform=macOS"]
    return _cmd(argv, os.path.join(ctx["repo"], first["dir"]), tool="xcodebuild")


def _b_py_coverage(ctx):
    blocked = _need_python(ctx) or _no_tests_dir(ctx)
    if blocked:
        return blocked
    if not _pymod(ctx, "coverage"):
        return _unavailable("coverage.py not importable (pip install coverage — dev side only)")
    if _has(ctx, "scripts/qa/run_coverage.sh"):
        return _project_coverage_plan(ctx)
    return _generic_coverage_plan(ctx)


def _generic_coverage_plan(ctx):
    env = dict(os.environ, COVERAGE_FILE=_out(ctx, ".coverage"))
    src = ",".join(_layout(ctx).get("py_src_roots") or ["."])
    run = [ctx["py"], "-m", "coverage", "run", "--source=" + src, "-m"] + _py_test_argv(ctx)[2:]
    steps = [_step(run, ctx["repo"], env),
             _step([ctx["py"], "-m", "coverage", "json", "-o", _out(ctx, "coverage.json")], ctx["repo"], env)]
    return _steps("cmd", steps, "coverage", post=_post_coverage_generic)


def _project_coverage_plan(ctx):
    steps = [_step(["bash", "scripts/qa/run_coverage.sh", ctx["out"] or ".qa-report"], ctx["repo"])]
    if _has(ctx, "scripts/qa/coverage_floor.py"):
        steps.append(_step([ctx["py"], "scripts/qa/coverage_floor.py", "--coverage-json",
                            _out(ctx, "coverage.json")], ctx["repo"]))
    return _steps("cmd", steps, "coverage", post=_post_coverage_project)


def _vitest_cov_ready(ctx, pkgs):
    blocked = _js_ready(ctx, pkgs, ["vitest"])
    if blocked:
        return blocked
    missing = [p["dir"] for p in pkgs if not p.get("coverage_provider")]
    if missing:
        return _unavailable("@vitest/coverage-* not installed in %s" % missing)
    return None


def _b_js_coverage(ctx):
    pkgs = [p for p in _js_pkgs(ctx) if p.get("test_runner") == "vitest"]
    if not pkgs:
        return _na("no vitest package (JS coverage in v1 = vitest --coverage)")
    blocked = _vitest_cov_ready(ctx, pkgs)
    if blocked:
        return blocked
    steps = _js_steps(ctx, pkgs, ["npx", "--no-install", "vitest", "run", "--coverage"])
    if all(p.get("coverage_thresholds") for p in pkgs):
        return _steps("cmd", steps, "vitest")
    return _steps("substituted", steps, "vitest",
                  note="vitest config has no coverage.thresholds — measured, not gated")


def _b_diff_coverage(ctx):
    return _need_python(ctx) or _internal(check_diff_coverage)


def _b_complexity(ctx):
    if _has(ctx, "scripts/qa/complexity.py"):
        return _cmd([ctx["py"], "scripts/qa/complexity.py", "--check"], ctx["repo"], tool="python",
                    post=_post_ledger_verdict)
    caps = ["--max-cc", str(ctx["det"]["thresholds"]["complexity_max"])]
    return _need_python(ctx) or _complexity_min_plan(ctx, "cc", "complexity", caps)


def _b_crap(ctx):
    if _has(ctx, "scripts/qa/crap.py"):
        argv = [ctx["py"], "scripts/qa/crap.py", "--check", "--coverage-json", _out(ctx, "coverage.json")]
        return _cmd(argv, ctx["repo"], tool="python", post=_post_ledger_verdict)
    return _need_python(ctx) or _internal(check_crap_fallback)


# --------------------------------------------------------------------------- #
# builders —— 第 3 档 集成 + 契约
# --------------------------------------------------------------------------- #

def _b_py_integration(ctx):
    integ = _layout(ctx).get("integration_dir")
    if not integ:
        return _na("no tests/integration dir")
    if _layout(ctx).get("py_runner") == "pytest":
        return _cmd([ctx["py"], "-m", "pytest", "-q", integ], ctx["repo"], tool="python-tests", post=_post_tests)
    argv = [ctx["py"], "-m", "unittest", "discover", "-s", integ, "-t", ctx["repo"]]
    return _cmd(argv, ctx["repo"], tool="python-tests", post=_post_tests)


def _e2e_for_pkg(ctx, pkg):
    cwd = os.path.join(ctx["repo"], pkg["dir"])
    script = next((s for s in ("test:e2e", "e2e") if s in pkg.get("scripts", {})), None)
    if script:
        return _cmd(["npm", "run", script], cwd, tool="npm")
    if _pkg_has(pkg, "playwright"):
        return _cmd(["npx", "--no-install", "playwright", "test"], cwd, tool="playwright")
    return None


def _b_js_e2e(ctx):
    for pkg in _js_pkgs(ctx):
        plan = _e2e_for_pkg(ctx, pkg)
        if plan:
            return plan
    return _na("no e2e script / playwright config")


def _tier_subset(ctx, pattern, what):
    files = _py_tests_matching(ctx, pattern)
    if not files:
        return _na("no python test file matches /%s/ (%s)" % (pattern, what))
    return _subset_plan(ctx, files)


def _b_golden_contract(ctx):
    return _tier_subset(ctx, r"golden|contract|snapshot|wire", "golden/contract files")


def _b_migration_roundtrip(ctx):
    return _tier_subset(ctx, r"migrat|round_?trip|parity|export|upgrade", "migration round-trip")


def _b_field_add_only(ctx):
    return _internal(check_field_add_only)


# --------------------------------------------------------------------------- #
# builders —— 第 4 档 变异 + 稳定性
# --------------------------------------------------------------------------- #

def _mutate_plan(ctx, modules, budget):
    argv = [ctx["py"], "scripts/qa/mutate.py", "--json", _out(ctx, "mutation.json"), "--md", _out(ctx, "mutation.md"),
            "--state", _out(ctx, "mutation_state.json"), "--force", "--time-budget", str(budget)]
    argv += ["--all"] if modules is None else ["--modules"] + modules
    return _cmd(argv, ctx["repo"], tool="python", post=_post_mutation)


def _generic_mutation(ctx):
    if _layout(ctx).get("mutmut") and _tool(ctx, "mutmut"):
        return _cmd(["mutmut", "run"], ctx["repo"], tool="mutmut")
    stryker = [p for p in _js_pkgs(ctx) if _pkg_has(p, "stryker")]
    if stryker:
        return _steps("cmd", _js_steps(ctx, stryker, ["npx", "--no-install", "stryker", "run"]), "stryker")
    return _unavailable("no mutation tool (scripts/qa/mutate.py / mutmut / stryker) — "
                        "manual procedure: references/adapters.md")


def _b_mutation_changed(ctx):
    if not _has(ctx, "scripts/qa/mutate.py"):
        return _generic_mutation(ctx)
    targets = set(ctx["det"].get("mutation_targets") or [])
    changed = sorted(targets & set(_diff(ctx)["changed_files"]))
    if not changed:
        return _na("no changed module in qa/mutation_targets.toml (constitution modules)")
    return _mutate_plan(ctx, changed, int(ctx["sel"].get("mutation_budget") or 1800))


def _b_mutation_full(ctx):
    if not _has(ctx, "scripts/qa/mutate.py"):
        return _generic_mutation(ctx)
    return _mutate_plan(ctx, None, 7 * 24 * 3600)


def _b_property_tests(ctx):
    files = _layout(ctx).get("property_tests") or []
    if not files:
        return _na("no python test imports hypothesis")
    if not _pymod(ctx, "hypothesis"):
        return _unavailable("hypothesis not importable (pip install hypothesis — dev side only)")
    return _subset_plan(ctx, files)


def _b_flaky_detect(ctx):
    reruns = int(ctx["sel"].get("reruns") or 3)
    layout = _layout(ctx)
    if not layout.get("tests_dir"):
        return _na("no python tests dir (JS shuffle: vitest --sequence.shuffle, not wired in v1)")
    if layout.get("py_runner") == "pytest" and _pymod(ctx, "pytest_randomly"):
        step = _step([ctx["py"], "-m", "pytest", "-q", "-p", "randomly"], ctx["repo"])
        return _steps("cmd", [step] * reruns, "python-tests", post=_post_tests)
    step = _step(_py_test_argv(ctx), ctx["repo"])
    return _steps("substituted", [step] * reruns, "python-tests", post=_post_tests,
                  note="%d plain reruns — cannot detect whole-suite order dependence (no shuffle plugin)" % reruns)


def _b_test_smells(ctx):
    return _internal(check_test_smells)


# --------------------------------------------------------------------------- #
# builders —— 第 5 档 通宵 / 通几天
# --------------------------------------------------------------------------- #

def _b_fuzz(ctx):
    if _layout(ctx).get("fuzz_dir"):
        return _unavailable("fuzz/ harness found but no runner wired in v1 — run it by hand and paste the result")
    return _na("no fuzz/ harness in project")


def _b_soak_race(ctx):
    files = _py_tests_matching(ctx, r"race|concurren|thread|lock|parallel|stress|writers")
    if not files:
        return _na("no race/concurrency test files")
    return _subset_plan(ctx, files, int(ctx["sel"].get("soak_reruns") or 20))


def _security_steps(ctx):
    steps = []
    if _tool(ctx, "bandit"):
        steps.append(_step(["bandit", "-q", "-r"] + (_layout(ctx).get("py_src_roots") or ["."]), ctx["repo"]))
    if _tool(ctx, "gitleaks"):
        steps.append(_step(["gitleaks", "detect", "--no-banner", "--redact", "-s", "."], ctx["repo"]))
    return steps + _pip_audit_steps(ctx) + _audit_steps(ctx)


def _pip_audit_steps(ctx):
    reqs = _layout(ctx).get("requirements") or []
    if not reqs or not _tool(ctx, "pip-audit"):
        return []
    return [_step(["pip-audit", "-r", reqs[0]], ctx["repo"])]


def _npm_audit_steps(ctx):
    if not _tool(ctx, "npm"):
        return []
    return _js_steps(ctx, [p for p in _js_pkgs(ctx) if p.get("lock")], ["npm", "audit", "--audit-level=high"])


_NATIVE_AUDITS = (("rust", "cargo-audit", ["cargo", "audit"]), ("go", "govulncheck", ["govulncheck", "./..."]))


def _audit_steps(ctx):
    steps = _npm_audit_steps(ctx)
    for stack, tool, argv in _NATIVE_AUDITS:
        if stack in _stacks(ctx) and _tool(ctx, tool):
            steps.append(_step(argv, ctx["repo"]))
    return steps


def _b_security_scan(ctx):
    steps = _security_steps(ctx)
    if not steps:
        return _unavailable("no security scanner on PATH (bandit / gitleaks / pip-audit / npm audit / "
                            "cargo-audit / govulncheck)")
    return _steps("cmd", steps, "security")


def _qlty_steps(ctx):
    if _layout(ctx).get("qlty") and _tool(ctx, "qlty"):
        return [_step(["qlty", "check", "--all", "--no-progress"], ctx["repo"])]
    return []


def _b_arch_audit(ctx):
    steps = _qlty_steps(ctx)
    for script in ("hygiene.py", "depgraph.py"):
        if _has(ctx, "scripts/qa/" + script):
            steps.insert(0, _step([ctx["py"], "scripts/qa/" + script, "--check"], ctx["repo"]))
    if not steps:
        return _na("no architecture rules to audit (scripts/qa/depgraph.py / hygiene.py / .qlty)")
    return _steps("cmd", steps, "arch", post=_post_ledger_verdict)


def _b_perf_budget(ctx):
    if _layout(ctx).get("benchmarks") and _pymod(ctx, "pytest_benchmark"):
        return _cmd([ctx["py"], "-m", "pytest", "-q", "benchmarks", "--benchmark-only"], ctx["repo"],
                    tool="python-tests")
    for pkg in _js_pkgs(ctx):
        if "bench" in pkg.get("scripts", {}):
            return _cmd(["npm", "run", "bench"], os.path.join(ctx["repo"], pkg["dir"]), tool="npm")
    return _na("no performance budget declared (benchmarks/ + pytest-benchmark, or npm run bench)")


def _b_docs_drift(ctx):
    return _internal(check_docs_drift)


def _vulture_plan(ctx):
    if "python" not in _stacks(ctx) or not _tool(ctx, "vulture"):
        return None
    roots = _layout(ctx).get("py_src_roots") or ["."]
    return _cmd(["vulture", "--min-confidence", "80"] + roots, ctx["repo"], tool="vulture")


def _b_dead_code(ctx):
    plan = _vulture_plan(ctx)
    if plan:
        return plan
    knip = [p for p in _js_pkgs(ctx) if "knip" in p.get("bins", [])]
    if knip:
        return _steps("cmd", _js_steps(ctx, knip, ["npx", "--no-install", "knip"]), "knip")
    return _unavailable("no dead-code tool (vulture / knip)")


# --------------------------------------------------------------------------- #
# builders —— 触发器加挂层
# --------------------------------------------------------------------------- #

def _b_crash_recovery(ctx):
    return _trigger_subset(ctx, "persisted_state", r"crash|truncat|recover|corrupt|partial|atomic|activation")


def _b_fault_injection(ctx):
    return _trigger_subset(ctx, "boundary", r"fault|inject|timeout|failure|unreachable|eperm|enospc|retry|blind")


def _b_race_stress(ctx):
    return _trigger_subset(ctx, "concurrency", r"race|concurren|thread|lock|parallel|stress|writers",
                           reruns=int(ctx["sel"].get("race_reruns") or 10))


def _b_resource_leak(ctx):
    return _trigger_subset(ctx, "spawns_processes", r"leak|orphan|\bfd\b|descriptor|resource|cleanup|zombie|gc")


def _b_corpus_regression(ctx):
    return _trigger_subset(ctx, "persisted_format", r"corpus|fixture|compat|legacy|migrat|schema|upgrade|golden")


def _b_contract_drift(ctx):
    return _trigger_subset(ctx, "documented_behavior", r"contract|drift|help|docs|golden|prompt|readme")


def _b_diff_minimality(ctx):
    return _internal(check_diff_minimality)


def _b_dependency_audit(ctx):
    steps = _pip_audit_steps(ctx) + _audit_steps(ctx)
    if not steps:
        return _unavailable("no dependency auditor on PATH (pip-audit / npm audit / cargo-audit / govulncheck)")
    return _steps("cmd", steps, "audit")


def _b_dependency_budget(ctx):
    return _internal(check_dependency_budget)


# --------------------------------------------------------------------------- #
# 结构门（第 1 档核心圈）—— structure_check.py 的确定性指标 + shrink-only 账本
# --------------------------------------------------------------------------- #

def _structure_caps(ctx):
    caps = dict(sc.DEFAULT_CAPS)
    caps.update(ctx["det"]["thresholds"].get("structure") or {})
    return caps


def check_structure(ctx):
    """tests-outside / dup-basename / depth / crowded-dir / cycle / orphan → 账本；镜像率进 details。"""
    repo = ctx["repo"]

    def read(rel):
        return lc.read_text_or_empty(os.path.join(repo, rel))

    violations, details, errors = sc.measure(_files(ctx), read, _layout(ctx).get("tests_dir"), _structure_caps(ctx))
    if errors:
        return _res("fail", "%d unparseable python file(s) — fail closed" % len(errors), {"errors": errors})
    res = _ledger_result(ctx, "structure", violations, "structure clean (tests placed, no dup basenames, "
                         "no import cycles, no orphans, dirs within caps)")
    res["details"].update(details)
    return res


def _b_structure(ctx):
    return _internal(check_structure)


# --------------------------------------------------------------------------- #
# builders —— 扩展圈（菜单可见、默认不勾；探到表面才亮）——大厂 presubmit 硬指标
# --------------------------------------------------------------------------- #

def _mypy_configured(ctx):
    pyproject = lc.read_text_or_empty(os.path.join(ctx["repo"], "pyproject.toml"))
    return "[tool.mypy]" in pyproject or _has(ctx, "mypy.ini") or _has(ctx, ".mypy.ini")


def _b_type_coverage(ctx):
    if _need_python(ctx):
        return _need_python(ctx)
    if not _mypy_configured(ctx):
        return _na("no mypy config ([tool.mypy] / mypy.ini) — type coverage not declared")
    if not _tool(ctx, "mypy"):
        return _unavailable("mypy configured but not on PATH")
    roots = _layout(ctx).get("py_src_roots") or ["."]
    return _cmd(["mypy", "--txt-report", _out(ctx, "mypy-report")] + roots, ctx["repo"], tool="mypy")


def _b_duplication(ctx):
    jscpd = [p for p in _js_pkgs(ctx) if "jscpd" in p.get("bins", [])]
    if jscpd:
        return _steps("cmd", _js_steps(ctx, jscpd, ["npx", "--no-install", "jscpd", "--threshold", "3", "."]), "jscpd")
    if "python" in _stacks(ctx) and _tool(ctx, "pylint"):
        roots = _layout(ctx).get("py_src_roots") or ["."]
        return _cmd(["pylint", "--disable=all", "--enable=duplicate-code"] + roots, ctx["repo"], tool="pylint")
    return _unavailable("no duplication tool (jscpd in node_modules / pylint on PATH) — target ≤ 3% duplicated lines")


def _buf_plan(ctx):
    if not _tool(ctx, "buf"):
        return _unavailable("buf.yaml present but buf not on PATH")
    base = _diff(ctx).get("base") or "main"
    return _cmd(["buf", "breaking", "--against", ".git#branch=%s" % base], ctx["repo"], tool="buf")


def _b_api_breaking(ctx):
    if _has(ctx, "buf.yaml") or _has(ctx, "buf.yml"):
        return _buf_plan(ctx)
    extractor = [p for p in _js_pkgs(ctx) if "api-extractor" in p.get("bins", [])]
    if extractor:
        return _steps("cmd", _js_steps(ctx, extractor, ["npx", "--no-install", "api-extractor", "run"]), "api-extractor")
    return _na("no API contract artifact (buf.yaml / api-extractor.json) — breaking-change check not applicable")


def _b_bundle_size(ctx):
    for pkg in _js_pkgs(ctx):
        cwd = os.path.join(ctx["repo"], pkg["dir"])
        if "size" in pkg.get("scripts", {}):
            return _cmd(["npm", "run", "size"], cwd, tool="npm")
        if "size-limit" in pkg.get("bins", []):
            return _cmd(["npx", "--no-install", "size-limit"], cwd, tool="size-limit")
    return _na("no bundle-size budget declared (npm script `size` / size-limit)")


def _b_license_check(ctx):
    if "python" in _stacks(ctx) and _tool(ctx, "pip-licenses"):
        return _steps("substituted", [_step(["pip-licenses", "--format=markdown"], ctx["repo"])], "pip-licenses",
                      note="license inventory only — no allowlist configured, so nothing is gated")
    checker = [p for p in _js_pkgs(ctx) if "license-checker" in p.get("bins", [])]
    if checker:
        return _steps("substituted", _js_steps(ctx, checker, ["npx", "--no-install", "license-checker", "--summary"]),
                      "license-checker", note="license inventory only — pass --failOn <list> to gate")
    return _unavailable("no license tool (pip-licenses / license-checker)")


def _b_doc_coverage(ctx):
    if _need_python(ctx):
        return _need_python(ctx)
    if not _tool(ctx, "interrogate"):
        return _unavailable("interrogate not on PATH (public-API docstring coverage)")
    roots = _layout(ctx).get("py_src_roots") or ["."]
    return _cmd(["interrogate", "-v", "--fail-under", "80"] + roots, ctx["repo"], tool="interrogate")


def _b_clean_install(ctx):
    if _has(ctx, "scripts/clean-vm-install.sh"):
        return _cmd(["bash", "scripts/clean-vm-install.sh"], ctx["repo"], tool="bash")
    hint = "tart on PATH — " if _tool(ctx, "tart") else ""
    return _unavailable("%sno clean-VM harness (scripts/clean-vm-install.sh): fresh VM → install per README → "
                        "doctor/smoke → upgrade from previous release → uninstall; recipe in references/catalog.md" % hint)


_FEEDBACK_RE = re.compile(r"telemetry|analytics|crash|sentry|feedback|issue_template|bugreport", re.I)


def check_feedback_channel(ctx):
    """信息项，永不判红：有没有用户反馈回路（遥测/崩溃上报/issue 模板）。缺 = 结构性盲区。"""
    found = sorted({f for f in _files(ctx) if _FEEDBACK_RE.search(f)})[:12]
    if found:
        return _res("pass", "feedback channel present (%d file(s))" % len(found), {"found": found})
    return _res("pass", "no feedback channel detected — structural blind spot, not a failure",
                {"found": [], "blind_spots": ["no telemetry / crash reporting / issue templates found — "
                                              "a single-run ladder cannot replace a feedback loop"]})


def _b_feedback_channel(ctx):
    return _internal(check_feedback_channel)


# --------------------------------------------------------------------------- #
# CATALOG（顺序 = 报告顺序；phase 1 可并行、2 串行、3 依赖 coverage.json）
# circle：core = 该档默认必跑（AI 只能多做不能少做）；extended = 菜单可见、默认不勾，
# 触发器点名或探到表面/人工勾选才跑（references/catalog.md）。
# --------------------------------------------------------------------------- #

def _entry(cid, tier, phase, est, label, build, trigger=None, circle="core"):
    return {"id": cid, "tier": tier, "phase": phase, "est": est, "label": label, "build": build,
            "trigger": trigger, "circle": circle}


CATALOG = [
    _entry("py_compile", 1, 1, 10, "Python compileall（语法）", _b_py_compile),
    _entry("py_lint", 1, 1, 20, "Python lint（ruff/flake8）", _b_py_lint),
    _entry("py_format", 1, 1, 15, "Python format check（若配置）", _b_py_format),
    _entry("ts_typecheck", 1, 1, 60, "TypeScript tsc --noEmit", _b_ts_typecheck),
    _entry("js_lint", 1, 1, 60, "JS/TS eslint", _b_js_lint),
    _entry("shellcheck", 1, 1, 10, "shellcheck *.sh", _b_shellcheck),
    _entry("swift_parse", 1, 1, 30, "swiftc -parse", _b_swift_parse),
    _entry("deps_direction", 1, 1, 10, "依赖方向规则", _b_deps_direction),
    _entry("length_caps", 1, 1, 10, "文件/函数行数上限", _b_length_caps),
    _entry("structure", 1, 1, 15, "结构门（测试放置/同名模块/深度/拥挤目录/import 环/孤儿）", _b_structure),
    _entry("secret_scan", 1, 1, 10, "secret 扫描（keys/tokens）", _b_secret_scan),
    _entry("actions_sha_pin", 1, 1, 5, "GitHub Actions SHA-pin", _b_actions_sha_pin),
    _entry("type_coverage", 2, 1, 120, "类型检查 + 类型覆盖（mypy，若配置）", _b_type_coverage, circle="extended"),
    _entry("duplication", 2, 1, 120, "重复率 ≤ 3%（jscpd / pylint duplicate-code）", _b_duplication, circle="extended"),
    _entry("doc_coverage", 2, 1, 30, "公开 API 文档覆盖（interrogate）", _b_doc_coverage, circle="extended"),
    _entry("api_breaking", 3, 1, 60, "API 破坏性变更（buf breaking / api-extractor）", _b_api_breaking, circle="extended"),
    _entry("bundle_size", 3, 2, 120, "bundle/二进制体积预算（size-limit / npm run size）", _b_bundle_size, circle="extended"),
    _entry("py_unit", 2, 2, 120, "Python 单元测试", _b_py_unit),
    _entry("js_unit", 2, 2, 120, "JS/TS 单元测试（vitest/jest）", _b_js_unit),
    _entry("swift_unit", 2, 2, 300, "Swift 测试（swift test / xcodebuild）", _b_swift_unit),
    _entry("py_coverage", 2, 2, 240, "Python 覆盖率 + 地板/no-drop", _b_py_coverage),
    _entry("js_coverage", 2, 2, 180, "JS 覆盖率（vitest --coverage）", _b_js_coverage),
    _entry("diff_coverage", 2, 3, 5, "diff 行覆盖（新增行 100%）", _b_diff_coverage),
    _entry("complexity", 2, 1, 10, "每函数圈复杂度", _b_complexity),
    _entry("crap", 2, 3, 15, "CRAP = CC²×(1−cov)³+CC", _b_crap),
    _entry("py_integration", 3, 2, 600, "Python 集成测试", _b_py_integration),
    _entry("js_e2e", 3, 2, 600, "e2e smoke（npm run test:e2e / playwright）", _b_js_e2e),
    _entry("golden_contract", 3, 2, 120, "golden / contract 判例子集", _b_golden_contract),
    _entry("migration_roundtrip", 3, 2, 120, "migration round-trip 判例子集", _b_migration_roundtrip),
    _entry("field_add_only", 3, 1, 5, "跨组件字段 add-only（diff 删键）", _b_field_add_only),
    _entry("mutation_changed", 4, 2, 1800, "变异测试（改动的宪法模块）", _b_mutation_changed),
    _entry("property_tests", 4, 2, 300, "property-based 测试（hypothesis）", _b_property_tests, circle="extended"),
    _entry("flaky_detect", 4, 2, 600, "flaky 探测（×N 重跑 + 打乱）", _b_flaky_detect),
    _entry("test_smells", 4, 1, 10, "test smells（无断言/sleep/真 IO）", _b_test_smells),
    _entry("mutation_full", 5, 2, None, "全量变异测试（无时限）", _b_mutation_full),
    _entry("fuzz", 5, 2, None, "fuzzing", _b_fuzz, circle="extended"),
    _entry("soak_race", 5, 2, None, "soak / race stress ×N", _b_soak_race, circle="extended"),
    _entry("security_scan", 5, 1, None, "安全 + 供应链扫描", _b_security_scan),
    _entry("arch_audit", 5, 1, None, "架构规则全审", _b_arch_audit),
    _entry("perf_budget", 5, 2, None, "性能预算", _b_perf_budget, circle="extended"),
    _entry("docs_drift", 5, 1, None, "docs-vs-code drift（悬空路径）", _b_docs_drift, circle="extended"),
    _entry("dead_code", 5, 1, None, "dead code（vulture/knip）", _b_dead_code, circle="extended"),
    _entry("license_check", 5, 1, None, "许可证清单/白名单（pip-licenses / license-checker）", _b_license_check,
           circle="extended"),
    _entry("clean_install", 5, 2, None, "干净 VM 从零安装 → 探活 → 升级 → 卸载", _b_clean_install, circle="extended"),
    _entry("feedback_channel", 5, 1, 5, "用户反馈回路存在性（信息项，永不判红）", _b_feedback_channel,
           circle="extended"),
    _entry("crash_recovery", None, 2, 120, "崩溃恢复/截断判例", _b_crash_recovery, "persisted_state"),
    _entry("fault_injection", None, 2, 120, "故障注入判例", _b_fault_injection, "boundary"),
    _entry("race_stress", None, 2, 600, "race stress ×N", _b_race_stress, "concurrency"),
    _entry("resource_leak", None, 2, 120, "资源泄漏基线判例", _b_resource_leak, "spawns_processes"),
    _entry("corpus_regression", None, 2, 120, "corpus 回归判例", _b_corpus_regression, "persisted_format"),
    _entry("contract_drift", None, 2, 120, "契约 drift 判例", _b_contract_drift, "documented_behavior"),
    _entry("diff_minimality", None, 1, 5, "diff 最小化（changed ⊆ declared）", _b_diff_minimality, "always"),
    _entry("dependency_audit", None, 1, 120, "依赖审计（漏洞）", _b_dependency_audit, "deps_changed"),
    _entry("dependency_budget", None, 1, 5, "依赖预算（新增 ⊆ declared_deps）", _b_dependency_budget, "deps_changed"),
]
BY_ID = {entry["id"]: entry for entry in CATALOG}


def default_checks(det, tier):
    """tier 的默认勾选：tier ≤ 选定 tier 的**核心圈**层 + 触发器点名的加挂层（always 恒在）。
    扩展圈只进菜单，不默认勾——AI/人可以多选，不能少选核心圈（run_ladder 记录 core_skipped）。"""
    fired = {t["id"] for t in det["triggers"]} | {"always"}
    wanted = {e["id"] for e in CATALOG
              if e["tier"] is not None and e["tier"] <= tier and e["circle"] == "core"}
    for trigger in fired:
        wanted.update(TRIGGER_CHECKS.get(trigger, []))
    return [e["id"] for e in CATALOG if e["id"] in wanted]


def _core_due(entry, tier):
    return entry["tier"] is not None and entry["tier"] <= tier and entry["circle"] == "core"


def core_skipped(det, tier, selected):
    """核心圈里本该跑（kind 可跑，非 na/unavailable）却没被选的层 → [id]。菜单来自 detect。"""
    kinds = {row["id"]: row["kind"] for row in det.get("menu") or []}
    chosen = set(selected)
    due = [e["id"] for e in CATALOG if _core_due(e, tier) and e["id"] not in chosen]
    return [cid for cid in due if kinds.get(cid) in ("cmd", "internal", "substituted")]


def build_plans(ctx, ids):
    """ids → {id: plan}；未知 id 抛 KeyError（调用方转 usage error）。"""
    return {cid: BY_ID[cid]["build"](ctx) for cid in ids}


def build_menu(ctx):
    """detect 输出里的 menu：每层一行，含可跑性、原因、时间估计、命令预览。"""
    menu = []
    for entry in CATALOG:
        plan = entry["build"](ctx)
        menu.append({"id": entry["id"], "tier": entry["tier"], "trigger": entry["trigger"],
                     "circle": entry["circle"], "label": entry["label"], "est_seconds": entry["est"],
                     "kind": plan["kind"], "reason": plan.get("reason") or plan.get("note"),
                     "command": preview(plan)})
    return menu
