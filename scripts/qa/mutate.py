#!/usr/bin/env python3
"""夜间变异测试 runner —— stdlib-only，自制（CONTRACT §57 管辖）。

干什么：对靶区模块（truth = qa/mutation_targets.toml）做确定性 operator flip
（== ↔ !=、< ↔ <=、> ↔ >=、+ ↔ -、and ↔ or、True ↔ False、continue ↔ break、
return X → return None、is ↔ is not、in ↔ not in、整数常量 ±1），把每个变异体
写进**临时工作区副本**（绝不改动真源树），跑该模块映射的定向测试子集——
测试杀不死的变异体 = 测试网的洞。

为什么自建不装现成工具（mutmut/cosmic-ray）：宪法第 7 条（运行时依赖 =
stdlib + PyYAML）+ Uncle Bob 采纳清单的判决——价值靠确定性工具执行，
工具本身要小到能被本仓库的测试钉死（判例见文件尾注）。

怎么跑：
  * 夜间 CI：.github/workflows/mutation-nightly.yml（ubuntu，预算封顶，
    报告落 pinned issue「Nightly mutation report」+ Actions artifact）。
  * 本地（无 launchd agent——owner 机器保持精简，D3/D5）：
        python3 scripts/qa/mutate.py --all
  * 永不作为 PR 门（owner 决策 D5 / R2.3.4）。

机器可读输出（P5 每日自我改进循环的输入——survivors 列表带 file:line +
operator，循环据此自动提出补测试提案，R2.3.4/R2.4.2；JSON 字段 add-only）：
  .qa/mutation/report.json    本轮聚合报告（schema 见 build_report()）
  .qa/mutation/state.json     断点续跑台账（跨夜 resume；模块内容 hash 变
                              = 该模块结果作废重跑）

确定性与预算：site 顺序 = AST 深度优先遍历序（跑两遍同一棵树得同一列表）；
round-robin 跨模块交错执行，预算（--time-budget）到点即停——每个模块每晚都
被访问到，长模块跨几夜跑完。

判例：tests/test_mutate_sites.py（site 生成/跳过规则/预算与续跑调度）、
tests/integration/test_mutation_runner.py（真子进程杀伤判定 + 弱测试存活）。
"""
import argparse
import ast
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = 1
# 变异算子集合的版本号：算子增删改 → bump → 旧 state 全部作废（结果不可比）。
RUNNER_VERSION = 1

DEFAULT_TARGETS = "qa/mutation_targets.toml"
DEFAULT_STATE = ".qa/mutation/state.json"
DEFAULT_JSON = ".qa/mutation/report.json"
DEFAULT_MD = ".qa/mutation/report.md"
DEFAULT_BUDGET_SECONDS = 2700  # 45 min——mutation-nightly.yml 的 60 min 顶之内
DEFAULT_MUTANT_TIMEOUT = 60
# 每 N 个变异体落一次 state（正常结束也会落）：预算内被硬杀（workflow 超时、
# 断电）最多丢 N 个变异体的账，绝不丢整夜。
CHECKPOINT_EVERY = 20
# markdown 报告里存活体列表的行数帽（GitHub issue body 上限 65,536 字符；
# 全量清单永远在 JSON 工件里，md 只是人看的摘要）。
MD_SURVIVOR_CAP = 200

# 等价变异体高发区的跳过启发（§57）：logging 调用里的改动（级别名、拼串）
# 几乎不可能被行为测试杀死，全部不铸 site。
_LOGGING_ATTRS = frozenset(
    {"debug", "info", "warning", "error", "exception", "critical", "log"})

# 临时工作区的 fallback 复制（非 git 树）要跳过的目录名。
_IGNORE_NAMES = frozenset(
    {".git", "node_modules", "__pycache__", ".qa", "state", ".build", "build"})


# --------------------------------------------------------------------------- #
# qa/mutation_targets.toml 的受限 TOML 子集解析
# --------------------------------------------------------------------------- #
# 为什么不用 tomllib：Python floor 是 3.9（tomllib 3.11+），宪法第 7 条又禁
# 新运行时依赖。子集 =［table 头、bare/带引号 key、值 = 双引号字符串（无转义）
# / 整数 / true / false / 单行字符串数组、# 注释］——超出子集直接报错，
# 语法被 tests/test_mutate_sites.py 钉死。

def _strip_comment(line):
    out = []
    in_str = False
    for ch in line:
        if ch == '"':
            in_str = not in_str
        elif ch == "#" and not in_str:
            break
        out.append(ch)
    return "".join(out).strip()


def _parse_value(raw, where):
    raw = raw.strip()
    if raw.startswith("[") and raw.endswith("]"):
        inner = raw[1:-1].strip()
        if not inner:
            return []
        items = []
        for piece in inner.split(","):
            piece = piece.strip()
            if not piece:
                continue
            if not (piece.startswith('"') and piece.endswith('"') and len(piece) >= 2):
                raise ValueError(f"{where}: 数组元素必须是双引号字符串: {piece!r}")
            items.append(piece[1:-1])
        return items
    if raw.startswith('"') and raw.endswith('"') and len(raw) >= 2:
        return raw[1:-1]
    if raw in ("true", "false"):
        return raw == "true"
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"{where}: 不在支持的 TOML 子集内: {raw!r}") from None


def parse_targets_toml(text):
    """→ {table 名: {key: value}}；只支持本文件头注释声明的子集。"""
    tables = {}
    current = None
    for n, rawline in enumerate(text.splitlines(), 1):
        line = _strip_comment(rawline)
        if not line:
            continue
        where = f"line {n}"
        if line.startswith("[") and line.endswith("]"):
            name = line[1:-1].strip()
            if not name or name.startswith("["):
                raise ValueError(f"{where}: 非法 table 头: {rawline!r}")
            current = tables.setdefault(name, {})
            continue
        if "=" not in line:
            raise ValueError(f"{where}: 期望 key = value: {rawline!r}")
        if current is None:
            raise ValueError(f"{where}: key 出现在任何 [table] 之前")
        key, _, raw = line.partition("=")
        key = key.strip()
        if key.startswith('"') and key.endswith('"') and len(key) >= 2:
            key = key[1:-1]
        current[key] = _parse_value(raw, where)
    return tables


# --------------------------------------------------------------------------- #
# site 收集（确定性 AST 深度优先）
# --------------------------------------------------------------------------- #
_CMP_FLIPS = {
    ast.Eq: (ast.NotEq, "== -> !="), ast.NotEq: (ast.Eq, "!= -> =="),
    ast.Lt: (ast.LtE, "< -> <="), ast.LtE: (ast.Lt, "<= -> <"),
    ast.Gt: (ast.GtE, "> -> >="), ast.GtE: (ast.Gt, ">= -> >"),
    ast.Is: (ast.IsNot, "is -> is not"), ast.IsNot: (ast.Is, "is not -> is"),
    ast.In: (ast.NotIn, "in -> not in"), ast.NotIn: (ast.In, "not in -> in"),
}


class Site:
    """一个可变异位点：op 分类 + 位置 + 原地施变闭包（apply 只对收集时那棵树有效）。"""

    __slots__ = ("op", "lineno", "col", "ordinal", "detail", "apply")

    def __init__(self, op, lineno, col, detail, apply):
        self.op = op
        self.lineno = lineno
        self.col = col
        self.ordinal = 0  # 同 (op,lineno,col) 的去重序号，collect_sites 收尾统一编
        self.detail = detail
        self.apply = apply

    @property
    def site_id(self):
        return f"{self.op}@{self.lineno}:{self.col}#{self.ordinal}"


def _call_simple_name(node):
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _skip_call(node):
    name = _call_simple_name(node)
    return name in _LOGGING_ATTRS or "log" in name.lower()


def _is_main_guard(node):
    return (isinstance(node, ast.If) and isinstance(node.test, ast.Compare)
            and isinstance(node.test.left, ast.Name)
            and node.test.left.id == "__name__")


def _const_sites(node, sites):
    value = node.value
    if isinstance(value, bool):
        flipped = not value
        sites.append(Site("const_bool", node.lineno, node.col_offset,
                          f"{value} -> {flipped}",
                          lambda n=node, v=flipped: setattr(n, "value", v)))
    elif isinstance(value, int):
        for delta, op in ((1, "int_plus1"), (-1, "int_minus1")):
            sites.append(Site(op, node.lineno, node.col_offset,
                              f"{value} -> {value + delta}",
                              lambda n=node, v=value + delta: setattr(n, "value", v)))


def _node_sites(node, sites):
    """当前 node 自身能铸出的 sites（不含 continue/break——那要父语句表）。"""
    if isinstance(node, ast.Compare):
        for j, op in enumerate(node.ops):
            flip = _CMP_FLIPS.get(type(op))
            if flip:
                repl, detail = flip
                sites.append(Site(f"cmp_{type(op).__name__.lower()}",
                                  node.lineno, node.col_offset, detail,
                                  lambda n=node, j=j, r=repl: n.ops.__setitem__(j, r())))
    elif isinstance(node, ast.BinOp):
        if isinstance(node.op, ast.Add):
            sites.append(Site("arith_add", node.lineno, node.col_offset, "+ -> -",
                              lambda n=node: setattr(n, "op", ast.Sub())))
        elif isinstance(node.op, ast.Sub):
            sites.append(Site("arith_sub", node.lineno, node.col_offset, "- -> +",
                              lambda n=node: setattr(n, "op", ast.Add())))
    elif isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            sites.append(Site("bool_and", node.lineno, node.col_offset, "and -> or",
                              lambda n=node: setattr(n, "op", ast.Or())))
        else:
            sites.append(Site("bool_or", node.lineno, node.col_offset, "or -> and",
                              lambda n=node: setattr(n, "op", ast.And())))
    elif isinstance(node, ast.Constant):
        _const_sites(node, sites)
    elif isinstance(node, ast.Return):
        value = node.value
        if value is not None and not (isinstance(value, ast.Constant)
                                      and value.value is None):
            sites.append(Site("return_none", node.lineno, node.col_offset,
                              "return X -> return None",
                              lambda n=node: setattr(
                                  n, "value", ast.Constant(value=None))))


def _walk(node, sites):
    # 跳过整棵子树的规则（等价变异体高发区，§57 明文）：
    #   __repr__ 函数体、`if __name__ == "__main__"` 守卫、logging 类调用。
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and \
            node.name == "__repr__":
        return
    if _is_main_guard(node):
        return
    if isinstance(node, ast.Call) and _skip_call(node):
        return
    _node_sites(node, sites)
    for _field, value in ast.iter_fields(node):
        if isinstance(value, list):
            for idx, child in enumerate(value):
                if isinstance(child, (ast.Continue, ast.Break)):
                    into = ast.Break if isinstance(child, ast.Continue) else ast.Continue
                    detail = ("continue -> break"
                              if isinstance(child, ast.Continue) else "break -> continue")
                    sites.append(Site("loop_flow", child.lineno, child.col_offset,
                                      detail,
                                      lambda lst=value, i=idx, cls=into, c=child:
                                      lst.__setitem__(i, ast.copy_location(cls(), c))))
                if isinstance(child, ast.AST):
                    _walk(child, sites)
        elif isinstance(value, ast.AST):
            _walk(value, sites)


def collect_sites(tree):
    """确定性 site 列表：同一棵树跑两遍 = 同一列表（site_id 稳定，state 靠它续跑）。"""
    sites = []
    _walk(tree, sites)
    seen = {}
    for site in sites:
        key = (site.op, site.lineno, site.col)
        site.ordinal = seen.get(key, 0)
        seen[key] = site.ordinal + 1
    return sites


def collect_sites_from_source(source):
    return collect_sites(ast.parse(source))


def render_mutant(source, index):
    """第 index 个 site 的变异体源码（重新 parse + 收集，顺序确定所以索引稳定）。"""
    tree = ast.parse(source)
    sites = collect_sites(tree)
    sites[index].apply()
    return ast.unparse(tree)


# --------------------------------------------------------------------------- #
# 临时工作区 + 定向子集执行
# --------------------------------------------------------------------------- #
def build_workspace(repo_root, dest):
    """把待测树复制到 dest：git 树走 `git ls-files`（精确 tracked 集合，天然排除
    state//缓存），非 git 树（测试 fixture）fallback 全量复制减 _IGNORE_NAMES。"""
    repo_root = Path(repo_root)
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    try:
        listing = subprocess.run(
            ["git", "-C", str(repo_root), "ls-files", "-z"],
            capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        listing = None
    if listing is not None and listing.returncode == 0 and listing.stdout:
        for rel in listing.stdout.decode("utf-8", "replace").split("\0"):
            if not rel:
                continue
            src = repo_root / rel
            if not src.is_file():
                continue  # tracked 但被删的文件
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target)
        return dest

    def _ignore(_dirpath, names):
        return [n for n in names if n in _IGNORE_NAMES]

    for entry in sorted(repo_root.iterdir()):
        if entry.name in _IGNORE_NAMES:
            continue
        target = dest / entry.name
        if entry.is_dir():
            shutil.copytree(entry, target, ignore=_ignore)
        else:
            shutil.copy2(entry, target)
    return dest


def _test_argv(test_files):
    """测试文件路径 → unittest argv；空映射 fallback 全套件（报告标 slow）。"""
    if not test_files:
        return ["-m", "unittest", "discover", "-s", "tests"]
    modules = [str(t)[:-3].replace("/", ".").replace(os.sep, ".")
               for t in test_files]
    return ["-m", "unittest"] + modules


def run_subset(workspace, test_files, timeout, python_exe=None):
    """在 workspace 里跑定向子集 → ("pass"|"fail"|"timeout", seconds)。

    POSIX 下起新进程组，超时连孙进程一起 killpg（测试可能 spawn 子进程）。
    """
    cmd = [python_exe or sys.executable] + _test_argv(test_files)
    env = os.environ.copy()
    home = tempfile.mkdtemp(prefix="mutate-home-")
    env["AIASSISTANT_HOME"] = home
    env["PYTHONDONTWRITEBYTECODE"] = "1"  # 变异体覆写同名文件，绝不许吃到旧 pyc
    popen_kwargs = {"cwd": str(workspace), "stdout": subprocess.DEVNULL,
                    "stderr": subprocess.DEVNULL, "env": env}
    if os.name == "posix":
        popen_kwargs["start_new_session"] = True
    start = time.monotonic()
    proc = subprocess.Popen(cmd, **popen_kwargs)
    try:
        rc = proc.wait(timeout=timeout)
        status = "pass" if rc == 0 else "fail"
    except subprocess.TimeoutExpired:
        if os.name == "posix":
            try:
                os.killpg(proc.pid, 9)
            except OSError:
                pass
        proc.kill()
        proc.wait()
        status = "timeout"
    finally:
        shutil.rmtree(home, ignore_errors=True)
    return status, time.monotonic() - start


# --------------------------------------------------------------------------- #
# state（断点续跑台账）
# --------------------------------------------------------------------------- #
def load_state(path):
    try:
        with open(path, encoding="utf-8") as fh:
            state = json.load(fh)
        if not isinstance(state, dict) or \
                state.get("runner_version") != RUNNER_VERSION:
            raise ValueError("stale runner_version")
        state.setdefault("modules", {})
        return state
    except (OSError, ValueError):
        return {"schema": SCHEMA, "runner_version": RUNNER_VERSION, "modules": {}}


def save_state(path, state):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def _content_hash(data):
    return hashlib.sha256(data).hexdigest()


# --------------------------------------------------------------------------- #
# 调度（round-robin + 预算）
# --------------------------------------------------------------------------- #
class ModulePlan:
    __slots__ = ("rel", "tests", "source", "sites", "results", "status", "slow")

    def __init__(self, rel, tests, source, sites, results, status, slow):
        self.rel = rel
        self.tests = tests
        self.source = source
        self.sites = sites
        self.results = results  # state 里该模块的 site_id → 结果 dict（可变引用）
        self.status = status
        self.slow = slow


def _prepare_module(repo_root, rel, tests, state_modules):
    path = Path(repo_root) / rel
    try:
        data = path.read_bytes()
    except OSError:
        return ModulePlan(rel, tests, None, [], {}, "missing", not tests)
    digest = _content_hash(data)
    entry = state_modules.get(rel)
    if not isinstance(entry, dict) or entry.get("content_hash") != digest:
        entry = {"content_hash": digest, "results": {}}
        state_modules[rel] = entry
    entry.setdefault("results", {})
    try:
        source = data.decode("utf-8")
        sites = collect_sites_from_source(source)
    except (SyntaxError, ValueError, UnicodeDecodeError):
        # 宪法第 11 条：一个模块坏了不许崩整轮——记 parse_error 继续别的模块
        return ModulePlan(rel, tests, None, [], entry["results"],
                          "parse_error", not tests)
    return ModulePlan(rel, tests, source, sites, entry["results"], "ok", not tests)


def round_robin(pending_lists):
    """[[a1,a2],[b1]] → [a1,b1,a2]——预算内每个模块都被访问（跨夜公平性）。"""
    order = []
    idx = 0
    while True:
        emitted = False
        for lst in pending_lists:
            if idx < len(lst):
                order.append(lst[idx])
                emitted = True
        if not emitted:
            return order
        idx += 1


def run_targets(repo_root, targets, *, budget_seconds, mutant_timeout,
                state, clock=time.monotonic, subset_runner=None, log=print,
                prune_state=False, checkpoint=None):
    """全部靶区模块跑一轮（受预算封顶）→ (report dict, state)。

    subset_runner 注入缝（测试用假 runner，绝不 spawn）：
        runner(module_rel, mutant_source_or_None, test_files, timeout) -> status
    mutant_source 为 None = baseline（未变异）运行。
    prune_state 只在整个靶区都在跑时开（--all）——单模块运行绝不清别人的账。
    checkpoint（可选零参回调）每 CHECKPOINT_EVERY 个变异体调一次——caller 用它
    落 state 文件，硬杀（workflow 超时）最多丢一个窗口的账。
    """
    deadline = clock() + budget_seconds
    state_modules = state.setdefault("modules", {})
    if prune_state:
        for stale in [k for k in state_modules if k not in targets]:
            del state_modules[stale]  # 靶区移除的模块不留账
    plans = [_prepare_module(repo_root, rel, tests, state_modules)
             for rel, tests in sorted(targets.items())]

    workspace_holder = {}

    def _real_runner(module_rel, mutant_source, test_files, timeout):
        if "dir" not in workspace_holder:
            workspace_holder["tmp"] = tempfile.mkdtemp(prefix="mutate-ws-")
            workspace_holder["dir"] = build_workspace(
                repo_root, Path(workspace_holder["tmp"]) / "tree")
        workspace = workspace_holder["dir"]
        target = Path(workspace) / module_rel
        original = target.read_bytes()
        try:
            if mutant_source is not None:
                target.write_text(mutant_source, encoding="utf-8")
            status, _elapsed = run_subset(workspace, test_files, timeout)
            return status
        finally:
            target.write_bytes(original)

    runner = subset_runner or _real_runner
    executed_this_run = 0
    budget_hit = False
    try:
        pending_map = {}
        for plan in plans:
            if plan.status != "ok":
                continue
            pending_map[plan.rel] = [
                (plan, i) for i, site in enumerate(plan.sites)
                if site.site_id not in plan.results]

        # baseline：映射子集必须先绿，红的映射比没有映射更坏（假杀伤）。
        # 已跑完（无 pending）的模块跳过 baseline——完成态的重复运行零成本。
        for plan in plans:
            if plan.status != "ok" or not pending_map.get(plan.rel):
                continue
            if clock() >= deadline:
                budget_hit = True
                break
            try:
                status = runner(plan.rel, None, plan.tests, mutant_timeout * 5)
            except OSError as exc:  # workspace 里没有该文件等 IO 角落
                log(f"mutate: baseline IO error for {plan.rel}: {exc}")
                status = "fail"
            if status != "pass":
                plan.status = "baseline_failed"
                pending_map[plan.rel] = []
                log(f"mutate: baseline {status} for {plan.rel} — 该模块本轮跳过")

        pending_lists = [pending_map[plan.rel] for plan in plans
                         if plan.status == "ok" and pending_map.get(plan.rel)]

        for plan, index in round_robin(pending_lists):
            if clock() >= deadline:
                budget_hit = True
                break
            site = plan.sites[index]
            try:
                mutant = render_mutant(plan.source, index)
            except (SyntaxError, ValueError) as exc:  # unparse 极端角落
                plan.results[site.site_id] = {
                    "status": "error", "line": site.lineno, "col": site.col,
                    "op": site.op, "detail": f"{site.detail} ({exc})"}
                continue
            try:
                status = runner(plan.rel, mutant, plan.tests, mutant_timeout)
            except OSError as exc:
                # 单变异体的 IO 角落不许崩整轮（宪法第 11 条）——记 error 继续
                log(f"mutate: workspace error at {plan.rel} {site.site_id}: {exc}")
                status = "workspace_error"
            outcome = {"pass": "survived", "fail": "killed",
                       "timeout": "timeout"}.get(status, "error")
            plan.results[site.site_id] = {
                "status": outcome, "line": site.lineno, "col": site.col,
                "op": site.op, "detail": site.detail}
            executed_this_run += 1
            if checkpoint is not None and \
                    executed_this_run % CHECKPOINT_EVERY == 0:
                checkpoint()
    finally:
        if "tmp" in workspace_holder:
            shutil.rmtree(workspace_holder["tmp"], ignore_errors=True)

    report = build_report(plans, budget_seconds=budget_seconds,
                          executed_this_run=executed_this_run,
                          budget_hit=budget_hit)
    return report, state


def build_report(plans, *, budget_seconds, executed_this_run, budget_hit):
    """聚合报告（JSON schema 的唯一出生点；字段 add-only——P5 循环消费它）。"""
    modules = {}
    complete = True
    for plan in plans:
        counts = {"killed": 0, "survived": 0, "timeout": 0, "error": 0}
        survivors = []
        known_ids = {site.site_id for site in plan.sites}
        for site_id, result in sorted(plan.results.items()):
            if site_id not in known_ids:
                continue
            counts[result["status"]] = counts.get(result["status"], 0) + 1
            if result["status"] == "survived":
                survivors.append({
                    "site": site_id, "line": result["line"], "col": result["col"],
                    "op": result["op"], "detail": result["detail"],
                    "location": f"{plan.rel}:{result['line']}"})
        executed = sum(counts.values())
        pending = len(plan.sites) - executed
        if plan.status == "ok" and pending:
            complete = False
        if plan.status in ("baseline_failed", "parse_error", "missing"):
            complete = False
        denominator = counts["killed"] + counts["survived"] + counts["timeout"]
        score = ((counts["killed"] + counts["timeout"]) / denominator
                 if denominator else None)
        modules[plan.rel] = {
            "status": plan.status,
            "tests": [str(t) for t in plan.tests],
            "slow_full_suite": plan.slow,
            "sites_total": len(plan.sites),
            "executed": executed,
            "pending": pending,
            "killed": counts["killed"],
            "survived": counts["survived"],
            "timeout": counts["timeout"],
            "error": counts["error"],
            "score": round(score, 4) if score is not None else None,
            "survivors": survivors,
        }
    return {
        "schema": SCHEMA,
        "runner_version": RUNNER_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "budget_seconds": budget_seconds,
        "budget_hit": budget_hit,
        "executed_this_run": executed_this_run,
        "complete": complete,
        "modules": modules,
    }


# --------------------------------------------------------------------------- #
# markdown 报告（pinned issue 的 body；scripts/qa/mutation_issue.py 负责投递）
# --------------------------------------------------------------------------- #
def render_markdown(report):
    lines = ["# Nightly mutation report", ""]
    state = "complete" if report["complete"] else \
        ("budget hit — resumes next night" if report["budget_hit"] else "partial")
    lines.append(f"Generated {report['generated_at']} · cycle {state} · "
                 f"budget {report['budget_seconds']}s · "
                 f"{report['executed_this_run']} mutants executed this run.")
    lines.append("")
    lines.append("**Never a PR gate** (owner decision D5, CONTRACT §57). "
                 "Surviving mutants are test-gap proposals for the daily "
                 "self-improvement loop — the JSON artifact `mutation-report` "
                 "on the workflow run is the machine-readable feed.")
    lines.append("")
    lines.append("| module | sites | run | killed | survived | timeout | score | status |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for rel, m in sorted(report["modules"].items()):
        score = "—" if m["score"] is None else f"{m['score'] * 100:.1f}%"
        status = m["status"] + (" · slow(full suite)" if m["slow_full_suite"] else "")
        lines.append(f"| `{rel}` | {m['sites_total']} | {m['executed']} | "
                     f"{m['killed']} | {m['survived']} | {m['timeout']} | "
                     f"{score} | {status} |")
    lines.append("")
    survivors = [(rel, s) for rel, m in sorted(report["modules"].items())
                 for s in m["survivors"]]
    lines.append(f"## Surviving mutants ({len(survivors)})")
    lines.append("")
    if survivors:
        # GitHub issue body 上限 65,536 字符——列表封顶，全量永远在 JSON 工件里
        for rel, s in survivors[:MD_SURVIVOR_CAP]:
            lines.append(f"- `{s['location']}` — {s['detail']} (`{s['site']}`)")
        if len(survivors) > MD_SURVIVOR_CAP:
            lines.append(f"- … and {len(survivors) - MD_SURVIVOR_CAP} more — "
                         "full list in the `mutation-report` JSON artifact")
    else:
        lines.append("None — every executed mutant was killed. 测试网无洞（本轮范围内）。")
    lines.append("")
    lines.append("Run locally (no launchd agent — owner machine stays lean): "
                 "`python3 scripts/qa/mutate.py --all`")
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def load_targets(path):
    tables = parse_targets_toml(Path(path).read_text(encoding="utf-8"))
    config = tables.get("config", {})
    raw_targets = tables.get("targets", {})
    targets = {}
    for rel, tests in raw_targets.items():
        if not isinstance(tests, list):
            raise ValueError(f"targets[{rel!r}] 必须是测试文件数组")
        targets[rel] = tests
    return config, targets


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="stdlib-only mutation runner（夜间；永不作为 PR 门——D5）")
    parser.add_argument("--all", action="store_true",
                        help="跑靶区文件里的全部模块")
    parser.add_argument("--modules", nargs="*", default=None,
                        help="只跑这些模块（repo 相对路径；未映射 = 全套件 fallback，慢）")
    parser.add_argument("--targets", default=DEFAULT_TARGETS)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--state", default=DEFAULT_STATE)
    parser.add_argument("--json", dest="json_out", default=DEFAULT_JSON)
    parser.add_argument("--md", dest="md_out", default=DEFAULT_MD)
    parser.add_argument("--time-budget", type=int, default=None,
                        help=f"秒（默认取 targets 配置，再默认 {DEFAULT_BUDGET_SECONDS}）")
    parser.add_argument("--force", action="store_true",
                        help="作废 state，全部重跑")
    parser.add_argument("--list", action="store_true",
                        help="只列 sites 不执行（调试用）")
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root).resolve()
    config, targets = load_targets(repo_root / args.targets)
    if args.modules:
        targets = {rel: targets.get(rel, []) for rel in args.modules}
    elif not args.all:
        parser.error("需要 --all 或 --modules")

    if args.list:
        for rel in sorted(targets):
            try:
                source = (repo_root / rel).read_text(encoding="utf-8")
            except OSError as exc:
                print(f"{rel}: unreadable ({exc})")
                continue
            for site in collect_sites_from_source(source):
                print(f"{rel}:{site.lineno}:{site.col} {site.site_id} {site.detail}")
        return 0

    budget = args.time_budget if args.time_budget is not None else \
        int(config.get("time_budget_seconds", DEFAULT_BUDGET_SECONDS))
    mutant_timeout = int(config.get("per_mutant_timeout_seconds",
                                    DEFAULT_MUTANT_TIMEOUT))
    state_path = repo_root / args.state
    state = {"schema": SCHEMA, "runner_version": RUNNER_VERSION, "modules": {}} \
        if args.force else load_state(state_path)

    report, state = run_targets(
        repo_root, targets, budget_seconds=budget,
        mutant_timeout=mutant_timeout, state=state, prune_state=bool(args.all),
        checkpoint=lambda: save_state(state_path, state))

    save_state(state_path, state)
    json_path = repo_root / args.json_out
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=1, sort_keys=True),
                         encoding="utf-8")
    md_path = repo_root / args.md_out
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(render_markdown(report), encoding="utf-8")

    total_survived = sum(m["survived"] for m in report["modules"].values())
    total_executed = sum(m["executed"] for m in report["modules"].values())
    print(f"mutate: {total_executed} mutants recorded, {total_survived} surviving; "
          f"complete={report['complete']} -> {json_path}")
    return 0  # 报告型工具：存活变异体不是失败（D5）；只有硬错误才非零


if __name__ == "__main__":
    sys.exit(main())
