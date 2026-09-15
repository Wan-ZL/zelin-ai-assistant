#!/usr/bin/env python3
"""web/public 的 PWA 图标生成器（docs/CONTRACT.md §73）。

安装清单（`web/public/manifest.webmanifest`）要的 192 / 512 位图不手绘：形状真源
是同目录的 `favicon.svg`（G7 自写资产），这里只把它的 `<rect>` 几何栅格化成
8-bit RGBA PNG——改 favicon 就重跑一次，两者永远同一个标志。

纯 stdlib（zlib + struct 写 PNG 块，与 act/lib/qr.py 同款技术；**不 import 它的
私名**，那支是 QR 专用的灰度写者）。抗锯齿 = 每个输出像素 4 条子扫描线 × 精确的
水平交叠率，圆角按圆弧解析求交，颜色按文档顺序 src-over 预乘合成。

「重跑零 diff」的口径是**像素**不是字节（§73）：deflate 的字节流取决于本机 zlib
构建（macOS 与 linux runner 的 libz 版本不同，zlib-ng 更是另一套），字节相等会
让门在别人的机器上莫名其妙地红；契约是图片内容，所以 `--check` 比 IHDR + 解压后
的扫描线。判例 tests/test_pwa_manifest.py。

用法：
    python3 scripts/ui/make_pwa_icons.py --write | --check
"""

import argparse
import math
import os
import struct
import sys
import xml.etree.ElementTree as ET
import zlib

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.normpath(os.path.join(_HERE, "..", ".."))
PUBLIC_DIR = os.path.join(REPO_ROOT, "web", "public")
FAVICON_PATH = os.path.join(PUBLIC_DIR, "favicon.svg")
SVG_NS = "{http://www.w3.org/2000/svg}"
SIZES = (192, 512)
SUBSAMPLES = 4  # 每个输出像素的子扫描线数（垂直方向抗锯齿）


def icon_path(size):
    """size×size 图标的落盘位置（manifest 的 `icons[].src` 逐字对应）。"""
    return os.path.join(PUBLIC_DIR, "icon-%d.png" % size)


# --------------------------------------------------------------------------- #
# favicon.svg = 形状真源
# --------------------------------------------------------------------------- #
def load_shapes(svg_path=FAVICON_PATH):
    """favicon.svg → (viewBox 边长, 文档顺序的矩形列表)。

    解析的是仓库自己的资产（不是不可信输入），所以 stdlib ElementTree 足够——
    运行时依赖白名单本来也只有 stdlib + PyYAML。
    """
    root = ET.parse(svg_path).getroot()
    viewbox = _viewbox(root.get("viewBox") or "")
    rects = [_rect(el) for el in root.iter(SVG_NS + "rect")]
    if not rects:
        raise ValueError("favicon.svg: no <rect> to rasterise")
    return viewbox, rects


def _viewbox(attr):
    """图标是正方形：只认原点上的正方形 viewBox，歪了就 fail-loud。"""
    box = attr.split()
    if len(box) != 4 or box[:2] != ["0", "0"] or box[2] != box[3]:
        raise ValueError("favicon.svg: expected a square viewBox at the origin")
    return float(box[2])


def _rect(el):
    return {"x": float(el.get("x", 0)), "y": float(el.get("y", 0)),
            "w": float(el.get("width")), "h": float(el.get("height")),
            "r": float(el.get("rx", 0)), "rgb": _rgb(el.get("fill", "#000000")),
            "alpha": float(el.get("opacity", 1))}


def _rgb(fill):
    text = fill.strip().lstrip("#")
    if len(text) != 6:
        raise ValueError("favicon.svg: only #rrggbb fills are supported: %r" % fill)
    return tuple(int(text[i:i + 2], 16) for i in (0, 2, 4))


# --------------------------------------------------------------------------- #
# 栅格化（圆角矩形 → 覆盖率 → 预乘 src-over）
# --------------------------------------------------------------------------- #
def _span(rect, line):
    """扫描线 `line`（SVG 坐标）与圆角矩形的水平交区间 [xa, xb)；不相交 = None。"""
    top, bottom = rect["y"], rect["y"] + rect["h"]
    if line < top or line >= bottom:
        return None
    radius = min(rect["r"], rect["w"] / 2.0, rect["h"] / 2.0)
    offset = 0.0
    if line < top + radius:
        offset = top + radius - line
    elif line > bottom - radius:
        offset = line - (bottom - radius)
    inset = radius - math.sqrt(max(radius * radius - offset * offset, 0.0))
    return rect["x"] + inset, rect["x"] + rect["w"] - inset


def _coverage(rect, row, scale, size):
    """输出行 `row` 上该矩形逐像素的覆盖率（0..1）。"""
    weights = {}
    for k in range(SUBSAMPLES):
        span = _span(rect, (row + (k + 0.5) / SUBSAMPLES) / scale)
        if span is None:
            continue
        key = (span[0] * scale, span[1] * scale)
        weights[key] = weights.get(key, 0.0) + 1.0 / SUBSAMPLES
    cov = [0.0] * size
    for (xa, xb), weight in weights.items():
        _add_span(cov, xa, xb, weight, size)
    return cov


def _add_span(cov, xa, xb, weight, size):
    for i in range(max(int(math.floor(xa)), 0), min(int(math.ceil(xb)), size)):
        part = min(xb, i + 1.0) - max(xa, float(i))
        if part > 0.0:
            cov[i] += weight * part


def _blend(acc, rect, cov):
    """预乘 src-over：`acc[i] = [r*a, g*a, b*a, a]`（颜色分量 0..255）。"""
    red, green, blue = rect["rgb"]
    for i, ratio in enumerate(cov):
        alpha = rect["alpha"] * ratio
        if alpha <= 0.0:
            continue
        keep = 1.0 - alpha
        pixel = acc[i]
        pixel[0] = red * alpha + pixel[0] * keep
        pixel[1] = green * alpha + pixel[1] * keep
        pixel[2] = blue * alpha + pixel[2] * keep
        pixel[3] = alpha + pixel[3] * keep


def _render_row(rects, row, scale, size):
    acc = [[0.0, 0.0, 0.0, 0.0] for _ in range(size)]
    for rect in rects:
        _blend(acc, rect, _coverage(rect, row, scale, size))
    return acc


def _row_bytes(acc):
    """一条 PNG 扫描线：filter 0（None）+ 反预乘后的 RGBA 字节。"""
    out = bytearray([0])
    for red, green, blue, alpha in acc:
        if alpha <= 0.0:
            out.extend(b"\x00\x00\x00\x00")
            continue
        out.extend((_byte(red / alpha), _byte(green / alpha),
                    _byte(blue / alpha), _byte(alpha * 255.0)))
    return bytes(out)


def _byte(value):
    return min(max(int(value + 0.5), 0), 255)


# --------------------------------------------------------------------------- #
# PNG 组装与读回
# --------------------------------------------------------------------------- #
def render(size, shapes=None):
    """favicon 几何 → size×size 的 8-bit RGBA PNG 字节。"""
    viewbox, rects = shapes if shapes is not None else load_shapes()
    scale = size / viewbox
    raw = b"".join(_row_bytes(_render_row(rects, row, scale, size))
                   for row in range(size))
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)  # 8-bit RGBA
    return (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr)
            + _chunk(b"IDAT", zlib.compress(raw, 9)) + _chunk(b"IEND", b""))


def _chunk(tag, payload):
    return (struct.pack(">I", len(payload)) + tag + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))


def pixels(blob):
    """PNG 字节 → (IHDR 元组, 解压后的扫描线)——「重跑零 diff」比的就是这两样。"""
    if blob[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    pos, header, data = 8, None, b""
    while pos + 8 <= len(blob):
        length = struct.unpack(">I", blob[pos:pos + 4])[0]
        tag, payload = blob[pos + 4:pos + 8], blob[pos + 8:pos + 8 + length]
        if tag == b"IHDR":
            header = struct.unpack(">IIBBBBB", payload)
        elif tag == b"IDAT":
            data += payload
        pos += 12 + length
    if header is None:
        raise ValueError("PNG without IHDR")
    return header, zlib.decompress(data)


# --------------------------------------------------------------------------- #
# CLI（--write / --check，与 scripts/ui/parity_fixture.py 同一副手柄）
# --------------------------------------------------------------------------- #
def main(argv=None):
    parser = argparse.ArgumentParser(
        description="rasterise web/public/favicon.svg into the PWA icons")
    parser.add_argument("--write", action="store_true", help="落盘（重跑覆盖）")
    parser.add_argument("--check", action="store_true",
                        help="只比对：committed 图标的像素 == 重新栅格化的像素")
    args = parser.parse_args(argv)
    shapes = load_shapes()
    fresh = {icon_path(size): render(size, shapes) for size in SIZES}
    if args.write:
        for path, blob in fresh.items():
            with open(path, "wb") as handle:
                handle.write(blob)
        print("wrote %s" % ", ".join(os.path.basename(p) for p in fresh))
    return _check_fresh(fresh) if args.check else 0


def _check_fresh(fresh):
    stale = [p for p, blob in fresh.items() if not _same_pixels(p, blob)]
    if stale:
        print("stale icon(s): %s — rerun with --write"
              % ", ".join(os.path.basename(p) for p in stale), file=sys.stderr)
        return 1
    print("pwa icons are fresh")
    return 0


def _same_pixels(path, blob):
    try:
        with open(path, "rb") as handle:
            committed = handle.read()
    except OSError:
        return False
    return pixels(committed) == pixels(blob)


if __name__ == "__main__":
    sys.exit(main())
