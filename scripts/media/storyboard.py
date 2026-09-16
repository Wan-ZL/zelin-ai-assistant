"""M/storyboard.md 生成器（CONTRACT §77 拟；QA 面 §58）。

分镜文档不是手写的：它是 `shots.json` 的渲染结果，改镜头只改 JSON，文档跟着重生成
（防腐第 5 条 文档指针纪律：文档里的数字由脚本生成，不手写字面量）。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import shots as shots_mod  # noqa: E402


def clock(seconds: int) -> str:
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def render(data: dict) -> str:
    rows = shots_mod.timeline(data["shots"])
    total = shots_mod.total_seconds(data["shots"])
    vp = data.get("viewport", {})
    lines = [
        f"# {data['title']['zh']} — 分镜 storyboard",
        "",
        "真源 = `scripts/media/shots.json`（本文件由 `scripts/media/storyboard.py` 生成，勿手改）。"
        f"视口 {vp.get('width')}×{vp.get('height')}，共 {len(rows)} 个镜头，总时长 {total}s"
        f"（闸门：每镜头 ≤ {shots_mod.MAX_SHOT_SECONDS}s，全片 {shots_mod.MIN_TOTAL_SECONDS}–{shots_mod.MAX_TOTAL_SECONDS}s）。",
        "",
        "所有像素都来自真实 app 的浏览器录屏（Playwright 驱动 `python3 -m server` + `scripts/demo_seed.py` 的虚构 demo 数据），"
        "没有一帧是模型画出来的界面。",
        "",
        "| # | 镜头 id | 时间 | 时长 | demo 场景 | 页面 / 深链 | 画面里发生什么 |",
        "|---|---|---|---|---|---|---|",
    ]
    for i, row in enumerate(rows, start=1):
        shot = row["shot"]
        lines.append(
            f"| {i} | `{shot['id']}` | {clock(row['start'])}–{clock(row['end'])} | {shot['seconds']}s "
            f"| `{shot.get('scene', '-')}` | `{shot.get('url', '/')}` | {shot['zh']} |"
        )
    lines += ["", "## 旁白（中文 / English）", ""]
    for i, row in enumerate(rows, start=1):
        shot = row["shot"]
        lines += [f"### {i}. `{shot['id']}`（{shot['seconds']}s）", "", f"- zh：{shot['zh']}", f"- en: {shot['en']}", ""]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="write M/storyboard.md from shots.json")
    ap.add_argument("--out", required=True, help="output path (M/storyboard.md)")
    ap.add_argument("--shots", default=None)
    args = ap.parse_args(argv)
    data = shots_mod.load(args.shots)
    total = shots_mod.total_seconds(data["shots"])
    if not shots_mod.MIN_TOTAL_SECONDS <= total <= shots_mod.MAX_TOTAL_SECONDS:
        raise SystemExit(f"total {total}s outside {shots_mod.MIN_TOTAL_SECONDS}-{shots_mod.MAX_TOTAL_SECONDS}s")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(data), encoding="utf-8")
    print(f"wrote {out} (shots={len(data['shots'])}, total={total}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
