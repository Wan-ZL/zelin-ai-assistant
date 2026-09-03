#!/usr/bin/env python3
"""test-ui skill · STRUCTURE 传感器：把一个 UI 变成 schemaVersion 1 的可达性清单
（roles + names + topology per screen）。三个适配器，同一 schema，`producer.mode` 说真话：

  web-source     TSX / JSX / HTML / Vue 的标记 tokenizer（隐式角色、role=、aria-label /
                 labelledby、文本子节点、<img alt>、标题、title、`text("zh","en")` 双语字面量、
                 `hidden` / aria-hidden / display:none → hidden、data-parity-id → pin、
                 `{flags.x && …}` → gated）——mode=source
  project:native `--from-source` 落到本 repo 的 scripts/ui/extract_native_inventory.py 产物
                 （ui/parity/native-inventory.json 或现跑 --out 到报告目录）并 add-only 归一：
                 controls[] → items、rail / lanes → landmarks + topology、theme_layout →
                 default_theme + layout.*——mode=frozen（D3 下 mac/Sources 只能是源）
  web-playwright probes/driver.cjs 在项目自己的 playwright 里跑（绝不安装）：ariaSnapshot 式
                 节点、bbox、computed tokens、tab 走位、overflow、截图——mode=runtime

法典指针：docs/CONTRACT.md §UI-parity（id 语法 `<kind>:<screen>:<role>:<slug>`，与 parity 契约
同一套；§45「屏幕不发起卡片」在此无关——清单只描述 UI 面）、§58（只读）。设计 =
docs/design/vnext2-plan.md R2.8 / D14。stdlib only；子进程只经 runner 注入缝。
判例：tests/test_skill_test_ui_inventory_source.py（含负控制：无名图标按钮、display:none）。

用法：
  inventory_a11y.py --from-source <dir> [--repo R] [--screen-map config.json] --out FILE
  inventory_a11y.py --native <repo> [--inventory FILE] --out FILE
  inventory_a11y.py --runtime <driver-config.json> --playwright <module path> --out FILE
"""

import argparse
import fnmatch
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ladder_common_vendored as lc  # noqa: E402
import testui_common as tc  # noqa: E402

SOURCE_EXT = (".html", ".htm", ".tsx", ".jsx", ".vue", ".svelte")
IMPLICIT_ROLES = {
    "button": "button", "a": "link", "select": "combobox", "textarea": "textbox", "h1": "heading",
    "h2": "heading", "h3": "heading", "h4": "heading", "h5": "heading", "h6": "heading", "nav": "navigation",
    "header": "banner", "main": "main", "footer": "contentinfo", "aside": "complementary", "ul": "list",
    "ol": "list", "li": "listitem", "img": "img", "dialog": "dialog", "label": "label", "table": "table",
    "tr": "row", "td": "cell", "th": "cell", "hr": "separator", "form": "form", "menu": "list",
    "summary": "button", "details": "group", "fieldset": "group", "output": "status", "progress": "status",
}
INPUT_ROLES = {"checkbox": "checkbox", "radio": "radio", "range": "slider", "number": "spinbutton",
               "search": "searchbox", "button": "button", "submit": "button", "reset": "button", "image": "button"}
STATIC_TAGS = frozenset({"p", "span", "label", "td", "th", "dt", "dd", "legend", "figcaption", "small", "strong",
                         "em", "caption", "option", "li", "summary", "output", "div"})
VOID_TAGS = frozenset({"img", "input", "br", "hr", "meta", "link", "source", "track", "wbr", "area", "base", "col",
                       "embed", "param"})
SKIP_TAGS = frozenset({"script", "style", "template", "noscript"})
CONTAINER_ROLES = frozenset({"list", "listitem", "row", "cell", "group", "dialog", "table", "tablist", "menu", "toolbar"})
NATIVE_ROLES = {"button": "button", "alert-button": "button", "toggle": "switch", "textfield": "textbox",
                "picker": "combobox", "option": "option", "menu-item": "menuitem", "menu": "button",
                "label": "static", "copy": "static", "help": "static", "dialog": "dialog", "textfield-secure": "textbox",
                "slider": "slider", "stepper": "spinbutton", "link": "link", "heading": "heading", "image": "img"}
_BILINGUAL_RE = re.compile(r"""\b(?:text|t|L)\(\s*"((?:[^"\\]|\\.)*)"\s*,\s*"((?:[^"\\]|\\.)*)"\s*\)""", re.S)
_LITERAL_RE = re.compile(r"""^\s*(?:"((?:[^"\\]|\\.)*)"|'((?:[^'\\]|\\.)*)')\s*$""", re.S)
_JSX_COMMENT_RE = re.compile(r"\{/\*.*?\*/\}", re.S)
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
# 行首的 // 与 /* … */ 注释（TS 文件头的中文说明里常写着 `<select>` 之类的标签名——不是元素）
_CODE_COMMENT_RE = re.compile(r"^[ \t]*//[^\n]*|^[ \t]*/\*.*?\*/", re.S | re.M)
_GATE_RE = re.compile(r"\{\s*(?:flags|features)\.[\w.]+\s*&&")
_MUSTACHE_RE = re.compile(r"\{\{[^}]*\}\}")
_STYLE_HIDDEN_RE = re.compile(r"display\s*:\s*none|visibility\s*:\s*hidden", re.I)


# --------------------------------------------------------------------------- #
# 标记 tokenizer → 节点树
# --------------------------------------------------------------------------- #

class Node(object):
    """一个元素：tag / attrs / children（Node 或 str）/ parent / 源位置。"""

    def __init__(self, tag, attrs, pos, parent=None):
        self.tag, self.attrs, self.pos, self.parent = tag, attrs, pos, parent
        self.children = []

    def is_component(self):
        return self.tag[:1].isupper() or "." in self.tag

    def attr(self, name):
        return self.attrs.get(name)


_NUMBER_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?|true|false)\s*$")


def _name_value(raw):
    """属性/文本表达式 → str（字面量）或 {"zh","en"}（双语）或 "{dynamic}"。`{-1}` / `{true}` 这类标量字面量
    按原文返回（tabIndex={-1} / disabled={true} 是写死的状态，不是运行时值）。"""
    bil = _BILINGUAL_RE.search(raw)
    if bil:
        return {"zh": bil.group(1), "en": bil.group(2)}
    lit = _LITERAL_RE.match(raw)
    if lit:
        return lit.group(1) if lit.group(1) is not None else lit.group(2)
    number = _NUMBER_RE.match(raw)
    return number.group(1) if number else "{dynamic}"


class _Quote(object):
    """引号状态机：feed(ch) → 现在是否在字符串里（进入/离开都在这里判）。"""

    def __init__(self, quotes="\"'`"):
        self.quotes, self.inside = quotes, None

    def feed(self, ch):
        if self.inside:
            if ch == self.inside:
                self.inside = None
            return True
        if ch in self.quotes:
            self.inside = ch
            return True
        return False


def _scan_expr(text, pos):
    """pos 指向 `{` → 配对 `}` 之后的位置（跟踪引号与嵌套花括号）。"""
    depth, quote = 0, _Quote()
    while pos < len(text):
        ch = text[pos]
        if not quote.feed(ch) and ch in "{}":
            depth += 1 if ch == "{" else -1
            if depth == 0:
                return pos + 1
        pos += 1
    return pos


def _scan_quoted(text, pos):
    end = text.find(text[pos], pos + 1)
    return len(text) if end < 0 else end + 1


def _attr_value(text, pos):
    """`=` 之后 → (value, next_pos)；值形：\"..\" / '..' / {expr} / 裸词。"""
    if pos < len(text) and text[pos] in "\"'":
        end = _scan_quoted(text, pos)
        return text[pos + 1:end - 1], end
    if pos < len(text) and text[pos] == "{":
        end = _scan_expr(text, pos)
        return _name_value(text[pos + 1:end - 1]), end
    match = re.compile(r"[^\s/>]*").match(text, pos)
    return match.group(0), match.end()


_ATTR_NAME_RE = re.compile(r"[:@\w.-]+")


def parse_attrs(text):
    """属性串 → {name: str | dict | None}；`{expr}` 走 _name_value（双语 / 字面量 / {dynamic}）。"""
    attrs, pos = {}, 0
    while pos < len(text):
        match = _ATTR_NAME_RE.match(text, pos)
        if not match:
            pos = _scan_expr(text, pos) if text[pos] == "{" else pos + 1
            continue
        name, pos = match.group(0), match.end()
        eq = re.compile(r"\s*=\s*").match(text, pos)
        if eq:
            attrs[name], pos = _attr_value(text, eq.end())
        else:
            attrs[name] = ""
    return attrs


def _scan_tag(text, start):
    """start 指向 `<` → 标签结束 `>` 之后的位置；花括号表达式与引号里的 `>` 不算。"""
    pos, quote = start + 1, _Quote("\"'")
    while pos < len(text):
        ch = text[pos]
        if quote.feed(ch):
            pass
        elif ch == "{":
            pos = _scan_expr(text, pos) - 1
        elif ch == ">":
            return pos + 1
        pos += 1
    return len(text)


_TAG_HEAD_RE = re.compile(r"<(/?)([A-Za-z][\w.:-]*)")


def iter_tags(text):
    """→ (closing, tag, attr_text, self_close, start, end)；`<` 后不是字母/斜杠的当文本。"""
    pos = 0
    while True:
        head = _TAG_HEAD_RE.search(text, pos)
        if not head:
            return
        end = _scan_tag(text, head.start())
        body = text[head.end():end - 1]
        self_close = body.rstrip().endswith("/")
        yield head.group(1), head.group(2), body.rstrip().rstrip("/"), self_close, head.start(), end
        pos = end


def _text_pieces(raw):
    """标签之间的文本 → 片段列表（字面量 / 双语 dict / "{dynamic}"）。花括号表达式先按配对
    切出；剩下还带 `{`/`}`/`=>` 的碎片是代码（`.map(i => ` 之类），不是 UI 文本，丢弃。"""
    raw = _MUSTACHE_RE.sub("{dynamic}", raw)
    pieces, pos = [], 0
    while pos < len(raw):
        brace = raw.find("{", pos)
        if brace < 0:
            pieces.append(raw[pos:])
            break
        pieces.append(raw[pos:brace])
        end = _scan_expr(raw, brace)
        pieces.append(_name_value(raw[brace + 1:end - 1]))
        pos = end
    return [p for p in pieces if _keep_piece(p)]


def _keep_piece(piece):
    if isinstance(piece, dict) or piece == "{dynamic}":
        return True
    return bool(piece.strip()) and not any(mark in piece for mark in ("{", "}", "=>"))


def _open(stack, tag, attrs, pos, root):
    parent = stack[-1] if stack else root
    node = Node(tag, attrs, pos, parent)
    parent.children.append(node)
    return node


def _close(stack, tag):
    for index in range(len(stack) - 1, -1, -1):
        if stack[index].tag == tag or tag == ">":
            del stack[index:]
            return


def _feed_tag(tag_tuple, stack, root, skipping):
    """一个标签 → 更新栈；返回新的 skipping 标签名（script/style 子树整体跳过）。"""
    closing, tag, attr_text, self_close, start, _end = tag_tuple
    if skipping:
        return _skip_state(closing, tag, skipping)
    if closing:
        _close(stack, tag)
        return None
    node = _open(stack, tag, parse_attrs(attr_text), start, root)
    if tag in SKIP_TAGS:
        return tag
    if _pushes(tag, self_close):
        stack.append(node)
    return None


def _skip_state(closing, tag, skipping):
    return None if closing and tag == skipping else skipping


def _pushes(tag, self_close):
    return not self_close and tag not in VOID_TAGS


def _blank_keep_lines(match):
    return "\n" * match.group(0).count("\n")


def preprocess(text):
    """剥注释（HTML / JSX / 行首代码注释）与 fragment 记号，换行数不变——offset 与行号都按这份文本算。"""
    for rx in (_HTML_COMMENT_RE, _JSX_COMMENT_RE, _CODE_COMMENT_RE):
        text = rx.sub(_blank_keep_lines, text)
    return re.sub(r"</?>", "", text)


def parse_markup(text):
    """预处理过的源码 → 根 Node（tag=""）；文本片段挂在当前节点下。"""
    root, stack, skipping, pos = Node("", {}, 0), [], None, 0
    for tag_tuple in iter_tags(text):
        if not skipping:
            for piece in _text_pieces(text[pos:tag_tuple[4]]):
                (stack[-1] if stack else root).children.append(piece)
        skipping = _feed_tag(tag_tuple, stack, root, skipping)
        pos = tag_tuple[5]
    return root


# --------------------------------------------------------------------------- #
# 角色 / 名字 / 可见性
# --------------------------------------------------------------------------- #

def _explicit_role(node):
    explicit = node.attr("role")
    if not isinstance(explicit, str) or not explicit.strip():
        return None
    role = explicit.split()[0].lower()
    return role if role in tc.ALL_ROLES else "generic"


def _input_role(node):
    kind = node.attr("type")
    kind = str(kind if kind else "text").lower()
    return None if kind == "hidden" else INPUT_ROLES.get(kind, "textbox")


def element_role(node):
    """显式 role= > input type 表 > 隐式表；组件与无角色容器 → None。"""
    explicit = _explicit_role(node)
    if explicit or node.is_component():
        return explicit
    if node.tag == "input":
        return _input_role(node)
    if node.tag == "a" and node.attr("href") is None:
        return None
    return IMPLICIT_ROLES.get(node.tag)


def _piece_text(piece):
    if isinstance(piece, dict):
        return piece
    return piece.strip()


def _merge_text(parts):
    """片段列表 → 单值：出现双语 dict 就返回第一个 dict；否则空白折叠的字符串。"""
    for part in parts:
        if isinstance(part, dict):
            return part
    return re.sub(r"\s+", " ", " ".join(p for p in parts if p)).strip()


def _text_children(node):
    """参与可达名计算的子节点：文本/双语片段，和非 svg、非 aria-hidden 的元素。"""
    for child in node.children:
        if isinstance(child, (str, dict)):
            yield child
        elif child.tag != "svg" and not is_aria_hidden(child):
            yield child


def text_content(node):
    """后代文本（跳过 aria-hidden 子树与 svg）；双语 dict 优先返回；只有组件子节点（<InlineView/>）
    而没有任何字面量 → "{dynamic}"（运行时才有名字，源模式不裁定它无名）。"""
    parts, components = [], False
    for child in _text_children(node):
        if isinstance(child, Node):
            parts.append(text_content(child))
            components = components or child.is_component()
        else:
            parts.append(_piece_text(child))
    merged = _merge_text(parts)
    if merged:
        return merged
    return "{dynamic}" if components else ""


def is_aria_hidden(node):
    return str(node.attr("aria-hidden") or "").lower() == "true"


def _hidden_attr(node):
    """`hidden` / hidden="" / hidden={cond} 算隐藏；`hidden={false}` 是写死的「不隐藏」，不算。"""
    return "hidden" in node.attrs and str(node.attr("hidden")).strip().lower() != "false"


def _style_hidden(node):
    style = node.attr("style")
    if not isinstance(style, str) or not _STYLE_HIDDEN_RE.search(style):
        return None
    return "display:none" if "display" in style.lower() else "visibility:hidden"


def hidden_by(node):
    """None = 可见；否则 hidden / aria-hidden / display:none / visibility:hidden 之一。"""
    if _hidden_attr(node):
        return "hidden"
    if is_aria_hidden(node):
        return "aria-hidden"
    return _style_hidden(node)


def _labelledby_text(node, by_id):
    ids = str(node.attr("aria-labelledby") or "").split()
    return _merge_text([text_content(by_id[i]) if i in by_id else "" for i in ids])


def _label_for_text(node, labels):
    target = node.attr("id")
    return text_content(labels[target]) if target in labels else ""


def _explicit_name(node, index):
    """aria-label / aria-labelledby → (name, source)；没有 → None。"""
    label = node.attr("aria-label")
    if label not in (None, ""):
        return label, "aria-label"
    if node.attr("aria-labelledby"):
        return _labelledby_text(node, index["by_id"]), "aria-labelledby"
    return None


def _field_name(node, index):
    text = _label_for_text(node, index["labels"])
    if text:
        return text, "label"
    value = node.attr("value")
    return (value if value else ""), "label"


def _content_name(node):
    text = text_content(node)
    if text:
        return text, "text"
    title = node.attr("title")
    return (title, "title") if title else ("", "none")


LABEL_ONLY_ROLES = tc.LANDMARK_ROLES | CONTAINER_ROLES
FIELD_TAGS = frozenset({"input", "select", "textarea"})


def _alt_name(node):
    alt = node.attr("alt")
    return (alt if alt else ""), "alt"


def accessible_name(node, role, index):
    """(name, source)：aria-label > aria-labelledby > alt > <label for> > 文本 > title。
    地标 / 容器只认显式标签，不吞后代文本（子控件变了容器名不该跟着变）。"""
    explicit = _explicit_name(node, index)
    if explicit:
        return explicit
    if role == "img":
        return _alt_name(node)
    if role in LABEL_ONLY_ROLES:
        return "", "none"
    if node.tag in FIELD_TAGS:
        return _field_name(node, index)
    return _content_name(node)


def _walk(node):
    yield node
    for child in node.children:
        if isinstance(child, Node):
            for sub in _walk(child):
                yield sub


def _label_target(node):
    """<label for> / JSX <label htmlFor> 指向的 id。"""
    if node.tag != "label":
        return None
    target = node.attr("for")
    target = target if target is not None else node.attr("htmlFor")
    return target if isinstance(target, str) else None


def build_index(root):
    """id → Node；<label for> → Node（名字解析用）。"""
    by_id, labels = {}, {}
    for node in _walk(root):
        if isinstance(node.attr("id"), str):
            by_id[node.attr("id")] = node
        target = _label_target(node)
        if target:
            labels[target] = node
    return {"by_id": by_id, "labels": labels}


def gated_spans(text):
    """`{flags.x && …}` 花括号范围 [(start, end)]——落在里面的元素是 gated。"""
    spans = []
    for match in _GATE_RE.finditer(text):
        depth, pos = 0, match.start()
        while pos < len(text):
            depth += {"{": 1, "}": -1}.get(text[pos], 0)
            if depth == 0:
                break
            pos += 1
        spans.append((match.start(), pos))
    return spans


def _in_spans(pos, spans):
    return any(start <= pos <= end for start, end in spans)


# --------------------------------------------------------------------------- #
# screen 映射（config.screens[].source globs → 目录启发式）
# --------------------------------------------------------------------------- #

def _mapped_screen(rel, screens):
    for entry in screens if screens else []:
        globs = entry.get("source")
        if any(fnmatch.fnmatch(rel, pat) for pat in (globs if globs else [])):
            return entry["id"]
    return None


def _heuristic_screen(rel):
    stem = os.path.splitext(os.path.basename(rel))[0].lower()
    parts = rel.split("/")
    if "pages" in parts:
        trimmed = re.sub(r"page$", "", stem)
        return trimmed if trimmed else stem
    if "components" in parts and parts.index("components") + 1 < len(parts) - 1:
        return parts[parts.index("components") + 1].lower()
    return stem


def screen_for_file(rel, screens=None):
    """config.screens[].source globs 优先，否则目录启发式（pages/<Name>Page.tsx → name；components/<dir>/ → dir）。"""
    mapped = _mapped_screen(rel, screens)
    return mapped if mapped else _heuristic_screen(rel)


# --------------------------------------------------------------------------- #
# 节点树 → items / landmarks（source 模式）
# --------------------------------------------------------------------------- #

def _name_record(name):
    if isinstance(name, dict):
        return {"raw": name.get("en") or name.get("zh") or "", "zh": name.get("zh"), "en": name.get("en"), "alt": []}
    return {"raw": name or "", "zh": None, "en": None, "alt": []}


def _kind_for(role):
    if role in tc.INTERACTIVE_ROLES:
        return "interactive"
    if role in tc.LANDMARK_ROLES:
        return "landmark"
    if role == "heading":
        return "heading"
    return "static"


def _side_attr(node):
    for key in ("data-side", "data-rail", "data-parity-side"):
        value = node.attr(key)
        if isinstance(value, str) and value in ("left", "right", "top", "bottom", "inside"):
            return value
    return None


def _topology_kind(role):
    return role in tc.LANDMARK_ROLES or role in tc.TOPOLOGY_KINDS


class SourceExtractor(object):
    """一份源码文件 → items + landmarks。parent 路径只由 landmark/list/region 类节点构成。"""

    def __init__(self, text, screen, rel, screens=None):
        text = preprocess(text)
        self.root = parse_markup(text)
        self.index = build_index(self.root)
        self.gates = gated_spans(text)
        self.screen, self.rel = screen, rel
        self.line_starts = [0] + [i + 1 for i, ch in enumerate(text) if ch == "\n"]
        self.items, self.landmarks, self.counter = [], [], {}

    def _line(self, pos):
        import bisect
        return bisect.bisect_right(self.line_starts, pos)

    def _order(self, parent_path):
        self.counter[parent_path] = self.counter.get(parent_path, 0) + 1
        return self.counter[parent_path] - 1

    def _hidden(self, node, inherited):
        return inherited or hidden_by(node)

    def _states(self, node, role, hidden):
        focusable = role in tc.INTERACTIVE_ROLES and hidden is None and not _literal_disabled(node) \
            and not _tab_removed(node) and "inert" not in node.attrs
        return {"source": {"visible": hidden is None, "hidden_by": hidden, "focusable": focusable}}

    def _item(self, node, role, parent_path, hidden):
        name, source = accessible_name(node, role, self.index)
        rec = _name_record(name)
        slug = _slug_for(rec["raw"], role)
        return {
            "id": tc.make_id(_id_kind(role), self.screen, role, slug),
            "key": {"screen": tc.screen_family(self.screen), "role": role, "slug": slug},
            "kind": _kind_for(role), "name": rec, "name_source": source, "pin": _str_attr(node, "data-parity-id"),
            "owner": "web", "gated": _in_spans(node.pos, self.gates) or _gate_attr(node),
            "shortcut": _str_attr(node, "data-shortcut"), "count": 1,
            "topology": {"parent": parent_path, "order": self._order(parent_path), "side": _side_attr(node)},
            "states": self._states(node, role, hidden), "screen": self.screen,
            "source": {"file": self.rel, "line": self._line(node.pos)}, "evidence": "source", "level": _heading_level(node),
        }

    def _emit(self, node, role, parent_path, hidden):
        """一个有角色的元素 → item（+ landmark 记录）；返回子节点应继承的地标路径。"""
        item = self._item(node, role, parent_path, hidden)
        self.items.append(item)
        if _topology_kind(role):
            self.landmarks.append({"id": item["id"], "role": role, "topology": item["topology"], "bbox": None,
                                   "children_order": []})
        if role == "heading":
            return parent_path
        return "%s>%s:%s" % (parent_path, role, item["key"]["slug"])

    def _visit(self, node, parent_path, hidden):
        role = element_role(node)
        hidden = self._hidden(node, hidden)
        path = parent_path
        if _emits_item(node, role):
            path = self._emit(node, role, parent_path, hidden)
        elif _static_text_node(node):
            self.items.append(self._item(node, "static", parent_path, hidden))
        for child in node.children:
            if isinstance(child, Node) and child.tag != "svg":
                self._visit(child, path, hidden)

    def run(self):
        for child in self.root.children:
            if isinstance(child, Node):
                self._visit(child, "window", None)
        return self.items, self.landmarks


def _slug_for(raw, role):
    if raw:
        return tc.slugify(str(raw).strip())
    return "unnamed" if role in tc.INTERACTIVE_ROLES else role


def _str_attr(node, name):
    value = node.attr(name)
    return value if isinstance(value, str) else None


def _gate_attr(node):
    return "data-gated" in node.attrs or "data-flag" in node.attrs


def _heading_level(node):
    return int(node.tag[1]) if node.tag[:1] == "h" and node.tag[1:].isdigit() else None


def _emits_item(node, role):
    """有角色（非 generic）的元素成条目；static / label 还要有字面量文本。"""
    if not role or role == "generic":
        return False
    return role not in ("static", "label") or _has_literal_text(node)


def _static_text_node(node):
    return node.tag in STATIC_TAGS and _has_literal_text(node) and not node.is_component()


def _tab_removed(node):
    """HTML `tabindex="-1"` 或 JSX `tabIndex={-1}` → 不在 Tab 序里（驱动器里 parseInt(tabindex) < 0 同义）。"""
    value = node.attr("tabindex")
    value = value if value is not None else node.attr("tabIndex")
    return str(value if value is not None else "0").strip() == "-1"


def _literal_disabled(node):
    """只有字面量 disabled（`disabled` / disabled="" / "true" / {true}）算禁用；`disabled={busy}` 是运行时状态。"""
    value = node.attr("disabled")
    return value is not None and str(value).strip().lower() in ("", "true", "disabled")


def _has_direct_text(node):
    return any(isinstance(c, dict) or (isinstance(c, str) and c.strip()) for c in node.children)


def _has_literal_text(node):
    """静态文本节点只认字面量 / 双语字面量；纯 `{expr}` 的 <span>{x}</span> 不成条目（运行时内容，源模式无名可比）。"""
    return any(isinstance(c, dict) or (isinstance(c, str) and c.strip() and c != "{dynamic}") for c in node.children)


def _id_kind(role):
    if role in tc.LANDMARK_ROLES:
        return "landmark"
    if role == "heading":
        return "heading"
    return "control"


def _assign_ordinals(items):
    """同 id 递增 #n 后缀（parity 语法）+ count = 同键条目数。整份清单合并后再算（跨文件同 id 也编号）；
    已带 #n 的先剥掉再编，幂等。"""
    groups = {}
    for item in items:
        item["id"] = re.sub(r"#\d+$", "", item["id"])
        groups.setdefault(item["id"], []).append(item)
    for base, group in groups.items():
        for index, item in enumerate(group):
            item["count"] = len(group)
            if index:
                item["id"] = "%s#%d" % (base, index + 1)


def _fill_children_order(items, landmarks):
    by_path = {}
    for item in items:
        by_path.setdefault(item["topology"]["parent"], []).append(item["id"])
    for mark in landmarks:
        own_path = "%s>%s:%s" % (mark["topology"]["parent"], mark["role"], mark["id"].split(":")[-1].split("#")[0])
        mark["children_order"] = by_path.get(own_path, [])


def _should_scan(rel):
    return rel.endswith(SOURCE_EXT) and ".test." not in rel and "/node_modules/" not in "/" + rel \
        and "/dist/" not in "/" + rel


def extract_source(root, screens=None, files=None, rel_prefix=""):
    """目录（或给定文件列表）→ 清单 dict（mode=source）。读不到的文件记 errors（调用方 fail closed）。"""
    side = {"role": "subject", "kind": "dir", "locator": root, "resolved": "path:%s" % os.path.abspath(root),
            "stack": "web-dom"}
    inv = tc.empty_inventory("web-source", "source", "inventory_a11y.py web-source", side)
    inv["errors"], seen_screens = [], {}
    for rel in files if files is not None else lc.walk_files(root):
        if not _should_scan(rel):
            continue
        _extract_file(root, rel, rel_prefix + rel, screens, inv, seen_screens)
    inv["screens"] = [{"id": sid, "source": sorted(srcs)} for sid, srcs in sorted(seen_screens.items())]
    return finish_inventory(inv)


def finish_inventory(inv):
    """合并后的收尾：全局 #n 序号、地标 children_order、静态名字集合（幂等，可重复调用）。"""
    _assign_ordinals(inv["items"])
    _fill_children_order(inv["items"], inv["landmarks"])
    inv["names"] = static_names(inv["items"])
    for item in inv["items"]:
        if item["name"]["raw"] == "{dynamic}":
            item["dynamic"] = True
    return inv


def _name_halves(item):
    name = item.get("name") or {}
    return [name.get("raw"), name.get("zh"), name.get("en")] + list(name.get("alt") or [])


def _is_static_text(value):
    return isinstance(value, str) and bool(value.strip()) and value != "{dynamic}"


def static_names(items):
    """清单里全部静态字面量：raw + zh + en + alt 每一半都算（runtime 在 zh 语言下看到的「批准」必须能对上源里的
    text("批准","Approve")）；{dynamic} 永不入集。"""
    return sorted({value for item in items for value in _name_halves(item) if _is_static_text(value)})


def _is_ui_source(rel):
    return rel.endswith((".tsx", ".jsx", ".vue", ".svelte")) and ".test." not in rel


def _is_page_html(rel):
    return rel.endswith((".html", ".htm")) and "/dist/" not in "/" + rel and "node_modules" not in rel


def surface_roots(files):
    """任意文件列表 → [(kind, root)]：web-react（tsx/jsx/vue/svelte）、static-html（无 web-react 时）。"""
    ui = [f for f in files if _is_ui_source(f)]
    if ui:
        return [("web-react", _common_dir(ui))]
    html = [f for f in files if _is_page_html(f)]
    return [("static-html", _common_dir(html))] if html else []


def _common_dir(files):
    dirs = sorted({f.rsplit("/", 1)[0] if "/" in f else "." for f in files})
    common = os.path.commonpath(dirs) if len(dirs) > 1 else dirs[0]
    return common.replace(os.sep, "/") or "."


def _extract_root(root, sub, screens):
    if sub == ".":
        return extract_source(root, screens)
    return extract_source(os.path.join(root, sub), screens, rel_prefix=sub.rstrip("/") + "/")


def extract_tree(root, screens=None):
    """整棵树（参照 worktree / dir:）→ 与 subject 同口径的清单：先找 UI 面根，再逐根提取（rel 带根前缀）。"""
    files = [f for f in lc.walk_files(root) if "/dist/" not in "/" + f]
    parts = [_extract_root(root, sub, screens) for _kind, sub in surface_roots(files)]
    if not parts:
        return extract_source(root, screens, files=[])
    merged = parts[0]
    for part in parts[1:]:
        merged = merge_inventories(merged, part)
    return merged


def merge_inventories(a, b):
    for key in ("items", "landmarks", "screens", "errors"):
        a[key] = a.get(key, []) + b.get(key, [])
    return finish_inventory(a)


def _extract_file(root, rel, rel_display, screens, inv, seen_screens):
    try:
        text = lc.read_text(os.path.join(root, rel))
    except OSError as exc:
        inv["errors"].append("%s: %s" % (rel, exc))
        return
    if text is None:
        return
    screen = screen_for_file(rel_display, screens)
    items, marks = SourceExtractor(text, screen, rel_display, screens).run()
    inv["items"] += items
    inv["landmarks"] += marks
    seen_screens.setdefault(screen, set()).add(rel_display)


# --------------------------------------------------------------------------- #
# project:native —— 冻结源清单 add-only 归一
# --------------------------------------------------------------------------- #

def _rows(native, key):
    """native[key] 的列表（缺席 / null → []）——避免到处写 `or []`。"""
    value = native.get(key)
    return value if isinstance(value, list) else []


def _native_name(entry):
    return {"raw": entry.get("en") or entry.get("zh") or "", "zh": entry.get("zh"), "en": entry.get("en"), "alt": []}


def _native_item(entry, role, kind, screen, order, parent, side=None, extra=None):
    # 原生清单的 `gated` = 「parity 门判不判这条」（§66.1：copy / help 说明性文案与 shell/os/retired 条目只列不判），
    # 与本清单 schema 的 `gated`（藏在 feature flag 后）同名异义——落到 add-only 字段 project_gated，`gated` 恒 False。
    item = {"id": entry["id"], "key": {"screen": tc.screen_family(screen), "role": role,
                                       "slug": entry["id"].split(":")[-1].split("#")[0]},
            "kind": kind, "name": _native_name(entry), "name_source": "L()", "pin": None,
            "owner": entry.get("owner", "web"), "gated": False, "project_gated": bool(entry.get("gated", True)),
            "shortcut": entry.get("shortcut"), "count": 1,
            "topology": {"parent": parent, "order": order, "side": side},
            "states": {"frozen": {"visible": True, "hidden_by": None, "focusable": role in tc.INTERACTIVE_ROLES}},
            "screen": screen, "source": {"file": str(entry.get("source", "")).split(":")[0],
                                         "line": _source_line(entry.get("source"))}, "evidence": "frozen"}
    item.update(extra or {})
    return item


def _source_line(source):
    if isinstance(source, str) and ":" in source and source.rsplit(":", 1)[1].isdigit():
        return int(source.rsplit(":", 1)[1])
    return None


def _native_controls(native):
    items, order = [], {}
    for entry in native.get("controls") or []:
        role = NATIVE_ROLES.get(entry.get("role", "label"), "static")
        screen = entry.get("screen", "window")
        order[screen] = order.get(screen, 0) + 1
        items.append(_native_item(entry, role, _kind_for(role), screen, order[screen] - 1, "window>%s" % screen,
                                  extra={"native_role": entry.get("role")}))
    return items


RAIL_LANDMARK_ID = "rail:order"  # 项目门给「侧栏容器在左 + 条目顺序」的 id（§66.2）——同一 id 才能同一本账、同一判决


def _native_rail(native):
    """rail 块缺席（清单里没有侧栏）→ 不铸 `rail:order` 地标——否则每份没有 rail 的参照都会假报一条 MISSING。"""
    rail = native.get("rail")
    if not isinstance(rail, dict):
        return [], []
    side = rail.get("side") or "left"
    mark = {"id": RAIL_LANDMARK_ID, "role": "navigation", "topology": {"parent": "window", "order": 0, "side": side},
            "bbox": None, "children_order": [e["id"] for e in rail.get("items") or []]}
    # 地标本身无 accessible name（原生侧栏没有）：配对键 slug 取角色名 `navigation`（无名地标的约定），名字留空
    items = [_native_item({"id": RAIL_LANDMARK_ID, "en": "", "zh": "", "owner": "web", "gated": True},
                          "navigation", "landmark", "window", 0, "window", side, extra={"key": {"screen": "window", "role": "navigation", "slug": "navigation"}})]
    for index, entry in enumerate(rail.get("items") or []):
        items.append(_native_item(entry, "link", "interactive", "window", index, "window>navigation:rail",
                                  extra={"shortcut": entry.get("shortcut")}))
    return items, [mark]


def _native_lanes(native):
    lanes = native.get("lanes") or {}
    items, marks = [], []
    for index, entry in enumerate(lanes.get("items") or []):
        side = entry.get("rail") or "inside"
        items.append(_native_item(entry, "list", "landmark", "board", index, "window>board>main:board", side))
        marks.append({"id": entry["id"], "role": "list", "topology": {"parent": "window>board>main:board",
                                                                     "order": index, "side": side},
                      "bbox": None, "children_order": []})
    return items, marks


def _native_screens(native):
    """screen:<screen> → heading 条目；配对键 slug 来自标题文字（`screen:settings.recording` 的标题是 "Recording"，
    slug = recording），不是 id 尾段——id 尾段带点号，永远配不上任何 <h*>。"""
    items = []
    for entry in _rows(native, "screens"):
        screen = entry["id"].split(":", 1)[1]
        slug = tc.slugify(str(entry.get("en") or entry.get("zh") or screen))
        items.append(_native_item(entry, "heading", "heading", screen, 0, "window",
                                  extra={"key": {"screen": tc.screen_family(screen), "role": "heading", "slug": slug}}))
    return items


def _native_shortcuts(native):
    return [_native_item(e, "menuitem", "interactive", e.get("screen", "menu"), 0, "window>menu",
                         extra={"shortcut": e.get("key")}) for e in _rows(native, "shortcuts")]


def _native_settings(native):
    return [_native_item(dict(e, en=e.get("key")), "switch", "interactive", "settings", 0, "window>settings")
            for e in _rows(native, "settings_keys")]


def _native_side(native):
    source = native.get("source")
    sha = (source if isinstance(source, dict) else {}).get("sha256", "?")
    return {"role": "reference", "kind": "alias", "locator": "native", "resolved": "sha256:%s" % sha, "stack": "swiftui"}


def _theme_default(native):
    for entry in _rows(native, "theme_layout"):
        if entry.get("id") == "theme:default":
            return entry.get("value")
    return None


def normalize_native(native, path=None):
    """extract_native_inventory.py 的 JSON → schemaVersion 1 清单（mode=frozen）。"""
    inv = tc.empty_inventory("project:extract_native_inventory", "frozen", "scripts/ui/extract_native_inventory.py",
                             _native_side(native))
    rail_items, rail_marks = _native_rail(native)
    lane_items, lane_marks = _native_lanes(native)
    inv["items"] = _native_controls(native) + rail_items + lane_items + _native_screens(native) \
        + _native_shortcuts(native) + _native_settings(native)
    inv["landmarks"] = rail_marks + lane_marks
    inv["screens"] = [{"id": e["id"].split(":", 1)[1], "label": {"zh": e.get("zh"), "en": e.get("en")},
                       "owner": e.get("owner")} for e in _rows(native, "screens")]
    inv["dims"]["default_theme"] = _theme_default(native)
    inv["layout_pointers"] = {t["id"]: t.get("token") for t in _rows(native, "theme_layout") if t.get("token")}
    inv["frozen_from"] = path
    return inv


def _regenerate_native(repo, script, out_dir, runner):
    path = os.path.join(out_dir, "native-inventory.json")
    res = runner([sys.executable, script, "--write", "--out", path], cwd=repo, timeout=600)
    return path if res.ok else None


def _native_path(repo, inventory_path, runner, out_dir):
    """已提交的清单路径；缺席且 producer 在场 → 现跑到 out_dir；否则 None。"""
    path = inventory_path if inventory_path else os.path.join(repo, "ui", "parity", "native-inventory.json")
    if os.path.exists(path):
        return path
    script = os.path.join(repo, "scripts", "ui", "extract_native_inventory.py")
    if os.path.exists(script) and out_dir:
        return _regenerate_native(repo, script, out_dir, runner)
    return None


def load_native(repo, inventory_path=None, runner=lc.run_command, out_dir=None):
    """读 ui/parity/native-inventory.json；缺席但 producer 在 → 跑到 out_dir（绝不写进 ui/）。
    两者都没有 → None（调用方记 UNAVAILABLE 并给命令提示）。"""
    path = _native_path(repo, inventory_path, runner, out_dir)
    if not path or not os.path.exists(path):
        return None
    return normalize_native(tc.read_json(path), path)


# --------------------------------------------------------------------------- #
# web-playwright —— probes/driver.cjs 的输出 → schema（runtime）
# --------------------------------------------------------------------------- #

DRIVER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "probes", "driver.cjs")


def _dim_key(run, state="rest"):
    return "%s::%s::%s::%s" % (run.get("theme"), run.get("viewport"), run.get("language"), state)


_DRIVER_SLUG_RE = re.compile(r"[^a-z0-9\u4e00-\u9fff]+")
_PATH_ROLES = tc.LANDMARK_ROLES | frozenset({"list", "region", "tablist"})  # driver.cjs landmarkPath 收的角色


def _driver_segment(role, name):
    """driver.cjs `landmarkPath` 的一段：`role:` + 名字（无名 → 角色名）按 driver 同一正则折成 -（不截断）。"""
    return "%s:%s" % (role, _DRIVER_SLUG_RE.sub("-", str(name or role).lower()).strip("-"))


class NameFilter(object):
    """静态名字过滤（SKILL.md「runtime names not found in the subject's source string set become {dynamic}」）：
    known=None → 不过滤（CLI 调试）；否则名字不在集合里且没 pin → 名字、id 的 slug、可见文本、以及这个地标在
    landmarkPath 里的那一段全部改成 {dynamic}/dynamic——用户内容进不了清单、进不了报告、进不了 id。空集合 = 什么都不认识
    = 全部 {dynamic}（fail closed，不是不过滤）。"""

    def __init__(self, known):
        self.known = None if known is None else set(known)
        self.segments = {}

    def foreign(self, name, pin=None):
        return self.known is not None and bool(name) and name not in self.known and not pin

    def name(self, raw, pin=None):
        """→ (name, dynamic)。"""
        return ("{dynamic}", True) if self.foreign(raw, pin) else (raw, False)

    def register(self, role, raw, pin=None):
        """地标类节点先登记：它的 landmarkPath 段要在后代的 parent 路径里被脱敏。"""
        if role in _PATH_ROLES and self.foreign(raw, pin):
            self.segments[_driver_segment(role, raw)] = "%s:dynamic" % role

    def parent(self, path):
        return ">".join(self.segments.get(seg, seg) for seg in str(path or "window").split(">"))


def _runtime_item(node, run, screen, names=None):
    names = names if names else NameFilter(None)
    role, raw = node.get("role") or "generic", str(node.get("name") or "")
    names.register(role, raw, node.get("pin"))
    name, dynamic = names.name(raw, node.get("pin"))
    slug = _slug_for(name, role)  # 与源提取同一条规矩：无名地标 / 列表 = 角色名，无名交互项 = unnamed
    return {"id": tc.make_id(_id_kind(role), screen, role, slug),
            "key": {"screen": tc.screen_family(screen), "role": role, "slug": slug}, "kind": _kind_for(role),
            "name": {"raw": name, "zh": None, "en": None, "alt": []}, "name_source": node.get("name_source"),
            "visible_text": None if dynamic else node.get("text"), "pin": node.get("pin"), "owner": "web", "gated": False,
            "shortcut": None, "count": 1, "dynamic": dynamic,
            "topology": {"parent": names.parent(node.get("parent")), "order": node.get("order", 0),
                         "side": node.get("side")},
            "states": {_dim_key(run): {"visible": bool(node.get("visible")), "hidden_by": node.get("hidden_by"),
                                       "focusable": bool(node.get("focusable")), "tab_index": node.get("tab_index"),
                                       "bbox": node.get("bbox"), "computed": node.get("computed") or {},
                                       "contrast": node.get("contrast")}},
            "screen": screen, "source": None, "evidence": "runtime", "level": node.get("level")}


def _merge_runtime_item(items_by_id, item, flags):
    existing = items_by_id.get(item["id"])
    if existing is None:
        item["gated"] = flags == "all_on"
        items_by_id[item["id"]] = item
        return
    existing["states"].update(item["states"])
    if flags == "default":
        existing["gated"] = False


def _runtime_landmarks(run, screen, names=None):
    names = names if names else NameFilter(None)
    out = []
    for m in run.get("landmarks") or []:
        name, _dynamic = names.name(m.get("name"))
        out.append({"id": tc.make_id("landmark", screen, m.get("role"), tc.slugify(str(name or m.get("role")))),
                    "role": m.get("role"), "topology": {"parent": names.parent(m.get("parent")), "order": m.get("order", 0),
                                                        "side": m.get("side")},
                    "bbox": m.get("bbox"), "children_order": m.get("children") or []})
    return out


def _run_nodes(run, screen, items_by_id, names=None):
    """一次 run 的 nodes → 合并进 items_by_id；返回 idx → item id（focus walk 用）。同一 run 里同 id 的第 n 个元素带
    `#n`（四张卡各一颗「批准」是四个条目，不是一个）；跨 run（另一主题 / 视口）同 `#n` 的才是同一元素，合并 states。"""
    by_idx, seen = {}, {}
    for node in _rows(run, "nodes"):
        item = _runtime_item(node, run, screen, names)
        seen[item["id"]] = seen.get(item["id"], 0) + 1
        if seen[item["id"]] > 1:
            item["id"] = "%s#%d" % (item["id"], seen[item["id"]])
        by_idx[node.get("idx")] = item["id"]
        _merge_runtime_item(items_by_id, item, run.get("flags", "default"))
    return by_idx


def _fill_counts(items):
    """count = 同基 id（去掉 #n）的条目数——序号在 _run_nodes 里按文档序定死，这里只补 count，不重编（focus walk 与
    landmarks 引用的 id 不能在事后变）。"""
    groups = {}
    for item in items:
        groups.setdefault(re.sub(r"#\d+$", "", item["id"]), []).append(item)
    for group in groups.values():
        for item in group:
            item["count"] = len(group)


def _run_walks(run, screen, inv, by_idx):
    key = "%s::%s" % (screen, _dim_key(run))
    inv["focus_walk"][key] = [by_idx.get(i, str(i)) for i in _rows(run, "focus_walk")]
    # 原始元素序号也留一份：四张卡各有一颗「批准」→ 同一个 id，按 id 看像回环，按元素看不是（focus_order 用这份）
    inv.setdefault("focus_walk_idx", {})[key] = list(_rows(run, "focus_walk"))
    inv["overflow"][key] = run.get("overflow")
    inv["lang"] = run.get("lang", inv.get("lang"))


def parse_runtime(output, subject_side, known_names=None):
    """driver.cjs 的 JSON → {inventory, tokens_observed, geometry, shots, axe, observed_theme}。
    known_names = subject 源字符串 ∪ 参照名字（sensors._known_names）：不在里面的 runtime 名字在构造时就成 {dynamic}
    （名字 / id / 可见文本 / 地标路径段一起脱敏，focus walk 与 landmarks 引用的 id 因此从头一致）；None = 不过滤（CLI）。
    inventory.names_filtered = 被脱敏的条目数。"""
    inv = tc.empty_inventory("web-playwright", "runtime", output.get("tool", "playwright"), subject_side)
    items_by_id, bundle = {}, {"tokens_observed": {}, "geometry": {}, "axe": [], "observed_theme": {}}
    names = NameFilter(known_names)
    for run in _rows(output, "runs"):
        screen = run.get("screen")
        _run_walks(run, screen, inv, _run_nodes(run, screen, items_by_id, names))
        inv["landmarks"] += _runtime_landmarks(run, screen, names)
        _collect_run_extras(run, screen, inv, bundle)
    inv["items"] = sorted(items_by_id.values(), key=lambda i: (i["screen"], i["topology"]["parent"],
                                                               i["topology"]["order"], i["id"]))
    _fill_counts(inv["items"])
    inv["names_filtered"] = sum(1 for item in inv["items"] if item.get("dynamic"))
    inv["dims"].update(output.get("dims") if output.get("dims") else {})
    return dict(bundle, inventory=inv)


def _shot_record(run, screen):
    return {"id": "shot:%s:%s:%s:%s:%s" % (screen, run.get("scene"), run.get("theme"), run.get("viewport"), run.get("language")),
            "path": run["shot"], "sha256": run.get("shot_sha256"), "masks": _rows(run, "masks"),
            "masked_ratio": run.get("masked_ratio", 0.0), "screen": screen, "theme": run.get("theme"),
            "viewport": run.get("viewport"), "scene": run.get("scene"), "language": run.get("language")}


def _collect_run_extras(run, screen, inv, bundle):
    if run.get("shot"):
        inv["shots"].append(_shot_record(run, screen))
    tokens = run.get("tokens")
    bundle["tokens_observed"].setdefault(run.get("theme"), {}).update(tokens if tokens else {})
    geometry = run.get("geometry")
    for key, boxes in (geometry if geometry else {}).items():
        bundle["geometry"].setdefault(key, []).extend(boxes)
    bundle["axe"] += [dict(v, screen=screen, theme=run.get("theme")) for v in _rows(run, "axe")]
    if run.get("observed_theme"):
        bundle["observed_theme"][run.get("emulation", "default")] = run["observed_theme"]


def run_driver(config, playwright_path, out_dir, runner=lc.run_command, node="node", timeout=None):
    """写 driver 配置 → node probes/driver.cjs → 解析。rc≠0 / 输出非 JSON → None（fail closed）。"""
    os.makedirs(out_dir, exist_ok=True)
    cfg_path, out_path = os.path.join(out_dir, "driver-config.json"), os.path.join(out_dir, "driver-output.json")
    tc.write_text(cfg_path, tc.dump_json(dict(config, playwright=playwright_path, out=out_path)))
    res = runner([node, DRIVER_PATH, cfg_path], cwd=out_dir, timeout=timeout)
    if not res.ok or not os.path.exists(out_path):
        return None, res
    try:
        return tc.read_json(out_path), res
    except ValueError:
        return None, res


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def _load_screens(path):
    if not path:
        return None
    return (tc.read_json(path).get("screens") or None)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--from-source", metavar="DIR")
    parser.add_argument("--native", metavar="REPO")
    parser.add_argument("--inventory", metavar="FILE")
    parser.add_argument("--runtime", metavar="DRIVER_CONFIG")
    parser.add_argument("--playwright", metavar="MODULE_PATH")
    parser.add_argument("--screen-map", metavar="CONFIG_JSON")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    inv = _dispatch(args)
    if inv is None:
        print("inventory_a11y: nothing extracted (adapter unavailable)", file=sys.stderr)
        return 2
    tc.write_text(args.out, tc.dump_json(inv))
    print("inventory_a11y: %d item(s) → %s" % (len(inv.get("items", [])), args.out))
    return 0


def _dispatch(args):
    if args.from_source:
        return extract_source(args.from_source, _load_screens(args.screen_map))
    if args.native:
        return load_native(args.native, args.inventory, out_dir=os.path.dirname(os.path.abspath(args.out)))
    if args.runtime:
        output, _res = run_driver(tc.read_json(args.runtime), args.playwright, os.path.dirname(os.path.abspath(args.out)))
        return parse_runtime(output, {"role": "subject", "kind": "url"})["inventory"] if output else None
    return None


if __name__ == "__main__":
    sys.exit(main())
