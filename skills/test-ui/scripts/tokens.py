#!/usr/bin/env python3
"""test-ui skill · TOKENS 传感器：设计语言（颜色、字号梯、间距、圆角、布局几何、默认主题）→
W3C design-tokens 形状，按主题扁平成点路径（`layout.lane.width`），供 parity 逐主题比较。

来源（都记 producer.mode）：
  tokens-css      CSS 自定义属性，三种主题作用域：`:root`（默认/light）、`[data-theme="x"]`、
                  `@media (prefers-color-scheme: dark) { … }` —— mode=source
  design-tokens   W3C design-tokens JSON（本 repo 的 ui/tokens/native-tokens.json；`$value`
                  里 {light, dark} 双值拆到两个主题）—— mode=frozen
  type-scale-ts   web/src/styles/typeScale.ts 的 `token: "--type-x", font: "600 15px/1.4 …"` 表
  literal-census  组件 CSS 里绕过 token 的字面量（color / radius / font-size）——`off_token_literals`
  theme-declared  index.html 首帧脚本 + tokens.css `color-scheme` → 声明的默认主题

法典指针：docs/CONTRACT.md §UI-parity.3（token 单源 = ui/tokens/native-tokens.json → tokens.css 生成块；
skill 只读两端、比较、不生成）、§58（阈值只读）。设计 = vnext2-plan R2.8 / D14；SKILL.md「TOKENS」。
颜色一律 `#rrggbbaa`，长度 px（1pt = 1px），字重 regular/medium/semibold/bold → 400/500/600/700。
判例：tests/test_skill_test_ui_tokens.py（负控制：dark 少一个 token；组件 CSS 里 `color: #fff`）。

用法：tokens.py --css FILE [--css FILE…] [--type-scale FILE] [--index-html FILE] --out FILE
      tokens.py --design-tokens FILE --out FILE
"""

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ladder_common_vendored as lc  # noqa: E402
import testui_common as tc  # noqa: E402

WEIGHTS = {"regular": 400, "medium": 500, "semibold": 600, "bold": 700, "normal": 400}
_FAMILY_PREFIX = (("--type-", "typography"), ("--font-", "typography"), ("--radius-", "radius"),
                  ("--space-", "spacing"), ("--spacing-", "spacing"), ("--gap-", "spacing"), ("--shadow-", "shadow"),
                  ("--native-layout-", "layout"), ("--native-color-", "color"), ("--native-overlay-", "color"),
                  ("--native-default-theme", "theme"), ("--z-", "other"), ("--motion-", "other"), ("--ease-", "other"))
_DECL_RE = re.compile(r"(--[\w-]+)\s*:\s*([^;{}]+);")
_FONT_RE = re.compile(r"^\s*(\d{3}|bold|normal)?\s*(\d+(?:\.\d+)?)px\s*(?:/\s*(\d+(?:\.\d+)?)(px)?)?\s*(.*)$")
_LITERAL_PROPS = {"color": "color", "background": "color", "background-color": "color", "border-color": "color",
                  "border": "color", "outline-color": "color", "fill": "color", "stroke": "color",
                  "border-radius": "radius", "font-size": "typography"}
_COLOR_LITERAL_RE = re.compile(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(")
_PX_LITERAL_RE = re.compile(r"\b\d+(?:\.\d+)?px\b")
_THEME_FALLBACK_RE = re.compile(r"""dataset\.theme\s*=\s*["'](light|dark)["']""")
_COLOR_SCHEME_RE = re.compile(r"color-scheme\s*:\s*(light|dark|light dark|dark light)")


# --------------------------------------------------------------------------- #
# CSS 自定义属性 → 按主题作用域
# --------------------------------------------------------------------------- #

def _scope_theme(selector, in_dark_media):
    """选择器 → 主题名。`[data-theme="dark"]` → dark；prefers-dark 媒体块里的 :root → dark；
    其余 :root/html/body → light（默认作用域）；别的选择器 → None（组件规则，不是 token 表）。"""
    match = re.search(r"""data-theme\s*=\s*["']?(\w+)""", selector)
    if match and ":not(" not in selector[:match.start()]:
        return match.group(1)
    if re.match(r"^\s*(:root|html|body)(\b|[:\[])", selector):
        return "dark" if in_dark_media else "light"
    return None


def _blocks(css):
    """→ [(selector, body, in_dark_media, line)]：一层 @media 展开，其余按花括号配对切块。"""
    out, pos, media_dark, media_end = [], 0, False, -1
    while True:
        open_pos = css.find("{", pos)
        if open_pos < 0:
            return out
        selector = css[pos:open_pos].strip().split("}")[-1].strip()
        if selector.startswith("@media"):
            media_dark = "prefers-color-scheme: dark" in selector or "prefers-color-scheme:dark" in selector
            media_end, pos = _match_brace(css, open_pos), open_pos + 1
            continue
        close = _match_brace(css, open_pos)
        out.append((selector, css[open_pos + 1:close], media_dark and open_pos < media_end, css.count("\n", 0, open_pos) + 1))
        pos = close + 1


def _match_brace(css, open_pos):
    depth = 0
    for index in range(open_pos, len(css)):
        depth += {"{": 1, "}": -1}.get(css[index], 0)
        if depth == 0:
            return index
    return len(css)


def _strip_css_comments(css):
    return re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), css, flags=re.S)


def parse_css_variables(css, rel="tokens.css"):
    """CSS → {theme: {"--name": {"value", "source"}}}（后声明覆盖先声明）。"""
    themes = {}
    for selector, body, in_dark, line in _blocks(_strip_css_comments(css)):
        theme = _scope_theme(selector, in_dark)
        if theme is None:
            continue
        for match in _DECL_RE.finditer(body):
            themes.setdefault(theme, {})[match.group(1)] = {
                "value": match.group(2).strip(), "source": "%s:%d" % (rel, line + body.count("\n", 0, match.start()))}
    return themes


# --------------------------------------------------------------------------- #
# 变量 → token（family / path / $type / $value）
# --------------------------------------------------------------------------- #

def family_of(name, value, categories=None):
    """`--name` → 家族：项目 config [tokens.categories] 前缀表优先 → 内置前缀表 → 解析成颜色 → other。"""
    for prefix, family in list((categories or {}).items()) + list(_FAMILY_PREFIX):
        if name.startswith(prefix):
            return family
    if _looks_like_color(value):
        return "color"
    return "other"


def _looks_like_color(value):
    text = value.strip()
    return bool(tc.parse_color(text)) or text.startswith("var(--")


def _native_path(name):
    """`--native-layout-rail-default-width` → `layout.rail.default_width`（还原生成块的 group.name）。"""
    rest = name[len("--native-layout-"):].split("-")
    return "layout.%s.%s" % (rest[0], "_".join(rest[1:])) if len(rest) > 1 else "layout.%s" % rest[0]


def token_path(name, family):
    if name.startswith("--native-layout-"):
        return _native_path(name)
    if name.startswith("--native-"):
        return "%s.%s" % (family, name[len("--native-"):].split("-", 1)[-1])
    bare = name[2:]
    for prefix in ("type-", "font-", "radius-", "space-", "spacing-", "gap-", "shadow-"):
        if bare.startswith(prefix):
            return "%s.%s" % (family, bare[len(prefix):])
    return "%s.%s" % (family, bare)


def _weight_number(weight):
    if weight in WEIGHTS:
        return WEIGHTS[weight]
    return int(weight) if weight and weight.isdigit() else 400


def _line_ratio(line, line_px, size):
    if not line:
        return None
    return round(float(line) / float(size), 3) if line_px else float(line)


def parse_font_shorthand(text):
    """`600 15px/1.4 var(--font-sans)` → {weight, size, line, family}；认不出 → None。"""
    match = _FONT_RE.match(text)
    if not match:
        return None
    weight, size, line, line_px, family = match.groups()
    return {"weight": _weight_number(weight), "size": float(size), "line": _line_ratio(line, line_px, size),
            "family": "mono" if "mono" in family else "sans"}


def _typed_value(family, value):
    """(family, raw) → ($type, $value)。颜色归一 hex8；typography 拆 font 简写；dimension 留字符串。"""
    if family == "color":
        return "color", tc.canonical_color(value) or value
    if family == "typography":
        parsed = parse_font_shorthand(value)
        return ("typography", parsed) if parsed else ("fontFamily", value)
    if family in ("radius", "spacing", "layout"):
        return "dimension", value
    return "string", value


def css_to_tokens(themes, categories=None):
    """{theme: {--name: {value, source}}} → {theme: {path: {$type, $value, source, var}}}。
    `var(--x)` 引用在同主题内解析一层。"""
    out = {}
    for theme, decls in themes.items():
        flat = {}
        for name, rec in decls.items():
            value = _resolve_var(rec["value"], decls)
            family = family_of(name, value, categories)
            kind, typed = _typed_value(family, value)
            flat[token_path(name, family)] = {"$type": kind, "$value": typed, "source": rec["source"], "var": name}
        out[theme] = flat
    return out


def _resolve_var(value, decls):
    match = re.match(r"^\s*var\((--[\w-]+)\)\s*$", value)
    if match and match.group(1) in decls:
        return decls[match.group(1)]["value"]
    return value


# --------------------------------------------------------------------------- #
# W3C design-tokens JSON（冻结源）→ 同一扁平形
# --------------------------------------------------------------------------- #

def design_tokens_to_themes(doc):
    """{group: {token: {$type,$value}}} → {light: {...}, dark: {...}}；$value 是 {light, dark} 就拆，
    否则两主题共用。`theme.default` 单独返回。"""
    flat = tc.flatten_tokens(doc)
    themes = {"light": {}, "dark": {}}
    for path, token in flat.items():
        value = token.get("$value")
        if isinstance(value, dict) and {"light", "dark"} <= set(value):
            for theme in ("light", "dark"):
                themes[theme][path] = dict(token, **{"$value": _norm_value(token.get("$type"), value[theme])})
        else:
            for theme in ("light", "dark"):
                themes[theme][path] = dict(token, **{"$value": _norm_value(token.get("$type"), value)})
    return themes


def _norm_value(kind, value):
    if kind == "color" and isinstance(value, str):
        return tc.canonical_color(value) or value
    return value


# --------------------------------------------------------------------------- #
# typeScale.ts / 默认主题声明 / 字面量普查
# --------------------------------------------------------------------------- #

_TS_ROW_RE = re.compile(r"""token:\s*"(--[\w-]+)"\s*,\s*font:\s*"([^"]+)\"""")


def parse_type_scale_ts(text):
    """`{ token: "--type-x", font: "600 15px/1.4 var(--font-sans)", … }` 行 → {path: token}。"""
    out = {}
    for name, font in _TS_ROW_RE.findall(text):
        parsed = parse_font_shorthand(font)
        out[token_path(name, "typography")] = {"$type": "typography", "$value": parsed, "source": "typeScale.ts",
                                               "var": name}
    return out


def _scheme_fallback(tokens_css):
    """tokens.css 的第一处 color-scheme → (fallback, evidence line)。"""
    scheme = _COLOR_SCHEME_RE.search(_strip_css_comments(tokens_css or ""))
    if not scheme:
        return None, "tokens.css: no color-scheme declaration"
    return scheme.group(1).split()[0], "tokens.css: color-scheme: %s" % scheme.group(1)


def declared_default_theme(index_html, tokens_css):
    """→ {"mode": fixed|system, "fallback": light|dark|None, "evidence": [...]}。
    index.html 写死 dataset.theme = "x" → fixed x；tokens.css :root color-scheme + prefers-dark 媒体块
    → system（fallback = :root 的 color-scheme）；只有 :root color-scheme 无媒体块 → fixed。"""
    match = _THEME_FALLBACK_RE.search(index_html or "")
    if match:
        return {"mode": "fixed", "fallback": match.group(1), "evidence": ["index.html: dataset.theme = %r" % match.group(1)]}
    fallback, evidence = _scheme_fallback(tokens_css)
    system = "prefers-color-scheme" in (tokens_css or "")
    return {"mode": "system" if system else "fixed", "fallback": fallback,
            "evidence": [evidence, "tokens.css: prefers-color-scheme media block %s" % ("present" if system else "absent")]}


def literal_census(css, rel, families=("color", "radius", "typography")):
    """组件 CSS 里绕过 var(--…) 的字面量 → [{file, line, property, value, family}]。"""
    hits = []
    for selector, body, _dark, line in _blocks(_strip_css_comments(css)):
        if _scope_theme(selector, False) is not None:
            continue  # token 表自己的声明不算「绕过」
        for match in re.finditer(r"([\w-]+)\s*:\s*([^;{}]+);", body):
            prop, value = match.group(1).lower(), match.group(2).strip()
            family = _LITERAL_PROPS.get(prop)
            if family in families and _is_literal(family, value):
                hits.append({"file": rel, "line": line + body.count("\n", 0, match.start()), "property": prop,
                             "value": value, "family": family})
    return hits


def _is_literal(family, value):
    if "var(--" in value or value in ("inherit", "transparent", "currentColor", "none", "0", "0px"):
        return False
    if family == "color":
        return bool(_COLOR_LITERAL_RE.search(value))
    return bool(_PX_LITERAL_RE.search(value))


# --------------------------------------------------------------------------- #
# 组装：tokens 文档（schemaVersion 1）
# --------------------------------------------------------------------------- #

def _families(themes):
    counts = {}
    for path in set().union(*[set(t) for t in themes.values()]) if themes else set():
        family = path.split(".")[0]
        counts[family] = counts.get(family, 0) + 1
    return counts


def tokens_document(adapter, mode, tool, themes, default_theme=None, literals=None, type_scale=None):
    """产物形状（字段 add-only）。"""
    return {"schemaVersion": tc.SCHEMA_VERSION, "producer": tc.producer(adapter, mode, tool),
            "default_theme": {"declared": default_theme, "observed": None}, "themes": themes,
            "families": _families(themes), "geometry": {}, "literals_outside": list(literals or []),
            "type_scale": type_scale or {}}


def _read_rel(repo, rel):
    return lc.read_text_or_empty(os.path.join(repo, rel)) if rel else ""


def _merged_variables(repo, css_files):
    merged = {}
    for rel in css_files:
        for theme, decls in parse_css_variables(_read_rel(repo, rel), rel).items():
            merged.setdefault(theme, {}).update(decls)
    return merged


def extract_css_tokens(repo, css_files, index_html=None, type_scale=None, only_dirs=None, categories=None):
    """项目 CSS → tokens 文档（mode=source）。css_files 相对 repo；token 表 = 每个文件里的 :root /
    data-theme / prefers 作用域；literal census 走 only_dirs 下的其它 CSS。"""
    themes = css_to_tokens(_merged_variables(repo, css_files), categories)
    declared = declared_default_theme(_read_rel(repo, index_html), _read_rel(repo, css_files[0] if css_files else None))
    literals = _census_dirs(repo, only_dirs or [], set(css_files))
    scale = parse_type_scale_ts(_read_rel(repo, type_scale)) if type_scale else {}
    return tokens_document("tokens-css", "source", "tokens.py tokens-css", themes, declared, literals, scale)


def _census_dirs(repo, dirs, exclude):
    hits = []
    for base in dirs:
        root = os.path.join(repo, base)
        if not os.path.isdir(root):
            continue
        for rel in lc.walk_files(root):
            full_rel = "%s/%s" % (base.rstrip("/"), rel)
            if rel.endswith(".css") and full_rel not in exclude:
                hits += literal_census(lc.read_text_or_empty(os.path.join(root, rel)), full_rel)
    return hits


_TABLE_NAMES = ("tokens.css", "variables.css", "theme.css")


def _is_project_css(rel):
    return rel.endswith(".css") and "/dist/" not in "/" + rel and "node_modules" not in rel


def _under_roots(rel, roots):
    if not roots:
        return True
    return any(r == "." or rel.startswith(r.rstrip("/") + "/") for r in roots)


def _is_named_table(root, rel):
    return os.path.basename(rel) in _TABLE_NAMES and _has_vars(root, rel)


def _is_root_table(root, rel):
    return _has_vars(root, rel) and ":root" in lc.read_text_or_empty(os.path.join(root, rel))


def _token_tables(root, css, roots=()):
    """token 表：UI 面根下的 tokens/variables/theme.css 含变量；退而求其次任一含 :root + 变量的 css（最多 2）。"""
    scoped = [f for f in css if _under_roots(f, roots)]
    tables = [f for f in scoped if _is_named_table(root, f)]
    if tables:
        return tables
    return [f for f in scoped if _is_root_table(root, f)][:2]


def _is_index_html(rel):
    return rel.endswith("index.html") and "/dist/" not in "/" + rel and "/public/" not in "/" + rel


def _component_dir(rel, tables, roots):
    """组件 css 所在目录（不是 token 表、在 UI 面根下）；根目录的 css → "."；否则 None。"""
    if rel in tables or not _under_roots(rel, roots):
        return None
    return rel.rsplit("/", 1)[0] if "/" in rel else "."


def find_token_files(root, files, surface_roots=()):
    """文件列表 → {css: token 表, index_html, type_scale, component_dirs}。表 = tokens/variables/theme.css
    含 `--` 变量，退而求其次任一含 :root 与 `--` 的 css（最多 2）；组件 css 目录只取 UI 面根下的。"""
    css = [f for f in files if _is_project_css(f)]
    tables = _token_tables(root, css, surface_roots)
    component_dirs = sorted({d for d in (_component_dir(f, tables, surface_roots) for f in css) if d})
    index_html = next((f for f in files if _is_index_html(f) and _under_roots(f, surface_roots)), None)
    return {"css": tables, "index_html": index_html, "type_scale": next((f for f in files if f.endswith("typeScale.ts")), None),
            "component_dirs": component_dirs}


def _has_vars(root, rel):
    return "--" in lc.read_text_or_empty(os.path.join(root, rel))


def load_design_tokens(path):
    """W3C JSON 文件 → tokens 文档（mode=frozen）；`theme.default` 提到 default_theme.declared。"""
    doc = tc.read_json(path)
    themes = design_tokens_to_themes(doc)
    default = themes["light"].get("theme.default", {}).get("$value")
    follows = themes["light"].get("theme.follows_system", {}).get("$value")
    declared = {"mode": "system" if follows else "fixed", "fallback": default, "evidence": ["%s: theme.default" % path]}
    return tokens_document("design-tokens-json", "frozen", path, themes, declared)


def _regenerate_native(repo, script, out_dir, runner):
    path = os.path.join(out_dir, "native-tokens.json")
    ok = runner([sys.executable, script, "--write", "--out", path, "--css", os.devnull], cwd=repo, timeout=600).ok
    return path if ok else None


def _can_regenerate(path, script, out_dir):
    return not os.path.exists(path) and os.path.exists(script) and bool(out_dir)


def load_native_tokens(repo, path=None, runner=lc.run_command, out_dir=None):
    """ui/tokens/native-tokens.json；缺席但 producer 在 → 现跑到 out_dir；都没有 → None。"""
    if not path:
        path = os.path.join(repo, "ui", "tokens", "native-tokens.json")
    script = os.path.join(repo, "scripts", "ui", "extract_native_tokens.py")
    if _can_regenerate(path, script, out_dir):
        path = _regenerate_native(repo, script, out_dir, runner)
    if not path or not os.path.exists(path):
        return None
    return load_design_tokens(path)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", default=os.getcwd())
    parser.add_argument("--css", action="append", default=[])
    parser.add_argument("--type-scale")
    parser.add_argument("--index-html")
    parser.add_argument("--only-dir", action="append", default=[])
    parser.add_argument("--design-tokens")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    if args.design_tokens:
        doc = load_design_tokens(args.design_tokens)
    else:
        doc = extract_css_tokens(os.path.abspath(args.repo), args.css, args.index_html, args.type_scale, args.only_dir)
    tc.write_text(args.out, tc.dump_json(doc))
    print("tokens: %s → %s" % (doc["families"], args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
