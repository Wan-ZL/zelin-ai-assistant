#!/usr/bin/env python3
"""test-ui skill · 探测（唯一看世界的地方）：UI 面 + 工具 + 项目适配器 + 配置/账本/golden + 两侧
（SUBJECT / REFERENCE，每个传感器一个仪器模式）+ 阈值来源 + diff → 触发器 + tier 推荐 + 菜单 → JSON。

法典指针：docs/CONTRACT.md §58（阈值单源：qa/gates.toml `[ui]` → ui/parity/config.json .thresholds →
skill 默认值并注明 strict；skill 只读）、§62（parity 契约适配器：scripts/ui/*.py、ui/parity/*、
web/e2e/visual.spec.ts 在场即被点名）。设计 = vnext2-plan R2.8 / D14；SKILL.md「1 Detect」。

用法：detect_ui.py [--repo PATH] [--base REF] [--against REF] [--out FILE]
退出码：0；2 = repo 不可读 / 配置坏 / 参照解析不出（列出候选）。零网络；子进程只有 git 与
`node -e require.resolve`（都经 ladder_common_vendored.run_command 注入缝）。
判例：tests/test_skill_test_ui_detect.py（docs-only diff 不点火；.md 里的 aria-label 不点火）。
"""

import argparse
import fnmatch
import json
import os
import re
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import checks_ui  # noqa: E402
import ladder_common_vendored as lc  # noqa: E402
import parity  # noqa: E402
import reference as refmod  # noqa: E402
import testui_common as tc  # noqa: E402
import tokens  # noqa: E402
import visual  # noqa: E402

TOOLS = ("git", "node", "npx", "npm", "odiff", "swiftc", "lighthouse")
_UI_EXT = (".tsx", ".jsx", ".vue", ".svelte", ".html", ".htm", ".css", ".swift", ".ts", ".js")
_DOC_EXT = (".md", ".rst", ".txt")
_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
FILE_TRIGGERS = (
    ("screen_changed", ("web/src/components/*", "web/src/pages/*", "*.tsx", "*.jsx", "*.vue", "*.svelte", "*.html",
                        "shell/Sources/*.swift")),
    ("tokens_changed", ("*tokens.css", "*typeScale.ts", "*Tokens*.swift", "ui/tokens/*", "tailwind.config.*")),
    ("names_changed", ("*i18n*", "server/lanes.py")),
    ("ledger_changed", ("ui/parity/*.txt", "ui/parity/config.json", "ui/parity/goldens/*", "qa/gates.toml")),
    ("demo_changed", ("scripts/demo_seed.py",)),
)
LINE_TRIGGERS = (
    ("theme_changed", re.compile(r"data-theme|prefers-color-scheme|color-scheme|zai\.theme")),
    ("layout_changed", re.compile(r"@media|grid-template|flex-wrap|--native-layout-|\b(?:width|height)\s*:\s*\d")),
    ("a11y_attr_changed", re.compile(r"aria-|\brole=|tabIndex|tabindex|:focus|\boutline\b|\binert\b|accessibilityLabel")),
    ("names_changed", re.compile(r"\bL\(\s*\"|\btext\(\s*\"")),
)


# --------------------------------------------------------------------------- #
# 文件 / 面 / tokens 文件
# --------------------------------------------------------------------------- #

def list_files(runner, repo):
    tracked = lc.git_lines(runner, repo, ["ls-files"])
    if tracked is None:
        return sorted(lc.walk_files(repo)), [], False
    untracked = lc.git_lines(runner, repo, ["ls-files", "--others", "--exclude-standard"]) or []
    keep = [f for f in sorted(set(tracked) | set(untracked)) if os.path.exists(os.path.join(repo, f))
            and not any(part in lc.SKIP_DIRS for part in f.split("/")[:-1])]
    return keep, [f for f in untracked if f in set(keep)], True


def _root_of(files, ext):
    """有这类扩展名的文件 → 最短公共顶层目录（web/src 之类）。"""
    dirs = sorted({f.rsplit("/", 1)[0] if "/" in f else "." for f in files if f.endswith(ext)})
    if not dirs:
        return None
    common = os.path.commonpath(dirs) if len(dirs) > 1 else dirs[0]
    return common.replace(os.sep, "/") or "."


_UI_SRC_EXT = (".tsx", ".jsx", ".vue", ".svelte")


def _is_ui_source(rel):
    return rel.endswith(_UI_SRC_EXT) and ".test." not in rel


def _is_page_html(rel):
    return rel.endswith((".html", ".htm")) and "/dist/" not in "/" + rel and not rel.startswith(("web/index", "node_modules"))


def _surface(kind, files, ext):
    return {"kind": kind, "root": _root_of(files, ext), "files": len(files)}


def _web_surface(files):
    tsx = [f for f in files if _is_ui_source(f)]
    if tsx:
        return [_surface("web-react", tsx, _UI_SRC_EXT)]
    html = [f for f in files if _is_page_html(f)]
    return [_surface("static-html", html, (".html", ".htm"))] if html else []


def detect_surfaces(files):
    """web-react（tsx/jsx/vue/svelte）> static-html（无 react 时）；swift-source 并列。"""
    surfaces = _web_surface(files)
    swift = [f for f in files if f.endswith(".swift")]
    if swift:
        surfaces.append(_surface("swift-source", swift, (".swift",)))
    return surfaces


def detect_tokens_files(repo, files, surfaces):
    roots = [s["root"] for s in surfaces if s["kind"] in ("web-react", "static-html")]
    web_root = [r.split("/src")[0] for r in roots]
    return tokens.find_token_files(repo, files, web_root or roots)


def detect_lang(repo, index_html):
    match = re.search(r"<html[^>]*\blang\s*=\s*[\"']([^\"']*)", lc.read_text_or_empty(os.path.join(repo, index_html)) if index_html else "")
    return match.group(1) if match else ("" if index_html else None)


# --------------------------------------------------------------------------- #
# 工具 / 项目适配器
# --------------------------------------------------------------------------- #

def _resolve_node_module(runner, cwd, name):
    res = runner(["node", "-e", "console.log(require.resolve(%s))" % json.dumps(name)], cwd=cwd, timeout=30)
    return res.stdout.strip().splitlines()[-1] if res.ok and res.stdout.strip() else None


def _browsers_cache():
    home = os.path.expanduser("~")
    for rel in ("Library/Caches/ms-playwright", ".cache/ms-playwright"):
        path = os.path.join(home, rel)
        if os.path.isdir(path) and os.listdir(path):
            return True
    return False


def _node_modules(runner, cwd, has_node):
    """playwright / @playwright/test / axe 的解析路径（没有 node 一律 None）。"""
    if not has_node:
        return {"playwright": None, "playwright_test": None, "axe": None}
    playwright = _resolve_node_module(runner, cwd, "playwright")
    test = _resolve_node_module(runner, cwd, "@playwright/test")
    if not playwright and test:
        playwright = _resolve_node_module(runner, cwd, "playwright-core")
    return {"playwright": playwright, "playwright_test": test, "axe": _resolve_node_module(runner, cwd, "axe-core/axe.min.js")}


def probe_tools(runner, repo, web_dir, which=shutil.which):
    tools = {name: which(name) for name in TOOLS}
    cwd = os.path.join(repo, web_dir) if web_dir else repo
    tools.update(_node_modules(runner, cwd, bool(tools["node"])))
    tools["playwright_bin"] = os.path.exists(os.path.join(cwd, "node_modules", ".bin", "playwright"))
    tools["browsers_cache"] = _browsers_cache()
    return tools


def detect_adapters(repo):
    rels = {"extract_native_inventory": "scripts/ui/extract_native_inventory.py",
            "extract_native_tokens": "scripts/ui/extract_native_tokens.py", "parity_check": "scripts/ui/parity_check.py",
            "visual_spec": "web/e2e/visual.spec.ts", "demo_seed": "scripts/demo_seed.py", "dev_preview": "scripts/dev-preview.sh",
            "server_module": "server/__main__.py", "web_dist": "web/dist/index.html"}
    return {key: rel if os.path.exists(os.path.join(repo, rel)) else None for key, rel in rels.items()}


def _web_dir(files):
    pkgs = [f for f in files if os.path.basename(f) == "package.json" and "node_modules" not in f]
    with_src = [os.path.dirname(f) for f in pkgs if any(x.startswith(os.path.dirname(f) + "/src/") for x in files)]
    return (with_src or [os.path.dirname(f) for f in pkgs] or [None])[0]


# --------------------------------------------------------------------------- #
# 配置（项目的 + 本 repo 内置适配器默认）/ 账本 / golden / 维度
# --------------------------------------------------------------------------- #

def _builtin_screens(files):
    pages = sorted(f for f in files if re.search(r"web/src/pages/\w+Page\.tsx$", f))
    screens = []
    for page in pages:
        name = re.sub(r"Page\.tsx$", "", os.path.basename(page)).lower()
        screens.append({"id": name, "route": "" if name == "board" else "?page=%s" % name,
                        "source": [page, "web/src/components/%s/*" % name]})
    if screens:
        screens.append({"id": "shell", "route": "", "source": ["web/src/components/shell/*", "web/src/components/chrome/*"]})
    return screens


def _builtin_launch(adapters):
    if not (adapters.get("server_module") and adapters.get("demo_seed")):
        return None
    return {"server": ["{py}", "-m", "server"], "seed": ["{py}", "scripts/demo_seed.py", "{home}", "--scene", "{scene}"],
            "ready": "/api/health", "marker": {"path": "/api/health", "expr": ".demo == true"},
            "home_env": "AIASSISTANT_HOME", "port_env": "ZAI_PORT", "needs": ["web_dist"],
            "flags_all_on": {"file": "settings_overrides.json", "prefix": "features."}}


BUILTIN_GEOMETRY = {"layout.lane.width": {"screen": "board", "role": "list", "measure": "width"},
                    "layout.rail.default_width": {"screen": "*", "role": "navigation", "measure": "width"},
                    "layout.lane.gap": {"screen": "board", "role": "list", "measure": "gap"},
                    "layout.strip.width": {"screen": "board", "selector": "[data-strip]", "measure": "width"}}
BUILTIN_DIMS = {"themes": ["light", "dark"], "default_theme": "light",
                "viewports": [{"name": "desktop", "w": 1440, "h": 900}, {"name": "narrow", "w": 960, "h": 800}],
                "languages": ["zh", "en"], "scenes": ["initial"]}


def _builtin_geometry(repo, adapters):
    if adapters.get("extract_native_tokens") or os.path.exists(os.path.join(repo, "ui", "tokens")):
        return dict(BUILTIN_GEOMETRY)
    return {}


def _config_source(source, used_default):
    if source:
        return source
    return "built-in adapter defaults (%s)" % ", ".join(used_default) if used_default else None


def build_config(repo, files, adapters):
    """ui/parity/config.json（项目）优先；缺的键用本 repo 内置适配器默认补（记 config_source）。"""
    cfg, source = refmod.load_config(repo)
    defaults = {"screens": _builtin_screens(files), "launch": _builtin_launch(adapters),
                "geometry": _builtin_geometry(repo, adapters), "dims": dict(BUILTIN_DIMS)}
    used_default = [k for k, v in defaults.items() if k not in cfg and v]
    for key in used_default:
        cfg[key] = defaults[key]
    return cfg, _config_source(source, used_default)


def detect_ledgers(runner, repo, base_commit):
    ledger_dir = os.path.join(repo, "ui", "parity")
    has = any(os.path.exists(os.path.join(ledger_dir, n)) for n in ("pending.txt", "waivers.txt", "aliases.txt"))
    parsed = parity.load_ledgers(ledger_dir if has else None)
    base_texts = None
    if base_commit and has:
        base_texts = {name: "\n".join(lc.git_lines(runner, repo, ["show", "%s:ui/parity/%s.txt" % (base_commit, name)]) or [])
                      for name in ("pending", "waivers", "aliases")}
    return {"dir": ledger_dir if has else None, "parsed": parsed, "base_texts": base_texts}


def _declared_goldens(cfg, reference_side):
    block = cfg.get("goldens")
    declared = (block if isinstance(block, dict) else {}).get("dir")
    if declared:
        return declared
    return (reference_side if reference_side else {}).get("goldens")


def _goldens_root(repo, cfg, reference_side):
    """config.goldens.dir → 参照别名的 goldens → ui/parity/goldens（存在时）→ None；相对路径以 repo 为基。"""
    declared = _declared_goldens(cfg, reference_side)
    if declared:
        return declared if os.path.isabs(declared) else os.path.join(repo, declared)
    fallback = os.path.join(repo, "ui", "parity", "goldens")
    return fallback if os.path.isdir(fallback) else None


def detect_goldens(repo, cfg, reference_side):
    root = _goldens_root(repo, cfg, reference_side)
    key = visual.machine_key(sys.platform, cfg.get("engine", "chromium"), cfg.get("dpr", 1))
    return {"dir": root, "machine_key": key, "machine_dir": os.path.join(root, key) if root else None}


# --------------------------------------------------------------------------- #
# 阈值（gates.toml [ui] → config.thresholds → 默认）+ merge-base 版本
# --------------------------------------------------------------------------- #

def _thresholds_from(gates_text, cfg):
    thr = dict(parity.DEFAULT_THRESHOLDS)
    ui = {}
    if gates_text:
        ui = lc.parse_toml_subset(gates_text).get("ui") or {}
    if ui:
        thr.update(ui, source="qa/gates.toml [ui]", note="project thresholds — single source of truth, skill reads only")
    elif cfg.get("thresholds"):
        thr.update(cfg["thresholds"], source="ui/parity/config.json .thresholds", note="project thresholds (config.json)")
    return thr


def detect_thresholds(runner, repo, cfg, base_commit):
    gates = lc.read_text_or_empty(os.path.join(repo, "qa", "gates.toml"))
    current = _thresholds_from(gates, cfg)
    if not base_commit:
        return current, None, None
    base_gates = "\n".join(lc.git_lines(runner, repo, ["show", "%s:qa/gates.toml" % base_commit]) or [])
    base_cfg_text = "\n".join(lc.git_lines(runner, repo, ["show", "%s:ui/parity/config.json" % base_commit]) or [])
    try:
        base_cfg = json.loads(base_cfg_text) if base_cfg_text.strip() else {}
    except ValueError:
        base_cfg = {}
    return current, _thresholds_from(base_gates, base_cfg), base_cfg


# --------------------------------------------------------------------------- #
# diff / 触发器 / 推荐
# --------------------------------------------------------------------------- #

class DiffParser(object):
    """`git diff -U0` 最小解析：新增行号 + 文本、改动文件名。"""

    def __init__(self):
        self.file, self.line, self.in_header = None, 0, False
        self.added_text = {}

    def feed(self, raw):
        if raw.startswith("diff "):
            self.in_header, self.file = True, None
        elif self.in_header and raw.startswith("+++ "):
            self._start_file(raw[4:].split("\t")[0])
        elif not self.in_header and self.file is not None:
            self._body(raw)

    def _start_file(self, name):
        self.in_header = False
        self.file = None if name == "/dev/null" else (name[2:] if name.startswith("b/") else name)
        if self.file:
            self.added_text.setdefault(self.file, [])

    def _body(self, raw):
        match = _HUNK_RE.match(raw)
        if match:
            self.line = int(match.group(1))
        elif raw.startswith("+"):
            self.added_text[self.file].append((self.line, raw[1:]))
            self.line += 1
        elif not raw.startswith("-") and not raw.startswith("\\ "):
            self.line += 1


def _merge_base(runner, repo, base):
    if not base:
        return None
    lines = lc.git_lines(runner, repo, ["merge-base", "HEAD", base])
    return lines[0] if lines else base


def _parse_diff(runner, repo, commit, parser):
    """→ 改动文件名列表；同时把 -U0 diff 喂进 parser。"""
    if not commit:
        return []
    res = runner(["git", "diff", "-U0", "--no-color", "--no-ext-diff", commit], cwd=repo, timeout=120)
    for raw in res.stdout.splitlines():
        parser.feed(raw)
    names = lc.git_lines(runner, repo, ["diff", "--name-only", commit])
    return names if names else []


def detect_diff(runner, repo, requested_base, untracked):
    base = lc.resolve_base(runner, repo, requested_base)
    commit = _merge_base(runner, repo, base)
    parser = DiffParser()
    names = _parse_diff(runner, repo, commit, parser)
    for rel in untracked:
        text = lc.read_text_or_empty(os.path.join(repo, rel))
        parser.added_text[rel] = list(enumerate(text.splitlines(), 1))
    changed = sorted(set(names) | set(parser.added_text) | set(untracked))
    return {"base": base, "base_commit": commit, "changed_files": changed, "added_text": parser.added_text,
            "untracked": list(untracked)}


def _is_doc(path):
    return path.endswith(_DOC_EXT)


def _file_hits(changed, hits):
    for path in changed:
        for tid, globs in FILE_TRIGGERS:
            if any(fnmatch.fnmatch(path, g) for g in globs) and not _is_doc(path):
                hits.setdefault(tid, []).append("%s: (file changed)" % path)


def _ui_file(path):
    """只有 UI 文件的新增行参与正则；文档 / Python / JSON / 测试不点火。"""
    return path.endswith(_UI_EXT) and ".test." not in path


def _line_triggers(path, lineno, text, hits):
    for tid, rx in LINE_TRIGGERS:
        if rx.search(text):
            hits.setdefault(tid, []).append("%s:%d: %s" % (path, lineno, text.strip()[:80]))


def _line_hits(added_text, hits):
    for path, lines in added_text.items():
        if _ui_file(path):
            for lineno, text in lines:
                _line_triggers(path, lineno, text, hits)


def detect_triggers(diff):
    """文件名规则 + 新增行正则 → [{id, evidence[:8], hits}]。文档文件不参与。"""
    hits = {}
    _file_hits(diff["changed_files"], hits)
    _line_hits(diff["added_text"], hits)
    return [{"id": tid, "evidence": ev[:8], "hits": len(ev)} for tid, ev in sorted(hits.items())]


def _screens_touched(changed, cfg):
    touched = set()
    screens = cfg.get("screens")
    for path in changed:
        for screen in (screens if screens else []):
            globs = screen.get("source")
            if any(fnmatch.fnmatch(path, g) for g in (globs if globs else [])):
                touched.add(screen["id"])
    return sorted(touched)


def _docs_or_ledger(path):
    return _is_doc(path) or path.startswith("ui/parity/")


def _shared_component(path):
    return "components/shell" in path or "components/chrome" in path


def _rec_rules(changed, fired, screens):
    """按顺序第一条命中的推荐；都不中 → None。"""
    n_changed = len(changed)
    rules = (
        (not changed, 2, "no diff vs base — measure the whole tree at tier 2 (runtime inventory)"),
        (all(_docs_or_ledger(p) for p in changed), 1, "docs / ledger-only diff (%d file(s)) — static gates suffice" % n_changed),
        ("tokens_changed" in fired and len(screens) > 3, 4, "tokens changed and %d screens touched — stability matters (tier 4)" % len(screens)),
        ("tokens_changed" in fired, 3, "tokens changed — every screen × theme (tier 3)"),
        (any(_shared_component(p) for p in changed), 3, "shared shell components changed — every screen × theme (tier 3)"),
        ("screen_changed" in fired, 2, "%d screen file(s) changed (%s) — runtime inventory on changed screens" % (
            n_changed, ", ".join(screens) if screens else "unmapped")),
    )
    for hit, tier, reason in rules:
        if hit:
            return tier, reason
    return None


def recommend(det):
    changed, fired = det["diff"]["changed_files"], {t["id"] for t in det["triggers"]}
    screens = _screens_touched(changed, det.get("config") if det.get("config") else {})
    rec = _rec_rules(changed, fired, screens)
    if rec is None:
        rec = (1, "%d file(s) changed, no UI surface touched — static gates" % len(changed))
    return {"tier": rec[0], "reason": rec[1], "screens": screens}


# --------------------------------------------------------------------------- #
# 两侧
# --------------------------------------------------------------------------- #

def _launch_missing(launch, adapters):
    """launch 配方缺什么（配方本身 / 它声明 needs 的产物）。"""
    if not launch:
        return ["launch recipe (ui/parity/config.json: launch.server / seed / ready / marker)"]
    needs = launch.get("needs")
    return [n.replace("_", "/") + " (cd web && npm run build)" for n in (needs if needs else []) if not adapters.get(n)]


def _runtime_hint(tools, launch, adapters, web_dir):
    missing = []
    if not tools.get("node"):
        missing.append("node")
    if not tools.get("playwright"):
        missing.append("playwright module (cd %s && npm i -D @playwright/test && npx playwright install chromium)" % (web_dir if web_dir else "web"))
    missing += _launch_missing(launch, adapters)
    return "runtime UNAVAILABLE — missing: " + "; ".join(missing) if missing else None


def build_sides(repo, cfg, tools, adapters, surfaces, against, runner, web_dir, commit, dirty):
    launch = cfg.get("launch")
    hint = _runtime_hint(tools, launch, adapters, web_dir)
    stack = "web-dom" if any(s["kind"] in ("web-react", "static-html") for s in surfaces) else (surfaces[0]["kind"] if surfaces else None)
    subject = refmod.subject_side(repo, stack, hint is None, launch, commit, dirty)
    reference = refmod.resolve_side(repo, against, cfg, runner)
    return {"subject": subject, "reference": reference}, hint


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #

def detect(repo, base=None, against=None, runner=lc.run_command, which=shutil.which, py=None):
    """全量探测 → JSON-able dict（schemaVersion 1，字段 add-only）。ReferenceError 往上抛（main → exit 2）。"""
    py = py or sys.executable
    files, untracked, is_git = list_files(runner, repo)
    adapters, web_dir = detect_adapters(repo), _web_dir(files)
    cfg, cfg_source = build_config(repo, files, adapters)
    tools = probe_tools(runner, repo, web_dir, which)
    diff = detect_diff(runner, repo, base, untracked) if is_git else {"base": None, "base_commit": None, "changed_files": list(untracked), "added_text": {}, "untracked": list(untracked)}
    head = (lc.git_lines(runner, repo, ["rev-parse", "HEAD"]) or ["unknown"])[0]
    dirty = bool(lc.git_lines(runner, repo, ["status", "--porcelain"]))
    against = against or refmod.default_against(cfg, runner, repo)
    surfaces = detect_surfaces(files)
    sides, hint = build_sides(repo, cfg, tools, adapters, surfaces, against, runner, web_dir, head, dirty)
    thresholds, thresholds_base, cfg_base = detect_thresholds(runner, repo, cfg, diff["base_commit"])
    tokens_files = detect_tokens_files(repo, files, surfaces)
    det = {"schemaVersion": 1, "skill": {"name": tc.SKILL_NAME, "version": tc.SKILL_VERSION}, "generated_at": lc.utc_iso(),
           "repo": repo, "is_git": is_git, "python": py, "files": files, "surfaces": surfaces, "web_dir": web_dir,
           "lang": detect_lang(repo, tokens_files.get("index_html")), "tokens_files": tokens_files, "tools": tools,
           "adapters": adapters, "config": cfg, "config_source": cfg_source, "config_base": cfg_base,
           "ledgers": detect_ledgers(runner, repo, diff["base_commit"]), "goldens": detect_goldens(repo, cfg, sides["reference"]),
           "dims": cfg.get("dims") or {}, "thresholds": thresholds, "thresholds_base": thresholds_base, "sides": sides,
           "against": against, "candidates": refmod.candidates(cfg), "runtime_hint": hint, "diff": diff}
    det["triggers"] = detect_triggers(diff)
    det["recommendation"] = recommend(det)
    det["menu"] = checks_ui.build_menu(checks_ui.make_ctx(repo, det, py=py))
    return det


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", default=os.getcwd())
    parser.add_argument("--base")
    parser.add_argument("--against")
    parser.add_argument("--out")
    args = parser.parse_args(argv)
    repo = os.path.abspath(args.repo)
    if not os.path.isdir(repo):
        print("detect_ui: not a directory: %s" % repo, file=sys.stderr)
        return 2
    try:
        det = detect(repo, args.base, args.against)
    except (refmod.ReferenceError, ValueError) as exc:
        print("detect_ui: %s" % exc, file=sys.stderr)
        return 2
    if args.out:
        lc.write_json(args.out, det)
        print("detect_ui: wrote %s (against %s; recommended tier %s)" % (args.out, det["against"], det["recommendation"]["tier"]))
    else:
        print(json.dumps(det, indent=1, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
