#!/usr/bin/env python3
"""test-ui skill · 检查目录（CATALOG）+ 计划构造（builders）。每个 check = 一行目录 + builder(ctx) → plan：
  {"kind": "cmd", "steps": [{"argv", "cwd", "env"?}], "tool", "post"?}   命令（顺序、首败即停）
  {"kind": "substituted", …同上…, "note"}                                替代物跑了，永不写 pass
  {"kind": "internal", "fn": callable}                                    纯 Python 检查（sensors.py）
  {"kind": "na", "reason"} / {"kind": "unavailable", "reason"}            项目无此面 / 工具缺席
  {"kind": "missing", "reason"}                                           触发器点名却无证据 → fail

三圈：core（该档必跑；AI 只能多做不能少做）/ extended（菜单可见、默认不勾）/ 表列（references/catalog.md）。
项目仪器优先：scripts/ui/parity_check.py → `project_parity`、web/e2e/visual.spec.ts → `project_visual`，
在场逐字调用；skill 自己的 pair/visual 也跑，共有 id 判决不一致 = run_ui 记 `parity_disagreement`。

法典指针：docs/CONTRACT.md §UI-parity（parity 契约仪器）、§58（阈值只读）。设计 = vnext2-plan R2.8 / D14；
层菜单 = references/tiers.md，触发器 = references/triggers.md，扩展圈 = references/catalog.md。
判例：tests/test_skill_test_ui_checks.py。
"""

import os
import shlex
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sensors  # noqa: E402
import testui_common as tc  # noqa: E402

TIER_TIMEOUTS = {1: 300, 2: 1800, 3: 3600, 4: 7200, 5: None}
SKILL_SCRIPTS = os.path.dirname(os.path.abspath(__file__))
TRIGGER_CHECKS = {
    "screen_changed": ["structure_source", "pair_structure", "topology_runtime"],
    "tokens_changed": ["tokens_source", "pair_tokens", "theme_default_declared", "geometry_runtime", "visual_diff"],
    "theme_changed": ["theme_default_declared", "theme_default_observed"],
    "layout_changed": ["topology_runtime", "geometry_runtime", "reflow"],
    "a11y_attr_changed": ["a11y_rules", "focus_order", "keyboard_reach"],
    "names_changed": ["i18n_parity"],
    "ledger_changed": ["ledger_lint", "golden_manifest", "thresholds_unmoved"],
    "demo_changed": ["seed_probe", "screens_capture"],
    "always": ["seed_guard", "ledger_lint"],
}
SENSOR_OF = {"structure": ("surface_detect", "structure_source", "pair_structure", "a11y_static", "structure_runtime",
                           "topology_runtime", "pair_runtime", "a11y_rules", "keyboard_reach", "focus_order", "i18n_parity",
                           "inventory_stability", "states_matrix", "project_parity"),
             "tokens": ("tokens_source", "pair_tokens", "theme_default_declared", "off_token_literals", "contrast_pairs",
                        "tokens_runtime", "geometry_runtime", "theme_default_observed", "dead_tokens", "token_census_floor"),
             "visual": ("screens_capture", "visual_diff", "matrix_themes_viewports", "reflow", "visual_stability",
                        "cross_engine", "golden_manifest", "project_visual", "golden_review_sheet")}


def make_ctx(repo, det, sel=None, out=None, py=None, runner=None, **extra):
    """builders / internal checks 共用上下文（唯一出生点；字段 add-only）。"""
    ctx = {"repo": repo, "det": det, "sel": sel or {}, "out": out, "skill_scripts": SKILL_SCRIPTS,
           "py": py or sys.executable, "state": {}, "runner": runner}
    ctx.update(extra)
    return ctx


# --------------------------------------------------------------------------- #
# plan 形状
# --------------------------------------------------------------------------- #

def _step(argv, cwd, env=None):
    return {"argv": argv, "cwd": cwd, "env": env}


def _cmd(argv, cwd, tool=None, post=None, kind="cmd", note=None):
    return {"kind": kind, "steps": [_step(argv, cwd)], "tool": tool or argv[0], "post": post, "note": note}


def _internal(fn):
    return {"kind": "internal", "fn": fn, "tool": "internal", "steps": []}


def _na(reason):
    return {"kind": "na", "reason": reason, "steps": []}


def _unavailable(reason):
    return {"kind": "unavailable", "reason": reason, "steps": []}


def preview(plan):
    if plan["kind"] == "internal":
        return "internal:%s" % plan["fn"].__name__
    return " && ".join(shlex.join(step["argv"]) for step in plan.get("steps", []))


# --------------------------------------------------------------------------- #
# ctx 访问器
# --------------------------------------------------------------------------- #

def _det(ctx, *keys):
    node = ctx["det"]
    for key in keys:
        node = (node or {}).get(key)
    return node


def _adapter(ctx, name):
    return (_det(ctx, "adapters") or {}).get(name)


def _runtime_ok(ctx):
    return ((_det(ctx, "sides", "subject") or {}).get("mode") or {}).get("structure") == "runtime"


def _has_surface(ctx):
    return bool(_det(ctx, "surfaces"))


def _needs_surface(ctx, fn):
    return _internal(fn) if _has_surface(ctx) else _na("no UI surface")


def _needs_runtime(ctx, fn):
    if not _has_surface(ctx):
        return _na("no UI surface")
    if not _runtime_ok(ctx):
        return _unavailable(_det(ctx, "runtime_hint") or "runtime instruments missing — node + project playwright + launch recipe")
    return _internal(fn)


# --------------------------------------------------------------------------- #
# builders
# --------------------------------------------------------------------------- #

def fill_recipe(argv, py, home, scene):
    """seed / server 配方里的占位符：{py} {home} {scene}。"""
    return [a.replace("{py}", py).replace("{home}", home).replace("{scene}", scene) for a in argv]


def _launch(ctx):
    side = _det(ctx, "sides", "subject")
    launch = (side if side else {}).get("launch")
    return launch if launch else {}


def _out_dir(ctx):
    return ctx["out"] if ctx["out"] else "."


def _b_seed_probe(ctx):
    seed = _launch(ctx).get("seed")
    if not seed:
        return _na("no demo seed recipe (config.launch.seed) — nothing to probe")
    argv = fill_recipe(seed, ctx["py"], os.path.join(_out_dir(ctx), "seed-probe"), "initial")
    check = [a for a in argv if a != "initial" and not a.startswith("--")] + ["--check"]
    return {"kind": "cmd", "steps": [_step(argv, ctx["repo"]), _step(check, ctx["repo"])], "tool": "demo-seed", "post": None,
            "note": None}


def _b_project_parity(ctx):
    script = _adapter(ctx, "parity_check")
    if not script:
        return _na("no project parity gate (scripts/ui/parity_check.py) — skill pairing is the only instrument")
    if not _det(ctx, "tools", "npx"):
        return _unavailable("scripts/ui/parity_check.py needs npx (vitest) on PATH")
    # 三个输出全部落到 <report>/project_parity/：--report DIR 只写判决文本，report.json / report.md 的默认路径是
    # 项目树里的 ui/parity/*（门自己的产物）——skill 只读项目树，永不重写它。
    out = _project_parity_dir(ctx)
    return _cmd([ctx["py"], script, "--check", "--report", out, "--report-json", os.path.join(out, "report.json"),
                 "--report-md", os.path.join(out, "report.md")], ctx["repo"], tool="parity_check", post=_post_project_parity)


def _project_parity_dir(ctx):
    return os.path.join(ctx["out"] or ".", "project_parity")


def _post_project_parity(ctx, plan, runs):
    """项目门的 report.json → items{id: PRESENT|PENDING|MISSING|STALE|WAIVED}，供 run_ui 比对 parity_disagreement。"""
    import json
    path = os.path.join(_project_parity_dir(ctx), "report.json")
    if not os.path.exists(path):
        return {"status": "fail", "summary": "parity_check produced no report.json — fail closed"}
    with open(path, "r", encoding="utf-8") as fh:
        report = json.load(fh)
    ctx["state"]["project_parity"] = report
    return {"summary": "project gate: %s" % json.dumps(report.get("counts") or report.get("summary") or {})[:200],
            "details": {"report": path, "counts": report.get("counts")}}


def _b_project_visual(ctx):
    spec = _adapter(ctx, "visual_spec")
    if not spec:
        return _na("no project visual baseline (web/e2e/visual.spec.ts)")
    pkg = _det(ctx, "web_dir")
    if not _det(ctx, "tools", "playwright_bin"):
        return _unavailable("web/e2e/visual.spec.ts present but @playwright/test not installed in %s (cd web && npm i -D @playwright/test && npx playwright install chromium)" % pkg)
    return _cmd(["npx", "--no-install", "playwright", "test", "e2e/visual.spec.ts"], os.path.join(ctx["repo"], pkg), tool="playwright")


def _b_opinion(ctx):
    return _internal(sensors.check_opinion)


def _b_table_only(what):
    def build(ctx):
        return _unavailable("%s — table-only in test-ui %s (references/catalog.md); run by hand, paste into Notes" % (what, tc.SKILL_VERSION))
    return build


def _entry(cid, tier, phase, est, label, build, trigger=None, circle="core"):
    return {"id": cid, "tier": tier, "phase": phase, "est": est, "label": label, "build": build, "trigger": trigger,
            "circle": circle}


def _surface(fn):
    return lambda ctx: _needs_surface(ctx, fn)


def _runtime(fn):
    return lambda ctx: _needs_runtime(ctx, fn)


CATALOG = [
    _entry("surface_detect", 1, 1, 2, "UI 面 + 参照解析", _surface(sensors.check_surface_detect)),
    _entry("seed_probe", 1, 1, 10, "demo seed --check", _b_seed_probe),
    _entry("structure_source", 1, 1, 20, "源清单（roles/names/topology）", _surface(sensors.check_structure_source)),
    _entry("tokens_source", 1, 1, 10, "源 tokens（CSS 变量 / design-tokens / typeScale）", _surface(sensors.check_tokens_source)),
    _entry("ledger_lint", 1, 1, 2, "账本卫生 + 只许缩", _surface(sensors.check_ledger_lint)),
    _entry("golden_manifest", 1, 1, 2, "golden 台账（sha + reason，绑机器）", _surface(sensors.check_golden_manifest)),
    _entry("thresholds_unmoved", 1, 1, 2, "阈值/遮罩不放宽（vs merge-base）", _surface(sensors.check_thresholds_unmoved)),
    _entry("pair_structure", 1, 3, 10, "配对 STRUCTURE（源 vs 参照）", _surface(sensors.check_pair_structure)),
    _entry("pair_tokens", 1, 3, 5, "配对 TOKENS（逐主题逐路径）", _surface(sensors.check_pair_tokens)),
    _entry("theme_default_declared", 1, 3, 2, "默认主题（声明）", _surface(sensors.check_theme_default_declared)),
    _entry("off_token_literals", 1, 3, 5, "绕过 token 的字面量普查", _surface(sensors.check_off_token_literals)),
    _entry("contrast_pairs", 1, 3, 2, "声明色对 WCAG 对比度", _surface(sensors.check_contrast_pairs)),
    _entry("a11y_static", 1, 3, 5, "静态 WCAG 子集（名字 / lang / 标题层级）", _surface(sensors.check_a11y_static)),
    _entry("structure_runtime", 2, 2, 240, "起 app → seed → driver 采集（runtime 清单 + 截图）", _runtime(sensors.check_structure_runtime)),
    _entry("app_launch", 2, 3, 2, "启动 / ready / marker 记录", _runtime(sensors.check_app_launch)),
    _entry("seed_guard", 2, 3, 2, "runtime 产物必带 skill 自己的 seed", _surface(sensors.check_seed_guard)),
    _entry("pair_runtime", 2, 3, 10, "配对 STRUCTURE（runtime vs 参照）", _runtime(sensors.check_pair_runtime)),
    _entry("topology_runtime", 2, 3, 5, "TOPOLOGY：地标 side / parent / order", _surface(sensors.check_topology_runtime)),
    _entry("tokens_runtime", 2, 3, 5, "computed tokens vs 声明表", _runtime(sensors.check_tokens_runtime)),
    _entry("geometry_runtime", 2, 3, 5, "GEOMETRY：layout.* 声明 vs 渲染 bbox", _surface(sensors.check_geometry_runtime)),
    _entry("theme_default_observed", 2, 3, 5, "默认主题（首帧观察）", _runtime(sensors.check_theme_default_observed)),
    _entry("a11y_rules", 2, 3, 10, "runtime WCAG（对比度 / 目标尺寸 / 键盘）+ axe", _runtime(sensors.check_a11y_rules)),
    _entry("screens_capture", 2, 3, 2, "截图已采（未比对）", _runtime(sensors.check_screens_capture)),
    _entry("visual_diff", 3, 3, 60, "VISUAL：截图 vs golden（感知差）", _runtime(sensors.check_visual_diff)),
    _entry("matrix_themes_viewports", 3, 3, 5, "主题 × 视口 × 语言矩阵", _runtime(sensors.check_matrix_themes_viewports)),
    _entry("keyboard_reach", 3, 3, 5, "Tab 可达", _runtime(sensors.check_keyboard_reach)),
    _entry("focus_order", 3, 3, 5, "焦点顺序无回环", _runtime(sensors.check_focus_order)),
    _entry("reflow", 3, 3, 5, "窄视口无横向溢出", _runtime(sensors.check_reflow)),
    _entry("i18n_parity", 3, 3, 5, "zh/en 双语成对", _surface(sensors.check_i18n_parity)),
    _entry("inventory_stability", 4, 3, 600, "清单稳定性 ×reruns", _runtime(sensors.check_inventory_stability)),
    _entry("visual_stability", 4, 3, 600, "截图稳定性 ×3（flaky 单列）", _runtime(sensors.check_visual_stability)),
    _entry("states_matrix", 4, 3, 300, "hover/focus/active/disabled 状态矩阵", _runtime(sensors.check_states_matrix)),
    _entry("cross_engine", 4, 3, 900, "跨引擎（webkit / firefox）", _runtime(sensors.check_cross_engine)),
    _entry("reference_runtime", 4, 3, 900, "参照也 runtime（git: 临时 worktree）", _runtime(sensors.check_reference_runtime)),
    _entry("matrix_all_routes", 5, 3, None, "全部路由矩阵", _runtime(sensors.check_matrix_all_routes)),
    _entry("all_references", 5, 3, None, "全部参照", _runtime(sensors.check_all_references)),
    _entry("clean_machine_ui", 5, 3, None, "干净机器：clone → build → launch → 清单 == 提交的参照", _runtime(sensors.check_clean_machine_ui)),
    _entry("golden_review_sheet", 5, 3, None, "golden 复核表", _runtime(sensors.check_golden_review_sheet)),
    _entry("project_parity", 1, 1, 120, "项目门 scripts/ui/parity_check.py --check（逐字）", _b_project_parity, circle="extended"),
    _entry("project_visual", 3, 2, 300, "项目视觉基线 web/e2e/visual.spec.ts（逐字）", _b_project_visual, circle="extended"),
    _entry("lighthouse_a11y", 3, 2, 120, "Lighthouse a11y", _b_table_only("lighthouse_a11y"), circle="extended"),
    _entry("reduced_motion", 3, 3, 30, "prefers-reduced-motion", _b_table_only("reduced_motion"), circle="extended"),
    _entry("touch_target_size", 3, 3, 5, "触控目标 44px", _b_table_only("touch_target_size"), circle="extended"),
    _entry("dead_tokens", 2, 3, 5, "声明未消费的 token", _b_table_only("dead_tokens"), circle="extended"),
    _entry("token_census_floor", 2, 3, 5, "token 普查地板", _b_table_only("token_census_floor"), circle="extended"),
    _entry("text_overflow_probe", 3, 3, 60, "长字符串溢出探针（scene long-strings）", _b_table_only("text_overflow_probe"), circle="extended"),
    _entry("font_fallback", 3, 3, 30, "字体回退", _b_table_only("font_fallback"), circle="extended"),
    _entry("rtl_smoke", 3, 3, 60, "RTL 冒烟", _b_table_only("rtl_smoke"), circle="extended"),
    _entry("perf_budget", 5, 2, None, "性能预算", _b_table_only("perf_budget"), circle="extended"),
    _entry("opinion", 1, 3, 1, "设计质量意见（advisory，只写 Opinion 段）", _b_opinion, circle="extended"),
]
BY_ID = {entry["id"]: entry for entry in CATALOG}


def default_checks(det, tier):
    """tier 的默认勾选：核心圈 tier ≤ 选定 + 触发器点名的层（always 恒在）。扩展圈只进菜单。"""
    fired = {t["id"] for t in det.get("triggers") or []} | {"always"}
    wanted = {e["id"] for e in CATALOG if _core_due(e, tier)}
    for trigger in fired:
        wanted.update(TRIGGER_CHECKS.get(trigger, []))
    return [e["id"] for e in CATALOG if e["id"] in wanted]


def _core_due(entry, tier):
    return entry["tier"] is not None and entry["tier"] <= tier and entry["circle"] == "core"


def core_skipped(det, tier, selected):
    """核心圈里本该跑（kind 可跑）却没被选的层 → [id]。"""
    kinds = {row["id"]: row["kind"] for row in det.get("menu") or []}
    chosen = set(selected)
    due = [e["id"] for e in CATALOG if _core_due(e, tier) and e["id"] not in chosen]
    return [cid for cid in due if kinds.get(cid) in ("cmd", "internal", "substituted")]


def build_plans(ctx, ids):
    return {cid: BY_ID[cid]["build"](ctx) for cid in ids}


def sensor_of(cid):
    for sensor, ids in SENSOR_OF.items():
        if cid in ids:
            return sensor
    return "ladder"


def build_menu(ctx):
    menu = []
    for entry in CATALOG:
        plan = entry["build"](ctx)
        menu.append({"id": entry["id"], "tier": entry["tier"], "trigger": entry["trigger"], "circle": entry["circle"],
                     "label": entry["label"], "est_seconds": entry["est"], "kind": plan["kind"], "sensor": sensor_of(entry["id"]),
                     "mode": _mode_for(ctx, entry["id"]), "reason": plan.get("reason") or plan.get("note"),
                     "command": preview(plan)})
    return menu


def _mode_for(ctx, cid):
    side = (_det(ctx, "sides", "subject") or {}).get("mode") or {}
    if cid.endswith("_runtime") or cid in ("visual_diff", "screens_capture", "a11y_rules", "app_launch"):
        return "runtime" if _runtime_ok(ctx) else "unavailable"
    return side.get(sensor_of(cid), "source")
