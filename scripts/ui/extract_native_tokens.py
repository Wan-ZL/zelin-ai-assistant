#!/usr/bin/env python3
"""原生 Mac app 的设计 token —— 从 mac/Sources 提取，写 ui/tokens/native-tokens.json，
再生成 web/src/styles/tokens.css 末尾的 `@generated native-tokens` 块。

法典：docs/CONTRACT.md §64.3（token 单源）。mac/Sources 全部走 SwiftUI 系统语义色
（.orange / .green / .accentColor / .primary.opacity(x) …），没有一处硬编码 hex，
所以「颜色 token」= 语义色用量 × macOS 解析值表（Apple HIG macOS system colors，
light/dark 两套，与 web tokens.css 头注已采用的暗色值一致）。提取项：
  color.semantic.*   —— 用到的语义色 → {light, dark} 解析值 + 用量计数
  color.overlay.*    —— Color.primary/secondary/accentColor.opacity(x) 的叠层比例
  typography.scale   —— .font(.system(size:weight:)) 的 (size, weight) 全集 + 计数
                        （角色级映射已由 web/src/styles/typeScale.ts 承担，此处不重复出 CSS）
  spacing / radius   —— .padding(N) / spacing: N / cornerRadius: N 的取值全集 + 计数
  layout.*           —— 列宽 400 / 书立条 44 / 列距 12 / 看板内边距 16 / 侧栏 48·200·160–320 /
                        窗口 900×640·min 720×480（按 file+regex 定点提取，任何一项找不到即 fail-loud）
  theme.default      —— light（owner 2026-09-02 拍板；原生 app 跟随 macOS 外观，无强制）

JSON 形状照 W3C Design Tokens 草案（组 → token{$type,$value,$extensions}）。生成的 CSS
块只放 `--native-*` 数据变量（light/dark 各一份），不做主题切换逻辑——把它们接进
语义 token（--danger/--status-*…）与列宽是 PR-B 的活；本脚本只钉住数值。

用法：
    python3 scripts/ui/extract_native_tokens.py --write   # JSON + CSS 块
    python3 scripts/ui/extract_native_tokens.py --check   # 两者都新鲜才 0
"""

import argparse
import os
import re
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ui_common as uc  # noqa: E402

TOKENS_CSS = os.path.join(uc.REPO_ROOT, "web", "src", "styles", "tokens.css")
BLOCK_BEGIN = "/* @generated-begin native-tokens"
BLOCK_END = "/* @generated-end native-tokens */"

# macOS 系统颜色解析值（Apple HIG · Color · macOS system colors，sRGB）。web
# tokens.css 的暗色家族（teal #6ac4dc / green #32d74b / red #ff453a / blue #0a84ff /
# purple #bf5af2 / orange #ff9f0a / gray #98989d / yellow #ffd60a）与本表逐值一致。
MACOS_COLORS = {
    "red": ("#ff3b30", "#ff453a"),
    "orange": ("#ff9500", "#ff9f0a"),
    "yellow": ("#ffcc00", "#ffd60a"),
    "green": ("#28cd41", "#32d74b"),
    "teal": ("#59adc4", "#6ac4dc"),
    "blue": ("#007aff", "#0a84ff"),
    "purple": ("#af52de", "#bf5af2"),
    "pink": ("#ff2d55", "#ff375f"),
    "gray": ("#8e8e93", "#98989d"),
    "accentColor": ("#007aff", "#0a84ff"),   # controlAccentColor 默认 = systemBlue
    "primary": ("rgba(0, 0, 0, 0.85)", "rgba(255, 255, 255, 0.85)"),      # labelColor
    "secondary": ("rgba(0, 0, 0, 0.5)", "rgba(255, 255, 255, 0.55)"),     # secondaryLabelColor
    "windowBackground": ("#ececec", "#323232"),                           # windowBackgroundColor
}
_SEMANTIC_RE = re.compile(r"\.(red|orange|yellow|green|teal|blue|purple|pink|gray|accentColor|primary|secondary)\b")
_OVERLAY_RE = re.compile(r"Color\.(primary|secondary|accentColor)\.opacity\(([0-9.]+)\)")
_FONT_RE = re.compile(r"\.font\(\.system\(size:\s*(\d+)(?:,\s*weight:\s*\.(\w+))?")
_PADDING_RE = re.compile(r"\.padding\((?:\.\w+,\s*)?(\d+)\)")
_SPACING_RE = re.compile(r"\bspacing:\s*(\d+)\b")
_RADIUS_RE = re.compile(r"cornerRadius:\s*(\d+)\b")
_WEIGHTS = ("regular", "medium", "semibold", "bold", "heavy")

# layout 定点：(token 路径, 文件, 正则（group 1 = 数值）, 单位)
LAYOUT_PROBES = (
    ("layout.lane.width", "Kanban.swift",
     r"\.padding\(\.bottom, 10\)\s*\}\s*\}\s*\.frame\(width: (\d+)\)", "px"),
    ("layout.strip.width", "Kanban.swift",
     r"\.frame\(width: (\d+)\)\s*\.frame\(maxHeight: \.infinity, alignment: \.top\)\s*"
     r"\.background\([^\n]*\n\s*\.clipShape\([^\n]*\n\s*\.contentShape", "px"),
    ("layout.lane.gap", "Kanban.swift",
     r"ScrollView\(\.horizontal\) \{\s*HStack\(alignment: \.top, spacing: (\d+)\)", "px"),
    ("layout.board.padding", "Kanban.swift", r"\.padding\((\d+)\)\s*\}\s*BoardFlightOverlay", "px"),
    ("layout.lane.radius", "Kanban.swift",
     r"\.frame\(width: 400\)\s*\.frame\(maxHeight[^\n]*\n\s*\.background[^\n]*\n\s*"
     r"\.clipShape\(RoundedRectangle\(cornerRadius: (\d+)\)\)", "px"),
    ("layout.card.radius", "Cards.swift", r"padding: 10, cornerRadius: (\d+), stroked: true", "px"),
    ("layout.rail.collapsed_width", "MainWindow.swift", r"collapsedWidth: Double = (\d+)", "px"),
    ("layout.rail.default_width", "MainWindow.swift", r"sidebarWidth = w == 0 \? (\d+) :", "px"),
    ("layout.rail.min_width", "MainWindow.swift", r"min\(max\(w, (\d+)\), \d+\)", "px"),
    ("layout.rail.max_width", "MainWindow.swift", r"min\(max\(w, \d+\), (\d+)\)", "px"),
    ("layout.window.default_width", "MainWindow.swift", r"NSRect\(x: 0, y: 0, width: (\d+), height: \d+\)", "px"),
    ("layout.window.default_height", "MainWindow.swift", r"NSRect\(x: 0, y: 0, width: \d+, height: (\d+)\)", "px"),
    ("layout.window.min_width", "MainWindow.swift", r"contentMinSize = NSSize\(width: (\d+), height: \d+\)", "px"),
    ("layout.window.min_height", "MainWindow.swift", r"contentMinSize = NSSize\(width: \d+, height: (\d+)\)", "px"),
)


def _token(kind, value, **ext):
    tok = {"$type": kind, "$value": value}
    if ext:
        tok["$extensions"] = {"zai": ext}
    return tok


def _sources(root):
    """[(name, stripped)]。"""
    out = []
    for path in uc.iter_swift_files(root):
        stripped, _ = uc.scan_views(uc.read_text(path))
        out.append((os.path.basename(path), stripped))
    return out


# --------------------------------------------------------------------------- #
# 颜色
# --------------------------------------------------------------------------- #

def semantic_colors(sources):
    counts = Counter()
    for _, text in sources:
        counts.update(_SEMANTIC_RE.findall(text))
    out = {}
    for name, (light, dark) in sorted(MACOS_COLORS.items()):
        if counts.get(name) or name == "windowBackground":
            out[name] = _token("color", {"light": light, "dark": dark},
                               usages=counts.get(name, 0), swift="." + name)
    return out


def overlay_colors(sources):
    counts = Counter()
    for _, text in sources:
        counts.update(_OVERLAY_RE.findall(text))
    out = {}
    for (base, alpha), n in sorted(counts.items(), key=lambda kv: (kv[0][0], float(kv[0][1]))):
        key = "%s-%s" % (base, ("%g" % float(alpha))[2:])   # 0.018 → 018, 0.10 → 1
        out[key] = _token("number", float(alpha), base=base, usages=n)
    return out


# --------------------------------------------------------------------------- #
# 字号 / 间距 / 圆角
# --------------------------------------------------------------------------- #

def typography_scale(sources):
    counts = Counter()
    for _, text in sources:
        for size, weight in _FONT_RE.findall(text):
            counts[(int(size), weight or "regular")] += 1
    scale = [{"size": s, "weight": w, "usages": n}
             for (s, w), n in sorted(counts.items(), key=lambda kv: (kv[0][0], _WEIGHTS.index(kv[0][1])
                                                                     if kv[0][1] in _WEIGHTS else 9))]
    return {"scale": scale, "roles": "web/src/styles/typeScale.ts (CONTRACT §54.1 item 10)"}


def _value_counts(sources, regex):
    counts = Counter()
    for _, text in sources:
        counts.update(int(v) for v in regex.findall(text))
    return {str(v): _token("dimension", "%dpx" % v, usages=n) for v, n in sorted(counts.items())}


# --------------------------------------------------------------------------- #
# layout 定点
# --------------------------------------------------------------------------- #

def _probe(sources, name, regex):
    text = dict(sources).get(name, "")
    m = re.search(regex, text)
    if not m:
        raise ValueError("layout probe found nothing: %s in %s" % (regex, name))
    return int(next(g for g in m.groups() if g))


def layout_tokens(sources):
    out = {}
    for path, name, regex, unit in LAYOUT_PROBES:
        value = _probe(sources, name, regex)
        node = out
        for part in path.split(".")[1:-1]:
            node = node.setdefault(part, {})
        node[path.split(".")[-1]] = _token("dimension", "%d%s" % (value, unit), source=name)
    return out


# --------------------------------------------------------------------------- #
# 装配
# --------------------------------------------------------------------------- #

def build_tokens(root=uc.MAC_SOURCES):
    sources = _sources(root)
    return {
        "$description": "Native Mac app design tokens, machine-extracted from mac/Sources "
                        "(frozen under D3). Truth for web/src/styles/tokens.css's generated block. "
                        "Regenerate: python3 scripts/ui/extract_native_tokens.py --write",
        "color": {"semantic": semantic_colors(sources), "overlay": overlay_colors(sources)},
        "typography": typography_scale(sources),
        "spacing": {"padding": _value_counts(sources, _PADDING_RE),
                    "stack": _value_counts(sources, _SPACING_RE)},
        "radius": _value_counts(sources, _RADIUS_RE),
        "layout": layout_tokens(sources),
        "theme": {"default": _token("string", "light",
                                    source="owner decision 2026-09-02; native follows macOS appearance"),
                  "follows_system": _token("boolean", True)},
    }


# --------------------------------------------------------------------------- #
# CSS 块
# --------------------------------------------------------------------------- #

def _flatten(node, prefix=""):
    """嵌套 token 组 → [(css-name, token)]。"""
    if "$value" in node:
        return [(prefix, node)]
    out = []
    for key, child in sorted(node.items()):
        if key.startswith("$"):
            continue
        out.extend(_flatten(child, "%s-%s" % (prefix, key) if prefix else key))
    return out


def _css_name(name):
    return "--native-" + re.sub(r"[^a-z0-9-]", "-", re.sub(r"(?<=[a-z])(?=[A-Z])", "-", name).lower())


def _color_lines(tokens):
    lines = []
    for name, tok in _flatten(tokens["color"]["semantic"], "color"):
        lines.append("  %s-light: %s;" % (_css_name(name), tok["$value"]["light"]))
        lines.append("  %s-dark: %s;" % (_css_name(name), tok["$value"]["dark"]))
    for name, tok in _flatten(tokens["color"]["overlay"], "overlay"):
        lines.append("  %s: %s;" % (_css_name(name), tok["$value"]))
    return lines


def render_css_block(tokens):
    lines = [BLOCK_BEGIN + " — generated by scripts/ui/extract_native_tokens.py from "
             "ui/tokens/native-tokens.json; edits here are overwritten (CONTRACT §64.3) */",
             ":root {",
             "  --native-default-theme: %s;" % tokens["theme"]["default"]["$value"]]
    lines += ["  %s: %s;" % (_css_name(name), tok["$value"])
              for name, tok in _flatten(tokens["layout"], "layout")]
    lines += _color_lines(tokens)
    lines += ["}", BLOCK_END]
    return "\n".join(lines) + "\n"


def splice_block(css_text, block):
    """tokens.css 原文 + 新块 → 新原文（块在末尾；已有块被替换）。"""
    start = css_text.find(BLOCK_BEGIN)
    if start >= 0:
        end = css_text.find(BLOCK_END, start)
        end = len(css_text) if end < 0 else end + len(BLOCK_END)
        return css_text[:start] + block.rstrip("\n") + css_text[end:]
    return css_text.rstrip("\n") + "\n\n" + block


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--root", default=uc.MAC_SOURCES)
    parser.add_argument("--out", default=uc.NATIVE_TOKENS_PATH)
    parser.add_argument("--css", default=TOKENS_CSS)
    args = parser.parse_args(argv)
    tokens = build_tokens(args.root)
    fresh = {args.out: uc.dump_json(tokens)}
    fresh[args.css] = splice_block(_read_or_empty(args.css), render_css_block(tokens))
    if args.write:
        for path, text in fresh.items():
            uc.write_text(path, text)
        print("wrote %s and the native-tokens block in %s" % (
            os.path.relpath(args.out, uc.REPO_ROOT), os.path.relpath(args.css, uc.REPO_ROOT)))
    return _check_fresh(fresh) if args.check else 0


def _read_or_empty(path):
    return uc.read_text(path) if os.path.exists(path) else ""


def _check_fresh(fresh):
    stale = [p for p, text in fresh.items() if _read_or_empty(p) != text]
    if stale:
        print("stale: %s — rerun with --write" % ", ".join(os.path.relpath(p, uc.REPO_ROOT) for p in stale),
              file=sys.stderr)
        return 1
    print("native tokens are fresh")
    return 0


if __name__ == "__main__":
    sys.exit(main())
