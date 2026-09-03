#!/usr/bin/env python3
"""test-ui skill · VISUAL 传感器：demo 数据截图 ⟷ golden 的感知差异 + golden 台账校验。

仪器（报告写明用了哪个）：
  odiff（PATH 上有就用；退出码 0 同 / 21 像素差 / 22 尺寸差）
  内置 stdlib diff：逐像素最大通道差 > pixel_tolerance 算变化 → 变化占比 + 变化区域 bbox（8×8
  tile 连通块）+ tile 均值热图 PNG；遮罩按 [x, y, w, h] 排除并计 masked_ratio；尺寸不同 =
  CHANGED dimensions，绝不缩放。

golden 绑机器：<goldens>/<platform>-<engine>-dpr<n>/manifest.json 里每张 golden 一条
{sha256, tool, seed, blessed_at, reason, diff_pct_at_bless}；sha 不在台账或没 reason = FAIL
`unreviewed_golden`；别的机器的 golden = UNAVAILABLE（不是 fail）。skill **永不**写 golden：
提案落到 <report>/proposed/goldens/，人来拷。

法典指针：docs/CONTRACT.md §UI-parity.4（视觉基线：web/e2e/visual.spec.ts + __screenshots__ 是项目仪器，
在场时逐字调用为 `project_visual`；本文件是无项目仪器时的 fallback）、§58（阈值只读）。
设计 = vnext2-plan R2.8 / D14。真实数据永不入图（seed_guard 在 run_ui）。
判例：tests/test_skill_test_ui_visual.py（1% 植入色块 → 0.01 ± 1e-6；60% 遮罩 → 超帽；截断 PNG → 抛）。

用法：visual.py diff A.png B.png [--tolerance N] [--mask x,y,w,h]… [--heatmap OUT.png]
      visual.py manifest <goldens-dir>
"""

import argparse
import json
import os
import re
import sys
from collections import deque

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import testui_common as tc  # noqa: E402

TILE = 8
MAX_REGIONS = 12
_ODIFF_RE = re.compile(r"Different pixels:\s*(\d+)\s*\(([\d.]+)%\)")


# --------------------------------------------------------------------------- #
# 载入 / 遮罩
# --------------------------------------------------------------------------- #

class Image(object):
    """解码后的 PNG：width / height / channels / rows(bytes)。"""

    def __init__(self, width, height, channels, rows):
        self.width, self.height, self.channels, self.rows = width, height, channels, rows

    @classmethod
    def load(cls, path):
        with open(path, "rb") as fh:
            return cls(*tc.decode_png(fh.read()))

    def pixel(self, x, y):
        offset = x * self.channels
        return self.rows[y][offset:offset + 3]


def _in_masks(x, y, masks):
    return any(mx <= x < mx + mw and my <= y < my + mh for mx, my, mw, mh in masks)


def masked_ratio(width, height, masks):
    """遮罩覆盖比例（重叠区域按 tile 粗算，不精确扣重叠——超帽只会更早红，不会更晚）。"""
    if not width or not height:
        return 0.0
    area = sum(max(0, min(w, width - x)) * max(0, min(h, height - y)) for x, y, w, h in masks)
    return round(min(1.0, area / float(width * height)), 4)


# --------------------------------------------------------------------------- #
# 逐像素差
# --------------------------------------------------------------------------- #

def _row_delta(row_a, row_b, channels):
    """两行 → 每像素最大通道差（bytes）。"""
    diffs = bytes(abs(a - b) for a, b in zip(row_a, row_b))
    if channels == 3:
        return bytes(map(max, diffs[0::3], diffs[1::3], diffs[2::3]))
    return bytes(map(max, diffs[0::4], diffs[1::4], diffs[2::4], diffs[3::4]))


def _changed_row(delta, y, tolerance, masks):
    if not masks:
        return [x for x, d in enumerate(delta) if d > tolerance]
    return [x for x, d in enumerate(delta) if d > tolerance and not _in_masks(x, y, masks)]


def _tile_grid(width, height):
    return (width + TILE - 1) // TILE, (height + TILE - 1) // TILE


def _mark_tiles(tiles, xs, y, cols):
    row = y // TILE
    for x in xs:
        tiles[row * cols + x // TILE] += 1


def _regions(tiles, cols, rows):
    """变化 tile 的 4 邻连通块 → [x, y, w, h]（像素坐标），按面积降序，最多 MAX_REGIONS。"""
    seen, regions = set(), []
    for start in [i for i, count in enumerate(tiles) if count]:
        if start in seen:
            continue
        box = _flood(tiles, cols, rows, start, seen)
        regions.append([box[0] * TILE, box[1] * TILE, (box[2] - box[0] + 1) * TILE, (box[3] - box[1] + 1) * TILE])
    regions.sort(key=lambda r: -(r[2] * r[3]))
    return regions[:MAX_REGIONS]


def _neighbours(tiles, cols, rows, x, y):
    for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
        if 0 <= nx < cols and 0 <= ny < rows and tiles[ny * cols + nx]:
            yield ny * cols + nx


def _flood(tiles, cols, rows, start, seen):
    queue, seen_here = deque([start]), {start}
    x0 = x1 = start % cols
    y0 = y1 = start // cols
    while queue:
        idx = queue.popleft()
        seen.add(idx)
        x, y = idx % cols, idx // cols
        x0, x1, y0, y1 = min(x0, x), max(x1, x), min(y0, y), max(y1, y)
        fresh = [n for n in _neighbours(tiles, cols, rows, x, y) if n not in seen_here]
        seen_here.update(fresh)
        queue.extend(fresh)
    return x0, y0, x1, y1


def _dimension_result(img_a, img_b):
    return {"status": "dimensions", "changed_pct": 1.0, "changed_pixels": None, "total": img_a.width * img_a.height,
            "regions": [], "tiles": None, "masked_ratio": 0.0,
            "dimensions": [[img_a.width, img_a.height], [img_b.width, img_b.height]]}


def _scan_rows(img_a, img_b, tolerance, masks, cols):
    tiles, changed = [0] * (cols * ((img_a.height + TILE - 1) // TILE)), 0
    for y in range(img_a.height):
        xs = _changed_row(_row_delta(img_a.rows[y], img_b.rows[y], img_a.channels), y, tolerance, masks)
        changed += len(xs)
        _mark_tiles(tiles, xs, y, cols)
    return tiles, changed


def diff_images(img_a, img_b, tolerance=0, masks=None):
    """→ {status: same|changed|dimensions, changed_pct, changed_pixels, total, regions, tiles, masked_ratio}。"""
    masks = [list(map(int, m)) for m in (masks or [])]
    if (img_a.width, img_a.height) != (img_b.width, img_b.height):
        return _dimension_result(img_a, img_b)
    if img_a.channels != img_b.channels:
        raise ValueError("visual: channel count differs (%d vs %d)" % (img_a.channels, img_b.channels))
    cols, rows = _tile_grid(img_a.width, img_a.height)
    tiles, changed = _scan_rows(img_a, img_b, tolerance, masks, cols)
    total = img_a.width * img_a.height
    return {"status": "changed" if changed else "same", "changed_pct": round(changed / float(total), 6),
            "changed_pixels": changed, "total": total, "regions": _regions(tiles, cols, rows),
            "tiles": {"changed": sum(1 for t in tiles if t), "total": cols * rows, "grid": tiles, "cols": cols},
            "masked_ratio": masked_ratio(img_a.width, img_a.height, masks)}


def heatmap_png(result, width, height):
    """tile 计数 → 红色强度热图（与原图同尺寸，filter 0）。"""
    tiles = result.get("tiles")
    if not tiles:
        return None
    cols, grid, peak = tiles["cols"], tiles["grid"], max(tiles["grid"] + [1])
    rows = []
    for y in range(height):
        row = bytearray()
        for x in range(width):
            level = int(255 * grid[(y // TILE) * cols + x // TILE] / float(peak))
            row += bytes((level, 0, 0))
        rows.append(bytes(row))
    return tc.encode_png(width, height, rows, 3)


# --------------------------------------------------------------------------- #
# odiff（在场即为仪器）
# --------------------------------------------------------------------------- #

def diff_with_odiff(runner, path_a, path_b, out_png, tolerance=0):
    """→ result dict（tool=odiff）或 None（解析不了 → 调用方退回内置 diff 并记录）。"""
    res = runner(["odiff", path_a, path_b, out_png, "--threshold", str(tolerance / 255.0)], timeout=300)
    if res.rc == 0:
        return {"status": "same", "changed_pct": 0.0, "changed_pixels": 0, "tool": "odiff"}
    if res.rc == 22:
        return {"status": "dimensions", "changed_pct": 1.0, "changed_pixels": None, "tool": "odiff"}
    match = _ODIFF_RE.search(res.text())
    if res.rc == 21 and match:
        return {"status": "changed", "changed_pct": round(float(match.group(2)) / 100.0, 6),
                "changed_pixels": int(match.group(1)), "tool": "odiff"}
    return None


# --------------------------------------------------------------------------- #
# golden 台账（machine-bound）
# --------------------------------------------------------------------------- #

def machine_key(platform, engine, dpr):
    return "%s-%s-dpr%s" % (platform, re.sub(r"[^a-z0-9]+", "", str(engine).lower()), dpr)


def load_manifest(golden_dir):
    path = os.path.join(golden_dir, "manifest.json")
    if not os.path.exists(path):
        return None
    try:
        return tc.read_json(path)
    except ValueError:
        return {"_error": "manifest.json unreadable"}


def _list_pngs(golden_dir):
    if not os.path.isdir(golden_dir):
        return []
    return sorted(f for f in os.listdir(golden_dir) if f.endswith(".png"))


def _reviewed(golden_dir, entries, name):
    entry = entries.get(name)
    return entry is not None and entry.get("sha256") == tc.sha256_file(os.path.join(golden_dir, name))


def _has_reason(entries, name):
    return bool((entries.get(name) or {}).get("reason", "").strip())


def _manifest_verdict(pngs, error, unreviewed, reasonless, dangling):
    problems = len(unreviewed) + len(reasonless) + len(dangling)
    return {"ok": error is None and problems == 0, "error": error, "unreviewed": unreviewed, "dangling": dangling,
            "reasonless": reasonless, "count": len(pngs)}


def _entries_of(manifest):
    entries = manifest.get("entries")
    return entries if isinstance(entries, dict) else {}


def _reasonless(entries, name):
    return name in entries and not _has_reason(entries, name)


def check_manifest(golden_dir):
    """→ {ok, unreviewed[], dangling[], reasonless[], count, error}。没 manifest = 全部 unreviewed。"""
    pngs = _list_pngs(golden_dir)
    manifest = load_manifest(golden_dir)
    if manifest is None:
        manifest = {}
    if manifest.get("_error"):
        return _manifest_verdict(pngs, manifest["_error"], pngs, [], [])
    entries = _entries_of(manifest)
    unreviewed = [f for f in pngs if not _reviewed(golden_dir, entries, f)]
    reasonless = [f for f in pngs if _reasonless(entries, f)]
    return _manifest_verdict(pngs, None, unreviewed, reasonless, sorted(set(entries) - set(pngs)))


def _instrument(shot_path, golden_path, tolerance, masks, runner, out_dir, tools):
    """odiff 在场就用它；否则内置 diff（结果都带 tool 名）。"""
    if (tools or {}).get("odiff") and runner and out_dir:
        result = diff_with_odiff(runner, shot_path, golden_path, os.path.join(out_dir, "odiff.png"), tolerance)
        if result is not None:
            return result
    return dict(diff_images(Image.load(shot_path), Image.load(golden_path), tolerance, masks), tool="internal")


def compare_shot(shot_path, golden_path, thresholds, masks=None, runner=None, out_dir=None, tools=None):
    """一张截图 vs 一张 golden → {status, changed_pct, threshold, tool, regions, masked_ratio, over_mask_cap}。"""
    max_pct = float(thresholds.get("max_changed_pct", 0.0))
    tolerance = int(thresholds.get("pixel_tolerance", 0))
    result = _instrument(shot_path, golden_path, tolerance, masks, runner, out_dir, tools)
    over = result["changed_pct"] > max_pct
    result.update({"threshold": max_pct, "item_status": "CHANGED" if over else "PRESENT",
                   "over_mask_cap": result.get("masked_ratio", 0.0) > float(thresholds.get("max_mask_ratio", 0.2))})
    return result


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def _parse_mask(text):
    parts = [int(p) for p in text.split(",")]
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("mask = x,y,w,h")
    return parts


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    diff = sub.add_parser("diff")
    diff.add_argument("a")
    diff.add_argument("b")
    diff.add_argument("--tolerance", type=int, default=0)
    diff.add_argument("--mask", type=_parse_mask, action="append", default=[])
    diff.add_argument("--heatmap")
    manifest = sub.add_parser("manifest")
    manifest.add_argument("dir")
    args = parser.parse_args(argv)
    return _run_cli(args)


def _cli_diff(args):
    try:
        img_a, img_b = Image.load(args.a), Image.load(args.b)
    except (OSError, ValueError) as exc:
        print("visual: unreadable PNG — fail closed: %s" % exc, file=sys.stderr)
        return 1
    result = diff_images(img_a, img_b, args.tolerance, args.mask)
    _write_heatmap(args.heatmap, result, img_a)
    result.pop("tiles", None)
    print(json.dumps(result, indent=1, sort_keys=True))
    return 0 if result["status"] == "same" else 1


def _write_heatmap(path, result, img):
    if path and result.get("tiles"):
        with open(path, "wb") as fh:
            fh.write(heatmap_png(result, img.width, img.height))


def _run_cli(args):
    if args.cmd == "manifest":
        result = check_manifest(args.dir)
        print(json.dumps(result, indent=1, sort_keys=True))
        return 0 if result["ok"] else 1
    return _cli_diff(args)


if __name__ == "__main__":
    sys.exit(main())
