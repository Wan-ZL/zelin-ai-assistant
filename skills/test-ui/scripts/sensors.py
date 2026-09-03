#!/usr/bin/env python3
"""test-ui skill · 自制检查的实现（internal checks）：每个函数 `check_<id>(ctx)` → {status, summary,
details}。它们调用 inventory_a11y / tokens / visual / parity 三个传感器，把中间产物放进
ctx["state"]（subject 清单、reference 清单、tokens 文档、runtime bundle），供后续 phase 读。

阶段：phase 1（并行）= 源提取、账本卫生、阈值不动；phase 2（串行）= 种 seed → 起 app →
driver 采集（一次采完，后续检查只读 bundle）；phase 3 = 配对、规则、视觉 diff。全部
fail closed：读不到 = fail，抛异常 = run_ui 记 fail。`producer.mode` 决定 substituted：
源清单对着 runtime 参照只能是 substituted；对着冻结源参照（`native`）就是真仪器。

法典指针：docs/CONTRACT.md §UI-parity（parity 契约 = 项目仪器优先，在场逐字调用）、§58（阈值只读）、
§45 无关（清单不铸卡）。设计 = vnext2-plan R2.8 / D14。判例：tests/test_skill_test_ui_checks.py、
tests/test_skill_test_ui_seed_guard.py、tests/test_skill_test_ui_run_ui.py。
"""

import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import inventory_a11y as inv  # noqa: E402
import ladder_common_vendored as lc  # noqa: E402
import parity  # noqa: E402
import reference as refmod  # noqa: E402
import testui_common as tc  # noqa: E402
import tokens as tk  # noqa: E402
import visual  # noqa: E402

LOOSENING = {"max_changed_pct": 1, "pixel_tolerance": 1, "max_mask_ratio": 1, "geometry_tolerance_px": 1,
             "max_off_token_literals": 1, "contrast_text": -1, "contrast_large": -1, "target_min_px": -1}
DEFAULT_CONTRAST_PAIRS = (("color.text-primary", "color.bg"), ("color.text-secondary", "color.bg"),
                          ("color.text-primary", "color.surface"), ("color.on-accent", "color.accent"))


def _res(status, summary, details=None):
    return {"status": status, "summary": summary, "details": details or {}}


def _state(ctx):
    return ctx.setdefault("state", {})


def _det(ctx, *keys, **kw):
    node = ctx["det"]
    for key in keys:
        node = (node if node else {}).get(key)
    return kw.get("default") if node is None else node


def _det_dict(ctx, *keys):
    value = _det(ctx, *keys)
    return value if isinstance(value, dict) else {}


def _det_list(ctx, *keys):
    value = _det(ctx, *keys)
    return value if isinstance(value, list) else []


def _ledgers(ctx):
    parsed = _det(ctx, "ledgers", "parsed")
    return parsed if parsed else parity.load_ledgers(None)


def _waivers(ctx):
    return _ledgers(ctx)["waivers"]


def _rules(ctx):
    return parity.load_rules(_config(ctx).get("rules"))


def _out_path(ctx, *parts):
    path = os.path.join(ctx["out"] or tempfile.gettempdir(), *parts)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


def _thresholds(ctx):
    return dict(parity.DEFAULT_THRESHOLDS, **_det_dict(ctx, "thresholds"))


def _config(ctx):
    return _det_dict(ctx, "config")


def _reference(ctx):
    return _det_dict(ctx, "sides", "reference")


def _subject_side(ctx):
    return _det_dict(ctx, "sides", "subject")


# --------------------------------------------------------------------------- #
# phase 1 —— surface / seed / 源清单 / 源 tokens / 账本 / 阈值
# --------------------------------------------------------------------------- #

def check_surface_detect(ctx):
    surfaces = _det_list(ctx, "surfaces")
    if not surfaces:
        return _res("na", "no UI surface detected (web/*.tsx, *.html, *.swift)", {"surfaces": []})
    ref = _reference(ctx)
    return _res("pass", "surfaces %s · reference %s (%s) · modes %s" % (
        ", ".join(s["kind"] for s in surfaces), ref.get("locator"), ref.get("kind"), ref.get("mode")),
        {"surfaces": surfaces, "reference": ref})


def _extract_surfaces(ctx, roots):
    parts = [inv.extract_source(os.path.join(ctx["repo"], s["root"]), _config(ctx).get("screens"),
                                rel_prefix=s["root"].rstrip("/") + "/") for s in roots]
    merged = parts[0]
    for part in parts[1:]:
        merged = inv.merge_inventories(merged, part)
    merged["lang"] = _det(ctx, "lang")
    merged["side"] = dict(_subject_side(ctx), role="subject")
    return merged


def subject_inventory(ctx):
    """subject 源清单（缓存在 state）；没有 web 面 → None。"""
    state = _state(ctx)
    if "subject_source" in state:
        return state["subject_source"]
    roots = [s for s in _det_list(ctx, "surfaces") if s["kind"] in ("web-react", "static-html")]
    merged = _extract_surfaces(ctx, roots) if roots else None
    state["subject_source"] = merged
    if merged is not None:
        tc.write_text(_out_path(ctx, "inventory", "subject-source.json"), tc.dump_json(merged))
    return merged


def _swift_note(ctx):
    swift = [s for s in _det_list(ctx, "surfaces") if s["kind"] == "swift-source"]
    if swift and not _det(ctx, "adapters", "extract_native_inventory"):
        return "Swift surface (%s) not covered — needs project adapter scripts/ui/extract_native_inventory.py; no Swift heuristics in the skill" % swift[0]["root"]
    return None


def _source_failure(subject):
    if subject.get("errors"):
        return _res("fail", "%d unreadable source file(s) — fail closed" % len(subject["errors"]), {"errors": subject["errors"]})
    if not subject["items"]:
        return _res("fail", "source extractor produced 0 items — fail closed", {})
    return None


def check_structure_source(ctx):
    subject, note = subject_inventory(ctx), _swift_note(ctx)
    if subject is None:
        return _res("unavailable", note) if note else _res("na", "no source surface to extract")
    failure = _source_failure(subject)
    if failure:
        return failure
    summary = "%d item(s) on %d screen(s), %d landmark(s) (mode=source)" % (len(subject["items"]), len(subject["screens"]), len(subject["landmarks"]))
    details = {"roles": _role_counts(subject["items"]), "blind_spots": [note] if note else []}
    return _res("pass", summary + (" · " + note if note else ""), details)


def _role_counts(items):
    counts = {}
    for item in items:
        counts[item["key"]["role"]] = counts.get(item["key"]["role"], 0) + 1
    return counts


def reference_inventory(ctx):
    """reference 清单（缓存）；design-system → None；alias/inventory 文件 → 读 + 归一；dir/git → 源提取。"""
    state = _state(ctx)
    if "reference_inventory" in state:
        return state["reference_inventory"]
    ref = _reference(ctx)
    loaded = _load_reference_inventory(ctx, ref)
    if loaded is not None:
        bad = tc.validate_inventory(loaded)
        if bad:
            raise ValueError("reference_unreadable: %s" % ", ".join(bad[:5]))
        tc.write_text(_out_path(ctx, "inventory", "reference.json"), tc.dump_json(loaded))
    state["reference_inventory"] = loaded
    return loaded


def _runner(ctx):
    runner = ctx.get("runner")
    return runner if runner else lc.run_command


def _frozen_reference(ctx, ref):
    """alias / inventory 文件：parity 契约原始形（有 controls）就归一，否则已是 schema。"""
    if ref.get("inventory"):
        raw = tc.read_json(ref["inventory"])
        return inv.normalize_native(raw, ref["inventory"]) if "controls" in raw else raw
    if ref.get("produced_by"):
        return inv.load_native(ctx["repo"], None, _runner(ctx), _out_path(ctx, "inventory", "x")[:-1])
    return None


def _load_reference_inventory(ctx, ref):
    kind = ref.get("kind")
    if kind in ("alias", "inventory"):
        return _frozen_reference(ctx, ref)
    if kind == "git":
        return inv.extract_tree(refmod.ensure_worktree(ctx["repo"], ref, _runner(ctx)), _config(ctx).get("screens"))
    if kind == "dir":
        return inv.extract_tree(os.path.abspath(ref["locator"]), _config(ctx).get("screens"))
    return None


def _tokens_config(ctx):
    cfg = _config(ctx).get("tokens")
    return cfg if isinstance(cfg, dict) else {}


def _census_dirs(ctx, files):
    only = _tokens_config(ctx).get("only_dirs")
    if only:
        return only
    dirs = files.get("component_dirs")
    return dirs if dirs else []


def check_tokens_source(ctx):
    files = _det_dict(ctx, "tokens_files")
    if not files.get("css"):
        return _res("na", "no CSS custom-property table found (tokens.css / *.css with --vars)")
    doc = tk.extract_css_tokens(ctx["repo"], files["css"], files.get("index_html"), files.get("type_scale"),
                                _census_dirs(ctx, files), _tokens_config(ctx).get("categories"))
    _state(ctx)["subject_tokens"] = doc
    tc.write_text(_out_path(ctx, "tokens", "subject.json"), tc.dump_json(doc))
    return _res("pass", "%s across themes %s · declared default %s" % (
        doc["families"], sorted(doc["themes"]), doc["default_theme"]["declared"]), {"families": doc["families"]})


def _load_reference_tokens(ctx, ref):
    if ref.get("tokens"):
        return tk.load_design_tokens(ref["tokens"])
    kind = ref.get("kind")
    if kind == "design-system":
        return _state(ctx).get("subject_tokens")
    if kind == "git":
        return _dir_tokens(ctx, refmod.ensure_worktree(ctx["repo"], ref, _runner(ctx)))
    if kind == "dir":
        return _dir_tokens(ctx, os.path.abspath(ref["locator"]))
    return None


def reference_tokens(ctx):
    state = _state(ctx)
    if "reference_tokens" in state:
        return state["reference_tokens"]
    doc = _load_reference_tokens(ctx, _reference(ctx))
    state["reference_tokens"] = doc
    if doc:
        tc.write_text(_out_path(ctx, "tokens", "reference.json"), tc.dump_json(doc))
    return doc


def _dir_tokens(ctx, root):
    files = [f for f in lc.walk_files(root) if "/dist/" not in "/" + f]
    found = tk.find_token_files(root, files, [r for _k, r in inv.surface_roots(files)])
    if not found["css"]:
        return None
    return tk.extract_css_tokens(root, found["css"], found["index_html"], found["type_scale"], found["component_dirs"])


def _mode_note(ctx, sensor):
    """源清单对 runtime 参照 = substituted；否则真仪器。返回 (status_if_pass, note)。"""
    ref_mode = _det_dict(ctx, "sides", "reference", "mode").get(sensor)
    if ref_mode == "runtime":
        return "substituted", "source inventory vs a runtime reference — cannot see rendered state (tier 2 pair_runtime is the real instrument)"
    return "pass", None


def check_pair_structure(ctx):
    subject, reference = subject_inventory(ctx), reference_inventory(ctx)
    if reference is None:
        return _res("na", "reference has no inventory (design-system mode) — structure is measured by a11y_static")
    if subject is None:
        return _res("unavailable", "no subject source inventory (see structure_source)")
    result = parity.compare_items(subject, reference, _ledgers(ctx), _thresholds(ctx))
    _state(ctx)["pair_source"] = result
    return _pair_verdict(ctx, result, "structure")


def _pair_details(result):
    rows = result["rows"]
    pending = sum(1 for r in rows if r["ledger"] == "pending")
    return {"rows": [r for r in rows if r["status"] != "PRESENT"], "counts": parity._counts(rows), "extras": result["extras"],
            "suggestions": result["suggestions"], "problems": result["problems"], "pending": pending,
            "red": [r for r in rows if parity._item_red(r)]}


def _pair_verdict(ctx, result, sensor):
    details = _pair_details(result)
    summary = "%s · %d pending · %d extra(s)" % (details["counts"], details["pending"], len(result["extras"]))
    red = details.pop("red")
    if red or result["problems"]:
        return _res("fail", "%d NEW MISSING/CHANGED, %d ledger problem(s) — %s" % (len(red), len(result["problems"]), summary), details)
    status, note = _mode_note(ctx, sensor)
    return _res(status, (note + " · " + summary) if note else summary, details)


def check_pair_tokens(ctx):
    subject, reference = _state(ctx).get("subject_tokens"), reference_tokens(ctx)
    if reference is None or _reference(ctx).get("kind") == "design-system":
        return _res("na", "design-system mode: the project tokens are the reference (see off_token_literals / contrast_pairs)")
    if subject is None:
        return _res("unavailable", "no subject tokens (see tokens_source)")
    rows = parity.compare_tokens(subject, reference, _thresholds(ctx))
    red = [r for r in rows if r["status"] in ("MISSING", "CHANGED")]
    details = {"rows": red, "counts": parity._counts(rows), "compared": len(rows)}
    if red:
        return _res("fail", "%d token(s) MISSING/CHANGED of %d compared" % (len(red), len(rows)), details)
    return _res("pass", "%d token path(s) equal across themes" % len(rows), details)


def check_theme_default_declared(ctx):
    subject, reference = _state(ctx).get("subject_tokens"), reference_tokens(ctx)
    if subject is None:
        return _res("unavailable", "no subject tokens document")
    if reference is None or reference is subject:
        declared = subject["default_theme"]["declared"]
        return _res("pass", "declared default theme: %s (%s)" % (declared.get("fallback"), declared.get("mode")), {"declared": declared})
    row = parity.compare_default_theme(subject, reference)
    _state(ctx)["theme_row"] = row
    if row["status"] == "CHANGED":
        return _res("fail", "theme:default CHANGED %s → %s" % (row["reference"].get("fallback"), row["subject"].get("fallback")), {"row": row})
    return _res("pass", "theme:default %s matches reference" % row["subject"].get("fallback"), {"row": row})


def check_off_token_literals(ctx):
    doc = _state(ctx).get("subject_tokens")
    if doc is None:
        return _res("unavailable", "no subject tokens document")
    hits = parity.rule_off_literals(doc, parity.load_rules(_config(ctx).get("rules")), _thresholds(ctx))
    cap = _thresholds(ctx).get("max_off_token_literals")
    details = {"hits": hits[:200], "total": len(hits), "cap": cap}
    if cap is not None and len(hits) > int(cap):
        return _res("fail", "%d literal(s) outside the token table > cap %s" % (len(hits), cap), details)
    return _res("pass", "%d literal(s) outside the token table (%s)" % (
        len(hits), "within cap %s" % cap if cap is not None else "advisory — set [ui] max_off_token_literals to gate"), details)


def _contrast_pairs(ctx):
    pairs = _tokens_config(ctx).get("contrast_pairs")
    return pairs if pairs else [list(p) for p in DEFAULT_CONTRAST_PAIRS]


def check_contrast_pairs(ctx):
    doc = _state(ctx).get("subject_tokens")
    if doc is None:
        return _res("unavailable", "no subject tokens document")
    pairs = _contrast_pairs(ctx)
    hits = [parity._waive_hit(h, _waivers(ctx)) for h in parity.rule_contrast_pairs(doc, _rules(ctx), _thresholds(ctx), pairs)]
    live = [h for h in hits if h["status"] == "hit"]
    if live:
        return _res("fail", "%d contrast pair(s) below floor" % len(live), {"hits": hits, "pairs": pairs})
    return _res("pass", "%d pair(s) × themes above floor" % len(pairs), {"hits": hits, "pairs": pairs})


def check_a11y_static(ctx):
    subject = subject_inventory(ctx)
    if subject is None:
        return _res("na", "no source inventory")
    rules, thr = _rules(ctx), _thresholds(ctx)
    hits = parity.rule_name_interactive(subject, rules, thr) + parity.rule_lang(subject, rules, thr) \
        + parity.rule_heading_order(subject, rules, thr)
    return _rules_verdict([parity._waive_hit(h, _waivers(ctx)) for h in hits], "static WCAG subset (name, lang, heading order)")


def _rules_verdict(hits, label):
    red = [h for h in hits if h["status"] == "hit" and h["severity"] in ("critical", "serious")]
    minor = [h for h in hits if h["status"] == "hit" and h not in red]
    details = {"hits": hits, "serious": len(red), "minor": len(minor)}
    if red:
        return _res("fail", "%d serious/critical rule hit(s), %d minor — %s" % (len(red), len(minor), label), details)
    return _res("pass", "0 serious hits, %d minor — %s" % (len(minor), label), details)


def check_ledger_lint(ctx):
    ledgers = _ledgers(ctx)
    if not ledgers.get("dir"):
        return _res("na", "no ledgers (ui/parity/{pending,waivers,aliases}.txt)")
    problems = parity.ledger_lint(ledgers)
    base = _det(ctx, "ledgers", "base_texts")
    if base is not None:
        acknowledged = ctx["sel"].get("waivers_acknowledged")
        problems += parity.ledger_shrink_check(ledgers, base, acknowledged if acknowledged else [])
    if problems:
        return _res("fail", "%d ledger problem(s): %s" % (len(problems), ", ".join(sorted({p["kind"] for p in problems}))), {"problems": problems})
    return _res("pass", "ledgers well-formed and shrink-only vs merge-base (%d pending, %d waivers, %d aliases)" % (
        len(ledgers["pending"]), len(ledgers["waivers"]), len(ledgers["aliases"])))


def check_golden_manifest(ctx):
    goldens = _det_dict(ctx, "goldens")
    if not goldens.get("dir"):
        return _res("na", "no goldens directory declared (config.goldens.dir / reference goldens)")
    machine_dir = goldens.get("machine_dir")
    if not machine_dir or not os.path.isdir(machine_dir):
        return _res("unavailable", "no goldens for this machine (%s) — other machines' goldens are not comparable" % goldens.get("machine_key"))
    result = visual.check_manifest(machine_dir)
    if not result["ok"]:
        return _res("fail", "%d unreviewed, %d reasonless, %d dangling golden(s)" % (
            len(result["unreviewed"]), len(result["reasonless"]), len(result["dangling"])), result)
    return _res("pass", "%d golden(s) all in manifest with reasons" % result["count"], result)


def _loosened_keys(current, base, ctx):
    loosened = [k for k, sign in LOOSENING.items() if _loosened(current.get(k), base.get(k), sign)]
    masks_now, masks_base = _mask_area(_config(ctx)), _mask_area(_det_dict(ctx, "config_base"))
    if masks_now > masks_base:
        loosened.append("masks (area %s → %s)" % (masks_base, masks_now))
    return loosened


def check_thresholds_unmoved(ctx):
    current, base = _det_dict(ctx, "thresholds"), _det(ctx, "thresholds_base")
    if base is None:
        return _res("na", "no merge-base thresholds to compare (no base or no diff)")
    loosened = _loosened_keys(current, base, ctx)
    if loosened:
        return _res("fail", "threshold_raised: %s" % ", ".join(loosened), {"loosened": loosened, "current": current, "base": base})
    return _res("pass", "thresholds and masks unchanged or stricter vs merge-base (source: %s)" % current.get("source"))


def _loosened(now, before, sign):
    if now is None or before is None:
        return False
    return (now - before) * sign > 0


def _mask_area(config):
    masks = config.get("masks")
    return sum(m[2] * m[3] for group in (masks if masks else {}).values() for m in group if len(m) == 4)


# --------------------------------------------------------------------------- #
# phase 2 —— 种 seed → 起 app → driver 采集（一次采完）
# --------------------------------------------------------------------------- #

def free_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class Launcher(object):
    """起一个长活进程（server），等 ready，最后杀整个进程组。测试注入 spawner。"""

    def __init__(self, spawner=None):
        self.spawner = spawner or self._popen
        self.proc = None

    @staticmethod
    def _popen(argv, cwd, env):
        return subprocess.Popen(argv, cwd=cwd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                start_new_session=True)

    def start(self, argv, cwd, env):
        self.proc = self.spawner(argv, cwd, env)
        return self.proc

    def stop(self):
        if self.proc is None:
            return
        try:
            os.killpg(self.proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, AttributeError, OSError):
            getattr(self.proc, "kill", lambda: None)()
        self.proc = None


def wait_ready(url, fetch, timeout=30.0, sleep=time.sleep, clock=time.monotonic):
    deadline = clock() + timeout
    while clock() < deadline:
        try:
            fetch(url)
            return True
        except (OSError, ValueError):
            sleep(0.2)
    return False


DEFAULT_MARKER = {"path": "/api/health", "expr": ".demo == true"}


def _launch_recipe(ctx):
    launch = _subject_side(ctx).get("launch")
    launch = launch if launch else {}
    if not launch.get("server"):
        return None
    port = free_port()
    home = tempfile.mkdtemp(prefix="test-ui-home-")
    env = dict(os.environ, **{launch.get("home_env", "AIASSISTANT_HOME"): home, launch.get("port_env", "ZAI_PORT"): str(port),
                              "PYTHONPATH": ctx["repo"]})
    marker = launch.get("marker")
    argv = [a.replace("{py}", sys.executable).replace("{port}", str(port)).replace("{home}", home) for a in launch["server"]]
    return {"argv": argv, "env": env, "home": home, "port": port,
            "url": "http://127.0.0.1:%d" % port, "seed": launch.get("seed"), "ready": launch.get("ready", "/api/health"),
            "marker": marker if marker else dict(DEFAULT_MARKER), "flags_all_on": launch.get("flags_all_on")}


def _seed(ctx, recipe, scene):
    if not recipe.get("seed"):
        return lc.RunResult(-2, "", "no seed recipe")
    argv = [a.replace("{py}", sys.executable).replace("{home}", recipe["home"]).replace("{scene}", scene)
            for a in recipe["seed"]]
    return (ctx.get("runner") or lc.run_command)(argv, cwd=ctx["repo"], timeout=300)


def _dims_for_tier(dims, full):
    """tier ≥ 3 全矩阵；tier 2 只跑默认主题 × 默认视口 × 默认语言。"""
    themes = dims.get("themes") or ["light", "dark"]
    viewports = dims.get("viewports") or [{"name": "desktop", "w": 1440, "h": 900}]
    languages = dims.get("languages") or ["zh"]
    if full:
        return themes, viewports, languages
    return [dims.get("default_theme") or themes[0]], viewports[:1], languages[:1]


def _cfg_or(cfg, key, default):
    value = cfg.get(key)
    return value if value else default


def _driver_config(ctx, recipe, tier):
    cfg, dims = _config(ctx), _det_dict(ctx, "dims")
    themes, viewports, languages = _dims_for_tier(dims, tier >= 3)
    return {"url": recipe["url"], "screens": _cfg_or(cfg, "screens", [{"id": "index", "route": ""}]),
            "themes": themes, "viewports": viewports, "languages": languages,
            "scenes": _cfg_or(dims, "scenes", ["initial"])[:1], "ready": recipe["ready"],
            "theme_storage_key": cfg.get("theme_storage_key", "zai.theme"),
            "lang_storage_key": cfg.get("lang_storage_key", "zai.lang"), "tokens": _token_var_names(ctx),
            "geometry": _cfg_or(cfg, "geometry", {}), "masks": _cfg_or(cfg, "masks", {}), "axe": _det(ctx, "tools", "axe"),
            "flags_all_on": recipe.get("flags_all_on"), "shots_dir": _out_path(ctx, "shots", "x")[:-1], "tab_limit": 400}


def _token_var_names(ctx):
    doc = _state(ctx).get("subject_tokens")
    themes = (doc if doc else {}).get("themes")
    names = {tok.get("var") for theme in (themes if themes else {}).values() for tok in theme.values()}
    return sorted(n for n in names if n)


def check_structure_runtime(ctx):
    """起 app（seed 进临时 HOME）→ marker 探针（seed_guard）→ driver（flags default + all_on）→ bundle。"""
    ready = _runtime_ready(ctx)
    if ready is not None:
        return ready
    recipe = _launch_recipe(ctx)
    state = _state(ctx)
    state["launch"] = {"recipe": {k: v for k, v in recipe.items() if k != "env"}, "seeded": False, "marker_seen": False}
    seed = _seed(ctx, recipe, _cfg_or(_det_dict(ctx, "dims"), "scenes", ["initial"])[0])
    if not seed.ok:
        return _res("fail", "seed refused — capture aborted: %s" % (seed.stderr.strip()[:200] if seed.stderr.strip() else "rc %s" % seed.rc))
    state["launch"]["seeded"] = True
    return _capture(ctx, recipe, state)


def _runtime_ready(ctx):
    if _det_dict(ctx, "sides", "subject", "mode").get("structure") != "runtime":
        hint = _det(ctx, "runtime_hint")
        return _res("unavailable", hint if hint else "runtime instruments missing (node + project playwright + launch recipe)")
    if not _det_dict(ctx, "sides", "subject", "launch").get("server"):
        return _res("unavailable", "no launch recipe (ui/parity/config.json launch.server / seed / ready / marker)")
    return None


def _capture(ctx, recipe, state):
    launcher = Launcher(ctx.get("spawner"))
    fetch = ctx.get("fetch")
    fetch = fetch if fetch else refmod._fetch_loopback
    try:
        launcher.start(recipe["argv"], ctx["repo"], recipe["env"])
        if not wait_ready(recipe["url"] + recipe["ready"], fetch, ctx.get("ready_timeout", 30.0)):
            return _res("fail", "app did not become ready at %s within budget" % recipe["url"])
        seen = refmod.probe_marker(recipe["url"], recipe["marker"], fetch)
        state["launch"]["marker_seen"] = seen
        if not seen:
            return _res("fail", "seed_guard: demo marker %s not seen — refusing to capture (real data risk)" % recipe["marker"])
        return _drive(ctx, recipe, state)
    finally:
        launcher.stop()
        shutil.rmtree(recipe["home"], ignore_errors=True)


def _node_bin(ctx):
    node = _det(ctx, "tools", "node")
    return node if node else "node"


def _drive(ctx, recipe, state):
    output, res = inv.run_driver(_driver_config(ctx, recipe, ctx["sel"].get("tier", 2)), _det(ctx, "tools", "playwright"),
                                 _out_path(ctx, "runtime", "x")[:-1], _runner(ctx), _node_bin(ctx), ctx.get("driver_timeout"))
    if output is None:
        return _res("fail", "driver failed (rc %s): %s" % (res.rc, res.text().strip()[-300:]))
    bundle = inv.parse_runtime(output, dict(_subject_side(ctx), seed={"seeded_by_skill": True, "marker": recipe["marker"]}))
    bundle["inventory"]["names_filtered"] = _static_name_filter(ctx, bundle["inventory"])
    state["runtime"] = bundle
    tc.write_text(_out_path(ctx, "inventory", "subject-runtime.json"), tc.dump_json(bundle["inventory"]))
    runs = len(inv._rows(output, "runs"))
    return _res("pass", "%d runtime item(s) across %d run(s); marker seen; seeded by skill" % (
        len(bundle["inventory"]["items"]), runs), {"runs": runs})


def _known_names(ctx):
    source = subject_inventory(ctx)
    names = (source if source else {}).get("names")
    return set(names if names else [])


def _foreign_name(item, known):
    raw = item["name"]["raw"]
    return bool(raw) and raw not in known and not item.get("pin")


def _static_name_filter(ctx, runtime_inv):
    """名字不在 subject 源字符串集合里 → {dynamic}（用户内容永不进清单）。没有源集合 = 不过滤。"""
    known = _known_names(ctx)
    if not known:
        return 0
    foreign = [item for item in runtime_inv["items"] if _foreign_name(item, known)]
    for item in foreign:
        item["name"]["raw"], item["dynamic"] = "{dynamic}", True
    return len(foreign)


# --------------------------------------------------------------------------- #
# phase 3 —— runtime 读数
# --------------------------------------------------------------------------- #

def _runtime(ctx):
    return _state(ctx).get("runtime")


def _need_runtime(ctx):
    if _runtime(ctx) is None:
        return _res("unavailable", "no runtime bundle (structure_runtime did not capture)")
    return None


def check_app_launch(ctx):
    launch = _state(ctx).get("launch")
    if launch is None:
        return _need_runtime(ctx)
    return _res("pass" if launch["marker_seen"] else "fail", "launched %s · seeded %s · marker %s" % (
        launch["recipe"].get("url"), launch["seeded"], launch["marker_seen"]), launch)


def check_seed_guard(ctx):
    """runtime 产物必须带 skill 自己种的 seed；没有 runtime 产物时 = pass（无图可泄）。"""
    bundle = _runtime(ctx)
    if bundle is None:
        return _res("pass", "no runtime artifacts this run — nothing captured, nothing to guard")
    side = bundle["inventory"].get("side")
    seed = (side if side else {}).get("seed")
    if not (seed if seed else {}).get("seeded_by_skill"):
        return _res("fail", "runtime artifacts without the skill's own seed — refused")
    return _res("pass", "every runtime artifact carries subject.seed (seeded_by_skill) · %d name(s) filtered to {dynamic}"
                % bundle["inventory"].get("names_filtered", 0))


def check_pair_runtime(ctx):
    miss = _need_runtime(ctx)
    if miss:
        return miss
    reference = reference_inventory(ctx)
    if reference is None:
        return _res("na", "design-system mode has no reference inventory")
    result = parity.compare_items(_runtime(ctx)["inventory"], reference, _ledgers(ctx), _thresholds(ctx),
                                  parity._reached(_runtime(ctx)["inventory"]))
    _state(ctx)["pair_runtime"] = result
    return _pair_verdict(ctx, result, "structure")


def check_topology_runtime(ctx):
    miss = _need_runtime(ctx)
    if miss:
        return _topology_source(ctx)
    result = _state(ctx).get("pair_runtime")
    if result is None:
        return _res("unavailable", "pairing did not run")
    rows = _topology_rows(result)
    if rows:
        return _res("fail", "%d landmark(s) CHANGED topology: %s" % (len(rows), ", ".join(r["id"] for r in rows[:5])), {"rows": rows})
    return _res("pass", "landmark side / parent / order match the reference", {"landmarks": len(_runtime(ctx)["inventory"]["landmarks"])})


def _topology_rows(result):
    return [r for r in result["rows"] if any(f.startswith("topology") for f in r["fields_changed"])]


def _topology_source(ctx):
    result = _state(ctx).get("pair_source")
    if result is None:
        return _res("unavailable", "no runtime bundle and no source pairing")
    rows = _topology_rows(result)
    status = "fail" if rows else "substituted"
    return _res(status, "source topology (data-side / landmark path) — %d CHANGED; cannot see rendered position" % len(rows), {"rows": rows})


def check_tokens_runtime(ctx):
    miss = _need_runtime(ctx)
    if miss:
        return miss
    observed = _cfg_or(_runtime(ctx), "tokens_observed", {})
    drift = _token_drift(parity._themes_of(_state(ctx).get("subject_tokens")), observed)
    if drift:
        return _res("fail", "%d computed token(s) differ from the declared table" % len(drift), {"drift": drift[:100]})
    return _res("pass", "computed values equal the declared table across %d theme(s)" % len(observed), {"themes": sorted(observed)})


def _color_drift(tok, value):
    """声明为颜色的 token，computed 值可解析且不等 → True。"""
    if not tok or tok.get("$type") != "color":
        return False
    computed = tc.canonical_color(value)
    return computed is not None and computed != tok["$value"]


def _token_drift(declared, observed):
    drift = []
    for theme, values in observed.items():
        table = {tok.get("var"): tok for tok in parity._theme_table({"themes": declared}, theme).values()}
        for var, value in values.items():
            if _color_drift(table.get(var), value):
                drift.append({"theme": theme, "var": var, "declared": table[var]["$value"], "computed": tc.canonical_color(value)})
    return drift


def _geometry_substitute(ctx, geometry_map):
    rows = parity.geometry_source_substitute(geometry_map, _component_css_texts(ctx))
    present = sum(1 for r in rows if r["status"] == "PRESENT")
    status = "fail" if present < len(rows) else "substituted"
    return _res(status, "source substitute: %d/%d layout token(s) consumed in CSS — rendered size unseen" % (present, len(rows)),
                {"rows": rows})


def _geometry_line(row):
    note = " (%s)" % row["note"] if row["note"] else ""
    return "%s %s→%s%s" % (row["location"], row["declared"], sorted(set(row["observed"])), note)


def check_geometry_runtime(ctx):
    geometry_map = _config(ctx).get("geometry")
    if not geometry_map:
        return _res("unavailable", "no geometry map — add ui/parity/config.json geometry {\"layout.lane.width\": {\"screen\": \"board\", \"role\": \"list\", \"measure\": \"width\"}}")
    if _runtime(ctx) is None:
        return _geometry_substitute(ctx, geometry_map)
    rows = parity.compare_geometry(geometry_map, reference_tokens(ctx), _cfg_or(_runtime(ctx), "geometry", {}), _thresholds(ctx),
                                   _state(ctx).get("subject_tokens"))
    changed = [r for r in rows if r["status"] == "CHANGED"]
    if changed:
        return _res("fail", "%d geometry CHANGED: %s" % (len(changed), "; ".join(_geometry_line(r) for r in changed)), {"rows": rows})
    return _res("pass", "%d layout token(s) rendered at declared size" % len(rows), {"rows": rows})


def _component_css_texts(ctx):
    texts = []
    for base in _cfg_or(_det_dict(ctx, "tokens_files"), "component_dirs", []):
        root = os.path.join(ctx["repo"], base)
        texts += [lc.read_text_or_empty(os.path.join(root, rel)) for rel in lc.walk_files(root) if rel.endswith(".css")]
    return texts


def check_theme_default_observed(ctx):
    miss = _need_runtime(ctx)
    if miss:
        return miss
    observed = _cfg_or(_runtime(ctx), "observed_theme", {})
    row = parity.compare_default_theme(_state(ctx).get("subject_tokens"), reference_tokens(ctx), observed)
    if "observed" in row["fields_changed"]:
        return _res("fail", "first frame observed %s ≠ reference default %s" % (observed, row["reference"].get("fallback")), {"row": row})
    return _res("pass", "first frame under both emulations: %s" % observed, {"row": row})


def _axe_hit(violation):
    target = violation.get("target", "?")
    return dict(violation, rule_id="axe:%s" % violation.get("id"), id=target, status="hit",
                severity=violation.get("impact", "serious"), measured=violation.get("help"), threshold="0 violations", location=target)


def check_a11y_rules(ctx):
    miss = _need_runtime(ctx)
    if miss:
        return miss
    bundle, has_axe = _runtime(ctx), bool(_det(ctx, "tools", "axe"))
    hits = parity.run_rules(bundle["inventory"], None, _thresholds(ctx), _rules(ctx), None, _waivers(ctx))
    hits += [_axe_hit(v) for v in inv._rows(bundle, "axe")]
    label = "runtime WCAG subset" + (" + axe-core" if has_axe else " (axe absent — SUBSTITUTED subset: no ARIA validity, no color-only cues)")
    verdict = _rules_verdict(hits, label)
    if verdict["status"] == "pass" and not has_axe:
        verdict["status"] = "substituted"
    return verdict


def check_screens_capture(ctx):
    miss = _need_runtime(ctx)
    if miss:
        return miss
    shots = inv._rows(_runtime(ctx)["inventory"], "shots")
    if not shots:
        return _res("fail", "driver returned no screenshots")
    return _res("pass", "%d screenshot(s) captured with demo seed (not yet diffed — tier 3 visual_diff)" % len(shots), {"shots": [s["id"] for s in shots]})


# --------------------------------------------------------------------------- #
# tier 3 —— visual diff / matrix / keyboard / reflow / i18n
# --------------------------------------------------------------------------- #

def _machine_goldens(ctx):
    """本机 golden 目录；没有 → (None, unavailable 结果)。"""
    goldens = _det_dict(ctx, "goldens")
    machine_dir = goldens.get("machine_dir")
    if machine_dir and os.path.isdir(machine_dir):
        return machine_dir, None
    return None, _res("unavailable", "no goldens for this machine (%s) — run --propose-goldens and have a human bless them"
                      % goldens.get("machine_key"))


def check_visual_diff(ctx):
    miss = _need_runtime(ctx)
    if miss:
        return miss
    machine_dir, unavailable = _machine_goldens(ctx)
    if unavailable:
        return unavailable
    rows = [_shot_row(ctx, shot, machine_dir) for shot in inv._rows(_runtime(ctx)["inventory"], "shots")]
    return _visual_verdict(rows, visual.check_manifest(machine_dir))


def _visual_verdict(rows, manifest):
    over = [r for r in rows if r.get("item_status") == "CHANGED"]
    if over or not manifest["ok"]:
        return _res("fail", "%d shot(s) over threshold, manifest ok=%s" % (len(over), manifest["ok"]), {"rows": rows, "manifest": manifest})
    no_golden = sum(1 for r in rows if r.get("status") == "no_golden")
    return _res("pass", "%d shot(s) within threshold (%d without golden)" % (len(rows), no_golden), {"rows": rows})


def _shot_row(ctx, shot, machine_dir):
    golden = os.path.join(machine_dir, os.path.basename(shot["path"]))
    if not os.path.exists(golden):
        return {"id": shot["id"], "status": "no_golden", "item_status": None}
    masks = _cfg_or(_cfg_or(_config(ctx), "masks", {}), shot.get("screen"), [])
    try:
        result = visual.compare_shot(shot["path"], golden, _thresholds(ctx), masks, ctx.get("runner"), ctx["out"], _det(ctx, "tools"))
    except (OSError, ValueError) as exc:
        return {"id": shot["id"], "status": "unreadable", "item_status": "CHANGED", "error": str(exc)}
    result.pop("tiles", None)
    return dict(result, id=shot["id"], golden=golden)


def check_matrix_themes_viewports(ctx):
    miss = _need_runtime(ctx)
    if miss:
        return miss
    dims = _cfg_or(_runtime(ctx)["inventory"], "dims", {})
    if len(inv._rows(dims, "themes")) < 2:
        return _res("substituted", "single theme captured — tier 3 needs themes × viewports × languages (rerun at tier 3)")
    return _res("pass", "matrix %s × %s × %s captured" % (dims.get("themes"), [v.get("name") for v in inv._rows(dims, "viewports")], dims.get("languages")))


def check_keyboard_reach(ctx):
    miss = _need_runtime(ctx)
    if miss:
        return miss
    hits = parity.rule_keyboard(_runtime(ctx)["inventory"], parity.load_rules(), _thresholds(ctx))
    return _rules_verdict(hits, "every visible interactive item reachable by Tab")


def check_focus_order(ctx):
    miss = _need_runtime(ctx)
    if miss:
        return miss
    walks = _cfg_or(_runtime(ctx)["inventory"], "focus_walk", {})
    loops = [k for k, walk in walks.items() if len(walk) != len(set(walk))]
    if loops:
        return _res("fail", "focus walk revisits an element before finishing: %s" % ", ".join(loops[:3]), {"loops": loops})
    return _res("pass", "%d focus walk(s) visit each element once" % len(walks))


def _overflows(measure):
    return bool(measure) and measure.get("scrollWidth", 0) > measure.get("clientWidth", 0)


def check_reflow(ctx):
    miss = _need_runtime(ctx)
    if miss:
        return miss
    overflow = _cfg_or(_runtime(ctx)["inventory"], "overflow", {})
    bad = {k: v for k, v in overflow.items() if _overflows(v)}
    if bad:
        return _res("fail", "horizontal overflow on %d run(s): %s" % (len(bad), ", ".join(sorted(bad)[:3])), {"overflow": bad})
    return _res("pass", "no horizontal overflow across %d run(s)" % len(overflow))


def check_i18n_parity(ctx):
    subject = subject_inventory(ctx)
    if subject is None:
        return _res("na", "no source inventory")
    half = [i["id"] for i in subject["items"] if (i["name"].get("zh") is None) != (i["name"].get("en") is None)]
    if half:
        return _res("fail", "%d item(s) with only one language half" % len(half), {"items": half[:100]})
    bilingual = sum(1 for i in subject["items"] if i["name"].get("zh") and i["name"].get("en"))
    return _res("pass", "%d bilingual name(s), none half-translated" % bilingual)


# --------------------------------------------------------------------------- #
# tier 4 / 5 —— v0.1 只诚实标 UNAVAILABLE（表里有行，代码不装样子）
# --------------------------------------------------------------------------- #

def _v01_unavailable(what):
    def check(ctx):
        return _res("unavailable", "%s — not wired in test-ui 0.1.0 (table-only; run by hand, paste into Notes)" % what)
    check.__name__ = "check_" + what.split(" ")[0]
    return check


check_inventory_stability = _v01_unavailable("inventory_stability ×reruns (needs tier 2 driver reruns)")
check_visual_stability = _v01_unavailable("visual_stability ×3 (self-different shots = flaky)")
check_states_matrix = _v01_unavailable("states_matrix hover/focus/active/disabled")
check_cross_engine = _v01_unavailable("cross_engine (webkit/firefox via project playwright)")
check_reference_runtime = _v01_unavailable("reference_runtime (git: ref launched in temp worktree)")
check_matrix_all_routes = _v01_unavailable("matrix_all_routes")
check_all_references = _v01_unavailable("all_references")
check_clean_machine_ui = _v01_unavailable("clean_machine_ui (temp clone → npm ci → build → launch → inventory == committed)")
check_golden_review_sheet = _v01_unavailable("golden_review_sheet")


def check_opinion(ctx):
    """advisory：只产出 Opinion 段；不碰任何状态（parity.apply_opinion 隔离）。"""
    opinion = ctx["sel"].get("opinion")
    text = opinion.get("text") if isinstance(opinion, dict) else None
    if not text:
        return _res("na", "opinion is advisory: pass selection.opinion.text (a design-quality note) to print it under "
                    "## Opinion (not a measurement)")
    return _res("pass", "opinion recorded — Nothing in it changes a status or a rank.", {"opinion": {"text": text}})
