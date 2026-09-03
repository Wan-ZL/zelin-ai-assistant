#!/usr/bin/env python3
"""test-ui skill · 梯子 runner：选择 → 执行（phase 1 并行静态 / 2 串行起 app 采集 / 3 依赖 bundle 的
配对与 diff）→ report.md + report.json；退出码 fail-closed。ASK 由 AI 在 SKILL.md 里完成，这里只记录。

法典指针：docs/CONTRACT.md §58（阈值只读、账本只缩）、§UI-parity（parity 契约：skill 写不进项目树——
`--propose-pending` / `--propose-goldens` 只落 <report>/proposed/ 并打印拷贝命令；项目门与 skill
判官对共有 id 不一致 = FAIL `parity_disagreement`，永不取平均）。设计 = vnext2-plan R2.8 / D14。

用法：
  run_ui.py [--repo PATH] [--base REF] [--detect FILE] [--against REF]
            (--selection FILE | --tier N [--checks a,b] [--skip a,b] [--screens a,b] [--chosen-by user|headless])
            [--out DIR] [--propose-pending] [--propose-goldens] [--dry-run] [--jobs N]
退出码：0 绿；1 红（任一 fail）；3 不完整（unavailable / substituted，或核心层跳过无理由）；2 用法错误。
判例：tests/test_skill_test_ui_run_ui.py（fake runner；负控制 = fixture 对里的每个植入缺陷都排进 fix-first）。
"""

import argparse
import json
import os
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import checks_ui as checks  # noqa: E402
import detect_ui  # noqa: E402
import ladder_common_vendored as lc  # noqa: E402
import parity  # noqa: E402
import reference as refmod  # noqa: E402
import testui_common as tc  # noqa: E402

EXIT_GREEN, EXIT_RED, EXIT_USAGE, EXIT_INCOMPLETE = 0, 1, 2, 3
BLIND_SPOTS = [
    "design quality / hierarchy / taste — opinion section only, never a status",
    "assistive-technology output — the tree is measured, the announcement is not heard",
    "behavioural correctness of controls — test-code's e2e layer, not this skill",
    "native macOS runtime (AX tree, AppKit computed styles) — frozen source only under D3",
    "motion, easing, scroll physics, haptics, sound — clocks frozen and animations disabled by design",
    "undeclared interaction states and undeclared feature flags",
    "interaction-revealed UI (dialog buttons, expanded card details, open menus) — the runtime tree is the REST state; a project probe that clicks through (this repo's parity_check vitest) sees more, and its verdicts stand on those ids",
    "real-data layouts — never shot; the static-name filter hides dynamic text on purpose",
    "cross-machine rendering — goldens are machine-bound, fingerprint recorded",
    "color perception beyond contrast arithmetic",
    "translation quality beyond zh/en pair presence",
    "performance unless Lighthouse is present",
    "no feedback channel — a single-run skill (P5's job)",
]
_VERSION_ARGV = {"node": ["node", "--version"], "npx": ["node", "--version"], "playwright": ["node", "--version"],
                 "odiff": ["odiff", "--version"], "git": ["git", "--version"], "demo-seed": ["{py}", "--version"],
                 "parity_check": ["{py}", "--version"], "internal": None}


class SelectionError(ValueError):
    """选择输入非法 → exit 2。"""


# --------------------------------------------------------------------------- #
# 选择
# --------------------------------------------------------------------------- #

def _split_csv(text):
    return [part.strip() for part in text.split(",") if part.strip()] if text else []


def _first_nonempty(*candidates):
    for value in candidates:
        if value:
            return value
    return []


def _selection_from_args(args, det):
    if args.tier is None:
        raise SelectionError("either --selection FILE or --tier N is required")
    ids = _first_nonempty(_split_csv(args.checks), checks.default_checks(det, args.tier))
    skip = set(_split_csv(args.skip))
    rec = det["recommendation"]
    return {"tier": args.tier, "against": det.get("against"), "checks": [i for i in ids if i not in skip],
            "screens": _first_nonempty(_split_csv(args.screens), rec.get("screens")),
            "ask": {"recommended": rec["tier"], "reason": rec["reason"], "chosen": args.tier,
                    "chosen_by": "user" if args.chosen_by == "user" else "recommended, not confirmed"},
            "skip_reasons": {}, "triggers_waived": {}, "waivers_acknowledged": [], "reruns": 3, "timeout_seconds": None}


def build_selection(args, det):
    sel = lc.read_json(args.selection) if args.selection else _selection_from_args(args, det)
    if not isinstance(sel.get("checks"), list) or sel.get("tier") is None:
        raise SelectionError("selection needs `tier` and a `checks` list")
    unknown = [c for c in sel["checks"] if c not in checks.BY_ID]
    if unknown:
        raise SelectionError("unknown check id(s): %s — see detect menu" % ", ".join(unknown))
    sel.setdefault("ask", {"recommended": det["recommendation"]["tier"], "reason": det["recommendation"]["reason"],
                           "chosen": sel["tier"], "chosen_by": "recommended, not confirmed"})
    sel.setdefault("against", det.get("against"))
    return sel


def timeout_for(entry, tier, sel):
    if sel.get("timeout_seconds"):
        return float(sel["timeout_seconds"])
    if tier >= 5:
        return None
    return checks.TIER_TIMEOUTS.get(entry["tier"] if entry["tier"] is not None else tier)


# --------------------------------------------------------------------------- #
# 执行（与 test-code 同语义：na/unavailable 原样、missing = fail、崩 = fail、超时 = fail 不许翻案）
# --------------------------------------------------------------------------- #

def _result(cid, plan, status, summary="", details=None, **extra):
    entry = checks.BY_ID[cid]
    out = {"id": cid, "tier": entry["tier"], "trigger": entry["trigger"], "label": entry["label"], "sensor": checks.sensor_of(cid),
           "status": status, "tool": plan.get("tool"), "command": checks.preview(plan), "summary": summary,
           "details": details or {}, "reason": plan.get("reason") or plan.get("note"), "rc": None, "duration_s": 0.0,
           "timed_out": False, "log": None}
    out.update(extra)
    return out


def _run_internal(cid, plan, ctx):
    started = time.monotonic()
    try:
        res = plan["fn"](ctx)
    except Exception as exc:  # 自制检查器崩 = 该层失败（fail closed）
        res = {"status": "fail", "summary": "check crashed — fail closed: %s: %s" % (type(exc).__name__, exc), "details": {}}
    return _result(cid, plan, res["status"], res["summary"], res.get("details"), duration_s=round(time.monotonic() - started, 2))


def _write_log(ctx, cid, plan, runs):
    if not ctx["out"]:
        return None
    path = os.path.join(ctx["out"], "logs", cid + ".log")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for step, run in zip(plan["steps"], runs):
            fh.write("$ %s\n[rc=%s timed_out=%s %.1fs]\n%s\n--- stderr ---\n%s\n"
                     % (" ".join(step["argv"]), run.rc, run.timed_out, run.duration, run.stdout, run.stderr))
    return os.path.relpath(path, ctx["out"])


def _status_from_runs(plan, runs, timeout):
    last, done, total = runs[-1], len(runs), len(plan["steps"])
    if last.timed_out:
        return "fail", "timed out after %ss (step %d/%d)" % (timeout, done, total)
    if last.rc == -2:
        return "fail", "could not start: %s" % last.stderr.strip()[:200]
    if last.rc != 0:
        return "fail", "exit code %d (step %d/%d)" % (last.rc, done, total)
    if plan["kind"] == "substituted":
        return "substituted", plan.get("note") or "substitute ran"
    return "pass", "%d step(s) exit 0" % done


def _run_post(post, ctx, plan, runs):
    """post hook 的返回（崩了 = fail closed 的 extra）。"""
    try:
        extra = post(ctx, plan, runs)
    except Exception as exc:
        return {"status": "fail", "summary": "post-processing crashed — fail closed: %s: %s" % (type(exc).__name__, exc)}
    return extra if extra else {}


def _merge_post(result, extra, substituted):
    for key in ("status", "summary", "details"):
        if key in extra:
            result[key] = extra[key]
    if substituted and result["status"] == "pass":
        result["status"] = "substituted"  # 替代物永不写 pass，post hook 也翻不了
    return result


def _apply_post(result, plan, ctx, runs):
    post = plan.get("post")
    if not post or runs[-1].timed_out:
        return result
    return _merge_post(result, _run_post(post, ctx, plan, runs), plan["kind"] == "substituted")


def _run_steps(cid, plan, ctx, runner, timeout):
    runs, started = [], time.monotonic()
    for step in plan["steps"]:
        res = runner(step["argv"], cwd=step.get("cwd"), timeout=timeout, env=step.get("env"))
        runs.append(res)
        if not res.ok:
            break
    status, summary = _status_from_runs(plan, runs, timeout)
    result = _result(cid, plan, status, summary, rc=runs[-1].rc, timed_out=runs[-1].timed_out,
                     duration_s=round(time.monotonic() - started, 2), log=_write_log(ctx, cid, plan, runs), steps_run=len(runs))
    return _apply_post(result, plan, ctx, runs)


def execute(cid, plan, ctx, runner, timeout):
    kind = plan["kind"]
    if kind in ("na", "unavailable"):
        return _result(cid, plan, kind, summary=plan["reason"])
    if kind == "missing":
        return _result(cid, plan, "fail", summary=plan["reason"])
    if kind == "internal":
        return _run_internal(cid, plan, ctx)
    return _run_steps(cid, plan, ctx, runner, timeout)


def run_all(plans, ctx, runner, timeouts, jobs):
    """phase 1 并行（静态 / 自制）→ phase 2 串行（起 app 采集）→ phase 3（依赖 bundle）。"""
    phase_of = {cid: checks.BY_ID[cid]["phase"] for cid in plans}
    results = {}
    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        futures = {pool.submit(execute, cid, plans[cid], ctx, runner, timeouts[cid]): cid
                   for cid in plans if phase_of[cid] == 1}
        for fut in as_completed(futures):
            results[futures[fut]] = fut.result()
    for phase in (2, 3):
        for cid in [c for c in plans if phase_of[c] == phase]:
            results[cid] = execute(cid, plans[cid], ctx, runner, timeouts[cid])
    return [results[cid] for cid in plans]


# --------------------------------------------------------------------------- #
# parity_disagreement（项目门 vs skill 判官，共有 id）
# --------------------------------------------------------------------------- #

_PROJECT_STATUS = {"MISSING": "MISSING", "PENDING": "MISSING", "PRESENT": "PRESENT", "STALE": "PRESENT"}


def _pair_result(ctx):
    runtime = ctx["state"].get("pair_runtime")
    return runtime if runtime else ctx["state"].get("pair_source")


def _skill_verdicts(rows):
    return {r["id"]: r["status"] for r in rows if r["status"] in ("MISSING", "PRESENT")}


def _project_verdicts(project):
    items = project.get("items")
    return {k: _PROJECT_STATUS[v] for k, v in (items if items else {}).items() if v in _PROJECT_STATUS}


def parity_disagreement(ctx):
    """→ 伪层结果或 None：project_parity 的 items{id: status} 与 skill rows 在共有 id 上对不上 = fail。"""
    project, pair = ctx["state"].get("project_parity"), _pair_result(ctx)
    if not project or not pair:
        return None
    mine, theirs = _skill_verdicts(pair["rows"]), _project_verdicts(project)
    shared = sorted(set(mine) & set(theirs))
    conflicts = [{"id": i, "project": theirs[i], "skill": mine[i]} for i in shared if mine[i] != theirs[i]]
    mode = "runtime rest-state tree" if ctx["state"].get("pair_runtime") else "source extraction"
    summary = "%d disagreement(s) on %d shared id(s) — skill (%s) vs project gate (click-through vitest); both lists kept, never averaged" % (
        len(conflicts), len(shared), mode)
    return {"id": "parity_disagreement", "tier": None, "trigger": None, "label": "项目门 vs skill 判官一致性", "sensor": "structure",
            "status": "fail" if conflicts else "pass", "tool": "internal", "command": "internal:parity_disagreement",
            "summary": summary, "details": {"conflicts": conflicts[:100], "shared": len(shared)}, "reason": None, "rc": None,
            "duration_s": 0.0, "timed_out": False, "log": None}


# --------------------------------------------------------------------------- #
# 报告组装
# --------------------------------------------------------------------------- #

def _rows(ctx):
    pair = _pair_result(ctx)
    return pair["rows"] if pair else []


def _changed_screens(sel):
    return set(sel.get("screens") or [])


def _is_hot(row):
    """rank 1 候选：MISSING 的交互项，或 CHANGED topology。"""
    topo = any(f.startswith("topology") for f in row["fields_changed"])
    return topo or (row["kind"] == "interactive" and row["status"] == "MISSING")


def _on_changed_screen(row, screens):
    return not screens or row["screen"] in screens or tc.screen_family(row["screen"]) in screens


def _needs_fix(row):
    return row["status"] in ("MISSING", "CHANGED") and row["ledger"] != "pending"


def _fix_items(rows, screens, project_items=None):
    """rank 1：改动屏上 MISSING 的交互项 + CHANGED topology；rank 5：其它屏。项目门（点遍按钮的 vitest）判 PRESENT 的
    id 不进 rank 1——rest 态树看不见对话框里的按钮，那是 parity_disagreement 的材料，不是先修项。"""
    project = project_items if project_items else {}
    return [_fix_item(row, screens, project.get(row["id"]) in ("PRESENT", "STALE")) for row in rows if _needs_fix(row)]


def _fix_item(row, screens, gate_present):
    rank = 1 if _is_hot(row) and _on_changed_screen(row, screens) and not gate_present else 5
    fields = ",".join(row["fields_changed"])
    kind = "%s %s" % (row["status"], fields if fields else row["kind"])
    return {"rank": rank, "kind": kind + (" — project gate says PRESENT (interaction-revealed?)" if gate_present else ""),
            "item": row["id"], "check": "pair_structure"}


_TOKEN_FIX_SOURCES = {"theme_default_declared": ("row", "location"), "theme_default_observed": ("row", "location"),
                      "geometry_runtime": ("rows", "location"), "pair_tokens": ("rows", "location"),
                      "tokens_runtime": ("drift", "var")}


def _fix_rows(result, key):
    rows = result["details"].get(key)
    if isinstance(rows, dict):
        return [rows]
    return [r for r in (rows if rows else []) if r.get("status") not in ("PRESENT", "N-A", "UNAVAILABLE")]


def _fix_tokens(results, screens):
    """rank 2：theme:default → 几何 → tokens（顺序由 CATALOG 顺序保证，rank 内按 check id 排）。"""
    out = []
    for result in [r for r in results if r["status"] == "fail" and r["id"] in _TOKEN_FIX_SOURCES]:
        key, field = _TOKEN_FIX_SOURCES[result["id"]]
        for row in _fix_rows(result, key):
            out.append({"rank": 2, "kind": "CHANGED %s" % result["id"], "item": row.get(field, "?"), "check": result["id"]})
    return out


def _rule_item_text(hit):
    theme = ", %s" % hit["theme"] if hit.get("theme") else ""
    return "%s (%s < %s%s)" % (hit["id"], hit["measured"], hit["threshold"], theme)


def _fix_rules(results):
    out = []
    for r in results:
        for hit in r["details"].get("hits") or []:
            if hit.get("status") == "hit" and hit.get("severity") in ("critical", "serious"):
                out.append({"rank": 3, "kind": "rule %s" % hit["rule_id"], "item": _rule_item_text(hit), "check": r["id"]})
    return out


def _fix_visual(results):
    out = []
    for r in results:
        if r["id"] != "visual_diff":
            continue
        for row in r["details"].get("rows") or []:
            if row.get("item_status") == "CHANGED":
                out.append({"rank": 4, "kind": "visual %.2f%% > %.2f%%" % (100 * row.get("changed_pct", 0), 100 * row.get("threshold", 0)),
                            "item": row["id"], "check": "visual_diff"})
    return out


def _detail_list(result, key):
    value = result["details"].get(key)
    return value if value else []


def _fix_ledger(results):
    out = []
    for r in results:
        out += [{"rank": 6, "kind": "ledger %s" % p["kind"], "item": p["line"], "check": r["id"]} for p in _detail_list(r, "problems")]
        if r["status"] != "fail":
            continue
        if r["id"] == "golden_manifest":
            out += [{"rank": 6, "kind": "unreviewed golden", "item": g, "check": r["id"]}
                    for g in _detail_list(r, "unreviewed") + _detail_list(r, "reasonless")]
        if r["id"] == "thresholds_unmoved":
            out += [{"rank": 6, "kind": "threshold_raised", "item": k, "check": r["id"]} for k in _detail_list(r, "loosened")]
    return out


def fix_first(results, ctx, sel):
    """1 MISSING 交互项 / CHANGED topology（改动屏）→ 2 theme:default、几何、字号、颜色 → 3 WCAG serious →
    4 视觉超阈 → 5 其它屏的 MISSING/CHANGED/规则 → 6 账本噪音 → 7 其它红层。"""
    screens = _changed_screens(sel)
    project = (ctx["state"].get("project_parity") or {}).get("items")
    items = _fix_items(_rows(ctx), screens, project) + _fix_tokens(results, screens) + _fix_rules(results) + _fix_visual(results) + _fix_ledger(results)
    covered = {i["check"] for i in items}
    items += [{"rank": 7, "kind": "failing layer", "item": "%s: %s" % (r["id"], r["summary"]), "check": r["id"]}
              for r in results if r["status"] == "fail" and r["id"] not in covered]
    return sorted(items, key=lambda i: (i["rank"], i["check"], str(i["item"])))


def not_run(results):
    split = {"na": [], "unavailable": [], "substituted": []}
    for r in results:
        if r["status"] in split:
            split[r["status"]].append({"id": r["id"], "reason": r["reason"] or r["summary"]})
    return split


def verdict(results, unexplained_core_skips=0):
    statuses = {r["status"] for r in results}
    if "fail" in statuses:
        return "red", EXIT_RED
    if statuses & {"unavailable", "substituted"} or unexplained_core_skips:
        return "incomplete", EXIT_INCOMPLETE
    return "green", EXIT_GREEN


def core_skips(det, sel):
    reasons = sel.get("skip_reasons") or {}
    return [{"id": cid, "reason": reasons.get(cid)} for cid in checks.core_skipped(det, sel["tier"], sel["checks"])]


def _side_mode(det, role, sensor):
    side = (det.get("sides") if det.get("sides") else {}).get(role)
    mode = (side if side else {}).get("mode")
    return (mode if mode else {}).get(sensor)


def sensors_table(det, results):
    ran = {r["sensor"] for r in results if r["status"] not in ("na", "unavailable")}
    return [{"sensor": s, "subject_mode": _side_mode(det, "subject", s), "reference_mode": _side_mode(det, "reference", s),
             "ran": s in ran} for s in ("structure", "tokens", "visual")]


def _items_summary(ctx):
    rows = _rows(ctx)
    return {"rows": [r for r in rows if r["status"] != "PRESENT"], "counts": parity._counts(rows), "total": len(rows),
            "pending": sum(1 for r in rows if r["ledger"] == "pending"),
            "extras": ((ctx["state"].get("pair_runtime") or ctx["state"].get("pair_source") or {}).get("extras") or [])}


def _rules_summary(results):
    return [dict(h, check=r["id"]) for r in results for h in r["details"].get("hits") or []]


def _visual_summary(results):
    return [row for r in results if r["id"] == "visual_diff" for row in r["details"].get("rows") or []]


def _version_line(runner, argv, py):
    res = runner([a.replace("{py}", py) for a in argv], timeout=30)
    lines = res.text().strip().splitlines()
    return lines[0] if res.ok and lines else "unknown"


def tool_versions(results, runner, py):
    versions = {"test-ui": tc.SKILL_VERSION}
    used = {r["tool"] for r in results if r["status"] not in ("na", "unavailable") and r["tool"]}
    for tool in sorted(used):
        argv = _VERSION_ARGV.get(tool)
        if argv:
            versions[tool] = _version_line(runner, argv, py)
    return versions


def _opinion(results):
    for r in results:
        if r["id"] == "opinion" and r["status"] == "pass":
            return parity.apply_opinion({}, r["details"].get("opinion"))["opinion"]
    return None


def assemble(ctx, det, sel, results, runner):
    skipped = core_skips(det, sel)
    state, exit_code = verdict(results, sum(1 for s in skipped if not s["reason"]))
    rerun = "%s %s --repo %s --selection %s" % (ctx["py"], os.path.join(ctx["skill_scripts"], "run_ui.py"), ctx["repo"],
                                                os.path.join(ctx["out"], "selection.json"))
    launch = ctx["state"].get("launch") or {}
    return {"schemaVersion": tc.SCHEMA_VERSION, "skill": {"name": tc.SKILL_NAME, "version": tc.SKILL_VERSION},
            "generated_at": lc.utc_iso(), "repo": ctx["repo"],
            "source_state": {"commit": (det["sides"]["subject"].get("commit") or "unknown"), "dirty": bool(det["sides"]["subject"].get("dirty"))},
            "base": det["diff"]["base"], "tier": sel["tier"], "ask": sel["ask"], "against": sel.get("against"),
            "reference": det["sides"]["reference"], "subject": det["sides"]["subject"], "demo_marker_seen": launch.get("marker_seen"),
            "thresholds": det["thresholds"], "sensors": sensors_table(det, results), "triggers": det["triggers"],
            "changed_files": det["diff"]["changed_files"], "screens": sel.get("screens") or [], "checks": results,
            "items": _items_summary(ctx), "rules": _rules_summary(results), "visual": _visual_summary(results),
            "not_run": not_run(results), "core_skipped": skipped, "blind_spots": list(BLIND_SPOTS),
            "fix_first": fix_first(results, ctx, sel), "ledger_note": _ledger_note(det),
            "tool_versions": tool_versions(results, runner, ctx["py"]), "rerun": rerun, "verdict": state, "exit_code": exit_code,
            "out_dir": ctx["out"], "notes": _notes(det, ctx), "opinion": _opinion(results)}


def _ledger_note(det):
    parsed = (det.get("ledgers") or {}).get("parsed") or {}
    return {"dir": parsed.get("dir"), "pending": len(parsed.get("pending") or {}), "waivers": len(parsed.get("waivers") or {}),
            "aliases": len(parsed.get("aliases") or {}), "rule": "ledgers live in the project and only shrink; the skill writes nothing"}


def _notes(det, ctx):
    notes = []
    if ".test-ui" not in lc.read_text_or_empty(os.path.join(ctx["repo"], ".gitignore")):
        notes.append("add `.test-ui/reports/` and `.test-ui/cache/` to .gitignore")
    if det.get("runtime_hint"):
        notes.append(det["runtime_hint"])
    if det.get("config_source"):
        notes.append("config: %s" % det["config_source"])
    return notes


# --------------------------------------------------------------------------- #
# report.md
# --------------------------------------------------------------------------- #

_MD_CAP = 40


def _md_list(title, items, fmt, empty="none"):
    lines = ["## %s" % title, ""] + ([fmt(i) for i in items[:_MD_CAP]] or ["- %s" % empty])
    if len(items) > _MD_CAP:
        lines.append("- … and %d more — full list in report.json" % (len(items) - _MD_CAP))
    return lines + [""]


def _md_header(rep):
    ask, ref, thr = rep["ask"], rep["reference"], rep["thresholds"]
    return ["# test-ui report — %s @ %s%s" % (os.path.basename(rep["repo"]), rep["source_state"]["commit"][:10],
                                              " (dirty)" if rep["source_state"]["dirty"] else ""), "",
            "- Generated %s · skill test-ui v%s · **tier %s/5** (%s; recommended %s/5: %s)" % (
                rep["generated_at"], rep["skill"]["version"], rep["tier"], ask.get("chosen_by"), ask.get("recommended"), ask.get("reason")),
            "- **Reference** `%s` (%s) resolved %s · modes %s" % (ref.get("locator"), ref.get("kind"), ref.get("resolved"), json.dumps(ref.get("mode"))),
            "- Base `%s` · %d changed file(s) · screens %s · triggers fired: %s · demo marker seen: %s" % (
                rep["base"], len(rep["changed_files"]), ", ".join(rep["screens"]) or "all", ", ".join(t["id"] for t in rep["triggers"]) or "none", rep["demo_marker_seen"]),
            "- Thresholds from `%s`: visual ≤ %s%% · contrast ≥ %s · target ≥ %spx (%s)" % (
                thr.get("source"), 100 * float(thr.get("max_changed_pct", 0)), thr.get("contrast_text"), thr.get("target_min_px"), thr.get("note")),
            "- **Verdict: %s** (exit %d)" % (rep["verdict"].upper(), rep["exit_code"]), ""]


def _md_sensors(rep):
    lines = ["## Sensors", "", "| sensor | subject mode | reference mode | ran |", "|---|---|---|---|"]
    lines += ["| %s | %s | %s | %s |" % (s["sensor"].upper(), s["subject_mode"], s["reference_mode"], "yes" if s["ran"] else "no") for s in rep["sensors"]]
    return lines + [""]


def _md_layers(rep):
    lines = ["## Layers", "", "| check | sensor | tier | status | time | summary |", "|---|---|---|---|---|---|"]
    for r in rep["checks"]:
        tier = "%d/5" % r["tier"] if r["tier"] is not None else "trigger"
        lines.append("| `%s` | %s | %s | **%s** | %.0fs | %s |" % (r["id"], r["sensor"], tier, r["status"].upper(), r["duration_s"], (r["summary"] or "").replace("|", "\\|")))
    return lines + [""]


_ITEMS_CAP = 80
_KIND_ORDER = {"theme": 0, "rail": 1, "layout": 2, "lane": 3, "lanes": 3, "screen": 4, "setting": 5, "shortcut": 6, "control": 7}


def _item_sort_key(row):
    """结构类 id（theme / rail / layout / lane / screen）先于 control；同类里 NEW（不在账上）先于挂账；CHANGED 先于 MISSING。"""
    kind = row["id"].split(":", 1)[0]
    return (_KIND_ORDER.get(kind, 9), 0 if row["ledger"] != "pending" else 1, 0 if row["status"] == "CHANGED" else 1, row["id"])


def _md_kind_table(rows):
    counts = {}
    for row in rows:
        kind = row["id"].split(":", 1)[0]
        counts.setdefault(kind, {})[row["status"]] = counts.setdefault(kind, {}).get(row["status"], 0) + 1
    lines = ["| kind | MISSING | CHANGED | WAIVED | of which pending |", "|---|---|---|---|---|"]
    for kind in sorted(counts, key=lambda k: (_KIND_ORDER.get(k, 9), k)):
        c = counts[kind]
        pending = sum(1 for r in rows if r["id"].split(":", 1)[0] == kind and r["ledger"] == "pending")
        lines.append("| %s | %d | %d | %d | %d |" % (kind, c.get("MISSING", 0), c.get("CHANGED", 0), c.get("WAIVED", 0), pending))
    return lines + [""]


def _md_items(rep):
    items = rep["items"]
    head = ["## Items (%s · %d pending · %d extras)" % (json.dumps(items["counts"]), items["pending"], len(items["extras"])), ""]
    rows = sorted([r for r in items["rows"] if r["status"] != "N-A"], key=_item_sort_key)
    body = ["- %s `%s` %s%s" % (r["status"], r["id"], ",".join(r["fields_changed"]), " [%s]" % r["ledger"] if r["ledger"] else "") for r in rows[:_ITEMS_CAP]]
    tail = ["- … and %d more — full list in report.json" % (len(rows) - _ITEMS_CAP)] if len(rows) > _ITEMS_CAP else []
    return head + (_md_kind_table(rows) if rows else []) + (body or ["- every reference item PRESENT"]) + tail + [""]


def _md_not_run(rep):
    nr = rep["not_run"]
    titles = (("na", "N-A (project has no such surface)"), ("unavailable", "UNAVAILABLE (tool/input missing — nothing ran)"),
              ("substituted", "SUBSTITUTED (something else ran — never written as pass)"))
    return ["## Layers not run as specified", ""] + ["- **%s:** %s" % (t, "; ".join("`%s` — %s" % (i["id"], i["reason"]) for i in nr[k]) or "none") for k, t in titles] + [""]


def _md_tail(rep):
    lines = _md_list("Core checks skipped (a skip needs a written reason)", rep["core_skipped"],
                     lambda s: "- `%s` — %s" % (s["id"], s["reason"] or "**no reason given → verdict INCOMPLETE**"), empty="none — every applicable core check ran")
    lines += _md_list("Structural blind spots (never filled, never red)", rep["blind_spots"], lambda s: "- %s" % s)
    lines += ["## Ledger note", "", "- %s · pending %d · waivers %d · aliases %d (%s)" % (
        rep["ledger_note"]["dir"], rep["ledger_note"]["pending"], rep["ledger_note"]["waivers"], rep["ledger_note"]["aliases"], rep["ledger_note"]["rule"]), ""]
    lines += _md_list("Triggers fired", rep["triggers"], lambda t: "- `%s` (%d hit(s)): %s" % (t["id"], t["hits"], "; ".join(t["evidence"][:3])))
    lines += _md_list("Tool versions", sorted(rep["tool_versions"].items()), lambda kv: "- %s: %s" % kv)
    lines += ["## Rerun", "", "```bash", rep["rerun"], "```", ""]
    lines += _md_list("Notes", rep["notes"], lambda n: "- %s" % n)
    if rep.get("opinion"):
        lines += ["## Opinion (not a measurement)", "", rep["opinion"]["banner"], "", rep["opinion"]["text"], ""]
    return lines


def render_md(rep):
    lines = _md_header(rep) + _md_sensors(rep) + _md_layers(rep) + _md_items(rep)
    severity_rank = {"critical": 0, "serious": 1, "moderate": 2, "minor": 3}
    hits = sorted([h for h in rep["rules"] if h.get("status") == "hit"], key=lambda h: (severity_rank.get(h.get("severity"), 9), h.get("rule_id", ""), str(h.get("id"))))
    lines += _md_list("Rules (hits)", hits,  # serious first, then moderate / minor
                      lambda h: "- %s `%s` %s vs %s (%s%s)" % (h["rule_id"], h["id"], h["measured"], h["threshold"], h["severity"], ", %s" % h["theme"] if h.get("theme") else ""))
    lines += _md_list("Visual", rep["visual"], lambda v: "- `%s` %s %.3f%% (threshold %.2f%%, masked %.1f%%)" % (
        v["id"], v.get("item_status") or v.get("status"), 100 * v.get("changed_pct", 0), 100 * v.get("threshold", 0), 100 * v.get("masked_ratio", 0)))
    lines += _md_not_run(rep)
    lines += _md_list("Fix first (1 MISSING interactive / CHANGED topology on changed screens → 2 theme, geometry, type, color → "
                      "3 WCAG serious → 4 visual over threshold → 5 elsewhere → 6 ledger noise → 7 other reds)", rep["fix_first"],
                      lambda i: "- r%d [%s] %s (`%s`)" % (i["rank"], i["kind"], i["item"], i["check"]), empty="nothing to fix in the layers that ran")
    return "\n".join(lines + _md_tail(rep)) + "\n"


# --------------------------------------------------------------------------- #
# 提案（只落 <report>/proposed/，永不写项目树）
# --------------------------------------------------------------------------- #

def propose_pending(ctx, det):
    rows = [r for r in _rows(ctx) if r["status"] == "MISSING" and r["ledger"] != "pending"]
    path = os.path.join(ctx["out"], "proposed", "pending.txt")
    tc.write_text(path, "# proposed additions to ui/parity/pending.txt — review, then append by hand (shrink-only ledger)\n"
                  + "".join("%s\n" % r["id"] for r in rows))
    return {"path": path, "count": len(rows), "copy": "cat %s >> %s" % (path, os.path.join((det.get("ledgers") or {}).get("dir") or "ui/parity", "pending.txt"))}


def _runtime_shots(ctx):
    bundle = ctx["state"].get("runtime")
    inventory = (bundle if bundle else {}).get("inventory")
    shots = (inventory if inventory else {}).get("shots")
    return shots if shots else []


def _goldens_meta(det):
    goldens = det.get("goldens")
    goldens = goldens if goldens else {}
    return goldens.get("machine_key", "unknown-machine"), goldens.get("dir", "ui/parity/goldens")


def _manifest_entry(shot):
    return {"sha256": tc.sha256_file(shot["path"]), "tool": "test-ui driver", "seed": "demo", "blessed_at": None,
            "reason": "", "diff_pct_at_bless": None}


def propose_goldens(ctx, det):
    shots = _runtime_shots(ctx)
    key, goldens_dir = _goldens_meta(det)
    target = os.path.join(ctx["out"], "proposed", "goldens", key)
    os.makedirs(target, exist_ok=True)
    entries = {}
    for shot in shots:
        shutil.copy2(shot["path"], os.path.join(target, os.path.basename(shot["path"])))
        entries[os.path.basename(shot["path"])] = _manifest_entry(shot)
    tc.write_text(os.path.join(target, "manifest.json"), tc.dump_json({"machine": key, "entries": entries}))
    return {"path": target, "count": len(shots), "copy": "cp -R %s %s/" % (target, goldens_dir if goldens_dir else "ui/parity/goldens")}


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #

def _parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", default=os.getcwd())
    parser.add_argument("--base")
    parser.add_argument("--detect", metavar="FILE")
    parser.add_argument("--against")
    parser.add_argument("--selection", metavar="FILE")
    parser.add_argument("--tier", type=int, choices=(1, 2, 3, 4, 5))
    parser.add_argument("--checks")
    parser.add_argument("--skip")
    parser.add_argument("--screens")
    parser.add_argument("--chosen-by", choices=("user", "headless"), default="headless")
    parser.add_argument("--out", metavar="DIR")
    parser.add_argument("--propose-pending", action="store_true")
    parser.add_argument("--propose-goldens", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--jobs", type=int, default=min(4, os.cpu_count() or 1))
    return parser.parse_args(argv)


def _dry_run(plans, sel):
    rows = [{"id": cid, "kind": plan["kind"], "reason": plan.get("reason") or plan.get("note"), "command": checks.preview(plan)}
            for cid, plan in plans.items()]
    print(json.dumps({"tier": sel["tier"], "against": sel.get("against"), "ask": sel["ask"], "plan": rows}, indent=1, ensure_ascii=False))
    return EXIT_GREEN


def run(repo, det, sel, out, runner=lc.run_command, jobs=1, dry_run=False, proposals=(), **ctx_extra):
    """库入口（测试也走这里）：返回 report dict；dry_run 只打印计划。"""
    ctx = checks.make_ctx(repo, det, sel, out, runner=runner, **ctx_extra)
    plans = checks.build_plans(ctx, sel["checks"])
    if dry_run:
        return {"exit_code": _dry_run(plans, sel), "dry_run": True}
    os.makedirs(out, exist_ok=True)
    lc.write_json(os.path.join(out, "selection.json"), sel)
    lc.write_json(os.path.join(out, "detect.json"), det)
    timeouts = {cid: timeout_for(checks.BY_ID[cid], sel["tier"], sel) for cid in plans}
    results = run_all(plans, ctx, runner, timeouts, jobs)
    extra = parity_disagreement(ctx)
    if extra:
        results.append(extra)
    report = assemble(ctx, det, sel, results, runner)
    report["proposals"] = {name: {"pending": propose_pending, "goldens": propose_goldens}[name](ctx, det) for name in proposals}
    _cleanup_reference(repo, det, runner)
    lc.write_json(os.path.join(out, "report.json"), report)
    with open(os.path.join(out, "report.md"), "w", encoding="utf-8") as fh:
        fh.write(render_md(report))
    return report


def _cleanup_reference(repo, det, runner):
    """跑完拆掉参照的 detached worktree；空的 cache 目录一并收走（留下的只有 .test-ui/reports/）。"""
    ref = (det.get("sides") if det.get("sides") else {}).get("reference")
    if ref and ref.get("worktree") and refmod.remove_git_side(repo, ref, runner):
        try:
            os.rmdir(os.path.dirname(ref["worktree"]))
        except OSError:
            pass


def _print_summary(report):
    for r in report["checks"]:
        print("%-12s %-24s %s" % (r["status"].upper(), r["id"], r["summary"]))
    for name, prop in (report.get("proposals") or {}).items():
        print("proposed %s: %d → %s   (%s)" % (name, prop["count"], prop["path"], prop["copy"]))
    print("verdict: %s (exit %d) — report: %s/report.md" % (report["verdict"], report["exit_code"], report["out_dir"]))


def main(argv=None):
    args = _parse_args(argv)
    repo = os.path.abspath(args.repo)
    if not os.path.isdir(repo):
        print("run_ui: not a directory: %s" % repo, file=sys.stderr)
        return EXIT_USAGE
    try:
        det = _load_detection(args, repo)
        sel = build_selection(args, det)
    except (SelectionError, refmod.ReferenceError, ValueError, OSError) as exc:
        print("run_ui: %s" % exc, file=sys.stderr)
        return EXIT_USAGE
    out = args.out if args.out else os.path.join(repo, ".test-ui", "reports", lc.utc_stamp())
    report = run(repo, det, sel, out, jobs=args.jobs, dry_run=args.dry_run, proposals=_proposals(args))
    if not report.get("dry_run"):
        _print_summary(report)
    return report["exit_code"]


def _load_detection(args, repo):
    """--detect 复用探测结果；但 --against 与其中记录的参照不同时重新探测（参照是 CLI 输入，不是猜的）。"""
    det = lc.read_json(args.detect) if args.detect else None
    if det is None or (args.against and det.get("against") != args.against):
        det = detect_ui.detect(repo, args.base, args.against)
    return det


def _proposals(args):
    return [n for n, flag in (("pending", args.propose_pending), ("goldens", args.propose_goldens)) if flag]


if __name__ == "__main__":
    sys.exit(main())
