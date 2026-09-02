#!/usr/bin/env python3
"""test-code skill · 梯子 runner：选择 → 执行（phase 1 并行 / 2 串行 / 3 依赖 coverage）
→ report.md + report.json；退出码 fail-closed。

法典指针：docs/CONTRACT.md §58（项目门 = 项目的，阈值 truth = qa/gates.toml，skill 只读）、
§57（变异报告 survivors 字段 = 每日循环的机器可读输入，本报告逐字转载 file:line）。
设计 = docs/design/vnext2-plan.md R2.8 / D14（owner 交互式 + agent 收工前非交互式两种
调用，R2.8.2）。ASK 步骤本身由 AI 助手在 SKILL.md 里完成；这里只**记录**答案。

用法：
  run_ladder.py [--repo PATH] [--base REF] [--detect FILE]
                (--selection FILE | --tier N [--checks a,b] [--skip a,b] [--chosen-by user|headless])
                [--declared GLOB ...] [--out DIR] [--init-baselines] [--dry-run] [--jobs N]
退出码：0 绿（选定层全部 pass / na）；1 红（任一 fail）；3 不完整（无红，但有
unavailable / substituted——不许写成 pass）；2 用法 / 输入错误。
判例：tests/test_skill_test_code_run_ladder.py（fake runner，含负控制）。
"""

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import checks  # noqa: E402
import detect as detect_mod  # noqa: E402
import ladder_common as lc  # noqa: E402

EXIT_GREEN, EXIT_RED, EXIT_USAGE, EXIT_INCOMPLETE = 0, 1, 2, 3
_TEST_TOOLS = ("python-tests", "js-tests", "swift", "xcodebuild")
_VERSION_ARGV = {
    "python": ["{py}", "--version"], "python-tests": ["{py}", "--version"],
    "coverage": ["{py}", "-m", "coverage", "--version"], "ruff": ["ruff", "--version"],
    "flake8": ["flake8", "--version"], "black": ["black", "--version"], "shellcheck": ["shellcheck", "--version"],
    "swiftc": ["swiftc", "--version"], "swift": ["swift", "--version"], "xcodebuild": ["xcodebuild", "-version"],
    "mutmut": ["mutmut", "--version"], "vulture": ["vulture", "--version"], "qlty": ["qlty", "--version"],
    "lint-imports": ["lint-imports", "--version"], "git": ["git", "--version"],
}
_NODE_TOOLS = ("tsc", "eslint", "vitest", "js-tests", "knip", "stryker", "playwright", "npm")


class SelectionError(ValueError):
    """选择输入非法（未知 check id、缺 tier…）→ exit 2。"""


# --------------------------------------------------------------------------- #
# 选择（ASK 的答案只在这里落盘；提问由 SKILL.md 指导 AI 完成）
# --------------------------------------------------------------------------- #

def _split_csv(text):
    return [part.strip() for part in text.split(",") if part.strip()] if text else []


def _selection_from_args(args, det):
    if args.tier is None:
        raise SelectionError("either --selection FILE or --tier N is required")
    ids = _split_csv(args.checks) or checks.default_checks(det, args.tier)
    skip = set(_split_csv(args.skip))
    chosen_by = {"user": "user"}.get(args.chosen_by, "recommended, not confirmed")
    rec = det["recommendation"]
    return {"tier": args.tier, "checks": [i for i in ids if i not in skip],
            "ask": {"recommended": rec["tier"], "reason": rec["reason"], "chosen": args.tier, "chosen_by": chosen_by},
            "declared_files": list(args.declared or [])}


def _validate_selection(sel):
    if not isinstance(sel.get("checks"), list) or sel.get("tier") is None:
        raise SelectionError("selection needs `tier` and a `checks` list")
    unknown = [c for c in sel["checks"] if c not in checks.BY_ID]
    if unknown:
        raise SelectionError("unknown check id(s): %s — see detect menu" % ", ".join(unknown))


def build_selection(args, det):
    """→ selection dict（schema：tier / checks / ask / declared_files / 可选旋钮）。"""
    sel = lc.read_json(args.selection) if args.selection else _selection_from_args(args, det)
    _validate_selection(sel)
    sel.setdefault("ask", {"recommended": det["recommendation"]["tier"], "reason": det["recommendation"]["reason"],
                           "chosen": sel["tier"], "chosen_by": "recommended, not confirmed"})
    return sel


def timeout_for(entry, tier, sel):
    """第 5 档全部无时限；否则按层自己的 tier（触发器加挂层按选定 tier）；selection 可整体覆盖。"""
    if sel.get("timeout_seconds"):
        return float(sel["timeout_seconds"])
    if tier >= 5:
        return None
    own = entry["tier"] if entry["tier"] is not None else tier
    return checks.TIER_TIMEOUTS.get(own)


# --------------------------------------------------------------------------- #
# 执行
# --------------------------------------------------------------------------- #

def _result(cid, plan, status, summary="", details=None, **extra):
    entry = checks.BY_ID[cid]
    out = {"id": cid, "tier": entry["tier"], "trigger": entry["trigger"], "label": entry["label"],
           "status": status, "tool": plan.get("tool"), "command": checks.preview(plan), "summary": summary,
           "details": details or {}, "reason": plan.get("reason") or plan.get("note"),
           "rc": None, "duration_s": 0.0, "timed_out": False, "log": None}
    out.update(extra)
    return out


def _run_internal(cid, plan, ctx):
    started = time.monotonic()
    try:
        res = plan["fn"](ctx)
    except Exception as exc:  # 自制检查器自己崩 = 该层失败，绝不是通过（fail closed）
        res = {"status": "fail", "summary": "check crashed — fail closed: %s: %s" % (type(exc).__name__, exc),
               "details": {}}
    return _result(cid, plan, res["status"], res["summary"], res.get("details"),
                   duration_s=round(time.monotonic() - started, 2))


def _write_log(ctx, cid, plan, runs):
    if not ctx["out"]:
        return None
    chunks = []
    for step, run in zip(plan["steps"], runs):
        chunks.append("$ %s\n[rc=%s timed_out=%s %.1fs]\n%s\n--- stderr ---\n%s\n"
                      % (" ".join(step["argv"]), run.rc, run.timed_out, run.duration, run.stdout, run.stderr))
    path = os.path.join(ctx["out"], "logs", cid + ".log")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("".join(chunks))
    return os.path.relpath(path, ctx["out"])


def _fail_reason(last, done, total):
    if last.rc == -2:
        return "could not start: %s" % last.stderr.strip()[:200]
    return "exit code %d (step %d/%d)" % (last.rc, done, total)


def _status_from_runs(plan, runs, timeout):
    last, done, total = runs[-1], len(runs), len(plan["steps"])
    if last.timed_out:
        return "fail", "timed out after %ss (step %d/%d)" % (timeout, done, total)
    if last.rc != 0:
        return "fail", _fail_reason(last, done, total)
    if plan["kind"] == "substituted":
        return "substituted", plan.get("note") or "substitute ran"
    return "pass", "%d step(s) exit 0" % done


def _merge_post(result, extra, substituted):
    for key in ("status", "summary", "details"):
        if key in extra:
            result[key] = extra[key]
    if substituted and result["status"] == "pass":
        result["status"] = "substituted"
    return result


def _apply_post(result, plan, ctx, runs):
    post = plan.get("post")
    if not post or runs[-1].timed_out:
        return result
    try:
        extra = post(ctx, plan, runs) or {}
    except Exception as exc:  # 解读输出的代码崩了也不许放行
        extra = {"status": "fail", "summary": "post-processing crashed — fail closed: %s: %s"
                 % (type(exc).__name__, exc)}
    return _merge_post(result, extra, plan["kind"] == "substituted")


def _run_steps(cid, plan, ctx, runner, timeout):
    runs, started = [], time.monotonic()
    for step in plan["steps"]:
        res = runner(step["argv"], cwd=step.get("cwd"), timeout=timeout, env=step.get("env"))
        runs.append(res)
        if not res.ok:
            break
    status, summary = _status_from_runs(plan, runs, timeout)
    result = _result(cid, plan, status, summary, rc=runs[-1].rc, timed_out=runs[-1].timed_out,
                     duration_s=round(time.monotonic() - started, 2), log=_write_log(ctx, cid, plan, runs),
                     steps_run=len(runs))
    return _apply_post(result, plan, ctx, runs)


def execute(cid, plan, ctx, runner, timeout):
    """一层的执行出口：na/unavailable 原样记；missing = fail；internal 调函数；其余跑命令。"""
    kind = plan["kind"]
    if kind in ("na", "unavailable"):
        return _result(cid, plan, kind, summary=plan["reason"])
    if kind == "missing":
        return _result(cid, plan, "fail", summary=plan["reason"])
    if kind == "internal":
        return _run_internal(cid, plan, ctx)
    return _run_steps(cid, plan, ctx, runner, timeout)


def _run_parallel(ids, plans, ctx, runner, timeouts, jobs):
    results = {}
    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        futures = {pool.submit(execute, cid, plans[cid], ctx, runner, timeouts[cid]): cid for cid in ids}
        for fut in as_completed(futures):
            results[futures[fut]] = fut.result()
    return results


def run_all(plans, ctx, runner, timeouts, jobs):
    """phase 1（静态/自制，可并行）→ phase 2（测试/覆盖/变异，串行）→ phase 3（依赖 coverage.json）。"""
    phase_of = {cid: checks.BY_ID[cid]["phase"] for cid in plans}
    results = _run_parallel([c for c in plans if phase_of[c] == 1], plans, ctx, runner, timeouts, jobs)
    for phase in (2, 3):
        for cid in [c for c in plans if phase_of[c] == phase]:
            results[cid] = execute(cid, plans[cid], ctx, runner, timeouts[cid])
    return [results[cid] for cid in plans]


# --------------------------------------------------------------------------- #
# 报告组装
# --------------------------------------------------------------------------- #

def _fix_tests(results):
    return [{"rank": 1, "kind": "failing test", "item": t, "check": r["id"]}
            for r in results if r["tool"] in _TEST_TOOLS for t in r["details"].get("new", [])]


def _fix_crap(results, changed):
    crap = [r for r in results if r["id"] == "crap"]
    keys = crap[0]["details"].get("new", []) if crap else []
    return [{"rank": 2, "kind": "changed-file CRAP offender", "item": k, "check": "crap"}
            for k in keys if k.split("::")[0] in changed]


def _fix_mutants(results, constitution):
    items = []
    for r in results:
        for s in r["details"].get("survivors", []):
            rank = 3 if s.get("module") in constitution else 4
            items.append({"rank": rank, "kind": "surviving mutant", "item": "%s %s" % (s.get("location"), s.get("op")),
                          "check": r["id"]})
    return items


def _fix_ledger(results):
    items = []
    for r in results:
        if r["id"] == "crap" or r["tool"] in _TEST_TOOLS:
            continue
        for key in ("new", "worse", "uncovered", "outside", "removed_keys"):
            items += [{"rank": 5, "kind": "%s (%s)" % (r["id"], key), "item": v, "check": r["id"]}
                      for v in r["details"].get(key, [])]
    return items


def _fix_other_failures(results, already):
    return [{"rank": 6, "kind": "failing layer", "item": "%s: %s" % (r["id"], r["summary"]), "check": r["id"]}
            for r in results if r["status"] == "fail" and r["id"] not in already]


def fix_first(results, det):
    """先修什么：失败测试 > 改动文件的 CRAP > 宪法模块存活变异体 > 其余存活体 > 账本新违例 > 其他红层。"""
    changed = set(det["diff"]["changed_files"])
    constitution = set(det.get("mutation_targets") or [])
    items = _fix_tests(results) + _fix_crap(results, changed) + _fix_mutants(results, constitution) + _fix_ledger(results)
    items += _fix_other_failures(results, {i["check"] for i in items})
    return sorted(items, key=lambda i: (i["rank"], i["check"], str(i["item"])))


def surviving_mutants(results):
    out = []
    for r in results:
        out += [{"file": s.get("module"), "line": s.get("line"), "op": s.get("op"), "location": s.get("location"),
                 "detail": s.get("detail")} for s in r["details"].get("survivors", [])]
    return out


def not_run(results):
    split = {"na": [], "unavailable": [], "substituted": []}
    for r in results:
        if r["status"] in split:
            split[r["status"]].append({"id": r["id"], "reason": r["reason"] or r["summary"]})
    return split


def baseline_note(results):
    pre = sorted({t for r in results for t in r["details"].get("pre_existing", [])})
    ledgers = {r["id"]: r["details"]["total"] for r in results
               if r["details"].get("total") and r["status"] == "pass" and r["details"].get("new") == []}
    return {"pre_existing_failing_tests": pre, "ledger_pre_existing": ledgers,
            "rule": "zero NEW: pre-existing failures are listed verbatim and never fixed silently"}


def verdict(results, unexplained_core_skips=0):
    """红 > 不完整 > 绿。核心圈层被跳过又没写理由 = 不完整（AI 只能多做，不能悄悄少做）。"""
    statuses = {r["status"] for r in results}
    if "fail" in statuses:
        return "red", EXIT_RED
    if statuses & {"unavailable", "substituted"} or unexplained_core_skips:
        return "incomplete", EXIT_INCOMPLETE
    return "green", EXIT_GREEN


def core_skips(det, sel):
    """→ [{id, reason}]；reason 来自 selection.skip_reasons，缺 = None（进 verdict 的不完整计数）。"""
    reasons = sel.get("skip_reasons") or {}
    return [{"id": cid, "reason": reasons.get(cid)}
            for cid in checks.core_skipped(det, sel["tier"], sel["checks"])]


def blind_spots(results):
    """各层 details.blind_spots 汇总（信息项，如「没有反馈回路」）。"""
    return [s for r in results for s in r["details"].get("blind_spots", [])]


def source_state(runner, repo):
    sha = lc.git_lines(runner, repo, ["rev-parse", "HEAD"]) or ["unknown"]
    status = lc.git_lines(runner, repo, ["status", "--porcelain"])
    return {"commit": sha[0], "dirty": bool(status), "dirty_files": len(status or [])}


def _version_argv(tool, py):
    if tool in _NODE_TOOLS:
        return ["node", "--version"]
    argv = _VERSION_ARGV.get(tool)
    return [a.replace("{py}", py) for a in argv] if argv else None


def _first_line(res):
    """版本串：第一行含数字的行（shellcheck 把版本放第二行）。"""
    text = res.text().strip()
    if not res.ok or not text:
        return "unknown"
    lines = text.splitlines()
    return next((line for line in lines if any(ch.isdigit() for ch in line)), lines[0])


def tool_versions(results, runner, py):
    versions = {"test-code": lc.SKILL_VERSION}
    for tool in sorted({r["tool"] for r in results if r["status"] not in ("na", "unavailable")}):
        argv = _version_argv(tool, py)
        if argv:
            versions[tool] = _first_line(runner(argv, timeout=30))
    return versions


def gitignore_hint(repo):
    text = lc.read_text_or_empty(os.path.join(repo, ".gitignore"))
    if ".test-code" in text:
        return None
    return "add `.test-code/reports/` to .gitignore (baselines under .test-code/baselines/ are meant to be committed)"


def assemble(ctx, det, sel, results, runner):
    """report.json 的唯一出生点（schemaVersion 1，字段 add-only——每日循环消费它）。"""
    skipped = core_skips(det, sel)
    state, exit_code = verdict(results, sum(1 for s in skipped if not s["reason"]))
    rerun = "%s %s --repo %s --selection %s" % (
        ctx["py"], os.path.join(ctx["skill_scripts"], "run_ladder.py"), ctx["repo"],
        os.path.join(ctx["out"], "selection.json"))
    return {
        "schemaVersion": lc.REPORT_SCHEMA, "skill": {"name": lc.SKILL_NAME, "version": lc.SKILL_VERSION},
        "generated_at": lc.utc_iso(), "repo": ctx["repo"], "source_state": source_state(runner, ctx["repo"]),
        "base": det["diff"]["base"], "tier": sel["tier"], "ask": sel["ask"], "thresholds": det["thresholds"],
        "stacks": det["stacks"], "triggers": det["triggers"], "changed_files": det["diff"]["changed_files"],
        "init_baselines": bool(ctx.get("init_baselines")), "checks": results, "not_run": not_run(results),
        "core_skipped": skipped, "blind_spots": blind_spots(results),
        "baseline_note": baseline_note(results), "fix_first": fix_first(results, det),
        "surviving_mutants": surviving_mutants(results), "tool_versions": tool_versions(results, runner, ctx["py"]),
        "rerun": rerun, "verdict": state, "exit_code": exit_code, "out_dir": ctx["out"],
        "notes": [n for n in (gitignore_hint(ctx["repo"]),) if n],
    }


# --------------------------------------------------------------------------- #
# report.md
# --------------------------------------------------------------------------- #

def _md_header(rep):
    ask = rep["ask"]
    state = rep["source_state"]
    thr = rep["thresholds"]
    return [
        "# test-code report — %s @ %s%s" % (os.path.basename(rep["repo"]), state["commit"][:10],
                                             " (dirty: %d files)" % state["dirty_files"] if state["dirty"] else ""),
        "",
        "- Generated %s · skill %s v%s · **tier %s/5** (%s; recommended %s/5: %s)" % (
            rep["generated_at"], rep["skill"]["name"], rep["skill"]["version"], rep["tier"], ask.get("chosen_by"),
            ask.get("recommended"), ask.get("reason")),
        "- Base `%s` · %d changed file(s) · triggers fired: %s" % (
            rep["base"], len(rep["changed_files"]), ", ".join(t["id"] for t in rep["triggers"]) or "none"),
        "- Thresholds from `%s`: complexity ≤ %s · CRAP ≤ %s · coverage %s (%s)" % (
            thr["source"], thr["complexity_max"], thr["crap_max"], thr["coverage"], thr["note"]),
        "- **Verdict: %s** (exit %d)%s" % (rep["verdict"].upper(), rep["exit_code"],
                                            " · baselines initialised this run" if rep["init_baselines"] else ""),
        "",
    ]


def _md_layers(rep):
    lines = ["## Layers", "", "| check | tier | status | time | summary |", "|---|---|---|---|---|"]
    for r in rep["checks"]:
        tier = "%d/5" % r["tier"] if r["tier"] is not None else "trigger:%s" % r["trigger"]
        lines.append("| `%s` | %s | **%s** | %.0fs | %s |" % (
            r["id"], tier, r["status"].upper(), r["duration_s"], (r["summary"] or "").replace("|", "\\|")))
    return lines + [""]


def _md_not_run(rep):
    nr = rep["not_run"]
    titles = (("na", "N-A (project has no such surface)"),
              ("unavailable", "UNAVAILABLE (tool/input missing — nothing ran)"),
              ("substituted", "SUBSTITUTED (something else ran — never written as pass)"))
    lines = ["## Layers not run as specified", ""]
    for key, title in titles:
        lines.append("- **%s:** %s" % (title, "; ".join("`%s` — %s" % (i["id"], i["reason"]) for i in nr[key]) or "none"))
    return lines + [""]


_MD_CAP = 40


def _md_list(title, items, fmt, empty="none"):
    """列表段；超过 _MD_CAP 行只印前段 + 「and N more (report.json)」——JSON 永远全量。"""
    lines = ["## %s" % title, ""]
    lines += [fmt(i) for i in items[:_MD_CAP]] or ["- %s" % empty]
    if len(items) > _MD_CAP:
        lines.append("- … and %d more — full list in report.json" % (len(items) - _MD_CAP))
    return lines + [""]


def _md_baseline(rep):
    note = rep["baseline_note"]
    lines = ["## Baseline note (%s)" % note["rule"], ""]
    lines += ["- pre-existing failing test: `%s`" % t for t in note["pre_existing_failing_tests"]]
    lines += ["- `%s`: %d pre-existing finding(s) on ledger" % (k, v) for k, v in sorted(note["ledger_pre_existing"].items())]
    return lines + (["- none"] if len(lines) == 2 else []) + [""]


def _md_core_skipped(rep):
    return _md_list("Core checks skipped (core = must run at this tier; a skip needs a written reason)",
                    rep.get("core_skipped", []),
                    lambda s: "- `%s` — %s" % (s["id"], s["reason"] or "**no reason given → verdict INCOMPLETE**"),
                    empty="none — every applicable core check ran")


def _md_tail(rep):
    lines = _md_core_skipped(rep)
    lines += _md_list("Structural blind spots (what this ladder cannot see here)", rep.get("blind_spots", []),
                      lambda s: "- %s" % s, empty="none recorded")
    lines += _md_list("Triggers fired", rep["triggers"],
                      lambda t: "- `%s` (%d hit(s)): %s" % (t["id"], t["hits"], "; ".join(t["evidence"][:3])))
    lines += _md_list("Tool versions", sorted(rep["tool_versions"].items()), lambda kv: "- %s: %s" % kv)
    lines += ["## Rerun", "", "```bash", rep["rerun"], "```", ""]
    lines += _md_list("Notes", rep["notes"], lambda n: "- %s" % n)
    return lines


def render_md(rep):
    lines = _md_header(rep) + _md_layers(rep) + _md_not_run(rep)
    lines += _md_list("Fix first (rank 1 failing tests → 2 changed-file CRAP → 3 constitution mutants → "
                      "4 other mutants → 5 new ledger violations → 6 other red layers)", rep["fix_first"],
                      lambda i: "- r%d [%s] %s (`%s`)" % (i["rank"], i["kind"], i["item"], i["check"]),
                      empty="nothing to fix in the layers that ran")
    lines += _md_baseline(rep)
    lines += _md_list("Surviving mutants", rep["surviving_mutants"],
                      lambda s: "- `%s` %s — %s" % (s["location"], s["op"], s["detail"]))
    return "\n".join(lines + _md_tail(rep)) + "\n"


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #

def _parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", default=os.getcwd())
    parser.add_argument("--base")
    parser.add_argument("--detect", metavar="FILE", help="reuse detect.py JSON instead of re-detecting")
    parser.add_argument("--selection", metavar="FILE")
    parser.add_argument("--tier", type=int, choices=(1, 2, 3, 4, 5), help="第 1–5 档（5 = 通宵/通几天，无时限）")
    parser.add_argument("--checks", help="comma-separated check ids (default: the tier's set + fired triggers)")
    parser.add_argument("--skip", help="comma-separated check ids to drop")
    parser.add_argument("--chosen-by", choices=("user", "headless"), default="headless")
    parser.add_argument("--declared", nargs="*", metavar="GLOB", help="declared changed-file set (diff minimality)")
    parser.add_argument("--out", metavar="DIR")
    parser.add_argument("--init-baselines", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--jobs", type=int, default=min(4, os.cpu_count() or 1))
    return parser.parse_args(argv)


def _load_detection(args, repo, runner):
    if args.detect:
        return lc.read_json(args.detect)
    return detect_mod.detect(repo, args.base, runner=runner)


def _dry_run(plans, sel):
    rows = [{"id": cid, "kind": plan["kind"], "reason": plan.get("reason") or plan.get("note"),
             "command": checks.preview(plan)} for cid, plan in plans.items()]
    print(json.dumps({"tier": sel["tier"], "ask": sel["ask"], "plan": rows}, indent=1, ensure_ascii=False))
    return EXIT_GREEN


def run(repo, det, sel, out, runner=lc.run_command, jobs=1, init_baselines=False, dry_run=False):
    """库入口（测试也走这里）：返回 report dict；dry_run 只打印计划。"""
    ctx = checks.make_ctx(repo, det, sel, out, init_baselines=init_baselines)
    plans = checks.build_plans(ctx, sel["checks"])
    if dry_run:
        return {"exit_code": _dry_run(plans, sel), "dry_run": True}
    os.makedirs(out, exist_ok=True)
    lc.write_json(os.path.join(out, "selection.json"), sel)
    lc.write_json(os.path.join(out, "detect.json"), det)
    timeouts = {cid: timeout_for(checks.BY_ID[cid], sel["tier"], sel) for cid in plans}
    results = run_all(plans, ctx, runner, timeouts, jobs)
    report = assemble(ctx, det, sel, results, runner)
    lc.write_json(os.path.join(out, "report.json"), report)
    with open(os.path.join(out, "report.md"), "w", encoding="utf-8") as fh:
        fh.write(render_md(report))
    return report


def _print_summary(report):
    for r in report["checks"]:
        print("%-12s %-20s %s" % (r["status"].upper(), r["id"], r["summary"]))
    print("verdict: %s (exit %d) — report: %s/report.md" % (report["verdict"], report["exit_code"], report["out_dir"]))


def main(argv=None):
    args = _parse_args(argv)
    repo = os.path.abspath(args.repo)
    if not os.path.isdir(repo):
        print("run_ladder: not a directory: %s" % repo, file=sys.stderr)
        return EXIT_USAGE
    try:
        det = _load_detection(args, repo, lc.run_command)
        sel = build_selection(args, det)
    except (SelectionError, ValueError, OSError) as exc:
        print("run_ladder: %s" % exc, file=sys.stderr)
        return EXIT_USAGE
    out = args.out or os.path.join(repo, ".test-code", "reports", lc.utc_stamp())
    report = run(repo, det, sel, out, jobs=args.jobs, init_baselines=args.init_baselines, dry_run=args.dry_run)
    if not report.get("dry_run"):
        _print_summary(report)
    return report["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
