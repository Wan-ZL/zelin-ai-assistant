#!/usr/bin/env python3
"""自动行为总账的硬门（CONTRACT §81；账本 truth = act/lib/automation.py:LEDGER）。

issue #451 / owner 决策 D83：这道门是「整理出来后重新设计」里**整理**那一半的
守夜人。总账自己只是一张表，表会烂——这里把它的四条不变量钉成可执行判据，
存量欠账明账挂 ``qa/automation_baseline.txt``（shrink-only，由
``scripts/qa/ledger_diff.py`` 自动看管：``qa/*_baseline.txt`` 是它的发现规则）。

违例键（前缀 = 规则名，与 deps / hygiene 同风格；分数恒 1.0 = 布尔违例）：

    switch:<slug>              这条 keep 行没有开关（不变量 1）
    cold-switch:<slug>:<字段>  actd 行的开关没进 live_fields（不变量 2）
    audit:<slug>               动手不留痕（不变量 3）
    default-on:<slug>          铸卡 / 删数据 / 花钱的行出厂是开的（不变量 4 / ask 4）
    code:<slug>                code 指针指不到真文件 / 真符号
    law:<slug>:§<N>            law 里的 § 在 CONTRACT 里没有正文
    orphan-call:<路径>:<slug>  代码里 enabled("X") / audit("X") 的 X 不在总账里
    no-callsite:<slug>         行说它往 automation.jsonl 留痕，代码里却没人 audit 过（空头支票）
    pinned:<slug>:<键>         模板把一把代价大的开关钉成活行（ask 4 的第二只眼）
    unlisted:<源>:<id>         调度器里有、总账里没有（完备性；三个可枚举源）

**诚实条款**（照 §77.1 的写法）：可枚举的只有 committed 的调度器文件——launchd
plist 的 Label、`install.sh` 的 cron 行变量、`.github/workflows/*.yml` 里带
``schedule:`` 的那些。库内重试循环、后台线程、Swift 壳侧 timer、server 的
watcher 线程**数不到**，本门不假装数得到；它们靠 ``no-callsite`` / ``audit``
两条与人工复核兜着。不知道就说不知道（§0 第 3 条）。

用法（不依赖 coverage，可单独跑）::

    python3 scripts/qa/automation_check.py --check [--report DIR]
    python3 scripts/qa/automation_check.py --list
    python3 scripts/qa/automation_check.py --write-baseline

判例：tests/test_qa_automation_gate.py。
"""

import argparse
import ast
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import qa_common  # noqa: E402

sys.path.insert(0, qa_common.REPO_ROOT)
from act.lib import automation, config  # noqa: E402

BASELINE = os.path.join(qa_common.REPO_ROOT, "qa", "automation_baseline.txt")

# CONTRACT 的节号正则（与 scripts/qa/coverage_inventory.py 同口径：`## N.` 与 `### N.M`）
_SECTION_RE = re.compile(r"^#{2,3} (\d+)\.", re.M)
# launchd plist 的 Label（<key>Label</key> 后面那个 <string>）
_LABEL_RE = re.compile(r"<key>Label</key>\s*<string>([^<]+)</string>")
# install.sh 里的 cron 行变量：`XXX_LINE="…"` / `INGEST_CHAIN="…"`，值以 cron 表达式开头
_CRON_VAR_RE = re.compile(r"^([A-Z_]+)=\"[-0-9*/, ]+ \* \* \*", re.M)
# 在 act/** server/** 里出现的 automation.enabled("X") / automation.audit("X")
_CALL_FUNCS = ("enabled", "audit")
_CALLSITE_DIRS = ("act", "server")


def _read(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


# --------------------------------------------------------------------------- #
# 不变量 1-4（纯表内判据 + 一次 hermetic Config() 求值）
# --------------------------------------------------------------------------- #
def _scan_switch(row, live, scores):
    """不变量 1 —— 但只对**代价大**的行执法。

    「每条行为一把开关」若照字面执行，就会要求给「诚实的健康扫描」「清自己
    中间文件的管家」「宿主 launchd agent」也配一把开关：前者违反 §0 第 3 条
    （健康报告不许可关），后两者的开关根本不在 config 层（装不装 agent / 在
    GitHub 上禁用）。所以射程 = ``effect`` 沾 :data:`automation.COSTLY` 的行
    ——会铸卡 / 会删数据 / 会花钱的，必须有人能停下它。
    """
    if not row.costly:
        return
    if row.kind == automation.KIND_NONE or not row.switch:
        scores["switch:%s" % row.slug] = 1.0
        return
    _scan_hot(row, live, scores)


def _scan_hot(row, live, scores):
    """不变量 2：actd 行的每一把开关都必须在 :func:`automation.live_fields` 里，
    否则它是「设置页翻了要重启守护进程才生效」的冷开关。"""
    if not row.live:
        return
    for name in row.switch:
        if name not in live:
            scores["cold-switch:%s:%s" % (row.slug, name)] = 1.0


def _scan_audit(row, scores):
    """不变量 3 —— 同样只对代价大的行执法（只读行为没什么可留痕的）。"""
    if row.costly and row.audit == automation.AUDIT_NONE:
        scores["audit:%s" % row.slug] = 1.0


def _scan_default(row, factory, scores):
    """ask 4：铸卡 / 删数据 / 花钱的行，出厂默认必须是关。

    用 ``config.Config()``（纯出厂值，零 IO、零网络）求值——**不读**机器上的
    config.yaml / overrides，否则门的判决会随跑它的那台机器变。
    """
    if row.costly and automation.row_enabled(row, factory):
        scores["default-on:%s" % row.slug] = 1.0


def _scan_pinned(row, template, scores):
    """ask 4 的第二半：模板**钉死**一把代价大的开关，等于每台新装机都带着一个
    用户从没做过的「显式选择」。

    `_scan_default` 只看 `config.Config()`（纯出厂值、可复现），所以它永远看不见
    `config.example.yaml` 里那行活的 `enabled: true`——2026-09-02 到 09-14 之间
    装的机器就是这么带上 `self_improve.enabled: true` 的（D57 原话）。这条规则补上
    那只眼睛：代价大的行，它的开关键不许出现在模板的**活行**里（注释掉的不算）。

    诚实条款：只判**带块名的**开关（`<块>.<键>` 与 `features.<flag>`）——那种
    拼法在模板里有确定的位置，找得准。扁平 Config 字段（`auto_resume` 之于
    `execution:`）在总账里不带 yaml 路径，靠末段字符串去模板里捞会误伤同名的
    别家键（`daily_loop.trash_retention_days` 就是现成的例子），宁可不判。
    """
    if not row.costly:
        return
    for name in row.switch:
        if "." not in name:
            continue
        block, key = name.split(".", 1)
        if _pinned_in_block(template, block, key):
            scores["pinned:%s:%s" % (row.slug, name)] = 1.0


def _pinned_in_block(template, block, key):
    """模板里 `<block>:` 那一段的活行中有没有 `<key>:`（注释行不算）。"""
    body = re.search(r"^%s:\s*$\n((?:[ \t].*\n|\n)*)" % re.escape(block),
                     template, re.M)
    if body is None:
        return False
    return re.search(r"^\s+%s\s*:" % re.escape(key), body.group(1), re.M) is not None


def _scan_pointers(row, sections, scores):
    if not _code_resolves(row.code):
        scores["code:%s" % row.slug] = 1.0
    for section in row.law:
        if section.lstrip("§").split(".")[0] not in sections:
            scores["law:%s:%s" % (row.slug, section)] = 1.0


def _code_resolves(code):
    """``<相对路径>[:<符号>]`` 指得到真东西吗。"""
    rel, _, symbol = code.partition(":")
    text = _read(os.path.join(qa_common.REPO_ROOT, rel))
    if text is None:
        return False
    if not symbol:
        return True
    if rel.endswith(".py"):
        return _symbol_in_py(text, symbol)
    return symbol in text


def _symbol_in_py(text, symbol):
    """模块级定义、类里的方法（qualname 末段）、模块级常量都算「指得到」。"""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return False
    names = {q for q, _n, _k in qa_common.collect_definitions(tree)}
    if symbol in names or any(q.rsplit(".", 1)[-1] == symbol for q in names):
        return True
    return re.search(r"^%s\b" % re.escape(symbol), text, re.M) is not None


def contract_sections(root=None):
    text = _read(os.path.join(root or qa_common.REPO_ROOT, "docs", "CONTRACT.md")) or ""
    return set(_SECTION_RE.findall(text))


# --------------------------------------------------------------------------- #
# 调用点（幽灵 slug / 空头支票）
# --------------------------------------------------------------------------- #
def _is_automation_call(func):
    """``automation.enabled`` / ``automation.audit`` 这个调用目标吗。"""
    return (isinstance(func, ast.Attribute) and func.attr in _CALL_FUNCS
            and isinstance(func.value, ast.Name) and func.value.id == "automation")


def _literal_first_arg(node):
    """首参是字符串字面量就返回它，否则 None（变量传参数不到，不假装数得到）。"""
    if not node.args:
        return None
    first = node.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return first.value
    return None


class _CallCollector(ast.NodeVisitor):
    """收 ``automation.<enabled|audit>("<slug>", …)`` 的字面量首参。

    两个函数**分开记**：`no-callsite` 问的是「这行说它往 automation.jsonl 留痕，
    代码里真有人 audit 过吗」，拿 `enabled()` 的调用点去顶这个证据等于放它过关
    ——一条新行只要写了闸门、忘了回执，门照样绿，而总账那一列在撒谎。
    """

    def __init__(self):
        self.by_func = {name: set() for name in _CALL_FUNCS}

    def visit_Call(self, node):
        func = node.func
        if _is_automation_call(func):
            slug = _literal_first_arg(node)
            if slug is not None:
                self.by_func[func.attr].add(slug)
        self.generic_visit(node)


def _callsites(root):
    """``({slug: {相对路径, …}}, {audit 过的 slug})`` —— 代码里真的有人问过 / 记过的。"""
    found = {}
    audited = set()
    for path in qa_common.iter_py_files(root, rel_dirs=_CALLSITE_DIRS):
        tree = qa_common.parse_file(path)
        if tree is None:
            continue
        collector = _CallCollector()
        collector.visit(tree)
        rel = os.path.relpath(path, root).replace(os.sep, "/")
        audited |= collector.by_func["audit"]
        for slug in set().union(*collector.by_func.values()):
            found.setdefault(slug, set()).add(rel)
    return found, audited


def _scan_callsites(rows, found, audited, scores):
    """幽灵 slug（代码问了一条总账里没有的行为）与空头支票（行声称往
    ``automation.jsonl`` 留痕，代码里却没有一处 ``automation.audit(…)``）。

    **不**要求每条行都经 ``automation.enabled()`` 判闸：大多数行的闸门是它自己
    模块里那一句历史悠久的 `cfg.feature(...)` / `if not enabled(cfg)`，总账的职责
    是**说明**那把开关在哪，不是把全仓库的门面重写一遍。声称走 :data:`AUDIT_LOG`
    的那些则必须真有调用点——否则总账在撒谎。
    """
    known = {row.slug for row in rows}
    for slug in sorted(set(found) - known):
        for rel in sorted(found[slug]):
            scores["orphan-call:%s:%s" % (rel, slug)] = 1.0
    for row in rows:
        if row.audit == automation.AUDIT_LOG and row.slug not in audited:
            scores["no-callsite:%s" % row.slug] = 1.0


# --------------------------------------------------------------------------- #
# 完备性（三个可枚举的调度器源；truth = scheduler_units 的返回键）
# --------------------------------------------------------------------------- #
def scheduler_units(root=None):
    """{源: {登记名, …}}。只数 committed 文件——数不到的那几类见模块 docstring。"""
    root = root or qa_common.REPO_ROOT
    return {
        "launchd": _launchd_labels(root),
        "cron": _cron_vars(root),
        "gha": _scheduled_workflows(root),
    }


def _launchd_labels(root):
    out = set()
    base = os.path.join(root, "act", "launchd")
    for name in sorted(os.listdir(base)) if os.path.isdir(base) else []:
        if name.endswith(".plist"):
            out.update(_LABEL_RE.findall(_read(os.path.join(base, name)) or ""))
    return out


def _cron_vars(root):
    text = _read(os.path.join(root, "install.sh")) or ""
    return {"install.sh:%s" % name for name in _CRON_VAR_RE.findall(text)}


def _scheduled_workflows(root):
    out = set()
    base = os.path.join(root, ".github", "workflows")
    for name in sorted(os.listdir(base)) if os.path.isdir(base) else []:
        if not name.endswith(".yml"):
            continue
        if re.search(r"^\s*schedule:", _read(os.path.join(base, name)) or "", re.M):
            out.add(".github/workflows/%s" % name)
    return out


def _scan_units(rows, units, scores):
    claimed = {row.unit for row in rows if row.unit}
    for source in sorted(units):
        for unit in sorted(units[source] - claimed):
            scores["unlisted:%s:%s" % (source, unit)] = 1.0


# --------------------------------------------------------------------------- #
def scan(root=None, rows=None):
    """全部违例：{violation_key: 1.0}。``rows`` 是判例的注入缝。"""
    root = root or qa_common.REPO_ROOT
    rows = automation.LEDGER if rows is None else tuple(rows)
    live = set(automation.live_fields())
    sections = contract_sections(root)
    template = _read(os.path.join(root, "config.example.yaml")) or ""
    factory = config.Config()
    scores = {}
    for row in rows:
        if row.verdict != automation.VERDICT_KEEP:
            continue
        _scan_switch(row, live, scores)
        _scan_audit(row, scores)
        _scan_default(row, factory, scores)
        _scan_pinned(row, template, scores)
        _scan_pointers(row, sections, scores)
    found, audited = _callsites(root)
    _scan_callsites(rows, found, audited, scores)
    _scan_units(rows, scheduler_units(root), scores)
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
            print(key)
        print("-- %d violations, %d behaviours in the ledger"
              % (len(scores), len(automation.LEDGER)))
        return 0
    if args.write_baseline:
        print("wrote %d entries to %s"
              % (qa_common.write_ledger(BASELINE, scores, "automation"), BASELINE))
        return 0
    return qa_common.run_gate("automation", scores, BASELINE, threshold=0.0,
                              tolerance=0.0, report_dir=args.report)


if __name__ == "__main__":
    sys.exit(main())
