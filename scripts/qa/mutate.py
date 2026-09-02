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
  .qa/mutation/state.json     断点续跑台账（跨夜 resume；模块内容**或其映射
                              测试子集**的 hash 变 = 该模块结果作废重跑——
                              测试变强必须重新判存活，不然夜报永远复述
                              已被杀死的变异体）

确定性与预算：site 顺序 = AST 深度优先遍历序（跑两遍同一棵树得同一列表）；
round-robin 跨模块交错执行，预算（--time-budget）到点即停——每个模块每晚都
被访问到，长模块跨几夜跑完。

判例：tests/test_mutate_sites.py（site 生成/跳过规则/预算与续跑调度）、
tests/integration/test_mutation_runner.py（真子进程杀伤判定 + 弱测试存活 +
测试变强作废旧账）。
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
# 变异算子/跳过规则的版本号：site 列表的生成规则变 → bump → 旧 state 全部
# 作废（site_id 不再可比）。v2：logging 跳过从「名字含 log」子串启发收紧为
# 精确名单（§57「不许悄悄扩」）。
RUNNER_VERSION = 2

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
# 几乎不可能被行为测试杀死，全部不铸 site。**精确名单**，不是子串匹配——
# `catalog(...)`、`_merge_event_logged(...)` 这类名字含 log 的真谓词照常
# 变异（§57：跳过名单成文、不许悄悄扩）。
_LOGGING_CALL_NAMES = frozenset(
    {"debug", "info", "warning", "error", "exception", "critical", "log",
     "log_event"})

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


def _quoted(text):
    """带双引号的字符串字面量（子集里无转义）。"""
    return text.startswith('"') and text.endswith('"') and len(text) >= 2


def _parse_array(raw, where):
    inner = raw[1:-1].strip()
    if not inner:
        return []
    items = []
    for piece in inner.split(","):
        piece = piece.strip()
        if not piece:
            continue
        if not _quoted(piece):
            raise ValueError(f"{where}: 数组元素必须是双引号字符串: {piece!r}")
        items.append(piece[1:-1])
    return items


def _parse_value(raw, where):
    raw = raw.strip()
    if raw.startswith("[") and raw.endswith("]"):
        return _parse_array(raw, where)
    if _quoted(raw):
        return raw[1:-1]
    if raw in ("true", "false"):
        return raw == "true"
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"{where}: 不在支持的 TOML 子集内: {raw!r}") from None


def _table_name(line, where, rawline):
    name = line[1:-1].strip()
    if not name or name.startswith("["):
        raise ValueError(f"{where}: 非法 table 头: {rawline!r}")
    return name


def _split_key(line, where, rawline):
    """`key = value` 行 → (key 去引号, 原始 value 文本)。"""
    if "=" not in line:
        raise ValueError(f"{where}: 期望 key = value: {rawline!r}")
    key, _, raw = line.partition("=")
    key = key.strip()
    if _quoted(key):
        key = key[1:-1]
    return key, raw


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
            current = tables.setdefault(_table_name(line, where, rawline), {})
            continue
        if current is None:
            raise ValueError(f"{where}: key 出现在任何 [table] 之前")
        key, raw = _split_key(line, where, rawline)
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
    return _call_simple_name(node) in _LOGGING_CALL_NAMES


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


def _compare_sites(node, sites):
    for j, op in enumerate(node.ops):
        flip = _CMP_FLIPS.get(type(op))
        if flip:
            repl, detail = flip
            sites.append(Site(f"cmp_{type(op).__name__.lower()}",
                              node.lineno, node.col_offset, detail,
                              lambda n=node, j=j, r=repl: n.ops.__setitem__(j, r())))


def _binop_sites(node, sites):
    if isinstance(node.op, ast.Add):
        sites.append(Site("arith_add", node.lineno, node.col_offset, "+ -> -",
                          lambda n=node: setattr(n, "op", ast.Sub())))
    elif isinstance(node.op, ast.Sub):
        sites.append(Site("arith_sub", node.lineno, node.col_offset, "- -> +",
                          lambda n=node: setattr(n, "op", ast.Add())))


def _boolop_sites(node, sites):
    if isinstance(node.op, ast.And):
        sites.append(Site("bool_and", node.lineno, node.col_offset, "and -> or",
                          lambda n=node: setattr(n, "op", ast.Or())))
    else:
        sites.append(Site("bool_or", node.lineno, node.col_offset, "or -> and",
                          lambda n=node: setattr(n, "op", ast.And())))


def _return_sites(node, sites):
    value = node.value
    if value is None or (isinstance(value, ast.Constant) and value.value is None):
        return  # `return` / `return None` 变异成自己 = 等价变异体
    sites.append(Site("return_none", node.lineno, node.col_offset,
                      "return X -> return None",
                      lambda n=node: setattr(n, "value", ast.Constant(value=None))))


# node 类型 → 铸 site 的函数（顺序 = 原 elif 链；类型互斥，首中即停）。
_SITE_MAKERS = (
    (ast.Compare, _compare_sites),
    (ast.BinOp, _binop_sites),
    (ast.BoolOp, _boolop_sites),
    (ast.Constant, _const_sites),
    (ast.Return, _return_sites),
)


def _node_sites(node, sites):
    """当前 node 自身能铸出的 sites（不含 continue/break——那要父语句表）。"""
    for cls, maker in _SITE_MAKERS:
        if isinstance(node, cls):
            maker(node, sites)
            return


def _skip_subtree(node):
    """整棵子树不铸 site 的规则（等价变异体高发区，§57 明文）：
    __repr__ 函数体、`if __name__ == "__main__"` 守卫、logging 类调用。"""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and \
            node.name == "__repr__":
        return True
    if _is_main_guard(node):
        return True
    return isinstance(node, ast.Call) and _skip_call(node)


def _loop_flow_site(children, idx, child, sites):
    """语句表里的 continue ↔ break 位点（需要父列表才能原地替换节点）。"""
    if isinstance(child, ast.Continue):
        into, detail = ast.Break, "continue -> break"
    else:
        into, detail = ast.Continue, "break -> continue"
    sites.append(Site("loop_flow", child.lineno, child.col_offset, detail,
                      lambda lst=children, i=idx, cls=into, c=child:
                      lst.__setitem__(i, ast.copy_location(cls(), c))))


def _walk_children(children, sites):
    for idx, child in enumerate(children):
        if isinstance(child, (ast.Continue, ast.Break)):
            _loop_flow_site(children, idx, child, sites)
        if isinstance(child, ast.AST):
            _walk(child, sites)


def _walk(node, sites):
    if _skip_subtree(node):
        return
    _node_sites(node, sites)
    for _field, value in ast.iter_fields(node):
        if isinstance(value, list):
            _walk_children(value, sites)
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
def _git_listing(repo_root):
    """git 树的 tracked 文件列表；非 git 树 / git 不可用 → None（走 fallback）。"""
    try:
        listing = subprocess.run(
            ["git", "-C", str(repo_root), "ls-files", "-z"],
            capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if listing.returncode != 0 or not listing.stdout:
        return None
    return listing.stdout.decode("utf-8", "replace").split("\0")


def _copy_git_tree(repo_root, dest, rels):
    for rel in rels:
        if not rel:
            continue
        src = repo_root / rel
        if not src.is_file():
            continue  # tracked 但被删的文件
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)


def _copy_fallback(repo_root, dest):
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


def build_workspace(repo_root, dest):
    """把待测树复制到 dest：git 树走 `git ls-files`（精确 tracked 集合，天然排除
    state//缓存），非 git 树（测试 fixture）fallback 全量复制减 _IGNORE_NAMES。"""
    repo_root = Path(repo_root)
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    rels = _git_listing(repo_root)
    if rels is not None:
        _copy_git_tree(repo_root, dest, rels)
    else:
        _copy_fallback(repo_root, dest)
    return dest


def _test_argv(test_files):
    """测试文件路径 → unittest argv；空映射 fallback 全套件（报告标 slow）。"""
    if not test_files:
        return ["-m", "unittest", "discover", "-s", "tests"]
    modules = [str(t)[:-3].replace("/", ".").replace(os.sep, ".")
               for t in test_files]
    return ["-m", "unittest"] + modules


def _kill_group(proc):
    """超时收割：POSIX 连孙进程一起 killpg（测试可能 spawn 子进程）。"""
    if os.name == "posix":
        try:
            os.killpg(proc.pid, 9)
        except OSError:
            pass
    proc.kill()
    proc.wait()


def run_subset(workspace, test_files, timeout, python_exe=None):
    """在 workspace 里跑定向子集 → ("pass"|"fail"|"timeout", seconds)。

    POSIX 下起新进程组，超时连孙进程一起 killpg（测试可能 spawn 子进程）。
    """
    cmd = [python_exe or sys.executable] + _test_argv(test_files)
    env = os.environ.copy()
    home = tempfile.mkdtemp(prefix="mutate-home-")
    env["AIASSISTANT_HOME"] = home
    # 子进程的 tests/__init__ 会无条件再 mkdtemp 一个自己的沙箱 HOME——把
    # 临时目录根指进本轮的 home，孙目录随下面的 rmtree 一起消失（不然
    # --all 一晚在 owner 机器上泄漏 ~1,200 个 tempdir，防腐 #4）。
    # TMPDIR 是 POSIX 键，TEMP/TMP 是 Windows 侧同义键。
    env["TMPDIR"] = env["TEMP"] = env["TMP"] = home
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
        _kill_group(proc)
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


def _fingerprint_files(repo_root, tests):
    """测试指纹覆盖的文件列表：有映射 = 子集自身（列表变 = 指纹变）；
    未映射（全套件 fallback）= tests/ 树下全部 .py（排序，确定性）。"""
    if tests:
        return [str(t) for t in tests]
    tests_dir = Path(repo_root) / "tests"
    if not tests_dir.is_dir():
        return []
    return sorted(p.relative_to(repo_root).as_posix()
                  for p in tests_dir.rglob("*.py"))


def _tests_fingerprint(repo_root, tests):
    """映射测试子集的内容指纹。测试变强必须作废该模块的旧账，否则夜报把
    已被杀死的变异体继续当「测试网的洞」喂给 P5（v0.48.13 审查 B3）。"""
    digest = hashlib.sha256()
    for rel in _fingerprint_files(repo_root, tests):
        digest.update(rel.encode("utf-8") + b"\0")
        try:
            digest.update((Path(repo_root) / rel).read_bytes())
        except OSError:
            digest.update(b"<missing>")
        digest.update(b"\0")
    return digest.hexdigest()


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
    tests_digest = _tests_fingerprint(repo_root, tests)
    entry = state_modules.get(rel)
    # 模块内容或映射测试子集任何一边变 = 旧结果作废（tests_hash 缺席的旧
    # state 条目也作废——它们可能早于测试网的强化）。
    if (not isinstance(entry, dict) or entry.get("content_hash") != digest
            or entry.get("tests_hash") != tests_digest):
        entry = {"content_hash": digest, "tests_hash": tests_digest,
                 "results": {}}
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


def _prune_stale(state_modules, targets):
    for stale in [k for k in state_modules if k not in targets]:
        del state_modules[stale]  # 靶区移除的模块不留账


def _pending_sites(plans):
    """{rel: [(plan, site index), …]}——只含还没记账的 site。"""
    pending_map = {}
    for plan in plans:
        if plan.status != "ok":
            continue
        pending_map[plan.rel] = [
            (plan, i) for i, site in enumerate(plan.sites)
            if site.site_id not in plan.results]
    return pending_map


def _make_real_runner(repo_root, workspace_holder):
    """真 runner：懒建一次工作区副本，每个变异体覆写目标文件、跑完还原。"""
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

    return _real_runner


def _baseline_status(runner, plan, timeout, log):
    try:
        return runner(plan.rel, None, plan.tests, timeout)
    except OSError as exc:  # workspace 里没有该文件等 IO 角落
        log(f"mutate: baseline IO error for {plan.rel}: {exc}")
        return "fail"


def _run_baselines(plans, pending_map, runner, mutant_timeout, deadline,
                   clock, log):
    """baseline：映射子集必须先绿，红的映射比没有映射更坏（假杀伤）。
    已跑完（无 pending）的模块跳过 baseline——完成态的重复运行零成本。
    返回是否被预算打断。"""
    for plan in plans:
        if plan.status != "ok" or not pending_map.get(plan.rel):
            continue
        if clock() >= deadline:
            return True
        status = _baseline_status(runner, plan, mutant_timeout * 5, log)
        if status != "pass":
            plan.status = "baseline_failed"
            pending_map[plan.rel] = []
            log(f"mutate: baseline {status} for {plan.rel} — 该模块本轮跳过")
    return False


def _execute_mutant(plan, index, runner, mutant_timeout, log):
    """一个 site 的执行与记账；render 失败记 error 且不计入执行数。"""
    site = plan.sites[index]
    try:
        mutant = render_mutant(plan.source, index)
    except (SyntaxError, ValueError) as exc:  # unparse 极端角落
        plan.results[site.site_id] = {
            "status": "error", "line": site.lineno, "col": site.col,
            "op": site.op, "detail": f"{site.detail} ({exc})"}
        return False
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
    return True


def _maybe_checkpoint(checkpoint, executed):
    if checkpoint is not None and executed % CHECKPOINT_EVERY == 0:
        checkpoint()


def _run_mutants(plans, pending_map, runner, mutant_timeout, deadline, clock,
                 log, checkpoint):
    """round-robin 执行全部 pending 变异体 → (执行数, 是否被预算打断)。"""
    pending_lists = [pending_map[plan.rel] for plan in plans
                     if plan.status == "ok" and pending_map.get(plan.rel)]
    executed = 0
    for plan, index in round_robin(pending_lists):
        if clock() >= deadline:
            return executed, True
        if _execute_mutant(plan, index, runner, mutant_timeout, log):
            executed += 1
            _maybe_checkpoint(checkpoint, executed)
    return executed, False


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
        _prune_stale(state_modules, targets)
    plans = [_prepare_module(repo_root, rel, tests, state_modules)
             for rel, tests in sorted(targets.items())]

    workspace_holder = {}
    runner = subset_runner or _make_real_runner(repo_root, workspace_holder)
    try:
        pending_map = _pending_sites(plans)
        budget_hit = _run_baselines(plans, pending_map, runner, mutant_timeout,
                                    deadline, clock, log)
        executed_this_run, mutants_hit = _run_mutants(
            plans, pending_map, runner, mutant_timeout, deadline, clock, log,
            checkpoint)
        budget_hit = budget_hit or mutants_hit
    finally:
        if "tmp" in workspace_holder:
            shutil.rmtree(workspace_holder["tmp"], ignore_errors=True)

    report = build_report(plans, budget_seconds=budget_seconds,
                          executed_this_run=executed_this_run,
                          budget_hit=budget_hit)
    return report, state


def _tally_results(plan):
    """模块的记账结果 → (状态计数, 存活体列表)；不认识的 site_id 忽略。"""
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
    return counts, survivors


def _module_score(counts):
    """kill 率：timeout 记 killed 侧（变异体把测试跑挂 = 被行为差异抓住）。"""
    denominator = counts["killed"] + counts["survived"] + counts["timeout"]
    if not denominator:
        return None
    return round((counts["killed"] + counts["timeout"]) / denominator, 4)


def _module_complete(plan, pending):
    if plan.status == "ok" and pending:
        return False
    return plan.status not in ("baseline_failed", "parse_error", "missing")


def build_report(plans, *, budget_seconds, executed_this_run, budget_hit):
    """聚合报告（JSON schema 的唯一出生点；字段 add-only——P5 循环消费它）。"""
    modules = {}
    complete = True
    for plan in plans:
        counts, survivors = _tally_results(plan)
        executed = sum(counts.values())
        pending = len(plan.sites) - executed
        complete = complete and _module_complete(plan, pending)
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
            "score": _module_score(counts),
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
def _cycle_state(report):
    if report["complete"]:
        return "complete"
    if report["budget_hit"]:
        return "budget hit — resumes next night"
    return "partial"


def _md_module_rows(modules):
    rows = []
    for rel, m in sorted(modules.items()):
        score = "—" if m["score"] is None else f"{m['score'] * 100:.1f}%"
        status = m["status"] + (" · slow(full suite)" if m["slow_full_suite"] else "")
        rows.append(f"| `{rel}` | {m['sites_total']} | {m['executed']} | "
                    f"{m['killed']} | {m['survived']} | {m['timeout']} | "
                    f"{score} | {status} |")
    return rows


def _md_survivor_lines(survivors):
    if not survivors:
        return ["None — every executed mutant was killed. 测试网无洞（本轮范围内）。"]
    # GitHub issue body 上限 65,536 字符——列表封顶，全量永远在 JSON 工件里
    lines = [f"- `{s['location']}` — {s['detail']} (`{s['site']}`)"
             for _rel, s in survivors[:MD_SURVIVOR_CAP]]
    if len(survivors) > MD_SURVIVOR_CAP:
        lines.append(f"- … and {len(survivors) - MD_SURVIVOR_CAP} more — "
                     "full list in the `mutation-report` JSON artifact")
    return lines


def render_markdown(report):
    lines = ["# Nightly mutation report", ""]
    lines.append(f"Generated {report['generated_at']} · cycle {_cycle_state(report)} · "
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
    lines.extend(_md_module_rows(report["modules"]))
    lines.append("")
    survivors = [(rel, s) for rel, m in sorted(report["modules"].items())
                 for s in m["survivors"]]
    lines.append(f"## Surviving mutants ({len(survivors)})")
    lines.append("")
    lines.extend(_md_survivor_lines(survivors))
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


def _list_sites(repo_root, targets):
    for rel in sorted(targets):
        try:
            source = (repo_root / rel).read_text(encoding="utf-8")
        except OSError as exc:
            print(f"{rel}: unreadable ({exc})")
            continue
        for site in collect_sites_from_source(source):
            print(f"{rel}:{site.lineno}:{site.col} {site.site_id} {site.detail}")
    return 0


def _resolve_budget(args, config):
    if args.time_budget is not None:
        return args.time_budget
    return int(config.get("time_budget_seconds", DEFAULT_BUDGET_SECONDS))


def _initial_state(force, state_path):
    if force:
        return {"schema": SCHEMA, "runner_version": RUNNER_VERSION, "modules": {}}
    return load_state(state_path)


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
        return _list_sites(repo_root, targets)

    mutant_timeout = int(config.get("per_mutant_timeout_seconds",
                                    DEFAULT_MUTANT_TIMEOUT))
    state_path = repo_root / args.state
    state = _initial_state(args.force, state_path)

    report, state = run_targets(
        repo_root, targets, budget_seconds=_resolve_budget(args, config),
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
    if not any(m["status"] == "ok" for m in report["modules"].values()):
        # 报告型工具照旧退 0（D5），但整夜零模块可执行 = 仪表在空转——
        # 在 stderr 喊一声，别让坏映射永远静默地绿下去。
        print("mutate: WARNING — no module reached execution "
              "(baseline_failed / parse_error / missing everywhere); "
              "tonight's survivors feed is stale", file=sys.stderr)
    return 0  # 报告型工具：存活变异体不是失败（D5）；只有硬错误才非零


if __name__ == "__main__":
    sys.exit(main())
