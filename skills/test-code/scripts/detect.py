#!/usr/bin/env python3
"""test-code skill · 探测：技术栈 + 可用工具 + 阈值来源 + diff/触发器 + 菜单 → JSON。

法典指针：docs/CONTRACT.md §58（阈值单源 qa/gates.toml——skill 只读不定义，R2.8.3；
项目门 scripts/qa/*.py 的存在决定用项目尺子还是 fallback）、§57（qa/mutation_targets.toml
= 宪法模块名单，用于 tier 推荐与变异靶区）。设计 = docs/design/vnext2-plan.md R2.8 / D14。

用法：detect.py [--repo PATH] [--base REF] [--out FILE]
退出码：0 成功；2 repo 不可读 / 阈值文件坏（fail closed，不猜）。零网络；唯一子进程 =
git 与 `python -c import` 探针（都经 ladder_common.run_command 注入缝）。
判例：tests/test_skill_test_code_detect.py。
"""

import argparse
import json
import os
import re
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import checks  # noqa: E402
import ladder_common as lc  # noqa: E402

TOOLS = ("git", "ruff", "flake8", "black", "mypy", "shellcheck", "node", "npx", "npm", "swift", "swiftc",
         "xcodebuild", "gitleaks", "bandit", "pip-audit", "mutmut", "vulture", "qlty", "lint-imports",
         "cargo", "cargo-audit", "go", "govulncheck", "pylint", "interrogate", "pip-licenses", "buf", "tart")
PYMODS = ("coverage", "hypothesis", "pytest", "pytest_randomly", "pytest_benchmark")
SKILL_DEFAULTS = {
    "complexity_max": 10, "crap_max": 30.0, "crap_tolerance": 0.5,
    "max_function_lines": 100, "max_file_lines": 1000, "coverage": "no-drop",
    "structure": {"max_dir_depth": 6, "max_files_per_dir": 40},
    "source": "skill-defaults",
    "note": "skill defaults — Bob-strict = 6 for complexity/CRAP (a project pins its own in qa/gates.toml)",
}
_STACK_RULES = (
    ("python", {".py"}, {"pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "Pipfile"}),
    ("js", {".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx"}, {"package.json"}),
    ("swift", {".swift"}, {"Package.swift"}),
    ("shell", {".sh"}, set()),
    ("go", {".go"}, {"go.mod"}),
    ("rust", {".rs"}, {"Cargo.toml"}),
    ("java", {".java"}, {"pom.xml", "build.gradle", "build.gradle.kts"}),
    ("scala", {".scala"}, {"build.sbt"}),
    ("sql", {".sql"}, set()),
)
_DOC_EXT = (".md", ".rst", ".txt")
_DOC_NAME_RE = re.compile(r"(^|/)(README|CONTRACT|CHANGELOG|prompts?)([./]|$)", re.I)
TRIGGER_RULES = (
    ("persisted_state", re.compile(
        r"sqlite3|\.commit\(|json\.dump|yaml\.(?:safe_)?dump|open\([^)]*[\"'][wa]|writeFile|fs\.write|"
        r"write_text\(|os\.replace\(|os\.rename\(|shutil\.move|localStorage|UserDefaults|FileManager\.default")),
    ("boundary", re.compile(
        r"subprocess|urllib|requests\.|httpx|http\.client|socket\.|\bfetch\(|child_process|URLSession|Process\(\)")),
    ("concurrency", re.compile(
        r"threading|asyncio|multiprocessing|concurrent\.futures|Lock\(|Semaphore|Promise\.all|Promise\.race|"
        r"DispatchQueue|\bactor\b|async let")),
    ("spawns_processes", re.compile(
        r"Popen|subprocess\.run|os\.fork|os\.spawn|socket\.socket|child_process|spawn\(|Process\(\)|"
        r"mkstemp|NamedTemporaryFile|os\.pipe")),
    ("persisted_format", re.compile(r"schema|migration|user_version|schemaVersion|SCHEMA|_UPGRADES|\bcompat")),
    ("documented_behavior", re.compile(r"help=|add_argument|--help|usage:|PROMPT|prompt")),
)
_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
_TARGET_KEY_RE = re.compile(r'^\s*"([^"]+)"\s*=')


# --------------------------------------------------------------------------- #
# 工具 / 文件 / 技术栈
# --------------------------------------------------------------------------- #

def probe_tools(which=shutil.which):
    return {name: which(name) for name in TOOLS}


def probe_pymods(runner, py):
    """一次子进程问清 dev 侧 python 模块是否可 import（coverage/hypothesis/pytest…）。"""
    code = ("import importlib.util, json, sys; "
            "print(json.dumps({m: importlib.util.find_spec(m) is not None for m in sys.argv[1:]}))")
    res = runner([py, "-c", code] + list(PYMODS), timeout=60)
    if not res.ok:
        return {m: False for m in PYMODS}
    try:
        return json.loads(res.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {m: False for m in PYMODS}


def _in_skip_dir(rel):
    return any(part in lc.SKIP_DIRS for part in rel.split("/")[:-1])


def _keep_file(repo, rel):
    return not _in_skip_dir(rel) and os.path.exists(os.path.join(repo, rel))


def _kept(repo, names):
    return [f for f in names if _keep_file(repo, f)]


def list_files(runner, repo):
    """→ (files, untracked, is_git)。git 不可用时 os.walk 兜底（untracked 无从谈起）。"""
    tracked = lc.git_lines(runner, repo, ["ls-files"])
    if tracked is None:
        return _kept(repo, list(lc.walk_files(repo))), [], False
    untracked = lc.git_lines(runner, repo, ["ls-files", "--others", "--exclude-standard"]) or []
    return _kept(repo, sorted(set(tracked) | set(untracked))), _kept(repo, untracked), True


def detect_stacks(files):
    exts = {os.path.splitext(f)[1] for f in files}
    names = {os.path.basename(f) for f in files}
    stacks = [name for name, ext, markers in _STACK_RULES if exts & ext or names & markers]
    if any(f.startswith(".github/workflows/") for f in files):
        stacks.append("actions")
    return stacks


# --------------------------------------------------------------------------- #
# layout（python / js / swift）
# --------------------------------------------------------------------------- #

_read_or_empty = lc.read_text_or_empty


def _mentions(repo, rel, needle):
    return needle in _read_or_empty(os.path.join(repo, rel))


def _py_runner(repo, names, pyproject):
    markers = {"pytest.ini", "conftest.py"} & names
    tox = _read_or_empty(os.path.join(repo, "tox.ini"))
    if markers or "[tool.pytest" in pyproject or "[pytest]" in tox:
        return "pytest"
    return "unittest"


def _py_format(pyproject):
    if "[tool.ruff.format]" in pyproject:
        return "ruff"
    if "[tool.black]" in pyproject:
        return "black"
    return None


def _integration_dir(repo, tests_dir):
    if tests_dir and os.path.isdir(os.path.join(repo, tests_dir, "integration")):
        return tests_dir + "/integration"
    return None


def _py_dirs(repo, py_files):
    tops = sorted({f.split("/")[0] for f in py_files if "/" in f})
    tests_dir = next((d for d in ("tests", "test", "spec") if d in tops), None)
    roots = tops + [f for f in py_files if "/" not in f]
    return {"py_roots": roots, "py_src_roots": [d for d in roots if d != tests_dir],
            "tests_dir": tests_dir, "integration_dir": _integration_dir(repo, tests_dir)}


def _property_tests(repo, py_files, tests_dir):
    prefix = (tests_dir or "\0") + "/"
    return [f for f in py_files if f.startswith(prefix) and _mentions(repo, f, "hypothesis")]


def _py_layout(repo, files, names, pyproject):
    py_files = [f for f in files if f.endswith(".py")]
    layout = _py_dirs(repo, py_files)
    layout.update({
        "py_runner": _py_runner(repo, names, pyproject),
        "py_format": _py_format(pyproject),
        "property_tests": _property_tests(repo, py_files, layout["tests_dir"]),
        "requirements": sorted(f for f in files if re.match(r"requirements[^/]*\.txt$", f)),
    })
    return layout


def _js_runner(deps):
    if "vitest" in deps:
        return "vitest"
    if "jest" in deps:
        return "jest"
    return None


def _js_bins(repo, pkg_dir):
    bin_dir = os.path.join(repo, pkg_dir, "node_modules", ".bin")
    if not os.path.isdir(bin_dir):
        return []
    return sorted(os.listdir(bin_dir))


def _js_config_text(repo, pkg_dir, names):
    configs = [n for n in names if n.startswith(("vite.config", "vitest.config"))]
    return "".join(_read_or_empty(os.path.join(repo, pkg_dir, n)) for n in configs)


def _js_deps(data):
    return set(data.get("dependencies") or {}) | set(data.get("devDependencies") or {})


def _js_lock(names):
    return next((n for n in ("package-lock.json", "yarn.lock", "pnpm-lock.yaml") if n in names), None)


def _js_package(repo, pkg_dir):
    data = _load_json(os.path.join(repo, pkg_dir, "package.json")) or {}
    deps = _js_deps(data)
    names = set(os.listdir(os.path.join(repo, pkg_dir)))
    cfg = _js_config_text(repo, pkg_dir, names)
    return {
        "dir": pkg_dir or ".", "tsconfig": "tsconfig.json" in names,
        "eslint": any(n.startswith((".eslintrc", "eslint.config")) for n in names),
        "test_runner": _js_runner(deps), "bins": _js_bins(repo, pkg_dir), "lock": _js_lock(names),
        "scripts": data.get("scripts") or {},
        "coverage_provider": any(d.startswith("@vitest/coverage-") for d in deps),
        "coverage_thresholds": "thresholds" in cfg,
        "playwright": bool({"@playwright/test", "playwright"} & deps),
        "stryker": "@stryker-mutator/core" in deps,
    }


def _load_json(path):
    try:
        return lc.read_json(path)
    except (OSError, ValueError):
        return None


def _js_packages(repo, files):
    return [_js_package(repo, os.path.dirname(f)) for f in files if os.path.basename(f) == "package.json"]


def _scheme(rel):
    container = rel[:rel.find("/xcshareddata/")]
    return {"scheme": os.path.basename(rel)[:-len(".xcscheme")], "dir": os.path.dirname(container) or ".",
            "container": os.path.basename(container)}


def _swift_layout(files):
    package = next((f for f in files if os.path.basename(f) == "Package.swift"), None)
    schemes = [_scheme(f) for f in files if f.endswith(".xcscheme") and "/xcshareddata/xcschemes/" in f]
    return {"package_dir": os.path.dirname(package) if package else None, "schemes": schemes}


def _flags(repo, names, pyproject):
    return {
        "importlinter": ".importlinter" in names or "[tool.importlinter]" in pyproject,
        "mutmut": "[tool.mutmut]" in pyproject,
        "benchmarks": os.path.isdir(os.path.join(repo, "benchmarks")),
        "fuzz_dir": os.path.isdir(os.path.join(repo, "fuzz")),
        "qlty": os.path.exists(os.path.join(repo, ".qlty", "qlty.toml")),
    }


def detect_layout(repo, files):
    names = {os.path.basename(f) for f in files}
    pyproject = _read_or_empty(os.path.join(repo, "pyproject.toml"))
    layout = _py_layout(repo, files, names, pyproject)
    layout["js_packages"] = _js_packages(repo, files)
    layout["swift"] = _swift_layout(files)
    layout.update(_flags(repo, names, pyproject))
    return layout


# --------------------------------------------------------------------------- #
# 阈值（项目的优先；skill 默认值只在零配置时生效）+ 变异靶区
# --------------------------------------------------------------------------- #

def _from_gates(repo, path):
    gates = lc.parse_toml_subset(lc.read_text(path) or "")
    cx, crap, hyg = gates.get("complexity", {}), gates.get("crap", {}), gates.get("hygiene", {})
    floor = os.path.exists(os.path.join(repo, "qa", "coverage_floor.txt"))
    structure = dict(SKILL_DEFAULTS["structure"])
    structure.update(gates.get("structure", {}))
    return {
        "complexity_max": cx.get("max", SKILL_DEFAULTS["complexity_max"]),
        "crap_max": crap.get("max", SKILL_DEFAULTS["crap_max"]),
        "crap_tolerance": crap.get("tolerance", SKILL_DEFAULTS["crap_tolerance"]),
        "max_function_lines": hyg.get("max_function_lines", SKILL_DEFAULTS["max_function_lines"]),
        "max_file_lines": hyg.get("max_file_lines", SKILL_DEFAULTS["max_file_lines"]),
        "coverage": "floor:qa/coverage_floor.txt" if floor else "no-drop",
        "structure": structure,
        "source": "qa/gates.toml", "note": "project thresholds — single source of truth, skill reads only",
    }


def detect_thresholds(repo):
    gates = os.path.join(repo, "qa", "gates.toml")
    if os.path.exists(gates):
        return _from_gates(repo, gates)
    pyproject = _read_or_empty(os.path.join(repo, "pyproject.toml"))
    thr = dict(SKILL_DEFAULTS)
    cx = re.search(r"max-complexity\s*=\s*(\d+)", pyproject)
    if cx:
        thr.update(complexity_max=int(cx.group(1)), source="pyproject.toml")
    cov = re.search(r"fail_under\s*=\s*([\d.]+)", pyproject)
    if cov:
        thr.update(coverage="floor:pyproject.toml fail_under=%s" % cov.group(1), source="pyproject.toml")
    return thr


def mutation_targets(repo):
    """qa/mutation_targets.toml [targets] 的模块名（宪法模块名单）；缺席 = []。"""
    text = _read_or_empty(os.path.join(repo, "qa", "mutation_targets.toml"))
    modules, in_targets = [], False
    for line in text.splitlines():
        if line.strip().startswith("["):
            in_targets = line.strip() == "[targets]"
            continue
        match = _TARGET_KEY_RE.match(line)
        if in_targets and match:
            modules.append(match.group(1))
    return modules


# --------------------------------------------------------------------------- #
# diff vs base → 新增行（号 + 文本）、删除行文本、changed_files
# --------------------------------------------------------------------------- #

def _new_file_name(raw):
    name = raw[4:].split("\t")[0]
    if name == "/dev/null":
        return None
    return name[2:] if name.startswith("b/") else name


class DiffParser(object):
    """`git diff -U0` 的最小解析：只要新增行号/文本与删除行文本。"""

    def __init__(self):
        self.file, self.line, self.in_header = None, 0, False
        self.added, self.added_text, self.removed = {}, {}, {}

    def _start_file(self, raw):
        self.file = _new_file_name(raw)
        if self.file is not None:
            self.added.setdefault(self.file, [])
            self.added_text.setdefault(self.file, [])
            self.removed.setdefault(self.file, [])

    def _header(self, raw):
        """头部行 → True（已消费）。`diff` 开头进入头部态，直到 `+++ ` 为止。"""
        if raw.startswith("diff "):
            self.in_header, self.file = True, None
            return True
        if self.in_header:
            if raw.startswith("+++ "):
                self._start_file(raw)
                self.in_header = False
            return True
        match = _HUNK_RE.match(raw)
        if match:
            self.line = int(match.group(1))
            return True
        return raw.startswith("\\ ")

    def _body(self, raw):
        if raw.startswith("+"):
            self.added[self.file].append(self.line)
            self.added_text[self.file].append(raw[1:])
            self.line += 1
        elif raw.startswith("-"):
            self.removed[self.file].append(raw[1:])
        else:
            self.line += 1

    def feed(self, raw):
        if self._header(raw) or self.file is None:
            return
        self._body(raw)


def _untracked_as_added(repo, untracked, parser):
    for rel in untracked:
        try:
            text = lc.read_text(os.path.join(repo, rel))
        except OSError:
            text = None
        if text is None:
            continue
        lines = text.splitlines()
        parser.added[rel] = list(range(1, len(lines) + 1))
        parser.added_text[rel] = lines
        parser.removed.setdefault(rel, [])


def _merge_base(runner, repo, base):
    """diff 对着 merge-base，不对着 base ref 本身——base 分支先跑了别人的 PR 时，
    直接 diff base 会把别人的改动反向算进「本次 diff」（首次实跑抓到的坑）。"""
    if not base:
        return None
    lines = lc.git_lines(runner, repo, ["merge-base", "HEAD", base])
    return lines[0] if lines else base


def detect_diff(runner, repo, requested_base, untracked):
    """→ {base, base_commit, changed_files, added, added_text, removed, untracked}；无 base = 空 diff。"""
    base = lc.resolve_base(runner, repo, requested_base)
    commit = _merge_base(runner, repo, base)
    parser = DiffParser()
    names = []
    if commit:
        res = runner(["git", "diff", "-U0", "--no-color", "--no-ext-diff", commit], cwd=repo, timeout=120)
        for raw in res.stdout.splitlines():
            parser.feed(raw)
        names = lc.git_lines(runner, repo, ["diff", "--name-only", commit]) or []
    _untracked_as_added(repo, untracked, parser)
    changed = sorted(set(names) | set(parser.added) | set(parser.removed) | set(untracked))
    return {"base": base, "base_commit": commit, "changed_files": changed, "added": parser.added,
            "added_text": parser.added_text, "removed": parser.removed, "untracked": list(untracked)}


# --------------------------------------------------------------------------- #
# 触发器 + tier 推荐
# --------------------------------------------------------------------------- #

def _is_doc(path):
    return path.endswith(_DOC_EXT) or bool(_DOC_NAME_RE.search(path))


def _name_triggers(diff, hits):
    docs = [p for p in diff["changed_files"] if _is_doc(p)]
    manifests = [p for p in diff["changed_files"] if checks.MANIFEST_RE.search(p)]
    if docs:
        hits.setdefault("documented_behavior", []).extend("%s: (doc file changed)" % p for p in docs)
    if manifests:
        hits.setdefault("deps_changed", []).extend("%s: (manifest changed)" % p for p in manifests)


def detect_triggers(diff):
    """新增行文本上的正则 + 文件名规则 → [{id, evidence[:8], hits}]（id 排序）。
    文档文件（.md/.rst/.txt、README/CONTRACT/prompts）不参与代码正则——一段描述 sqlite 的
    文字不该触发崩溃恢复层（首次实跑：skill 自己的文档把六个触发器全点亮了）；文档只喂
    documented_behavior。"""
    hits = {}
    for path, lines in diff["added_text"].items():
        if _is_doc(path):
            continue
        for lineno, text in zip(diff["added"].get(path, []), lines):
            for tid, rx in TRIGGER_RULES:
                if rx.search(text):
                    hits.setdefault(tid, []).append("%s:%d: %s" % (path, lineno, text.strip()[:80]))
    _name_triggers(diff, hits)
    return [{"id": tid, "evidence": ev[:8], "hits": len(ev)} for tid, ev in sorted(hits.items())]


def _rec_no_diff(det, changed, fired):
    if not changed:
        return 2, "no diff vs base — measure the whole tree at tier 2 (unit + rulers)"
    return None


def _rec_docs(det, changed, fired):
    if all(_is_doc(p) for p in changed):
        return 1, "docs-only diff (%d file(s)) — static gates (tier 1) suffice" % len(changed)
    return None


def _rec_high(det, changed, fired):
    hot = sorted(fired & {"persisted_state", "concurrency", "persisted_format"})
    constitution = sorted(set(changed) & set(det.get("mutation_targets", [])))
    if hot or constitution:
        return 4, "high blast radius — triggers %s; constitution modules touched %s — tier 4" % (hot, constitution)
    return None


def _rec_medium(det, changed, fired):
    if len(changed) > 15 or fired & {"boundary", "deps_changed"}:
        return 3, ("%d files changed / boundary or dependency trigger fired — integration + contracts (tier 3)"
                   % len(changed))
    return None


def recommend(det):
    changed = det["diff"]["changed_files"]
    fired = {t["id"] for t in det["triggers"]}
    for rule in (_rec_no_diff, _rec_docs, _rec_high, _rec_medium):
        rec = rule(det, changed, fired)
        if rec:
            return {"tier": rec[0], "reason": rec[1]}
    return {"tier": 2, "reason": "%d source file(s) changed, no high-stakes trigger fired — tier 2" % len(changed)}


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #

def _empty_diff(untracked):
    return {"base": None, "base_commit": None, "changed_files": list(untracked), "added": {}, "added_text": {},
            "removed": {}, "untracked": list(untracked)}


def detect(repo, base=None, runner=lc.run_command, which=shutil.which, py=None):
    """全量探测 → JSON-able dict（schemaVersion 1，字段 add-only）。"""
    py = py or sys.executable
    files, untracked, is_git = list_files(runner, repo)
    det = {
        "schemaVersion": 1, "skill": {"name": lc.SKILL_NAME, "version": lc.SKILL_VERSION},
        "generated_at": lc.utc_iso(), "repo": repo, "is_git": is_git, "python": py,
        "files": files, "stacks": detect_stacks(files), "tools": probe_tools(which),
        "pymods": probe_pymods(runner, py), "thresholds": detect_thresholds(repo),
        "layout": detect_layout(repo, files), "mutation_targets": mutation_targets(repo),
        "diff": detect_diff(runner, repo, base, untracked) if is_git else _empty_diff(untracked),
    }
    det["triggers"] = detect_triggers(det["diff"])
    det["recommendation"] = recommend(det)
    det["menu"] = checks.build_menu(checks.make_ctx(repo, det, py=py))
    return det


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", default=os.getcwd())
    parser.add_argument("--base", help="diff base ref (default: origin/main, main, master)")
    parser.add_argument("--out", help="write JSON here instead of stdout")
    args = parser.parse_args(argv)
    repo = os.path.abspath(args.repo)
    if not os.path.isdir(repo):
        print("detect: not a directory: %s" % repo, file=sys.stderr)
        return 2
    try:
        det = detect(repo, args.base)
    except ValueError as exc:
        print("detect: unreadable project config — fail closed: %s" % exc, file=sys.stderr)
        return 2
    if args.out:
        lc.write_json(args.out, det)
        print("detect: wrote %s" % args.out)
    else:
        print(json.dumps(det, indent=1, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
