#!/usr/bin/env python3
"""UI 对齐契约的共用件：Swift 源扫描原语 + 账本/JSON 落盘（docs/CONTRACT.md §63）。

三支脚本共用：scripts/ui/extract_native_inventory.py（原生 UI 清单）、
scripts/ui/extract_native_tokens.py（设计 token）、scripts/ui/parity_check.py
（对齐门）。原生 mac/Sources 在 D3 下是冻结的只读规格——这里只读它、永不改它。

stdlib-only（owner 机器 /usr/bin/python3 3.9 floor + CI qa-gates job）。不依赖
PyYAML。判例：tests/test_ui_swift_scan.py。

扫描模型（同一份文本的三个等长视图，offset 互通，行号只算一次）：
  raw       —— 文件原文
  stripped  —— 注释换成空白（换行保留），字符串原样
  masked    —— stripped 之上再把字符串内容换成空白（引号保留）
结构分析（括号配对、声明范围、调用链）走 masked；取字面量走 stripped。
"""

import bisect
import json
import os
import re

REPO_ROOT = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
)
MAC_SOURCES = os.path.join(REPO_ROOT, "mac", "Sources")
PARITY_DIR = os.path.join(REPO_ROOT, "ui", "parity")
TOKENS_DIR = os.path.join(REPO_ROOT, "ui", "tokens")

INVENTORY_PATH = os.path.join(PARITY_DIR, "native-inventory.json")
WAIVERS_PATH = os.path.join(PARITY_DIR, "waivers.txt")
PENDING_PATH = os.path.join(PARITY_DIR, "pending.txt")
NATIVE_TOKENS_PATH = os.path.join(TOKENS_DIR, "native-tokens.json")


# --------------------------------------------------------------------------- #
# 文件遍历
# --------------------------------------------------------------------------- #

def iter_swift_files(root=MAC_SOURCES):
    """mac/Sources/*.swift，按文件名排序（清单确定性的第一道保证）。"""
    if not os.path.isdir(root):
        return []
    return [os.path.join(root, fn) for fn in sorted(os.listdir(root))
            if fn.endswith(".swift")]


def read_text(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


# --------------------------------------------------------------------------- #
# 注释剥离 / 字符串遮罩（等长，行号不变）
# --------------------------------------------------------------------------- #

def _blank(ch):
    """注释/字符串内容换成空白，换行保留——offset 与行号都不漂移。"""
    return ch if ch == "\n" else " "


class _Scanner(object):
    """单遍字符状态机：产出 stripped 与 masked 两个视图。"""

    def __init__(self, text):
        self.text = text
        self.stripped = []
        self.masked = []
        self.i = 0
        self.block_depth = 0

    def run(self):
        n = len(self.text)
        while self.i < n:
            if self.block_depth:
                self._block_comment()
            elif self.text.startswith("//", self.i):
                self._line_comment()
            elif self.text.startswith("/*", self.i):
                self.block_depth = 1
                self._emit_blank(2)
            elif self.text[self.i] == '"':
                self._string()
            else:
                self._emit_keep(1)
        return "".join(self.stripped), "".join(self.masked)

    def _emit_keep(self, count):
        chunk = self.text[self.i:self.i + count]
        self.stripped.append(chunk)
        self.masked.append(chunk)
        self.i += count

    def _emit_blank(self, count):
        chunk = "".join(_blank(c) for c in self.text[self.i:self.i + count])
        self.stripped.append(chunk)
        self.masked.append(chunk)
        self.i += count

    def _emit_string_body(self, count):
        chunk = self.text[self.i:self.i + count]
        self.stripped.append(chunk)
        self.masked.append("".join(_blank(c) for c in chunk))
        self.i += count

    def _line_comment(self):
        end = self.text.find("\n", self.i)
        end = len(self.text) if end < 0 else end
        self._emit_blank(end - self.i)

    def _block_comment(self):
        if self.text.startswith("/*", self.i):
            self.block_depth += 1
            self._emit_blank(2)
        elif self.text.startswith("*/", self.i):
            self.block_depth -= 1
            self._emit_blank(2)
        else:
            self._emit_blank(1)

    def _string(self):
        """普通 "..." 或多行 \"\"\"...\"\"\"；转义 \\x 与插值 \\(...) 整体留在字符串里。"""
        triple = self.text.startswith('"""', self.i)
        quote_len = 3 if triple else 1
        self._emit_keep(quote_len)
        while self.i < len(self.text):
            if self.text[self.i] == "\\":
                self._string_escape()
            elif self.text.startswith('"' * quote_len, self.i):
                self._emit_keep(quote_len)
                return
            else:
                self._emit_string_body(1)

    def _string_escape(self):
        """\\( 开启插值：吞到配对的 )；其余转义吞两字符。"""
        if self.text.startswith("\\(", self.i):
            depth, j = 0, self.i + 1
            while j < len(self.text):
                depth += {"(": 1, ")": -1}.get(self.text[j], 0)
                if depth == 0:
                    break
                j += 1
            self._emit_string_body(j + 1 - self.i)
        else:
            self._emit_string_body(2)


def scan_views(text):
    """原文 → (stripped, masked)。"""
    return _Scanner(text).run()


# --------------------------------------------------------------------------- #
# 行号 / 括号配对
# --------------------------------------------------------------------------- #

class LineIndex(object):
    """offset → 1-based 行号（bisect，一次建索引）。"""

    def __init__(self, text):
        self.starts = [0] + [i + 1 for i, ch in enumerate(text) if ch == "\n"]

    def line_of(self, offset):
        return bisect.bisect_right(self.starts, offset)


_PAIR = {"{": "}", "(": ")", "[": "]"}


def match_close(masked, open_pos):
    """masked[open_pos] 是 { ( [ 之一 → 配对闭括号的 offset；配不上返回 -1。"""
    opener = masked[open_pos]
    closer = _PAIR[opener]
    depth = 0
    for j in range(open_pos, len(masked)):
        ch = masked[j]
        if ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return j
    return -1


def match_open(masked, close_pos):
    """masked[close_pos] 是 } ) ] 之一 → 配对开括号的 offset；配不上返回 -1。"""
    closer = masked[close_pos]
    opener = {v: k for k, v in _PAIR.items()}[closer]
    depth = 0
    for j in range(close_pos, -1, -1):
        ch = masked[j]
        if ch == closer:
            depth += 1
        elif ch == opener:
            depth -= 1
            if depth == 0:
                return j
    return -1


# --------------------------------------------------------------------------- #
# 声明范围（顶层类型 + 成员）
# --------------------------------------------------------------------------- #

_TOP_DECL = re.compile(
    r"^(?:@\w+\s+)*(?:(?:private|fileprivate|public|internal|final|indirect)\s+)*"
    r"(struct|class|enum|extension|protocol|actor)\s+(\w+)", re.M)
_MEMBER_DECL = re.compile(
    r"^[ \t]+(?:@\w+(?:\([^)]*\))?\s+)*(?:(?:private|fileprivate|public|internal|static|"
    r"override|final|lazy|mutating|nonisolated)\s+)*(var|func|let)\s+(\w+)", re.M)


def _decl_span(masked, decl_start):
    """声明起点 → (body_open, body_close)；找不到函数体（协议方法/存储属性）→ None。"""
    # 声明头可能折行（泛型 where / 多行参数）：从声明起点起找第一个 {，但它必须
    # 在下一个顶层声明之前，且中间不能先出现 = 或 ;（存储属性 / 常量）
    open_pos = masked.find("{", decl_start)
    if open_pos < 0:
        return None
    head = masked[decl_start:open_pos]
    if "\n\n" in head.strip("\n") or " = " in head:
        return None
    close_pos = match_close(masked, open_pos)
    return (open_pos, close_pos) if close_pos > 0 else None


def top_level_spans(masked):
    """[(kind, name, start_off, end_off)]，按出现顺序；start = 声明行首。"""
    spans = []
    for m in _TOP_DECL.finditer(masked):
        body = _decl_span(masked, m.start())
        if body is None:
            continue
        spans.append((m.group(1), m.group(2), m.start(), body[1]))
    return spans


def member_spans(masked, start, end):
    """类型体 [start, end) 内的成员 var/func 范围 [(kind, name, start_off, end_off)]。
    只收带函数体/计算体的成员；存储属性略过。"""
    out = []
    for m in _MEMBER_DECL.finditer(masked, start, end):
        body = _decl_span(masked, m.start())
        if body is None or body[1] > end:
            continue
        out.append((m.group(1), m.group(2), m.start(), body[1]))
    return out


def innermost(spans, offset):
    """包含 offset 的最内层 span（spans 元素形 (kind, name, start, end)）。"""
    best = None
    for span in spans:
        if span[2] <= offset <= span[3]:
            if best is None or span[2] >= best[2]:
                best = span
    return best


# --------------------------------------------------------------------------- #
# L("zh", "en") 双语字面量
# --------------------------------------------------------------------------- #

_STR = r'"((?:[^"\\]|\\.)*)"'
L_CALL = re.compile(r"\bL\(\s*" + _STR + r"\s*,\s*" + _STR + r"\s*\)", re.S)
_INTERP = re.compile(r"\\\((.*?)\)")


def unescape_swift(literal):
    """Swift 字符串字面量 → 人读文本；插值 \\(expr) 变成 {expr} 占位。"""
    text = _INTERP.sub(lambda m: "{%s}" % m.group(1).strip(), literal)
    text = text.replace('\\"', '"').replace("\\n", "\n").replace("\\t", "\t")
    text = re.sub(r"\\u\{([0-9a-fA-F]+)\}", lambda m: chr(int(m.group(1), 16)), text)
    return text.replace("\\\\", "\\")


def find_l_calls(stripped, masked=None):
    """stripped 视图里的全部 L() → [(offset, zh, en)]（offset = L 的位置）。给了 masked
    就只认代码里的 L(——写在多行字符串正文里的 `L("…", "…")` 不是调用。"""
    out = []
    for m in L_CALL.finditer(stripped):
        if masked is not None and not masked.startswith("L(", m.start()):
            continue
        out.append((m.start(), unescape_swift(m.group(1)), unescape_swift(m.group(2))))
    return out


# --------------------------------------------------------------------------- #
# 调用链：offset 外层每个 ( / { 的「调用者标识」
# --------------------------------------------------------------------------- #

_IDENT_TAIL = re.compile(r"([A-Za-z_][\w.]*)\s*$")
_LABEL_TAIL = re.compile(r"(\w+)\s*:\s*$")


def _ident_before(masked, pos):
    """pos 之前紧邻的标识符（允许 .a.b 链）；没有 → ''。"""
    m = _IDENT_TAIL.search(masked, max(0, pos - 120), pos)
    return m.group(1) if m else ""


def _brace_owner(masked, open_pos):
    """{ 的语义主人：`Foo(...) {` → Foo；`Foo {` → Foo；`} label: {` → 前一个
    调用的 Foo；`label: {`（其它参数闭包）→ 'closure:<label>'；`View {` → 'body'。"""
    before = masked[:open_pos].rstrip()
    if before.endswith(")"):
        call_open = match_open(masked, len(before) - 1)
        return _ident_before(masked, call_open) if call_open >= 0 else ""
    label = _LABEL_TAIL.search(before[-40:])
    if label:
        return _closure_owner(masked, before, label.group(1))
    return _ident_before(masked, len(before))


def _closure_owner(masked, before, label):
    if label != "label":
        return "closure:" + label
    prev = before[:-len(label) - 1].rstrip().rstrip(":").rstrip()
    if prev.endswith("}"):
        prev_open = match_open(masked, len(prev) - 1)
        return _brace_owner(masked, prev_open) if prev_open >= 0 else ""
    return "closure:label"


class BraceTree(object):
    """masked 视图里全部 ( { 的嵌套树，一遍建成：opener offset → (parent, close)。
    enclosing(offset) 走「最后一个开在 offset 之前的括号 → 祖先链」，每次 O(深度)
    ——1,500 个 L() × 2,700 行文件也只是毫秒级（线性回扫会是分钟级）。"""

    def __init__(self, masked):
        self.masked = masked
        self.positions = []
        self.parent = {}
        self.close = {}
        stack = []
        for j, ch in enumerate(masked):
            if ch in "({":
                self.positions.append(j)
                self.parent[j] = stack[-1] if stack else -1
                stack.append(j)
            elif ch in ")}" and stack:
                self.close[stack.pop()] = j

    def enclosing(self, offset):
        """包含 offset 的最内层开括号 offset；没有 → -1。"""
        k = bisect.bisect_left(self.positions, offset) - 1
        node = self.positions[k] if k >= 0 else -1
        while node >= 0 and self.close.get(node, len(self.masked)) < offset:
            node = self.parent[node]
        return node

    def chain(self, offset, limit=12):
        """offset 外层的调用者标识列表，最内层在前。`(` → 前置标识符；`{` → _brace_owner。"""
        chain = []
        node = self.enclosing(offset)
        while node >= 0 and len(chain) < limit:
            if self.masked[node] == "(":
                chain.append(_ident_before(self.masked, node))
            else:
                chain.append(_brace_owner(self.masked, node))
            node = self.parent[node]
        return chain


# --------------------------------------------------------------------------- #
# 稳定 id / 确定性 JSON / 账本
# --------------------------------------------------------------------------- #

_SLUG_STRIP = re.compile(r"[^a-z0-9\u4e00-\u9fff]+")


def slugify(text, limit=48):
    """人读文本 → id 片段：小写、非字母数字（保留汉字）折成 -，截断。"""
    slug = _SLUG_STRIP.sub("-", text.lower()).strip("-")
    return slug[:limit].rstrip("-") or "item"


def dump_json(obj):
    """确定性 JSON：键排序、2 空格、非 ASCII 原样、末尾换行。"""
    return json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def write_text(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def load_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def parse_ledger(text):
    """账本文本 → {inventory_id: 备注}。行形 `<id>  <rest…>`；# 注释与空行忽略。"""
    entries = {}
    for line in text.splitlines():
        stripped = line.split("#", 1)[0].strip() if line.lstrip().startswith("#") else line.strip()
        if not stripped:
            continue
        parts = stripped.split(None, 1)
        entries[parts[0]] = parts[1] if len(parts) > 1 else ""
    return entries


def load_ledger(path):
    """账本文件 → {id: 备注}；缺席 = 空账。"""
    if not os.path.exists(path):
        return {}
    return parse_ledger(read_text(path))
