#!/usr/bin/env python3
"""test-ui skill 共用件：颜色数学（WCAG 对比度）、stdlib PNG 编解码、稳定 id 语法、
W3C design-tokens 扁平化、清单 schema 校验。detect_ui / inventory_a11y / tokens /
visual / parity / run_ui 都从这里取。

法典指针：docs/CONTRACT.md §58（阈值只读；账本 new/worse/stale 语义借用）、§UI-parity（UI 对齐
契约——feat/ui-parity-contract 落地时分配法条号，truth = docs/CONTRACT.md；本 skill 各文件的「§UI-parity」都指它；id 语法 `<kind>:<screen>:<role>:<slug>` 与
scripts/ui/ui_common.slugify 逐字兼容，判例钉死）。设计 = docs/design/vnext2-plan.md
R2.8 / D14（test-code 的姐妹 skill）。stdlib only、py3.9 floor、零网络、不 import act。

fail-closed 约定：PNG 读不懂（非 8-bit RGB/RGBA、截断、CRC 坏）抛 ValueError——调用方
把它记成 FAIL，绝不当 pass。判例：tests/test_skill_test_ui_common.py。
"""

import hashlib
import json
import os
import re
import struct
import zlib

SKILL_NAME = "test-ui"
SKILL_VERSION = "0.1.0"
SCHEMA_VERSION = 1

# WAI-ARIA 角色词表（清单里 role 的合法值；原生词表经适配器的数据表映射过来）
INTERACTIVE_ROLES = frozenset({
    "button", "link", "checkbox", "radio", "switch", "textbox", "searchbox", "combobox",
    "listbox", "option", "slider", "spinbutton", "tab", "menuitem", "menuitemcheckbox",
    "menuitemradio", "treeitem", "scrollbar",
})
LANDMARK_ROLES = frozenset({"banner", "navigation", "main", "complementary", "contentinfo",
                            "region", "search", "form"})
STRUCTURE_ROLES = frozenset({"heading", "list", "listitem", "tablist", "tabpanel", "table", "row",
                             "cell", "img", "static", "generic", "dialog", "alert", "status", "group",
                             "radiogroup", "menu", "menubar", "toolbar", "separator", "label"})
ALL_ROLES = INTERACTIVE_ROLES | LANDMARK_ROLES | STRUCTURE_ROLES
ITEM_STATUSES = ("PRESENT", "MISSING", "CHANGED", "WAIVED")
TOPOLOGY_KINDS = frozenset({"landmark", "navigation", "region", "list", "heading", "tablist"})


# --------------------------------------------------------------------------- #
# 颜色：任何 CSS 颜色写法 → sRGB #rrggbbaa；合成；相对亮度；WCAG 对比度
# --------------------------------------------------------------------------- #

_NAMED = {"white": (255, 255, 255, 1.0), "black": (0, 0, 0, 1.0), "transparent": (0, 0, 0, 0.0),
          "red": (255, 0, 0, 1.0), "canvas": (255, 255, 255, 1.0), "canvastext": (0, 0, 0, 1.0)}
_HEX_RE = re.compile(r"^#([0-9a-fA-F]{3,8})$")
_FUNC_RE = re.compile(r"^(rgba?|hsla?)\(\s*([^)]*)\)$", re.I)


def _hex_parts(digits):
    """3/4/6/8 位 hex → (r, g, b, a)；其它长度 → None。"""
    if len(digits) in (3, 4):
        digits = "".join(ch * 2 for ch in digits)
    if len(digits) not in (6, 8):
        return None
    vals = [int(digits[i:i + 2], 16) for i in range(0, len(digits), 2)]
    alpha = vals[3] / 255.0 if len(vals) == 4 else 1.0
    return vals[0], vals[1], vals[2], alpha


def _channel(raw, scale=255.0):
    """`50%` → 按 scale 折算；`0.5`/`128` → 数字。"""
    text = raw.strip()
    if text.endswith("%"):
        return float(text[:-1]) * scale / 100.0
    return float(text)


def _hsl_to_rgb(h, s, lum):
    """CSS HSL → (r, g, b) 0–255（标准 hue 分段公式）。"""
    c = (1 - abs(2 * lum - 1)) * s
    hp = (h % 360) / 60.0
    x = c * (1 - abs(hp % 2 - 1))
    sector = int(hp) % 6
    table = [(c, x, 0), (x, c, 0), (0, c, x), (0, x, c), (x, 0, c), (c, 0, x)]
    r1, g1, b1 = table[sector]
    m = lum - c / 2
    return tuple(int(round((v + m) * 255)) for v in (r1, g1, b1))


def _func_color(name, args):
    parts = [p for p in re.split(r"[,\s/]+", args.strip()) if p]
    if len(parts) not in (3, 4):
        return None
    alpha = _channel(parts[3], 1.0) if len(parts) == 4 else 1.0
    if name.lower().startswith("hsl"):
        rgb = _hsl_to_rgb(float(parts[0].rstrip("deg")), _channel(parts[1], 1.0), _channel(parts[2], 1.0))
    else:
        rgb = tuple(int(round(_channel(p))) for p in parts[:3])
    return rgb[0], rgb[1], rgb[2], max(0.0, min(1.0, alpha))


def parse_color(text):
    """CSS 颜色 → (r, g, b, alpha)；认不出 → None（调用方决定是否 fail closed）。"""
    if not isinstance(text, str):
        return None
    value = text.strip()
    if value.lower() in _NAMED:
        return _NAMED[value.lower()]
    hex_match = _HEX_RE.match(value)
    if hex_match:
        return _hex_parts(hex_match.group(1))
    func = _FUNC_RE.match(value)
    if func:
        return _func_color(func.group(1), func.group(2))
    return None


def to_hex8(rgba):
    r, g, b, a = rgba
    clamp = [max(0, min(255, int(round(v)))) for v in (r, g, b, a * 255.0)]
    return "#%02x%02x%02x%02x" % tuple(clamp)


def composite(fg, bg):
    """fg 叠在 bg 上（都是 (r,g,b,a)）→ 不透明 (r,g,b,1.0)；对比度必须在合成色上算。"""
    a = fg[3]
    inv = 1.0 - a
    return (fg[0] * a + bg[0] * inv, fg[1] * a + bg[1] * inv, fg[2] * a + bg[2] * inv, 1.0)


def _linear(channel):
    c = channel / 255.0
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def relative_luminance(rgba):
    return 0.2126 * _linear(rgba[0]) + 0.7152 * _linear(rgba[1]) + 0.0722 * _linear(rgba[2])


def contrast_ratio(fg, bg):
    """WCAG 2.x 对比度；fg 带 alpha 先合成到 bg。"""
    if fg[3] < 1.0:
        fg = composite(fg, bg)
    l1, l2 = relative_luminance(fg), relative_luminance(bg)
    hi, lo = max(l1, l2), min(l1, l2)
    return round((hi + 0.05) / (lo + 0.05), 2)


def canonical_color(text):
    """任何写法 → `#rrggbbaa`；认不出 → None。"""
    rgba = parse_color(text)
    return to_hex8(rgba) if rgba else None


# --------------------------------------------------------------------------- #
# PNG（zlib + 5 种过滤器；只认 8-bit RGB/RGBA，其余 fail closed）
# --------------------------------------------------------------------------- #

_PNG_SIG = b"\x89PNG\r\n\x1a\n"


def _chunk(kind, body):
    crc = zlib.crc32(kind + body) & 0xFFFFFFFF
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", crc)


def encode_png(width, height, rows, channels=3):
    """rows = 每行一个 bytes/bytearray（width*channels 字节）→ PNG bytes（filter 0，无损）。"""
    if channels not in (3, 4):
        raise ValueError("encode_png: channels must be 3 or 4")
    color_type = 2 if channels == 3 else 6
    raw = b"".join(b"\x00" + bytes(row) for row in rows)
    ihdr = struct.pack(">IIBBBBB", width, height, 8, color_type, 0, 0, 0)
    return _PNG_SIG + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", zlib.compress(raw, 9)) + _chunk(b"IEND", b"")


def _iter_chunks(data):
    pos = 8
    while pos + 8 <= len(data):
        length, kind = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + length]
        if len(body) != length:
            raise ValueError("png: truncated chunk %r" % kind)
        yield kind, body
        pos += 12 + length


def _paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def _unfilter_sub(cur, prev, bpp):
    out = bytearray(cur)
    for i in range(bpp, len(out)):
        out[i] = (out[i] + out[i - bpp]) & 0xFF
    return out


def _unfilter_up(cur, prev, bpp):
    return bytearray((c + p) & 0xFF for c, p in zip(cur, prev))


def _unfilter_average(cur, prev, bpp):
    out = bytearray(cur)
    for i in range(len(out)):
        left = out[i - bpp] if i >= bpp else 0
        out[i] = (out[i] + ((left + prev[i]) >> 1)) & 0xFF
    return out


def _unfilter_paeth(cur, prev, bpp):
    out = bytearray(cur)
    for i in range(len(out)):
        left = out[i - bpp] if i >= bpp else 0
        upleft = prev[i - bpp] if i >= bpp else 0
        out[i] = (out[i] + _paeth(left, prev[i], upleft)) & 0xFF
    return out


_UNFILTER = {0: lambda cur, prev, bpp: bytearray(cur), 1: _unfilter_sub, 2: _unfilter_up,
             3: _unfilter_average, 4: _unfilter_paeth}


def _parse_ihdr(body):
    width, height, depth, color_type, _comp, _filt, interlace = struct.unpack(">IIBBBBB", body)
    if depth != 8 or color_type not in (2, 6) or interlace != 0:
        raise ValueError("png: only 8-bit non-interlaced RGB/RGBA is supported (depth=%d type=%d)"
                         % (depth, color_type))
    return width, height, 3 if color_type == 2 else 4


def _unfilter_rows(raw, width, height, channels):
    stride = width * channels
    if len(raw) != height * (stride + 1):
        raise ValueError("png: decompressed size mismatch")
    rows, prev = [], bytearray(stride)
    for y in range(height):
        start = y * (stride + 1)
        ftype = raw[start]
        if ftype not in _UNFILTER:
            raise ValueError("png: unknown filter %d" % ftype)
        prev = _UNFILTER[ftype](raw[start + 1:start + 1 + stride], prev, channels)
        rows.append(bytes(prev))
    return rows


def _collect_chunks(data):
    header, idat = None, []
    for kind, body in _iter_chunks(data):
        if kind == b"IHDR":
            header = _parse_ihdr(body)
        elif kind == b"IDAT":
            idat.append(body)
    return header, idat


def decode_png(data):
    """PNG bytes → (width, height, channels, rows[bytes])。任何不认识的形状抛 ValueError。"""
    if not data.startswith(_PNG_SIG):
        raise ValueError("png: bad signature")
    header, idat = _collect_chunks(data)
    if header is None or not idat:
        raise ValueError("png: missing IHDR/IDAT")
    width, height, channels = header
    return width, height, channels, _unfilter_rows(zlib.decompress(b"".join(idat)), width, height, channels)


# --------------------------------------------------------------------------- #
# id 语法（与 scripts/ui/ui_common.slugify 逐字兼容）/ 名字归一 / 确定性 JSON
# --------------------------------------------------------------------------- #

_SLUG_STRIP = re.compile(r"[^a-z0-9\u4e00-\u9fff]+")
_WS_RE = re.compile(r"\s+")
_DIGITS_RE = re.compile(r"\d+")
_INTERP_RE = re.compile(r"\{[^}]*\}|\$\{[^}]*\}|%[sd]")


def slugify(text, limit=48):
    """人读文本 → id 片段：小写、非字母数字（保留汉字）折成 -，截断。"""
    slug = _SLUG_STRIP.sub("-", text.lower()).strip("-")
    return slug[:limit].rstrip("-") or "item"


def normalize_name(text):
    """可达名归一：trim、空白折叠、数字串 → {n}、插值 → {}、case fold。"""
    if not text:
        return ""
    value = _WS_RE.sub(" ", str(text)).strip()
    value = _INTERP_RE.sub("{}", value)
    value = _DIGITS_RE.sub("{n}", value)
    return value.casefold()


def make_id(kind, screen, role, slug):
    """`<kind>:<screen>:<role>:<slug>` —— 与 parity 契约同一套语法，绝不造第二套。"""
    return "%s:%s:%s:%s" % (kind, screen, role, slug)


def screen_family(screen):
    """`board.card` → `board`：配对键只看首段（子屏精度进 topology.parent）。"""
    return (screen or "").split(".")[0] or "window"


def pair_key(screen, role, name):
    """配对键 (screen family, role, slug(name))——slug 与 id 同一口径（parity 契约 slugify），
    不是 normalize_name（那是近似匹配用的）。"""
    return screen_family(screen), role, slugify(str(name or "").strip())


def dump_json(obj):
    """确定性 JSON：键排序、2 空格、非 ASCII 原样、末尾换行（与 scripts/ui/ui_common 同形）。"""
    return json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    with open(path, "rb") as fh:
        return sha256_bytes(fh.read())


def read_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def write_text(path, text):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


# --------------------------------------------------------------------------- #
# W3C design-tokens → 扁平点路径
# --------------------------------------------------------------------------- #

def _is_token(node):
    return isinstance(node, dict) and "$value" in node


def flatten_tokens(nested, prefix=""):
    """{group: {token: {$value,$type}}} → {"group.token": {...}}；`$` 开头的组元数据跳过。"""
    flat = {}
    for key, node in sorted(nested.items()):
        if key.startswith("$"):
            continue
        path = "%s.%s" % (prefix, key) if prefix else key
        if _is_token(node):
            flat[path] = node
        elif isinstance(node, dict):
            flat.update(flatten_tokens(node, path))
    return flat


def token_value_text(token):
    """$value → 可比较的字符串（dimension `8px`、color 归一 hex8、其余 json）。"""
    value = token.get("$value")
    if token.get("$type") == "color" and isinstance(value, str):
        return canonical_color(value) or value
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True)
    return str(value)


def px_value(text):
    """`400px` / `400` / `1.5rem`(×16) → float px；认不出 → None。"""
    match = re.match(r"^\s*(-?\d+(?:\.\d+)?)\s*(px|pt|rem|em)?\s*$", str(text))
    if not match:
        return None
    number, unit = float(match.group(1)), match.group(2)
    return number * 16.0 if unit in ("rem", "em") else number


# --------------------------------------------------------------------------- #
# 清单 schema 校验（fail closed：坏清单 = reference_unreadable / subject_unreadable）
# --------------------------------------------------------------------------- #

_REQUIRED_TOP = ("schemaVersion", "producer", "side", "items")
_REQUIRED_ITEM = ("id", "key", "kind", "name", "topology", "states")


def _validate_item(index, item):
    errors = ["items[%d].%s" % (index, key) for key in _REQUIRED_ITEM if key not in item]
    if "key" in item and not {"screen", "role", "slug"} <= set(item["key"]):
        errors.append("items[%d].key" % index)
    if item.get("key", {}).get("role") not in ALL_ROLES:
        errors.append("items[%d].role" % index)
    return errors


def _validate_items(items):
    errors = []
    for index, item in enumerate(items):
        errors += _validate_item(index, item) if isinstance(item, dict) else ["items[%d]" % index]
    return errors


def validate_inventory(obj):
    """→ 错误路径列表（空 = 合法）。只查形状，不查语义。"""
    if not isinstance(obj, dict):
        return ["<root>"]
    errors = [key for key in _REQUIRED_TOP if key not in obj]
    if obj.get("producer", {}).get("mode") not in ("runtime", "source", "frozen"):
        errors.append("producer.mode")
    return errors + _validate_items(obj.get("items") or [])


def producer(adapter, mode, tool, argv=None):
    """每个产物都带的 producer 记录（mode ∈ runtime | source | frozen）。"""
    return {"adapter": adapter, "mode": mode, "tool": tool, "skill": "%s %s" % (SKILL_NAME, SKILL_VERSION),
            "argv": list(argv or [])}


def empty_inventory(adapter, mode, tool, side):
    """schemaVersion 1 空清单骨架（字段 add-only）。"""
    return {"schemaVersion": SCHEMA_VERSION, "producer": producer(adapter, mode, tool), "side": side,
            "dims": {"themes": [], "default_theme": None, "viewports": [], "languages": [], "scenes": [],
                     "flags": ["default"]},
            "screens": [], "items": [], "landmarks": [], "focus_walk": {}, "overflow": {}, "shots": []}
